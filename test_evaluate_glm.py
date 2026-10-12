"""Offline pilot checks; never sends an API request."""
import json
import evaluate_glm as e


def main():
    text = 'A company announced a contract today.'
    payload = e.payload(text)
    assert payload['model'] == 'glm-4.7-flash'
    assert payload['thinking'] == {'type': 'disabled'} and 'tools' not in payload
    fields = {'certainty': 'confirmed', 'novelty': 'new', 'event': 'contract',
              'evidence': [0], 'relative_size': None, 'semantics': {'topics': [], 'relations': []}}
    envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(fields)}}]}
    result = e.parse(envelope, text)
    assert result['validated']['event'] == 'contract' and result['raw_fields']['evidence'] == [0]
    envelope['choices'][0]['finish_reason'] = 'length'
    try:
        e.parse(envelope, text)
        raise AssertionError('Accepted truncated output')
    except ValueError:
        pass
    print('Offline GLM checks passed')


if __name__ == '__main__':
    main()
