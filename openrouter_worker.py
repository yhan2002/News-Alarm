"""Incremental, budgeted OpenRouter queue. Activation is explicit; no historical backfill."""
import argparse
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import first_pass as fp
import news
import openrouter_pilot as api

CONFIG = news.ROOT/'data/openrouter-worker.json'
MODEL = api.MODELS[1]
RESERVE = .01  # Reserve before sending; retain on timeout or unknown cost.
LIMITS = {'day': 1.0, 'month': 17.0, 'total': 18.0}


def config():
    if not CONFIG.exists():
        return {}
    value = json.loads(CONFIG.read_text(encoding='utf-8'))
    if value.get('enabled') and (value.get('model') != MODEL or not value.get('activated_at')):
        raise ValueError('Invalid OpenRouter worker configuration')
    if 'relation_verifier' in value and type(value['relation_verifier']) is not bool:
        raise ValueError('Invalid relation verifier flag')
    return value


def enabled():
    return config().get('enabled') is True


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS first_pass (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS openrouter_excluded (id TEXT PRIMARY KEY)')
    conn.execute('CREATE TABLE IF NOT EXISTS openrouter_calls (id INTEGER PRIMARY KEY, at TEXT NOT NULL, key TEXT NOT NULL, cost REAL NOT NULL, body TEXT)')
    conn.commit()


def activate():
    if enabled():
        print('Already active; original exclusion boundary retained.')
        return
    if not api.KEY_FILE.exists():
        raise ValueError('Saved encrypted API key required')
    api.load_key()  # Validate account access without a paid call or exposing the key.
    articles, _ = news.load_saved(news.ROOT/'data/news.sqlite')
    fp.CACHE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(fp.CACHE, timeout=10)) as conn, conn:
        setup(conn)
        conn.executemany('INSERT OR IGNORE INTO openrouter_excluded VALUES (?)', [(a['id'],) for a in articles])
    value = {'enabled': True, 'model': MODEL, 'activated_at': news.utcnow().isoformat()}
    tmp = CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(value), encoding='utf-8')
    tmp.replace(CONFIG)
    print(f'Activated for future article IDs; {len(articles)} existing IDs excluded. Limits: {LIMITS}')


def reserve(conn, key, now, *, amount=RESERVE, optional_limits=None):
    # One SQLite transaction protects the budget even if another launcher is added later.
    stamp = now.astimezone(timezone(timedelta(hours=9))).isoformat()
    conn.execute('BEGIN IMMEDIATE')
    try:
        day, month, total = conn.execute(
            'SELECT COALESCE(SUM(CASE WHEN substr(at,1,10)=? THEN cost ELSE 0 END),0), '
            'COALESCE(SUM(CASE WHEN substr(at,1,7)=? THEN cost ELSE 0 END),0), COALESCE(SUM(cost),0) '
            'FROM openrouter_calls', (stamp[:10], stamp[:7])).fetchone()
        if type(amount) not in (int, float) or not math.isfinite(amount) or not 0 < amount <= RESERVE:
            raise ValueError('Invalid reservation')
        if any(spent + amount > LIMITS[name] + 1e-9 for name, spent in [('day', day), ('month', month), ('total', total)]):
            conn.rollback()
            return None
        if optional_limits:
            optional_day, optional_month = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN substr(at,1,10)=? THEN cost ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN substr(at,1,7)=? THEN cost ELSE 0 END),0) "
                "FROM openrouter_calls WHERE key LIKE 'jev:%'", (stamp[:10], stamp[:7])).fetchone()
            if day + amount + RESERVE > LIMITS['day'] or any(
                    spent + amount > optional_limits[name]+1e-9 for name, spent in [('day', optional_day), ('month', optional_month)]):
                conn.rollback()
                return None
        call_id = conn.execute('INSERT INTO openrouter_calls (at,key,cost) VALUES (?,?,?)', (stamp,key,amount)).lastrowid
        conn.commit()
        return call_id
    except Exception:
        conn.rollback()
        raise


def body_input(article):
    path = news.ROOT/'data/codex-review/bodies'/(fp.ca.fingerprint(article)+'.json')
    if not path.exists():
        try:
            body, url, truncated = fp.ca.fetch_body(article['url'])
            cached = {'body':body, 'url':url, 'coverage':'본문 발췌' if truncated else '본문 추출'}
        except Exception as exc:
            cached = {'body':article.get('summary',''), 'url':article['url'],
                      'coverage':'제목·RSS 요약', 'error':type(exc).__name__}
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
        temp.replace(path)
    return fp.evidence_input(article)


