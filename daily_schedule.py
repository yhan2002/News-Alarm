"""Official event calendars -> one daily QuantBot group message. No model calls."""
import argparse
import ast
import calendar
import hashlib
import html as html_escape
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Reuse this PC's installed runtime packages without changing the FinBERT venv.
bundled_site = Path(sys.base_prefix) / 'Lib' / 'site-packages'
if bundled_site.is_dir() and str(bundled_site) not in sys.path:
    sys.path.append(str(bundled_site))
from lxml import html
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
KST = ZoneInfo('Asia/Seoul')
ET = ZoneInfo('America/New_York')
ZONES = {'ET': ET, 'EST': ET, 'EDT': ET, 'PT': ZoneInfo('America/Los_Angeles'),
         'PST': ZoneInfo('America/Los_Angeles'), 'PDT': ZoneInfo('America/Los_Angeles'),
         'MT': ZoneInfo('America/Denver'), 'MST': ZoneInfo('America/Denver'),
         'MDT': ZoneInfo('America/Denver'), 'UTC': timezone.utc, 'GMT': timezone.utc}
NYFED = 'https://www.newyorkfed.org/research/calendars/nationalecon_cal'
BUYBACK = 'https://home.treasury.gov/system/files/221/Tentative-Buyback-Schedule.xml'
AUCTIONS = 'https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json'
EARNINGS = 'https://api.nasdaq.com/api/calendar/earnings?date='
CONSENSUS = 'https://forex.tradingcharts.com/economic_calendar/{day}.html?code=USD'
INVESTING = 'https://www.investing.com/economic-calendar/'
BOJ_CALENDAR = 'https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm'
GLOBAL_CALENDARS = {'JPY': ('Japan', '일본', 'BOJ'), 'EUR': ('Euro', '유로존', 'ECB'),
                    'GBP': ('British Pound', '영국', 'BOE')}
CORPORATE = {
    'MU': ('마이크론', 'https://investors.micron.com/events-and-presentations/default.aspx', 'q4'),
    'IBM': ('IBM', 'https://www.ibm.com/investor/events', 'ibm'),
    'MRVL': ('마벨', 'https://investor.marvell.com/news-events/ir-calendar', 'marvell'),
    'COIN': ('코인베이스', 'https://investor.coinbase.com/events-and-presentations/events/default.aspx', 'q4'),
    'LITE': ('루멘텀', 'https://investor.lumentum.com/events-and-presentations/', 'q4'),
    'SNDK': ('샌디스크', 'https://investor.sandisk.com/news-events/events', 'drupal'),
    'DELL': ('델', 'https://investors.delltechnologies.com/news-events/upcoming-events/', 'drupal'),
    'AAPL': ('애플', 'https://www.apple.com/apple-events/', 'apple'),
}
MAJOR = {
    'consumer price index': 'CPI 소비자물가지수',
    'producer price index': 'PPI 생산자물가지수',
    'employment situation': '고용지표 (비농업고용·실업률)',
    'jolts': 'JOLTS 구인·이직',
    'initial claims': '신규 실업수당 청구',
    'personal income': '개인소득·소비 / PCE 물가',
    'gross domestic product': 'GDP',
    'retail sales': '소매판매',
    'ism manufacturing': 'ISM 제조업 PMI',
    'ism non-manufacturing': 'ISM 서비스업 PMI',
    'adp national employment': 'ADP 고용',
    'michigan consumer': '미시간대 소비자심리',
    'consumer confidence': '소비자신뢰지수',
}
MONTH_PATTERN = '(' + '|'.join(calendar.month_name[1:]) + ')'
DATE_PATTERN = MONTH_PATTERN + r'\s+(\d{1,2}),?\s+(\d{4})'
TIME_PATTERN = r'(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected redirect')


def request(url, body=None):
    """Bounded requests; exception messages never expose a Telegram token URL."""
    try:
        req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers={'User-Agent': 'NewsAlarm/1.0',
                               'Accept': 'application/json, text/html, text/calendar, text/xml, application/xml',
                               **({'Content-Type': 'application/json'} if body is not None else {})})
        opener = build_opener(NoRedirect) if body is not None else build_opener()
        with opener.open(req, timeout=15) as response:
            if urlsplit(response.url).scheme != 'https':
                raise ValueError('HTTPS required')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError('응답 크기 제한 초과')
        return raw.decode('utf-8-sig')
    except HTTPError as exc:
        raise RuntimeError(f'HTTP {exc.code}') from None
    except TimeoutError:
        raise RuntimeError('응답 지연(15초)') from None
    except URLError as exc:
        reason = '응답 지연(15초)' if isinstance(exc.reason, TimeoutError) else '연결 실패'
        raise RuntimeError(reason) from None
    except (OSError, ValueError):
        raise RuntimeError('연결 실패 또는 예상하지 못한 응답') from None


def source_error(exc):
    """Only locally defined diagnostics may reach logs or Telegram."""
    reason = str(exc)
    if isinstance(exc, RuntimeError) and (reason in (
            '응답 지연(15초)', '연결 실패', '연결 실패 또는 예상하지 못한 응답', '응답 크기 제한 초과')
            or re.fullmatch(r'HTTP [1-5]\d{2}', reason)):
        return reason
    if isinstance(exc, ValueError):
        return '일정 형식·기준일 확인 실패'
    return '조회 실패'


def text(node):
    return ' '.join(node.text_content().split())


def document(raw):
    tree = html.fromstring(raw)
    for node in tree.xpath('//script | //style'):
        node.drop_tree()
    return tree


def clock(value):
    value = value.replace('.', '').strip()
    for fmt in ('%I:%M %p', '%I:%M%p', '%I %p', '%I%p', '%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            pass
    raise ValueError('시간 형식 미확인')


def english_date(value):
    match = re.search(DATE_PATTERN, value, re.I)
    if not match:
        # Marvell uses abbreviated English months.
        match = re.search(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})', value)
        if not match:
            raise ValueError('날짜 형식 미확인')
        month = list(calendar.month_abbr).index(match[1].title())
    else:
        month = list(calendar.month_name).index(match[1].title())
    return date(int(match[3]), month, int(match[2]))


