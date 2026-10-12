"""Incremental local Qwen facts, never a discard filter. Dry-run by default."""
import argparse
import json
import sqlite3
import time
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone, timedelta
from urllib.request import build_opener, ProxyHandler
import content_assessment as ca
import news

MODEL = 'qwen3.5:9b'
CACHE = ca.ROOT / 'data' / 'first-pass.sqlite'
VERSION = 4


def evidence_input(article, coverage=None):
    """Reuse a fetched article body; never fetch or claim full coverage on a read."""
    body_modes = ('본문 추출', '본문 발췌')
    if coverage is None or coverage in body_modes:
        path = ca.ROOT/'data'/'codex-review'/'bodies'/(ca.fingerprint(article)+'.json')
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            body = cached.get('body')
            if cached.get('coverage') in body_modes and isinstance(body, str) and len(body) >= 400:
                mode = '본문 발췌' if len(body) > 12000 else cached['coverage']
                return article['title']+'\n'+body[:12000], mode
        except (OSError, ValueError, AttributeError):
            pass
        if coverage in body_modes:
            raise ValueError('Assessed source body is unavailable')
    return article['title']+'\n'+article.get('summary','')[:1600], '제목·RSS 요약'


def same_input(article, record):
    text, coverage = evidence_input(article)
    return record.get('coverage') == coverage and record.get('text_hash') == ca.hashlib.sha256((article.get('published_at','')+'\n'+text).encode()).hexdigest()


def records(path=CACHE):
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path)) as conn:
        return {k:json.loads(v) for k,v in conn.execute('SELECT key, body FROM first_pass')}


def current(record):
    return record.get('version') == VERSION and (record.get('model') == MODEL or
        record.get('provider') == 'openrouter' and record.get('model') == 'qwen/qwen3.7-flash')


def progress(articles, now, path=CACHE):
    cached = records(path)
    counts = dict(done=0, pending=0, retry_wait=0, exhausted=0)
    for a in articles:
        r = cached.get(ca.fingerprint(a), {})
        if not current(r):
            state = 'pending'
        elif r.get('status') == 'done':
            state = 'done' if same_input(a, r) else 'pending'
        elif r.get('attempts', 0) >= 3:
            state = 'exhausted'
        elif r.get('retry_after') and news.timestamp(r['retry_after']) > now:
            state = 'retry_wait'
        else:
            state = 'pending'
        counts[state] += 1
    return counts


def attach(articles, path=CACHE):
    cached = records(path)
    for a in articles:
        r = cached.get(ca.fingerprint(a), {})
        a['qwen'] = r if current(r) and (r.get('status') != 'done' or same_input(a, r)) else {'status':'pending'}
    return articles


