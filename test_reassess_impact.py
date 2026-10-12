"""Historical opt-in preserves originals and does not change the normal queue."""
import contextlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import first_pass as fp
import openrouter_worker as w

now = w.news.utcnow()
a = {'id':'old','title':'Alpha announced an agreement with Beta.', 'summary':'',
     'url':'https://example.com/a','published_at':now.isoformat()}
key = fp.ca.fingerprint(a)
previous = {'version':fp.VERSION,'provider':'openrouter','model':w.MODEL,'status':'done',
            'coverage':'제목·RSS 요약','facts':{'event':'contract'},'attempts':1,
            'text_hash':fp.ca.hashlib.sha256((a['published_at']+'\n'+a['title']+'\n').encode()).hexdigest()}
with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
    db = Path(folder)/'test.sqlite'
    secret = Path(folder)/'fake-key'
    secret.touch()
    for obj, name, value in [(w.api,'KEY_FILE',secret),(w,'enabled',lambda:True),
                             (w,'config',lambda:{'enabled':True}), (w.api,'load_key',lambda:'fake')]:
        stack.enter_context(patch.object(obj,name,value))
    stack.enter_context(patch.object(w,'body_input',return_value=(a['title']+'\n','제목·RSS 요약')))
    stack.enter_context(patch.object(w.api,'parse',return_value={'impact':{'version':1,'score':70}}))
    request = stack.enter_context(patch.object(w,'request',return_value={'usage':{'cost':.0001}}))
    stack.enter_context(patch.object(fp,'same_input',return_value=True))
    with contextlib.closing(sqlite3.connect(db)) as c, c:
        w.setup(c)
        c.execute('INSERT INTO openrouter_excluded VALUES (?)',(a['id'],))
        c.execute('INSERT INTO first_pass VALUES (?,?)',(key,json.dumps(previous)))
    assert w.run([a],now,path=db,execute=True)==0
    request.assert_not_called()
    w.run([a],now,path=db,execute=True,reassess=True)
    assert request.call_count==1
    with contextlib.closing(sqlite3.connect(db)) as c:
        assert json.loads(c.execute('SELECT body FROM impact_history').fetchone()[0])==previous
    assert fp.records(db)[key]['facts']['impact']['score']==70
    w.run([a],now,path=db,execute=True,reassess=True)
    assert request.call_count==1
print('PASS: historical opt-in, archive and reuse')
