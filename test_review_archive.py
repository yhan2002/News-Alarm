import json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import news

class ArchiveReviewTest(unittest.TestCase):
    def test_unchanged_rows_not_rewritten_and_bridged_duplicates_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'news.sqlite'
            def article(id,title,url):return dict(id=id,title=title,url=url,published_at='2026-09-01T12:00:00+00:00',feed_symbols=[id],summary='facts')
            a=article('a','one','https://example.test/a');b=article('b','two','https://example.test/b')
            news.save_batch(path,[a,b],[],'2026-09-01T13:00:00+00:00')
            with closing(sqlite3.connect(path)) as c, c:
                c.executescript("CREATE TABLE audit(kind TEXT,id TEXT); CREATE TRIGGER au AFTER UPDATE ON articles BEGIN INSERT INTO audit VALUES ('update',new.id); END; CREATE TRIGGER ad AFTER DELETE ON articles BEGIN INSERT INTO audit VALUES ('delete',old.id); END; CREATE TRIGGER ai AFTER INSERT ON articles BEGIN INSERT INTO audit VALUES ('insert',new.id); END;")
            news.save_batch(path,[a],[],'2026-09-01T14:00:00+00:00')
            with closing(sqlite3.connect(path)) as c, c:self.assertEqual(c.execute('SELECT * FROM audit').fetchall(),[])
            bridge=article('a','two','https://example.test/a')
            news.save_batch(path,[bridge],[],'2026-09-01T15:00:00+00:00')
            with closing(sqlite3.connect(path)) as c, c:
                self.assertEqual(c.execute('SELECT id FROM articles').fetchall(),[('a',)])
                self.assertEqual(set(c.execute('SELECT * FROM audit')),{('update','a'),('delete','b')})
                body=json.loads(c.execute('SELECT body FROM articles').fetchone()[0])
                self.assertEqual(body['first_collected_at'],'2026-09-01T13:00:00+00:00')
                self.assertEqual(body['feed_symbols'],['a','b'])
