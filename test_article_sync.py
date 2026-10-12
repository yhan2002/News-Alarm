"""No network/models: existing timestamps, compact catalogs and restartable sync."""
import contextlib
import copy
import json
import sqlite3
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import article_sync as s
import news

now=datetime(2026,9,16,9,tzinfo=timezone.utc)
a={'id':'a','title':'Company update','summary':'Facts','url':'https://example.com/a',
   'published_at':now.isoformat(),'feed_symbols':[],'sentiment':{},'qwen':{'status':'done','at':now.isoformat()}}
def dashboard(articles,meta,hours,now,mode):
 return {'hours':hours,'as_of':now.isoformat(),'articles':copy.deepcopy(articles),'issues':[{'id':'g','article_ids':['a']}],'summary':{'count':len(articles)}}
with patch.object(news,'dashboard',side_effect=dashboard):
 rows,catalogs=s.prepare([a],{},now)
 assert rows[0]['first_collected_at'] is None
 assert rows[0]['analysis_updated_at']==now.isoformat()
 assert len(json.loads(rows[0]['body'])['issue_groups'])==5
 assert all('articles' not in json.loads(c['body']) and 'issues' not in json.loads(c['body']) for c in catalogs)
 assert max(len(b) for b in s.batches(rows*101))<=50
 with tempfile.TemporaryDirectory() as tmp,patch.object(s,'STATE',Path(tmp)/'sync.sqlite'),patch.object(news,'load_saved',return_value=([a],{})),patch.object(news,'utcnow',return_value=now):
  sent=[]
  def exchange(config,payload=None):
   if payload is None:return {'protocol':3,'removals':True}
   sent.append(payload);return {'ok':True}
  with patch.object(s,'exchange',side_effect=exchange):
   s.synchronize({})
   assert sum(len(v['articles']) for v in sent)==1
   sent.clear();s.synchronize({});assert sum(len(v['articles']) for v in sent)==0
   # Protocol migration must resend existing rows and preserve retired IDs.
   with contextlib.closing(sqlite3.connect(s.STATE)) as conn:
    conn.execute('DELETE FROM sync_protocol')
    conn.execute('INSERT INTO synced VALUES (?,?)',('legacy-retired','old'));conn.commit()
   sent.clear();s.synchronize({})
   assert sum(len(v['articles']) for v in sent)==1
   assert sent[-1]['removed_ids']==['legacy-retired']
   changed=dict(a,summary='Updated source facts')
   with patch.object(news,'load_saved',return_value=([changed],{})):
    sent.clear();s.synchronize({});assert sum(len(v['articles']) for v in sent)==1
  # A failed HTTP batch must not advance the local acknowledgement.
  def failure(config,payload=None):
   if payload is None:return {'protocol':3}
   raise TimeoutError()
  with patch.object(s,'exchange',side_effect=failure):
   try:s.synchronize({});raise AssertionError('Timeout ignored')
   except TimeoutError:pass
  with patch.object(s,'exchange',side_effect=exchange):
   sent.clear();s.synchronize({});assert sum(len(v['articles']) for v in sent)==1
  with contextlib.closing(sqlite3.connect(s.STATE)) as conn:
   conn.execute('INSERT INTO synced VALUES (?,?)',('retired','old'));conn.commit()
  def catalog_failure(config,payload=None):
   if payload is None:return {'protocol':3,'removals':True}
   if 'catalogs' in payload:raise TimeoutError()
   return {'ok':True}
  with patch.object(s,'exchange',side_effect=catalog_failure):
   try:s.synchronize({});raise AssertionError('Catalog failure ignored')
   except TimeoutError:pass
  with contextlib.closing(sqlite3.connect(s.STATE)) as conn:
   assert conn.execute('SELECT id FROM synced WHERE id=?',('retired',)).fetchone()
  with patch.object(s,'exchange',side_effect=exchange):
   sent.clear();s.synchronize({})
   assert sent[-1]['removed_ids']==['retired']
  with contextlib.closing(sqlite3.connect(s.STATE)) as conn:
   assert not conn.execute('SELECT id FROM synced WHERE id=?',('retired',)).fetchone()
 # A capped run must finish its frozen catalog even when live source data changes.
 with tempfile.TemporaryDirectory() as tmp,patch.object(s,'STATE',Path(tmp)/'sync.sqlite'),patch.object(news,'utcnow',return_value=now):
  original=[a,dict(a,id='b')]
  sent=[]
  with patch.object(s,'exchange',side_effect=exchange):
   with patch.object(news,'load_saved',return_value=(original,{})):
    try:s.synchronize({},limit=1);raise AssertionError('Cap ignored')
    except RuntimeError as exc:assert 'Bound reached' in str(exc)
   assert not any('catalogs' in v for v in sent)
   with patch.object(news,'load_saved',side_effect=AssertionError('Must resume frozen plan')):
    s.synchronize({},limit=1)
   assert [r['id'] for v in sent for r in v['articles']]==['a','b']
   assert json.loads(sent[-1]['catalogs'][0]['body'])['summary']['count']==2
   with contextlib.closing(sqlite3.connect(s.STATE)) as conn:
    assert conn.execute('SELECT COUNT(*) FROM pending_sync').fetchone()[0]==0
with tempfile.TemporaryDirectory() as tmp:
 db=Path(tmp)/'news.sqlite'
 news.save_batch(db,[copy.deepcopy(a)],[],now.isoformat())
 later='2026-09-17T09:00:00+00:00'
 news.save_batch(db,[copy.deepcopy(a)],[],later)
 with contextlib.closing(sqlite3.connect(db)) as c:
  stored=json.loads(c.execute('SELECT body FROM articles').fetchone()[0]);assert stored['first_collected_at']==now.isoformat()
  stored.pop('first_collected_at');c.execute('UPDATE articles SET body=?',(json.dumps(stored),));c.commit()
 news.save_batch(db,[copy.deepcopy(a)],[],later)
 with contextlib.closing(sqlite3.connect(db)) as c:
  assert json.loads(c.execute('SELECT body FROM articles').fetchone()[0])['first_collected_at'] is None
print('PASS: timestamp preservation, unknown legacy time, no duplicated period articles, incremental ack and safe retry.')
