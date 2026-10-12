"""Offline checks only."""
import json
from unittest.mock import patch
import openrouter_pilot as p

text = 'A company announced a contract today.'
article = {'id': 'test', 'title': text, 'summary': '', 'url': 'https://example.com/news', 'published_at': '2026-09-16T00:00:00+00:00'}
for model in p.MODELS:
    request = p.payload(text, model)
    assert request['model'] == model and request['max_tokens'] == 1600
    assert request['provider']['data_collection'] == 'deny'
    assert not request['provider']['allow_fallbacks'] and not request['reasoning']['enabled']
fields = {'certainty': 'confirmed', 'novelty': 'new', 'event': 'contract',
          'evidence': [0], 'relative_size': None, 'semantics': {'topics': [], 'relations': []}}
envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(fields)}}]}
assert p.parse(envelope, text, '제목·RSS 요약', article['published_at'])['event'] == 'contract'
envelope['choices'][0]['finish_reason'] = 'length'
try:
    p.parse(envelope, text, '제목·RSS 요약', article['published_at'])
    raise AssertionError('Accepted truncated result')
except ValueError:
    pass
with patch.object(p.news, 'load_saved', return_value=([article], {})), \
     patch.object(p, 'urlopen', side_effect=AssertionError('Network forbidden')), \
     patch.object(p.getpass, 'getpass', side_effect=AssertionError('Key prompt forbidden')):
    assert p.main([]) == 0
print('PASS: bounded payload, source validation, truncated response rejection, no-network default')
