"""Local-only dashboard. Reading the UI never fetches news or downloads a model."""
import argparse
import getpass
import html
import json
import socket
import sqlite3
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from auth import COOKIE_NAME, SESSION_SECONDS, SharedPassword, cookie_token, set_password
from demo import demo_articles
from news import (ROOT, MODEL_DIR, MODEL_ID, UNIVERSE, MACRO_FEEDS, analyze, dashboard, deduplicate,
                  fetch_yahoo, fetch_macro, iso, load_finbert, load_saved, save_batch, utcnow)

DB = ROOT / 'data' / 'news.sqlite'
STATIC = ROOT / 'web'


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR otherwise lets unrelated servers share one port.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, db=DB, auth=None, public_url='', **kwargs):
        self.db = db
        self.auth = auth
        self.public_url = public_url
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_request(self, code='-', size='-'):
        # Never log request targets, query strings, cookies, or submitted credentials.
        self.log_message('%s %s', self.command, code)

    def reply(self, status, content, mime='application/json; charset=utf-8', headers=None):
        payload = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        # no-referrer makes native form POSTs send Origin: null, breaking CSRF checks.
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def valid_host(self):
        # Exact host check also prevents DNS rebinding into this local data endpoint.
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.public_url:
            allowed.add(urlsplit(self.public_url).netloc)
        return len(self.headers.get_all('Host', [])) == 1 and self.headers.get('Host') in allowed

    def login_page(self, message='', status=200, headers=None):
        page = (STATIC / 'login.html').read_text(encoding='utf-8')
        self.reply(status, page.replace('{{message}}', html.escape(message)), 'text/html; charset=utf-8', headers)

    def session_cookie(self, token):
        secure = '; Secure' if self.public_url else ''
        age = SESSION_SECONDS if token else 0
        return f'{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, '{"error":"Invalid host"}')
        request = urlsplit(self.path)
        if request.path == '/login':
            return self.login_page()
        if request.path == '/style.css':
            return self.reply(200, (STATIC / 'style.css').read_bytes(), 'text/css; charset=utf-8')
        try:
            authenticated = self.auth is not None and self.auth.valid(cookie_token(self.headers.get('Cookie', '')))
        except RuntimeError:
            return self.reply(503, '{"error":"Authentication unavailable"}')
        if not authenticated:
            if request.path.startswith('/api/') or request.path.endswith('.js'):
                return self.reply(401, '{"error":"Login required"}')
            return self.reply(303, '', headers={'Location': '/login'})
        if request.path == '/api/dashboard':
            try:
                query = parse_qs(request.query)
                mode, hours = query.get('mode', ['demo'])[0], int(query.get('hours', ['24'])[0])
                if mode not in ('demo', 'saved'):
                    raise ValueError('올바른 데이터 모드를 선택하세요')
                now = utcnow()
                articles, metadata = demo_articles(now) if mode == 'demo' else load_saved(self.db)
                data = dashboard(articles, metadata, hours, now, mode)
                return self.reply(200, json.dumps(data, ensure_ascii=False, allow_nan=False))
            except ValueError as exc:
                return self.reply(400, json.dumps({'error': str(exc)}, ensure_ascii=False))
            except (OSError, sqlite3.Error, KeyError):
                return self.reply(500, json.dumps({'error': '저장 데이터를 읽지 못했습니다. data/news.sqlite 파일과 터미널을 확인하세요.'}, ensure_ascii=False))
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/time-data.js': ('time-data.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        files.update({'/journal.js': ('journal.js', 'text/javascript; charset=utf-8'), '/journal.css': ('journal.css', 'text/css; charset=utf-8')})
        if request.path not in files:
            return self.reply(404, '{"error":"Not found"}')
        filename, mime = files[request.path]
        self.reply(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, '{"error":"Invalid host"}')
        origin = self.public_url or 'http://' + self.headers['Host']
        if self.headers.get_all('Origin', []) != [origin]:
            return self.reply(403, '{"error":"Invalid origin"}')
        if self.path not in ('/login', '/logout'):
            return self.reply(404, '{"error":"Not found"}')
        if self.auth is None:
            return self.reply(503, '{"error":"Authentication unavailable"}')
        token = cookie_token(self.headers.get('Cookie', ''))
        if self.path == '/logout':
            self.auth.logout(token)
            return self.reply(303, '', headers={'Location': '/login', 'Set-Cookie': self.session_cookie('')})
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded' or self.headers.get('Transfer-Encoding'):
            return self.reply(415, '{"error":"Unsupported body"}')
        try:
            if len(self.headers.get_all('Content-Length', [])) != 1:
                raise ValueError('Ambiguous body length')
            length = int(self.headers['Content-Length'])
            if not 1 <= length <= 4096:
                return self.reply(413, '{"error":"Body too large or empty"}')
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete body')
            values = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=2)
            if set(values) != {'password'} or len(values['password']) != 1:
                raise ValueError('Invalid form')
            session, retry = self.auth.login(values['password'][0], token)
        except (ValueError, UnicodeError):
            return self.login_page('비밀번호 입력을 확인해 주세요.', 400)
        except RuntimeError:
            return self.reply(503, '{"error":"Authentication unavailable"}')
        if retry:
            return self.login_page(f'로그인 시도가 많습니다. {retry}초 후 다시 시도해 주세요.', 429, {'Retry-After': str(retry)})
        if not session:
            return self.login_page('비밀번호가 올바르지 않습니다.', 401)
        self.reply(303, '', headers={'Location': '/', 'Set-Cookie': self.session_cookie(session)})


