"""Small offline checks: python test_daily_schedule.py. Never calls Telegram."""
import json
import os
import sqlite3
import tempfile
import io
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import daily_schedule as d

request_impl = d.request


def check_ir_fallback():
    day = d.date(2026, 9, 29)
    for failure in (TimeoutError('private-url'), d.URLError(TimeoutError('private-url'))):
        with patch.object(d, 'build_opener') as opener:
            opener.return_value.open.side_effect = failure
            try:
                request_impl('https://example.org')
            except RuntimeError as exc:
                assert str(exc) == '응답 지연(15초)'
            else:
                raise AssertionError('Timeout not reported')
    assert d.source_error(RuntimeError('private-token')) == '조회 실패'
    assert d.source_error(d.HTTPError('private-url', 403, 'secret', {}, None)) == '조회 실패'
    assert d.source_error(RuntimeError('HTTP 403')) == 'HTTP 403'
    assert d.source_error(ValueError('private-payload')) == '일정 형식·기준일 확인 실패'
    marker = {'kind': 'earnings_checked', 'local_day': day.isoformat()}
    earning = {'kind': 'earnings', 'symbol': 'SNDK', 'local_day': day.isoformat()}
    for rows, expected in [([marker, earning], '당일 등재 있음'),
                           ([marker], '당일 등재 없음'), (None, '대체 달력도 미확인')]:
        with ExitStack() as stack:
            stack.enter_context(patch('universe.active_symbols', return_value=['SNDK']))
            requests = stack.enter_context(patch.object(d, 'request', return_value=''))
            stack.enter_context(patch.object(d, 'collect_company', side_effect=RuntimeError('응답 지연(15초)')))
            for name in ('collect_nyfed', 'parse_fed', 'parse_auctions', 'parse_buybacks',
                         'parse_boj', 'parse_consensus'):
                stack.enter_context(patch.object(d, name, return_value=[]))
            stack.enter_context(patch.object(d, 'apply_investing', return_value=0))
            stack.enter_context(patch.object(d, 'parse_earnings', return_value=rows,
                                            side_effect=ValueError('private-token') if rows is None else None))
            stack.enter_context(redirect_stderr(io.StringIO()))
            events, good, errors = d.collect(day)
            warning = next(e for e in errors if e.startswith('SNDK 공식 IR:'))
            assert '응답 지연(15초)' in warning and expected in warning
            assert '기타 행사 미확인' in warning and 'private-token' not in str(errors)
            assert sum(c.args[0].startswith(d.EARNINGS) for c in requests.call_args_list) == 1
            assert sum(e.get('kind') == 'earnings' for e in events) == (1 if rows and earning in rows else 0)
    message, _ = d.render([dict(marker, title='조회 완료', at=None)], day,
                          ['SNDK 공식 IR: 응답 지연(15초) / 실적 Nasdaq 달력 대체 확인(당일 등재 없음) · 기타 행사 미확인'])
    assert '일정 일부 확인 실패' not in message and '기타 행사 미확인' not in message
    print('IR timeout diagnostics and shared earnings fallback passed (offline).')


