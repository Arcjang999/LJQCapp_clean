"""Real signatures and isolated server/device state exercise the desktop gate."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
from email.message import Message
from io import BytesIO
import json
import os
from pathlib import Path
import ssl
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_licensing.client import LicenseClient, _NoRedirect
from desktop_licensing.device import default_state_dir, load_or_create_device, read_private_json
from desktop_licensing.protocol import (
    LicenseConfigurationError, LicenseProtocolError, LicenseRejected, LicenseUnavailable,
    TrustPolicy, canonical_json, strict_json, verify_grant,
)
from licensing_server.config import ServerConfig
from licensing_server.service import LicenseError as ServerError, LicensingService


class Clock:
    def __init__(self):
        self.wall = datetime.fromisoformat('2026-11-12T12:00:00+08:00').timestamp()
        self.mono = 1000.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class Adapter:
    def __init__(self, service):
        self.service = service
        self.calls = []
        self.unavailable = False
        self.corrupt = False
        self.lose_next_completion = False

    def __call__(self, endpoint, payload):
        self.calls.append((endpoint, deepcopy(payload)))
        if self.unavailable:
            raise LicenseUnavailable('isolated network failure')
        try:
            response = self.service.request(endpoint, payload)
        except ServerError as error:
            raise LicenseRejected(error.code, str(error)) from error
        if endpoint != '/v1/challenge' and self.lose_next_completion:
            self.lose_next_completion = False
            raise LicenseUnavailable('response lost after commit')
        if endpoint != '/v1/challenge' and self.corrupt:
            response['grant']['signed']['expires_at'] += 1
        return response


class DesktopLicensingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        config = ServerConfig(self.root / 'server' / 'licenses.db', 'isolated-test',
                              Ed25519PrivateKey.generate(), b'p' * 32)
        self.service = LicensingService(config, clock=lambda: self.clock.wall)
        self.policy = TrustPolicy(**{key: value for key, value in config.public_trust(
            'https://license.example.invalid').items() if key != 'format_version'})
        self.issued = self.service.store.issue('isolated customer')
        self.adapter = Adapter(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, index=0, **kwargs):
        return LicenseClient(self.policy, self.root / ('client-' + str(index)),
            transport=kwargs.pop('transport', self.adapter),
            clock=kwargs.pop('clock', lambda: self.clock.wall),
            monotonic=lambda: self.clock.mono,
            machine_hash_provider=kwargs.pop('machine_hash_provider', lambda: f'{index + 1:064x}'), **kwargs)

    def test_online_activation_has_fixed_deadline_and_does_not_save_code(self):
        result = self.client().check(self.issued['code'])
        self.assertTrue(result.allowed, result)
        self.assertEqual('online', result.status)
        self.assertEqual(self.issued['expires_at'], result.expires_at)
        self.assertEqual(int(self.clock.wall + 86400), result.offline_until)
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])
        for path in (self.root / 'client-0').glob('*.json'):
            self.assertNotIn(self.issued['code'], path.read_text())
            if os.name != 'nt':
                self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_new_session_checks_online_and_same_device_reinstall_is_one_seat(self):
        original = self.client()
        self.assertTrue(original.check(self.issued['code']).allowed)
        count = len(self.adapter.calls)
        self.assertEqual('cached', original.check().status)
        self.assertEqual(count, len(self.adapter.calls))
        replacement = self.client()
        self.assertEqual('online', replacement.check().status)
        self.assertGreater(len(self.adapter.calls), count)
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])

    def test_short_cache_never_delays_online_rejection_beyond_five_minutes(self):
        client = self.client()
        self.assertTrue(client.check(self.issued['code']).allowed)
        self.service.store.set_enabled(self.issued['license_id'], False)
        self.clock.advance(299)
        self.assertEqual('cached', client.check().status)
        self.clock.advance(1)
        result = client.check()
        self.assertFalse(result.allowed)
        self.assertEqual('DISABLED', result.reason)

    def test_lost_successful_activation_response_can_retry_without_consuming_extra_seat(self):
        self.adapter.lose_next_completion = True
        client = self.client()
        self.assertFalse(client.check(self.issued['code']).allowed)
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])
        self.assertTrue(client.check(self.issued['code']).allowed)
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])

    def test_eleventh_device_is_rejected_and_existing_device_still_renews(self):
        for index in range(10):
            self.assertTrue(self.client(index).check(self.issued['code']).allowed)
        rejected = self.client(10).check(self.issued['code'])
        self.assertFalse(rejected.allowed)
        self.assertEqual('DEVICE_LIMIT', rejected.reason)
        self.assertTrue(self.client(0).check().allowed)
        self.assertEqual(10, self.service.store.list_licenses()[0]['active_devices'])

    def test_offline_lease_is_valid_before_24_hours_and_denied_at_exact_boundary(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        self.adapter.unavailable = True
        self.clock.advance(86399)
        self.assertEqual('offline', self.client().check().status)
        self.clock.advance(1)
        result = self.client().check()
        self.assertFalse(result.allowed)
        self.assertEqual('offline_expired', result.reason)

    def test_license_end_clamps_offline_lease_and_is_not_extended_by_cache(self):
        self.clock.wall = self.issued['expires_at'] - 60
        client = self.client()
        result = client.check(self.issued['code'])
        self.assertTrue(result.allowed)
        self.assertEqual(result.expires_at, result.offline_until)
        self.clock.advance(60)
        self.adapter.unavailable = True
        self.assertFalse(client.check().allowed)

    def test_extension_recovers_automatically_even_after_old_local_deadline(self):
        self.clock.wall = self.issued['expires_at'] - 60
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        self.clock.advance(120)
        new_end = self.issued['expires_at'] + 86400
        self.service.store.extend(self.issued['license_id'], new_end)
        renewed = self.client().check()
        self.assertTrue(renewed.allowed, renewed)
        self.assertEqual(new_end, renewed.expires_at)

    def test_explicit_server_disable_blocks_subsequent_offline_fallback(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        self.service.store.set_enabled(self.issued['license_id'], False)
        rejected = self.client().check()
        self.assertFalse(rejected.allowed)
        self.assertEqual('DISABLED', rejected.reason)
        self.adapter.unavailable = True
        self.assertFalse(self.client().check().allowed)

    def test_invalid_online_signature_blocks_subsequent_offline_fallback(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        self.adapter.corrupt = True
        rejected = self.client().check()
        self.assertFalse(rejected.allowed)
        self.assertEqual('invalid_response', rejected.reason)
        self.adapter.unavailable = True
        self.assertFalse(self.client().check().allowed)

    def test_validly_signed_wrong_nonce_or_overlong_lease_is_still_rejected(self):
        for change in ('nonce', 'lease'):
            with self.subTest(change=change):
                client = self.client(4 if change == 'nonce' else 5)
                def altered(endpoint, payload):
                    result = self.adapter(endpoint, payload)
                    if endpoint != '/v1/challenge':
                        signed = result['grant']['signed']
                        if change == 'nonce':
                            signed['request_nonce'] = 'different-response-nonce'
                        else:
                            signed['expires_at'] = signed['issued_at'] + 86401
                        result['grant'] = self.service.sign(signed)
                    return result
                client.transport = altered
                result = client.check(self.issued['code'])
                self.assertFalse(result.allowed)
                self.assertEqual('invalid_response', result.reason)

    def test_copying_all_device_files_to_another_machine_does_not_authorize_it(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        copied = self.client(machine_hash_provider=lambda: 'b' * 64)
        calls = len(self.adapter.calls)
        result = copied.check()
        self.assertFalse(result.allowed)
        self.assertEqual('device_identity', result.reason)
        self.assertEqual(calls, len(self.adapter.calls))

    def test_clock_rollback_blocks_offline_in_existing_and_fresh_sessions(self):
        client = self.client()
        self.assertTrue(client.check(self.issued['code']).allowed)
        self.clock.advance(3600)
        self.adapter.unavailable = True
        self.assertTrue(client.check().allowed)
        self.clock.wall -= 1800
        self.assertEqual('clock_rollback', client.check().reason)
        self.assertEqual('clock_rollback', self.client().check().reason)

    def test_initial_slow_local_clock_does_not_extend_lease_across_restarts(self):
        local_clock = lambda: self.clock.wall - 300
        self.assertTrue(self.client(clock=local_clock).check(self.issued['code']).allowed)
        self.adapter.unavailable = True
        self.clock.advance(86399)
        self.assertTrue(self.client(clock=local_clock).check().allowed)
        self.clock.advance(1)
        self.assertFalse(self.client(clock=local_clock).check().allowed)

    def test_bad_signature_in_saved_grant_cannot_use_in_process_short_cache(self):
        client = self.client()
        self.assertTrue(client.check(self.issued['code']).allowed)
        path = self.root / 'client-0' / 'license.json'
        value = json.loads(path.read_text())
        value['grant']['signed']['license_expires_at'] += 86400
        path.write_text(json.dumps(value))
        result = client.check()
        self.assertFalse(result.allowed)
        self.assertEqual('invalid_grant', result.reason)

    def test_5xx_is_temporary_but_tls_failure_and_http_rejection_are_not(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        headers = Message()
        error = HTTPError('https://license.example.invalid', 503, 'unavailable', headers, BytesIO(b''))
        with patch('desktop_licensing.client.build_opener') as opener:
            opener.return_value.open.side_effect = error
            client = self.client(transport=None)
            self.assertEqual('offline', client.check().status)
        with patch('desktop_licensing.client.build_opener') as opener:
            opener.return_value.open.side_effect = URLError(ssl.SSLCertVerificationError('untrusted'))
            result = self.client(transport=None).check()
            self.assertFalse(result.allowed)
            self.assertEqual('invalid_response', result.reason)
        self.adapter.unavailable = True
        self.assertFalse(self.client().check().allowed)

    def test_http_signed_denial_has_safe_specific_message(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        response = self.service.denial(ServerError('DEVICE_LIMIT'), '')
        error = HTTPError('https://license.example.invalid', 403, 'denied', Message(),
                          BytesIO(canonical_json(response)))
        with patch('desktop_licensing.client.build_opener') as opener:
            opener.return_value.open.side_effect = error
            result = self.client(transport=None).check()
            self.assertFalse(result.allowed)
            self.assertIn('台数已满', result.message)
            self.assertEqual('DEVICE_LIMIT', result.reason)

    def test_redirects_and_overlarge_responses_cannot_release_cached_access(self):
        with self.assertRaises(LicenseProtocolError):
            _NoRedirect().redirect_request(None, None, 302, 'moved', {}, 'https://other.invalid')
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        headers = Message()
        headers['Content-Type'] = 'application/json'
        headers['Content-Length'] = '65537'
        with patch('desktop_licensing.client.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.headers = headers
            result = self.client(transport=None).check()
            self.assertFalse(result.allowed)
            self.assertEqual('invalid_response', result.reason)
            response.read.assert_not_called()

    def test_corrupt_time_anchor_requires_network_reactivation_and_can_be_repaired(self):
        self.assertTrue(self.client().check(self.issued['code']).allowed)
        path = self.root / 'client-0' / 'license.json'
        value = json.loads(path.read_text())
        value['clock_anchor']['grant_digest'] = '0' * 64
        path.write_text(json.dumps(value))
        client = self.client()
        self.assertEqual('invalid_clock_anchor', client.check().reason)
        self.assertTrue(client.check(self.issued['code']).allowed)
        self.assertEqual(1, self.service.store.list_licenses()[0]['active_devices'])

    def test_missing_or_insecure_trust_configuration_fails_closed(self):
        path = self.root / 'not-created'
        result = LicenseClient(None, path).check()
        self.assertFalse(result.allowed)
        self.assertEqual('unconfigured', result.status)
        self.assertFalse(path.exists())
        with self.assertRaises(LicenseConfigurationError):
            TrustPolicy.from_file(self.root / 'missing.json')
        encoded = canonical_json(self.service.config.public_trust('https://license.example.invalid'))
        path = self.root / 'trust.json'
        path.write_bytes(encoded)
        self.assertEqual(self.policy, TrustPolicy.from_bytes(encoded))
        self.assertEqual(TrustPolicy.from_file(path), TrustPolicy.from_bytes(encoded))
        for raw in (b'{}', b'{"format_version":true}', b'{"format_version":1,"format_version":1}'):
            with self.assertRaises(LicenseConfigurationError):
                TrustPolicy.from_bytes(raw)
        for origin, keys in (('http://license.example.invalid', self.policy.keys),
                             ('https://license.example.invalid', {})):
            with self.assertRaises(LicenseConfigurationError):
                TrustPolicy(origin, keys)

    def test_duplicate_or_deep_json_and_business_location_override_are_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'[' * 2000 + b'0' + b']' * 2000):
            with self.assertRaises(LicenseProtocolError):
                strict_json(raw)
        with patch.dict(os.environ, {'LJQC_DATA_ROOT': str(self.root / 'business')}):
            self.assertFalse(default_state_dir().is_relative_to(self.root / 'business'))
        identity = load_or_create_device(self.root / 'manual-device', machine_hash='c' * 64)
        self.assertEqual(identity.public_key,
            load_or_create_device(self.root / 'manual-device', machine_hash='c' * 64).public_key)


if __name__ == '__main__':
    unittest.main(verbosity=2)
