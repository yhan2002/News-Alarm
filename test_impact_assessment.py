"""Free, offline score propagation and evidence-boundary checks."""
import copy
import json
import briefing
import first_pass
import openrouter_pilot as api
import impact_assessment as ia

text = 'Alpha announced that its only product failed a phase 3 clinical trial.'
impact = {'score':95, 'scope':'company', 'target':'Alpha', 'confidence':'high',
          'expectations':'unknown', 'change':'New failure of the only product.',
          'magnitude':'Core commercial prospects affected; size unknown.',
          'realization':'Trial failure announced.', 'duration':'Potentially persistent.',
          'reason':'Failure of its only product could undermine its core valuation assumptions.',
          'evidence':[0], 'expectation_evidence':[]}
raw = {'certainty':'confirmed','novelty':'new','event':'regulation','relative_size':None,
       'evidence':[0],'semantics':{'topics':[],'relations':[]}, 'impact':impact}
def parse(value):
    return api.parse({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]},
                     text, '제목·RSS 요약', '2026-09-16T00:00:00Z', require_impact=True)

facts = parse(raw)
omitted = copy.deepcopy(raw)
omitted['semantics'] = {'topics':[{'topic':'other','quote':0}]}
recovered = parse(omitted)
assert recovered['impact']['score'] == 95
assert set(recovered['semantic_warnings']) == {'missing_relations','unclassified_topic'}
assert facts['score'] == 95 and facts['impact']['confidence'] == 'medium'
article = {'title':text, 'qwen':{'version':first_pass.VERSION,'provider':'openrouter',
           'model':api.MODELS[1],'status':'done','coverage':'제목·RSS 요약','facts':facts}}
assert briefing.importance(article)['score'] == 95
assert briefing.importance(article)['reasons'] == ['규제 결정']
for score in [0, 30, 35, 65, 70, 85, 90, 100]:
    candidate = copy.deepcopy(raw)
    candidate['impact']['score'] = score
    article['qwen']['facts'] = parse(candidate)
    result = briefing.importance(article)
    assert result['score'] == score and result['high'] == (score >= 70)
for change in [{'score':True}, {'score':73}, {'evidence':[-1]}, {'evidence':[True]},
               {'expectations':'documented'}, {'scope':'universe'}, {'reason':''}]:
    try:
        ia.validate(dict(impact, **change), [text], '본문 추출')
        raise AssertionError('Accepted malformed impact: '+str(change))
    except ValueError:
        pass
try:
    parse({k:v for k,v in raw.items() if k != 'impact'})
    raise AssertionError('Missing required impact accepted')
except ValueError:
    pass
request = api.payload(text, api.MODELS[1], impact=True)
assert 'POTENTIAL price impact' in request['messages'][0]['content']
assert request['reasoning']['enabled'] is False and request['max_tokens'] == 1600
print('Impact scoring: offline checks passed')
