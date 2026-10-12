"""Explicit, bounded historical impact reassessment. Offline plan unless --run."""
import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import timedelta
import first_pass as fp
import openrouter_worker as worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=int, default=12)
    parser.add_argument('--max-cost', type=float, default=.10)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', help='Resume a saved manifest without expanding its window or budget')
    args = parser.parse_args()
    if not 1 <= args.hours <= 24 or not 0 < args.max_cost <= .20:
        parser.error('hours 1..24; max-cost >0 and <=0.20')
    now = worker.news.utcnow()
    articles, _ = worker.news.load_saved(worker.news.ROOT/'data/news.sqlite')
    unique = {fp.ca.fingerprint(a):a for a in articles
              if now-timedelta(hours=args.hours) <= worker.news.timestamp(a['published_at']) <= now}
    articles = list(unique.values())
    target = None
    manifest = None
    if args.resume:
        from pathlib import Path
        target = Path(args.resume)
        manifest = json.loads(target.read_text(encoding='utf-8'))
        now = worker.news.timestamp(manifest['as_of'])
        args.hours, args.max_cost = manifest['hours'], manifest['cap']
        # Reload by saved keys; the rolling 12h window must not drop pending articles.
        all_articles, _ = worker.news.load_saved(worker.news.ROOT/'data/news.sqlite')
        frozen = manifest.get('articles')
        if frozen is None:
            frozen = [a for a in all_articles if now-timedelta(hours=args.hours) <= worker.news.timestamp(a['published_at']) <= now]
            frozen = [{k:a[k] for k in ('id','title','summary','url','published_at') if k in a} for a in frozen]
        unique = {fp.ca.fingerprint(a):a for a in frozen}
        manifest.update(keys=list(unique), articles=frozen)
        articles = list(unique.values())
    print(f'Fixed window {now-timedelta(hours=args.hours)} .. {now}: {len(articles)} inputs; cap ${args.max_cost:.2f}', flush=True)
    if not args.run:
        print('PLAN ONLY: no API calls or writes. Add --run to execute.')
        return
    try:
        import msvcrt
    except ImportError:
        raise SystemExit('This worker requires Windows file locking; see docs/MACOS.md for supported local commands.') from None
    with (fp.CACHE.parent/'qwen-worker.lock').open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        with closing(sqlite3.connect(fp.CACHE)) as conn:
            baseline = conn.execute('SELECT COALESCE(SUM(cost),0) FROM openrouter_calls WHERE julianday(at)<julianday(?)',
                                    (now.isoformat(),)).fetchone()[0]
        worker.LIMITS = dict(worker.LIMITS, total=min(worker.LIMITS['total'], baseline+args.max_cost))
        target = target or worker.news.ROOT/'data'/f'impact-reassessment-{now:%Y%m%d-%H%M%S}.json'
        manifest = manifest or {'as_of':now.isoformat(), 'hours':args.hours, 'keys':list(unique), 'cap':args.max_cost}
        manifest.setdefault('articles', [{k:a[k] for k in ('id','title','summary','url','published_at') if k in a} for a in articles])
        target.write_text(json.dumps(manifest), encoding='utf-8')
        def completed():
            records = fp.records()
            return sum(records.get(k,{}).get('status') == 'done' and
                       records.get(k,{}).get('facts',{}).get('impact',{}).get('version') == 1 for k in unique)
        before = completed()
        while before < len(articles):
            batch_started = worker.news.utcnow()
            worker.run(articles, batch_started, execute=True, reassess=True)
            done = completed()
            with closing(sqlite3.connect(fp.CACHE)) as conn:
                spent = conn.execute('SELECT COALESCE(SUM(cost),0) FROM openrouter_calls').fetchone()[0]-baseline
            manifest.update(done=done, remaining=len(articles)-done, charged=spent)
            target.write_text(json.dumps(manifest), encoding='utf-8')
            print(f'Progress {done}/{len(articles)}; charged ${spent:.6f}', flush=True)
            records = fp.records()
            if any(records.get(k,{}).get('cost_warning') or
                   records.get(k,{}).get('status') == 'error' and
                   worker.news.timestamp(records[k]['at']) >= batch_started for k in unique):
                raise SystemExit('Stopped on error/budget/cooldown; persisted results and originals retained.')
            if done == before:
                waiting = [worker.news.timestamp(records[k]['retry_after']) for k in unique
                           if records.get(k,{}).get('retry_after') and records[k].get('attempts',0) < 3
                           and worker.news.timestamp(records[k]['retry_after']) > worker.news.utcnow()]
                if waiting and spent + worker.RESERVE <= args.max_cost:
                    print('Waiting for existing retry cooldown; no request sent.', flush=True)
                    time.sleep(min(60, max(1, (min(waiting)-worker.news.utcnow()).total_seconds())))
                    continue
                raise SystemExit('Stopped without progress; inspect budget and retry limits.')
            before = done
        print('Reassessment complete. Existing publisher can sync these records.', flush=True)


if __name__ == '__main__':
    main()