def event(title, day, value, source, zone=ET, note=''):
    when = datetime.combine(day, clock(value), zone) if value else None
    return {'title': title, 'at': when.astimezone(KST).isoformat() if when else None,
            'local_day': day.isoformat(), 'source': source, 'note': note}


def event_from_text(title, value, source, default_zone=None):
    day = english_date(value)
    match = re.search(TIME_PATTERN + r'\s*(ET|EDT|EST|PT|PDT|PST|MT|MDT|MST|UTC|GMT)\b', value, re.I)
    if not match:
        return event(title, day, None, source)
    zone = ZONES.get(match[4].upper(), default_zone)
    if zone is None:
        raise ValueError('시간대 미확인')
    return event(title, day, f'{match[1]}:{match[2] or "00"} {match[3]}m', source, zone)


def parse_nyfed(raw, day, source=NYFED):
    tree = document(raw)
    headings = tree.xpath('//td[contains(@class,"ts-data-table-head")]')
    if not any(day.strftime('%B %Y').lower() in text(n).lower() for n in headings):
        raise ValueError('달력의 기준 월이 다릅니다')
    cells = tree.xpath('//td[contains(@class,"somatd")]')
    if not cells:
        raise ValueError('경제지표 달력 형식 변경')
    events = []
    for cell in cells:
        number = re.match(r'(\d{1,2})(?!\d)', text(cell))
        if not number or int(number[1]) != day.day:
            continue
        for anchor in cell.xpath('.//a'):
            title = next((v for k, v in MAJOR.items() if k in text(anchor).lower()), None)
            if not title:
                continue
            # The time belongs to this anchor, before the next event anchor.
            suffix = anchor.tail or ''
            for sibling in anchor.itersiblings():
                if sibling.tag == 'a':
                    break
                suffix += ' ' + text(sibling) + ' ' + (sibling.tail or '')
            match = re.search(r'\((\d{1,2}:\d{2})\)', suffix)
            events.append(event(title, day, match[1] if match else None, source))
    return events


def collect_nyfed(day):
    raw = request(NYFED)
    tree = document(raw)
    wanted = f'i-{calendar.month_abbr[day.month].lower()}{day.year % 100:02}.html'
    if not any(day.strftime('%B %Y').lower() in text(n).lower()
               for n in tree.xpath('//td[contains(@class,"ts-data-table-head")]')):
        links = tree.xpath('//a/@href')
        target = next((urljoin(NYFED, u) for u in links if u.endswith('/' + wanted)), None)
        if not target or urlsplit(target).hostname != 'www.newyorkfed.org':
            raise ValueError('해당 월의 공식 달력 미확인')
        return parse_nyfed(request(target), day, target)
    return parse_nyfed(raw, day)


def parse_fed(raw, day, source):
    tree = document(raw)
    panels = tree.xpath('//div[contains(@class,"panel-unstyled")]')
    if not panels:
        raise ValueError('연준 달력 형식 변경')
    events = []
    for panel in panels:
        times = panel.xpath('.//div[contains(@class,"col-xs-2")]')
        bodies = panel.xpath('.//div[contains(@class,"col-xs-7")]')
        days = panel.xpath('.//div[contains(@class,"col-xs-3")]')
        if not bodies or not days:
            continue
        paragraphs = bodies[0].xpath('./p')
        title = text(paragraphs[0]) if paragraphs else text(bodies[0])
        if not any(k in title.lower() for k in ('fomc', 'speech', 'beige book', 'industrial production')):
            continue
        if day.day not in [int(n) for n in re.findall(r'\d+', text(days[0]))]:
            continue
        title = {'FOMC Meeting': 'FOMC 금리결정', 'FOMC Press Conference': 'FOMC 기자회견',
                 'Beige Book': '연준 베이지북'}.get(title, title.replace('Speech - ', '연준 연설 · '))
        value = text(times[0]) if times else ''
        events.append(event(title, day, value if re.search(r'\d', value) else None, source))
    return events


def parse_auctions(raw, day):
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('경매 응답 형식 변경')
    events = []
    for row in rows:
        if row.get('securityType') not in ('Note', 'Bond', 'TIPS', 'FRN'):
            continue
        if row.get('auctionDate', '')[:10] != day.isoformat():
            continue
        term = row.get('originalSecurityTerm') or row.get('securityTerm', '')
        label = term.replace('-Year', '년물').replace('-Month', '개월물')
        if row.get('tips') == 'Yes':
            label += ' TIPS'
        if row.get('floatingRate') == 'Yes':
            label += ' FRN'
        source = 'https://www.treasurydirect.gov/auctions/auction-query/?' + urlencode({'cusip': row['cusip']})
        events.append(event(f'미국채 {label} 경매', day, row.get('closingTimeCompetitive'), source,
                            note='경쟁입찰 마감 기준'))
    return events


def parse_buybacks(raw, day):
    import xml.etree.ElementTree as ETXML
    root = ETXML.fromstring(raw)
    if root.tag != 'BuyBackCalendar':
        raise ValueError('바이백 달력 형식 변경')
    if not (root.findtext('StartDate') <= day.isoformat() <= root.findtext('EndDate')):
        raise ValueError('바이백 달력 유효기간 밖')
    events = []
    for row in root.findall('BuybackCalendarDate'):
        bucket = row.findtext('PurchaseBucketName', '')
        if row.findtext('AnnouncementDate') == day.isoformat():
            events.append(event('재무부 국채 바이백 예비 발표 · ' + bucket, day, None, BUYBACK, note='잠정 일정'))
        if row.findtext('OperationDate') == day.isoformat():
            # Treasury FAQ specifies 11:00 ET as the usual final-announcement time.
            events.append(event('재무부 국채 바이백 최종 발표', day, '11:00', BUYBACK, note='통상 시각·잠정 일정'))
            events.append(event('재무부 국채 바이백 실시 · ' + bucket, day,
                                row.findtext('OperationStartTimeEasternUS'), BUYBACK, note='잠정 일정'))
    return events