def public_origin(value):
    if not value:
        return ''
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/') or url.port not in (None, 443)
            or url.hostname in ('localhost', '127.0.0.1', '::1')
            or any(char.isspace() for char in value)):
        raise ValueError('--public-url 은 경로 없는 외부 HTTPS 주소여야 합니다.')
    return 'https://' + url.hostname.lower()


def collect(args):
    include_macro = args.macro or args.macro_only
    symbols = [] if args.macro_only else list(UNIVERSE) if args.tickers.upper() == 'CSQ_UNIV' else list(dict.fromkeys(s.strip().upper() for s in args.tickers.split(',') if s.strip()))
    invalid = set(symbols) - UNIVERSE.keys()
    if (not symbols and not include_macro) or invalid:
        raise ValueError('지원 종목: ' + ', '.join(UNIVERSE))
    if not 1 <= args.limit <= 50:
        raise ValueError('--limit 은 1~50 범위입니다')
    sources = [(symbol, partial(fetch_yahoo, symbol)) for symbol in symbols]
    if include_macro:
        sources.extend((name, partial(fetch_macro, name)) for name in MACRO_FEEDS)
    print(f'대상 {len(symbols)}종목 + 지표 {len(sources) - len(symbols)}피드 / 최대 {len(sources) * args.limit}기사 / RSS·Atom {len(sources)}회 / 유료 API 없음')
    if args.dry_run:
        print('Dry run: 수집·모델 로드·저장을 실행하지 않았습니다.')
        return
    # Fail before network I/O when FinBERT is not installed or downloaded.
    scorer = load_finbert() if args.engine == 'finbert' else None
    articles, errors, succeeded = [], [], 0
    for source, fetcher in sources:
        try:
            batch, rejected = fetcher(args.limit)
            articles.extend(batch)
            print(f'{source}: {len(batch)}건, 제외 {rejected}건')
            if rejected:
                errors.append(f'{source}: 제목·날짜·링크 검증에서 {rejected}건 제외')
            if not batch and source not in MACRO_FEEDS:
                errors.append(f'{source}: 유효한 RSS 기사가 없습니다')
            elif batch or not rejected:
                succeeded += 1
        except Exception as exc:
            errors.append(f'{source}: {type(exc).__name__}: {exc}')
            print(errors[-1], file=sys.stderr)
    if not articles and not succeeded:
        raise RuntimeError('수집된 기사가 없습니다. 기존 저장 데이터는 유지됩니다.')
    unique = deduplicate(articles)
    saved, _ = load_saved(DB)
    analyzed = analyze(unique, args.engine, scorer, cached=saved) if unique else []
    total = save_batch(DB, analyzed, errors)
    print(f'중복 제거 후 {len(unique)}건 처리 (동일 제목·요약·엔진은 분석 재사용) / 누적 {total}건 저장 / 엔진 {args.engine}')
    if errors:
        print('일부 수집 오류가 있습니다. 대시보드의 상태와 위 로그를 확인하세요.', file=sys.stderr)
        return 2


def download_model():
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    print('ProsusAI/finbert 모델을 내려받습니다. 가중치 약 440 MB, 모델 API 비용 없음.')
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    tokenizer.save_pretrained(MODEL_DIR)
    model.save_pretrained(MODEL_DIR)
    print(f'저장 완료: {MODEL_DIR}')


def main():
    parser = argparse.ArgumentParser(description='News Alarm · US equity news research')
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve', help='오프라인 대시보드 실행')
    server.add_argument('--port', type=int, default=8873)
    server.add_argument('--public-url', default='', help='외부 HTTPS 주소. 지정 시 Secure 세션 쿠키 사용')
    fetch = commands.add_parser('collect', help='Yahoo RSS 수집 후 로컬 분석')
    fetch.add_argument('--tickers', default='CSQ_UNIV', help='기본: CSQ_UNIV 전체 또는 쉼표로 구분한 티커')
    fetch.add_argument('--limit', type=int, default=20)
    fetch.add_argument('--engine', choices=['finbert', 'lexicon'], default='finbert')
    fetch.add_argument('--dry-run', action='store_true')
    fetch.add_argument('--macro', action='store_true', help='BLS·연준 발표와 Yahoo 시장 지표 뉴스 추가')
    fetch.add_argument('--macro-only', action='store_true', help='종목 피드 없이 주요 지표 뉴스만 수집')
    commands.add_parser('download-model', help='FinBERT 모델 최초 다운로드')
    commands.add_parser('self-check', help='외부 통신 없는 집중 검증')
    commands.add_parser('set-password', help='공유 비밀번호 설정/변경 (입력은 화면에 표시되지 않음)')
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            if not 1024 <= args.port <= 65535:
                raise ValueError('포트는 1024~65535 범위입니다')
            external = public_origin(args.public_url)
            password_auth = SharedPassword()
            with LocalHTTPServer(('127.0.0.1', args.port), partial(Handler, db=DB, auth=password_auth, public_url=external)) as httpd:
                print(f'News Alarm: http://127.0.0.1:{args.port} (Ctrl+C 종료)', flush=True)
                if external:
                    print(f'외부 주소: {external} (HTTPS 터널을 별도로 연결해야 합니다)', flush=True)
                httpd.serve_forever()
        elif args.command == 'collect':
            return collect(args)
        elif args.command == 'download-model':
            download_model()
        elif args.command == 'set-password':
            password = getpass.getpass('공유 비밀번호: ')
            if password != getpass.getpass('한 번 더 입력: '):
                raise ValueError('비밀번호가 일치하지 않습니다.')
            set_password(password)
            print('비밀번호를 저장했습니다. 기존 세션은 다음 요청부터 해제됩니다.')
        else:
            import test_news
            test_news.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
