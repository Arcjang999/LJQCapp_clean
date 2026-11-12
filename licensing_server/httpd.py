"""Small loopback-only HTTP adapter; public transport terminates at HTTPS nginx."""
from __future__ import annotations

import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
import time

from .config import ConfigError, ServerConfig
from .service import LicenseError, LicensingService, canonical_json, normalize_observed_ip

MAX_BODY_BYTES = 16384


def observed_request_ip(peer, headers, trust_proxy_ip=False):
    """Resolve only the socket peer or an explicitly trusted local proxy header."""
    if isinstance(peer, (tuple, list)) and peer:
        peer = peer[0]
    direct = normalize_observed_ip(peer)
    if trust_proxy_ip is True and direct == '127.0.0.1':
        if hasattr(headers, 'get_all'):
            values = headers.get_all('X-LJQC-Client-IP') or []
        else:
            value = headers.get('X-LJQC-Client-IP') if headers is not None else None
            values = [] if value is None else [value]
        if len(values) == 1:
            forwarded = normalize_observed_ip(values[0])
            if forwarded:
                return forwarded
    return direct


def parse_request(raw: bytes):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise LicenseError('INVALID_REQUEST', 400) from exc


class LicensingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, service, *, trust_proxy_ip=False):
        if address[0] != '127.0.0.1':
            raise ValueError('授权服务仅监听127.0.0.1，由HTTPS入口转发。')
        self.service = service
        if type(trust_proxy_ip) is not bool:
            raise ValueError('反代IP信任设置无效。')
        self.trust_proxy_ip = trust_proxy_ip
        self._slots = threading.BoundedSemaphore(32)
        self._rate_lock = threading.Lock()
        self._recent = deque()
        super().__init__(address, LicensingHandler)

    def rate_allowed(self):
        now = time.monotonic()
        with self._rate_lock:
            while self._recent and self._recent[0] < now - 60:
                self._recent.popleft()
            if len(self._recent) >= 300:
                return False
            self._recent.append(now)
            return True

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
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
        # Request payloads, codes, proofs and private paths must not enter logs.
        return


class LicensingHandler(BaseHTTPRequestHandler):
    server_version = 'LJQC-License'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, format, *args):
        return

    def observed_client_ip(self):
        return observed_request_ip(self.client_address, self.headers,
                                   trust_proxy_ip=self.server.trust_proxy_ip)

    def _send(self, status, value):
        raw = canonical_json(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            self._send(200, {'status': 'ok'})
        else:
            self._send(404, {'error': 'NOT_FOUND', 'message': '接口不存在。'})

    def do_POST(self):
        payload = {}
        try:
            if not self.server.rate_allowed():
                raise LicenseError('SERVICE_BUSY', 429)
            if self.path not in ('/v1/challenge', '/v1/activate', '/v1/renew'):
                raise LicenseError('INVALID_REQUEST', 404)
            lengths = self.headers.get_all('Content-Length') or []
            if (len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding')
                    or not 1 <= int(lengths[0]) <= MAX_BODY_BYTES
                    or self.headers.get_content_type() != 'application/json'):
                raise LicenseError('INVALID_REQUEST', 400)
            length = int(lengths[0])
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise LicenseError('INVALID_REQUEST', 400)
            payload = parse_request(raw)
            self._send(200, self.server.service.request(self.path, payload,
                                                       observed_ip=self.observed_client_ip()))
        except LicenseError as exc:
            nonce = payload.get('nonce', '')
            nonce = nonce if isinstance(nonce, str) and len(nonce) <= 128 else ''
            self._send(exc.status, self.server.service.denial(exc, nonce))
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            self._send(503, {'error': 'SERVICE_UNAVAILABLE', 'message': '验证服务暂时不可用，请稍后重试。'})


def main(argv=None):
    parser = argparse.ArgumentParser(description='运行桌面授权验证服务，仅监听本机。')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8518)
    parser.add_argument('--trust-proxy-ip', action='store_true',
                        help='仅接受本机反代覆写的单一X-LJQC-Client-IP；须同时正确配置nginx。')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('端口须为1024到65535之间的整数。')
    try:
        service = LicensingService(ServerConfig.load(args.config))
        with LicensingHTTPServer(('127.0.0.1', args.port), service, trust_proxy_ip=args.trust_proxy_ip) as server:
            server.serve_forever()
    except (ConfigError, OSError, ValueError, sqlite3.Error):
        parser.exit(1, '授权服务启动失败，请核对私有配置及文件权限。\n')


if __name__ == '__main__':
    main()

