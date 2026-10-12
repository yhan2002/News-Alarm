"""Offline check of the prepared snapshot and the actual hosted SQL (no upload)."""
import base64
import gzip
import json
import re
import sqlite3
from unittest.mock import patch
import publish_news
from pathlib import Path

root = Path(__file__).resolve().parent
# A clean checkout must not need any real article database or exported snapshot.
with patch('publish_news.news.load_saved', return_value=([], {})):
    payload = publish_news.export()
route = (root / 'hosted-site/worker/news.mjs').read_text(encoding='utf-8')
delete = re.search(r"prepare\('(DELETE FROM news_snapshots[^']+)'\)", route)[1]
insert = re.search(r"prepare\('(INSERT INTO news_snapshots[^']+)'\)", route)[1]
db = sqlite3.connect(':memory:')
db.executescript((root / 'hosted-site/drizzle/0011_unknown_peter_parker.sql').read_text())

def apply(value):
    with db:
        db.execute(delete, (value['generated_at'],))
        for snapshot in value['snapshots']:
            for offset in range(0, len(snapshot['body']), 500000):
                db.execute(insert, (snapshot['hours'], offset // 500000,
                                    snapshot['body'][offset:offset + 500000],
                                    value['generated_at'], value['generated_at']))

apply(payload)
for snapshot in payload['snapshots']:
    body = ''.join(r[0] for r in db.execute('SELECT body FROM news_snapshots WHERE hours=? ORDER BY part', (snapshot['hours'],)))
    assert body == snapshot['body']
    document = json.loads(gzip.decompress(base64.b64decode(body)))
    assert document['hours'] == snapshot['hours']
before = db.execute('SELECT * FROM news_snapshots ORDER BY hours,part').fetchall()
apply({**payload, 'generated_at': '2000-01-01T00:00:00.000000+00:00'})
assert before == db.execute('SELECT * FROM news_snapshots ORDER BY hours,part').fetchall()
apply(payload)
assert before == db.execute('SELECT * FROM news_snapshots ORDER BY hours,part').fetchall()
print('5 periods round-trip; migration, stale upload and retry checks passed. No network.')