def check_earnings_pipeline():
    import review_harness as h
    day = d.date(2026, 9, 30)
    source = d.CORPORATE['MU'][1]
    feed = {'GetEventListResult': [
        {'StartDate': '09/30/2026 16:30:00', 'TimeZone': 'ET',
         'Title': "Micron's Fourth Quarter 2026 Financial Call",
         'LinkToDetailPage': '/events-and-presentations/event-details/2026/call/default.aspx'},
        {'StartDate': '09/30/2026 18:00:00', 'TimeZone': 'ET',
         'Title': "Micron's Post Earnings Analyst Call"}]}
    with patch.object(d, 'request', return_value=json.dumps(feed)) as request:
        calls = d.classify_company_events(d.collect_company('MU', day), 'MU')
        assert request.call_count == 1
        assert request.call_args.args[0].startswith('https://investors.micron.com/feed/Event.svc/')
    assert all(c['kind'] == 'earnings_call' for c in calls)
    assert calls[0]['at'] == '2026-10-01T05:30:00+09:00'
    assert '/event-details/' in calls[0]['source']
    with patch('universe.active_symbols', return_value=['MU']):
        raw = json.dumps({'status': {'rCode': 200}, 'data': {'asOf': 'Wed, Sep 30, 2026',
                         'rows': [{'symbol': 'MU', 'time': 'time-after-hours'}]}})
        releases = d.parse_earnings(raw, day)
        message, selected = d.render(releases+calls, day, [], start_time=d.time(19))
        assert message.index('MU 실적 발표') < message.index('경제지표·주요 일정')
        assert '미국 장후 · 한국 익일' in message and '05:30 (익일)' in message
        assert message.count('Financial Call') == 1 and '발표 시각과 다릅니다' in message
        assert len([e for e in selected if e.get('kind') == 'earnings']) == 1
        for errors in ([], ['CSQ 실적 달력']):
            fallback, _ = d.render(calls, day, errors)
            assert '실적 발표 일정 미확인' in fallback and '05:30 (익일)' in fallback
            assert '해당 종목 없음' not in fallback
        assert not d.render(calls, day+d.timedelta(days=1), [])[1]
        record = {'version':2, 'events':releases+calls, 'session_day':day.isoformat(),
                  'start_time':'19:00', 'errors':[], 'html':message}
        assert h.validated_agenda(record) == message
        try:
            h.validated_agenda(dict(record, html=message.replace('MU 실적 발표', '')))
        except ValueError:
            pass
        else:
            raise AssertionError('Lost earnings accepted in final message')
    # Same classifier handles other issuers, unknown time and post-earnings calls.
    assert d.classify_company_events([d.event('Other Q3 Earnings Webcast', day, None, source)], 'COIN')[0]['kind']=='earnings_call'
    assert 'kind' not in d.classify_company_events([d.event('Citi Conference', day, None, source)], 'COIN')[0]
    winter = dict(feed['GetEventListResult'][0], StartDate='01/20/2027 14:30:00', TimeZone='MT')
    assert d.parse_q4(json.dumps({'GetEventListResult':[winter]}), 'MU', source)[0]['at']=='2027-01-21T06:30:00+09:00'
    bad = dict(winter, LinkToDetailPage='https://elsewhere.example/event')
    try:
        d.parse_q4(json.dumps({'GetEventListResult':[bad]}), 'MU', source)
    except ValueError:
        pass
    else:
        raise AssertionError('Untrusted IR detail link accepted')
    print('Earnings pipeline: official feed, release/call distinction, fallback, DST, stale dates, immutable rendered content passed (offline).')


