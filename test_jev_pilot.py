"""Offline guards: sentiment retention, conservative routing, no paid calls."""
import copy
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import jev_pilot as j


def main():
    article = {'id': 'example', 'title': 'Walmart price war', 'published_at': '2026-09-23T00:00:00Z',
               'summary': 'Retail competition', 'sentiment': {'score': -.31, 'engine': 'ProsusAI/finbert'}}
    original = copy.deepcopy(article)
    with patch.object(j.p.fp, 'evidence_input', return_value=('Retail competition', '본문 추출')), \
         patch.object(j.p.news, 'entities', return_value=([], [])):
        body = j.payload(article)
        for sentiment in ({}, {'score': 0, 'engine': 'lexicon'}):
            try:
                j.payload({**article, 'sentiment': sentiment})
                raise AssertionError('Missing FinBERT score accepted')
            except ValueError:
                pass
        env = {'answers': {k: {'type': 'noul', 'noul': 0.0} for k in body['questions']}, 'usage': {'cost': .0001}}
        env['answers']['routine']['noul'] = .99
        assert j.route(env, body, 'example')['suggested_route'] == 'defer_body'
        env['answers']['material']['noul'] = .5
        assert j.route(env, body, 'example')['suggested_route'] == 'qwen'
        env['answers']['material']['noul'] = 0
        body['state']['coverage'] = '제목·RSS 요약'
        assert j.route(env, body, 'example')['suggested_route'] == 'qwen'
        for bad in (float('nan'), True, 2, None):
            broken = copy.deepcopy(env)
            broken['answers']['routine']['noul'] = bad
            try:
                j.route(broken, body, 'example')
                raise AssertionError('Invalid probabilities accepted')
            except ValueError:
                pass
        with tempfile.TemporaryDirectory() as folder:
            snapshot, output = Path(folder)/'snapshot.json', Path(folder)/'result.json'
            snapshot.write_text(json.dumps({'example': article}), encoding='utf-8')
            args = ['--snapshot', str(snapshot), '--ids', 'example', '--output', str(output)]
            with patch.object(j.p, 'load_key', return_value='fake') as key, \
                 patch.object(j, 'urlopen', return_value=io.BytesIO(json.dumps(env).encode())) as call:
                assert j.main(args) == 0
                assert not call.called and not key.called and not output.exists()
                assert j.main(args+['--run']) == 0
                assert json.loads(output.read_text(encoding='utf-8'))['rows'][0]['sentiment'] == article['sentiment']
                try:
                    j.main(args+['--run'])
                    raise AssertionError('Repeated run accepted')
                except FileExistsError:
                    pass
                assert call.call_count == 1
        assert article == original
    print('PASS: offline preview, mock API, sentiment retention, routing and repeat-spend guard')


if __name__ == '__main__':
    main()
