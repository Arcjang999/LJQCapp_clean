"""Online activation with signed, machine-bound, at-most-24-hour offline leases."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import math
from pathlib import Path
import socket
import ssl
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

from .device import (DeviceIdentity, DeviceIdentityError, ensure_private_directory,
                     load_or_create_device, machine_fingerprint, read_private_json,
                     state_lock, write_private_json)
from .protocol import (LicenseConfigurationError, LicenseError, LicenseProtocolError,
                       LicenseRejected, LicenseUnavailable, TrustPolicy,
                       canonical_json, proof_payload, request_digest, strict_json,
                       verify_denial, verify_grant)


@dataclass(frozen=True)
class AccessResult:
    allowed: bool
    status: str
    message: str
    expires_at: int | None = None
    offline_until: int | None = None
    reason: str = ''


_DENIAL_MESSAGES = {
    'invalid_code': '验证码无效，请核对后重试。',
    'expired': '使用期限已到，请联系软件维护人员。',
    'license_expired': '使用期限已到，请联系软件维护人员。',
    'revoked': '授权已停用，请联系软件维护人员。',
    'license_revoked': '授权已停用，请联系软件维护人员。',
    'device_revoked': '本机授权已停用，请联系软件维护人员。',
    'device_limit': '此验证码的使用台数已满，请联系软件维护人员。',
    'device_limit_reached': '此验证码的使用台数已满，请联系软件维护人员。',
    'rate_limited': '验证次数较多，请稍后重试。',
    'challenge_expired': '本次验证已超时，请重试。',
    'disabled': '此验证码已停用，请联系软件维护人员。',
    'device_identity_changed': '此电脑的授权标识已变化，请联系软件维护人员恢复。',
    'device_unbound': '此电脑已解除授权，请联系软件维护人员。',
    'grant_revoked': '授权状态已更新，请重新输入验证码。',
    'invalid_grant': '授权凭据无效，请重新输入验证码。',
    'invalid_proof': '本机授权验证未通过，请重新打开软件后重试。',
    'challenge_used': '本次验证已完成，请重新验证。',
    'service_busy': '验证服务繁忙，请稍后重试。',
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Authorization material must never be forwarded to a redirected host.
        raise LicenseProtocolError('授权服务地址发生跳转，请联系软件维护人员。')


class LicenseClient:
    """Keep one instance per app session; a new session always checks online.

    Explicit injected transport and clocks are for isolated QA. There is no
    environment-variable switch that makes an unconfigured desktop trusted.
    """
    def __init__(self, policy: TrustPolicy | None, state_dir: str | Path, *,
                 transport: Callable | None = None, clock: Callable = time.time,
                 monotonic: Callable = time.monotonic,
                 machine_hash_provider: Callable = machine_fingerprint):
        self.policy = policy
        self.state_dir = Path(state_dir)
        self.transport = transport
        self.clock = clock
        self.monotonic = monotonic
        self.machine_hash_provider = machine_hash_provider
        self._last_network_mono = None
        self._last_network_digest = None
        self._last_network_online = False
        self._anchor = None
        self._previous_wall = None
        self._previous_mono = None
        self._identity = None
        self._identity_stat = None
        self._identity_checked_mono = None
        self._last_seen_memory = 0

    def _post(self, endpoint: str, payload: dict) -> dict:
        if self.transport is not None:
            if callable(self.transport):
                return self.transport(endpoint, payload)
            return self.transport.post(endpoint, payload)
        policy = self.policy
        if policy is None:
            raise LicenseConfigurationError('桌面授权服务尚未配置完成。')
        context = ssl.create_default_context(cafile=certifi.where())
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
        request = Request(policy.origin + policy.api_prefix + endpoint,
            data=canonical_json(payload), method='POST',
            headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                     'User-Agent': 'LJQCApp-License/1'})
        try:
            with opener.open(request, timeout=policy.timeout_seconds) as response:
                length = response.headers.get('Content-Length')
                if length is not None:
                    try:
                        declared_length = int(length)
                    except ValueError as error:
                        raise LicenseProtocolError('授权响应大小无效。') from error
                    if declared_length < 0 or declared_length > policy.max_response_bytes:
                        raise LicenseProtocolError('授权响应超过允许大小。')
                if response.headers.get_content_type() != 'application/json':
                    raise LicenseProtocolError('授权响应格式无效。')
                body = strict_json(response.read(policy.max_response_bytes + 1),
                                   maximum=policy.max_response_bytes)
                if not isinstance(body, dict):
                    raise LicenseProtocolError('授权响应格式无效。')
                return body
        except HTTPError as error:
            if 500 <= error.code <= 599:
                raise LicenseUnavailable('授权服务暂时不可用。') from error
            reason = 'http_' + str(error.code)
            try:
                rejection = strict_json(error.read(policy.max_response_bytes + 1),
                                        maximum=policy.max_response_bytes)
            except (LicenseError, OSError):
                rejection = None
            if isinstance(rejection, dict) and 'denial' in rejection:
                verified = verify_denial(rejection['denial'], policy,
                                          request_nonce=payload.get('nonce', ''))
                if verified['error'] != rejection.get('error'):
                    raise LicenseProtocolError('授权拒绝响应与本次验证不匹配。')
                reason = verified['error']
            elif isinstance(rejection, dict):
                # Unsigned nginx/application errors remain generic hard rejections.
                pass
            raise LicenseRejected(reason, _DENIAL_MESSAGES.get(reason.lower(),
                '授权验证未通过，请联系软件维护人员。')) from error
        except (ssl.SSLCertVerificationError, ssl.CertificateError, ssl.SSLError) as error:
            raise LicenseProtocolError('授权服务的安全连接验证失败。') from error
        except URLError as error:
            if isinstance(error.reason, (ssl.SSLError, ssl.CertificateError)):
                raise LicenseProtocolError('授权服务的安全连接验证失败。') from error
            if isinstance(error.reason, (TimeoutError, socket.timeout, socket.gaierror, ConnectionError)):
                raise LicenseUnavailable('暂时无法连接授权服务。') from error
            # Network routing and unreachable-host errors also arrive as OSError.
            if isinstance(error.reason, OSError):
                raise LicenseUnavailable('暂时无法连接授权服务。') from error
            raise LicenseProtocolError('授权服务连接未完成。') from error
        except (TimeoutError, socket.timeout, ConnectionError) as error:
            raise LicenseUnavailable('暂时无法连接授权服务。') from error

    @staticmethod
    def _grant_digest(grant: dict) -> str:
        return hashlib.sha256(canonical_json(grant)).hexdigest()

    def _clock_state(self, state: dict, signed: dict | None):
        wall, monotonic = float(self.clock()), float(self.monotonic())
        seen = state.get('last_seen_at', 0)
        last_wall = state.get('last_wall_at', wall)
        if (not math.isfinite(wall) or not math.isfinite(monotonic)
                or type(seen) not in (int, float) or not math.isfinite(seen) or seen < 0
                or type(last_wall) not in (int, float) or not math.isfinite(last_wall)):
            raise LicenseProtocolError('本机授权时间记录无效。')
        seen = max(seen, self._last_seen_memory)
        rollback = wall < last_wall - 120
        corrected = wall
        if signed is not None:
            checkpoint = state.get('clock_anchor')
            if (not isinstance(checkpoint, dict)
                    or set(checkpoint) != {'wall_at_online', 'server_at_online', 'grant_digest'}
                    or checkpoint['grant_digest'] != self._grant_digest(state['grant'])
                    or any(type(checkpoint[field]) not in (int, float)
                           or not math.isfinite(checkpoint[field])
                           for field in ('wall_at_online', 'server_at_online'))
                    or not signed['issued_at'] <= checkpoint['server_at_online'] <= signed['issued_at'] + 60):
                raise LicenseProtocolError('本机授权时间锚点无效，请重新输入验证码联网验证。')
            corrected = wall + checkpoint['server_at_online'] - checkpoint['wall_at_online']
            rollback = rollback or corrected < seen - 120
        if self._previous_wall is not None and self._previous_mono is not None:
            elapsed = max(0, monotonic - self._previous_mono)
            rollback = rollback or wall - self._previous_wall < elapsed - 120
        effective = max(corrected, float(seen))
        if self._anchor is not None:
            digest, server_time, anchor_mono = self._anchor
            if signed is not None and digest == self._grant_digest(state['grant']):
                effective = max(effective, server_time + max(0, monotonic - anchor_mono))
        self._previous_wall, self._previous_mono = wall, monotonic
        return effective, monotonic, rollback

    @staticmethod
    def _result(allowed: bool, status: str, message: str, signed: dict | None = None,
                reason: str = '') -> AccessResult:
        return AccessResult(allowed, status, message,
            signed['license_expires_at'] if signed else None,
            signed['expires_at'] if signed else None, reason)

    def _persist_denial(self, path: Path, state: dict, reason: str):
        state['blocked_reason'] = reason
        write_private_json(path, state)
        self._last_network_mono = None
        self._last_network_digest = None

    def _remember_seen(self, path: Path, state: dict, effective: float):
        self._last_seen_memory = max(self._last_seen_memory, effective)
        # Every check uses the in-memory/monotonic high-water mark. Persist it
        # at least every 30 seconds without fsyncing on each business commit.
        if effective - state.get('last_seen_at', 0) >= 30:
            state['last_seen_at'] = effective
            state['last_wall_at'] = self.clock()
            write_private_json(path, state)

    def _device_identity(self) -> DeviceIdentity:
        path = self.state_dir / 'device.json'
        if self.state_dir.is_symlink() or path.is_symlink():
            raise DeviceIdentityError('本机授权资料目录无效。')
        signature = None
        if path.exists():
            info = path.stat()
            signature = (info.st_ino, info.st_size, info.st_mtime_ns)
        monotonic = self.monotonic()
        if (self._identity is None or signature != self._identity_stat
                or self._identity_checked_mono is None
                or monotonic - self._identity_checked_mono >= 300):
            directory = ensure_private_directory(self.state_dir)
            self._identity = load_or_create_device(directory, machine_hash=self.machine_hash_provider())
            info = path.stat()
            self._identity_stat = (info.st_ino, info.st_size, info.st_mtime_ns)
            self._identity_checked_mono = monotonic
        return self._identity

    def _exchange(self, action: str, base: dict, identity: DeviceIdentity) -> dict:
        challenge = self._post('/v1/challenge', base)
        if (not isinstance(challenge, dict)
                or set(challenge) != {'challenge_id', 'nonce', 'expires_at', 'request_digest'}
                or type(challenge['expires_at']) is not int or challenge['expires_at'] <= 0):
            raise LicenseProtocolError('授权验证请求无效。')
        digest = request_digest(base)
        if (not isinstance(challenge['request_digest'], str)
                or not hmac.compare_digest(challenge['request_digest'], digest)):
            raise LicenseProtocolError('授权验证请求与本机请求不匹配。')
        proof = identity.sign(proof_payload(action, challenge['challenge_id'], challenge['nonce'], digest))
        response = self._post('/v1/' + action, dict(base,
            challenge_id=challenge['challenge_id'], nonce=challenge['nonce'], proof=proof))
        if not isinstance(response, dict) or set(response) != {'grant'}:
            raise LicenseProtocolError('授权响应格式无效。')
        verify_grant(response['grant'], self.policy, device_public_key=identity.public_key,
                     machine_hash=identity.machine_hash, request_nonce=challenge['nonce'])
        return response['grant']

    def check(self, code: str | None = None) -> AccessResult:
        if self.policy is None:
            return self._result(False, 'unconfigured', '桌面授权服务尚未配置完成。', reason='unconfigured')
        try:
            identity = self._device_identity()
            with state_lock(self.state_dir, 'client.lock'):
                return self._check_locked(identity, code)
        except DeviceIdentityError as error:
            return self._result(False, 'denied', str(error), reason='device_identity')
        except (LicenseError, OSError, TypeError, ValueError, RecursionError):
            return self._result(False, 'denied', '本机授权检查未完成，请联系软件维护人员。',
                                reason='local_state_invalid')

    def _check_locked(self, identity: DeviceIdentity, code: str | None) -> AccessResult:
        path = self.state_dir / 'license.json'
        state = read_private_json(path) or {'format_version': 1}
        if type(state.get('format_version')) is not int or state['format_version'] != 1:
            raise LicenseProtocolError('本机授权资料版本无效。')
        if code is not None and (not isinstance(code, str) or len(code) > 256):
            return self._result(False, 'needs_code', '请填写有效的验证码。', reason='invalid_code')
        code = code.strip() if code is not None else None
        if code == '':
            return self._result(False, 'needs_code', '请填写验证码。', reason='needs_code')
        grant = state.get('grant')
        signed = None
        if grant is not None:
            try:
                signed = verify_grant(grant, self.policy, device_public_key=identity.public_key,
                                      machine_hash=identity.machine_hash)
            except LicenseProtocolError:
                if not code:
                    self._persist_denial(path, state, 'invalid_grant')
                    return self._result(False, 'denied', '本机授权凭据无效，请重新输入验证码联网验证。',
                                        reason='invalid_grant')
        if not code and signed is None:
            return self._result(False, 'needs_code', '请输入验证码并联网激活。', reason='needs_code')
        try:
            effective, monotonic, rollback = self._clock_state(state, signed)
        except LicenseProtocolError:
            if not code:
                self._persist_denial(path, state, 'invalid_clock_anchor')
                return self._result(False, 'denied', '本机授权时间记录无效，请重新输入验证码联网验证。',
                                    signed, reason='invalid_clock_anchor')
            # Explicitly entering the code can repair a corrupt local time
            # checkpoint only through a fresh authenticated server exchange.
            state, grant, signed = {'format_version': 1}, None, None
            effective, monotonic, rollback = self._clock_state(state, None)
        current = bool(signed and effective < signed['expires_at'] and effective < signed['license_expires_at'])
        digest = self._grant_digest(grant) if signed is not None else None
        short_cache = (not code and current and not rollback and not state.get('blocked_reason')
            and self._last_network_mono is not None and digest == self._last_network_digest
            and 0 <= monotonic - self._last_network_mono < self.policy.cache_seconds)
        if short_cache:
            self._remember_seen(path, state, effective)
            if self._last_network_online:
                return self._result(True, 'cached', '本机授权有效。', signed)
            return self._result(True, 'offline', '暂时无法联网，正在使用本机有效授权。', signed,
                                reason='temporarily_unavailable')
        action = 'activate' if code else 'renew'
        base = dict(action=action, device_public_key=identity.public_key, machine_hash=identity.machine_hash)
        base.update({'code': code} if code else {'grant': grant})
        start = self.monotonic()
        try:
            renewed = self._exchange(action, base, identity)
            fresh = verify_grant(renewed, self.policy, device_public_key=identity.public_key,
                                 machine_hash=identity.machine_hash)
            if signed and fresh['license_id'] == signed['license_id']:
                if (fresh['license_generation'] < signed['license_generation']
                        or fresh['device_generation'] < signed['device_generation']
                        or fresh['issued_at'] < signed['issued_at'] - 120):
                    raise LicenseProtocolError('授权响应早于本机已核对的授权。')
            finished = self.monotonic()
            server_now = fresh['issued_at'] + max(0, finished - start)
            if server_now >= min(fresh['expires_at'], fresh['license_expires_at']):
                raise LicenseProtocolError('本次授权已到期，请重新联网验证。')
            renewed_digest = self._grant_digest(renewed)
            state = dict(format_version=1, grant=renewed, last_seen_at=server_now,
                last_wall_at=self.clock(), clock_anchor=dict(wall_at_online=self.clock(),
                    server_at_online=server_now, grant_digest=renewed_digest))
            write_private_json(path, state)
            self._last_seen_memory = server_now
            self._last_network_mono = finished
            self._last_network_digest = renewed_digest
            self._last_network_online = True
            self._anchor = (self._last_network_digest, server_now, finished)
            return self._result(True, 'online', '授权已验证。', fresh)
        except LicenseUnavailable:
            self._last_network_mono = self.monotonic()
            self._last_network_digest = digest
            self._last_network_online = False
            if state.get('blocked_reason'):
                return self._result(False, 'denied', '本机授权需要联网重新验证。', signed,
                                    reason=state['blocked_reason'])
            if rollback:
                return self._result(False, 'denied', '本机时间发生变化，请校准时间后联网验证。', signed,
                                    reason='clock_rollback')
            if not code and current:
                self._remember_seen(path, state, effective)
                return self._result(True, 'offline', '暂时无法联网，正在使用本机有效授权。', signed,
                                    reason='temporarily_unavailable')
            message = ('本机离线授权已到期，请联网验证。' if signed else '首次激活需要联网，请连接网络后重试。')
            return self._result(False, 'denied', message, signed, reason='offline_expired' if signed else 'offline_activation')
        except LicenseRejected as error:
            self._persist_denial(path, state, error.reason)
            return self._result(False, 'denied', str(error), signed, reason=error.reason)
        except LicenseProtocolError as error:
            self._persist_denial(path, state, 'invalid_response')
            return self._result(False, 'denied', str(error), signed, reason='invalid_response')
