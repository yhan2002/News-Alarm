"""Tiny offline fixtures: no private positions, websites, models or Telegram."""
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import portfolio_events as p
import daily_schedule as d


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    (root/'Master_Dashboard').mkdir()
    cards = [{'date': '20261002', 'html_link': 'Reports/Wrap Fund Analysis (demo).html', 'goal_achieved': False},
             {'date': '20261002', 'html_link': 'Reports/Wrap Fund Analysis (closed).html', 'goal_achieved': True}]
    (root/'Master_Dashboard'/'2026-10-02_index.html').write_text('const CARDS = '+json.dumps(cards)+';', encoding='utf-8')
    with closing(sqlite3.connect(root/'wrap_market_data.sqlite3')) as c, c:
        c.executescript('CREATE TABLE holdings(fund,report_date,code,direction,weight_pct); CREATE TABLE instruments(code,provider_ticker);')
        c.executemany('INSERT INTO instruments VALUES (?,?)', [('a','TSLA'),('b','9984.T'),('c','OLD'),('x','CLOSED')])
        c.executemany('INSERT INTO holdings VALUES (?,?,?,?,?)', [('demo','20261001','c','L',10),('demo','20261002','a','L',10),
                      ('demo','20261002','a','S',10),('demo','20261002','b','S',5),('closed','20261002','x','L',20)])
    day = d.date(2026,10,2)
    targets = p.holdings(day,root)
    assert targets == {'symbols':['9984.T','TSLA'],'as_of':['20261002'],'errors':[]}
    assert not p.holdings(day+d.timedelta(days=8),root)['symbols']
    event = {'kind':'portfolio_event','symbol':'TSLA','certainty':'official','evidence':'Officially announced October 2 event',
             'title':'TSLA Investor Day','at':'2026-10-03T10:00:00+09:00','local_day':'2026-10-02',
             'source':'https://example.org/event','stars':3,'note':'공식 발표','country':'미국'}
    with patch.object(p,'ROOT',root):
        assert p.reviewed_events(day,targets)[1]
        f=root/'data'/'portfolio-events'/'2026-10-02.json'; f.parent.mkdir(parents=True)
        record={'date':day.isoformat(),'reviewed_symbols':targets['symbols'],'events':[event]}
        f.write_text(json.dumps(record),encoding='utf-8')
        assert p.reviewed_events(day,targets)==([event],[])
        event['certainty']='estimated'; f.write_text(json.dumps(record),encoding='utf-8')
        assert not p.reviewed_events(day,targets)[0]
    event['certainty']='official'
    marker={'kind':'portfolio_targets','symbols':targets['symbols'],'as_of':targets['as_of']}
    with patch('universe.active_symbols',return_value=[]):
        message,selected=d.render([marker,event],day,[],start_time=d.time(19))
        assert event in selected and '익일 19:00까지' in message and '★★★' in message
        assert 'demo' not in message and 'closed' not in message
        late=dict(event,at='2026-10-03T19:00:00+09:00')
        assert not d.render([marker,late],day,[],start_time=d.time(19))[1]
    watch = dict(event, kind='portfolio_watch', certainty='reported', at=None,
                 local_zone='America/New_York', title='TSLA 생산·출하량 — 미국 10/02 발표 예상',
                 evidence='보도 원문이 10월 2일 발표를 예상함',
                 consensus_source='https://example.org/official-consensus',
                 consensus_label='공식 IR 집계 컨센서스: 출하량 461,974대 · 에너지 저장 15.9GWh')
    with patch.object(p,'ROOT',root), patch('universe.active_symbols',return_value=[]):
        record['events']=[watch]; f.write_text(json.dumps(record),encoding='utf-8')
        assert p.reviewed_events(day,targets)==([watch],[])
        message,selected=d.render([marker,watch],day,['출처 확인 실패'],start_time=d.time(19))
        assert watch in selected and '공식 발표일·시각 사전공지 미확인' in message
        assert message.index('경제지표·주요 일정') < message.index('보유 종목 · 미확정 발표 관찰') < message.index('항목을 누르면')
        assert '461,974대' in message and '⚠ 일정 일부 확인 실패' not in message
        assert not d.render([marker,watch],day+d.timedelta(days=1),[],start_time=d.time(19))[1]
        assert not d.render([watch],day,[],start_time=d.time(19))[1]
        watch['certainty']='estimated'; f.write_text(json.dumps(record),encoding='utf-8')
        assert not p.reviewed_events(day,targets)[0]
print('PASS: latest holdings, long/short, closed/stale exclusion, fact-check cache and 24h event window.')