def company_title(symbol, title):
    name = CORPORATE[symbol][0]
    for key, label in [('goldman sachs', 'GS 컨퍼런스'), ('citi', '씨티 컨퍼런스'),
                       ('morgan stanley', 'MS 컨퍼런스')]:
        if key in title.lower() and 'conference' in title.lower():
            return f'{name}({symbol}) {label}'
    return f'{name}({symbol}) {title[:100]}'


def parse_q4(raw, symbol, source):
    rows = json.loads(raw)['GetEventListResult']
    if not isinstance(rows, list):
        raise ValueError('IR 일정 형식 변경')
    events = []
    for row in rows:
        stamp = datetime.strptime(row['StartDate'], '%m/%d/%Y %H:%M:%S')
        zone = ZONES.get(row.get('TimeZone'))
        value = stamp.strftime('%H:%M:%S') if zone and stamp.time() != time(0) else None
        detail = urljoin(source, row.get('LinkToDetailPage') or source)
        if urlsplit(detail).scheme != 'https' or urlsplit(detail).hostname != urlsplit(source).hostname:
            raise ValueError('기업 행사 상세 출처 미확인')
        events.append(event(company_title(symbol, row['Title']), stamp.date(), value, detail, zone or ET))
    return events


def parse_ics(raw, title, source):
    events = []
    unfolded = re.sub(r'\r?\n[ \t]', '', raw)
    if 'BEGIN:VCALENDAR' not in unfolded:
        raise ValueError('ICS 형식 변경')
    for block in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', unfolded, re.S):
        if 'STATUS:CANCELLED' in block:
            continue
        if 'RRULE:' in block:
            raise ValueError('반복 행사 ICS는 현재 미지원')
        found = re.search(r'^DTSTART([^:]*):([^\r\n]+)', block, re.M)
        if not found:
            raise ValueError('행사 시작일 미확인')
        value = found[2].strip()
        if re.fullmatch(r'\d{8}', value):
            events.append(event(title, datetime.strptime(value, '%Y%m%d').date(), None, source))
            continue
        zone_name = re.search(r'TZID=([^;]+)', found[1])
        zone = timezone.utc if value.endswith('Z') else ZoneInfo(zone_name[1]) if zone_name else None
        if zone is None:
            raise ValueError('ICS 시간대 미확인')
        stamp = datetime.strptime(value.rstrip('Z'), '%Y%m%dT%H%M%S').replace(tzinfo=zone)
        events.append({'title': title, 'at': stamp.astimezone(KST).isoformat(),
                       'local_day': stamp.date().isoformat(), 'source': source, 'note': ''})
    return events


def collect_company(symbol, day):
    _, source, kind = CORPORATE[symbol]
    if kind == 'q4':
        params = {'LanguageId': 1, 'eventSelection': 1, 'eventDateFilter': 1,
                  'includeFinancialReports': 'false', 'includePresentations': 'false',
                  'includePressReleases': 'false', 'sortOperator': 0, 'pageSize': 50,
                  'pageNumber': 0, 'includeTags': 'true', 'year': -1, 'excludeSelection': 1}
        url = urljoin(source, '/feed/Event.svc/GetEventList') + '?' + urlencode(params)
        return parse_q4(request(url), symbol, source)
    raw = request(source)
    tree = document(raw)
    events = []
    if kind == 'apple':
        links = [urljoin(source, u) for u in tree.xpath('//a/@href') if '.ics' in u.lower()]
        for link in list(dict.fromkeys(links))[:2]:
            if urlsplit(link).hostname != urlsplit(source).hostname:
                raise ValueError('다른 도메인의 행사 ICS는 확인 필요')
            events.extend(parse_ics(request(link), '애플(AAPL) 신제품 행사', source))
        if not links and re.search(r'join us|tune in', text(tree), re.I):
            raise ValueError('예고 행사의 날짜·시간 확인 필요')
        if not links and 'Apple Event' not in text(tree):
            raise ValueError('Apple 행사 페이지 형식 변경')
        return events
    if kind == 'ibm':
        cards = tree.xpath('//c4d-content-item-row')
        if not cards:
            raise ValueError('IBM 일정 형식 변경')
        for card in cards:
            try:
                card_day = english_date(text(card))
            except ValueError:
                continue
            if card_day != day:
                continue
            if len(events) >= 10:
                raise ValueError('IBM 당일 상세 조회 한도 초과')
            headings = card.xpath('.//c4d-content-item-heading')
            links = card.xpath('.//*[@href]/@href')
            detail = next((urljoin(source, u) for u in links if '/investor/events/' in u), None)
            if not headings or not detail or urlsplit(detail).hostname != 'www.ibm.com':
                raise ValueError('IBM 행사 상세 미확인')
            page = document(request(detail))
            info = page.xpath('//*[@id="mbox-subheading"]')
            if not info:
                raise ValueError('IBM 행사 시간 형식 변경')
            events.append(event_from_text(company_title(symbol, text(headings[0])), text(info[0]), detail))
        return events
    if kind == 'marvell':
        cards = tree.xpath('//article[contains(@class,"media")]')
        if not cards:
            raise ValueError('마벨 일정 형식 변경')
        for card in cards:
            dates = card.xpath('.//time')
            headings = card.xpath('.//*[contains(@class,"media-heading")]')
            if dates and headings:
                events.append(event_from_text(company_title(symbol, text(headings[0])), text(dates[0]), source))
        return events
    # Drupal investor sites publish one date field per event card.
    dates = tree.xpath('//*[contains(@class,"date-display-single")]')
    if not dates:
        raise ValueError('기업 IR 일정 형식 미확인')
    for date_node in dates:
        parent = date_node
        for _ in range(7):
            links = parent.xpath('.//a[contains(@href,"event-details")]')
            if links:
                events.append(event_from_text(company_title(symbol, text(links[0])), text(date_node), source))
                break
            parent = parent.getparent()
            if parent is None:
                raise ValueError('기업 행사 제목 미확인')
        else:
            raise ValueError('기업 행사 제목 미확인')
    return events


