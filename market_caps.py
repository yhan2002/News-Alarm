"""Optional USD market-cap snapshot. Network runs only with --refresh."""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

PATH = Path(__file__).resolve().parent / 'data' / 'market-caps.json'
URL = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true'


def parse_rows(rows, symbols, now):
    result = {}
    for row in rows:
        symbol = row.get('symbol')
        if symbol not in symbols:
            continue
        try:
            value = float(str(row.get('marketCap', '')).replace(',', '').replace('$', ''))
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            result[symbol] = {'market_cap': value, 'market_cap_as_of': now}
            sector = row.get('sector')
            if isinstance(sector, str) and sector.strip():
                result[symbol]['sector'] = sector.strip()
    return result


def load_caps(path=PATH):
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
        return {symbol: row for symbol, row in rows.items()
                if isinstance(row, dict) and isinstance(row.get('market_cap'), (int, float))
                and math.isfinite(row['market_cap']) and row['market_cap'] > 0}
    except (OSError, ValueError, AttributeError):
        return {}


def refresh():
    from universe import active_symbols
    request = Request(URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                                   'Origin': 'https://www.nasdaq.com'})
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    rows = payload['data']['rows']
    caps = parse_rows(rows, set(active_symbols()), datetime.now(timezone.utc).isoformat())
    if not caps:
        raise ValueError('No valid USD market caps received; previous snapshot retained')
    saved = load_caps()
    saved.update(caps)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved), encoding='utf-8')
    temporary.replace(PATH)
    print(f'Market caps saved: {len(caps)} symbols; missing values remain unavailable.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', required=True)
    parser.parse_args()
    refresh()
