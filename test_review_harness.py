import json,tempfile
from pathlib import Path
from unittest.mock import patch
import review_harness as h
now=h.d.datetime(2026,9,10,19,0,tzinfo=h.d.KST)
a={'id':'a','title':'Example announced a share repurchase today.','summary':'No company relative size is provided.','url':'https://example.com/a','published_at':now.isoformat()}
with tempfile.TemporaryDirectory() as tmp:
    home=Path(tmp); folder=home/'2026-09-10'
    with patch.object(h,'HOME',home),patch.object(h.news,'load_saved',return_value=([a],{'last_collected':now.isoformat()})),patch.object(h.news,'entities',return_value=([],{})):
        h.prepare(folder,now)
        assert len(h.read(folder/'packet.json')['articles'])==1
        with patch.object(h,'fetch_body',return_value=(a['title'],a['url'],False)) as fetch:
            h.bodies(folder,['a']);h.bodies(folder,['a'])
            assert fetch.call_count==1
        h.write(folder/'agenda.json',{'html':'오늘 실적 발표: 예시','errors':[]})
        h.write(folder/'luna-filter.json',{'items':[{'id':'a','decision':'candidate','reason':'New buyback','evidence':a['title']}],'selected_ids':['a']})
        assert h.luna_filter(folder)==['a']
        h.write(folder/'luna-summaries.json',{'items':[{'id':'a','summary_en':a['title'],'evidence':a['title'],'coverage':'본문 추출'}]})
        assert h.validate_luna_summaries(folder)==['a']
        good=h.read(folder/'luna-filter.json')
        h.write(folder/'luna-filter.json',dict(good,items=[]))
        try:h.luna_filter(folder);raise AssertionError('Missing Luna classification accepted')
        except ValueError:pass
        h.write(folder/'luna-filter.json',good)
        review={'reviewed_ids':['a'],'reviewed_source_ids':['a'],'items':[{'id':'a','level':'중요','summary_en':a['title'],'reason_ko':'자사주 매입 발표','evidence':a['title']}]}
        h.draft(folder,review)
        assert '오늘 실적' in h.read(folder/'draft.json')['messages']['agenda']
        assert '오늘 실적' not in h.read(folder/'draft.json')['messages']['news']
        assert '1차 검토 완료' not in h.read(folder/'draft.json')['messages']['news']
        warning='⚠ 일정 일부 확인 실패:'
        for agenda_html in ('오늘 실적 발표: 예시', '오늘 실적 발표: 예시\n'+warning+' SNDK'):
            h.write(folder/'agenda.json',{'html':agenda_html,'errors':['SNDK']})
            h.draft(folder,review)
            assert h.read(folder/'draft.json')['messages']['agenda']==agenda_html
        bad=dict(review,reviewed_ids=[])
        try:h.draft(folder,bad);raise AssertionError('Missing review accepted')
        except ValueError:pass
        try:h.draft(folder,dict(review,reviewed_source_ids=[]));raise AssertionError('Missing source title accepted')
        except ValueError:pass
        packet=h.read(folder/'packet.json');h.write(folder/'packet.json',dict(packet,backlog=573))
        try:h.draft(folder,review);raise AssertionError('Legacy incomplete packet accepted')
        except ValueError:pass
        h.write(folder/'packet.json',packet)
        with patch.object(h.d,'DATA',home),patch.object(h.d,'destinations',return_value=('fake',['-1'])),patch.object(h.d,'telegram',return_value={'message_id':1}) as api:
            h.send(folder,now);h.send(folder,now)
            assert api.call_count==2
        assert h.read(folder/'done.json')['recipients']==1
        review['items'][0]['summary_en']='Changed summary.'
        try:h.draft(folder,review);raise AssertionError('Dispatch mutated')
        except ValueError:pass
        assert h.fingerprint(a) in h.read(home/'reviewed.json')
print('Harness checks passed: caching, complete review, immutable dispatch, duplicate prevention. No network.')

# Older material events must survive a flood of recent commentary; no newest-240 cut.
with tempfile.TemporaryDirectory() as tmp:
    home=Path(tmp)
    older=dict(a,id='earnings',title='Nvidia reports record quarterly revenue',published_at=(now-h.timedelta(hours=20)).isoformat())
    junk=[dict(a,id=f'j{i}',url=f'https://example.com/j{i}',title=f'Should you buy stock number {i}?') for i in range(250)]
    duplicate=dict(older,id='duplicate',url='https://other.example.com/copy')
    def entities(x): return ([{'symbol':'NVDA'}] if 'Nvidia' in x['title'] else [],{})
    with patch.object(h,'HOME',home),patch.object(h.news,'load_saved',return_value=(junk+[older,duplicate],{'last_collected':now.isoformat()})),patch.object(h.news,'entities',side_effect=entities):
        h.prepare(home/'first',now)
        packet=h.read(home/'first/packet.json')
        assert packet['source_count']==252 and packet['group_count']==251
        assert len(packet['articles'])>240 and len(packet['sample_ids'])==2
        assert packet['recommended_ids'][0] in ('earnings','duplicate')
        assert sum(len(g['member_ids']) for g in packet['articles'])==252
        h.write(home/'checkpoint.json',{'through':(now-h.timedelta(hours=4)).isoformat()})
        h.prepare(home/'next',now)
        p=h.read(home/'next/packet.json')
        assert 'earnings' not in [g['id'] for g in p['articles']]
        assert h.news.timestamp(p['window_start'])==now-h.timedelta(hours=6)
        h.write(home/'reviewed.json',{h.fingerprint(older):now.date().isoformat()})
        h.prepare(home/'catchup',now,72)
        catchup=h.read(home/'catchup/packet.json')
        assert catchup['source_count']==252
        assert h.news.timestamp(catchup['window_start'])==now-h.timedelta(hours=72)
print('Incremental window, event grouping, no recency cutoff and sampling checks passed.')

# Cached local first pass is incremental; failure never removes an article.
import first_pass as f
import io
facts={'semantics':{'version':2,'topics':[],'relations':[],'withheld':0},'certainty':'confirmed','novelty':'new','event':'repurchase','relative_size':None,'evidence':[a['title']]}
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'qwen.sqlite'
    with patch.object(f,'build_opener') as opener,patch.object(f.ca,'evaluate',return_value=facts) as evaluate:
        opener.return_value.open.side_effect=lambda *a,**k:io.BytesIO(b'{"models":[{"name":"qwen3.5:9b"}]}')
        f.run([a],now,path=path,execute=False)
        assert not path.exists() and not evaluate.called
        f.run([a],now,path=path,execute=True)
        f.run([a],now,path=path,execute=True)
        assert evaluate.call_count==1
        changed=dict(a,summary='Updated summary')
        evaluate.side_effect=ValueError('bad evidence')
        f.run([changed],now,path=path,execute=True)
        assert f.attach([changed],path)[0]['qwen']['status']=='error'
        f.run([changed],now,path=path,execute=True)
        assert evaluate.call_count==2
        assert f.attach([dict(a,id='unknown',title='Unseen title')],path)[0]['qwen']['status']=='pending'
print('Qwen incremental cache, dry-run and failure retention passed. No model or network calls.')
