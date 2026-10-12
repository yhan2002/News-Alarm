"""Offline 429 retry bounds, diagnostics, provider hints and budget checks."""
import io
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from email.utils import format_datetime
from urllib.error import HTTPError
from unittest.mock import patch
import openrouter_worker as w

now = w.news.utcnow()
def error(header=None, status=429):
    return HTTPError('https://example.com',status,'test',
                     {'Retry-After':header} if header else {},io.BytesIO(b'{"error":"busy secret"}'))

with patch.object(w.news,'utcnow',return_value=now):
    assert w.http_error(error('bad'),'secret',1)['retry_after'] == (now+timedelta(seconds=30)).isoformat()
    assert w.http_error(error('120'),'secret',0)['retry_after'] == (now+timedelta(seconds=120)).isoformat()
    date = format_datetime((now+timedelta(seconds=120)).replace(microsecond=0),usegmt=True)
    assert w.news.timestamp(w.http_error(error(date),'secret',0)['retry_after']) >= now+timedelta(seconds=119)
    for outcomes, expected, sleeps in [([error(),error(),error(),{'ok':1}],4,[15,30,60]),
                                      ([error(),error(),error(),error()],4,[15,30,60]),
                                      ([error('120')],1,[]), ([error(status=401)],1,[])]:
        with closing(sqlite3.connect(':memory:')) as c:
            w.setup(c)
            initial = w.reserve(c,'a',now)
            record = {'status':'error','provider':'openrouter'}
            with patch.object(w,'request',side_effect=outcomes) as request, patch.object(w.time,'sleep') as sleep, patch.object(w,'enabled',return_value=True):
                try:
                    result, call_id = w.request_with_retries(c,'a',record,initial,'text','secret',w.time.monotonic()+900)
                    assert result == {'ok':1}
                except HTTPError:
                    assert isinstance(outcomes[-1],HTTPError)
                assert request.call_count == expected
                assert [x.args[0] for x in sleep.call_args_list] == sleeps
            rows = c.execute('SELECT cost,body FROM openrouter_calls').fetchall()
            assert len(rows)==expected and all(cost==w.RESERVE for cost,body in rows)
            assert all('secret' not in body for cost,body in rows if body)
            if len(outcomes)==4 and isinstance(outcomes[-1],HTTPError):
                assert record['retry_exhausted'] is True
    with closing(sqlite3.connect(':memory:')) as c:
        w.setup(c)
        initial=w.reserve(c,'a',now)
        with patch.object(w,'request',side_effect=error()) as request, patch.object(w.time,'sleep'), patch.object(w,'enabled',return_value=True), patch.dict(w.LIMITS,total=.01):
            try:w.request_with_retries(c,'a',{},initial,'text','secret',w.time.monotonic()+900)
            except HTTPError:pass
            assert request.call_count==1
print('PASS: retry delays, provider hints, bounded attempts, redaction and per-request budget')
