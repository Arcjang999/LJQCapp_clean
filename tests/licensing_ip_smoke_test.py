"""Observed activation IPs, conservative proxy trust and isolated schema upgrades."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email.message import Message
import io
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop_licensing.protocol import canonical_json, proof_payload, verify_grant
from licensing_server.httpd import LicensingHandler, observed_request_ip
from licensing_server.service import LicenseError, LicensingService, normalize_observed_ip
from licensing_server.store import APPLICATION_ID, LicenseStore, SCHEMA
from tests import licensing_server_smoke_test as fixtures


class LicensingIPTests(unittest.TestCase):
    setUp = fixtures.LicensingServerTests.setUp
    tearDown = fixtures.LicensingServerTests.tearDown
    base = fixtures.LicensingServerTests.base
    assert_error = fixtures.LicensingServerTests.assert_error

    def prepare(self, key, base, *, proof_key=None):
        challenge = self.service.request('/v1/challenge', base, observed_ip='198.51.100.250')
        proof = base64.b64encode((proof_key or key).sign(proof_payload(base['action'], challenge['challenge_id'],
            challenge['nonce'], challenge['request_digest']))).decode('ascii')
        return dict(base, challenge_id=challenge['challenge_id'], nonce=challenge['nonce'], proof=proof)

    def exchange(self, key, base, observed_ip=''):
        request = self.prepare(key, base)
        response = self.service.request('/v1/' + base['action'], request, observed_ip=observed_ip)
        verify_grant(response['grant'], self.policy,
                     device_public_key=base['device_public_key'], machine_hash=base['machine_hash'],
                     now=self.clock())
        return response['grant']

    def devices(self):
        return self.service.store.list_devices(self.issued['license_id'])

    def handler(self, *, peer='127.0.0.1', trust=False, headers=()):
        handler = object.__new__(LicensingHandler)
        handler.client_address = (peer, 12345)
        handler.server = SimpleNamespace(service=self.service, trust_proxy_ip=trust, rate_allowed=lambda: True)
        handler.headers = Message()
        for name, value in headers:
            handler.headers[name] = value
        return handler

    def test_successful_activation_and_renewal_record_only_observed_normalized_ip(self):
        key, identity = fixtures.device('ip-success')
        grant = self.exchange(key, self.base(identity), '198.51.100.10')
        row = self.devices()[0]
        self.assertEqual(('198.51.100.10', '198.51.100.10'), (row['first_ip'], row['last_ip']))
        self.clock.now += 30
        grant = self.exchange(key, self.base(identity, grant=grant), '2001:0DB8:0000:0000:0000:0000:0000:0020')
        row = self.devices()[0]
        self.assertEqual(('198.51.100.10', '2001:db8::20'), (row['first_ip'], row['last_ip']))
        for invalid in ('', '198.51.100.1,198.51.100.2', 'fe80::1%eth0', '192.0.2.1\r\nInjected: yes'):
            grant = self.exchange(key, self.base(identity, grant=grant), invalid)
            self.assertEqual('2001:db8::20', self.devices()[0]['last_ip'])
        self.assertEqual(1, self.service.store.get_license(self.issued['license_id'])['active_devices'])

    def test_first_nonempty_observed_ip_is_retained_when_initial_activation_has_no_ip(self):
        key, identity = fixtures.device('ip-later')
        grant = self.exchange(key, self.base(identity))
        self.assertEqual(('', ''), (self.devices()[0]['first_ip'], self.devices()[0]['last_ip']))
        grant = self.exchange(key, self.base(identity, grant=grant), '203.0.113.20')
        self.assertEqual(('203.0.113.20', '203.0.113.20'),
                         (self.devices()[0]['first_ip'], self.devices()[0]['last_ip']))
        self.exchange(key, self.base(identity), '203.0.113.21')
        self.assertEqual(('203.0.113.20', '203.0.113.21'),
                         (self.devices()[0]['first_ip'], self.devices()[0]['last_ip']))

    def test_invalid_proof_replay_and_json_claims_never_change_recorded_ip(self):
        key, identity = fixtures.device('ip-spoof')
        base = self.base(identity)
        grant = self.exchange(key, base, '192.0.2.10')
        before = self.devices()
        other, _ = fixtures.device('wrong-proof')
        request = self.prepare(key, self.base(identity, grant=grant), proof_key=other)
        self.assert_error('INVALID_PROOF', lambda: self.service.request('/v1/renew', request, observed_ip='192.0.2.99'))
        self.assertEqual(before, self.devices())
        for field in ('ip', 'observed_ip', 'first_ip', 'last_ip'):
            request = self.prepare(key, base)
            request[field] = '192.0.2.99'
            self.assert_error('INVALID_REQUEST', lambda: self.service.request('/v1/activate', request,
                                                                             observed_ip='192.0.2.11'))
            self.assertEqual(before, self.devices())
        request = self.prepare(key, base)
        self.service.request('/v1/activate', request, observed_ip='192.0.2.11')
        after = self.devices()
        self.assert_error('CHALLENGE_USED', lambda: self.service.request('/v1/activate', request,
                                                                       observed_ip='192.0.2.99'))
        self.assertEqual(after, self.devices())
        self.assertEqual(('192.0.2.10', '192.0.2.11'), (after[0]['first_ip'], after[0]['last_ip']))

    def test_proxy_ip_requires_explicit_trust_loopback_peer_and_single_valid_custom_header(self):
        custom = [('X-LJQC-Client-IP', '203.0.113.70'), ('X-Forwarded-For', '203.0.113.99')]
        self.assertEqual('127.0.0.1', self.handler(headers=custom).observed_client_ip())
        self.assertEqual('203.0.113.70', self.handler(trust=True, headers=custom).observed_client_ip())
        self.assertEqual('192.0.2.5', self.handler(peer='192.0.2.5', trust=True, headers=custom).observed_client_ip())
        self.assertEqual('127.0.0.1', self.handler(trust=True,
            headers=[('X-Forwarded-For', '203.0.113.99')]).observed_client_ip())
        invalid = ('203.0.113.70,203.0.113.71', 'example.test', '[2001:db8::1]', 'fe80::1%en0',
                   '203.0.113.70:9999', ' 203.0.113.70', '203.0.113.70\r\nHost: other')
        for value in invalid:
            with self.subTest(header=value):
                self.assertEqual('127.0.0.1', self.handler(trust=True,
                    headers=[('X-LJQC-Client-IP', value)]).observed_client_ip())
                self.assertEqual('', normalize_observed_ip(value))
        self.assertEqual('127.0.0.1', self.handler(trust=True, headers=[
            ('X-LJQC-Client-IP', '203.0.113.70'), ('X-LJQC-Client-IP', '203.0.113.71')]).observed_client_ip())
        self.assertEqual('2001:db8::70', self.handler(trust=True,
            headers=[('X-LJQC-Client-IP', '2001:0db8:0:0:0:0:0:0070')]).observed_client_ip())
        self.assertEqual('203.0.113.70', observed_request_ip(('127.0.0.1', 1234),
            {'X-LJQC-Client-IP': '203.0.113.70'}, trust_proxy_ip=True))
        self.assertEqual('127.0.0.1', observed_request_ip('127.0.0.1',
            {'X-LJQC-Client-IP': '203.0.113.70'}, trust_proxy_ip='false'))

    def test_http_adapter_passes_observed_peer_or_explicit_proxy_address_to_successful_activation(self):
        for trust, expected in ((False, '127.0.0.1'), (True, '203.0.113.40')):
            key, identity = fixtures.device('http-' + str(trust))
            request = self.prepare(key, self.base(identity))
            body = canonical_json(request)
            handler = self.handler(trust=trust, headers=[('Content-Length', str(len(body))),
                ('Content-Type', 'application/json'), ('X-LJQC-Client-IP', '203.0.113.40')])
            handler.path, handler.rfile = '/v1/activate', io.BytesIO(body)
            replies = []
            handler._send = lambda status, value: replies.append((status, value))
            handler.do_POST()
            self.assertEqual(200, replies[0][0])
            row = next(row for row in self.devices() if row['machine_hash'] == identity['machine_hash'])
            self.assertEqual((expected, expected), (row['first_ip'], row['last_ip']))

    def test_upgrade_of_legacy_authorization_db_preserves_records_and_adds_empty_ips(self):
        key, identity = fixtures.device('legacy')
        old_grant = self.exchange(key, self.base(identity), '192.0.2.123')
        before_license = self.service.store.get_license(self.issued['license_id'])
        original_config = self.config_path.read_bytes()
        original_trust = self.public_path.read_bytes()
        path = self.root / 'legacy.sqlite3'
        path.touch(mode=0o600)
        legacy_schema = SCHEMA.replace(" first_ip TEXT NOT NULL DEFAULT '', last_ip TEXT NOT NULL DEFAULT '',\n", '')
        with self.service.store.connection() as source:
            target = sqlite3.connect(path)
            try:
                target.executescript(legacy_schema)
                target.execute('PRAGMA application_id=' + str(APPLICATION_ID))
                target.execute('PRAGMA user_version=1')
                for table in ('licenses', 'devices', 'revoked_device_keys', 'challenges', 'audit_events'):
                    columns = [row[1] for row in target.execute('PRAGMA table_info(' + table + ')')]
                    rows = source.execute('SELECT ' + ','.join(columns) + ' FROM ' + table).fetchall()
                    target.executemany('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' +
                                       ','.join('?' for _ in columns) + ')', [tuple(row) for row in rows])
                target.commit()
            finally:
                target.close()
        config = replace(self.config, database_path=path)
        migrated = LicensingService(config, clock=self.clock)
        self.assertEqual(before_license, migrated.store.get_license(self.issued['license_id']))
        row = migrated.store.list_devices(self.issued['license_id'])[0]
        self.assertEqual(('', ''), (row['first_ip'], row['last_ip']))
        self.assertEqual(old_grant['signed']['activation_id'], row['id'])
        with migrated.store.connection() as connection:
            self.assertEqual(2, connection.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(APPLICATION_ID, connection.execute('PRAGMA application_id').fetchone()[0])
        self.assertEqual(original_config, self.config_path.read_bytes())
        self.assertEqual(original_trust, self.public_path.read_bytes())
        again = LicenseStore(config, clock=self.clock)
        self.assertEqual(row, again.list_devices(self.issued['license_id'])[0])
        original_service = self.service
        try:
            self.service = migrated
            self.exchange(key, self.base(identity, grant=old_grant), '203.0.113.80')
        finally:
            self.service = original_service
        self.assertEqual(('203.0.113.80', '203.0.113.80'),
                         (again.list_devices(self.issued['license_id'])[0]['first_ip'],
                          again.list_devices(self.issued['license_id'])[0]['last_ip']))

    def test_concurrent_ip_recording_keeps_ten_seat_limit_and_safe_license_query(self):
        identities = [fixtures.device('concurrent-ip-' + str(index)) for index in range(11)]
        gate = threading.Barrier(11)

        def activate(index):
            key, identity = identities[index]
            request = self.prepare(key, self.base(identity))
            gate.wait(timeout=10)
            try:
                self.service.request('/v1/activate', request, observed_ip='198.51.100.' + str(index + 1))
                return index
            except LicenseError as exc:
                self.assertEqual('DEVICE_LIMIT', exc.code)
                return None

        with ThreadPoolExecutor(max_workers=11) as pool:
            successful = [index for index in pool.map(activate, range(11)) if index is not None]
        self.assertEqual(10, len(successful))
        records = self.devices()
        self.assertEqual(10, len(records))
        for index in successful:
            expected = '198.51.100.' + str(index + 1)
            row = next(row for row in records if row['machine_hash'] == identities[index][1]['machine_hash'])
            self.assertEqual((expected, expected), (row['first_ip'], row['last_ip']))
        details = self.service.store.get_license(self.issued['license_id'])
        self.assertEqual(details, self.service.store.list_licenses()[0])
        self.assertEqual(10, details['active_devices'])
        self.assertNotIn('code_digest', details)
        for value in ('does-not-exist', "' OR 1=1 --", None, []):
            with self.assertRaises(ValueError):
                self.service.store.get_license(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
