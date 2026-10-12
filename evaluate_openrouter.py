"""Evaluate a frozen 1-10 article sample; explicit paid --run, no production changes."""
import argparse
import json
import time
from pathlib import Path
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import openrouter_pilot as p


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', type=Path, help='Reuse completed results from the same frozen sample/model')
    args = parser.parse_args()
    sample = json.loads((p.ca.ROOT/'data/openrouter-eval-input.json').read_text(encoding='utf-8'))
    if not 1 <= len(sample) <= 10:
        raise ValueError('Expected 1-10 frozen articles')
    requests = [p.payload(row['input'], p.MODELS[1]) for row in sample]
    print(f'{len(sample)} sequential calls; Qwen3.7 Flash; no retries; <=1600 output tokens each; $0.05 run stop threshold')
    if not args.run:
        print('Offline preview; no API or production writes.')
        return
    key = p.load_key()
    target = p.ca.ROOT/'data'/f'openrouter-eval-{datetime.now():%Y%m%d-%H%M%S}.json'
    previous = json.loads(args.resume.read_text(encoding='utf-8')) if args.resume else {}
    if previous and previous.get('model') != p.MODELS[1]:
        raise ValueError('Resume model mismatch')
    results = [row for row in previous.get('rows',[]) if 'result' in row and not row.get('error')]
    by_id = {item['id']:item for item in sample}
    for row in results:
        item = by_id[row['id']]
        if row['baseline'].get('text_hash') != item['baseline'].get('text_hash'):
            raise ValueError('Resume source changed')
    spent = sum(row['usage']['cost'] for row in results)
    completed = {row['id'] for row in results}
    for item, payload in zip(sample, requests):
        if item['id'] in completed:
            continue
        # Conservative allowance per next request; provider-reported cost checked afterwards.
        if spent + .01 > .05:
            break
        time.sleep(5)
        row = {k:v for k,v in item.items() if k != 'input'}
        started = time.monotonic()
        try:
            request = Request(p.URL, data=json.dumps(payload).encode(), headers={
                'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
            with urlopen(request, timeout=90) as response:
                raw = response.read(128001)
            if len(raw) > 128000:
                raise ValueError('Response too large')
            envelope = json.loads(raw)
            row['usage'] = envelope.get('usage', {})
            row['response_model'] = envelope.get('model')
            row['raw_response'] = envelope['choices'][0]
            row['result'] = p.parse(envelope, item['input'], item['coverage'], item['published_at'])
            cost = row['usage'].get('cost')
            if not isinstance(cost, (float,int)) or cost < 0:
                raise ValueError('Missing usage cost; stop before another request')
            spent += cost
        except HTTPError as exc:
            row['error'] = f'HTTP {exc.code}'
        except Exception as exc:
            row['error'] = type(exc).__name__
        row['seconds'] = round(time.monotonic()-started,3)
        results.append(row)
        target.write_text(json.dumps({'model':p.MODELS[1], 'cost_usd':spent, 'rows':results},
                                    ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{len(results)}/{len(sample)} {row.get('error','complete')} {row['seconds']}s",flush=True)
        if row.get('error'):
            break
    print(target)


if __name__ == '__main__':
    main()
