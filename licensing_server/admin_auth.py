"""Private administrator credentials and short-lived in-memory browser sessions."""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from desktop_licensing.protocol import strict_json
from .config import ConfigError, private_path, write_private_json


SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 5


class AdminError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def validate_origin(origin, *, allow_local_http=False):
    try:
        parsed = urlsplit(origin)
        local = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
        if (not isinstance(origin, str) or not all(32 < ord(character) < 127 for character in origin)
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or (parsed.scheme != 'https' and not (allow_local_http and local))
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError
        hostname = parsed.hostname.lower()
        host = '[' + hostname + ']' if ':' in hostname else hostname
        if parsed.port is not None and parsed.port != (443 if parsed.scheme == 'https' else 80):
            host += ':' + str(parsed.port)
        return parsed.scheme + '://' + host
    except (ValueError, TypeError, AttributeError) as error:
        raise ConfigError('管理页面须使用独立HTTPS地址；本机预览须明确允许localhost或127.0.0.1。') from error


def _decode(value, size=32):
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != size:
            raise ValueError
        return decoded
    except (ValueError, TypeError) as error:
        raise ConfigError('管理员密码配置无效。') from error


def password_record(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ConfigError('管理员密码须为12到256个字符。')
    salt = secrets.token_bytes(32)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
    return dict(algorithm='scrypt', n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                salt=base64.b64encode(salt).decode('ascii'),
                digest=base64.b64encode(digest).decode('ascii'))


@dataclass(frozen=True)
class AdminConfig:
    license_config: Path
    origin: str
    username: str
    password: dict = field(repr=False)
    allow_local_http: bool = False
    trust_proxy_ip: bool = False
    session_absolute_seconds: int = 3600
    session_idle_seconds: int = 1800

    def __post_init__(self):
        origin = validate_origin(self.origin, allow_local_http=self.allow_local_http is True)
        if (not isinstance(self.username, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', self.username)
                or type(self.allow_local_http) is not bool or type(self.trust_proxy_ip) is not bool
                or type(self.session_absolute_seconds) is not int
                or not 300 <= self.session_absolute_seconds <= 3600
                or type(self.session_idle_seconds) is not int
                or not 60 <= self.session_idle_seconds <= self.session_absolute_seconds):
            raise ConfigError('管理员账号或会话配置无效。')
        value = self.password
        if (not isinstance(value, dict)
                or set(value) != {'algorithm', 'n', 'r', 'p', 'salt', 'digest'}
                or value['algorithm'] != 'scrypt' or type(value['n']) is not int
                or type(value['r']) is not int or type(value['p']) is not int
                or (value['n'], value['r'], value['p']) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)):
            raise ConfigError('管理员密码配置无效。')
        _decode(value['salt'])
        _decode(value['digest'])
        object.__setattr__(self, 'origin', origin)
        object.__setattr__(self, 'license_config', private_path(self.license_config, must_exist=True))

    @classmethod
    def load(cls, path):
        try:
            data = strict_json(private_path(path, must_exist=True).read_bytes(), maximum=16384)
            if (not isinstance(data, dict) or type(data.get('format_version')) is not int
                    or data.pop('format_version') != 1):
                raise ValueError
            return cls(**data)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ConfigError('管理员配置无效，请核对私有文件及权限。') from error

    @property
    def secure_cookie(self):
        return self.origin.startswith('https://')

    @property
    def cookie_name(self):
        return '__Host-ljqc-admin' if self.secure_cookie else 'ljqc_admin'

    @property
    def host(self):
        return urlsplit(self.origin).netloc.lower()


def initialize_admin(path, license_config, origin, username, password, *,
                     allow_local_http=False, trust_proxy_ip=False):
    path = private_path(path)
    if path.exists():
        raise ConfigError('管理员配置已存在，请保留原文件。')
    config = AdminConfig(Path(license_config), origin, username, password_record(password),
                         allow_local_http=allow_local_http, trust_proxy_ip=trust_proxy_ip)
    if path == config.license_config:
        raise ConfigError('管理员配置须使用独立文件。')
    write_private_json(path, dict(format_version=1, license_config=str(config.license_config),
        origin=config.origin, username=config.username, password=config.password,
        allow_local_http=config.allow_local_http, trust_proxy_ip=config.trust_proxy_ip,
        session_absolute_seconds=config.session_absolute_seconds,
        session_idle_seconds=config.session_idle_seconds))
    return config


@dataclass
class Session:
    token: str = field(repr=False)
    csrf: str = field(repr=False)
    created: float
    last_seen: float
    authenticated: bool = False


class AdminAuth:
    def __init__(self, config: AdminConfig, *, clock=time.monotonic, wall_clock=time.time):
        self.config, self.clock, self.wall_clock = config, clock, wall_clock
        self._lock = threading.RLock()
        self._sessions = {}
        self._attempts = deque()
        self._ip_attempts = {}
        self._password_slots = threading.BoundedSemaphore(2)

    def _expired(self, session, now):
        absolute = self.config.session_absolute_seconds if session.authenticated else 600
        idle = self.config.session_idle_seconds if session.authenticated else 600
        return now < session.created or now - session.created >= absolute or now - session.last_seen >= idle

    def _prune(self, now):
        for token, session in list(self._sessions.items()):
            if self._expired(session, now):
                self._sessions.pop(token, None)

    def _new(self, authenticated=False):
        now = self.clock()
        self._prune(now)
        if len(self._sessions) >= 1024:
            raise AdminError('BUSY', '管理服务繁忙，请稍后重试。', 429)
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now, now, authenticated)
        self._sessions[session.token] = session
        return session

    def cookie(self, session=None):
        cookie = SimpleCookie()
        name = self.config.cookie_name
        cookie[name] = session.token if session else ''
        cookie[name]['path'] = '/'
        cookie[name]['httponly'] = True
        cookie[name]['samesite'] = 'Strict'
        cookie[name]['max-age'] = (self.config.session_absolute_seconds if session and session.authenticated
                                   else 600 if session else 0)
        if self.config.secure_cookie:
            cookie[name]['secure'] = True
        return cookie.output(header='').strip()

    def lookup(self, cookie_value, *, create=False):
        token = ''
        if isinstance(cookie_value, str) and len(cookie_value) <= 4096:
            try:
                pairs = [part.strip().split('=', 1)[0] for part in cookie_value.split(';')]
                if pairs.count(self.config.cookie_name) == 1:
                    cookie = SimpleCookie()
                    cookie.load(cookie_value)
                    token = cookie[self.config.cookie_name].value if self.config.cookie_name in cookie else ''
            except (ValueError, KeyError, CookieError):
                token = ''
        with self._lock:
            now = self.clock()
            self._prune(now)
            session = self._sessions.get(token) if re.fullmatch(r'[A-Za-z0-9_-]{43}', token) else None
            if session:
                session.last_seen = now
                return session, False
            if create:
                return self._new(), True
        return None, False

    def require_csrf(self, session, token):
        if (session is None or not isinstance(token, str) or not 16 <= len(token) <= 128
                or not hmac.compare_digest(session.csrf.encode(), token.encode('utf-8'))):
            raise AdminError('CSRF', '页面验证已失效，请刷新页面后重试。', 403)

    def deadlines(self, session):
        now, wall = self.clock(), self.wall_clock()
        absolute = self.config.session_absolute_seconds if session.authenticated else 600
        idle = self.config.session_idle_seconds if session.authenticated else 600
        return dict(expires_at=int(wall + max(0, session.created + absolute - now)),
                    idle_expires_at=int(wall + max(0, min(session.created + absolute,
                                                        session.last_seen + idle) - now)))

    def login(self, session, username, password, client_ip):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 256:
            raise AdminError('INVALID_LOGIN', '账号或密码不正确。', 401)
        now = self.clock()
        with self._lock:
            while self._attempts and now - self._attempts[0] >= 300:
                self._attempts.popleft()
            if len(self._attempts) >= 30:
                raise AdminError('LOGIN_LIMIT', '登录尝试较多，请5分钟后重试。', 429)
            per_ip = self._ip_attempts.setdefault(client_ip, deque())
            while per_ip and now - per_ip[0] >= 300:
                per_ip.popleft()
            if len(per_ip) >= 5:
                raise AdminError('LOGIN_LIMIT', '登录尝试较多，请5分钟后重试。', 429)
            self._attempts.append(now)
            per_ip.append(now)
            # Bound attacker-controlled proxy addresses independently of sessions.
            if len(self._ip_attempts) > 1024:
                self._ip_attempts = {key: value for key, value in self._ip_attempts.items()
                                     if value and now - value[-1] < 300}
        if not self._password_slots.acquire(blocking=False):
            raise AdminError('LOGIN_BUSY', '登录验证繁忙，请稍后重试。', 429)
        try:
            record = self.config.password
            actual = hashlib.scrypt(password.encode('utf-8'), salt=_decode(record['salt']),
                n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
        finally:
            self._password_slots.release()
        matching = hmac.compare_digest(actual, _decode(record['digest']))
        matching = hmac.compare_digest(username.encode('utf-8'), self.config.username.encode('utf-8')) and matching
        if not matching:
            raise AdminError('INVALID_LOGIN', '账号或密码不正确。', 401)
        with self._lock:
            # A concurrent logout/expiration cannot be undone by a slow scrypt.
            if session is None or self._sessions.get(session.token) is not session or self._expired(session, self.clock()):
                raise AdminError('CSRF', '登录页面已失效，请刷新后重试。', 403)
            self._sessions.pop(session.token, None)
            return self._new(authenticated=True)

    def logout(self, session):
        with self._lock:
            if session:
                self._sessions.pop(session.token, None)

    def require_authenticated(self, session):
        with self._lock:
            if (session is None or not session.authenticated
                    or self._sessions.get(session.token) is not session
                    or self._expired(session, self.clock())):
                raise AdminError('UNAUTHENTICATED', '登录已失效，请重新登录。', 401)