def schedule_symbols(day=None):
    from universe import active_symbols
    from portfolio_events import holdings
    return set(active_symbols()) | set(holdings(day)['symbols'])


def parse_earnings(raw, day):
    from universe import active_symbols
    payload = json.loads(raw)
    if payload.get('status', {}).get('rCode') != 200:
        raise ValueError('실적 달력 응답 실패')
    data = payload.get('data') or {}
    if datetime.strptime(data.get('asOf', ''), '%a, %b %d, %Y').date() != day:
        raise ValueError('실적 달력 기준일 불일치')
    rows = data.get('rows')
    if not isinstance(rows, list):
        raise ValueError('실적 달력 데이터 미확인')
    active, events, seen = schedule_symbols(day), [], set()
    labels = {'time-pre-market': '미국 장전', 'time-after-hours': '미국 장후 · 한국 익일',
              'time-not-supplied': '발표 시간 미정'}
    for row in rows:
        symbol = row['symbol']
        if (symbol not in active and symbol.replace('.', '-') not in active) or symbol in seen:
            continue
        seen.add(symbol)
        item = event(f'{symbol} 실적 발표', day, None,
                     'https://www.nasdaq.com/market-activity/earnings?date=' + day.isoformat(),
                     note=labels.get(row.get('time'), '발표 시간 미정'))
        item.update(kind='earnings', symbol=symbol)
        events.append(item)
    # A dated marker distinguishes a successful empty calendar from an unqueried source.
    events.append(dict(event('실적 달력 조회 완료', day, None,
                             'https://www.nasdaq.com/market-activity/earnings'), kind='earnings_checked'))
    return events


def parse_consensus(raw, day, source, currency="USD"):
    """Read the dated public HTML table, retaining period and units verbatim."""
    tree = document(raw)
    heading = ' '.join(tree.xpath('//h1//text()'))
    country = 'United States' if currency == 'USD' else GLOBAL_CALENDARS[currency][0]
    if english_date(heading) != day or country+' Economic Calendar' not in heading or 'US EASTERN TIME' not in text(tree):
        raise ValueError('경제일정 날짜·국가·시간대 미확인')
    tables = tree.xpath('//table[./tr/td/b[text()="Forecast"]]')
    if len(tables) != 1:
        raise ValueError('예상·이전 표 형식 변경')
    current_day = None
    output = []
    aliases = {
        'Initial Jobless Claims': ('신규 실업수당 청구', '신규 실업수당 청구'),
        'Continuing Claims': ('계속 실업수당 청구', '계속 실업수당 청구'),
        'Univ. of Mich. Consumer Confidence Index': ('미시간대 소비자심리', '미시간대 소비자심리'),
        'Interest Rate Decision': ('FOMC 금리결정', 'FOMC 금리결정'),
        'Nonfarm Payrolls': ('비농업고용', '고용지표 (비농업고용·실업률)'),
        'Unemployment Rate': ('실업률', '고용지표 (비농업고용·실업률)'),
        'Monthly Budget Statement': ('재정수지', '재정수지'),
    }
    for row in tables[0].xpath('./tr'):
        cells = row.xpath('./td')
        if not cells:
            continue
        if cells[0].get('colspan') == '4':
            match = re.fullmatch(r'[A-Za-z]+\., ([A-Za-z]{3}) (\d{1,2})', text(cells[0]))
            current_day = datetime.strptime(f'{day.year} {match[1]} {match[2]}', '%Y %b %d').date() if match else None
            if [text(c) for c in cells[1:]] != ['Period', 'Actual', 'Forecast', 'Previous']:
                raise ValueError('경제일정 열 순서 변경')
            continue
        if current_day != day or len(cells) != 8 or text(cells[1]) != currency:
            continue
        name = text(cells[3])
        if currency != 'USD':
            region, bank = GLOBAL_CALENDARS[currency][1:]
            policy = bool(re.search(r'interest rate decision|policy rate|deposit (?:facility )?rate|refinancing rate|bank rate', name, re.I))
            conference = bool(re.search(r'(?:BOJ|ECB|BOE).*press conference|monetary policy (?:statement|report)', name, re.I))
            if not (policy or conference):
                continue
            group = bank+' 금리결정' if policy else bank+' 통화정책 발표'
            value = text(cells[2])
            if value.lower() in ('', '-', 'tentative', 'tba', 'all day'):
                value = None
            item = event(region+' '+group+' · '+name, day, value, source)
            item.update(kind='indicator', global_impact=True, country=region, currency=currency,
                        macro_group=group, source_name=name, period=text(cells[4]), stars=3,
                        forecast=None if text(cells[6]) in ('', '-') else text(cells[6]),
                        previous=None if text(cells[7]) in ('', '-') else text(cells[7]))
            output.append(item)
            continue
        label, group = aliases.get(name, (None, None))
        for pattern, translated, official in (
            (r'(Core )?CPI \((M-o-M|Y-o-Y)\)', 'CPI 소비자물가지수', 'CPI 소비자물가지수'),
            (r'(Core )?PPI \((M-o-M|Y-o-Y)\)', 'PPI 생산자물가지수', 'PPI 생산자물가지수'),
            (r'(Core )?PCE Price Index \((M-o-M|Y-o-Y)\)', 'PCE 물가지수', '개인소득·소비 / PCE 물가'),
        ):
            match = re.fullmatch(pattern, name)
            if match:
                label = ('근원 ' if match[1] else '') + translated + (' · 전월비' if match[2] == 'M-o-M' else ' · 전년비')
                group = official
        if label is None:
            # Preserve the source's specific indicator name instead of guessing a series.
            for key, official in MAJOR.items():
                if key in name.lower():
                    label, group = name, official
                    break
        if label is None:
            continue
        period = text(cells[4])
        item = event(label + (f' [{period}]' if period != '-' else ''), day, text(cells[2]), source)
        item.update(kind='indicator', macro_group=group, source_name=name, period=period,
                    forecast=None if text(cells[6]) in ('', '-') else text(cells[6]),
                    previous=None if text(cells[7]) in ('', '-') else text(cells[7]))
        output.append(item)
    return output


