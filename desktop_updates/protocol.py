"""Strict signed metadata for the fixed 1.00 desktop base release.

The updater trusts an embedded Ed25519 public key, never a key supplied by a
manifest. Accepted sequence numbers must additionally be persisted with the
canonical metadata digest by the caller, so equal-sequence equivocation cannot
replace a previously accepted release.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import platform
import re
import sys
from urllib.parse import unquote, urlsplit

BASE_RELEASE = "1.0.0-2026110606"
UPDATER_VERSION = 1
DEFAULT_ORIGIN = "https://159.75.243.120"
TARGETS = frozenset(("windows-x86_64", "macos-arm64"))
MAX_MANIFEST_BYTES = 262144
MAX_ARTIFACT_BYTES = 4 * 1024**3
MAX_UNPACKED_BYTES = 12 * 1024**3
MAX_FILES = 100000
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class UpdateError(RuntimeError):
    """An update cannot safely continue; the current program is retained."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("更新清单含重复字段，已停止更新。")
        result[key] = value
    return result


def strict_json(raw: bytes, *, maximum: int = MAX_MANIFEST_BYTES) -> dict:
    if len(raw) > maximum:
        raise UpdateError("更新清单超过允许大小。")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise UpdateError("更新清单无法读取。") from error
    if not isinstance(value, dict):
        raise UpdateError("更新清单格式无效。")
    return value


def canonical_json(value: dict) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise UpdateError("更新清单格式无效。") from error


def metadata_digest(manifest: dict) -> str:
    """Digest of signed content, excluding locally attached verification fields."""
    return hashlib.sha256(canonical_json({key: value for key, value in manifest.items()
                                         if not key.startswith("_")})).hexdigest()


