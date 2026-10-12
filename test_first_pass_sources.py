"""Source coverage and confidence gates; no HTTP or model calls."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import first_pass as f
import content_assessment as c
import semantics as s
import briefing as b


def main():
    a = {'title':'Walmart declared war on DoorDash in delivery.', 'summary':'A competitive delivery initiative.', 'url':'https://example.com/news'}
    text = a['title']+'\n'+a['summary']
    facts = c.validate({'event':'contract', 'certainty':'confirmed', 'novelty':'new', 'evidence':[a['title']], 'relative_size':None}, text)
    assert facts['event'] == 'contract' and facts['review_required']
    assert c.classify(facts, '제목·RSS 요약')['level'] == '추가 확인'
    r = {'version':f.VERSION, 'model':f.MODEL, 'status':'done', 'coverage':'제목·RSS 요약', 'facts':facts, 'text_hash':c.hashlib.sha256(('\n'+text).encode()).hexdigest()}
    assert not b.importance(dict(a, qwen=r))['high']
    assert b.importance(dict(a, qwen=r, web_review={'score':90}))['score'] == 90
    old_quote = 'On August 13, OPKO issued $125 million in senior secured notes.'
    old = c.validate(dict(facts, event='financing', evidence=[old_quote]), old_quote, '2026-09-14T03:00:00+00:00')
    assert old['novelty'] == 'new' and old['review_required']
    assert c.classify(old, '본문 추출')['level'] == '추가 확인'
    fresh = c.validate(dict(facts, event='financing', evidence=[old_quote]), old_quote, '2026-08-14T03:00:00+00:00')
    assert not fresh['review_required']
    assert not f.same_input(dict(a, published_at='2026-09-14'), r)
    with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'ROOT', Path(tmp)):
        assert f.evidence_input(a) == (text, '제목·RSS 요약') and f.same_input(a, r)
        path = Path(tmp)/'data/codex-review/bodies'/(c.fingerprint(a)+'.json')
        path.parent.mkdir(parents=True)
        quote = 'Walmart competes against DoorDash for delivery customers.'
        body = quote+' '+('Additional source detail. '*30)
        path.write_text(json.dumps({'body':body, 'coverage':'본문 추출'}), encoding='utf-8')
        assert f.evidence_input(a)[1] == '본문 추출' and not f.same_input(a, r)
        assert f.evidence_input(a, '제목·RSS 요약')[0] == text
        stored = s.validate({'topics':[], 'relations':[{'source':'Walmart','target':'DoorDash','kind':'competition','quote':quote}]}, body)
        article = dict(a, qwen=dict(r, coverage='본문 추출', facts={'semantics':stored}))
        assert s.attach(article)['semantic_relations'][0]['target_key'] == 'DASH'
        path.write_text(json.dumps({'body':body*25, 'coverage':'본문 추출'}), encoding='utf-8')
        assert f.evidence_input(a)[1] == '본문 발췌'
        path.unlink()
        assert s.attach(article)['semantic_status'] == 'pending'
    print('Source upgrade, body grounding, abstention and review precedence passed')


if __name__ == '__main__':
    main()
