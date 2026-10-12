"""Single shared password; local hash, expiring server-side sessions, no dependencies."""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

AUTH_FILE = Path(__file__).resolve().parent / 'data' / 'auth.json'
ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = 'news_alarm_session'


def set_password(password, path=AUTH_FILE):
    if not 8 <= len(password) <= 256:
        raise ValueError('비밀번호는 8~256자여야 합니다.')
    salt = secrets.token_bytes(16)
    record = {'algorithm': 'pbkdf2-sha256', 'iterations': ITERATIONS, 'salt': salt.hex(),
              'hash': hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS).hex()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.auth-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(record, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cookie_token(header):
    if len(header) > 4096:
        return ''
    cookie = SimpleCookie()
    try:
        cookie.load(header)
        return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ''
    except CookieError:
        return ''


class SharedPassword:
    def __init__(self, path=AUTH_FILE):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.record = b''
        self.sessions = {}
        self.attempts = deque()
        self._reload()

    def _reload(self):
        try:
            record = self.path.read_bytes()
            if record == self.record:
                return
            config = json.loads(record)
            if config['algorithm'] != 'pbkdf2-sha256' or config['iterations'] != ITERATIONS:
                raise ValueError('Unsupported hash configuration')
            salt, digest = bytes.fromhex(config['salt']), bytes.fromhex(config['hash'])
            if len(salt) != 16 or len(digest) != 32:
                raise ValueError('Invalid password hash')
            self.salt, self.digest, self.record = salt, digest, record
            self.sessions.clear()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.sessions.clear()
            raise RuntimeError('인증 설정을 읽지 못했습니다. .\\run.ps1 set-password 로 설정하세요.') from exc

    def valid(self, token):
        with self.lock:
            self._reload()
            return bool(token and self.sessions.get(token, 0) > time.monotonic())

    def login(self, password, previous_token=''):
        with self.lock:
            self._reload()
            now = time.monotonic()
            # ponytail: one 10/minute bucket for a small shared team; use trusted edge per-IP limits at scale.
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 10:
                return '', max(1, int(60 - (now - self.attempts[0])) + 1)
            self.attempts.append(now)
            if not isinstance(password, str) or not 8 <= len(password) <= 256:
                return '', 0
            supplied = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), self.salt, ITERATIONS)
            if not hmac.compare_digest(supplied, self.digest):
                return '', 0
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            self.sessions.pop(previous_token, None)
            if len(self.sessions) >= 256:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = time.monotonic() + SESSION_SECONDS
            return token, 0

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)