def check():
    check_ir_fallback()
    check_earnings_pipeline()
    day = d.date(2026, 9, 10)
    source = 'https://example.org/calendar'
    assert '21:30' in d.event('summer', day, '8:30 am', source)['at']
    assert '22:30' in d.event('winter', d.date(2026, 1, 10), '8:30 am', source)['at']
    assert '00:50' in d.event_from_text('LITE', 'September 10, 2026 8:50 am PT', source)['at']
    assert d.event_from_text('unknown', 'Sep 10, 2026', source)['at'] is None
    # Synthetic public calendar: reject stale dates and keep MoM/YoY columns distinct.
    public = '<h1>United States Economic Calendar for September 10, 2026</h1><p>US EASTERN TIME</p><table><tr><td colspan="4">Thu., Sep 10</td><td><b>Period</b></td><td><b>Actual</b></td><td><b>Forecast</b></td><td><b>Previous</b></td></tr><tr><td></td><td>USD</td><td>08:30 AM</td><td>CPI (Y-o-Y)</td><td>AUG</td><td>9%</td><td>3.2%</td><td>3.1%</td></tr></table>'
    rows = d.parse_consensus(public, day, source)
    assert len(rows) == 1 and rows[0]['forecast'] == '3.2%' and '전년비' in rows[0]['title']
    assert rows[0]['at'][11:16] == '21:30'
    stars = '<svg class="opacity-60"></svg>' * 3
    investing = '<table><tr>' + ''.join('<th>'+v+'</th>' for v in ['Time','Cur.','Event','Imp.','Actual','Forecast','Previous']) + '</tr><tr><td colspan="8">Thursday, September 10, 2026</td></tr><tr><td></td><td></td><td><span data-test="flag-US"></span></td><td><a href="https://www.investing.com/economic-calendar/cpi-733">CPI (YoY) (Aug)</a></td><td>'+stars+'</td><td>9%</td><td>3.3%</td><td>3.1%</td><td></td></tr></table>'
    assert d.apply_investing(investing, day, rows) == 1
    assert rows[0]['forecast'] == '3.3%' and rows[0]['stars'] == 3
    assert d.apply_investing(investing.replace('(YoY)', '(MoM)'), day, rows) == 0
    for parse, raw in [(d.parse_consensus, public), (lambda raw, day, source: d.apply_investing(raw, day, rows), investing)]:
        try:
            parse(raw, day+d.timedelta(days=1), source)
            raise AssertionError('Stale calendar accepted')
        except ValueError:
            pass
    nyfed = '''<table><tr><td class="ts-data-table-head">September 2026</td></tr>
    <tr><td class="somatdR"><div>10<br><span><a>Producer Price Index</a><br>(08:30)<br>
    <a>JOLTS</a><br>(10:00)</span></div></td></tr></table>'''
    macro = d.parse_nyfed(nyfed, day)
    assert d.event_stars(macro[0]) == 3
    assert d.event_stars(macro[1]) == 2
    assert d.indicator_values(macro[0]) == '(예상: 미확인 · 이전: 미확인)'
    assert d.indicator_values(dict(macro[0], forecast='0.2%', previous='0.0%')) == '(예상: 0.2% · 이전: 0.0%)'
    assert d.indicator_values(dict(macro[0], forecast='<x>', previous=0)) == '(예상: &lt;x&gt; · 이전: 0)'
    assert d.event_stars({'title':'기타 행사'}) == 0
    assert [x['at'][11:16] for x in macro] == ['21:30', '23:00']
    fed = '''<div class="panel panel-unstyled"><div class="panel-body"><div class="row">
    <div class="col-xs-2"><p>2:00 p.m.</p></div><div class="col-xs-7"><p>FOMC Meeting</p></div>
    <div class="col-xs-3"><p>10</p></div></div></div></div>'''
    assert d.parse_fed(fed, day, source)[0]['at'] == '2026-09-11T03:00:00+09:00'
    row = {'securityType': 'Bond', 'auctionDate': '2026-09-10T00:00:00', 'cusip': 'example',
           'originalSecurityTerm': '30-Year', 'closingTimeCompetitive': '01:00 PM',
           'closingTimeNoncompetitive': '12:00 PM'}
    assert '02:00' in d.parse_auctions(json.dumps([row]), day)[0]['at']
    xml = '''<BuyBackCalendar><StartDate>2026-09-01</StartDate><EndDate>2026-09-30</EndDate>
    <BuybackCalendarDate><AnnouncementDate>2026-09-10</AnnouncementDate>
    <OperationDate>2026-09-10</OperationDate><OperationStartTimeEasternUS>13:40</OperationStartTimeEasternUS>
    </BuybackCalendarDate></BuyBackCalendar>'''
    buybacks = d.parse_buybacks(xml, day)
    assert buybacks[0]['at'] is None and '00:00' in buybacks[1]['at']
    q4 = {'GetEventListResult': [{'StartDate': '09/10/2026 13:15:00', 'TimeZone': 'ET', 'Title': 'Citi Conference'}]}
    assert '02:15' in d.parse_q4(json.dumps(q4), 'COIN', source)[0]['at']
    q4['GetEventListResult'][0]['TimeZone'] = 'unknown'
    assert d.parse_q4(json.dumps(q4), 'COIN', source)[0]['at'] is None
    assert not d.parse_ics('BEGIN:VCALENDAR\nBEGIN:VEVENT\nSTATUS:CANCELLED\nEND:VEVENT\nEND:VCALENDAR', 'Apple', source)
    overnight = d.event('overnight <&>', day, '2:00 pm', source)
    early = d.event('exclude', day, '5:00 am', source)
    message, selected = d.render(macro + [overnight, overnight, early], day, ['DELL'])
    assert 'PPI 생산자물가지수 ★★★' in message
    assert '(예상: 미확인 · 이전: 미확인)' in message
    assert len(selected) == 3 and '익일' in message and '&lt;&amp;&gt;' in message
    assert '실적: CSQ 전체 대조 / 기타 기업 행사:' not in message
    assert '확인 누락: DELL' not in message and 'Nasdaq 예상 일정' not in message
    assert '일부 출처 확인 실패' in d.render([], day, ['DELL'])[0]
    calendar = {'status': {'rCode': 200}, 'data': {'asOf': 'Thu, Sep 10, 2026', 'rows': [
        {'symbol': 'ADBE', 'time': 'time-after-hours'},
        {'symbol': 'ORCL', 'time': 'time-pre-market'},
        {'symbol': 'OUTSIDE', 'time': 'time-not-supplied'}]}}
    with patch('universe.active_symbols', return_value=['ADBE', 'ORCL']):
        earnings = d.parse_earnings(json.dumps(calendar), day)
        message, selected = d.render(earnings, day, [])
        assert len(selected) == 2 and all(e['at'] is None for e in selected)
        assert 'ADBE 실적 발표' in message and 'ORCL 실적 발표' in message
        assert '미국 장후 · 한국 익일' in message and '미국 장전' in message
        assert 'OUTSIDE' not in message and '16:00' not in message
        assert '실적 일정 미확인' in d.render(earnings, day + d.timedelta(days=1), [])[0]
        from briefing import compose
        with patch('news.load_saved', return_value=([], {})):
            assert 'ADBE 실적 발표' in compose(message, d.datetime.combine(day, d.time(19, 30), d.KST))
        calendar['data']['rows'] = []
        assert '해당 종목 없음' in d.render(d.parse_earnings(json.dumps(calendar), day), day, [])[0]
        for bad in [None, {}]:
            calendar['data']['rows'] = bad
            try:
                d.parse_earnings(json.dumps(calendar), day)
                raise AssertionError('missing calendar accepted')
            except ValueError:
                pass
        assert '실적 일정 미확인' in d.render([], day, ['CSQ 실적 달력'])[0]

    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / 'config.py'
        config.write_text("BOT_TOKEN = '123:fake_token'\nALLOWED_CHAT_IDS = [123, -456]\nraise Exception('must not execute')", encoding='utf-8')
        with patch.dict(os.environ, {'QUANTBOT_CONFIG_PATH': str(config)}):
            assert d.quantbot_destination() == ('123:fake_token', '-456')
            config.write_text("BOT_TOKEN = '123:fake_token'\nALLOWED_CHAT_IDS = [-456, -789]", encoding='utf-8')
            try:
                d.quantbot_destination()
                raise AssertionError('ambiguous group accepted')
            except ValueError:
                pass
        db = Path(folder) / 'delivery.sqlite'
        with patch.object(d, 'telegram', return_value={'message_id': 1}) as api:
            assert d.send_once(message, day, 'fake', '-456', db)
            assert not d.send_once(message, day, 'fake', '-456', db)
            assert api.call_count == 1
            assert api.call_args.args[1]['chat_id'] == '-456'
        tomorrow = day + d.timedelta(days=1)
        with patch.object(d, 'telegram', side_effect=TimeoutError) as api:
            try:
                d.send_once(message, tomorrow, 'fake', '-456', db)
                raise AssertionError('timeout ignored')
            except TimeoutError:
                pass
            assert not d.send_once(message, tomorrow, 'fake', '-456', db)
            assert api.call_count == 1
        with d.closing(sqlite3.connect(db)) as conn:
            assert conn.execute('SELECT status FROM delivery ORDER BY day').fetchall() == [('sent',), ('unknown',)]
        recipients = Path(folder) / 'recipients.json'
        recipients.write_text('[123, 123]', encoding='utf-8')
        with patch.object(d, 'quantbot_destination', return_value=('fake', '-456')):
            assert d.destinations(recipients) == ('fake', ['-456', '123'])
        with patch.object(d, 'telegram', side_effect=[TimeoutError(), {'message_id': 2}]) as api:
            try:
                d.send_all(message, day, 'fake', ['-456', '123'], Path(folder))
                raise AssertionError('partial failure ignored')
            except RuntimeError:
                pass
            assert api.call_count == 2
            assert api.call_args.args[1]['chat_id'] == '123'
        with patch.object(d, 'telegram') as api:
            d.send_all(message, day, 'fake', ['-456', '123'], Path(folder))
            api.assert_not_called()
    print('Daily agenda offline checks passed.')


