"""Bounded OpenRouter connection pilot. Default: offline preview; production is unchanged."""
import argparse
import getpass
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import content_assessment as ca
import first_pass as fp
import news
from semantics import numbered_request, resolve_spans, TOPICS

URL = 'https://openrouter.ai/api/v1/chat/completions'
MODELS = ('qwen/qwen3.5-flash-02-23', 'qwen/qwen3.7-flash')
KEY_FILE = ca.ROOT/'data/openrouter-key.dpapi'


def save_key():
    key = getpass.getpass('OpenRouter key (hidden; saved with Windows encryption): ').strip()
    if not key.startswith('sk-or-') or any(c.isspace() for c in key):
        raise ValueError('Invalid OpenRouter key format')
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    command = "$k=[Console]::In.ReadLine(); ConvertTo-SecureString $k -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $env:NEWS_OR_KEY_FILE -Encoding ascii"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            input=key+'\n', capture_output=True, text=True, errors='replace', timeout=15,
                            env={**{k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'},
                                 'NEWS_OR_KEY_FILE': str(KEY_FILE)}, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('Encrypted key storage failed')
    print('Key encrypted for the current Windows account.')


def load_key():
    if os.environ.get('OPENROUTER_API_KEY'):
        return os.environ['OPENROUTER_API_KEY'].strip()
    if KEY_FILE.exists():
        command = "$s=(Get-Content -LiteralPath $env:NEWS_OR_KEY_FILE -Raw).Trim() | ConvertTo-SecureString; [System.Net.NetworkCredential]::new('', $s).Password"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                env={**{k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'},
                                     'NEWS_OR_KEY_FILE': str(KEY_FILE)},
                                capture_output=True, text=True, errors='replace', timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not result.stdout.strip():
            raise ValueError('Cannot unlock saved OpenRouter key for this Windows account')
        return result.stdout.strip()
    return getpass.getpass('OpenRouter key (hidden; not saved): ').strip()


def payload(text, model, *, impact=False):
    if model not in MODELS or not text.strip() or len(text) > 16000:
        raise ValueError('Unsupported model or input size')
    schema, prompt, numbered, spans = numbered_request(text, ca.SCHEMA)
    schema['properties']['explanation'] = {'type': 'string', 'maxLength': 500}
    schema['required'].append('explanation')
    if impact:
        import impact_assessment as ia
        schema['properties']['impact'] = ia.SCHEMA
        schema['required'].append('impact')
        prompt += ia.PROMPT
        from company_links import add_request
        prompt = add_request(schema, prompt, text, spans)
    prompt += '\nAdditional allowed topics: oil (crude/OPEC), market_rates (Treasury yields), dollar_strength (USD/JPY, dollar index, gold demand). Allowed topic IDs: '+', '.join(TOPICS)
    fmt = {'type': 'json_object'}
    if model == MODELS[0]:
        fmt = {'type': 'json_schema', 'json_schema': {'name': 'news_assessment', 'strict': True, 'schema': schema}}
    return {'model': model, 'messages': [{'role': 'system', 'content': prompt},
            {'role': 'user', 'content': numbered}], 'response_format': fmt,
            'reasoning': {'enabled': False}, 'temperature': 0, 'max_tokens': 1600,
            'stream': False, 'provider': {'allow_fallbacks': False, 'data_collection': 'deny',
                                        'require_parameters': True}}


def parse(envelope, text, coverage, published_at, *, require_impact=False, require_companies=False):
    choice = envelope['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Incomplete response')
    raw = json.loads(choice['message']['content'])
    if not isinstance(raw.get('semantics'), dict):
        raise ValueError('Missing semantic assessment')
    warnings = []
    for field in ('topics', 'relations'):
        if field not in raw['semantics']:
            raw['semantics'][field] = []
            warnings.append('missing_'+field)
    topics = raw['semantics']['topics']
    if isinstance(topics, list) and any(isinstance(t,dict) and t.get('topic') == 'other' for t in topics):
        raw['semantics']['topics'] = [t for t in topics if not isinstance(t,dict) or t.get('topic') != 'other']
        warnings.append('unclassified_topic')
    spans = numbered_request(text, ca.SCHEMA)[3]
    companies = None
    if require_companies or 'companies' in raw:
        from company_links import validate as validate_companies
        companies = validate_companies(raw.get('companies'), text, spans)
    facts = ca.validate(resolve_spans(raw, spans), text, published_at=published_at,
                        company_symbols={r['symbol'] for r in companies['rows'] if r['decision']=='relevant'} if companies is not None else None)
    if require_impact or 'impact' in raw:
        import impact_assessment as ia
        facts['impact'] = ia.validate(raw.get('impact'), numbered_request(text, ca.SCHEMA)[3], coverage)
    if companies is not None:
        facts['companies'] = companies
    if warnings:
        facts['semantic_warnings'] = warnings
    return ca.classify(facts, coverage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, default=MODELS[1])
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--run', action='store_true', help='Send paid requests; no automatic retries')
    parser.add_argument('--save-key', action='store_true', help='Hidden key input, Windows encrypted storage; no API call by itself')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 10:
        parser.error('limit must be 1-10')
    if args.save_key:
        save_key()
    articles, _ = news.load_saved(ca.ROOT/'data/news.sqlite')
    articles = sorted(articles, key=lambda a: a['published_at'], reverse=True)[:args.limit]
    sample = [(a, *fp.evidence_input(a)) for a in articles]
    requests = [payload(text, args.model) for _, text, _ in sample]
    print(f'Model: {args.model}; requests <= {len(sample)}; output <= 1600 tokens/request; timeout 90s/request')
    for a, text, coverage in sample:
        print(f"{a['id']}: {coverage}, {len(text)} characters")
    if not args.run:
        print('OFFLINE PREVIEW: no API call, key prompt, body download or production write. Add --run after funding.')
        return 0
    if not sample:
        raise SystemExit('No saved articles')
    key = load_key()
    if not key:
        raise SystemExit('API key required')
    target = ca.ROOT/'data'/f'openrouter-pilot-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    results = []
    for (a, text, coverage), body in zip(sample, requests):
        row = {'id': a['id'], 'coverage': coverage, 'input_hash': hashlib.sha256(text.encode()).hexdigest()}
        start = time.monotonic()
        try:
            request = Request(URL, data=json.dumps(body).encode(), headers={
                'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
            with urlopen(request, timeout=90) as response:
                raw = response.read(128001)
            if len(raw) > 128000:
                raise ValueError('Response too large')
            envelope = json.loads(raw)
            row['usage'] = envelope.get('usage', {})
            row['response_model'] = envelope.get('model')
            row['result'] = parse(envelope, text, coverage, a['published_at'])
        except HTTPError as exc:
            row['error'] = f'HTTP {exc.code}'
        except Exception as exc:
            row['error'] = type(exc).__name__
        row['seconds'] = round(time.monotonic()-start, 2)
        results.append(row)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'model': args.model, 'rows': results}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{len(results)}/{len(sample)}: {row.get('error', 'complete')}; {row['seconds']}s")
        if 'error' in row:
            print('Stopped without retry or model fallback. Check funding, model support and provider availability.')
            print(target)
            return 1
    print(target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
