"""Free/offline evaluation-runner check: no credential access or API traffic."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import evaluate_openrouter as e

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    (root/'data').mkdir()
    text = 'A company announced a contract today.'
    sample = [{'id':'test', 'title':text, 'input':text, 'coverage':'제목·RSS 요약',
               'published_at':'2026-09-16T00:00:00+00:00', 'baseline':{'text_hash':'test'}}]
    (root/'data/openrouter-eval-input.json').write_text(json.dumps(sample), encoding='utf-8')
    fields = {'certainty':'confirmed', 'novelty':'new', 'event':'contract', 'evidence':[0],
              'relative_size':None, 'semantics':{'topics':[], 'relations':[]}}
    response = {'usage':{'cost':.0001}, 'model':e.p.MODELS[1], 'choices':[
        {'finish_reason':'stop', 'message':{'content':json.dumps(fields)}}]}
    with patch.object(e.p.ca,'ROOT',root), patch.object(e.p,'load_key',return_value='fake') as key, \
         patch.object(e,'urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(response).encode())) as send, \
         patch.object(e.time,'sleep'), patch('sys.argv',['evaluate_openrouter.py']):
        e.main()
        assert not key.called and not send.called
        with patch('sys.argv',['evaluate_openrouter.py','--run']):
            e.main()
        assert send.call_count == 1
        result = next(p for p in (root/'data').glob('openrouter-eval-*.json') if p.name != 'openrouter-eval-input.json')
        assert json.loads(result.read_text(encoding='utf-8'))['cost_usd'] == .0001
        with patch('sys.argv',['evaluate_openrouter.py','--run','--resume',str(result)]):
            e.main()
        assert send.call_count == 1, 'Resume must reuse completed results'
print('PASS: preview, mocked paid run, usage accounting, resume without repeated calls')