def apply_investing(raw, day, events):
    """Prefer Investing values only for an exact US date, series and period match."""
    tree = document(raw)
    tables = tree.xpath('//table[.//th[normalize-space(.)="Forecast"]]')
    if len(tables) != 1:
        raise ValueError('Investing 예상 열 미확인')
    headers = [text(c) for c in tables[0].xpath('.//tr[th][1]/th')]
    if headers[:7] != ['Time', 'Cur.', 'Event', 'Imp.', 'Actual', 'Forecast', 'Previous']:
        raise ValueError('Investing 열 순서 변경')
    def key(name):
        return re.sub(r'[^a-z0-9]', '', name.lower())
    by_key = {(key(e['source_name']), key(e['period'])): e for e in events}
    current_day, matched, found_date = None, 0, False
    for row in tables[0].xpath('.//tr'):
        cells = row.xpath('./td')
        if len(cells) == 1 and cells[0].get('colspan'):
            try:
                current_day = english_date(text(cells[0]))
            except ValueError:
                current_day = None
            found_date |= current_day == day
            continue
        if current_day != day or len(cells) != 9 or not cells[2].xpath('.//*[@data-test="flag-US"]'):
            continue
        links = cells[3].xpath('.//a[@href]')
        if not links:
            continue
        name = text(links[0])
        period = re.search(r'\s+\(([^()]*)\)$', name)
        name_key = key(name[:period.start()] if period else name)
        period_key = key(period[1]) if period else ''
        item = by_key.get((name_key, period_key))
        if item is None:
            continue
        source = links[0].get('href')
        if urlsplit(source).scheme != 'https' or urlsplit(source).hostname != 'www.investing.com':
            continue
        stars = cells[4].xpath('.//*[local-name()="svg"]')
        active = [n for n in stars if n.get('class') == 'opacity-60']
        if len(stars) != 3 or any(n.get('class') not in ('opacity-60', 'opacity-20') for n in stars):
            continue
        item.update(source=source, stars=len(active), forecast=text(cells[6]) or None,
                    previous=text(cells[7]) or None)
        matched += 1
    if not found_date:
        raise ValueError('Investing 발표일 불일치')
    return matched


def parse_boj(raw, day):
    tree = document(raw)
    tables = tree.xpath('//table[caption[contains(., "'+str(day.year)+'")]]')
    if len(tables) != 1 or 'Date of MPM' not in text(tables[0]):
        raise ValueError('BOJ 회의 연도·표 미확인')
    for row in tables[0].xpath('.//tbody/tr'):
        cells = row.xpath('./td')
        if not cells:
            continue
        value = text(cells[0])
        month = re.match(r'([A-Za-z]+)\.?', value)
        dates = re.findall(r'\b(\d{1,2})\s*\(', value)
        if not month or not dates:
            raise ValueError('BOJ 회의 날짜 형식 변경')
        number = next((i for i in range(1,13) if calendar.month_name[i].lower().startswith(month[1].lower())), None)
        if number is None:
            raise ValueError('BOJ 회의 월 미확인')
        if date(day.year,number,int(dates[-1])) == day:
            item = event('일본 BOJ 금리결정',day,None,BOJ_CALENDAR,
                         note='회의 종료일 · 발표 시각 미정')
            item.update(kind='indicator',global_impact=True,country='일본',currency='JPY',
                        macro_group='BOJ 금리결정',stars=3,local_zone='Asia/Tokyo')
            return [item]
    return []


def classify_company_events(events, symbol):
    """An earnings call confirms a call, not the earnings release timestamp."""
    for item in events:
        title = item['title'].lower()
        if re.search(r'\b(?:earnings|financial|results)\b.*\b(?:call|webcast)\b', title):
            item.update(kind='earnings_call', symbol=symbol)
    return events


def collect(day):
    from universe import active_symbols
    from portfolio_events import holdings, reviewed_events
    targets = holdings(day)
    active = set(active_symbols()) | set(targets['symbols'])
    fed_url = f'https://www.federalreserve.gov/newsevents/{day.year}-{calendar.month_name[day.month].lower()}.htm'
    sources = [
        ('CSQ 실적 달력', lambda: parse_earnings(request(EARNINGS + day.isoformat()), day)),
        ('뉴욕연준 경제지표', lambda: collect_nyfed(day)),
        ('연준', lambda: parse_fed(request(fed_url), day, fed_url)),
        ('국채 경매', lambda: parse_auctions(request(AUCTIONS), day)),
        ('국채 바이백', lambda: parse_buybacks(request(BUYBACK), day)),
        ('BOJ 공식 회의 일정', lambda: parse_boj(request(BOJ_CALENDAR), day+timedelta(days=1))),
    ] + [(f'{code} 핵심 일정 {offset}', lambda code=code, offset=offset: parse_consensus(
        request(CONSENSUS.format(day=(day+timedelta(days=offset)).isoformat()).replace('code=USD','code='+code)),
        day+timedelta(days=offset), CONSENSUS.format(day=(day+timedelta(days=offset)).isoformat()).replace('code=USD','code='+code), code))
        for code in GLOBAL_CALENDARS for offset in (0,1)] + [(symbol, lambda symbol=symbol: collect_company(symbol, day)) for symbol in CORPORATE if symbol in active]
    events, errors, succeeded = [], [], []
    for name, fetch in sources:
        try:
            fetched = fetch()
            events.extend(classify_company_events(fetched, name) if name in CORPORATE else fetched)
            succeeded.append(name)
        except Exception as exc:
            reason = source_error(exc)
            if name in CORPORATE:
                # Reuse the already fetched calendar; it covers earnings, not conferences.
                checked = 'CSQ 실적 달력' in succeeded and any(
                    e.get('kind') == 'earnings_checked' and e['local_day'] == day.isoformat()
                    for e in events)
                listed = any(e.get('kind') == 'earnings' and e.get('symbol') == name
                             and e['local_day'] == day.isoformat() for e in events)
                fallback = ('실적 Nasdaq 달력 대체 확인(' + ('당일 등재 있음' if listed else '당일 등재 없음') + ')'
                            if checked else '실적 대체 달력도 미확인')
                errors.append(f'{name} 공식 IR: {reason} / {fallback} · 기타 행사 미확인')
            else:
                errors.append(name)
            print(f'{name}: {reason}', file=sys.stderr)
    try:
        source = CONSENSUS.format(day=day.isoformat())
        detailed = parse_consensus(request(source), day, source)
        try:
            if apply_investing(request(INVESTING), day, detailed):
                succeeded.append('Investing 예상·이전·중요도')
        except Exception:
            # The dated public table still provides independently sourced values.
            print('Investing 미확인: 공개 대체 출처 유지', file=sys.stderr)
        # Replace only the corresponding release at the same instant, not another series.
        covered = {(e['macro_group'], e['at']) for e in detailed}
        events = [e for e in events if (e['title'], e['at']) not in covered] + detailed
        succeeded.append('공개 경제일정 예상·이전')
    except Exception as exc:
        errors.append('공개 경제일정 예상·이전')
        print(f'공개 경제일정: {type(exc).__name__}', file=sys.stderr)
    # Keep the official BOJ date fallback only when no timed decision is available.
    timed_boj = {datetime.fromisoformat(e['at']).astimezone(KST).date().isoformat()
                 for e in events if e.get('macro_group')=='BOJ 금리결정' and e['at']}
    events = [e for e in events if not (e.get('local_zone')=='Asia/Tokyo' and e['local_day'] in timed_boj)]
    portfolio, portfolio_errors = reviewed_events(day, targets)
    events.append({'kind': 'portfolio_targets', 'symbols': targets['symbols'], 'as_of': targets['as_of']})
    events.extend(portfolio)
    errors.extend(portfolio_errors)
    if targets['symbols'] and not portfolio_errors:
        succeeded.append('보유 종목 주요 행사 팩트체크')
    return events, succeeded, errors