def run(articles, now, *, path=CACHE, limit=100, seconds=900, execute=False, on_batch=None):
    import openrouter_worker
    if openrouter_worker.enabled():
        return openrouter_worker.run(articles,now,path=path,limit=limit,seconds=seconds,execute=execute,on_batch=on_batch)
    if not 1 <= limit <= 1000 or not 1 <= seconds <= 1800:
        raise ValueError('limit 1-1000; seconds 1-1800')
    cached = records(path)
    pending = []
    for a in sorted(articles, key=lambda a:a['published_at'], reverse=True):
        if not now-timedelta(hours=72) <= news.timestamp(a['published_at']) <= now:
            continue
        r = cached.get(ca.fingerprint(a), {})
        if current(r) and ((r['status']=='done' and same_input(a,r)) or (r['status']!='done' and r.get('attempts',0)>=3)):
            continue
        if current(r) and r.get('retry_after') and news.timestamp(r['retry_after'])>now:
            continue
        pending.append(a)
    recent = [a for a in articles if now-timedelta(hours=72) <= news.timestamp(a['published_at']) <= now]
    print('Qwen progress (72h):', json.dumps(progress(recent, now, path)))
    print(f'Qwen pending eligible: {len(pending)}; cap {limit} articles / {seconds}s; local only. Errors are not successful reviews.')
    if not execute or not pending:
        return len(pending)
    with build_opener(ProxyHandler({})).open('http://127.0.0.1:11434/api/tags',timeout=5) as response:
        models=json.loads(response.read(1_000_000))['models']
    installed=next((m for m in models if m.get('name')==MODEL),None)
    if not installed or installed.get('remote_host') or installed.get('remote_model'):
        raise ValueError(f'Exact local {MODEL} installation required')
    path.parent.mkdir(parents=True,exist_ok=True)
    from semantics import VERSION as semantic_version
    text_cache = {r['text_hash']:r['facts'] for r in cached.values() if current(r) and r.get('status')=='done' and r.get('text_hash') and r.get('facts',{}).get('semantics',{}).get('version')==semantic_version}
    deadline=time.monotonic()+seconds
    processed = 0
    with closing(sqlite3.connect(path,timeout=5)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS first_pass (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        for a in pending[:limit]:
            remaining=deadline-time.monotonic()
            if remaining<1:break
            key=ca.fingerprint(a); old=cached.get(key,{})
            attempts=old.get('attempts',0)+1 if current(old) and old.get('status')!='done' else 1
            record={'version':VERSION,'model':MODEL,'status':'error','attempts':attempts,
                    'at':now.isoformat(),'coverage':'제목·RSS 요약','retry_after':(now+timedelta(hours=1)).isoformat()}
            # Persist claim: a killed process leaves an explicit retryable error, never a completed review.
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',(key,json.dumps(record)))
            conn.commit()
            try:
                text, record['coverage'] = evidence_input(a)
                text_hash=ca.hashlib.sha256((a.get('published_at','')+'\n'+text).encode()).hexdigest()
                started=time.monotonic()
                result=text_cache.get(text_hash)
                reused=result is not None
                if result is None:
                    result=ca.evaluate(text,MODEL,coverage=record['coverage'],timeout=min(120,remaining),semantics=True,published_at=a['published_at'])
                facts={k:result[k] for k in ('certainty','novelty','event','evidence','relative_size','semantics')}
                facts['review_required'] = result.get('review_required', False)
                record.update(status='done',facts=facts,text_hash=text_hash,reused=reused,elapsed_seconds=round(time.monotonic()-started,3))
                text_cache[text_hash]=facts
                record.pop('retry_after')
            except Exception as exc:
                record['error']=type(exc).__name__
            record['at'] = datetime.now(timezone.utc).isoformat()
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',(key,json.dumps(record,ensure_ascii=False)))
            conn.commit(); cached[key]=record
            print(f"Qwen processed {key}: {record['status']}; attempt {attempts}", flush=True)
            processed += 1
            if on_batch and processed % 100 == 0:
                on_batch()
    if on_batch and processed % 100:
        on_batch()
    return len(pending)


def publish_batch():
    from publish_news import CONFIG
    config = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    if not config.get('enabled'):
        return
    try:
        # A long-running analyzer must not publish through stale imported review code.
        result = subprocess.run([sys.executable, '-u', str(ca.ROOT/'publish_news.py'), '--upload'],
                                cwd=ca.ROOT, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Publisher failed; inspect local publisher independently')
        print('Qwen batch: hosted snapshot updated.', flush=True)
    except Exception as exc:
        # Publication failures must not discard successful analysis or stop the queue.
        print(f'Qwen batch: publication failed ({type(exc).__name__}); retry next batch.', flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',action='store_true')
    p.add_argument('--limit',type=int,default=100)
    p.add_argument('--seconds',type=int,default=900)
    p.add_argument('--publish',action='store_true',help='Publish after each 100 attempts and final partial batch')
    args=p.parse_args()
    # Windows releases the byte lock on exit, including crashes. All CLI callers share it.
    try:
        import msvcrt
    except ImportError:
        raise SystemExit('This worker requires Windows file locking; see docs/MACOS.md for supported local commands.') from None
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with (CACHE.parent/'qwen-worker.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Qwen worker already running.')
        articles,_=news.load_saved(ca.ROOT/'data/news.sqlite')
        run(articles,datetime.now(timezone.utc),limit=args.limit,seconds=args.seconds,
            execute=args.run,on_batch=publish_batch if args.publish else None)


if __name__=='__main__':
    if sys.stdout is None:  # pythonw: keep diagnostics without creating a console window.
        from contextlib import redirect_stdout, redirect_stderr
        import traceback
        log_dir = ca.ROOT/'data'
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir/'first-pass.out.log').open('a', encoding='utf-8', buffering=1) as out, \
             (log_dir/'first-pass.err.log').open('a', encoding='utf-8', buffering=1) as err, \
             redirect_stdout(out), redirect_stderr(err):
            try:
                main()
            except Exception:
                traceback.print_exc()
                sys.exit(1)
    else:
        main()
