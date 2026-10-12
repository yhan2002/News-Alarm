import json
from unittest.mock import patch
import content_assessment as c
import briefing as b

text = 'The board approved a $20 million share repurchase increase. Revenue and market cap were not provided.'
r = dict(summary_ko='이사회가 자사주 매입 확대를 승인했다.', fact_ko='자사주 매입 확대 발표', unknowns_ko='기업 대비 규모 미제공', evidence=[text], certainty='confirmed', novelty='new', event='repurchase', relative_size=None)
facts=c.validate(dict(r,level='필수 확인',impact='material'),text)
assert 'level' not in facts and 'impact' not in facts
assert c.classify(facts,'본문 추출')['level']=='중요'
for certainty,novelty,event in [('opinion','new','repurchase'),('confirmed','unknown','repurchase'),('confirmed','repeated','repurchase'),('confirmed','new','other')]:
    assert c.classify(dict(facts,certainty=certainty,novelty=novelty,event=event),'본문 추출')['level']=='참고'
quote='The contract represents 12% of the company annual revenue.'
large=dict(r,evidence=[quote],event='contract',relative_size=dict(percent=12,basis='annual_revenue',quote=quote))
facts=c.validate(large,quote)
assert c.classify(facts,'본문 추출')['level']=='필수 확인'
assert c.classify(facts,'본문 발췌')['level']=='중요'
assert c.classify(facts,'제목·요약만 확인')['level']=='중요'
assert c.classify(dict(facts,certainty='reported'),'본문 추출')['level']=='중요'
for bad in [dict(r,evidence=['Invented evidence that is not in the article.']),dict(r,relative_size={'percent':12,'basis':'market_cap','quote':text}),dict(r,novelty='maybe')]:
    try:
        c.validate(bad,text)
        raise AssertionError('Invalid evidence accepted')
    except ValueError:
        pass
article={'title':'Buyback news','summary':text,'url':'https://example.org/a','assessment':dict(c.classify(c.validate(r,text),'본문 추출'),status='assessed',coverage='본문 추출')}
assert b.importance(article)['high'] and b.one_line(article)==text.split(". ")[0]+"."
for url in ['http://example.org','https://user:pass@example.org','https://127.0.0.1','https://[::1]']:
    try:
        c.public_url(url)
        raise AssertionError('Unsafe URL accepted')
    except ValueError:
        pass
print('Fact validation and deterministic priority checks passed; no model calls.')

assert not any(k in c.validate(r,text) for k in ('summary_ko','fact_ko','unknowns_ko'))
assert c.SCHEMA['additionalProperties'] is False
assert c.classify(dict(c.validate(r,text),event='macro'),'본문 추출')['reason_ko'].find('거시 정책')>=0

assert c.source_sentence('Example Corp. approved $20 million. Revenue is unknown.') == 'Example Corp. approved $20 million.'
assert c.source_sentence('U.S. yields rose 0.5%. Stocks fell.') == 'U.S. yields rose 0.5%.'

# Abstention is a completed, non-actionable assessment; fabricated evidence is never required.
abstain = dict(evidence=[], event='other', certainty='unknown', novelty='unknown', relative_size=None)
assert c.classify(c.validate(abstain, text), '제목·요약만 확인')['level'] == '참고'
try:
    c.validate(dict(abstain, event='contract', certainty='confirmed', novelty='new'), text)
    raise AssertionError('Important event without evidence accepted')
except ValueError:
    pass
