"""Read CSQ UNIV without importing or executing QuantBot. Never fetch on import."""
import ast
import os
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config' / 'universe.example.py'
# Confirmed corporate actions; do not delete symbols merely because a quote lookup fails.
EXCLUDED = {
    'MASI': ('인수 완료·상장 종료', 'https://www.sec.gov/Archives/edgar/data/937556/000110465926072151/tm2617395d1_8k.htm'),
    'NUVL': ('인수 완료·상장 종료', 'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-493'),
    'LNWO': ('미국 거래소 상장 종료·호주 상장 전환', 'https://explore.lnw.com/media/p4lppw0u/final_press-release-for-oct-13-2025.pdf'),
}


def csq_symbols(path=None):
    # Explicit private configuration only; never search neighboring projects.
    path = Path(path or os.environ.get('NEWS_UNIVERSE_CONFIG') or CONFIG)
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'universe_groups' for t in node.targets):
            values = ast.literal_eval(node.value)['CSQ_UNIV']
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', v) for v in values):
                raise ValueError('CSQ_UNIV 형식 오류')
            return list(dict.fromkeys(values))
    raise ValueError('QuantBot CSQ_UNIV 미확인')


def listing_names(directory=ROOT / 'data' / 'listings'):
    names = {}
    for filename, key in [('nasdaqlisted', 'Symbol'), ('otherlisted', 'ACT Symbol')]:
        path = directory / (filename + '.txt')
        if not path.exists():
            continue
        with path.open(encoding='utf-8-sig') as handle:
            for row in csv.DictReader(handle, delimiter='|'):
                if row.get('Test Issue') == 'N' and row.get(key):
                    names[row[key]] = row['Security Name']
    return names


def active_symbols():
    return [symbol for symbol in csq_symbols() if symbol not in EXCLUDED]


def universe_status():
    symbols, names = csq_symbols(), listing_names()
    return {'name': 'CSQ UNIV', 'symbols': active_symbols(), 'original_count': len(symbols),
            'excluded': [{'symbol': s, 'reason': EXCLUDED[s][0], 'source': EXCLUDED[s][1]} for s in symbols if s in EXCLUDED],
            'unverified': [s for s in symbols if s not in names and s not in EXCLUDED],
            'listing_checked': '2026-09-10', 'source': 'https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs'}


def expand_universe(known):
    names = listing_names()
    result = {}
    for symbol in active_symbols():
        if symbol in known:
            result[symbol] = known[symbol]
            continue
        name = names.get(symbol, symbol).split(' - ')[0]
        alias = re.sub(r'\s+(?:Common Stock|Ordinary Shares|Class [A-Z]\b.*)$', '', name, flags=re.I)
        alias = re.sub(r'\s+(?:Inc\.?|Corporation|Corp\.?|plc|Ltd\.?)\b.*$', '', alias, flags=re.I).strip().rstrip(',')
        aliases = [alias.lower()] if len(alias) > 3 and alias.upper() != symbol else []
        result[symbol] = (name, aliases, [])
    return result
