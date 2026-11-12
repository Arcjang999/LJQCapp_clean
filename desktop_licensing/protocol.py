"""Strict, purpose-separated desktop license messages and trusted public keys."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


GRANT_PURPOSE = 'LJQCApp-desktop-license-grant-v1'
PROOF_PURPOSE = 'LJQCApp-desktop-license-proof-v1'
DENIAL_PURPOSE = 'LJQCApp-desktop-license-denial-v1'
MAX_OFFLINE_SECONDS = 24 * 60 * 60
MAX_CACHE_SECONDS = 5 * 60
MAX_MESSAGE_BYTES = 64 * 1024
_HASH = re.compile(r'^[0-9a-f]{64}$')
_IDENTIFIER = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')
_GRANT_FIELDS = frozenset((
    'format_version', 'purpose', 'license_id', 'activation_id', 'device_public_key',
    'machine_hash', 'license_generation', 'device_generation', 'issued_at',
    'expires_at', 'license_expires_at', 'request_nonce',
))


class LicenseError(ValueError):
    """A license operation failed without disclosing server or device secrets."""


class LicenseConfigurationError(LicenseError):
    pass


class LicenseProtocolError(LicenseError):
    pass


class LicenseUnavailable(LicenseError):
    """Only temporary connectivity, timeouts and HTTP 5xx use this exception."""


class LicenseRejected(LicenseError):
    def __init__(self, reason: str, message: str = '授权验证未通过，请联系软件维护人员。'):
        super().__init__(message)
        self.reason = reason


def canonical_json(value) -> bytes:
    """One encoding shared by challenge proofs and server-signed grants."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LicenseProtocolError('授权响应包含重复字段。')
        result[key] = value
    return result


