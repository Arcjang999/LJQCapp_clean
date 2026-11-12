"""Challenge-response activation and independently signed device leases."""
from __future__ import annotations

import base64
import ipaddress
import re
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desktop_licensing.protocol import GRANT_PURPOSE, PROOF_PURPOSE, canonical_json, proof_payload, request_digest
from .config import ConfigError, OFFLINE_SECONDS, ServerConfig, decode_key
from .store import LicenseStore

DENIAL_PURPOSE = 'LJQCApp-desktop-license-denial-v1'
CHALLENGE_SECONDS = 90
REFRESH_MAX_AGE = 30 * 86400
MAX_PENDING_CHALLENGES = 4096
GRANT_FIELDS = frozenset(('format_version', 'purpose', 'license_id', 'activation_id',
    'device_public_key', 'machine_hash', 'license_generation', 'device_generation',
    'issued_at', 'expires_at', 'license_expires_at', 'request_nonce'))

MESSAGES = {
    'INVALID_REQUEST': '验证请求无效，请重新打开软件后重试。',
    'INVALID_CODE': '验证码无效，请核对后重新输入。',
    'DISABLED': '此验证码已停用，请联系维护人员。',
    'EXPIRED': '使用期限已到，请联系维护人员。',
    'DEVICE_LIMIT': '此验证码已达到设备台数上限，请联系维护人员。',
    'DEVICE_IDENTITY_CHANGED': '此电脑的授权标识已变化，请联系维护人员恢复。',
    'DEVICE_UNBOUND': '此电脑已解除授权，请联系维护人员。',
    'GRANT_REVOKED': '授权状态已更新，请重新输入验证码。',
    'INVALID_GRANT': '授权凭证无效，请重新输入验证码。',
    'INVALID_PROOF': '此电脑的授权验证未通过，请重新打开软件后重试。',
    'CHALLENGE_EXPIRED': '本次验证已过期，请重试。',
    'CHALLENGE_USED': '本次验证已完成，请重新验证。',
    'SERVICE_BUSY': '验证服务繁忙，请稍后重试。',
}


class LicenseError(ValueError):
    def __init__(self, code, status=403):
        self.code, self.status = code, status
        super().__init__(MESSAGES[code])


def normalize_observed_ip(value):
    """Only server-observed, unscoped single IPv4/IPv6 addresses are stored."""
    if not isinstance(value, str) or not 1 <= len(value) <= 45 or value != value.strip() or '%' in value:
        return ''
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ''


