"""Independent admin site: real loopback HTTP, private fixtures and authorization changes."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing_server.admin_auth import AdminAuth, AdminConfig, AdminError, initialize_admin, validate_origin
from licensing_server.admin_cli import main as admin_cli
from licensing_server.admin_httpd import AdminHTTPServer
from licensing_server.config import ConfigError, ServerConfig
from licensing_server.service import LicensingService
from desktop_licensing.client import LicenseClient
from desktop_licensing.protocol import TrustPolicy


PASSWORD = 'isolated-admin-password-2026'


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wall = datetime.fromisoformat('2026-11-12T12:00:00+08:00').timestamp()
        self.mono = 1000.0
        self.license_path = self.root / 'license.json'
        self.license_path.write_text('{}')
        os.chmod(self.license_path, 0o600)
        config = ServerConfig(self.root / 'licenses.db', 'admin-qa', Ed25519PrivateKey.generate(), b'p' * 32)
        self.licensing = LicensingService(config, clock=lambda: self.wall)
        self.config_path = self.root / 'admin.json'
        self.config = initialize_admin(self.config_path, self.license_path, 'https://admin.example.invalid',
                                       'admin', PASSWORD, trust_proxy_ip=True)
        self.auth = AdminAuth(self.config, clock=lambda: self.mono, wall_clock=lambda: self.wall)
        self.server = AdminHTTPServer(('127.0.0.1', 0), self.config, self.licensing.store, auth=self.auth)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.cookie, self.csrf = '', ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds

    def request(self, method, path, value=None, *, cookie=None, csrf=None, origin=None,
                omit_origin=False, raw=None, headers=None, remember=True):
        outgoing = {'Host': self.config.host}
        cookie = self.cookie if cookie is None else cookie
        if cookie:
            outgoing['Cookie'] = cookie
        if method == 'POST':
            outgoing['Content-Type'] = 'application/json'
            if not omit_origin:
                outgoing['Origin'] = self.config.origin if origin is None else origin
            outgoing['X-CSRF-Token'] = self.csrf if csrf is None else csrf
            raw = json.dumps(value or {}).encode() if raw is None else raw
        outgoing.update(headers or {})
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=outgoing)
            response = connection.getresponse()
            body = response.read()
            received = dict(response.getheaders())
            if remember and received.get('Set-Cookie'):
                self.cookie = received['Set-Cookie'].split(';', 1)[0]
            result = json.loads(body) if received.get('Content-Type', '').startswith('application/json') else body
            if remember and isinstance(result, dict) and 'csrf' in result:
                self.csrf = result['csrf']
            return response.status, result, received
        finally:
            connection.close()

    def login(self):
        self.assertEqual(200, self.request('GET', '/api/session')[0])
        result = self.request('POST', '/api/login', {'username': 'admin', 'password': PASSWORD})
        self.assertEqual(200, result[0], result)
        return result

    def issue(self):
        return self.request('POST', '/api/licenses', {'label': '隔离测试实验室', 'max_devices': 10,
                            'expires_at': '2026-11-30T23:59:00+08:00'})

    def test_private_scrypt_config_origin_rules_and_no_overwrite(self):
        value = json.loads(self.config_path.read_text())
        self.assertNotIn(PASSWORD, self.config_path.read_text())
        self.assertEqual(('scrypt', 16384, 8, 5), tuple(value['password'][key] for key in ('algorithm', 'n', 'r', 'p')))
        self.assertEqual(self.config, AdminConfig.load(self.config_path))
        self.assertNotIn('digest', repr(self.config))
        if os.name != 'nt':
            self.assertEqual(0o600, self.config_path.stat().st_mode & 0o777)
        original = self.config_path.read_bytes()
        with self.assertRaises(ConfigError):
            initialize_admin(self.config_path, self.license_path, self.config.origin, 'admin', PASSWORD)
        self.assertEqual(original, self.config_path.read_bytes())
        self.assertEqual('https://admin.example.invalid', validate_origin('https://ADMIN.example.invalid:443/'))
        for origin in ('http://admin.example.invalid', 'http://127.0.0.1:8519', 'https://admin.example.invalid/path'):
            with self.assertRaises(ConfigError):
                validate_origin(origin)
        self.assertEqual('http://127.0.0.1:8519', validate_origin('http://127.0.0.1:8519', allow_local_http=True))

    def test_public_shell_actual_assets_have_strict_headers_and_no_private_api_access(self):
        for path in ('/', '/admin.css', '/admin.js'):
            status, body, headers = self.request('GET', path)
            self.assertEqual(200, status, path)
            self.assertTrue(body)
            self.assertEqual('no-store', headers['Cache-Control'])
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual({'status': 'ok'}, self.request('GET', '/health')[1])
        self.assertEqual(401, self.request('GET', '/api/licenses')[0])
        self.assertNotEqual(200, self.request('GET', '/../admin.json')[0])
        self.assertEqual(400, self.request('GET', '/api/session', headers={'Host': 'other.invalid'})[0])

    def test_login_requires_prelogin_csrf_exact_origin_and_rotates_every_credential(self):
        first = self.request('GET', '/api/session')
        self.assertFalse(first[1]['authenticated'])
        flags = first[2]['Set-Cookie']
        for flag in ('__Host-ljqc-admin=', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, flags)
        before_cookie, before_csrf = self.cookie, self.csrf
        credentials = {'username': 'admin', 'password': PASSWORD}
        self.assertEqual(403, self.request('POST', '/api/login', credentials, omit_origin=True)[0])
        self.assertEqual(403, self.request('POST', '/api/login', credentials, origin='https://elsewhere.invalid')[0])
        self.assertEqual(403, self.request('POST', '/api/login', credentials, csrf='wrong-token-that-is-long-enough')[0])
        logged = self.request('POST', '/api/login', credentials)
        self.assertTrue(logged[1]['authenticated'])
        self.assertEqual('admin', logged[1]['username'])
        self.assertIn('expires_at', logged[1])
        self.assertNotEqual(before_cookie, self.cookie)
        self.assertNotEqual(before_csrf, self.csrf)
        self.assertEqual(401, self.request('GET', '/api/licenses', cookie=before_cookie, remember=False)[0])
        self.assertEqual(403, self.request('POST', '/api/login', credentials,
                                         cookie=before_cookie, csrf=before_csrf, remember=False)[0])

    def test_state_changes_require_auth_json_csrf_and_same_origin(self):
        self.login()
        self.assertEqual(403, self.request('POST', '/api/licenses', {}, omit_origin=True)[0])
        self.assertEqual(403, self.request('POST', '/api/licenses', {}, csrf='incorrect-csrf-token')[0])
        self.assertEqual(400, self.request('POST', '/api/licenses', {}, headers={'Content-Type': 'text/plain'})[0])
        self.assertEqual(400, self.request('POST', '/api/licenses', raw=b'{"label":"a","label":"b"}')[0])
        self.assertEqual(400, self.request('POST', '/api/licenses', raw=b' ' * 16385)[0])
        self.assertEqual(400, self.request('POST', '/api/licenses', {'label': '', 'max_devices': True,
                                         'expires_at': '2026-11-30T23:59:00+08:00'})[0])
        self.assertEqual([], self.licensing.store.list_licenses())

    def test_issue_lists_and_device_actions_use_same_store_without_exposing_original_code(self):
        self.login()
        status, issued, headers = self.issue()
        self.assertEqual(201, status)
        self.assertEqual(10, issued['max_devices'])
        license_id, code = issued['license_id'], issued['code']
        listing = self.request('GET', '/api/licenses')[1]
        self.assertNotIn(code, json.dumps(listing))
        self.assertNotIn('code_digest', json.dumps(listing))
        policy = TrustPolicy.from_bytes(json.dumps(self.licensing.config.public_trust('https://license.invalid')).encode())
        client = LicenseClient(policy, self.root / 'device',
            transport=lambda endpoint, body: self.licensing.request(endpoint, body),
            clock=lambda: self.wall, monotonic=lambda: self.mono, machine_hash_provider=lambda: 'a' * 64)
        self.assertTrue(client.check(code).allowed)
        devices = self.request('GET', '/api/licenses/' + license_id + '/devices')[1]
        self.assertEqual(1, devices['license']['active_devices'])
        device_id = devices['devices'][0]['id']
        self.assertEqual(200, self.request('POST', '/api/licenses/' + license_id + '/extend',
                                          {'expires_at': '2026-12-30T23:59:00+08:00'})[0])
        self.assertEqual(200, self.request('POST', '/api/licenses/' + license_id + '/disable')[0])
        self.assertEqual(0, self.licensing.store.get_license(license_id)['enabled'])
        self.assertEqual(200, self.request('POST', '/api/licenses/' + license_id + '/enable')[0])
        self.assertEqual(1, self.licensing.store.get_license(license_id)['enabled'])
        self.assertEqual(200, self.request('POST', '/api/licenses/' + license_id + '/devices/' + device_id + '/unbind')[0])
        self.assertEqual(0, self.licensing.store.get_license(license_id)['active_devices'])
        self.assertNotIn(code, json.dumps(self.request('GET', '/api/licenses')[1]))
        self.assertEqual(404, self.request('POST', '/api/licenses/not-an-id/disable')[0])

    def test_logout_requires_csrf_and_old_session_cannot_be_replayed(self):
        self.login()
        old_cookie, old_csrf = self.cookie, self.csrf
        self.assertEqual(403, self.request('POST', '/api/logout', csrf='wrong-csrf-token')[0])
        result = self.request('POST', '/api/logout')
        self.assertEqual(200, result[0])
        self.assertIn('Max-Age=0', result[2]['Set-Cookie'])
        self.assertEqual(401, self.request('GET', '/api/licenses', cookie=old_cookie, remember=False)[0])
        self.assertEqual(401, self.request('POST', '/api/logout', cookie=old_cookie,
                                         csrf=old_csrf, remember=False)[0])

    def test_idle_and_absolute_deadlines_survive_page_reopens(self):
        result = self.login()[1]
        deadline = result['expires_at']
        self.assertEqual(int(self.wall + 3600), deadline)
        for _ in range(3):
            self.advance(1000)
            session = self.request('GET', '/api/session')[1]
            self.assertTrue(session['authenticated'])
            self.assertEqual(deadline, session['expires_at'])
        self.advance(600)
        self.assertEqual(401, self.request('GET', '/api/licenses')[0])
        self.login()
        self.advance(1800)
        self.assertEqual(401, self.request('GET', '/api/licenses')[0])

    def test_five_failures_per_ip_and_global_limit_cannot_be_evaded_by_forwarded_for(self):
        self.request('GET', '/api/session')
        bad = {'username': 'admin', 'password': 'wrong-password'}
        for index in range(5):
            self.assertEqual(401, self.request('POST', '/api/login', bad,
                headers={'X-Forwarded-For': '198.51.100.' + str(index + 1)})[0])
        self.assertEqual(429, self.request('POST', '/api/login', bad)[0])
        self.advance(300)
        self.assertEqual(200, self.request('POST', '/api/login', {'username': 'admin', 'password': PASSWORD})[0])

    def test_proxy_addresses_global_limit_and_disabled_proxy_trust(self):
        self.request('GET', '/api/session')
        bad = {'username': 'admin', 'password': 'wrong-password'}
        for index in range(30):
            self.assertEqual(401, self.request('POST', '/api/login', bad,
                headers={'X-LJQC-Client-IP': '198.51.100.' + str(index + 1)})[0])
        self.assertEqual(429, self.request('POST', '/api/login', bad,
            headers={'X-LJQC-Client-IP': '203.0.113.99'})[0])
        self.assertLessEqual(len(self.auth._ip_attempts), 30)
        self.assertFalse(AdminConfig(self.config.license_config, self.config.origin,
            self.config.username, self.config.password).trust_proxy_ip)

    def test_restart_invalidates_sessions_and_local_http_is_explicit(self):
        self.login()
        self.server.auth = AdminAuth(self.config, clock=lambda: self.mono)
        self.assertEqual(401, self.request('GET', '/api/licenses')[0])
        local = replace(self.config, origin='http://127.0.0.1:8519', allow_local_http=True)
        cookie = AdminAuth(local).cookie(AdminAuth(local)._new())
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertNotIn('Secure', cookie)
        self.assertNotIn('__Host-', cookie)

    def test_cli_asks_twice_never_accepts_password_argument_and_does_not_overwrite(self):
        new_path = self.root / 'admin-cli.json'
        arguments = ['init', '--config', str(new_path), '--license-config', str(self.license_path),
                     '--origin', self.config.origin]
        with patch('licensing_server.admin_cli.getpass.getpass', side_effect=[PASSWORD, PASSWORD]), patch('builtins.print') as output:
            admin_cli(arguments)
            self.assertNotIn(PASSWORD, str(output.call_args_list))
        self.assertTrue(new_path.is_file())
        original = new_path.read_bytes()
        with patch('licensing_server.admin_cli.getpass.getpass', side_effect=[PASSWORD, PASSWORD]), self.assertRaises(SystemExit):
            admin_cli(arguments)
        self.assertEqual(original, new_path.read_bytes())
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            admin_cli(arguments + ['--password', PASSWORD])


if __name__ == '__main__':
    unittest.main(verbosity=2)