def event_stars(item):
    """Editorial priority, independent of article sentiment or importance scores."""
    if type(item.get('stars')) is int and 0 <= item['stars'] <= 3:
        return item['stars']
    title = (item.get('macro_group', '') + ' ' + item['title']).lower()
    if any(k in title for k in ('cpi', 'ppi', 'pce', 'fomc', '비농업', '실업률', 'gdp', '금리결정')):
        return 3
    if item.get('kind') in ('earnings', 'earnings_call') or any(k in title for k in ('jolts', '실업수당', 'adp', 'ism', '소매판매', '소비자', '연준 연설')):
        return 2
    if any(k in title for k in ('경매', '바이백', '베이지북', '컨퍼런스', 'conference')):
        return 1
    return 0


def indicator_values(item):
    if item.get('kind') != 'indicator' and item['title'] not in MAJOR.values() and '금리결정' not in item['title']:
        return ''
    # Only explicitly supplied observations may be displayed; never infer consensus.
    def value(key):
        raw = item.get(key)
        return html_escape.escape(str(raw)) if raw is not None and str(raw).strip() not in ('', '-') else '미확인'
    return f'(예상: {value("forecast")} · 이전: {value("previous")})'


def render(events, session_day, errors, start_time=time(19, 30)):
    from universe import active_symbols
    active = set(active_symbols()) | {s for e in events if e.get('kind') == 'portfolio_targets' for s in e['symbols']}
    start = datetime.combine(session_day, start_time, KST)
    end = datetime.combine(session_day + timedelta(days=1), time(9), KST)
    selected, calls, watches, seen = [], [], [], set()
    us_day = start.astimezone(ET).date().isoformat()
    earnings = [e for e in events if e.get('kind') == 'earnings'
                and e['local_day'] == us_day and (e.get('symbol') in active or e.get('symbol', '').replace('.', '-') in active)]
    checked = any(e.get('kind') == 'earnings_checked' and e['local_day'] == us_day for e in events)
    for item in events:
        if item.get('kind') == 'portfolio_targets':
            continue
        if item.get('kind') in ('earnings', 'earnings_checked'):
            continue
        if any(f'({symbol})' in item['title'] for symbol in CORPORATE if symbol not in active):
            continue
        when = datetime.fromisoformat(item['at']) if item['at'] else None
        if when and when.tzinfo is None:
            raise ValueError('일정 시간대 누락')
        if item.get('kind') in ('portfolio_event', 'portfolio_watch') and item.get('symbol') not in active:
            continue
        item_end = start+timedelta(days=1) if item.get('global_impact') or item.get('kind') in ('portfolio_event', 'portfolio_watch') else end
        if when and not start <= when < item_end:
            continue
        expected_day = (session_day+timedelta(days=1)).isoformat() if item.get('local_zone')=='Asia/Tokyo' else start.astimezone(ET).date().isoformat()
        if not when:
            if item.get('kind') in ('portfolio_event', 'portfolio_watch'):
                zone = ZoneInfo(item['local_zone'])
                if item['local_day'] not in {start.astimezone(zone).date().isoformat(), item_end.astimezone(zone).date().isoformat()}:
                    continue
            elif item['local_day'] != expected_day:
                continue
        key = (item['title'], item['at'], item['local_day'])
        if key in seen:
            continue
        seen.add(key)
        (watches if item.get('kind') == 'portfolio_watch' else calls if item.get('kind') == 'earnings_call' else selected).append(item)
    calls.sort(key=lambda e: (e['at'] is None, e['at'] or '', e['title']))
    selected.sort(key=lambda e: (e['at'] is None, e['at'] or '', e['title']))
    title = f'금일 주요 일정\n{start:%m/%d %H:%M} ~ {end:%m/%d} 09:00 (한국시간)'
    if any(e.get('global_impact') for e in selected):
        title += f'\n해외 핵심 일정: 익일 {start:%H:%M}까지 포함'
    if any(e.get('kind') == 'portfolio_event' for e in selected):
        title += f'\n보유 종목 주요 행사: 익일 {start:%H:%M}까지 포함'
    lines = [html_escape.escape(title), '']
    for item in selected:
        when = datetime.fromisoformat(item['at']) if item['at'] else None
        prefix = when.strftime('%H:%M') + (' (익일)' if when.date() > session_day else '') if when else '시간 미정 (' + ((item.get('country', '') + ' 현지 ') if item.get('kind') == 'portfolio_event' else '일본 ' if item.get('local_zone')=='Asia/Tokyo' else '미 동부 기준 ') + item['local_day'][5:].replace('-', '/') + ')'
        label = f'{prefix} {item["title"]}' + (' ' + '★' * event_stars(item) if event_stars(item) else '') + (f' [{item["note"]}]' if item['note'] else '')
        url = item['source']
        if urlsplit(url).scheme != 'https' or urlsplit(url).username:
            raise ValueError('출처 주소 오류')
        lines.append(f'<a href="{html_escape.escape(url, quote=True)}">{html_escape.escape(label)}</a>')
        values = indicator_values(item)
        if values:
            lines.append(values)
    if not selected:
        lines.append('확인된 일정 없음' if not errors else '확인된 일정 없음 — 일부 출처 확인 실패')
    earnings_lines = ['<b>오늘 실적 발표 · 미국 ' + us_day[5:].replace('-', '/') + '</b>']
    for item in earnings:
        label = html_escape.escape(item['title'] + ' — ' + item['note'] + ' ' + '★' * event_stars(item))
        url = item['source']
        if urlsplit(url).scheme != 'https' or urlsplit(url).username:
            raise ValueError('실적 출처 주소 오류')
        earnings_lines.append(f'<a href="{html_escape.escape(url, quote=True)}">{label}</a>')
    if not earnings:
        earnings_lines.append('실적 발표 일정 미확인 · 아래 공식 실적콜 참고' if calls else
                              '달력상 CSQ 해당 종목 없음' if checked and 'CSQ 실적 달력' not in errors
                              else '⚠ 실적 일정 미확인 — 발표 없음으로 해석하지 마세요')
    for item in calls:
        when = datetime.fromisoformat(item['at']) if item['at'] else None
        prefix = when.strftime('%H:%M') + (' (익일)' if when.date() > session_day else '') if when else '시각 미확인'
        label = f'{prefix} {item["title"]} ' + '★' * event_stars(item)
        url = item['source']
        if urlsplit(url).scheme != 'https' or urlsplit(url).username:
            raise ValueError('실적콜 출처 주소 오류')
        earnings_lines.append(f'<a href="{html_escape.escape(url, quote=True)}">{html_escape.escape(label)}</a>')
    if calls:
        earnings_lines.append('실적콜 시작 시각은 실적자료 발표 시각과 다릅니다.')
    # Keep earnings visible even when the macro calendar is long.
    lines[2:2] = earnings_lines + ['', '<b>경제지표·주요 일정</b>', '']
    if watches:
        lines += ['', '<b>보유 종목 · 미확정 발표 관찰</b>']
        for item in sorted(watches, key=lambda e: (-event_stars(e), e['local_day'], e['title'])):
            label = item['title'] + ' ' + '★' * event_stars(item)
            lines.append(f'<a href="{html_escape.escape(item["source"], quote=True)}">{html_escape.escape(label)}</a>')
            lines.append('보도상 예상일이며 공식 발표일·시각 사전공지 미확인. 확정 일정과 구분합니다.')
            if item.get('consensus_source'):
                lines.append(f'<a href="{html_escape.escape(item["consensus_source"], quote=True)}">{html_escape.escape(item["consensus_label"])}</a>')
    lines += ['', '항목을 누르면 출처로 이동합니다.']
    selected.extend(earnings + calls + watches)
    result = '\n'.join(lines)
    # Do not silently truncate an oversized agenda or send a partial multi-message batch.
    plain = document('<div>' + result + '</div>').text_content()
    if len(plain.encode('utf-16-le')) // 2 > 3900:
        raise ValueError('일정이 메시지 길이 제한을 초과했습니다')
    return result, selected


