"""Offline checks: python test_semantics.py"""
from semantics import validate, attach
q='Walmart Just Declared War on DoorDash and Uber Eats, But Winning Will Not Be Easy.'
r={'topics':[{'topic':'geopolitics','quote':q},{'topic':'delivery_competition','quote':q}], 'relations':[{'source':'Walmart','target':'DoorDash','kind':'competition','quote':q},{'source':'Walmart','target':'Apple','kind':'competition','quote':q}]}
f=validate(r,q)
assert len(f['topics'])==1 and f['topics'][0]['topic']=='delivery_competition'
assert len(f['relations'])==1 and f['withheld']==2
assert not validate({'topics':[{'topic':'finance','quote':'This quote is not present in the article.'}],'relations':[]},q)['topics']
a={'title':q,'summary':'','qwen':{'status':'done','facts':{'semantics':f}}}
attach(a)
assert a['semantic_relations'][0]['target_key']=='DASH'
assert '정책·지정학' not in a['themes']
b={'title':q,'summary':'','themes':{'정책·지정학':['war']}}
attach(b)
assert b['semantic_status']=='pending' and not b['themes']
print('PASS: figurative war withheld, quote/participant grounding, ticker mapping, legacy pending')
from semantics import numbered_request, resolve_spans
from content_assessment import SCHEMA
schema,prompt,numbered,spans=numbered_request(q,SCHEMA)
resolved=resolve_spans({'evidence':[0],'semantics':{'topics':[],'relations':[{'source':'Walmart','target':'DoorDash','kind':'competition','quote':0}]}},spans)
assert resolved['evidence']==[q] and resolved['semantics']['relations'][0]['quote']==q
try:
    resolve_spans({'evidence':[99]},spans)
    raise AssertionError('Invalid span accepted')
except ValueError:pass
print('PASS: compact span IDs restore exact source and reject invented IDs')
bad={'topics':[{'topic':'ai','quote':'Airlines for America CEO Chris Sununu said U.S.'}],'relations':[{'source':'ORCL','target':'Larry Ellison','kind':'regulation','quote':'ORCL Slides After Larry Ellison Canceling His Stock Sale'},{'source':'anthropic','target':'ai','kind':'supply','quote':'anthropic wants an ai slowdown but offers no agreement'}]}
t='\n'.join([bad['topics'][0]['quote']]+[r['quote'] for r in bad['relations']])
checked=validate(bad,t)
assert not checked['topics'] and not checked['relations'] and checked['withheld']==3
print('PASS: Airlines/AI, executive/regulator, and conceptual supply hallucinations withheld')

from news import entities
assert [r['symbol'] for r in entities({'title':'GE Aerospace','summary':''})[0]] == ['GE']
assert [r['symbol'] for r in entities({'title':'Uber Eats','summary':''})[0]] == ['UBER']
q = 'GE Vernova has signed an agreement to supply turbines for ESB wind farm in Scotland.'
valid = validate({'topics':[{'topic':'energy','quote':q}], 'relations':[{'source':'GE Vernova','target':'ESB','kind':'supply','quote':q}]}, q)
assert len(valid['relations']) == 1 and valid['topics'][0]['topic'] == 'energy'
q = 'GE Vernova and ESB appeared in a roundup about supply chains today.'
assert not validate({'topics':[], 'relations':[{'source':'GE Vernova','target':'ESB','kind':'supply','quote':q}]}, q)['relations']
print('PASS: listing names and explicit non-universe counterparties; no co-mention inference')
for q, expected in [('Central banks increase gold reserves this quarter.', True),
                    ('USD/JPY rises as the yen weakens against the dollar.', True),
                    ('Goldman reports higher revenue this quarter.', False)]:
    result = validate({'topics':[{'topic':'dollar_strength','quote':q}], 'relations':[]}, q)
    assert bool(result['topics']) == expected
print('PASS: dollar/yen and gold demand grounded; unrelated company name rejected')

def relation_check(text, quote, source, target, kind, expected):
    row={'source':source,'target':target,'kind':kind,'quote':quote}
    assert bool(validate({'topics':[], 'relations':[row]},text)['relations']) == expected, quote

q='QurCan Therapeutics announced a research and collaboration agreement with Eli Lilly and Company.'
relation_check(q,q,'QurCan Therapeutics','Eli Lilly and Company','partnership',True)
relation_check(q,q,'Eli Lilly and Company','QurCan Therapeutics','partnership',True)
q='The company, which spun off from Intel in 2020, is competing with Nvidia. Cornelis offers an open architecture.'
t='Cornelis, a company creating networking technology, raised funds. '+q
relation_check(t,q,'Cornelis','Nvidia','competition',True)
relation_check('Cornelis, a company, raised funds. Another company launched a product. '+q,q,'Cornelis','Nvidia','competition',False)
for q,src,dst,kind in [
    ('QurCan Therapeutics and Lilly appeared in a report about industry collaboration.', 'QurCan Therapeutics','Lilly','partnership'),
    ('QurCan Therapeutics did not enter a collaboration agreement with Lilly.', 'QurCan Therapeutics','Lilly','partnership'),
    ('John Smith is competing with Nvidia for attention in an interview.', 'John Smith','Nvidia','competition'),
    ('Nvidia is competing with artificial intelligence in a fictional story.', 'Nvidia','artificial intelligence','competition'),
]:
    relation_check(q,q,src,dst,kind,False)
print('PASS: grounded external cooperation/competition; co-mentions, negation, people and concepts blocked')
