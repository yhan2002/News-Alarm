"""Free checks for shared budgets, uncertain retention, display filtering and cache reuse."""
import copy
import json
import sqlite3
from unittest.mock import patch
import relation_verifier as v
import openrouter_worker as w
import semantics
import company_links


def main():
    now = w.news.utcnow()
    text = 'Walmart competes with DoorDash in delivery services, not in an armed conflict.'
    relation = {'source':'Walmart','target':'DoorDash','kind':'competition','quote':text}
    facts = {'semantics':{'version':2,'topics':[],'relations':[relation]},
             'companies':{'version':1,'rows':[{'symbol':'WMT','decision':'relevant','quote':text},
                                             {'symbol':'DASH','decision':'relevant','quote':text}]},
             'impact':{'score':70}}
    original = copy.deepcopy(facts)
    checks = v.plan(facts,text)
    assert checks and any(c['kind']=='relation' for c in checks)
    def response(value):
        return {'usage':{'cost':.00003},'answers':{c['id']:{'type':'noul','noul':value} for c in checks}}
    with sqlite3.connect(':memory:') as conn:
        w.setup(conn)
        with patch.object(v,'request',return_value=response(.5)) as call:
            result = v.verify(conn,facts,text,'fake',now)
            assert result['status']=='done' and call.call_count==1
            again=v.verify(conn,facts,text,'fake',now)
            assert again['cached'] and call.call_count==1
            assert conn.execute('SELECT SUM(cost) FROM openrouter_calls').fetchone()[0]==.00003
        uncertain=dict(facts,verification=result)
        assert v.apply(uncertain)['semantics']['relations']==[relation]
        assert facts==original
        no=dict(facts,verification=v.parse(response(.01),checks))
        assert not v.apply(no)['semantics']['relations']
        assert v.apply(no)['impact']==facts['impact']
        article={'title':text,'summary':'','sentiment':{'score':-.3,'engine':'ProsusAI/finbert'},
                 'qwen':{'status':'done','coverage':'본문 추출','facts':no}}
        assert 'DASH' not in {r['symbol'] for r in company_links.resolve(article)}
        with patch('first_pass.evidence_input',return_value=(text,'본문 추출')):
            attached=semantics.attach(copy.deepcopy(article))
        assert attached['sentiment']==article['sentiment'] and not attached['semantic_relations']
        article['qwen']['facts']=uncertain
        with patch('first_pass.evidence_input',return_value=(text,'본문 추출')):
            attached=semantics.attach(copy.deepcopy(article))
        assert attached['semantic_relations'] and attached['relation_verification']['uncertain']>0
        for limit in ('day','month','total'):
            with patch.dict(w.LIMITS,{limit:.00004}):
                assert w.reserve(conn,'jev:new',now,amount=v.RESERVE,optional_limits=v.LIMITS) is None
        for limit in ('day','month'):
            with patch.dict(v.LIMITS,{limit:.00004}):
                assert w.reserve(conn,'jev:new',now,amount=v.RESERVE,optional_limits=v.LIMITS) is None
    with sqlite3.connect(':memory:') as conn:
        w.setup(conn)
        with patch.object(v,'request',side_effect=TimeoutError()) as call:
            assert v.verify(conn,facts,text,'fake',now)['status']=='unavailable'
            assert v.verify(conn,facts,text,'fake',now)['status']=='unavailable'
            assert call.call_count==1
            assert conn.execute('SELECT SUM(cost) FROM openrouter_calls').fetchone()[0]==v.RESERVE
    for bad in (True,float('nan'),float('inf'),-1,2,None):
        env=response(bad)
        try:
            v.parse(env,checks)
            raise AssertionError('Invalid probability accepted')
        except ValueError:
            pass
    assert not v.plan({'semantics':{'relations':[]}},text)
    print('PASS: budgets, no retry on uncertain delivery, cache, raw preservation, uncertain links and sentiment')


if __name__=='__main__':
    main()