def quantbot_destination():
    token = os.environ.get('NEWS_TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('NEWS_TELEGRAM_CHAT_ID', '').strip()
    values = {'BOT_TOKEN': token, 'ALLOWED_CHAT_IDS': [chat]}
    if not token and not chat:
        explicit_path = os.environ.get('QUANTBOT_CONFIG_PATH', '').strip()
        if not explicit_path:
            raise ValueError('Telegram 미설정: 환경변수 또는 명시적 설정 파일이 필요합니다')
        path = Path(explicit_path)
        for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ('BOT_TOKEN', 'ALLOWED_CHAT_IDS'):
                        values[target.id] = ast.literal_eval(node.value)
    token = values.get('BOT_TOKEN', '')
    groups = {str(v) for v in values.get('ALLOWED_CHAT_IDS', []) if re.fullmatch(r'-\d+', str(v))}
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token) or len(groups) != 1:
        raise ValueError('QuantBot 메인 토큰 또는 유일한 단체방 설정을 확인하세요')
    return token, groups.pop()


def telegram_paused(day=None):
    day = day or datetime.now(KST).date()
    path = DATA / 'telegram-skip-date.txt'
    return path.exists() and path.read_text(encoding='utf-8-sig').strip() == day.isoformat()


def telegram(method, payload, token):
    if method == 'sendMessage' and telegram_paused():
        raise RuntimeError('Telegram paused for today by user')
    result = json.loads(request(f'https://api.telegram.org/bot{token}/{method}', payload))
    if result.get('ok') is not True:
        raise RuntimeError('Telegram API 응답 실패')
    return result['result']


def destinations(path=DATA / 'agenda-recipients.json'):
    token, group = quantbot_destination()
    extra = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(extra, list) or any(not re.fullmatch(r'[1-9]\d*', str(v)) for v in extra):
        raise ValueError('추가 개인 수신자 설정 오류')
    return token, [group] + list(dict.fromkeys(str(v) for v in extra))


