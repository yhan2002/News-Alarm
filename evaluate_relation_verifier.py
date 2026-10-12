"""Frozen, pre-labelled Jev integration evaluation; shared $1 test ledger, dry-run default."""
import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import news
import openrouter_worker as w
import relation_verifier as v

LEDGER = news.ROOT/'data/jev-test-budget.sqlite'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    raw = args.input.read_bytes()
    cases = json.loads(raw)['cases']
    if not 1 <= len(cases) <= 30 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Expected 1-30 unique cases')
    for case in cases:
        checks = v.plan(case['facts'], case['text'])
        if not checks or set(case['expected']) != {c['id'] for c in checks} or any(type(x) is not bool for x in case['expected'].values()):
            raise ValueError('Every generated check needs a pre-labelled expected answer')
    print(f'{len(cases)} cases; shared cumulative test limit $1; cache reuse; no production writes.')
    if not args.run:
        print('OFFLINE: no key, network, ledger or output writes.')
        return 0
    result = {'input_sha256': hashlib.sha256(raw).hexdigest(), 'rows': []}
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(result, target)
    key = w.api.load_key()
    with sqlite3.connect(LEDGER) as conn, patch.dict(w.LIMITS, day=1, month=1, total=1), patch.dict(v.LIMITS, day=1, month=1):
        w.setup(conn)
        # Include earlier Jev pilots conservatively; import each result file only once.
        for filename in ('jev-pilot-20260923.json', 'jev-relations-result-20260923.json'):
            path = news.ROOT/'data'/filename
            reference = 'prior:'+filename
            if path.exists() and not conn.execute('SELECT 1 FROM openrouter_calls WHERE key=?',(reference,)).fetchone():
                previous=json.loads(path.read_text(encoding='utf-8'))
                amount=sum((r.get('response',{}).get('usage',{}).get('cost', .01)) for r in previous['rows'])
                conn.execute('INSERT INTO openrouter_calls(at,key,cost,body) VALUES(?,?,?,?)',
                             (news.utcnow().isoformat(),reference,amount,json.dumps({'imported':True})))
                conn.commit()
        for case in cases:
            started = time.monotonic()
            verification = v.verify(conn,case['facts'],case['text'],key,news.utcnow(),timeout=30)
            result['rows'].append({'id':case['id'],'verification':verification,'seconds':round(time.monotonic()-started,4)})
            result['test_budget_spent_or_reserved'] = conn.execute('SELECT SUM(cost) FROM openrouter_calls').fetchone()[0]
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"{len(result['rows'])}/{len(cases)} {verification['status']}",flush=True)
            if verification['status'] != 'done':
                return 1
    counts = {'correct':0,'wrong':0,'uncertain':0}
    for case, row in zip(cases,result['rows']):
        for check in row['verification']['checks']:
            expected=case['expected'][check['id']]
            outcome='uncertain' if check['decision']=='uncertain' else 'correct' if (check['decision']=='supported')==expected else 'wrong'
            counts[outcome]+=1
    result['metrics']=counts
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(counts), 'budget:', result['test_budget_spent_or_reserved'])
    return 0


if __name__=='__main__':
    raise SystemExit(main())