class LicensingService:
    def __init__(self, config: ServerConfig, *, clock=time.time):
        self.config, self.clock = config, clock
        self.store = LicenseStore(config, clock=clock)

    def sign(self, payload):
        return dict(signed=payload, key_id=self.config.key_id,
                    signature=base64.b64encode(self.config.signing_key.sign(canonical_json(payload))).decode('ascii'))

    def denial(self, error: LicenseError, nonce=''):
        payload = dict(format_version=1, purpose=DENIAL_PURPOSE, error=error.code,
                       issued_at=int(self.clock()), request_nonce=nonce)
        return dict(error=error.code, message=str(error), denial=self.sign(payload))

    def _base(self, request, action):
        if not isinstance(request, dict) or action not in ('activate', 'renew'):
            raise LicenseError('INVALID_REQUEST', 400)
        fields = {'action', 'device_public_key', 'machine_hash', 'code' if action == 'activate' else 'grant'}
        value = {key: request[key] for key in fields if key in request}
        if set(value) != fields or value.get('action') != action:
            raise LicenseError('INVALID_REQUEST', 400)
        try:
            raw_key = decode_key(value['device_public_key'])
            if base64.b64encode(raw_key).decode('ascii') != value['device_public_key']:
                raise ValueError
            if not isinstance(value['machine_hash'], str) or not re.fullmatch('[0-9a-f]{64}', value['machine_hash']):
                raise ValueError
            if action == 'activate':
                self.store.digest(value['code'])
            else:
                if not isinstance(value['grant'], dict):
                    raise ValueError
            canonical_json(value)
        except (ValueError, TypeError, ConfigError) as exc:
            raise LicenseError('INVALID_REQUEST', 400) from exc
        return value

    def _verify_grant(self, envelope):
        try:
            if set(envelope) != {'signed', 'key_id', 'signature'} or envelope['key_id'] != self.config.key_id:
                raise ValueError
            payload = envelope['signed']
            if not isinstance(payload, dict) or set(payload) != GRANT_FIELDS:
                raise ValueError
            signature = decode_key(envelope['signature'], 64)
            self.config.signing_key.public_key().verify(signature, canonical_json(payload))
            if payload['format_version'] != 1 or payload['purpose'] != GRANT_PURPOSE:
                raise ValueError
            integers = ('license_generation', 'device_generation', 'issued_at', 'expires_at', 'license_expires_at')
            if any(type(payload[key]) is not int or payload[key] < 1 for key in integers):
                raise ValueError
            if (payload['issued_at'] > payload['expires_at'] or payload['expires_at'] > payload['license_expires_at']
                    or payload['expires_at'] - payload['issued_at'] > OFFLINE_SECONDS):
                raise ValueError
            for name in ('license_id', 'activation_id'):
                if not isinstance(payload[name], str) or not re.fullmatch('[0-9a-f]{32}', payload[name]):
                    raise ValueError
            now = int(self.clock())
            if payload['issued_at'] > now + 30 or now - payload['issued_at'] > REFRESH_MAX_AGE:
                raise ValueError
            return payload
        except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
            raise LicenseError('INVALID_GRANT') from exc

    def _license(self, connection, base):
        grant = None
        if base['action'] == 'activate':
            row = connection.execute('SELECT * FROM licenses WHERE code_digest=?',
                                     (self.store.digest(base['code']),)).fetchone()
            if row is None:
                raise LicenseError('INVALID_CODE')
        else:
            grant = self._verify_grant(base['grant'])
            if (grant['device_public_key'] != base['device_public_key']
                    or grant['machine_hash'] != base['machine_hash']):
                raise LicenseError('INVALID_PROOF')
            row = connection.execute('SELECT * FROM licenses WHERE id=?', (grant['license_id'],)).fetchone()
            if row is None:
                raise LicenseError('INVALID_GRANT')
        if not row['enabled']:
            raise LicenseError('DISABLED')
        if row['expires_at'] <= int(self.clock()):
            raise LicenseError('EXPIRED')
        if grant and not row['min_generation'] <= grant['license_generation'] <= row['generation']:
            raise LicenseError('GRANT_REVOKED')
        return row, grant

    def _renew_device(self, connection, license_row, base, grant):
        device = connection.execute('SELECT * FROM devices WHERE id=? AND license_id=?',
                                    (grant['activation_id'], license_row['id'])).fetchone()
        if device is None or not device['active'] or device['generation'] != grant['device_generation']:
            raise LicenseError('DEVICE_UNBOUND')
        if device['public_key'] != base['device_public_key'] or device['machine_hash'] != base['machine_hash']:
            raise LicenseError('INVALID_PROOF')
        return device

    def challenge(self, request):
        action = request.get('action') if isinstance(request, dict) else None
        base = self._base(request, action)
        if set(request) != set(base):
            raise LicenseError('INVALID_REQUEST', 400)
        now = int(self.clock())
        with self.store.write() as connection:
            connection.execute('DELETE FROM challenges WHERE expires_at<?', (now - 60,))
            if connection.execute('SELECT COUNT(*) FROM challenges').fetchone()[0] >= MAX_PENDING_CHALLENGES:
                raise LicenseError('SERVICE_BUSY', 429)
            license_row, grant = self._license(connection, base)
            if grant:
                self._renew_device(connection, license_row, base, grant)
            challenge_id, nonce = secrets.token_hex(16), secrets.token_urlsafe(32)
            digest = request_digest(base)
            connection.execute('INSERT INTO challenges(id,nonce,action,request_digest,license_id,device_public_key,'
                'machine_hash,activation_id,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (challenge_id, nonce, action, digest, license_row['id'], base['device_public_key'],
                 base['machine_hash'], grant['activation_id'] if grant else None, now + CHALLENGE_SECONDS))
        return dict(challenge_id=challenge_id, nonce=nonce, expires_at=now + CHALLENGE_SECONDS, request_digest=digest)

    def complete(self, request, action, *, observed_ip=''):
        base = self._base(request, action)
        if set(request) != set(base) | {'challenge_id', 'nonce', 'proof'}:
            raise LicenseError('INVALID_REQUEST', 400)
        challenge_id, nonce = request['challenge_id'], request['nonce']
        if (not isinstance(challenge_id, str) or not re.fullmatch('[0-9a-f]{32}', challenge_id)
                or not isinstance(nonce, str) or not re.fullmatch('[A-Za-z0-9_-]{43}', nonce)):
            raise LicenseError('INVALID_REQUEST', 400)
        failure, payload = None, None
        with self.store.write() as connection:
            challenge = connection.execute('SELECT * FROM challenges WHERE id=?', (challenge_id,)).fetchone()
            if challenge is None:
                raise LicenseError('CHALLENGE_EXPIRED')
            if challenge['consumed']:
                raise LicenseError('CHALLENGE_USED')
            # Commit consumption even when proof/seat/state checks fail. Retrying a
            # lost successful response uses a fresh challenge and the same binding.
            connection.execute('UPDATE challenges SET consumed=1 WHERE id=?', (challenge_id,))
            try:
                digest = request_digest(base)
                if challenge['expires_at'] <= int(self.clock()):
                    raise LicenseError('CHALLENGE_EXPIRED')
                if challenge['nonce'] != nonce or challenge['action'] != action or challenge['request_digest'] != digest:
                    raise LicenseError('INVALID_PROOF')
                try:
                    Ed25519PublicKey.from_public_bytes(decode_key(base['device_public_key'])).verify(
                        decode_key(request['proof'], 64), proof_payload(action, challenge_id, nonce, digest))
                except (ValueError, TypeError, InvalidSignature) as exc:
                    raise LicenseError('INVALID_PROOF') from exc
                license_row, grant = self._license(connection, base)
                if license_row['id'] != challenge['license_id']:
                    raise LicenseError('INVALID_PROOF')
                if action == 'renew':
                    device = self._renew_device(connection, license_row, base, grant)
                else:
                    device = self._activate_device(connection, license_row, base)
                now = int(self.clock())
                current_ip = normalize_observed_ip(observed_ip)
                connection.execute("UPDATE devices SET last_seen_at=?,first_ip=CASE WHEN first_ip='' AND ?<>'' THEN ? "
                    "ELSE first_ip END,last_ip=CASE WHEN ?<>'' THEN ? ELSE last_ip END WHERE id=?",
                    (now, current_ip, current_ip, current_ip, current_ip, device['id']))
                payload = dict(format_version=1, purpose=GRANT_PURPOSE, license_id=license_row['id'],
                    activation_id=device['id'], device_public_key=device['public_key'], machine_hash=device['machine_hash'],
                    license_generation=license_row['generation'], device_generation=device['generation'], issued_at=now,
                    expires_at=min(now + OFFLINE_SECONDS, license_row['expires_at']),
                    license_expires_at=license_row['expires_at'], request_nonce=nonce)
                self.store.event(connection, action, license_row['id'], device['id'])
            except LicenseError as exc:
                failure = exc
                self.store.event(connection, 'deny-' + exc.code, challenge['license_id'])
        if failure:
            raise failure
        return {'grant': self.sign(payload)}

    def _activate_device(self, connection, license_row, base):
        license_id = license_row['id']
        if connection.execute('SELECT 1 FROM revoked_device_keys WHERE license_id=? AND public_key=?',
                              (license_id, base['device_public_key'])).fetchone():
            raise LicenseError('DEVICE_UNBOUND')
        device = connection.execute('SELECT * FROM devices WHERE license_id=? AND machine_hash=?',
                                    (license_id, base['machine_hash'])).fetchone()
        other = connection.execute('SELECT * FROM devices WHERE license_id=? AND public_key=?',
                                   (license_id, base['device_public_key'])).fetchone()
        if other and other['machine_hash'] != base['machine_hash']:
            raise LicenseError('INVALID_PROOF')
        if device and device['active']:
            if device['public_key'] != base['device_public_key']:
                raise LicenseError('DEVICE_IDENTITY_CHANGED')
            return device
        count = connection.execute('SELECT COUNT(*) FROM devices WHERE license_id=? AND active=1', (license_id,)).fetchone()[0]
        if count >= license_row['max_devices']:
            raise LicenseError('DEVICE_LIMIT')
        now = int(self.clock())
        if device:
            connection.execute('UPDATE devices SET active=1,public_key=?,generation=generation+1,last_seen_at=? WHERE id=?',
                               (base['device_public_key'], now, device['id']))
            identity = device['id']
        else:
            identity = secrets.token_hex(16)
            connection.execute('INSERT INTO devices(id,license_id,machine_hash,public_key,active,created_at,last_seen_at) '
                'VALUES(?,?,?,?,1,?,?)', (identity, license_id, base['machine_hash'], base['device_public_key'], now, now))
        return connection.execute('SELECT * FROM devices WHERE id=?', (identity,)).fetchone()

    def request(self, path, payload, *, observed_ip=''):
        if path == '/v1/challenge':
            return self.challenge(payload)
        if path in ('/v1/activate', '/v1/renew'):
            return self.complete(payload, path.rsplit('/', 1)[-1], observed_ip=observed_ip)
        raise LicenseError('INVALID_REQUEST', 404)
