"""Offline production-path checks: boundaries, persistence, budgets and fail-closed retries."""
import contextlib
import io
import json
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import openrouter_worker as w
import first_pass as fp
import luna_queue
import review_harness as h

now = w.news.utcnow()
article = {'id':'new','title':'Nvidia announced a partnership with Cornelis today.',
           'summary':'Cornelis, a company creating networking technology, partners with Nvidia.',
           'url':'https://example.com/new','published_at':now.isoformat()}
old = dict(article,id='old',url='https://example.com/old')
stale = dict(article,id='stale',url='https://example.com/stale',published_at=(now-timedelta(hours=25)).isoformat())
raw = {'certainty':'confirmed','novelty':'new','event':'contract','relative_size':None,
       'evidence':[0],'semantics':{'topics':[],'relations':[]}}
raw['companies'] = [{'symbol':'NVDA','decision':'relevant','quote':0}]
raw['impact'] = {'score':55, 'scope':'company', 'target':'Nvidia', 'confidence':'low',
    'expectations':'unknown', 'change':'New partnership.', 'magnitude':'Not quantified.',
    'realization':'Announced.', 'duration':'Unknown.', 'reason':'Commercial impact is not quantified.',
    'evidence':[0], 'expectation_evidence':[]}
envelope = {'usage':{'cost':.00006}, 'choices':[{'finish_reason':'stop','message':{'content':json.dumps(raw)}}]}

with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
    root = Path(tmp)
    config = root/'config.json'
    db = root/'first-pass.sqlite'
    key = root/'key.dpapi'
    key.touch()
    home = root/'data/codex-review'
    for obj,name,value in [(w,'CONFIG',config),(fp,'CACHE',db),(w.api,'KEY_FILE',key),
                           (w.news,'ROOT',root),(fp.ca,'ROOT',root),(h,'HOME',home)]:
        stack.enter_context(patch.object(obj,name,value))
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    loadkey = stack.enter_context(patch.object(w.api,'load_key',return_value='test-only'))
    fetch = stack.enter_context(patch.object(fp.ca,'fetch_body',side_effect=ValueError('unavailable')))
    request = stack.enter_context(patch.object(w,'request',return_value=envelope))
    stack.enter_context(patch.object(w.time,'sleep'))
    stack.enter_context(patch.object(w.news,'load_saved',return_value=([old],{})))
    assert w.run([article],now,path=db,execute=True)==0
    request.assert_not_called()
    w.activate()
    assert w.enabled()
    cfg=json.loads(config.read_text(encoding='utf-8'))
    cfg['relation_verifier']=True
    config.write_text(json.dumps(cfg),encoding='utf-8')
    verification=stack.enter_context(patch('relation_verifier.verify',return_value={
        'version':1,'status':'done','checks':[]}))
    assert w.run([old,stale,article],now,path=db,execute=False)==1
    request.assert_not_called()
    publish=[]
    fp.run([old,stale,article],now,path=db,execute=True,on_batch=lambda:publish.append(1))
    record=fp.records(db)[fp.ca.fingerprint(article)]
    assert record['provider']=='openrouter' and record['status']=='done'
    assert verification.call_count==1 and record['facts']['verification']['status']=='done'
    assert record['coverage']=='제목·RSS 요약' and fp.current(record)
    assert fp.attach([dict(article)],db)[0]['qwen']['facts']['event']=='contract'
    assert publish==[1] and request.call_count==1
    w.run([old,stale,article],now,path=db,execute=True)
    assert request.call_count==1 and fetch.call_count==1
    assert luna_queue.prepare(now) is None
    # One settled but invalid response does not block another article or the next cycle.
    bad=dict(article,id='bad',url='https://example.com/bad')
    good=dict(article,id='good',url='https://example.com/good')
    with patch.object(w.api,'parse',side_effect=[ValueError('invalid evidence'), {'event':'contract'}]):
        w.run([bad,good],now,path=db,execute=True)
    failed_bad=fp.records(db)[fp.ca.fingerprint(bad)]
    assert failed_bad['error_scope']=='article' and failed_bad['status']=='error'
    assert fp.records(db)[fp.ca.fingerprint(good)]['status']=='done'
    # Legacy errors also unblock, but only with a saved, settled response.
    with contextlib.closing(sqlite3.connect(db)) as legacy:
        failed_bad.pop('error_scope')
        legacy.execute('UPDATE first_pass SET body=? WHERE key=?',
                       (json.dumps(failed_bad),fp.ca.fingerprint(bad)))
        legacy.commit()
    before=request.call_count
    fresh=dict(article,id='fresh',url='https://example.com/fresh')
    w.run([bad,fresh],now,path=db,execute=True)
    assert request.call_count==before+1
    # Keep the remaining pre-existing budget assertions independent of this scenario.
    with contextlib.closing(sqlite3.connect(db)) as cleanup:
        for item in (bad,good,fresh):
            fingerprint=fp.ca.fingerprint(item)
            cleanup.execute('DELETE FROM first_pass WHERE key=?',(fingerprint,))
            cleanup.execute('DELETE FROM openrouter_calls WHERE key=?',(fingerprint,))
        cleanup.commit()
    baseline=request.call_count
    with contextlib.closing(sqlite3.connect(db)) as conn:
        cost, body=conn.execute('SELECT cost,body FROM openrouter_calls').fetchone()
        assert cost==.00006 and json.loads(body)==envelope
        with patch.dict(w.LIMITS,day=.005):
            assert w.reserve(conn,'over-budget',now) is None
        for limit_name in ('month','total'):
            with patch.dict(w.LIMITS,{limit_name:.005}):
                assert w.reserve(conn,'over-budget',now) is None
    # Timeouts remain charged conservatively and do not loop on the next minute.
    another=dict(article,id='another',url='https://example.com/another')
    request.side_effect=TimeoutError()
    w.run([another],now,path=db,execute=True)
    assert request.call_count==baseline+1
    w.run([dict(article,id='third',url='https://example.com/third')],now,path=db,execute=True)
    assert request.call_count==baseline+1
    failed=fp.records(db)[fp.ca.fingerprint(another)]
    assert failed['status']=='error' and 'facts' not in failed and failed['attempts']==1
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT SUM(cost) FROM openrouter_calls').fetchone()[0]==.01006
    # Daily v4 keeps title audit and exact evidence checks without requiring Luna files.
    folder=home/'test-day'
    h.write(folder/'packet.json',{'version':4,'date':now.date().isoformat(),'articles':[{'id':'new'}],
        'sample_ids':[],'last_collected':now.isoformat(),'prepared_at':now.isoformat()})
    h.write(folder/'snapshot.json',{'new':article})
    h.write(folder/'agenda.json',{'html':'Test agenda','errors':[]})
    review={'reviewed_ids':['new'],'reviewed_source_ids':['new'],'items':[{
        'id':'new','level':'중요','summary_en':article['title'],'reason_ko':'원문 계약 확인','evidence':article['title']}]}
    h.draft(folder,review)
    assert (folder/'draft.json').exists()
    try:
        h.draft(folder,dict(review,reviewed_source_ids=[]))
        raise AssertionError('Missing title audit accepted')
    except ValueError:
        pass
print('PASS: offline activation, new-only/24h queue, DB/cache, body fallback, budgets, cooldown, Luna bypass and final audit')