def request(text, key, timeout):
    payload = api.payload(text, MODEL, impact=True)
    # Reject more expensive routes; input/output prices are USD per million tokens.
    payload['provider']['max_price'] = {'prompt': .03, 'completion': .13}
    req = Request(api.URL, data=json.dumps(payload).encode(), headers={
        'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(128001)
    if len(raw) > 128000:
        raise ValueError('Response too large')
    return json.loads(raw)


def http_error(exc, key, retry_count):
    """Keep bounded diagnostics without credentials; honor seconds and HTTP-date hints."""
    now = news.utcnow()
    header = (exc.headers.get('Retry-After', '') if exc.headers else '')[:200]
    delay = None
    try:
        delay = int(header) if header.isdigit() else (parsedate_to_datetime(header)-now).total_seconds()
        if not math.isfinite(delay) or delay < 0:
            delay = 0
    except (TypeError, ValueError, OverflowError):
        pass
    if delay is None:
        delay = (15, 30, 60)[min(retry_count, 2)] if exc.code == 429 else 3600
    try:
        body = exc.read(8193).decode('utf-8', errors='replace')[:8192]
    except (OSError, AttributeError):
        body = ''
    return {'http_status':exc.code, 'error_body':body.replace(key, '[REDACTED]'),
            'retry_after_header':header.replace(key, '[REDACTED]'),
            'retry_after':(now+timedelta(seconds=min(delay, 31536000))).isoformat()}


def request_with_retries(conn, fingerprint, record, call_id, text, key, deadline):
    while True:
        try:
            return request(text, key, min(60,max(1,deadline-time.monotonic()))), call_id
        except HTTPError as exc:
            count = record.get('rate_limit_retries', 0)
            detail = http_error(exc, key, count)
            record.update(detail)
            record['error'] = f'HTTP {exc.code}'
            if exc.code == 429 and count >= 3:
                record['retry_exhausted'] = True
            conn.execute('UPDATE openrouter_calls SET body=? WHERE id=?',
                         (json.dumps(detail,ensure_ascii=False),call_id))
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',
                         (fingerprint,json.dumps(record,ensure_ascii=False)))
            conn.commit()
            delay = max(0,(news.timestamp(detail['retry_after'])-news.utcnow()).total_seconds())
            # Long provider waits survive process exit; never truncate them to retry early.
            if exc.code != 429 or count >= 3 or delay > 60 or delay+45 >= deadline-time.monotonic():
                raise
            time.sleep(delay)
            if not enabled():
                raise
            next_id = reserve(conn, fingerprint, news.utcnow())
            if next_id is None:
                raise
            call_id = next_id
            record['rate_limit_retries'] = count+1
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',
                         (fingerprint,json.dumps(record,ensure_ascii=False)))
            conn.commit()