if __name__ == '__main__':
    with patch.object(d, 'request', side_effect=AssertionError('Network forbidden in tests')), patch.object(d, 'telegram_paused', return_value=False):
        check()
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'telegram', return_value={'message_id': 1}) as api:
            saturday = d.date(2026, 9, 12)
            sunday = d.date(2026, 9, 13)
            news_db = Path(folder) / 'delivery-news.sqlite'
            for day in (saturday, sunday):
                assert not d.send_once('news', day, 'fake', '123', news_db)
            api.assert_not_called()
            assert not news_db.exists()
            assert d.send_once('agenda', saturday, 'fake', '123', Path(folder) / 'delivery-agenda.sqlite')
            assert d.send_once('news', d.date(2026, 9, 14), 'fake', '123', news_db)
            assert api.call_count == 2

# Overseas policy risk: existing star/value format, Asia next-day coverage.
def global_fixture(country, code, day, name, value='11:00 PM'):
    return f'<h1>{country} Economic Calendar for {day:%B %d, %Y}</h1><p>US EASTERN TIME</p><table><tr><td colspan="4">{day:%a}., {day:%b} {day.day}</td><td><b>Period</b></td><td><b>Actual</b></td><td><b>Forecast</b></td><td><b>Previous</b></td></tr><tr><td></td><td>{code}</td><td>{value}</td><td>{name}</td><td>-</td><td>-</td><td>0.75%</td><td>0.50%</td></tr></table>'
