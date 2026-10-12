"""Offline regression: company identity, negative decisions and sector inputs."""
import copy
from contextlib import closing
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import news
import company_links as cl
import openrouter_pilot as api
import semantics


def symbols(text):
    return {r['symbol'] for r in news.entities({'title':text,'summary':''})[0]}


headline = 'Arm Stock Surges As Meta’s Muse Reportedly Fuels CPU Rally'
assert {'ARM', 'META'} <= symbols(headline)
for text in ['Meta Surges on Excitement Over Muse Personal AI Agent',
             'Arm announced a new processor']:
    rows = cl.candidates(text)
    assert rows
    cl.validate([{'symbol':r['symbol'], 'decision':'relevant', 'quote':0} for r in rows], text, [text])
for text in ['Arm exercises improve strength.', 'Meta analysis of clinical trials.']:
    assert not symbols(text) & {'ARM', 'META'}

for text, symbol in [
    ('SpaceX pursues the holy grail of rocketry.', 'GRAL'),
    ('Popular ways to save on groceries.', 'BPOP'),
    ('A progressive policy wins support.', 'PGR'),
    ('Apple harvests and the Amazon rainforest.', 'AAPL'),
    ('Apple harvests and the Amazon rainforest.', 'AMZN'),
    ('A tapestry of cultures and an oracle of ancient Greece.', 'TPR'),
    ('A tapestry of cultures and an oracle of ancient Greece.', 'ORCL'),
    ('The alphabet is taught to children.', 'GOOGL'),
    ('DASH through the finish line, under the HOOD.', 'DASH'),
]:
    assert symbol not in symbols(text), (text,symbol)
for text, symbol in [('GRAIL, Inc. reports results.', 'GRAL'),
                     ('$PGR reports earnings.', 'PGR'), ('NASDAQ: AAPL reports results.', 'AAPL'),
                     ('Nvidia and Microsoft announce a partnership.', 'NVDA'),
                     ('DoorDash and Uber Eats compete.', 'DASH')]:
    assert symbol in symbols(text), (text,symbol)

text = 'Grail announced a cancer screening collaboration with Nvidia.'
spans = [text]
decisions = [{'symbol':'GRAL','decision':'relevant','quote':0},
             {'symbol':'NVDA','decision':'relevant','quote':0}]
review = cl.validate(decisions,text,spans)
relation = {'source':'Grail','target':'Nvidia','kind':'partnership','quote':text}
article = {'id':'test','title':text,'summary':'','published_at':news.iso(news.utcnow()),
           'url':'https://example.com/test','source':'test','sentiment':{'label':'neutral','score':0,'engine':'test'},
           'qwen':{'status':'done','facts':{'companies':review,'semantics':{
               'version':semantics.VERSION,'topics':[],'relations':[relation]}}}}
assert not symbols(text)&{'GRAL'}  # Ambiguous until reviewed.
assert {r['symbol'] for r in semantics.attach(copy.deepcopy(article))['links']} == {'GRAL','NVDA'}
assert semantics.attach(copy.deepcopy(article))['semantic_relations'][0]['source_key']=='GRAL'
for decision in ['unrelated','uncertain']:
    denied = copy.deepcopy(article)
    denied['qwen']['facts']['companies']['rows'][0]['decision']=decision
    out=semantics.attach(denied)
    assert {r['symbol'] for r in out['links']}=={'NVDA'}
    assert not out['semantic_relations'], 'Rejected company must not survive through relation endpoints'
    with patch('first_pass.attach',side_effect=lambda rows:rows), patch('first_pass.progress',return_value={}), patch('review_harness.luna_records',return_value={}):
        doc=news.dashboard([denied],{},24)
    assert {r['symbol'] for r in doc['articles'][0]['links']}=={'NVDA'}, 'Dashboard must not restore rejected keywords'
    assert next(s for s in doc['stocks'] if s['symbol']=='GRAL')['count']==0

# Full-body mentions can be retained even when absent from the headline/summary.
body_only=copy.deepcopy(article);body_only['title']='A new commercial collaboration';body_only['summary']=''
assert {r['symbol'] for r in cl.resolve(body_only)}=={'GRAL','NVDA'}
for bad in [None, [], decisions[:1], decisions+[decisions[0]],
            [dict(decisions[0],quote=True),decisions[1]],
            [dict(decisions[0],symbol='AAPL'),decisions[1]],
            [dict(decisions[0],decision='yes'),decisions[1]]]:
    try:cl.validate(bad,text,spans)
    except ValueError:pass
    else:raise AssertionError('Invalid review accepted')
try:cl.validate(decisions,text,['An unrelated article with no company names.'])
except ValueError:pass
else:raise AssertionError('Unrelated quote accepted')

request=api.payload(text,api.MODELS[1],impact=True)
assert 'EVERY candidate' in request['messages'][0]['content']
assert request['max_tokens']==1600 and request['reasoning']['enabled'] is False
raw={'event':'contract','certainty':'confirmed','novelty':'new','relative_size':None,
     'evidence':[0],'semantics':{'topics':[],'relations':[dict(relation,quote=0)]},'companies':decisions}
envelope={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(raw)}}]}
parsed=api.parse(envelope,text,'본문 추출',article['published_at'],require_companies=True)
assert parsed['companies']==review and len(parsed['semantics']['relations'])==1
del raw['companies'];envelope['choices'][0]['message']['content']=json.dumps(raw)
try:api.parse(envelope,text,'본문 추출',article['published_at'],require_companies=True)
except ValueError:pass
else:raise AssertionError('Missing company review accepted')
print('PASS: ambiguous aliases, explicit identities, grounded reviews, negative overrides, relations and sector exclusion; no API calls')

# The saved-news reader used by reporting also removes stale persisted links.
with tempfile.TemporaryDirectory() as tmp:
    db=Path(tmp)/'news.sqlite'
    stored={'id':'old','title':'The holy grail of rocketry','summary':'',
            'url':'https://example.com/old','published_at':news.iso(news.utcnow()),
            'links':[{'symbol':'GRAL','name':'GRAIL','evidence':['grail']}]}
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE articles (id TEXT PRIMARY KEY, body TEXT)')
        conn.execute('CREATE TABLE runs (at TEXT, body TEXT)')
        conn.execute('INSERT INTO articles VALUES (?,?)',('old',json.dumps(stored)))
        conn.commit()
    with patch('first_pass.records',return_value={}):
        loaded,_=news.load_saved(db)
    assert loaded[0]['links']==[], 'Reports must not reuse stale DB keyword links'
    with closing(sqlite3.connect(db)) as conn:
        assert json.loads(conn.execute('SELECT body FROM articles').fetchone()[0])==stored
print('PASS: saved-news reader updates derived links without rewriting source records')

q="Walmart Just Declared War on DoorDash and Uber Eats, But Winning Won't Be Easy for 1 Simple Reason"
canonical={'topics':[],'relations':[{'source':'Walmart','target':'DoorDash, Inc.','kind':'competition','quote':q},{'source':'Walmart','target':'Uber Technologies','kind':'competition','quote':q}]}
fixed=semantics.validate(canonical,q,company_symbols={'WMT','DASH','UBER'})
assert {r['target'] for r in fixed['relations']}=={'DoorDash','Uber Eats'}
assert not semantics.validate(canonical,q,company_symbols={'WMT'})['relations']
print('PASS: approved company legal names normalize to exact quoted brands; rejected identities stay blocked')
