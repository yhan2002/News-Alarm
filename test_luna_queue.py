"""Hourly packet caps and completed-result reuse, without network or model calls."""
import contextlib
import io
import tempfile
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import first_pass
import luna_queue as q

h = q.h
now = datetime.now(h.d.KST)
articles = [{'id':str(i),'title':f'Company {i} announces update', 'summary':'Original facts',
             'url':f'https://example.com/{i}','published_at':now.isoformat()} for i in range(101)]
with tempfile.TemporaryDirectory() as tmp, patch.object(h,'HOME',Path(tmp)), patch.object(h.news,'load_saved',return_value=(articles,{'last_collected':now.isoformat()})), patch.object(first_pass,'records',return_value={}), contextlib.redirect_stdout(io.StringIO()):
    folder = q.prepare(now)
    assert h.read(folder/'packet.json')['source_count'] == 100
    assert h.read(folder/'packet.json')['queued_after_batch'] == 1
    assert q.active_folder(now+timedelta(hours=1)) == folder
    snapshot = h.read(folder/'snapshot.json')
    rows = [{'id':i,'decision':'candidate','reason':'check','evidence':a['title']} for i,a in snapshot.items()]
    h.write(folder/'luna-filter.json',{'items':rows,'selected_ids':list(snapshot)[:5]})
    try:
        h.luna_filter(folder)
        raise AssertionError('Body cap bypassed')
    except ValueError:
        pass
    h.write(folder/'luna-filter.json',{'items':rows,'selected_ids':list(snapshot)[:4]})
    ids = h.luna_filter(folder)
    assert len(ids) == 6
    for i in ids:
        h.write(h.HOME/'bodies'/(h.fingerprint(snapshot[i])+'.json'),{'body':snapshot[i]['title'],'coverage':'본문 추출'})
    h.write(folder/'luna-summaries.json',{'items':[{'id':i,'summary_en':snapshot[i]['title'],'evidence':snapshot[i]['title'],'coverage':'본문 추출'} for i in ids]})
    q.commit(folder,now)
    assert len(h.luna_records()) == 100
    assert len(h.luna_records(pending_only=True)) == 100
    try:
        h.draft(folder, {'reviewed_ids':[], 'reviewed_source_ids':[], 'items':[]})
        raise AssertionError('Incomplete final review accepted')
    except ValueError:
        pass
    assert len(h.luna_records(pending_only=True)) == 100
    reported = dict(snapshot)
    for a in reported.values():
        a['luna'] = h.luna_records()[h.fingerprint(a)]
    h.mark_luna_reported(dict(list(reported.items())[:2]),now.date().isoformat())
    assert len(h.luna_records(pending_only=True)) == 98
    assert len(h.luna_records()) == 100
    h.write(folder/'snapshot.json', reported)
    h.write(folder/'body-attempts.json', ids)
    h.write(folder/'agenda.json', {'html':'Test agenda','errors':[]})
    with contextlib.closing(sqlite3.connect(h.HOME/'luna.sqlite')) as conn, conn:
        conn.execute('INSERT INTO luna_reviews (key,body,reviewed_at) VALUES (?,?,?)',
                     ('arrived-after-snapshot',json.dumps({'at':now.isoformat()}),now.isoformat()))
    chosen = reported[ids[0]]
    h.draft(folder, {'reviewed_ids':[a['id'] for a in h.read(folder/'packet.json')['articles']],
                    'reviewed_source_ids':list(reported), 'items':[{'id':chosen['id'],
                    'level':'중요','summary_en':chosen['title'],'reason_ko':'Verified event',
                    'evidence':chosen['title']}]})
    assert (folder/'draft.json').exists()
    assert set(h.luna_records(pending_only=True)) == {'arrived-after-snapshot'}
    assert len(h.luna_records()) == 101
    next_folder = q.prepare(now+timedelta(hours=1))
    assert h.read(next_folder/'packet.json')['source_count'] == 1
    with contextlib.redirect_stdout(io.StringIO()) as output:
        assert q.prepare(now+timedelta(hours=2)) == next_folder
    assert 'REVIEW_REQUIRED: current_batch_count=1; queued_after_batch=0' in output.getvalue()
    assert not (next_folder/'complete.json').exists()
print('100-title/6-body cap, interrupted batch reuse, validation and incremental cache checks passed.')