def run(articles, now, *, path=None, limit=100, seconds=900, execute=False, on_batch=None, reassess=False):
    if not 1 <= limit <= 1000 or not 1 <= seconds <= 1800:
        raise ValueError('Invalid worker bounds')
    cfg = config()
    if not cfg.get('enabled'):
        print('OpenRouter disabled. No API calls.')
        return 0
    path = path or fp.CACHE
    cached = fp.records(path)
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        excluded = {r[0] for r in conn.execute('SELECT id FROM openrouter_excluded')}
        # Older validation errors had no scope. Only release their global pause
        # when the latest saved response proves that its charge was settled.
        for fingerprint, record in cached.items():
            if record.get('provider') != 'openrouter' or record.get('error') != 'ValueError' or record.get('error_scope'):
                continue
            row = conn.execute('SELECT cost,body FROM openrouter_calls WHERE key=? ORDER BY id DESC LIMIT 1', (fingerprint,)).fetchone()
            try:
                saved = json.loads(row[1]) if row else {}
                cost = saved.get('usage', {}).get('cost')
                if type(cost) in (int, float) and math.isfinite(cost) and 0 <= cost <= RESERVE and row[0] == cost and saved.get('choices'):
                    record['error_scope'] = 'article'
            except (ValueError, TypeError, AttributeError):
                pass
    if any(r.get('provider')=='openrouter' and (r.get('cost_warning') or
           not reassess and r.get('status')=='error' and r.get('error_scope') != 'article'
           and r.get('retry_after') and news.timestamp(r['retry_after']) > now)
           for r in cached.values()):
        print('OpenRouter paused: retry cooldown or cost verification required.')
        return 0
    pending = []
    for a in sorted(articles, key=lambda a:a['published_at'], reverse=True):
        if (a['id'] in excluded and not reassess) or not now-timedelta(hours=24) <= news.timestamp(a['published_at']) <= now:
            continue
        r = cached.get(fp.ca.fingerprint(a), {})
        if r.get('provider') == 'openrouter' and (r.get('status') == 'done' and fp.same_input(a,r)
                and (not reassess or r.get('facts',{}).get('impact',{}).get('version') == 1) or r.get('attempts',0) >= 3 or r.get('retry_exhausted') or
                r.get('retry_after') and news.timestamp(r['retry_after']) > now):
            continue
        pending.append(a)
    print(f'OpenRouter new eligible: {len(pending)}; max {min(limit,100)} / {min(seconds,900)}s. Limits: {LIMITS}', flush=True)
    if not execute or not pending:
        return len(pending)
    if not api.KEY_FILE.exists():
        raise ValueError('Saved encrypted API key required; unattended input is forbidden')
    key = api.load_key()
    deadline = time.monotonic() + min(seconds,900)
    processed = 0
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        setup(conn)
        if reassess:
            conn.execute('CREATE TABLE IF NOT EXISTS impact_history (call_id INTEGER PRIMARY KEY, key TEXT NOT NULL, body TEXT NOT NULL)')
            conn.commit()
        for a in pending[:min(limit,100)]:
            if deadline-time.monotonic() < 45 or not enabled():
                break
            fingerprint = fp.ca.fingerprint(a)
            previous = cached.get(fingerprint,{})
            attempts = previous.get('attempts',0)+1 if previous.get('provider')=='openrouter' else 1
            call_id = reserve(conn, fingerprint, news.utcnow())
            if call_id is None:
                print('Budget limit reached; remaining articles stay pending.', flush=True)
                break
            if reassess and previous:
                conn.execute('INSERT INTO impact_history VALUES (?,?,?)',
                             (call_id,fingerprint,json.dumps(previous,ensure_ascii=False)))
            record = {'version':fp.VERSION,'model':MODEL,'provider':'openrouter','status':'error',
                      'attempts':attempts,'at':news.utcnow().isoformat(),
                      'rate_limit_retries':previous.get('rate_limit_retries',0)+(1 if previous.get('http_status') == 429 else 0),
                      'retry_after':(news.utcnow()+timedelta(hours=1)).isoformat()}
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)', (fingerprint,json.dumps(record)))
            conn.commit()
            response_settled = False
            try:
                text, record['coverage'] = body_input(a)
                started = time.monotonic()
                envelope, call_id = request_with_retries(conn,fingerprint,record,call_id,text,key,deadline)
                # Retain original response for free revalidation after validator fixes.
                usage = envelope.get('usage',{})
                cost = usage.get('cost')
                valid_cost = isinstance(cost,(int,float)) and not isinstance(cost,bool) and math.isfinite(cost) and cost >= 0
                conn.execute('UPDATE openrouter_calls SET cost=?,body=? WHERE id=?',
                             (cost if valid_cost else RESERVE,json.dumps(envelope,ensure_ascii=False),call_id))
                conn.commit()
                response_settled = valid_cost and cost <= RESERVE
                if not response_settled:
                    record['cost_warning'] = True
                result = api.parse(envelope,text,record['coverage'],a['published_at'],require_impact=True,require_companies=True)
                record.update(status='done', facts=result, usage=usage,
                              text_hash=fp.ca.hashlib.sha256((a['published_at']+'\n'+text).encode()).hexdigest(),
                              elapsed_seconds=round(time.monotonic()-started,3))
                if cfg.get('relation_verifier') is True and response_settled and deadline-time.monotonic() >= 15:
                    from relation_verifier import verify
                    result['verification'] = verify(conn, result, text, key, news.utcnow(), timeout=15)
                record.pop('retry_after')
                for field in ('error','http_status','error_body','retry_after_header','rate_limit_retries'):
                    record.pop(field, None)
                if not valid_cost or cost > RESERVE:
                    record['cost_warning'] = True
            except Exception as exc:
                record['error'] = f'HTTP {exc.code}' if isinstance(exc,HTTPError) else type(exc).__name__
                # A rejected article response must not stop unrelated articles.
                # Unknown charges, transport failures and provider limits still stop the queue.
                if response_settled and isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
                    record['error_scope'] = 'article'
                if not isinstance(exc,HTTPError):
                    record['retry_after'] = (news.utcnow()+timedelta(hours=1)).isoformat()
                    record.pop('http_status', None)
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)', (fingerprint,json.dumps(record,ensure_ascii=False)))
            conn.commit()
            processed += 1
            print(f"OpenRouter {a['id']}: {record['status']}", flush=True)
            if (record['status'] != 'done' and record.get('error_scope') != 'article') or record.get('cost_warning'):
                break  # Bounded 429 retries are exhausted/deferred; preserve the reservation.
            if processed < min(len(pending),limit,100):
                time.sleep(5)
    if processed and on_batch:
        on_batch()
    return len(pending)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true', help='Authorize future paid scheduled analysis; exclude all current IDs')
    parser.add_argument('--relation-verifier', choices=('enable','disable'), help='Explicit optional Jev activation; existing records are not reprocessed')
    args = parser.parse_args()
    if args.activate and args.relation_verifier:
        parser.error('Choose one activation operation')
    if args.activate or args.relation_verifier:
        import msvcrt
        with (fp.CACHE.parent/'qwen-worker.lock').open('a+b') as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            if args.activate:
                activate()
            else:
                value = config()
                if not value.get('enabled'):
                    raise ValueError('Activate the primary worker first')
                value['relation_verifier'] = args.relation_verifier == 'enable'
                tmp = CONFIG.with_suffix('.tmp')
                tmp.write_text(json.dumps(value), encoding='utf-8')
                tmp.replace(CONFIG)
                print('Relation verifier:', args.relation_verifier, '; shared monthly cap $5; no backfill.')
    else:
        print(json.dumps(config(),ensure_ascii=False))
