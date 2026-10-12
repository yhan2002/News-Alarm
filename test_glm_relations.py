"""Offline retry check: same article, no real network or waits."""
import io
from contextlib import closing
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import glm_relations as g

with tempfile.TemporaryDirectory() as tmp:
    a={'title':'Example article','summary':'Summary','published_at':g.news.utcnow().isoformat()}
    error=lambda: HTTPError(g.glm.URL,429,'busy',{},io.BytesIO(b'{"error":{"code":"1305"}}'))
    with patch.object(g,'CACHE',Path(tmp)/'cache.sqlite'), patch.object(g.news,'load_saved',return_value=([a],{})), patch.object(g.fp.ca,'fingerprint',return_value='article'), patch.object(g.fp,'evidence_input',return_value=('same text','RSS')), patch.object(g.glm,'parse',return_value={'validated':{'semantics':{}}}), patch.object(g,'urlopen',side_effect=[error(),error(),io.BytesIO(b'{}')]) as requests, patch.object(g.time,'sleep') as sleep, patch.object(g,'report'), patch.dict(g.os.environ,{'ZAI_API_KEY':'test-only'}), patch('sys.argv',['glm_relations.py','--run']):
        g.main()
        assert [c.args[0] for c in sleep.call_args_list]==[30,35]
        assert len({c.args[0].data for c in requests.call_args_list})==1
        with closing(g.sqlite3.connect(g.CACHE)) as conn:
            row=json.loads(conn.execute('SELECT body FROM first_pass').fetchone()[0])
            assert row['status']=='done' and row['attempts']==3
print('Same-article retry and persisted success passed; no API calls.')