day=d.date(2026,9,17)
japan=d.parse_consensus(global_fixture('Japan','JPY',day,'Interest Rate Decision'),day,'https://example.org/jp','JPY')
assert japan[0]['at']=='2026-09-18T12:00:00+09:00'
message,selected=d.render(japan,day,[])
assert japan[0] in selected and '일본 BOJ' in message and '★★★' in message
assert '(예상: 0.75% · 이전: 0.50%)' in message and '익일 19:30까지' in message
assert not d.parse_consensus(global_fixture('Japan','JPY',day,'Retail Sales'),day,'https://example.org/jp','JPY')
assert '영국 BOE' in d.parse_consensus(global_fixture('British Pound','GBP',day,'Interest Rate Decision','07:00 AM'),day,'https://example.org/gb','GBP')[0]['title']
try:d.parse_consensus(global_fixture('Japan','JPY',day,'Interest Rate Decision'),day,'https://example.org/jp','EUR')
except ValueError:pass
else:raise AssertionError('Wrong country accepted')
boj='<table><caption>Table : 2026</caption><thead><tr><th>Date of MPM</th></tr></thead><tbody><tr><td>Sept. 17 (Thurs.), 18 (Fri.)</td><td>-</td></tr></tbody></table>'
assert not d.parse_boj(boj,day)
unknown=d.parse_boj(boj,day+d.timedelta(days=1))
assert unknown[0]['at'] is None
message,selected=d.render(unknown,day,[])
assert unknown[0] in selected and '時間' not in message and '시간 미정 (일본 09/18)' in message
assert '예상: 미확인' in message
assert not d.render(unknown,day+d.timedelta(days=1),[])[1], 'Do not resend a past daytime decision'
try:d.parse_boj(boj,d.date(2027,9,18))
except ValueError:pass
else:raise AssertionError('Wrong year accepted')
print('Overseas policy calendars: country/timezone validation, next-day Asia, stars/values and unknown time passed.')
