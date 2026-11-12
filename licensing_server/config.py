"""Private server configuration and explicit, local-only key provisioning."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_ENDS_AT = '2026-11-30T23:59:00+08:00'
DEFAULT_MAX_DEVICES = 10
OFFLINE_SECONDS = 86400
SOURCE_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    pass


def decode_key(value: str, size: int = 32) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConfigError('密钥配置无效。') from exc
    if len(raw) != size:
        raise ConfigError('密钥配置无效。')
    return raw


def parse_deadline(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return int(parsed.timestamp())
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError('截止时间须包含时区，例如2026-11-30T23:59:00+08:00。') from exc


def private_path(value: str | Path, *, must_exist=False, business_root='/var/lib/ljqc') -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError('授权配置和数据库须使用绝对路径。')
    if path.is_symlink() or path.parent.is_symlink():
        raise ConfigError('授权文件不能使用符号链接。')
    path = path.resolve()
    if path.parent.exists() and os.name != 'nt':
        parent = path.parent.stat()
        if parent.st_mode & 0o022 or parent.st_uid not in (0, os.geteuid()):
            raise ConfigError('授权私有目录须由当前服务账号或root持有，且不可供其他账号写入。')
    forbidden = (SOURCE_ROOT.resolve(), Path(business_root).expanduser().resolve())
    if any(path.is_relative_to(root) for root in forbidden):
        raise ConfigError('授权配置和数据库须位于源码及业务资料目录之外。')
    if must_exist and not path.is_file():
        raise ConfigError('授权配置文件未找到。')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError('授权路径须指向普通文件。')
        if os.name != 'nt' and info.st_mode & 0o077:
            raise ConfigError('授权文件权限须为0600。')
    return path


def write_private_json(path: Path, value: dict) -> None:
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class ServerConfig:
    database_path: Path
    key_id: str
    signing_key: Ed25519PrivateKey = field(repr=False)
    code_pepper: bytes = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> 'ServerConfig':
        config_path = private_path(path, must_exist=True)
        try:
            content = config_path.read_bytes()
            if len(content) > 16384:
                raise ValueError
            value = json.loads(content)
            if value['format_version'] != 1:
                raise ValueError
            key_id = value['key_id']
            if not isinstance(key_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key_id):
                raise ValueError
            database = private_path(value['database_path'])
            return cls(database, key_id,
                       Ed25519PrivateKey.from_private_bytes(decode_key(value['private_key'])),
                       decode_key(value['code_pepper']))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError('授权服务配置无效，请核对私有配置文件。') from exc

    def public_trust(self, origin: str, api_prefix='/licensing') -> dict:
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ConfigError('公开服务地址须为HTTPS主机地址。')
        if not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', api_prefix):
            raise ConfigError('授权接口前缀无效。')
        key = self.signing_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return dict(format_version=1, origin=origin.rstrip('/'), api_prefix=api_prefix,
                    keys={self.key_id: base64.b64encode(key).decode('ascii')}, timeout_seconds=8,
                    offline_seconds=OFFLINE_SECONDS, cache_seconds=300)


def initialize(config_path: Path, database_path: Path, public_path: Path, origin: str) -> dict:
    """Only the explicit CLI command calls this; importing creates no secrets."""
    config_path, database_path = private_path(config_path), private_path(database_path)
    if config_path.exists() or database_path.exists() or public_path.exists():
        raise ConfigError('目标文件已存在，请保留原配置并选择空目录。')
    private_key = Ed25519PrivateKey.generate()
    private = dict(format_version=1, database_path=str(database_path), key_id='license-' + secrets.token_hex(8),
                   private_key=base64.b64encode(private_key.private_bytes(
                       serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                       serialization.NoEncryption())).decode('ascii'),
                   code_pepper=base64.b64encode(secrets.token_bytes(32)).decode('ascii'))
    # Validate the public address before writing either file.
    config = ServerConfig(database_path, private['key_id'], private_key, decode_key(private['code_pepper']))
    trust = config.public_trust(origin)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    if public_path.resolve() == config_path or public_path.resolve() == database_path:
        raise ConfigError('公开信任文件必须使用独立路径。')
    write_private_json(config_path, private)
    with public_path.open('x', encoding='utf-8') as output:
        json.dump(trust, output, ensure_ascii=False, indent=2)
        output.write('\n')
    return trust

