"""Independent loopback administrator site; HTTPS belongs to its own proxy origin."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sqlite3
import threading

from desktop_licensing.protocol import canonical_json, strict_json
from .admin_auth import AdminAuth, AdminConfig, AdminError
from .httpd import observed_request_ip


MAX_BODY_BYTES = 16384
_LICENSE = re.compile(r'^/api/licenses/([0-9a-f]{32})(?:/(extend|disable|enable|devices))?$')
_UNBIND = re.compile(r'^/api/licenses/([0-9a-f]{32})/devices/([0-9a-f]{32})/unbind$')
_SAFE_MESSAGES = frozenset((
    '使用期限须晚于当前时间。', '设备台数须为1到1000之间的整数。', '备注最多160个字。',
    '验证码记录不存在。', '延期后的时间不能早于原截止时间。', '设备记录不存在。',
    '截止时间须包含时区，例如2026-11-30T23:59:00+08:00。',
))


class AdminHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, config: AdminConfig, store, *, auth=None, assets_dir=None):
        if address[0] != '127.0.0.1':
            raise ValueError('管理服务仅监听127.0.0.1，由独立HTTPS地址转发。')
        self.config, self.store = config, store
        self.auth = auth or AdminAuth(config)
        self.assets_dir = Path(assets_dir) if assets_dir else Path(__file__).parent / 'admin_assets'
        self.trust_proxy_ip = config.trust_proxy_ip
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(address, AdminHandler)

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n'
                                b'Cache-Control: no-store\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        return


class AdminHandler(BaseHTTPRequestHandler):
    server_version = 'LJQC-Admin'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, format, *args):
        return

    def _send(self, status, value, *, cookie=None):
        self._bytes(status, canonical_json(value), 'application/json; charset=utf-8', cookie=cookie)

    def _bytes(self, status, raw, content_type, *, cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'; object-src 'none'")
        if self.server.config.secure_cookie:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self._send(code, dict(error='INVALID_REQUEST', message='请求未完成，请刷新页面后重试。'))

    def _host(self):
        hosts = self.headers.get_all('Host') or []
        if len(hosts) != 1 or hosts[0].lower() != self.server.config.host:
            raise AdminError('INVALID_HOST', '管理页面地址不匹配，请使用指定网址。', 400)

    def _origin(self):
        origins = self.headers.get_all('Origin') or []
        if len(origins) != 1 or origins[0] != self.server.config.origin:
            raise AdminError('ORIGIN', '请从管理页面提交操作。', 403)

    def _session(self, *, authenticated=False, create=False):
        cookies = self.headers.get_all('Cookie') or []
        if len(cookies) > 1:
            raise AdminError('UNAUTHENTICATED', '请先登录。', 401)
        session, created = self.server.auth.lookup(cookies[0] if cookies else '', create=create)
        if authenticated and (session is None or not session.authenticated):
            raise AdminError('UNAUTHENTICATED', '登录已失效，请重新登录。', 401)
        return session, created

    def _csrf(self, session):
        tokens = self.headers.get_all('X-CSRF-Token') or []
        self.server.auth.require_csrf(session, tokens[0] if len(tokens) == 1 else None)

    def _body(self):
        lengths = self.headers.get_all('Content-Length') or []
        types = self.headers.get_all('Content-Type') or []
        if (len(lengths) != 1 or not lengths[0].isdigit() or len(types) != 1
                or not 1 <= int(lengths[0]) <= MAX_BODY_BYTES or self.headers.get('Transfer-Encoding')
                or self.headers.get_content_type() != 'application/json'
                or self.headers.get('Content-Encoding')):
            raise AdminError('INVALID_BODY', '请提交有效的页面内容。')
        raw = self.rfile.read(int(lengths[0]))
        if len(raw) != int(lengths[0]):
            raise AdminError('INVALID_BODY', '提交内容不完整，请重试。')
        try:
            value = strict_json(raw, maximum=MAX_BODY_BYTES)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError) as error:
            raise AdminError('INVALID_BODY', '提交内容格式无效，请核对后重试。') from error

    def _session_value(self, session, username):
        value = dict(authenticated=session.authenticated, csrf=session.csrf)
        value.update(self.server.auth.deadlines(session))
        if session.authenticated:
            value['username'] = username
        return value

    def _failure(self, error):
        if isinstance(error, AdminError):
            self._send(error.status, dict(error=error.code, message=str(error)))
        elif isinstance(error, ValueError):
            text = str(error)
            self._send(400, dict(error='INVALID_VALUE', message=text if text in _SAFE_MESSAGES
                                else '请核对填写内容和所选记录后重试。'))
        else:
            self._send(503, dict(error='UNAVAILABLE', message='管理服务暂时不可用，请稍后重试。'))

    def do_GET(self):
        try:
            self._host()
            if self.path == '/health':
                self._send(200, {'status': 'ok'})
                return
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/admin.css': ('admin.css', 'text/css; charset=utf-8'),
                      '/admin.js': ('admin.js', 'text/javascript; charset=utf-8')}
            if self.path in assets:
                name, content_type = assets[self.path]
                asset = self.server.assets_dir / name
                if asset.is_symlink() or not asset.is_file() or asset.stat().st_size > 1024 * 1024:
                    raise AdminError('NOT_FOUND', '页面资源未找到。', 404)
                self._bytes(200, asset.read_bytes(), content_type)
                return
            if self.path == '/api/session':
                session, created = self._session(create=True)
                self._send(200, self._session_value(session, self.server.config.username),
                           cookie=self.server.auth.cookie(session) if created else None)
                return
            self._session(authenticated=True)
            if self.path == '/api/licenses':
                self._send(200, {'licenses': self.server.store.list_licenses()})
                return
            match = _LICENSE.fullmatch(self.path)
            if match and match[2] == 'devices':
                license = self.server.store.get_license(match[1])
                self._send(200, dict(license=license, devices=self.server.store.list_devices(match[1])))
                return
            raise AdminError('NOT_FOUND', '页面或记录未找到。', 404)
        except (AdminError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            self._failure(error)

    def do_POST(self):
        try:
            self._host()
            self._origin()
            session, _ = self._session(authenticated=self.path != '/api/login')
            self._csrf(session)
            value = self._body()
            if self.path == '/api/login':
                if set(value) != {'username', 'password'}:
                    raise AdminError('INVALID_LOGIN', '请填写账号和密码。')
                ip = observed_request_ip(self.client_address, self.headers,
                                         trust_proxy_ip=self.server.trust_proxy_ip)
                session = self.server.auth.login(session, value['username'], value['password'], ip)
                self._send(200, self._session_value(session, self.server.config.username),
                           cookie=self.server.auth.cookie(session))
                return
            self.server.auth.require_authenticated(session)
            if self.path == '/api/logout':
                if value:
                    raise AdminError('INVALID_BODY', '退出登录内容无效。')
                self.server.auth.logout(session)
                self._send(200, {'ok': True}, cookie=self.server.auth.cookie())
                return
            if self.path == '/api/licenses':
                if (set(value) != {'label', 'max_devices', 'expires_at'}
                        or not isinstance(value['label'], str) or len(value['label']) > 160
                        or type(value['max_devices']) is not int
                        or not 1 <= value['max_devices'] <= 1000
                        or not isinstance(value['expires_at'], str) or len(value['expires_at']) > 64):
                    raise AdminError('INVALID_VALUE', '请填写160字以内的备注、1到1000台设备和带时区的截止时间。')
                issued = self.server.store.issue(**value)
                self._send(201, issued)
                return
            match = _LICENSE.fullmatch(self.path)
            if match and match[2] in ('extend', 'disable', 'enable'):
                license_id, action = match.groups()
                if action == 'extend':
                    if (set(value) != {'expires_at'} or not isinstance(value['expires_at'], str)
                            or len(value['expires_at']) > 64):
                        raise AdminError('INVALID_VALUE', '请填写带时区的截止时间。')
                    self.server.store.extend(license_id, value['expires_at'])
                else:
                    if value:
                        raise AdminError('INVALID_BODY', '请重新选择需要启停的记录。')
                    self.server.store.set_enabled(license_id, action == 'enable')
                self._send(200, {'ok': True})
                return
            match = _UNBIND.fullmatch(self.path)
            if match:
                if value:
                    raise AdminError('INVALID_BODY', '请重新选择需要解绑的设备。')
                self.server.store.unbind(match[1], match[2])
                self._send(200, {'ok': True})
                return
            raise AdminError('NOT_FOUND', '操作或记录未找到。', 404)
        except (AdminError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            self._failure(error)
