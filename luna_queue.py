"""Bounded hourly Luna packets; no model calls or Telegram delivery in this script."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
import review_harness as h


def active_folder(now):
    home = h.HOME/'luna-continuous'
    pending = sorted(p.parent for p in home.glob('*/packet.json') if not (p.parent/'complete.json').exists())
    return pending[0] if pending else home/now.strftime('%Y-%m-%d-%H')


def prepare(now):
    from openrouter_worker import enabled
    if enabled():
        print('STOP: OpenRouter single-stage analysis active. No duplicate Luna work.')
        return
    folder = active_folder(now)
    if (folder/'complete.json').exists():
        print('This hour already completed. No work.')
        return
    cache = h.luna_records()
    h.prepare(folder, now, 72, exclude_keys=cache, article_limit=100)
    packet = h.read(folder/'packet.json')
    packet['body_limit'] = 6
    h.write(folder/'packet.json', packet)
    if not packet['source_count']:
        h.write(folder/'complete.json', {'at':now.isoformat(),'count':0})
        print('STOP: current_batch_count=0. No articles to review.')
    else:
        print(f"REVIEW_REQUIRED: current_batch_count={packet['source_count']}; "
              f"queued_after_batch={packet['queued_after_batch']}; body_limit=6; folder={folder}")
        print('Review and commit this current batch. queued_after_batch counts OTHER articles only; zero does not mean stop.')
    return folder


def commit(folder, now):
    h.validate_luna_summaries(folder)
    snapshot = h.read(folder/'snapshot.json')
    summaries = {r['id']:r for r in h.read(folder/'luna-summaries.json')['items']}
    records = []
    for row in h.read(folder/'luna-filter.json')['items']:
        body = {'version':1,'at':now.isoformat(),'filter':row,'summary':summaries.get(row['id'])}
        records.append((h.fingerprint(snapshot[row['id']]),json.dumps(body,ensure_ascii=False),now.isoformat()))
    with closing(sqlite3.connect(h.HOME/'luna.sqlite',timeout=10)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS luna_reviews (key TEXT PRIMARY KEY, body TEXT NOT NULL, reviewed_at TEXT NOT NULL, report_day TEXT)')
        conn.executemany('INSERT INTO luna_reviews (key,body,reviewed_at) VALUES (?,?,?) ON CONFLICT(key) DO NOTHING', records)
    h.write(folder/'complete.json', {'at':now.isoformat(),'count':len(snapshot),'bodies':len(summaries)})
    print('Validated Luna results cached:',len(snapshot),'; bodies:',len(summaries),'; no Telegram.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare','filter','bodies','commit'])
    p.add_argument('--ids', nargs='+', default=[])
    args = p.parse_args()
    now = datetime.now(h.d.KST)
    folder = active_folder(now)
    if args.command == 'prepare':
        prepare(now)
    elif args.command == 'filter':
        ids = h.luna_filter(folder)
        h.write(folder/'summary-request.json', {'ids':ids})
        print(ids)
    elif args.command == 'bodies':
        allowed = h.luna_filter(folder)
        if not set(args.ids)<=set(allowed):
            raise ValueError('Only the validated six-body shortlist may be fetched')
        h.bodies(folder,args.ids)
    else:
        commit(folder,now)
