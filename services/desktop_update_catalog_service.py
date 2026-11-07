"""Read signed bootstrap links from the local public release index.

The webpage does not fetch network content or open customer storage. A missing,
incomplete, stale or untrusted index leaves the fixed base downloads intact.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from desktop_updates.protocol import (BASE_RELEASE, DEFAULT_ORIGIN, TARGETS,
    UpdateError, canonical_json, strict_json, validate_trust)

LOGGER = logging.getLogger(__name__)
MAX_INDEX_BYTES = 262144
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,200}")


@dataclass(frozen=True)
class BootstrapDownload:
    target: str
    version: str
    build_number: int
    published_at: datetime
    url: str
    sha256: str
    size_bytes: int
    filename: str


@dataclass(frozen=True)
class DesktopUpdateCatalog:
    bootstraps: tuple[BootstrapDownload, ...]
    readable: bool


def _read_limited(path: Path) -> bytes:
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_INDEX_BYTES + 1)
    if len(raw) > MAX_INDEX_BYTES:
        raise ValueError("Release index exceeds the size limit")
    return raw


def _positive(value) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid release size or number")
    return value


def _sha(value) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("Invalid release hash")
    return value


def _verify_index(raw: bytes, trust: dict, now: datetime) -> tuple[dict, datetime]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    validate_trust(trust)
    if trust["origin"] != DEFAULT_ORIGIN:
        raise ValueError("The download origin is not the fixed public server")
    envelope = strict_json(raw, maximum=MAX_INDEX_BYTES)
    if set(envelope) != {"signed", "key_id", "signature"} or not isinstance(envelope["signed"], dict):
        raise ValueError("Invalid signed release envelope")
    key_id = envelope["key_id"]
    if not isinstance(key_id, str) or key_id not in trust["keys"]:
        raise ValueError("Unknown release key")
    try:
        key = base64.b64decode(trust["keys"][key_id], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(key) != 32 or len(signature) != 64:
            raise ValueError("Invalid signature length")
        Ed25519PublicKey.from_public_bytes(key).verify(signature, canonical_json(envelope["signed"]))
    except InvalidSignature as error:
        raise ValueError("The release signature did not verify") from error
    value = envelope["signed"]
    required = {"format": "ljqc-desktop-release", "format_version": 1, "product": "LJQCApp",
                "channel": "dev", "base_release": BASE_RELEASE, "source_dirty": False}
    if any(type(value.get(key)) is not type(expected) or value.get(key) != expected
           for key, expected in required.items()):
        raise ValueError("The release identity is not supported")
    if (not isinstance(value.get("version"), str) or _VERSION.fullmatch(value["version"]) is None
            or not isinstance(value.get("source_commit"), str)
            or re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) is None):
        raise ValueError("Invalid release source")
    _sha(value.get("source_digest"))
    _positive(value.get("build_number"))
    _positive(value.get("sequence"))
    issued = value.get("issued_at")
    if not isinstance(issued, str) or len(issued) > 40:
        raise ValueError("Invalid release timestamp")
    issued = datetime.fromisoformat(issued.replace("Z", "+00:00"))
    if issued.tzinfo is None or issued.utcoffset() != timedelta(0):
        raise ValueError("Release timestamp must be UTC")
    if now.tzinfo is None or issued > now + timedelta(minutes=5) or issued < now - timedelta(days=31):
        raise ValueError("Release index is stale or not yet valid")
    if (not isinstance(value.get("targets"), dict) or set(value["targets"]) != TARGETS
            or not isinstance(value.get("files"), list) or len(value["files"]) != 6):
        raise ValueError("Both complete platform releases are required")
    return value, issued


def _catalog(value: dict, issued: datetime, origin: str) -> DesktopUpdateCatalog:
    by_target = {target: {} for target in TARGETS}
    for record in value["files"]:
        if not isinstance(record, dict) or record.get("target") not in TARGETS:
            raise ValueError("Unknown release target")
        target, kind = record["target"], record.get("kind")
        if kind not in ("manifest", "patch", "bootstrap") or kind in by_target[target]:
            raise ValueError("Duplicate or unknown platform artifact")
        _sha(record.get("sha256"))
        _positive(record.get("size_bytes"))
        path = record.get("path")
        if (not isinstance(path, str) or "\\" in path or path.count("/") != 1
                or path.split("/", 1)[0] != target
                or _FILENAME.fullmatch(path.split("/", 1)[1]) is None):
            raise ValueError("Invalid artifact filename")
        by_target[target][kind] = record
    results = []
    for target in ("windows-x86_64", "macos-arm64"):
        records = by_target[target]
        if set(records) != {"manifest", "patch", "bootstrap"}:
            raise ValueError("Missing platform artifact")
        manifest, entry = records["manifest"], value["targets"][target]
        if (not isinstance(entry, dict) or manifest["path"] != target + "/latest.json"
                or _sha(entry.get("manifest_sha256")) != manifest["sha256"]
                or _positive(entry.get("manifest_size_bytes")) != manifest["size_bytes"]):
            raise ValueError("Platform manifest differs from the release index")
        bootstrap = records["bootstrap"]
        filename = bootstrap["path"].split("/", 1)[1]
        suffix = ".exe" if target == "windows-x86_64" else ".dmg"
        if not filename.lower().endswith(suffix):
            raise ValueError("Bootstrap is not an installable platform file")
        url = bootstrap.get("url")
        expected = f"{origin}/downloads/update-bootstrap/1/{value['build_number']}/{target}/{filename}"
        if not isinstance(url, str) or url != expected:
            raise ValueError("Bootstrap URL differs from the fixed public location")
        # Exact URL equality above excludes alternate hosts, credentials, query
        # strings, fragments, ports, traversal and percent-encoded filenames.
        if urlsplit(url).scheme != "https":
            raise ValueError("Bootstrap HTTPS is required")
        results.append(BootstrapDownload(target, value["version"], value["build_number"], issued,
                         url, bootstrap["sha256"], bootstrap["size_bytes"], filename))
    return DesktopUpdateCatalog(tuple(results), True)


def get_desktop_update_catalog(path: Path | None = None, trust_path: Path | None = None,
                               *, now: datetime | None = None) -> DesktopUpdateCatalog:
    """Return enabled links only for a complete, freshly signed local release."""
    public = Path(os.environ.get("LJQC_PUBLIC_UPDATE_ROOT", "/var/www/ljqc-public"))
    index = Path(path) if path is not None else public / "updates/desktop/dev/current/release-index.json"
    trust_file = (Path(trust_path) if trust_path is not None else
                  Path(__file__).resolve().parents[1] / "resources/desktop_update_trust.json")
    try:
        if path is None and (not public.is_absolute() or not index.resolve().is_relative_to(public.resolve())):
            raise ValueError("The release index escapes the public directory")
        trust = strict_json(_read_limited(trust_file), maximum=MAX_INDEX_BYTES)
        value, issued = _verify_index(_read_limited(index), trust, now or datetime.now(timezone.utc))
        return _catalog(value, issued, trust["origin"])
    except FileNotFoundError:
        return DesktopUpdateCatalog((), False)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, ImportError, RuntimeError, RecursionError) as error:
        LOGGER.warning("Desktop bootstrap catalog is unavailable: %s", type(error).__name__)
        return DesktopUpdateCatalog((), False)
