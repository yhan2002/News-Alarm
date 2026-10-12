r"""Focused free/offline checks: .\run.ps1 self-check"""
import copy
import http.client
import json
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from auth import SharedPassword, set_password
from demo import demo_articles
from news import (analyze, clean_url, dashboard, deduplicate, entities, iso,
                  lexicon_sentiment, load_saved, parse_feed, save_batch, macro_events, fetch_macro, fetch_feed)



def check_grail_idiom():
    def linked(title):
        return {row['symbol'] for row in entities({'title':title,'summary':''})[0]}
    for title in ["Starship Test: SpaceX's Quest for the Holy Grail of Rocketry", 'The HOLY-GRAIL of AI', 'The holy—grail of investing']:
        assert 'GRAL' not in linked(title), title
    for title in ['GRAIL, Inc. announces cancer screening results', 'Holy grail of screening: GRAIL, Inc. reports results', 'Holy grail of screening: $GRAL reports results']:
        assert 'GRAL' in linked(title), title

def check_feed_retry():
    from io import BytesIO
    from urllib.error import HTTPError
    def error(code):
        return HTTPError('https://example.com/feed', code, 'test', {}, None)
    with patch('news.urlopen', side_effect=[error(500), BytesIO(b'<rss><channel/></rss>')]) as fetch, patch('news.time.sleep') as sleep:
        assert fetch_feed('https://example.com/feed') == ([], 0)
        assert fetch.call_count == 2 and sleep.call_count == 1
    for code, attempts in [(503, 3), (404, 1)]:
        with patch('news.urlopen', side_effect=lambda *a, **k: (_ for _ in ()).throw(error(code))) as fetch, patch('news.time.sleep'):
            try:
                fetch_feed('https://example.com/feed')
                raise AssertionError('Expected HTTP error')
            except HTTPError:
                assert fetch.call_count == attempts