def send_all(message, day, token, chats, directory=DATA, kind=''):
    failed = 0
    for index, chat in enumerate(chats):
        # Preserve the existing group ledger; extra recipients have independent ledgers.
        name = 'agenda-delivery' if index == 0 else 'agenda-delivery-' + hashlib.sha256(chat.encode()).hexdigest()
        try:
            send_once(message, day, token, chat, directory / (name + ('-' + kind if kind else '') + '.sqlite'))
        except Exception:
            failed += 1
            print(f'수신처 {index + 1}: 전송 실패 또는 결과 미확인', file=sys.stderr)
    if failed:
        raise RuntimeError('일부 수신처 전송 실패 또는 결과 미확인')


def send_once(message, day, token, chat, db=DATA / 'agenda-delivery.sqlite'):
    if Path(db).stem.endswith('-news') and day.weekday() >= 5:
        print('주말 뉴스 메시지는 발송하지 않습니다.')
        return False
    if telegram_paused():
        print('오늘 Telegram 발송은 사용자 요청으로 생략합니다.')
        return False
    # At-most-once delivery: timeout/crash after claiming requires manual inspection.
    # ponytail: no automatic retries after ambiguous delivery; Telegram has no idempotency key.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS delivery (day TEXT PRIMARY KEY, status TEXT, digest TEXT, at TEXT)')
        try:
            conn.execute('INSERT INTO delivery VALUES (?, ?, ?, ?)',
                         (day.isoformat(), 'pending', hashlib.sha256(message.encode()).hexdigest(),
                          datetime.now(KST).isoformat()))
            conn.commit()
        except sqlite3.IntegrityError:
            print('이미 처리했거나 전송 확인이 필요한 날짜입니다. 중복 발송하지 않았습니다.')
            return False
        try:
            result = telegram('sendMessage', {'chat_id': chat, 'text': message, 'parse_mode': 'HTML',
                                             'link_preview_options': {'is_disabled': True}}, token)
            if not isinstance(result, dict) or 'message_id' not in result:
                raise RuntimeError('전송 결과 확인 실패')
        except Exception:
            conn.execute('UPDATE delivery SET status=? WHERE day=?', ('unknown', day.isoformat()))
            conn.commit()
            raise
        conn.execute('UPDATE delivery SET status=? WHERE day=?', ('sent', day.isoformat()))
        conn.commit()
    print('수신처 발송 완료.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, help='검토할 한국 날짜 (preview 전용)')
    parser.add_argument('--send', action='store_true', help='단체방에 실제 발송')
    parser.add_argument('--scheduled', action='store_true', help='19:30~20:30 KST 안에서만 실행')
    parser.add_argument('--agenda-only', action='store_true', help='뉴스 없이 일정만 별도 메시지로 발송 (19~23시 KST)')
    parser.add_argument('--check-destination', action='store_true', help='봇과 단체방 조회만 수행')
    parser.add_argument('--dry-run', action='store_true', help='설정만 검사 (통신·발송·DB 쓰기 없음)')
    parser.add_argument('--offline-dir', type=Path, help='미리 수집한 초안 JSON에서 preview (네트워크 없음)')
    args = parser.parse_args()
    now = datetime.now(KST)
    if args.dry_run:
        _, chats = destinations()
        print(f'설정 확인 완료: 매일 19:30 KST, 수신처 {len(chats)}곳, 저장 뉴스 + 공식 일정. 통신·발송 없음.')
        return
    if args.send and (args.date or args.offline_dir or not args.scheduled):
        raise ValueError('발송은 --send --scheduled 조합만 허용합니다')
    start, end = (time(19), time(23)) if args.agenda_only else (time(19, 30), time(20, 30))
    if args.scheduled and not start <= now.time().replace(tzinfo=None) < end:
        print('발송 시간 밖입니다. 지난 일정은 보내지 않았습니다.')
        return
    if args.check_destination:
        token, chats = destinations()
        me = telegram('getMe', {}, token)
        if not me.get('is_bot'):
            raise ValueError('봇 확인 실패')
        for index, chat in enumerate(chats):
            result = telegram('getChat', {'chat_id': chat}, token)
            expected = ('group', 'supergroup') if index == 0 else ('private',)
            if result.get('type') not in expected or str(result.get('id')) != chat:
                raise ValueError('수신처 확인 실패')
            print(f'수신처 {index + 1} 접근 확인 완료 (메시지 전송 없음).')
        return
    session_day = args.date or now.date()
    if args.offline_dir:
        record = json.loads((args.offline_dir / 'latest.json').read_text(encoding='utf-8'))
        events, good, errors = record['events'], record['sources_ok'], record['sources_failed']
    else:
        events, good, errors = collect(datetime.combine(session_day, start, KST).astimezone(ET).date())
    message, selected = render(events, session_day, errors, start_time=start)
    from briefing import compose
    if not args.agenda_only and now.weekday() < 5:
        message = compose(message, now)
    record = {'session_day': session_day.isoformat(), 'fetched_at': now.isoformat(),
              'events': events, 'selected': selected, 'sources_ok': good, 'sources_failed': errors}
    if not args.offline_dir:
        output = DATA / 'agenda'
        output.mkdir(parents=True, exist_ok=True)
        (output / 'latest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'latest.html').write_text('<!doctype html><meta charset="utf-8"><pre>' + message + '</pre>', encoding='utf-8')
    print(document('<div>' + message + '</div>').text_content())
    print(f'\n출처 확인 {len(good)}/{len(good) + len(errors)}, 대상 일정 {len(selected)}건')
    if args.send:
        if not good:
            raise RuntimeError('모든 출처 확인 실패: 일정 메시지를 발송하지 않았습니다')
        token, chats = destinations()
        send_all(message, session_day, token, chats, kind='agenda' if args.agenda_only or now.weekday() >= 5 else '')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        # Configuration/transport errors can contain private values; do not dump exceptions.
        print(f'일정 처리 실패 ({type(exc).__name__}). 설정과 data/agenda를 확인하세요.', file=sys.stderr)
        sys.exit(1)
