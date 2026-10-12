"""Optional ten-article GLM Flash pilot; dry-run unless --run is supplied."""
import argparse
import copy
import getpass
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import content_assessment as ca
from semantics import numbered_request, resolve_spans

ROOT = Path(__file__).resolve().parent
MODEL = 'glm-4.7-flash'
URL = 'https://api.z.ai/api/paas/v4/chat/completions'


def payload(text):
    _, prompt, numbered, _ = numbered_request(text, ca.SCHEMA)
    return {'model': MODEL, 'messages': [{'role': 'system', 'content': prompt},
            {'role': 'user', 'content': numbered}], 'response_format': {'type': 'json_object'},
            'thinking': {'type': 'disabled'}, 'temperature': 0, 'max_tokens': 1000, 'stream': False}


def parse(envelope, text):
    choice = envelope['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Incomplete response')
    raw = json.loads(choice['message']['content'])
    spans = numbered_request(text, ca.SCHEMA)[3]
    facts = ca.validate(resolve_spans(copy.deepcopy(raw), spans), text)
    return {'raw_fields': raw, 'validated': ca.classify(facts, '제목·요약만 확인')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    sample = json.loads((ROOT/'data/qwen35-pilot-input.json').read_text(encoding='utf-8'))
    if not 1 <= len(sample) <= 10 or any(not isinstance(r.get('input'), str) or len(r['input']) > 10000 for r in sample):
        raise SystemExit('Expected 1-10 bounded pilot inputs')
    print(f'{MODEL}: {len(sample)} title/RSS inputs; no tools, no production writes; max 1000 output tokens/call.')
    if not args.run:
        print('Dry run only. --run sends these inputs to Z.ai; verify current free-model pricing first.')
        return
    key = os.environ.get('ZAI_API_KEY') or getpass.getpass('Z.ai API key (hidden, not saved): ')
    if not key.strip():
        raise SystemExit('API key required')
    target = ROOT/'data'/f'glm-pilot-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    rows = []
    for item in sample:
        row = dict(item)
        start = time.monotonic()
        try:
            request = Request(URL, data=json.dumps(payload(item['input'])).encode(),
                              headers={'Authorization': 'Bearer '+key.strip(), 'Content-Type': 'application/json'})
            with urlopen(request, timeout=90) as response:
                body = response.read(128001)
            if len(body) > 128000:
                raise ValueError('Response too large')
            envelope = json.loads(body)
            row['response'] = envelope
            row.update(parse(envelope, item['input']))
        except HTTPError as exc:
            row['error'] = f'HTTP {exc.code}; stopped without retry or model fallback'
        except Exception as exc:
            row['error'] = type(exc).__name__  # Never log request headers or credentials.
        row['seconds'] = round(time.monotonic()-start, 2)
        rows.append(row)
        target.write_text(json.dumps({'model': MODEL, 'prompt': 'narrative-first v2', 'rows': rows},
                                    ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'{len(rows)}/{len(sample)}: {row.get("error", "complete")} ({row["seconds"]}s)', flush=True)
        if 'error' in row:
            break
    print(target)


if __name__ == '__main__':
    main()
