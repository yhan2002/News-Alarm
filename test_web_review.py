"""Completed title reviews and selected body assessments must remain distinct."""
import tempfile
from pathlib import Path
import briefing
import review_harness as h

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    folder = home/'day'
    a = {'id':'a', 'title':'Example company announces an acquisition today.', 'summary':'Original summary', 'url':'https://example.com/a'}
    b = dict(a, id='b', url='https://example.com/b')
    h.write(folder/'packet.json', {'version':2, 'backlog':0, 'prepared_at':'2026-09-10', 'articles':[a,b]})
    h.write(folder/'snapshot.json', {'a':a,'b':b})
    h.write(folder/'draft.json', {'review':{'reviewed_ids':['a','b'], 'reviewed_source_ids':['a','b'],
        'items':[{'id':'a','level':'중요','reason_ko':'계약 확인','evidence':a['title']}]}})
    h.write(home/'bodies'/(h.fingerprint(a)+'.json'), {'coverage':'본문 추출','body':a['title']})
    changed = dict(a, summary='Updated content needs a new review')
    result = h.attach_web_reviews([dict(a),dict(b),changed], home)
    assert briefing.importance(result[0])['level'] == '중요'
    assert briefing.importance(result[1])['level'] == '제목 검토 완료'
    assert briefing.importance(result[2])['level'] == '본문 평가 대기'
    h.write(folder/'packet.json', {'version':2,'backlog':1,'articles':[a,b]})
    assert 'web_review' not in h.attach_web_reviews([dict(a)], home)[0]
print('Review linkage, title/body distinction, changed content and incomplete packet checks passed.')
