"""Isolated server security, lease lifecycle and concurrent ten-device limits."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_licensing.protocol import TrustPolicy, proof_payload, verify_grant
from licensing_server.cli import main as cli
from licensing_server.config import (
    ConfigError, DEFAULT_ENDS_AT, SOURCE_ROOT, ServerConfig, initialize, parse_deadline, private_path,
)
from licensing_server.httpd import LicensingHTTPServer, parse_request
from licensing_server.service import LicenseError, LicensingService, canonical_json
from licensing_server.store import LicenseStore


class Clock:
    def __init__(self):
        self.now = parse_deadline('2026-11-12T12:00:00+08:00')

    def __call__(self):
        return self.now


def device(index):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')
    return key, dict(device_public_key=public, machine_hash=hashlib.sha256(str(index).encode()).hexdigest())


class LicensingServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='ljqc-license-server-')
        self.root = Path(self.temp.name)
        self.config_path, self.database_path = self.root / 'private.json', self.root / 'licenses.sqlite3'
        self.public_path = self.root / 'trust.json'
        initialize(self.config_path, self.database_path, self.public_path, 'https://licensing.example.test')
        self.config = ServerConfig.load(self.config_path)
        self.clock = Clock()
        self.service = LicensingService(self.config, clock=self.clock)
        self.policy = TrustPolicy.from_file(self.public_path)
        self.issued = self.service.store.issue('临时隔离测试')

    def tearDown(self):
        self.temp.cleanup()

    def base(self, identity, *, grant=None, code=None):
        return dict(identity, action='renew' if grant else 'activate',
                    **({'grant': grant} if grant else {'code': code or self.issued['code']}))

    def finish(self, base, key, challenge=None):
        challenge = challenge or self.service.request('/v1/challenge', base)
        proof = base64.b64encode(key.sign(proof_payload(base['action'], challenge['challenge_id'],
            challenge['nonce'], challenge['request_digest']))).decode('ascii')
        request = dict(base, challenge_id=challenge['challenge_id'], nonce=challenge['nonce'], proof=proof)
        return self.service.request('/v1/' + base['action'], request), request

    def activate(self, key, identity, code=None):
        response, _ = self.finish(self.base(identity, code=code), key)
        verify_grant(response['grant'], self.policy, **identity, now=self.clock())
        return response['grant']

    def assert_error(self, code, action):
        with self.assertRaises(LicenseError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_concurrent_eleven_devices_never_exceeds_ten(self):
        devices = [device(index) for index in range(11)]
        gate = threading.Barrier(11)

        def run(item):
            key, identity = item
            gate.wait(timeout=10)
            try:
                return self.activate(key, identity)
            except LicenseError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=11) as pool:
            results = list(pool.map(run, devices))
        self.assertEqual(10, sum(isinstance(row, dict) for row in results))
        self.assertEqual(['DEVICE_LIMIT'], [row for row in results if isinstance(row, str)])
        self.assertEqual(10, self.service.store.list_licenses()[0]['active_devices'])
        self.assertEqual(10, len(self.service.store.list_devices(self.issued['license_id'])))

    def test_concurrent_same_device_and_lost_response_are_idempotent(self):
        key, identity = device('same')
        with ThreadPoolExecutor(max_workers=8) as pool:
            grants = list(pool.map(lambda _: self.activate(key, identity), range(8)))
        self.assertEqual(1, len({row['signed']['activation_id'] for row in grants}))
        again = self.activate(key, identity)
        self.assertEqual(grants[0]['signed']['activation_id'], again['signed']['activation_id'])
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])
        self.assertNotEqual(grants[0]['signed']['request_nonce'], again['signed']['request_nonce'])

    def test_wrong_code_proof_and_request_changes_fail_without_consuming_seat(self):
        key, identity = device('proof')
        self.assert_error('INVALID_CODE', lambda: self.service.challenge(self.base(identity, code='incorrect-code')))
        base = self.base(identity)
        challenge = self.service.challenge(base)
        other, _ = device('other')
        self.assert_error('INVALID_PROOF', lambda: self.finish(base, other, challenge))
        self.assert_error('CHALLENGE_USED', lambda: self.finish(base, key, challenge))
        challenge = self.service.challenge(base)
        changed = dict(base, machine_hash='f' * 64)
        self.assert_error('INVALID_PROOF', lambda: self.finish(changed, key, challenge))
        self.assert_error('CHALLENGE_USED', lambda: self.finish(base, key, challenge))
        self.assertEqual(0, self.service.store.list_licenses()[0]['active_devices'])

    def test_replay_and_expired_challenge_fail(self):
        key, identity = device('replay')
        base = self.base(identity)
        _, request = self.finish(base, key)
        self.assert_error('CHALLENGE_USED', lambda: self.service.request('/v1/activate', request))
        challenge = self.service.challenge(base)
        self.clock.now = challenge['expires_at']
        self.assert_error('CHALLENGE_EXPIRED', lambda: self.finish(base, key, challenge))
        self.assert_error('CHALLENGE_USED', lambda: self.finish(base, key, challenge))

    def test_deadline_caps_lease_and_extension_renews_expired_old_grant(self):
        deadline = parse_deadline(DEFAULT_ENDS_AT)
        self.clock.now = deadline - 10
        key, identity = device('expires')
        grant = self.activate(key, identity)
        self.assertEqual(deadline, grant['signed']['expires_at'])
        self.assertEqual(deadline, grant['signed']['license_expires_at'])
        self.clock.now = deadline
        base = self.base(identity, grant=grant)
        self.assert_error('EXPIRED', lambda: self.service.challenge(base))
        self.service.store.extend(self.issued['license_id'], '2026-12-05T23:59:00+08:00')
        response, _ = self.finish(base, key)
        new = verify_grant(response['grant'], self.policy, **identity, now=self.clock())
        self.assertEqual(grant['signed']['activation_id'], new['activation_id'])
        self.assertEqual(86400, new['expires_at'] - new['issued_at'])
        self.assertGreater(new['license_generation'], grant['signed']['license_generation'])

    def test_disable_enable_requires_code_and_keeps_original_device_seat(self):
        key, identity = device('disable')
        grant = self.activate(key, identity)
        base = self.base(identity, grant=grant)
        self.service.store.set_enabled(self.issued['license_id'], False)
        self.assert_error('DISABLED', lambda: self.service.challenge(base))
        self.service.store.set_enabled(self.issued['license_id'], True)
        self.assert_error('GRANT_REVOKED', lambda: self.service.challenge(base))
        replacement = self.activate(key, identity)
        self.assertEqual(grant['signed']['activation_id'], replacement['signed']['activation_id'])
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])

    def test_unbind_revokes_old_key_and_allows_explicit_new_key_recovery(self):
        key, identity = device('unbind')
        grant = self.activate(key, identity)
        changed_key, changed = device('new-key')
        changed['machine_hash'] = identity['machine_hash']
        self.assert_error('DEVICE_IDENTITY_CHANGED', lambda: self.activate(changed_key, changed))
        self.service.store.unbind(self.issued['license_id'], grant['signed']['activation_id'])
        self.assertEqual(0, self.service.store.list_licenses()[0]['active_devices'])
        self.assert_error('DEVICE_UNBOUND', lambda: self.service.challenge(self.base(identity, grant=grant)))
        self.assert_error('DEVICE_UNBOUND', lambda: self.activate(key, identity))
        recovered = self.activate(changed_key, changed)
        self.assertEqual(grant['signed']['activation_id'], recovered['signed']['activation_id'])
        self.assertGreater(recovered['signed']['device_generation'], grant['signed']['device_generation'])
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])

    def test_copy_grant_or_device_key_to_another_machine_is_rejected(self):
        key, identity = device('original')
        grant = self.activate(key, identity)
        copied = dict(identity, machine_hash='0' * 64)
        self.assert_error('INVALID_PROOF', lambda: self.service.challenge(self.base(copied, grant=grant)))
        self.assert_error('INVALID_PROOF', lambda: self.activate(key, copied))
        fake_key, fake = device('fake')
        fake['machine_hash'] = identity['machine_hash']
        self.assert_error('INVALID_PROOF', lambda: self.service.challenge(self.base(fake, grant=grant)))

    def test_modified_grant_key_and_outdated_grant_are_rejected(self):
        key, identity = device('tamper')
        grant = self.activate(key, identity)
        modified = deepcopy(grant)
        modified['signed']['expires_at'] += 1
        self.assert_error('INVALID_GRANT', lambda: self.service.challenge(self.base(identity, grant=modified)))
        modified = dict(grant, key_id='untrusted-key')
        self.assert_error('INVALID_GRANT', lambda: self.service.challenge(self.base(identity, grant=modified)))
        self.clock.now += 31 * 86400
        self.service.store.extend(self.issued['license_id'], '2027-02-01T23:59:00+08:00')
        self.assert_error('INVALID_GRANT', lambda: self.service.challenge(self.base(identity, grant=grant)))

    def test_admin_change_between_challenge_and_completion_takes_effect(self):
        key, identity = device('race')
        base = self.base(identity)
        challenge = self.service.challenge(base)
        self.service.store.set_enabled(self.issued['license_id'], False)
        self.assert_error('DISABLED', lambda: self.finish(base, key, challenge))
        self.assert_error('CHALLENGE_USED', lambda: self.finish(base, key, challenge))
        self.assertEqual(0, self.service.store.list_licenses()[0]['active_devices'])

    def test_private_material_is_absent_from_public_trust_store_outputs_and_code_storage(self):
        private = json.loads(self.config_path.read_text())
        public = self.public_path.read_text()
        for secret in (private['private_key'], private['code_pepper'], self.issued['code']):
            self.assertNotIn(secret, public)
            self.assertNotIn(secret, json.dumps(self.service.store.list_licenses()))
        self.assertNotIn(self.issued['code'].encode(), self.database_path.read_bytes())
        self.assertNotIn(repr(self.config.code_pepper), repr(self.config))
        if os.name != 'nt':
            self.assertEqual(0o600, self.config_path.stat().st_mode & 0o777)
            self.assertEqual(0o600, self.database_path.stat().st_mode & 0o777)
            self.config_path.chmod(0o644)
            with self.assertRaises(ConfigError):
                ServerConfig.load(self.config_path)
            self.config_path.chmod(0o600)
            self.root.chmod(0o777)
            try:
                with self.assertRaises(ConfigError):
                    ServerConfig.load(self.config_path)
            finally:
                self.root.chmod(0o700)
        with self.assertRaises(ConfigError):
            private_path(SOURCE_ROOT / 'private-license.json')
        with self.assertRaises(ConfigError):
            private_path('/var/lib/ljqc/license.sqlite3')
        alias = self.root / 'alias.json'
        alias.symlink_to(self.config_path)
        with self.assertRaises(ConfigError):
            ServerConfig.load(alias)

    def test_existing_business_database_is_never_initialized_as_license_storage(self):
        business = self.root / 'existing-business.db'
        descriptor = os.open(business, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(business)) as connection:
            connection.execute('CREATE TABLE results(id INTEGER,value TEXT)')
            connection.execute("INSERT INTO results VALUES(1,'preserve')")
            connection.commit()
        before = business.read_bytes()
        config = ServerConfig(business, self.config.key_id, self.config.signing_key, self.config.code_pepper)
        with self.assertRaises(ValueError):
            LicenseStore(config, clock=self.clock)
        self.assertEqual(before, business.read_bytes())

    def test_http_parsing_routes_and_denials_do_not_expose_administration_or_secrets(self):
        for raw in (b'{"action":"activate","action":"renew"}', b'{"code":NaN}', b'[]', b'\xff'):
            self.assert_error('INVALID_REQUEST', lambda raw=raw: parse_request(raw))
        for route in ('/admin', '/v1/issue', '/v1/list', '/v1/disable'):
            self.assert_error('INVALID_REQUEST', lambda route=route: self.service.request(route, {}))
        with self.assertRaises(ValueError):
            LicensingHTTPServer(('0.0.0.0', 8518), self.service)
        error = LicenseError('DEVICE_LIMIT')
        denial = self.service.denial(error, 'test-request-nonce')
        signed = denial['denial']
        self.config.signing_key.public_key().verify(base64.b64decode(signed['signature']), canonical_json(signed['signed']))
        self.assertEqual('test-request-nonce', signed['signed']['request_nonce'])
        self.assertNotIn(self.issued['code'], json.dumps(denial))

    def test_cli_management_output_does_not_repeat_code_or_private_key(self):
        def run(*arguments):
            output = io.StringIO()
            with redirect_stdout(output), patch('licensing_server.cli.LicenseStore',
                    side_effect=lambda config: LicenseStore(config, clock=self.clock)):
                cli([arguments[0], '--config', str(self.config_path), *arguments[1:]])
            return json.loads(output.getvalue())

        issued = run('issue', '--label', 'CLI测试', '--max-devices', '10')
        self.assertTrue(issued['code'].startswith('LJQC-'))
        listed = run('list')
        self.assertNotIn(issued['code'], json.dumps(listed))
        self.assertNotIn('code_digest', json.dumps(listed))
        for command in ('disable', 'enable'):
            run(command, '--license-id', issued['license_id'])
        run('extend', '--license-id', issued['license_id'], '--expires-at', '2026-12-01T23:59:00+08:00')
        key, identity = device('cli')
        grant = self.activate(key, identity, issued['code'])
        self.assertEqual(1, len(run('devices', '--license-id', issued['license_id'])))
        run('unbind', '--license-id', issued['license_id'], '--device-id', grant['signed']['activation_id'])
        self.assertEqual(0, next(row for row in run('list') if row['id'] == issued['license_id'])['active_devices'])
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            cli(['list', '--config', str(self.root / 'missing-private.json')])
        self.assertNotIn(str(self.root), stderr.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
