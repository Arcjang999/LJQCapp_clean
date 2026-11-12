"""Desktop authorization at UI and database boundaries; no environment bypass."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import hashlib
import hmac
from pathlib import Path
import sys
import threading


_client_context = ContextVar('desktop_license_client', default=None)
_candidate_database = ContextVar('desktop_license_candidate_database', default=None)
_default_client = None
_client_lock = threading.Lock()


class BusinessAccessDenied(ValueError):
    pass


def authorization_required() -> bool:
    # A compiled desktop runtime stays protected when launched without its
    # shortcut or with an inherited cloud/development environment variable.
    return bool(getattr(sys, 'frozen', False)) or Path(__file__).suffix.lower() in ('.so', '.pyd')


def create_client():
    from desktop_licensing.client import LicenseClient
    from desktop_licensing.protocol import LicenseConfigurationError, TrustPolicy
    from desktop_licensing.trust_anchor import TRUST_SHA256
    from desktop_licensing.device import default_state_dir
    trust = Path(__file__).resolve().parents[1] / 'resources' / 'desktop_license_trust.json'
    try:
        raw = trust.read_bytes()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), TRUST_SHA256):
            raise LicenseConfigurationError('桌面授权信任文件与程序不一致。')
        policy = TrustPolicy.from_bytes(raw)
    except (LicenseConfigurationError, OSError):
        policy = None
    return LicenseClient(policy, default_state_dir())


def bind_client(client) -> None:
    """Reuse the UI's client when a fragment or its DAO checks authorization."""
    _client_context.set(client)


def _business_client():
    client = _client_context.get()
    if client is not None:
        return client
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = create_client()
        return _default_client


def assert_business_access(database_path: str | Path) -> None:
    candidate = _candidate_database.get()
    if candidate is not None:
        if Path(database_path).resolve() == candidate:
            return
        raise BusinessAccessDenied('更新检查只能访问候选资料。')
    if not authorization_required():
        return
    result = _business_client().check()
    if not result.allowed:
        raise BusinessAccessDenied('当前授权不可用，请重新验证后保存。')


@contextmanager
def candidate_preflight_access(database_path: Path):
    """Permit only the already validated updater candidate's migration.

    run_app also installs its process-wide SQLite path audit before entering
    this scope. This is deliberately unrelated to the deployment-mode setting.
    """
    database_path = Path(database_path).resolve()
    root = database_path.parent.parent
    marker = root / 'update-candidate.json'
    try:
        declaration = json.loads(marker.read_text(encoding='utf-8'))
        if (database_path != root / 'data' / 'qc_lj_app.db' or marker.is_symlink()
                or declaration.get('format') != 'ljqc-update-candidate'
                or declaration.get('base_release') != '1.0.0-2026110606'):
            raise ValueError('candidate')
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise BusinessAccessDenied('更新检查位置无效。') from error
    token = _candidate_database.set(database_path)
    try:
        yield
    finally:
        _candidate_database.reset(token)
