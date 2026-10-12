"""Read current portfolio tickers only; never expose accounts or mutate portfolio data."""
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def holdings(day=None, directory=None):
    day = day or datetime.now(ZoneInfo('Asia/Seoul')).date()
    directory = directory or os.environ.get('NEWS_PORTFOLIO_ROOT')
    if not directory:
        return {'symbols': [], 'as_of': [], 'errors': ['NEWS_PORTFOLIO_ROOT 미설정: 보유 포지션 조회 비활성']}
    directory = Path(directory)
    try:
        master = max((directory / 'Master_Dashboard').glob('*_index.html'))
        if master.stat().st_size > 10_000_000:
            raise ValueError('Master size')
        raw = master.read_text(encoding='utf-8-sig')
        marker = 'const CARDS = '
        cards, _ = json.JSONDecoder().raw_decode(raw[raw.index(marker) + len(marker):])
        active = set()
        for card in cards:
            stamp = datetime.strptime(card['date'], '%Y%m%d').date()
            if card.get('goal_achieved') or not 0 <= (day - stamp).days <= 7:
                continue
            # Same fund naming convention as build_risk_dashboard.analyze.
            fund = Path(unquote(urlsplit(card['html_link']).path)).stem.removeprefix('Wrap Fund Analysis (').removesuffix(')')
            active.add(fund)
        tickers, dates, errors = set(), set(), []
        with closing(sqlite3.connect((directory / 'wrap_market_data.sqlite3').as_uri() + '?mode=ro', uri=True)) as conn:
            for fund in active:
                latest = conn.execute('SELECT MAX(report_date) FROM holdings WHERE fund=? AND report_date<=?',
                                      (fund, day.strftime('%Y%m%d'))).fetchone()[0]
                if not latest or (day - datetime.strptime(latest, '%Y%m%d').date()).days > 7:
                    errors.append('보유 포지션 최신 스냅샷 미확인')
                    continue
                dates.add(latest)
                for (ticker,) in conn.execute('SELECT DISTINCT i.provider_ticker FROM holdings h LEFT JOIN instruments i ON h.code=i.code '
                                             'WHERE h.fund=? AND h.report_date=? AND h.weight_pct>0', (fund, latest)):
                    if not isinstance(ticker, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}', ticker):
                        errors.append('보유 종목 식별자 미매핑')
                    else:
                        tickers.add(ticker)  # Long and short both matter; never net away held tickers.
        if not active:
            errors.append('보유 포지션 최신 계좌 목록 미확인')
        return {'symbols': sorted(tickers), 'as_of': sorted(dates), 'errors': sorted(set(errors))}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {'symbols': [], 'as_of': [], 'errors': ['보유 포지션 자료 조회 실패']}


def reviewed_events(day, targets):
    """Daily human fact-check cache, not inferred earnings/delivery dates."""
    path = ROOT / 'data' / 'portfolio-events' / (day.isoformat() + '.json')
    if not targets['symbols']:
        return [], targets['errors']
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value['date'] != day.isoformat():
            raise ValueError('Review date')
        if not isinstance(value['reviewed_symbols'], list) or not isinstance(value['events'], list):
            raise ValueError('Review format')
        checked = set(value['reviewed_symbols'])
        if not checked <= set(targets['symbols']):
            raise ValueError('Review targets changed')
        events = []
        for item in value['events']:
            if not isinstance(item, dict):
                raise ValueError('Event format')
            if item['symbol'] not in checked or item.get('kind') not in ('portfolio_event', 'portfolio_watch'):
                raise ValueError('Unreviewed event')
            certainty = 'reported' if item['kind'] == 'portfolio_watch' else 'official'
            if item.get('certainty') != certainty or not item.get('evidence'):
                raise ValueError('Missing event evidence')
            if item.get('consensus_source') or item.get('consensus_label'):
                url = urlsplit(item.get('consensus_source', ''))
                if url.scheme != 'https' or not url.hostname or url.username or not item.get('consensus_label'):
                    raise ValueError('Consensus source')
            url = urlsplit(item['source'])
            if url.scheme != 'https' or not url.hostname or url.username:
                raise ValueError('Source')
            if item['at']:
                if datetime.fromisoformat(item['at']).tzinfo is None:
                    raise ValueError('Missing timezone')
            elif not item.get('local_zone'):
                raise ValueError('Date-only timezone')
            datetime.strptime(item['local_day'], '%Y-%m-%d')
            if item['stars'] not in (2, 3):
                raise ValueError('Priority')
            events.append(item)
        errors = list(targets['errors'])
        if checked != set(targets['symbols']):
            errors.append('보유 종목 주요 행사 일부 미확인')
        return events, errors
    except (OSError, ValueError, KeyError, TypeError):
        return [], targets['errors'] + ['보유 종목 주요 행사 확인 미완료']


if __name__ == '__main__':
    # This output contains ticker/date/coverage only, no account names or position sizes.
    print(json.dumps(holdings(), ensure_ascii=False))
