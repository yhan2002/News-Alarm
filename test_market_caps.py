"""Offline market-cap input checks; no network calls."""
from market_caps import parse_rows

rows = [{'symbol': 'A', 'marketCap': '1,234'},
        {'symbol': 'B', 'marketCap': 'N/A'},
        {'symbol': 'C', 'marketCap': 'NaN'},
        {'symbol': 'D', 'marketCap': '-1'},
        {'symbol': 'OUTSIDE', 'marketCap': '500'}]
assert parse_rows(rows, {'A', 'B', 'C', 'D'}, 'now') == {
    'A': {'market_cap': 1234.0, 'market_cap_as_of': 'now'}}
print('Market cap parsing and universe filtering passed.')
