"""Offline checks: batching, cached skips, and today's Telegram suppression."""
import contextlib
import io
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import first_pass as f
import daily_schedule as d
import subprocess

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp)/'qwen.sqlite'
    now = datetime.now(timezone.utc)
    articles = [{'id':str(i),'title':f'Article {i}','summary':'Facts',
                 'url':f'https://example.com/{i}','published_at':now.isoformat()} for i in range(101)]
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"models":[{"name":"qwen3.5:9b"}]}'
    opener = MagicMock()
    opener.open.return_value = response
    published = []
    import json, sqlite3
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE first_pass (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        rows = [('done', 1), ('error', 3), ('error', 1)]
        for a, (status, attempts) in zip(articles, rows):
            conn.execute('INSERT INTO first_pass VALUES (?,?)', (f.ca.fingerprint(a), json.dumps({
                'version':f.VERSION, 'model':f.MODEL, 'coverage':'제목·RSS 요약', 'text_hash':f.ca.hashlib.sha256((a['published_at']+'\n'+a['title']+'\nFacts').encode()).hexdigest(), 'status':status, 'attempts':attempts,
                'retry_after':(now+timedelta(hours=1)).isoformat()})))
    assert f.progress(articles[:4], now, path) == {'done':1,'pending':1,'retry_wait':1,'exhausted':1}
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM first_pass')
    with patch.object(f,'build_opener',return_value=opener), patch.object(f.ca,'evaluate',return_value={'certainty':'unknown','novelty':'unknown','event':'other','evidence':[],'relative_size':None,'semantics':{'version':2,'topics':[],'relations':[],'withheld':0}}) as evaluate, patch.object(f.ca,'validate',return_value={}), contextlib.redirect_stdout(io.StringIO()):
        f.run(articles,now,path=path,limit=1000,seconds=1800,execute=True,on_batch=lambda:published.append(len(f.records(path))))
        assert published == [100,101]
        assert all(r['status']=='done' and 'semantics' in r['facts'] for r in f.records(path).values())
        f.run(articles,now,path=path,limit=1000,execute=True,on_batch=lambda:published.append(-1))
        assert evaluate.call_count == 101 and published == [100,101]
    with patch.object(d,'DATA',Path(tmp)), patch.object(d,'request') as request:
        (Path(tmp)/'telegram-skip-date.txt').write_text(datetime.now(d.KST).date().isoformat())
        assert d.telegram_paused()
        assert not d.telegram_paused(datetime.now(d.KST).date()+timedelta(days=1))
        try:
            d.telegram('sendMessage',{},'not-a-token')
            raise AssertionError('Pause bypassed')
        except RuntimeError:
            pass
        request.assert_not_called()
    import publish_news
    config = Path(tmp)/'publish.json'
    config.write_text('{"enabled":true}')
    with patch.object(publish_news,'CONFIG',config), patch.object(f.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as launch, contextlib.redirect_stdout(io.StringIO()):
        f.publish_batch()
        assert launch.call_args.args[0][-1] == '--upload'
        assert launch.call_args.args[0][0] == f.sys.executable
        assert launch.call_args.kwargs['timeout'] == 300
print('100-item publishing, final partial batch, cached skip, day-scoped Telegram pause passed. No network/model calls.')