def strict_json(raw: bytes, *, maximum: int = MAX_MESSAGE_BYTES):
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise LicenseProtocolError('授权响应大小无效。')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        pending = [(result, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 32 or (isinstance(value, float) and not math.isfinite(value)):
                raise ValueError('unsupported nesting/number')
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return result
    except (ValueError, UnicodeError, TypeError, RecursionError) as error:
        raise LicenseProtocolError('授权响应格式无效。') from error


def decode_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise LicenseProtocolError('授权公钥格式无效。') from error
    if len(decoded) != 32:
        raise LicenseProtocolError('授权公钥长度无效。')
    return decoded


def request_digest(request: dict) -> str:
    return hashlib.sha256(canonical_json(request)).hexdigest()


def proof_payload(action: str, challenge_id: str, nonce: str,
                  request_digest: str) -> bytes:
    if action not in ('activate', 'renew'):
        raise LicenseProtocolError('授权操作无效。')
    if (not isinstance(challenge_id, str) or not _IDENTIFIER.fullmatch(challenge_id)
            or not isinstance(nonce, str) or not 16 <= len(nonce) <= 256
            or any(ord(character) < 33 or ord(character) > 126 for character in nonce)
            or not isinstance(request_digest, str) or not _HASH.fullmatch(request_digest)):
        raise LicenseProtocolError('授权验证请求无效。')
    return canonical_json(dict(purpose=PROOF_PURPOSE, action=action,
        challenge_id=challenge_id, nonce=nonce, request_digest=request_digest))


@dataclass(frozen=True)
class TrustPolicy:
    origin: str
    keys: Mapping[str, str] = field(repr=False)
    api_prefix: str = '/licensing'
    timeout_seconds: float = 8
    offline_seconds: int = MAX_OFFLINE_SECONDS
    cache_seconds: int = MAX_CACHE_SECONDS
    max_response_bytes: int = MAX_MESSAGE_BYTES

    def __post_init__(self):
        try:
            url = urlsplit(self.origin)
            valid = (url.scheme == 'https' and bool(url.hostname) and not url.username
                     and not url.password and url.path in ('', '/')
                     and not url.query and not url.fragment
                     and all(32 < ord(character) < 127 for character in self.origin))
            # Accessing port also validates malformed or out-of-range values.
            _ = url.port
            if not valid or not isinstance(self.keys, Mapping) or not self.keys:
                raise ValueError('origin/keys')
            for key_id, value in self.keys.items():
                if not isinstance(key_id, str) or not _IDENTIFIER.fullmatch(key_id):
                    raise ValueError('key identifier')
                decode_public_key(value)
            if (not isinstance(self.api_prefix, str)
                    or not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', self.api_prefix)
                    or type(self.offline_seconds) is not int
                    or not 0 < self.offline_seconds <= MAX_OFFLINE_SECONDS
                    or type(self.cache_seconds) is not int
                    or not 0 <= self.cache_seconds <= MAX_CACHE_SECONDS
                    or isinstance(self.timeout_seconds, bool)
                    or not 0 < self.timeout_seconds <= 30
                    or type(self.max_response_bytes) is not int
                    or not 1024 <= self.max_response_bytes <= MAX_MESSAGE_BYTES):
                raise ValueError('policy limits')
        except (ValueError, TypeError, AttributeError) as error:
            raise LicenseConfigurationError('桌面授权服务尚未配置完成。') from error
        object.__setattr__(self, 'origin', self.origin.rstrip('/'))
        object.__setattr__(self, 'keys', dict(self.keys))

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'TrustPolicy':
        try:
            payload = strict_json(raw)
            if (not isinstance(payload, dict) or type(payload.get('format_version')) is not int
                    or payload.pop('format_version') != 1):
                raise ValueError('format')
            return cls(**payload)
        except (ValueError, TypeError) as error:
            raise LicenseConfigurationError('桌面授权服务尚未配置完成。') from error

    @classmethod
    def from_file(cls, path: str | Path) -> 'TrustPolicy':
        try:
            return cls.from_bytes(Path(path).read_bytes())
        except (OSError, ValueError, TypeError) as error:
            raise LicenseConfigurationError('桌面授权服务尚未配置完成。') from error


def verify_grant(envelope: dict, policy: TrustPolicy, *, device_public_key: str,
                 machine_hash: str, request_nonce: str | None = None,
                 now: float | None = None) -> dict:
    """Verify identity and a bounded signed lease; expired leases may be renewed.

    Passing ``now=None`` verifies cryptography and structure without requiring
    the old lease to remain current. The renewal server must consult its own
    database for current authorization, rather than trusting this old snapshot.
    """
    if (not isinstance(envelope, dict)
            or set(envelope) != {'signed', 'key_id', 'signature'}
            or not isinstance(envelope.get('signed'), dict)):
        raise LicenseProtocolError('授权凭据格式无效。')
    signed = envelope['signed']
    if set(signed) != _GRANT_FIELDS:
        raise LicenseProtocolError('授权凭据字段无效。')
    key_id = envelope['key_id']
    if not isinstance(key_id, str) or key_id not in policy.keys:
        raise LicenseProtocolError('授权签发密钥不受信任。')
    try:
        signature = base64.b64decode(envelope['signature'], validate=True)
        if len(signature) != 64:
            raise ValueError('signature size')
        Ed25519PublicKey.from_public_bytes(decode_public_key(policy.keys[key_id])).verify(
            signature, canonical_json(signed))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise LicenseProtocolError('授权凭据签名无效，请重新联网验证。') from error
    if signed['purpose'] != GRANT_PURPOSE or type(signed['format_version']) is not int or signed['format_version'] != 1:
        raise LicenseProtocolError('授权凭据用途无效。')
    if (signed['device_public_key'] != device_public_key
            or signed['machine_hash'] != machine_hash
            or not isinstance(machine_hash, str) or not _HASH.fullmatch(machine_hash)):
        raise LicenseProtocolError('授权凭据与本机不匹配。')
    decode_public_key(device_public_key)
    for field_name in ('license_id', 'activation_id'):
        if not isinstance(signed[field_name], str) or not _IDENTIFIER.fullmatch(signed[field_name]):
            raise LicenseProtocolError('授权凭据标识无效。')
    for field_name in ('license_generation', 'device_generation', 'issued_at', 'expires_at', 'license_expires_at'):
        if type(signed[field_name]) is not int or signed[field_name] < 0:
            raise LicenseProtocolError('授权凭据期限无效。')
    issued, expires, total = (signed[field_name] for field_name in ('issued_at', 'expires_at', 'license_expires_at'))
    if not issued < expires <= min(total, issued + policy.offline_seconds):
        raise LicenseProtocolError('授权凭据超过允许的离线期限。')
    nonce = signed['request_nonce']
    if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 256
            or any(ord(character) < 33 or ord(character) > 126 for character in nonce)
            or (request_nonce is not None and nonce != request_nonce)):
        raise LicenseProtocolError('授权响应与本次请求不匹配。')
    if now is not None and (now < issued - 120 or now >= expires or now >= total):
        raise LicenseProtocolError('本机授权需要重新联网验证。')
    return dict(signed)


def verify_denial(envelope: dict, policy: TrustPolicy, *, request_nonce: str) -> dict:
    """A signed rejection only restricts access and never enables an offline lease."""
    try:
        if (not isinstance(envelope, dict) or set(envelope) != {'signed', 'key_id', 'signature'}
                or envelope['key_id'] not in policy.keys):
            raise ValueError('envelope')
        signed = envelope['signed']
        if (not isinstance(signed, dict)
                or set(signed) != {'format_version', 'purpose', 'error', 'issued_at', 'request_nonce'}
                or type(signed['format_version']) is not int or signed['format_version'] != 1
                or signed['purpose'] != DENIAL_PURPOSE or signed['request_nonce'] != request_nonce
                or not isinstance(signed['error'], str) or not _IDENTIFIER.fullmatch(signed['error'])
                or type(signed['issued_at']) is not int or signed['issued_at'] < 0):
            raise ValueError('payload')
        signature = base64.b64decode(envelope['signature'], validate=True)
        Ed25519PublicKey.from_public_bytes(decode_public_key(policy.keys[envelope['key_id']])).verify(
            signature, canonical_json(signed))
        return dict(signed)
    except (InvalidSignature, ValueError, TypeError, KeyError) as error:
        raise LicenseProtocolError('授权拒绝响应的签名无效。') from error
