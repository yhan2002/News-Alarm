"""Incremental article/time-axis sync. Default is an offline plan, never an upload."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

import news

STATE = news.ROOT/'data/article-sync.sqlite'


def encode(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def prepare(articles,meta,now):
    from briefing import transaction
    documents = {hours:news.dashboard(articles,meta,hours,now,'saved') for hours in (0,24,72,168,720)}
    groups = {}
    catalogs = []
    for hours,doc in documents.items():
        for issue in doc['issues']:
            for article_id in issue['article_ids']:
                groups.setdefault(article_id,{})[str(hours)] = issue['id']
        catalog = {k:v for k,v in doc.items() if k not in ('articles','issues')}
        catalog['as_of'] = now.isoformat(timespec='microseconds')
        catalogs.append({'hours':hours,'body':encode(catalog)})
    rows = []
    for a in documents[0]['articles']:
        # Retain validated facts/quotes, never raw API envelopes, full bodies or credentials.
        a = dict(a)
        for key in ('assessment','qwen'):
            if a.get(key):
                a[key] = {k:v for k,v in a[key].items() if k not in ('body','input','response','usage')}
        first = a.get('first_collected_at')
        analyzed = a.get('qwen',{}).get('at') if a.get('qwen',{}).get('status')=='done' else None
        a.update(first_collected_at=first,analysis_updated_at=analyzed,
                 issue_groups=groups.get(a['id'],{}),transaction=transaction(a))
        rows.append({'id':a['id'],'published_at':int(news.timestamp(a['published_at']).timestamp()*1000),
                     'first_collected_at':first,'analysis_updated_at':analyzed,'body':encode(a)})
    return rows,catalogs


def exchange(config,payload=None):
    p=urlsplit(config['site_url'])
    if p.scheme!='https' or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
        raise ValueError('Expected HTTPS site origin')
    req=Request(config['site_url'].rstrip('/')+'/news/api/sync',
        data=encode(payload).encode() if payload is not None else None,
        headers={'Authorization':'Bearer '+config['upload_token'],'Content-Type':'application/json','User-Agent':'CSQNewsPublisher/2.0'},
        method='POST' if payload is not None else 'GET')
    with build_opener(ProxyHandler({})).open(req,timeout=30) as response:
        result=json.load(response)
    if payload is not None and result.get('ok') is not True:
        raise RuntimeError('Article sync unconfirmed; acknowledgements not advanced')
    return result


def batches(rows):
    batch=[]; size=0
    for row in rows:
        n=len(encode(row).encode())
        if n>500000:
            raise ValueError('One article exceeds safety limit: '+row['id'])
        if batch and (len(batch)>=50 or size+n>750000):
            yield batch
            batch=[];size=0
        batch.append(row);size+=n
    if batch:yield batch


def synchronize(config, *, limit=1000):
    capabilities=exchange(config)
    if capabilities.get('protocol')!=3:
        raise RuntimeError('Deploy article/time-axis endpoint before syncing data')
    STATE.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(STATE,timeout=10)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS synced (id TEXT PRIMARY KEY, hash TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS pending_sync (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS sync_protocol (version INTEGER PRIMARY KEY)')
        if not conn.execute('SELECT 1 FROM sync_protocol WHERE version=3').fetchone():
            # Protocol 2 acknowledgements referred to live rows, not staged rows.
            conn.execute("UPDATE synced SET hash=''")
            conn.execute('DELETE FROM pending_sync')
            conn.execute('INSERT INTO sync_protocol VALUES (3)')
            conn.commit()
        pending=conn.execute('SELECT body FROM pending_sync WHERE id=1').fetchone()
        if pending:
            plan=json.loads(pending[0])
            rows,catalogs,revision=plan['rows'],plan['catalogs'],plan['revision']
        else:
            articles,meta=news.load_saved(news.ROOT/'data/news.sqlite')
            now=news.utcnow()
            rows,catalogs=prepare(articles,meta,now)
            revision=now.isoformat(timespec='microseconds')
            # Resume the same rows AND catalog after a cap, timeout or process exit.
            conn.execute('INSERT INTO pending_sync VALUES (1,?)',
                         (encode({'rows':rows,'catalogs':catalogs,'revision':revision}),))
            conn.commit()
        old=dict(conn.execute('SELECT id,hash FROM synced'))
        hashes={r['id']:hashlib.sha256(encode(r).encode()).hexdigest() for r in rows}
        removed=sorted(set(old)-set(hashes))
        if removed and not capabilities.get('removals'):
            raise RuntimeError('Deploy article removal support before syncing deduplicated data')
        changed=[r for r in rows if old.get(r['id'])!=hashes[r['id']]]
        print(f'Article sync: {len(changed)} changed / {len(rows)} total; cap {limit}',flush=True)
        for batch in batches(changed[:limit]):
            exchange(config,{'generated_at':revision,'articles':batch})
            conn.executemany('INSERT OR REPLACE INTO synced VALUES (?,?)',[(r['id'],hashes[r['id']]) for r in batch])
            conn.commit()
        if len(changed)>limit:
            raise RuntimeError('Bound reached; saved batches retained. Resume the same command.')
        # Small derived statistics only: no copies of articles or issue payloads per period.
        exchange(config,{'generated_at':revision,'articles':[],'catalogs':catalogs,'removed_ids':removed})
        conn.executemany('DELETE FROM synced WHERE id=?',[(i,) for i in removed])
        conn.execute('DELETE FROM pending_sync WHERE id=1')
        conn.commit()
    print('Article DB sync complete; all five display periods retained.',flush=True)


def locked_sync(config,limit=20000):
    try:
        import msvcrt
    except ImportError:
        raise SystemExit('This worker requires Windows file locking; see docs/MACOS.md for supported local commands.') from None
    with (news.ROOT/'data/article-sync.lock').open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        synchronize(config,limit=limit)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',action='store_true')
    p.add_argument('--enable',action='store_true',help='Enable automatic article sync after a successful initial import')
    p.add_argument('--limit',type=int,default=1000)
    args=p.parse_args()
    if not 1<=args.limit<=20000:p.error('limit must be 1-20000')
    if args.enable and not args.run:p.error('--enable requires --run')
    if not args.run:
        with closing(sqlite3.connect('file:'+str(news.ROOT/'data/news.sqlite')+'?mode=ro',uri=True)) as conn:
            count=conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        print(f'PLAN ONLY: up to {count} existing articles, {args.limit} row cap, batches <=50. No model calls. Missing historical first-collection timestamps remain null.')
    else:
        from publish_news import CONFIG
        config=json.loads(CONFIG.read_text(encoding='utf-8'))
        if not config.get('enabled'):raise SystemExit('Hosted publication disabled')
        locked_sync(config,args.limit)
        if args.enable:
            config['article_sync_enabled']=True
            temporary=CONFIG.with_suffix('.tmp')
            temporary.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
            temporary.replace(CONFIG)
            print('Automatic article sync enabled.')
