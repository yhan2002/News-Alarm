"""Bounded, resumable 24-hour GLM replay; explicit --run, no paid fallback."""
import argparse
from contextlib import closing
import getpass
import json
import os
import sqlite3
import time
from datetime import timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import evaluate_glm as glm
import first_pass as fp
import news

CACHE = news.ROOT / 'data/glm-relations.sqlite'


def report(message):
    line = news.utcnow().isoformat() + ' ' + message
    print(line, flush=True)
    with (news.ROOT/'data/glm-relations.out.log').open('a', encoding='utf-8') as handle:
        handle.write(line+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    now = news.utcnow()
    articles, _ = news.load_saved(news.ROOT/'data/news.sqlite')
    rows = [a for a in articles if now-timedelta(hours=24) <= news.timestamp(a['published_at']) <= now]
    rows.sort(key=lambda a:a['published_at'], reverse=True)
    print(f'{glm.MODEL}: {len(rows)} articles; serial; 25s initial spacing; each 429 adds 5s, capped at 60s.', flush=True)
    if not args.run:
        return
    key = os.environ.get('ZAI_API_KEY') or getpass.getpass('API key (hidden): ')
    if not key.strip():
        raise SystemExit('API key required')
    failures = 0
    delay = 25
    with closing(sqlite3.connect(CACHE)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS first_pass (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        cached = {k:json.loads(v) for k,v in conn.execute('SELECT key,body FROM first_pass')}
        for index, article in enumerate(rows):
            fingerprint = fp.ca.fingerprint(article)
            old = cached.get(fingerprint, {})
            if old.get('status') == 'done' and fp.same_input(article, old):
                continue
            text, coverage = fp.evidence_input(article)
            record = {'model':glm.MODEL, 'status':'error', 'coverage':coverage,
                      'text_hash':fp.ca.hashlib.sha256((article['published_at']+'\n'+text).encode()).hexdigest(),
                      'at':news.utcnow().isoformat()}
            start = time.monotonic()
            return_after_auth = False
            for attempt in range(8):
                record.pop('error', None)
                record.pop('provider_code', None)
                record['attempts'] = attempt + 1
                try:
                    request = Request(glm.URL, data=json.dumps(glm.payload(text)).encode(),
                                      headers={'Authorization':'Bearer '+key.strip(), 'Content-Type':'application/json'})
                    with urlopen(request, timeout=90) as response:
                        body = response.read(128001)
                    if len(body) > 128000:
                        raise ValueError('Response too large')
                    parsed = glm.parse(json.loads(body), text)
                    facts = parsed['validated']
                    if 'semantics' not in facts:
                        raise ValueError('Missing semantics')
                    record.update(status='done', facts=facts)
                except HTTPError as exc:
                    record['error'] = 'HTTP '+str(exc.code)
                    try:
                        record['provider_code'] = str(json.loads(exc.read(4000)).get('error',{}).get('code',''))
                    except (ValueError, AttributeError):
                        pass
                    if exc.code in (401,403):
                        return_after_auth = True
                except Exception as exc:
                    record['error'] = type(exc).__name__
                if record.get('error') == 'HTTP 429':
                    delay = min(60, delay + 5)
                if record['status'] == 'done' or record.get('error') != 'HTTP 429' or attempt == 7:
                    break
                record['retry_after_seconds'] = delay
                conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',(fingerprint,json.dumps(record)))
                conn.commit()
                report(f"{index+1}/{len(rows)} HTTP 429; retry SAME article in {delay}s (attempt {attempt+2}/8)")
                time.sleep(delay)
            record.pop('retry_after_seconds', None)
            record['at'] = news.utcnow().isoformat()
            failures = 0 if record['status'] == 'done' else failures + 1
            record['elapsed_seconds'] = round(time.monotonic()-start,2)
            conn.execute('INSERT OR REPLACE INTO first_pass VALUES (?,?)',(fingerprint,json.dumps(record)))
            conn.commit()
            report(f"{index+1}/{len(rows)} {record['status']} {record.get('error','')} {record.get('provider_code','')} {record['elapsed_seconds']}s")
            if return_after_auth or failures >= 3:
                report('STOPPED: three failed articles or authentication failure; saved results preserved.')
                return
            if index+1 < len(rows):
                time.sleep(delay)

    report('COMPLETE: all articles in this run processed; saved results retained.')


if __name__ == '__main__':
    main()