def positive_int(value, label: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise UpdateError("更新清单的" + label + "无效。")
    return value


def _timestamp(value) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise UpdateError("更新清单的有效期无效。")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
        return result.astimezone(timezone.utc)
    except ValueError as error:
        raise UpdateError("更新清单的有效期无效。") from error


def validate_https_url(url: str, origin: str = DEFAULT_ORIGIN) -> str:
    """Only a fixed HTTPS origin is accepted, including every redirect hop."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(char) < 33 for char in url):
        raise UpdateError("更新下载地址无效。")
    try:
        source = urlsplit(origin)
        parsed = urlsplit(url)
        decoded_path = unquote(parsed.path)
        if (source.scheme != "https" or source.username or source.password
                or source.path not in ("", "/") or source.query or source.fragment
                or parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.fragment or parsed.query or not parsed.hostname
                or parsed.hostname != source.hostname or parsed.port != source.port
                or not parsed.path.startswith("/") or "\\" in decoded_path
                or any(part in (".", "..") for part in decoded_path.split("/"))
                or any(ord(char) < 32 for char in decoded_path)):
            raise ValueError("untrusted origin")
    except (ValueError, TypeError) as error:
        raise UpdateError("更新文件必须来自指定服务器的 HTTPS 地址。") from error
    return url


def validate_trust(trust: dict) -> dict:
    """Validate the embedded public configuration without fetching or writing."""
    if (not isinstance(trust, dict) or type(trust.get("format_version")) is not int
            or trust["format_version"] != 1 or trust.get("product") != "LJQCApp"
            or trust.get("channel") != "dev" or trust.get("base_release") != BASE_RELEASE):
        raise UpdateError("更新接入组件的信任配置无效，请重新安装更新组件。")
    origin = trust.get("origin")
    if not isinstance(origin, str):
        raise UpdateError("更新接入组件的服务器设置无效。")
    validate_https_url(origin.rstrip("/") + "/", origin)
    keys = trust.get("keys")
    if not isinstance(keys, dict) or not keys or len(keys) > 16:
        raise UpdateError("更新接入组件缺少发行公钥。")
    for key_id, encoded in keys.items():
        if not isinstance(key_id, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id) is None:
            raise UpdateError("更新发行密钥标识无效。")
        try:
            if not isinstance(encoded, str) or len(base64.b64decode(encoded, validate=True)) != 32:
                raise ValueError("bad public key")
        except (ValueError, TypeError) as error:
            raise UpdateError("更新发行公钥无效。") from error
    return trust


def _verify_signature(envelope: dict, trust: dict) -> dict:
    if set(envelope) != {"signed", "key_id", "signature"} or not isinstance(envelope.get("signed"), dict):
        raise UpdateError("更新清单签名封装无效。")
    key_id = envelope.get("key_id")
    keys = trust.get("keys")
    if not isinstance(keys, dict) or not isinstance(key_id, str) or key_id not in keys:
        raise UpdateError("更新清单的发行密钥不受信任。")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as error:
        raise UpdateError("更新组件不完整，请重新安装更新接入组件。") from error
    try:
        key = base64.b64decode(keys[key_id], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(key) != 32 or len(signature) != 64:
            raise ValueError("invalid key/signature size")
        Ed25519PublicKey.from_public_bytes(key).verify(signature, canonical_json(envelope["signed"]))
    except (InvalidSignature, ValueError, TypeError, KeyError) as error:
        raise UpdateError("更新签名检查未通过，当前程序未更改。") from error
    return envelope["signed"]


def _os_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or re.fullmatch(r"\d+(?:\.\d+){1,3}", value) is None:
        raise UpdateError("更新清单的最低系统版本无效。")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def verify_envelope(raw: bytes, trust: dict, *, target: str, installed_build: int,
                    highest_sequence: int, now: datetime | None = None) -> dict:
    """Verify first, then parse the exact target/channel/base and release gates.

    An equal installed build is valid metadata but must never trigger installation.
    A lower build is refused. Equal sequence may be retried only with the same
    metadata_digest previously persisted by the caller.
    """
    validate_trust(trust)
    maximum = min(positive_int(trust.get("max_manifest_bytes", MAX_MANIFEST_BYTES), "清单大小"), MAX_MANIFEST_BYTES)
    manifest = _verify_signature(strict_json(raw, maximum=maximum), trust)
    expected = {"format": "ljqc-desktop-update", "format_version": 1,
                "product": "LJQCApp", "edition": "desktop", "channel": "dev",
                "target": target, "base_release": BASE_RELEASE}
    if (target not in TARGETS or trust.get("product", "LJQCApp") != "LJQCApp"
            or trust.get("channel", "dev") != "dev"
            or trust.get("base_release", BASE_RELEASE) != BASE_RELEASE
            or any(type(manifest.get(key)) is not type(value) or manifest.get(key) != value
                   for key, value in expected.items())):
        raise UpdateError("更新不适用于当前基础包、平台或发行渠道。")
    if any(key.startswith("_") for key in manifest):
        raise UpdateError("更新清单包含保留字段。")
    build = positive_int(manifest.get("build_number"), "构建号")
    sequence = positive_int(manifest.get("sequence"), "发布序号")
    positive_int(installed_build, "本机版本", minimum=0)
    positive_int(highest_sequence, "本机发布序号", minimum=0)
    if build < installed_build or sequence < highest_sequence:
        raise UpdateError("服务器返回旧版本清单，已保留当前程序。")
    if (not isinstance(manifest.get("version"), str)
            or re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]) is None
            or not isinstance(manifest.get("source_commit"), str)
            or HEX40.fullmatch(manifest["source_commit"]) is None
            or not isinstance(manifest.get("source_digest"), str)
            or HEX64.fullmatch(manifest["source_digest"]) is None):
        raise UpdateError("更新版本或来源记录不完整。")
    issued, expires = _timestamp(manifest.get("issued_at")), _timestamp(manifest.get("expires_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise UpdateError("本机时间未包含时区。")
    if issued > current + timedelta(minutes=5) or expires <= current or expires <= issued or expires - issued > timedelta(days=31):
        raise UpdateError("更新清单尚未生效或已过期，请稍后重试并核对本机时间。")
    if positive_int(manifest.get("min_updater_version"), "最低更新组件版本") > UPDATER_VERSION:
        raise UpdateError("请先安装新版更新接入组件，再更新程序。")
    if type(manifest.get("data_format_version")) is not int or manifest["data_format_version"] != 1:
        raise UpdateError("此更新需要尚未支持的数据升级，已保留当前资料。")
    minimum = _os_version(manifest.get("min_os_version"))
    if target == "macos-arm64" and sys.platform == "darwin":
        if _os_version(platform.mac_ver()[0]) < minimum:
            raise UpdateError("此更新需要较新的 macOS，已保留当前程序。")
    if target == "windows-x86_64" and sys.platform == "win32":
        if _os_version(platform.version()) < minimum:
            raise UpdateError("此更新需要较新的 Windows，已保留当前程序。")
    origin = trust.get("origin", DEFAULT_ORIGIN)
    validate_https_url(origin.rstrip("/") + "/", origin)
    artifact, payload = manifest.get("artifact"), manifest.get("payload")
    if not isinstance(artifact, dict) or not isinstance(payload, dict) or artifact.get("kind") != "tar.gz":
        raise UpdateError("更新文件格式无效。")
    validate_https_url(artifact.get("url"), origin)
    if not str(urlsplit(artifact["url"]).path).startswith("/downloads/patches/"):
        raise UpdateError("更新文件不在程序补丁目录。")
    limit = min(positive_int(trust.get("max_artifact_bytes", MAX_ARTIFACT_BYTES), "下载大小"), MAX_ARTIFACT_BYTES)
    unpack_limit = min(positive_int(trust.get("max_unpacked_bytes", MAX_UNPACKED_BYTES), "展开大小"), MAX_UNPACKED_BYTES)
    positive_int(artifact.get("size_bytes"), "文件大小", maximum=limit)
    positive_int(artifact.get("unpacked_size_bytes"), "展开大小", maximum=unpack_limit)
    expected_entry = "LJQCApp.exe" if target == "windows-x86_64" else "LJQCApp.app/Contents/MacOS/LJQCApp"
    if (not isinstance(artifact.get("sha256"), str) or HEX64.fullmatch(artifact["sha256"]) is None
            or payload.get("entrypoint") != expected_entry
            or not isinstance(payload.get("manifest_sha256"), str)
            or HEX64.fullmatch(payload["manifest_sha256"]) is None):
        raise UpdateError("更新文件的校验信息不完整。")
    result = dict(manifest)
    result["_trusted_origin"] = origin.rstrip("/")
    result["_metadata_digest"] = metadata_digest(manifest)
    result["_max_files"] = min(positive_int(trust.get("max_files", MAX_FILES), "文件数量"), MAX_FILES)
    return result