def run():
    check_feed_retry()
    from news import plain
    title = 'Despite Lackluster IPO, Shein’s Market Valuation Tops H&M’s'
    assert plain(title) == title
    assert plain('<b>AT&amp;T</b> announces growth') == 'AT&T announces growth'
    now = datetime(2026, 9, 10, 3, tzinfo=timezone.utc)
    regression = f'<rss><channel><item><title>{title.replace("&", "&amp;")}</title><link>https://example.com/tpr</link><pubDate>Tue, 01 Sep 2026 20:37:53 +0000</pubDate></item></channel></rss>'
    accepted, rejected = parse_feed(regression.encode(), 'TPR', now=now)
    assert rejected == 0 and accepted[0]['title'] == title
    xml = b'''<rss><channel>
    <item><title>Nvidia beats forecasts</title><description>&lt;p&gt;AI chip growth &amp;amp; strong orders.&lt;/p&gt;</description><link>https://finance.yahoo.com/news/test.html?utm_source=feed</link><pubDate>Thu, 10 Sep 2026 01:00:00 GMT</pubDate></item>
    <item><title>Missing date</title><link>https://example.com/missing</link></item>
    <item><title>Unsafe link</title><link>javascript:alert(1)</link><pubDate>Thu, 10 Sep 2026 01:00:00 GMT</pubDate></item>
    <item><title>Future article</title><link>https://example.com/future</link><pubDate>Thu, 10 Sep 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>'''
    articles, rejected = parse_feed(xml, 'AAPL', now=now)
    assert len(articles) == 1 and rejected == 3
    assert articles[0]['summary'] == 'AI chip growth & strong orders.'
    assert articles[0]['url'] == 'https://finance.yahoo.com/news/test.html'
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>CPI report</title><link rel="self" href="https://example.com/feed"/><link href="https://www.bls.gov/news.release/archives/cpi_test.htm"/>
    <content>&lt;p&gt;Consumer prices rise.&lt;/p&gt;</content><published>2026-09-09T08:30:00-04:00</published></entry>
    <entry><title>Updated is not published</title><link href="https://example.com/missing"/><updated>2026-09-09T08:30:00-04:00</updated></entry>
    <entry><title>Naive time</title><link href="https://example.com/naive"/><published>2026-09-09T08:30:00</published></entry>
    </feed>'''
    releases, rejected = parse_feed(atom, now=now)
    assert len(releases) == 1 and rejected == 2
    assert releases[0]['feed_symbols'] == [] and releases[0]['summary'] == 'Consumer prices rise.'
    assert releases[0]['published_at'] == '2026-09-09T12:30:00+00:00'
    assert entities(releases[0])[0] == [], 'Macro releases must not create stock exposure'
    for title, key in [('CPI inflation', 'CPI'), ('Producer price index', 'PPI'), ('FOMC minutes', 'FOMC'),
                       ('Nonfarm payrolls report', 'EMPLOYMENT'), ('JOLTS job openings', 'JOLTS'),
                       ('Initial jobless claims', 'CLAIMS'), ('Personal consumption expenditures', 'PCE'),
                       ('Gross domestic product', 'GDP'), ('ISM manufacturing PMI', 'PMI'), ('Retail sales', 'RETAIL')]:
        assert key in macro_events({'title': title, 'summary': ''})
    assert not macro_events({'title': 'FedEx shipping and chip equipment', 'summary': ''})
    for title, key in [('Brent crude rises on OPEC supply cuts', 'OIL'),
                       ('Treasury yields climb as bond prices fall', 'RATES'),
                       ('USD/JPY rises as the yen weakens', 'DOLLAR_STRENGTH'),
                       ('Central banks increase gold reserves', 'DOLLAR_STRENGTH')]:
        assert key in macro_events({'title': title, 'summary': ''})
    assert not macro_events({'title': 'Goldman reports software yield and $5 billion revenue', 'summary': ''})
    gold = {**releases[0], 'title': 'Central banks increase gold reserves', 'summary': ''}
    with patch('news.fetch_feed', return_value=([gold], 0)):
        related, _ = fetch_macro('yahoo-gold')
    assert len(related) == 1 and not related[0]['official_source']
    with patch('news.fetch_feed', return_value=(copy.deepcopy(releases), 0)):
        official, _ = fetch_macro('bls-cpi')
    assert official[0]['official_source'] == 'BLS' and official[0]['feed_topics'] == ['CPI']
    with patch('news.fetch_feed', return_value=(copy.deepcopy(releases), 0)):
        related, _ = fetch_macro('yahoo-market')
    assert not related[0]['official_source'], 'Publisher labels must come from the configured official feed'
    with patch('news.urlopen', side_effect=AssertionError('Invalid options must not fetch')):
        try:
            fetch_feed('https://example.com', limit=51)
            assert False, 'Invalid feed limit accepted'
        except ValueError:
            pass
    assert not clean_url('https://user:secret@example.com')
    assert not clean_url('javascript:alert(1)')
    assert clean_url('https://example.com/n?id=1&utm_campaign=x#top') == 'https://example.com/n?id=1'
    try:
        parse_feed(b'<html><body>blocked</body></html>', 'NVDA', now=now)
        assert False, 'Non-RSS response accepted'
    except ValueError:
        pass
    links, themes = entities(articles[0])
    assert [link['symbol'] for link in links] == ['NVDA'], 'Feed ticker must not become a direct link'
    assert 'AI 반도체' in themes
    no_entities = {**articles[0], 'title': 'chair repair and coin collection', 'summary': ''}
    assert entities(no_entities) == ([], {}), 'Word boundaries / ambiguous ticker regression'
    assert entities({**no_entities, 'title': '$COIN and (META) update'})[0]
    duplicate = {**articles[0], 'id': 'second', 'url': articles[0]['url'] + '?utm_source=other', 'feed_symbols': ['NVDA']}
    deduped = deduplicate(articles + [duplicate])
    assert len(deduped) == 1 and deduped[0]['feed_symbols'] == ['AAPL', 'NVDA']
    second = {**articles[0], 'id': 'other', 'title': 'Different title', 'url': 'https://example.com/second', 'feed_symbols': ['MSFT']}
    bridge = {**second, 'url': articles[0]['url'], 'feed_symbols': ['AMD']}
    assert len(deduplicate([articles[0], second, bridge])) == 1, 'Transitive duplicates must merge'
    next_release = {**articles[0], 'id': 'next-month', 'url': 'https://example.com/next-month', 'published_at': iso(now + timedelta(days=30))}
    assert len(deduplicate([articles[0], next_release])) == 2, 'Recurring releases with identical titles on different days must survive'
    combined = deduplicate([articles[0], {**second, 'feed_sources': ['yahoo-market'], 'feed_topics': ['CPI']}, bridge])
    assert combined[0]['feed_sources'] == ['yahoo-market'] and combined[0]['feed_topics'] == ['CPI']
    assert lexicon_sentiment('strong growth beats forecasts')['score'] > 0
    assert lexicon_sentiment('weak demand cuts outlook losses')['score'] < 0
    assert lexicon_sentiment('scheduled meeting')['confidence'] is None
    scored = analyze(deduped)
    mock_result = {'score': -.7, 'label': 'negative', 'engine': 'ProsusAI/finbert', 'confidence': .8, 'probabilities': {'negative': .8, 'neutral': .1, 'positive': .1}}
    assert analyze(deduped, 'finbert', lambda texts: [mock_result])[0]['sentiment']['score'] == -.7
    cached = analyze(deduped, 'finbert', lambda texts: [mock_result])
    with patch('news.load_finbert', side_effect=AssertionError('Cached text must not run FinBERT')):
        assert analyze(deduped, 'finbert', cached=cached) == cached
        assert analyze([], 'finbert') == []
    revised = {**deduped[0], 'summary': 'Revised guidance', 'feed_sources': ['yahoo-market']}
    with patch('news.load_finbert') as loader:
        loader.return_value.return_value = [mock_result]
        updated = analyze([revised], 'finbert', cached=cached)
        loader.return_value.assert_called_once_with([revised['title'] + '. Revised guidance'])
        assert updated[0]['feed_sources'] == ['yahoo-market']
    assert analyze(deduped, 'lexicon', cached=cached)[0]['sentiment']['engine'] == 'lexicon'
    assert cached[0]['summary'] == deduped[0]['summary'], 'Cached records must not be mutated'
    try:
        analyze(deduped, 'finbert', lambda texts: [])
        assert False, 'Mismatched model batch accepted'
    except ValueError:
        pass
    metadata = {'history_start': iso(now), 'last_collected': iso(now), 'errors': []}
    result = dashboard(scored, metadata, now=now)
    assert not result['baseline'] and not result['themes'], 'Unreviewed keywords must not become grounded themes'
    assert result['summary']['count'] == 1 and result['summary']['active_stocks'] == 1
    assert all(stock['count'] == 0 for stock in result['stocks'] if stock['symbol'] == 'AAPL')
    macro_result = dashboard(analyze(official), metadata, hours=720, now=now)
    assert macro_result['articles'][0]['macro_events']['CPI']
    assert next(item for item in macro_result['indicators'] if item['id'] == 'CPI')['count'] == 1
    from news import MACRO_FEEDS
    assert len(macro_result['macro_sources']) == len(MACRO_FEEDS) == 11
    assert 'macro_events' not in official[0], 'Dashboard must not mutate stored rows'
    historical = copy.deepcopy(scored[0])
    historical['published_at'] = iso(now - timedelta(hours=24))
    before = copy.deepcopy(historical)
    before['published_at'] = iso(now - timedelta(hours=24, seconds=1))
    metadata['history_start'] = iso(now - timedelta(hours=48))
    result = dashboard([historical, before], metadata, now=now)
    assert result['summary']['count'] == result['summary']['previous_count'] == 1
    assert result['summary']['momentum'] == 0
    empty_result = dashboard([], metadata, now=now)
    assert empty_result['summary']['count'] == 0 and empty_result['summary']['momentum'] is None
    synthetic, meta = demo_articles(now)
    for hours in (24, 72, 168):
        result = dashboard(synthetic, meta, hours, now, 'demo')
        assert all(-1 <= theme['sentiment'] <= 1 and 0 <= theme['intensity'] <= 100 for theme in result['themes'])
        json.dumps(result, allow_nan=False)
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / 'news.sqlite'
        assert load_saved(db)[0] == []
        assert save_batch(db, scored, [], iso(now)) == 1
        assert save_batch(db, scored, ['test warning'], iso(now + timedelta(seconds=1))) == 1
        saved, meta = load_saved(db)
        assert len(saved) == 1 and meta['errors'] == ['test warning']
        assert meta['history_start'] == iso(now)
        # save_batch now upserts duplicate IDs; inject a late SQLite failure to
        # test rollback of the complete transaction, including article writes.
        from contextlib import closing
        broken = {**scored[0], 'id': 'synthetic-rollback', 'title': 'Unrelated article',
                  'url': 'https://example.com/unrelated'}
        with closing(sqlite3.connect(db)) as conn, conn:
            before_rows = conn.execute('SELECT * FROM articles ORDER BY id').fetchall()
            before_runs = conn.execute('SELECT * FROM runs').fetchall()
            conn.execute("CREATE TRIGGER reject_fixture_run BEFORE INSERT ON runs "
                         "BEGIN SELECT RAISE(ABORT, 'fixture rollback'); END")
        try:
            save_batch(db, [broken, scored[0]], [], iso(now))
            assert False, 'Expected injected SQLite error'
        except sqlite3.IntegrityError:
            pass
        with closing(sqlite3.connect(db)) as conn, conn:
            assert conn.execute('SELECT * FROM articles ORDER BY id').fetchall() == before_rows
            assert conn.execute('SELECT * FROM runs').fetchall() == before_runs
            conn.execute('DROP TRIGGER reject_fixture_run')
        import app
        from argparse import Namespace
        options = Namespace(tickers='NVDA', limit=1, engine='lexicon', dry_run=True, macro=False, macro_only=True)
        with patch('app.fetch_macro', side_effect=AssertionError('Dry run fetched a feed')), patch('app.load_finbert', side_effect=AssertionError('Dry run loaded a model')):
            assert app.collect(options) is None
        options.dry_run = False
        with patch('app.DB', db), patch('app.MACRO_FEEDS', {'bls-cpi': None, 'fed-monetary': None}), patch('app.fetch_macro', return_value=([], 0)):
            assert app.collect(options) is None, 'No matching macro news is a successful empty collection'
            assert len(load_saved(db)[0]) == 1
        with patch('app.DB', db), patch('app.MACRO_FEEDS', {'bls-cpi': None, 'fed-monetary': None}), patch('app.fetch_macro', side_effect=[OSError('fixture outage'), (copy.deepcopy(releases), 0)]):
            assert app.collect(options) == 2
            saved_before_failure = load_saved(db)
            assert len(saved_before_failure[1]['errors']) == 1
        with patch('app.DB', db), patch('app.MACRO_FEEDS', {'bls-cpi': None}), patch('app.fetch_macro', side_effect=OSError('fixture outage')):
            try:
                app.collect(options)
                assert False, 'All failed feeds must report failure'
            except RuntimeError:
                pass
            assert load_saved(db) == saved_before_failure, 'All failed feeds must preserve data and metadata'
        auth_path = Path(temp) / 'auth.json'
        fixture_password = 'local-test-password-only'
        set_password(fixture_password, auth_path)
        assert fixture_password not in auth_path.read_text()
        password_auth = SharedPassword(auth_path)
        from app import Handler, LocalHTTPServer, public_origin
        assert public_origin('https://news.example.com/') == 'https://news.example.com'
        for invalid_origin in ('http://news.example.com', 'https://user:secret@example.com', 'https://news.example.com/path', 'https://localhost', 'https://example.com?x=1'):
            try:
                public_origin(invalid_origin)
                assert False, 'Invalid public origin accepted'
            except ValueError:
                pass
        with LocalHTTPServer(('127.0.0.1', 0), partial(Handler, db=db, auth=password_auth)) as server:
            with socket.socket() as contender:
                contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    contender.bind(server.server_address)
                except OSError:
                    pass
                else:
                    raise AssertionError('An unrelated server can steal the dashboard port')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(path, method='GET', body=None, headers=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                result = response.status, dict(response.getheaders()), response.read()
                connection.close()
                return result

            try:
                with patch('news.urlopen', side_effect=AssertionError('No external I/O in dashboard')):
                    for path in ('/api/dashboard', '/api/dashboard?mode=saved', '/app.js'):
                        status, _, body = request(path)
                        assert status == 401 and b'articles' not in body
                    assert request('/')[0] == 303
                    status, headers, _ = request('/login')
                    assert status == 200 and headers['Referrer-Policy'] == 'same-origin', 'Native login forms must retain their Origin'
                    assert request('/style.css')[0] == 200
                    assert request('/data/auth.json')[0] == 303
                    origin_headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/x-www-form-urlencoded'}
                    login_body = urlencode({'password': fixture_password})
                    assert request('/login', 'POST', login_body, {'Content-Type': 'application/x-www-form-urlencoded'})[0] == 403
                    assert request('/login', 'POST', login_body, {**origin_headers, 'Origin': 'null'})[0] == 403
                    assert request('/login', 'POST', login_body, {**origin_headers, 'Origin': 'https://untrusted.example'})[0] == 403
                    assert request('/login', 'POST', 'password=incorrect', origin_headers)[0] == 401
                    assert request('/login', 'POST', 'x' * 4097, origin_headers)[0] == 413
                    assert request('/login', 'POST', '{}', {**origin_headers, 'Content-Type': 'application/json'})[0] == 415
                    assert request('/login', 'POST', 'password=first&password=second', origin_headers)[0] == 400
                    status, headers, _ = request('/login', 'POST', login_body, origin_headers)
                    assert status == 303 and headers['Location'] == '/'
                    cookie = headers['Set-Cookie'].split(';')[0]
                    assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
                    assert headers['Referrer-Policy'] == 'same-origin'
                    assert 'Secure' not in headers['Set-Cookie'], 'Local-only preview must work over loopback HTTP'
                    for path, expected in [('/', 200), ('/app.js', 200), ('/style.css', 200), ('/api/dashboard', 200), ('/api/dashboard?mode=saved', 200), ('/api/dashboard?hours=oops', 400), ('/api/dashboard?hours=1', 400), ('/api/dashboard?mode=bad', 400), ('/../news.py', 404)]:
                        status, _, _ = request(path, headers={'Cookie': cookie})
                        assert status == expected, (path, status)
                    assert request('/', headers={'Cookie': cookie})[1]['Referrer-Policy'] == 'same-origin', 'Native logout forms must retain their Origin'
                    assert request('/logout', 'POST', '', {**origin_headers, 'Origin': 'null', 'Cookie': cookie})[0] == 403
                    assert request('/api/dashboard', headers={'Host': 'untrusted.example', 'Cookie': cookie})[0] == 403
                    assert request('/api/dashboard', headers={'Cookie': cookie + 'tampered'})[0] == 401
                    assert request('/data/auth.json', headers={'Cookie': cookie})[0] == 404
                    status, headers, _ = request('/logout', 'POST', '', {**origin_headers, 'Cookie': cookie})
                    assert status == 303 and 'Max-Age=0' in headers['Set-Cookie']
                    assert request('/api/dashboard', headers={'Cookie': cookie})[0] == 401
                    _, headers, _ = request('/login', 'POST', login_body, origin_headers)
                    cookie = headers['Set-Cookie'].split(';')[0]
                    set_password('replacement-test-password', auth_path)
                    assert request('/api/dashboard', headers={'Cookie': cookie})[0] == 401, 'Password rotation must revoke sessions'
                    clock = time.monotonic() + 120
                    with patch('auth.time.monotonic', return_value=clock):
                        token, _ = password_auth.login('replacement-test-password')
                        assert password_auth.valid(token)
                    with patch('auth.time.monotonic', return_value=clock + 8 * 3600):
                        assert not password_auth.valid(token), 'Session must expire'
                    with patch('auth.time.monotonic', return_value=clock + 8 * 3600 + 120):
                        for _ in range(10):
                            assert password_auth.login('short') == ('', 0)
                        token, retry = password_auth.login('replacement-test-password')
                        assert not token and retry > 0
                        assert request('/login', 'POST', login_body, origin_headers)[0] == 429
                    with patch('auth.time.monotonic', return_value=clock + 8 * 3600 + 181):
                        token, retry = password_auth.login('replacement-test-password')
                        assert token and retry == 0, 'Rate limit must recover'
                    auth_path.unlink()
                    assert request('/api/dashboard', headers={'Cookie': cookie})[0] == 503, 'Missing credentials must fail closed'
            finally:
                server.shutdown()
                thread.join(timeout=5)
        set_password(fixture_password, auth_path)
        with LocalHTTPServer(('127.0.0.1', 0), partial(Handler, db=db, auth=SharedPassword(auth_path), public_url='https://news.example.com')) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, _ = request('/login', 'POST', login_body, {'Origin': 'https://news.example.com', 'Host': 'news.example.com', 'Content-Type': 'application/x-www-form-urlencoded'})
                assert status == 303 and 'Secure' in headers['Set-Cookie']
                assert request('/login', 'POST', login_body, {'Origin': 'http://news.example.com', 'Host': 'news.example.com', 'Content-Type': 'application/x-www-form-urlencoded'})[0] == 403
            finally:
                server.shutdown()
                thread.join(timeout=5)
    print('PASS: news pipeline, SQLite rollback, exclusive port binding, password auth, protected API, session expiry/revocation, CSRF, throttling, Secure cookies. No external API/model calls.')


if __name__ == '__main__':
    check_grail_idiom()
    run()
