"""Sign, transfer and atomically publish desktop patches without business access.

The receiver is intended for a dedicated forced-command SSH identity. Install
this file and the pinned public trust file read-only; give that identity write
access only to its own staging directory and the static downloads/updates trees.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import struct
import time
import threading
import zipfile
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit, unquote, parse_qsl
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "1.0.0-2026110606"
TARGETS = ("windows-x86_64", "macos-arm64")


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(path: Path) -> str:
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()


def read_object(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def private_key(path: Path | None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if path is None:
        value = os.environ.get("LJQC_UPDATE_SIGNING_KEY", "").strip()
        if not value:
            raise ValueError("The release signing secret is not configured")
        raw = base64.b64decode(value, validate=True)
    else:
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError("The signing key file must be accessible only to its owner")
        raw = path.read_bytes()
        if raw.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("An Ed25519 release key is required")
            return key
        if len(raw) != 32:
            raw = base64.b64decode(raw.strip(), validate=True)
    return Ed25519PrivateKey.from_private_bytes(raw)


def envelope(value: dict, key, key_id: str) -> dict:
    return {"signed": value, "key_id": key_id,
            "signature": base64.b64encode(key.sign(canonical(value))).decode("ascii")}


def verify_index(raw: dict, trust: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(trust["keys"][raw["key_id"]], validate=True))
    key.verify(base64.b64decode(raw["signature"], validate=True), canonical(raw["signed"]))
    value = raw["signed"]
    if (value.get("format") != "ljqc-desktop-release" or value.get("format_version") != 1
            or value.get("base_release") != BASE or value.get("channel") != trust["channel"]
            or value.get("product") != "LJQCApp" or value.get("source_dirty") is not False):
        raise ValueError("Unsupported release index")
    if set(value.get("targets", {})) != set(TARGETS):
        raise ValueError("Both platforms must be released together")
    return value


def verify_signature(raw: dict, trust: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if set(raw) != {"signed", "key_id", "signature"} or not isinstance(raw["signed"], dict):
        raise ValueError("Invalid signed metadata envelope")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(trust["keys"][raw["key_id"]], validate=True))
    key.verify(base64.b64decode(raw["signature"], validate=True), canonical(raw["signed"]))
    return raw["signed"]


def sign_candidates(inputs: Path, output: Path, trust_path: Path, *, key_path: Path | None = None,
                    key_id: str = "release-2026-11", lifetime_days: int = 14,
                    sequence: int | None = None) -> dict:
    from desktop_updates.protocol import verify_envelope
    from cryptography.hazmat.primitives import serialization
    trust = read_object(trust_path)
    key = private_key(key_path)
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
    if trust["keys"].get(key_id) != public:
        raise ValueError("The signing identity does not match the shipped trust file")
    if not 1 <= lifetime_days <= 30:
        raise ValueError("Manifest lifetime must be between 1 and 30 days")
    output.mkdir(parents=True, exist_ok=False)
    issued = datetime.now(timezone.utc)
    release_sequence = sequence if sequence is not None else int(issued.timestamp() * 1_000_000)
    records, identities, target_records = [], [], {}
    for target in TARGETS:
        candidates = [path for path in inputs.rglob("candidate-update.json") if read_object(path).get("target") == target]
        if len(candidates) != 1:
            raise ValueError("Exactly one candidate for each platform is required")
        candidate_file = candidates[0]
        candidate = read_object(candidate_file)
        candidate["issued_at"] = issued.isoformat()
        candidate["expires_at"] = (issued + timedelta(days=lifetime_days)).isoformat()
        candidate["sequence"] = release_sequence
        signed = envelope(candidate, key, key_id)
        verify_envelope(canonical(signed), trust, target=target,
                        installed_build=2026110606, highest_sequence=0, now=issued)
        identities.append((candidate["version"], candidate["build_number"], candidate["source_commit"], candidate["source_digest"]))
        target_dir = output / target
        target_dir.mkdir()
        latest = target_dir / "latest.json"
        latest.write_bytes(canonical(signed) + b"\n")
        target_records[target] = {"manifest_sha256": digest(latest), "manifest_size_bytes": latest.stat().st_size}
        records.append({"kind": "manifest", "target": target, "path": target + "/latest.json",
                        "sha256": digest(latest), "size_bytes": latest.stat().st_size})
        artifact = candidate["artifact"]
        filename = PurePosixPath(urlsplit(artifact["url"]).path).name
        source = candidate_file.parent / filename
        if (source.is_symlink() or not source.is_file() or source.stat().st_size != artifact["size_bytes"]
                or digest(source) != artifact["sha256"]):
            raise ValueError("Candidate program archive failed size/hash verification")
        shutil.copyfile(source, target_dir / filename)
        records.append({"kind": "patch", "target": target, "path": target + "/" + filename,
                        "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"], "url": artifact["url"]})
        bootstrap = read_object(candidate_file.parent / "bootstrap-build-receipt.json")
        if bootstrap.get("target") != target or bootstrap.get("status") != "passed":
            raise ValueError("A verified platform bootstrap is required")
        if any(bootstrap.get("release", {}).get(field) != candidate[source] for field, source in
               (("version", "version"), ("build_number", "build_number"), ("git_commit", "source_commit"), ("source_digest", "source_digest"))):
            raise ValueError("Bootstrap source identity differs from the program patch")
        helper_name = bootstrap["artifact"]["file"]
        if not isinstance(helper_name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,200}", helper_name) is None:
            raise ValueError("Bootstrap artifact must be a plain candidate filename")
        helper = candidate_file.parent / helper_name
        if (helper.is_symlink() or not helper.is_file() or digest(helper) != bootstrap["artifact"]["sha256"]
                or helper.stat().st_size != bootstrap["artifact"]["size_bytes"]
                or helper.resolve().parent != candidate_file.parent.resolve()):
            raise ValueError("Bootstrap artifact differs from its verified receipt")
        shutil.copyfile(helper, target_dir / helper.name)
        helper_url = (trust["origin"] + f"/downloads/update-bootstrap/1/{candidate['build_number']}/{target}/" + helper.name)
        records.append({"kind": "bootstrap", "target": target, "path": target + "/" + helper.name,
                        "sha256": digest(helper), "size_bytes": helper.stat().st_size, "url": helper_url})
    if len(set(identities)) != 1:
        raise ValueError("The two platform candidates do not have the same source identity")
    version, build, commit, source_digest = identities[0]
    index = {"format": "ljqc-desktop-release", "format_version": 1, "product": "LJQCApp",
        "channel": "dev", "base_release": BASE, "version": version, "build_number": build,
        "sequence": release_sequence, "source_commit": commit,
        "source_digest": source_digest, "source_dirty": False, "issued_at": issued.isoformat(),
        "release_id": f"{version}-{build}-{source_digest[:12]}", "targets": target_records, "files": records}
    (output / "release-index.json").write_bytes(canonical(envelope(index, key, key_id)) + b"\n")
    verify_bundle(output, trust_path)
    return index


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or "\\" in value or any(ord(char) < 32 for char in value)
            or path.as_posix() != value or path.is_absolute() or any(part in ("..", ".", "") or ":" in part for part in path.parts)
            or len(path.parts) != 2 or path.parts[0] not in TARGETS):
        raise ValueError("Invalid publication path")
    return path


def verify_bundle(bundle: Path, trust_path: Path) -> dict:
    from desktop_updates.protocol import verify_envelope
    trust = read_object(trust_path)
    index = verify_index(read_object(bundle / "release-index.json"), trust)
    expected, seen = {"release-index.json"}, set()
    destinations = set()
    for record in index["files"]:
        relative = safe_relative(record["path"])
        path = bundle.joinpath(*relative.parts)
        metadata_only = index.get("metadata_only") is True and record["kind"] in ("patch", "bootstrap")
        if record["path"] in seen or (not metadata_only and (path.is_symlink() or not path.is_file())):
            raise ValueError("Missing or duplicate published artifact")
        seen.add(record["path"])
        if not metadata_only and (path.stat().st_size != record["size_bytes"] or digest(path) != record["sha256"]):
            raise ValueError("Published artifact hash/length mismatch")
        if not metadata_only:
            expected.add(record["path"])
        target = record["target"]
        if target != relative.parts[0]:
            raise ValueError("Publication target mismatch")
        if record["kind"] in ("patch", "bootstrap"):
            parsed = urlsplit(record["url"])
            if parsed.scheme + "://" + parsed.netloc != trust["origin"] or parsed.query or parsed.fragment:
                raise ValueError("Published artifact has an unexpected origin")
            allowed = ("/downloads/patches/" + BASE + "/" if record["kind"] == "patch" else "/downloads/update-bootstrap/1/")
            if not parsed.path.startswith(allowed) or "/../" in parsed.path or "%" in parsed.path or "\\" in parsed.path:
                raise ValueError("Published artifact has an invalid immutable URL")
            if PurePosixPath(parsed.path).name != path.name or parsed.path in destinations:
                raise ValueError("Duplicate or mismatched destination")
            destinations.add(parsed.path)
        elif record["kind"] != "manifest":
            raise ValueError("Unexpected publication record")
    actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if not path.is_dir()}
    if actual != expected:
        raise ValueError("Bundle contains unlisted files")
    for target in TARGETS:
        raw = (bundle / target / "latest.json").read_bytes()
        body = verify_envelope(raw, trust, target=target, installed_build=2026110606, highest_sequence=0)
        if (body["version"] != index["version"] or body["build_number"] != index["build_number"] or body["source_commit"] != index["source_commit"]
                or body["source_digest"] != index["source_digest"] or body["sequence"] != index["sequence"]
                or digest(bundle / target / "latest.json") != index["targets"][target]["manifest_sha256"]):
            raise ValueError("Platform manifest differs from the release index")
        matching = [record for record in index["files"] if record["target"] == target]
        if sorted(record["kind"] for record in matching) != ["bootstrap", "manifest", "patch"]:
            raise ValueError("Each platform must contain exactly one manifest, patch and bootstrap")
        patch = next(record for record in matching if record["kind"] == "patch")
        if any(patch[field] != body["artifact"][field] for field in ("url", "size_bytes", "sha256")):
            raise ValueError("Patch differs from its platform manifest")
    return index


def immutable_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or digest(destination) != digest(source):
            raise ValueError("Refusing to overwrite an immutable published file")
        return
    temporary = destination.parent / ("." + destination.name + "." + uuid.uuid4().hex)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o644)
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        # Hard-link creation refuses a concurrent overwrite instead of racing rename.
        os.link(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def checked_public_directory(public: Path, directory: Path) -> None:
    relative = directory.relative_to(public)
    cursor = public
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
            raise ValueError("Public destination contains an unexpected path indirection")
    directory.mkdir(parents=True, exist_ok=True)


def verify_public_artifact(record: dict, trust: dict) -> None:
    """Verify real HTTPS bytes before selecting the release, including redirects."""
    from desktop_updates.transport import _open_https
    expected = record["size_bytes"]
    received, hash_value = 0, hashlib.sha256()
    with _open_https(record["url"], trust["origin"]) as response:
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) != expected):
            raise ValueError("Public HTTPS download has a different length")
        while True:
            chunk = response.read(min(1024**2, expected - received + 1))
            if not chunk:
                break
            received += len(chunk)
            if received > expected:
                raise ValueError("Public HTTPS download exceeds its signed length")
            hash_value.update(chunk)
    if received != expected or hash_value.hexdigest() != record["sha256"]:
        raise ValueError("Public HTTPS download differs from the signed release")


def promote(bundle: Path, public: Path, trust_path: Path, *, public_verifier=None) -> dict:
    index = verify_bundle(bundle, trust_path)
    trust = read_object(trust_path)
    public_verifier = public_verifier or (lambda record: verify_public_artifact(record, trust))
    public = public.resolve()
    release_id = index["release_id"]
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-[0-9a-f]{12}", release_id) is None:
        raise ValueError("Invalid release identifier")
    channel = public / "updates/desktop/dev"
    checked_public_directory(public, channel)
    # Serialize the compare-and-switch operation across both platforms.
    import fcntl
    if (channel / ".publish.lock").is_symlink():
        raise ValueError("The publication lock cannot be a symbolic link")
    with (channel / ".publish.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = channel / "current"
        if current.is_symlink():
            selected = current.readlink()
            if selected.is_absolute() or len(selected.parts) != 2 or selected.parts[0] != "releases" or ".." in selected.parts:
                raise ValueError("The current channel has an unexpected target")
        elif current.exists():
            raise ValueError("The current channel is not a managed symbolic link")
        if current.exists():
            previous = verify_index(read_object(current / "release-index.json"), read_object(trust_path))
            if index["sequence"] < previous["sequence"] or index["build_number"] < previous["build_number"]:
                raise ValueError("An older release cannot replace the current channel")
            if index["sequence"] == previous["sequence"] and canonical(index) != canonical(previous):
                raise ValueError("A release sequence cannot change content")
        missing_bytes = sum(record["size_bytes"] for record in index["files"]
            if record["kind"] != "manifest" and not (public / urlsplit(record["url"]).path.lstrip("/")).exists())
        if shutil.disk_usage(public).free < missing_bytes + 1024**3:
            raise ValueError("Publication would consume the server's reserved free space")
        for record in index["files"]:
            if record["kind"] != "manifest":
                destination = public / urlsplit(record["url"]).path.lstrip("/")
                checked_public_directory(public, destination.parent)
                if index.get("metadata_only") is True:
                    if (destination.is_symlink() or not destination.is_file()
                            or destination.stat().st_size != record["size_bytes"]
                            or digest(destination) != record["sha256"]):
                        raise ValueError("Metadata refresh requires the unchanged published artifacts")
                else:
                    immutable_copy(bundle / record["path"], destination)
                public_verifier(record)
        release_dir = channel / "releases" / (release_id + "-" + str(index["sequence"]))
        checked_public_directory(public, release_dir)
        for target in TARGETS:
            immutable_copy(bundle / target / "latest.json", release_dir / target / "latest.json")
        immutable_copy(bundle / "release-index.json", release_dir / "release-index.json")
        fsync_directory(release_dir)
        fsync_directory(release_dir.parent)
        for target in TARGETS:
            fixed = channel / target
            if fixed.is_symlink():
                if fixed.readlink().as_posix() != "current/" + target:
                    raise ValueError("A platform channel symbolic link has an unexpected target")
            else:
                if fixed.exists():
                    raise ValueError("A platform channel entry already exists and is not managed")
                fixed.symlink_to("current/" + target, target_is_directory=True)
        temporary = channel / (".current-" + uuid.uuid4().hex)
        temporary.symlink_to(release_dir.relative_to(channel), target_is_directory=True)
        temporary.replace(current)
        for directory in (channel, channel.parent, channel.parent.parent, public / "updates", public):
            fsync_directory(directory)
    return {"status": "published", "release_id": release_id, "sequence": index["sequence"],
            "base_installer_changed": False, "business_data_access": False}


def refresh_metadata(output: Path, trust_path: Path, *, key_path: Path | None = None,
                     key_id: str = "release-2026-11", lifetime_days: int = 14,
                     fetch=None) -> dict:
    """Refresh signatures and expiry only; fetch no program/customer content."""
    from desktop_updates.protocol import strict_json, verify_envelope
    from desktop_updates.transport import fetch_metadata_url
    trust = read_object(trust_path)
    if not 1 <= lifetime_days <= 30:
        raise ValueError("Manifest lifetime must be between 1 and 30 days")
    if fetch is None:
        # fetch_manifest keeps TLS verification, fixed-origin redirects and limits.
        fetch = lambda url: fetch_metadata_url(trust, url)
    origin = trust["origin"]
    old_raw = strict_json(fetch(origin + "/updates/desktop/dev/current/release-index.json"))
    index = dict(verify_index(old_raw, trust))
    key = private_key(key_path)
    from cryptography.hazmat.primitives import serialization
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
    if trust["keys"].get(key_id) != public:
        raise ValueError("The signing identity does not match shipped trust")
    issued = datetime.now(timezone.utc)
    sequence = max(int(issued.timestamp() * 1_000_000), int(index["sequence"]) + 1)
    fixed_release = index["release_id"] + "-" + str(index["sequence"])
    output.mkdir(parents=True, exist_ok=False)
    for target in TARGETS:
        old_bytes = fetch(origin + f"/updates/desktop/dev/releases/{fixed_release}/{target}/latest.json")
        if hashlib.sha256(old_bytes).hexdigest() != index["targets"][target]["manifest_sha256"]:
            raise ValueError("Published platform manifest differs from the signed index")
        old_body = verify_signature(strict_json(old_bytes), trust)
        # Refresh may read an expired trusted manifest; the new envelope must pass
        # every current schema/platform/base/size/version/expiry gate afterward.
        body = {**old_body, "sequence": sequence, "issued_at": issued.isoformat(),
                "expires_at": (issued + timedelta(days=lifetime_days)).isoformat()}
        encoded = canonical(envelope(body, key, key_id)) + b"\n"
        checked = verify_envelope(encoded, trust, target=target, installed_build=2026110606, highest_sequence=0, now=issued)
        if any(checked[field] != index[field] for field in ("version", "build_number", "source_commit", "source_digest")):
            raise ValueError("Published platform source differs from the release index")
        directory = output / target
        directory.mkdir()
        latest = directory / "latest.json"
        latest.write_bytes(encoded)
        index["targets"][target] = {"manifest_sha256": digest(latest), "manifest_size_bytes": len(encoded)}
        for record in index["files"]:
            if record["target"] == target and record["kind"] == "manifest":
                record.update(sha256=digest(latest), size_bytes=len(encoded))
    index.update(sequence=sequence, issued_at=issued.isoformat(), metadata_only=True)
    (output / "release-index.json").write_bytes(canonical(envelope(index, key, key_id)) + b"\n")
    verify_bundle(output, trust_path)
    return index


DELIVERY_REFERENCE = "delivery-reference.json"
DELIVERY_REPOSITORY = "Arcjang999/LJQCapp_clean"
DELIVERY_MAX_ZIP = 4 * 1024**3
DELIVERY_TIMEOUT = 7200
# Confirmed by the authenticated GitHub artifact API for this publication.
# New storage shards require an explicit control-layer allowlist review; raw
# signed-bundle transfer remains available without widening network access.
DELIVERY_HOSTS = frozenset({"productionresultssa6.blob.core.windows.net"})
_DELIVERY_PATH = re.compile(r"/actions-results/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/workflow-job-run-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/artifacts/[0-9a-f]{64}\.zip")


class DeliveryReferenceError(ValueError):
    """Sanitized delivery-stage failure; never contains vendor credentials."""


def validate_delivery_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ValueError("Invalid delivery download address")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in DELIVERY_HOSTS
            and parsed.username is None and parsed.password is None and not parsed.fragment
            and parsed.port in (None, 443) and parsed.netloc == parsed.hostname + (":443" if parsed.port else "")
            and "\\" not in url and "%" not in parsed.path and unquote(parsed.path) == parsed.path
            and _DELIVERY_PATH.fullmatch(parsed.path) is not None and bool(parsed.query))
    except (ValueError, UnicodeError):
        valid = False
    if valid:
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            query = dict(pairs)
            expiry = datetime.fromisoformat(query["se"].replace("Z", "+00:00"))
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            valid = (len(query) == len(pairs) and query.get("sp") == "r" and query.get("sr") == "b"
                and bool(query.get("sig")) and 60 <= remaining <= 3600
                and expiry.utcoffset() == timedelta(0)
                and not any(ord(char) < 32 or ord(char) == 127 for pair in pairs for item in pair for char in item))
        except (KeyError, ValueError, TypeError, OverflowError):
            valid = False
    if not valid:
        raise ValueError("The delivery download vendor, path, or short-lived read permission is not approved")
    return url


class _DeliveryRedirects(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_delivery_url(newurl)
        redirects = getattr(req, "_ljqc_delivery_redirects", 0) + 1
        if redirects > 3:
            raise ValueError("Delivery redirect limit exceeded")
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            result._ljqc_delivery_redirects = redirects
        return result


def _retrieve_delivery_archive(url: str, destination: Path, expected_size: int, expected_hash: str) -> Path:
    from desktop_updates.transport import _https_context
    validate_delivery_url(url)
    received, hashed = 0, hashlib.sha256()
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    last_progress = time.monotonic()
    print(json.dumps({"delivery_stage":"download", "bytes_received":0, "expected_bytes":expected_size}), file=sys.stderr, flush=True)
    try:
        # Explicitly ignore ambient proxies. No GitHub token or Authorization
        # header is ever read, constructed, or forwarded to the storage vendor.
        opener = build_opener(ProxyHandler({}), _DeliveryRedirects(), HTTPSHandler(context=_https_context()))
        request = Request(url, headers={"User-Agent": "LJQCApp-Delivery/1", "Accept-Encoding": "identity"})
        with opener.open(request, timeout=20) as response, destination.open("xb") as target:
            destination.chmod(0o600)
            validate_delivery_url(response.geturl())
            if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ValueError("The delivery response is not a complete archive")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) != expected_size):
                raise ValueError("The delivery response has an unexpected length")
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Delivery download timed out")
                block = reader(min(1024**2, expected_size - received + 1))
                if not block:
                    break
                received += len(block)
                if received > expected_size:
                    raise ValueError("Delivery download exceeds its expected size")
                hashed.update(block); target.write(block)
                if time.monotonic() - last_progress >= 30:
                    print(json.dumps({"delivery_stage":"download", "bytes_received":received, "expected_bytes":expected_size}), file=sys.stderr, flush=True)
                    last_progress = time.monotonic()
            target.flush(); os.fsync(target.fileno())
        if received != expected_size or hashed.hexdigest() != expected_hash:
            raise ValueError("Delivery archive verification failed")
        return destination
    except Exception as error:
        destination.unlink(missing_ok=True)
        # urllib exceptions carry the complete SAS URL. Do not retain or chain
        # them into receiver output, a traceback, or a publication receipt.
        message = ("Temporary artifact access expired or was denied; the previous channel remains selected"
            if isinstance(error, HTTPError) and error.code in (401, 403)
            else "Delivery archive HTTPS retrieval failed; the previous channel remains selected")
        raise DeliveryReferenceError(message) from None


def _download_delivery_archive(url: str, destination: Path, expected_size: int, expected_hash: str) -> Path:
    # Socket timeouts alone do not bound slow headers. A daemon performs only
    # private archive I/O; the caller cannot promote after timeout and the
    # forced-command process exits, terminating the daemon and its descriptors.
    completed = threading.Event(); outcome = {}
    def retrieve():
        try: outcome["value"] = _retrieve_delivery_archive(url, destination, expected_size, expected_hash)
        except Exception as error: outcome["error"] = error
        finally: completed.set()
    threading.Thread(target=retrieve, name="LJQCDeliveryDownload", daemon=True).start()
    if not completed.wait(DELIVERY_TIMEOUT):
        raise DeliveryReferenceError("Delivery archive exceeded its total time budget; the previous channel remains selected") from None
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, DeliveryReferenceError):raise error from None
        raise DeliveryReferenceError("Delivery archive HTTPS retrieval failed; the previous channel remains selected") from None
    return outcome["value"]


def _precheck_delivery_current(public: Path, index: dict, trust: dict) -> tuple[int, int]:
    current = public / "updates/desktop/dev/current"
    if not current.exists() and not current.is_symlink():
        return 2026110606, 0
    if not current.is_symlink():
        raise ValueError("The current channel is not a managed symbolic link")
    selected = current.readlink()
    if not current.resolve().is_relative_to(public.resolve()):
        raise ValueError("The current channel escapes the public release tree")
    if (selected.is_absolute() or len(selected.parts) != 2 or selected.parts[0] != "releases"
            or ".." in selected.parts or not current.exists()):
        raise ValueError("The current channel cannot be verified")
    from desktop_updates.protocol import strict_json
    previous_path = current / "release-index.json"
    if (previous_path.is_symlink() or not previous_path.is_file()
            or not previous_path.resolve().is_relative_to(public.resolve())
            or not 0 < previous_path.stat().st_size <= 262144):
        raise ValueError("The selected release index is not a bounded public file")
    with previous_path.open("rb") as handle:
        previous = verify_index(strict_json(handle.read(262145), maximum=262144), trust)
    if (type(index.get("sequence")) is not int or type(index.get("build_number")) is not int
            or index["sequence"] < previous["sequence"] or index["build_number"] < previous["build_number"]
            or (index["sequence"] == previous["sequence"] and canonical(index) != canonical(previous))):
        raise ValueError("Delivery metadata cannot roll back or rewrite the selected release")
    return previous["build_number"], previous["sequence"]


def _delivery_metadata(bundle: Path, trust_path: Path, public: Path) -> tuple[dict, dict[str, dict]]:
    from desktop_updates.protocol import strict_json, validate_trust, verify_envelope
    trust = validate_trust(strict_json(trust_path.read_bytes()))
    raw = (bundle / "release-index.json").read_bytes()
    encoded = strict_json(raw, maximum=262144)
    if set(encoded) != {"signed", "key_id", "signature"} or not isinstance(encoded["signed"], dict):
        raise ValueError("Invalid delivery signed envelope")
    index = verify_index(encoded, trust)
    if (type(index.get("format_version")) is not int or index["format_version"] != 1
            or type(index.get("build_number")) is not int or not 2026110606 <= index["build_number"] <= 2**63-1
            or not isinstance(index.get("version"), str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", index["version"]) is None
            or index.get("metadata_only") is True or not isinstance(index.get("files"), list)
            or len(index["files"]) != 6 or type(index.get("sequence")) is not int
            or not 0 < index["sequence"] <= 2**63 - 1
            or not isinstance(index.get("source_commit"), str) or re.fullmatch(r"[0-9a-f]{40}", index["source_commit"]) is None
            or not isinstance(index.get("source_digest"), str) or re.fullmatch(r"[0-9a-f]{64}", index["source_digest"]) is None
            or index.get("release_id") != str(index.get("version"))+"-"+str(index.get("build_number"))+"-"+index["source_digest"][:12]):
        raise ValueError("Invalid complete delivery release metadata")
    issued = index.get("issued_at")
    if not isinstance(issued, str) or len(issued) > 40:
        raise ValueError("Invalid delivery release timestamp")
    issued = datetime.fromisoformat(issued.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if issued.tzinfo is None or issued.utcoffset() != timedelta(0) or not now-timedelta(days=31) <= issued <= now+timedelta(minutes=5):
        raise ValueError("Delivery release timestamp is stale or not yet valid")
    build, sequence = _precheck_delivery_current(public, index, trust)
    records = {}
    destinations = set()
    for record in index["files"]:
        relative = safe_relative(record["path"])
        if (record["path"] in records or record.get("target") != relative.parts[0]
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,200}", relative.name) is None
                or type(record.get("size_bytes")) is not int or not 0 < record["size_bytes"] <= DELIVERY_MAX_ZIP
                or not isinstance(record.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
            raise ValueError("Invalid or duplicate delivery record")
        if record.get("kind") in ("patch", "bootstrap"):
            parsed = urlsplit(record["url"])
            prefix = "/downloads/patches/" + BASE + "/" if record["kind"] == "patch" else "/downloads/update-bootstrap/1/"
            if (parsed.scheme+"://"+parsed.netloc != trust["origin"] or parsed.query or parsed.fragment
                    or not parsed.path.startswith(prefix) or "/../" in parsed.path or "%" in parsed.path or "\\" in parsed.path
                    or PurePosixPath(parsed.path).name != relative.name or parsed.path in destinations):
                raise ValueError("Invalid delivery public artifact binding")
            if record["kind"] == "bootstrap" and record["url"] != (trust["origin"]+"/downloads/update-bootstrap/1/"+str(index["build_number"])+"/"+record["target"]+"/"+relative.name):
                raise ValueError("Delivery bootstrap destination differs from its release")
            destinations.add(parsed.path)
        elif record.get("kind") != "manifest" or relative.name != "latest.json" or record["size_bytes"] > 262144:
            raise ValueError("Invalid delivery manifest record")
        records[record["path"]] = record
    for target in TARGETS:
        matching = [value for value in records.values() if value["target"] == target]
        if sorted(value["kind"] for value in matching) != ["bootstrap", "manifest", "patch"]:
            raise ValueError("A complete platform pair is required")
        raw = (bundle / target / "latest.json").read_bytes()
        manifest_record = records[target+"/latest.json"]
        if (len(raw) != manifest_record["size_bytes"] or hashlib.sha256(raw).hexdigest() != manifest_record["sha256"]
                or index["targets"][target] != {"manifest_sha256": manifest_record["sha256"], "manifest_size_bytes": len(raw)}):
            raise ValueError("Delivery platform bytes do not match the signed index")
        body = verify_envelope(raw, trust, target=target, installed_build=build, highest_sequence=sequence)
        if (any(body[field] != index[field] for field in ("version", "build_number", "source_commit", "source_digest", "sequence"))
                or datetime.fromisoformat(body["issued_at"].replace("Z", "+00:00")) != issued):
            raise ValueError("Delivery platform identity differs from the signed index")
        patch = next(value for value in matching if value["kind"] == "patch")
        if any(patch[field] != body["artifact"][field] for field in ("url", "size_bytes", "sha256")):
            raise ValueError("Delivery patch differs from its platform envelope")
    return index, records


def _bounded_zip_directory(archive: Path) -> None:
    # Bound central-directory allocation before zipfile parses attacker headers.
    with archive.open("rb") as handle:
        handle.seek(0, 2); size = handle.tell(); handle.seek(max(0, size-65577))
        tail = handle.read(65577)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail)-position < 22:
        raise ValueError("Delivery ZIP end record is missing")
    _, disk, central_disk, disk_entries, entries, central_size, central_offset, comment = struct.unpack("<4s4H2LH", tail[position:position+22])
    end_offset = size-len(tail)+position
    # This bounded, <=4GiB artifact format needs no ZIP64. Python otherwise
    # follows a locator even when small legacy fields hide an enormous ZIP64
    # central directory, before our per-member limits can run.
    zip64_locator = position >= 20 and tail[position-20:position-16] == b"PK\x06\x07"
    if (disk or central_disk or disk_entries != entries or not 7 <= entries <= 9
            or zip64_locator or entries == 0xffff or central_size == 0xffffffff or central_offset == 0xffffffff
            or central_size > 1024**2 or central_offset+central_size != end_offset
            or position+22+comment != len(tail)):
        raise ValueError("Delivery ZIP directory exceeds its limit")


def extract_delivery_zip(archive: Path, destination: Path, metadata: Path, index: dict) -> Path:
    _bounded_zip_directory(archive)
    expected = {value["path"]: value for value in index["files"]}
    expected["release-index.json"] = {"size_bytes": (metadata/"release-index.json").stat().st_size,
                                     "sha256": digest(metadata/"release-index.json")}
    if sum(value["size_bytes"] for value in expected.values()) > 12*1024**3:
        raise ValueError("Delivery expanded size exceeds its limit")
    destination.mkdir(mode=0o700)
    seen = set(); directories = set()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            original = getattr(member, "orig_filename", member.filename)
            name = member.filename
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (original != name or member.flag_bits & 1 or "\\" in name or any(ord(c) < 32 for c in name)
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or len(member.extra) > 65536 or len(member.comment) > 4096 or mode & 0o7000):
                raise ValueError("Unsupported delivery ZIP entry")
            if member.is_dir():
                if name not in {target+"/" for target in TARGETS} or kind not in (0, stat.S_IFDIR) or member.file_size or name.casefold() in directories:
                    raise ValueError("Invalid delivery ZIP directory")
                directories.add(name.casefold()); continue
            if (kind not in (0, stat.S_IFREG) or name not in expected or name.casefold() in seen
                    or member.file_size != expected[name]["size_bytes"]
                    or member.file_size > max(1, member.compress_size)*1000):
                raise ValueError("Delivery ZIP contents differ from the signed full file set")
            seen.add(name.casefold())
            path = destination/name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            count, hashed = 0, hashlib.sha256()
            with source.open(member) as received, path.open("xb") as target:
                path.chmod(0o600)
                while True:
                    block=received.read(min(1024**2, member.file_size-count+1))
                    if not block:break
                    count += len(block)
                    if count > member.file_size:raise ValueError("Delivery ZIP entry exceeds its signed length")
                    hashed.update(block);target.write(block)
                target.flush();os.fsync(target.fileno())
            if count != member.file_size or hashed.hexdigest() != expected[name]["sha256"]:
                raise ValueError("Delivery ZIP file verification failed")
        if seen != {name.casefold() for name in expected}:
            raise ValueError("Delivery ZIP is incomplete")
    for name in ("release-index.json", *(target+"/latest.json" for target in TARGETS)):
        if (destination/name).read_bytes() != (metadata/name).read_bytes():
            raise ValueError("Delivery ZIP changed the original signed metadata bytes")
    return destination


def hydrate_delivery_reference(bundle: Path, public: Path, trust_path: Path, *, downloader=None) -> Path:
    from desktop_updates.protocol import strict_json
    try:
        files={path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if not path.is_dir()}
        if files != {DELIVERY_REFERENCE, "release-index.json", *(target+"/latest.json" for target in TARGETS)}:
            raise ValueError("Delivery reference must contain exactly four metadata files")
        for name in files:
            candidate = bundle/name
            maximum = 65536 if name == DELIVERY_REFERENCE else 262144
            if candidate.is_symlink() or not candidate.is_file() or not 0 < candidate.stat().st_size <= maximum:
                raise ValueError("Delivery metadata must be bounded ordinary files")
        index, records = _delivery_metadata(bundle, trust_path, public)
        reference = strict_json((bundle/DELIVERY_REFERENCE).read_bytes(), maximum=65536)
        if (set(reference) != {"format", "format_version", "artifact", "source", "index_sha256"}
                or reference.get("format") != "ljqc-desktop-delivery-reference" or type(reference.get("format_version")) is not int or reference["format_version"] != 1
                or reference.get("index_sha256") != digest(bundle/"release-index.json")):
            raise ValueError("Invalid delivery reference identity")
        artifact, origin = reference["artifact"], reference["source"]
        if (not isinstance(artifact, dict) or set(artifact) != {"url", "size_bytes", "sha256", "kind"}
                or artifact.get("kind") != "github-artifact-zip" or type(artifact.get("size_bytes")) is not int
                or not 0 < artifact["size_bytes"] <= DELIVERY_MAX_ZIP
                or not isinstance(artifact.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
                or not isinstance(origin, dict) or set(origin) != {"repository", "run_id", "run_attempt", "artifact_id"}
                or origin.get("repository") != DELIVERY_REPOSITORY
                or any(type(origin.get(field)) is not int or origin[field] <= 0 for field in ("run_id", "run_attempt", "artifact_id"))):
            raise ValueError("Invalid delivery artifact provenance")
        validate_delivery_url(artifact["url"])
        expanded = sum(value["size_bytes"] for value in records.values()) + (bundle/"release-index.json").stat().st_size
        if shutil.disk_usage(bundle).free < artifact["size_bytes"]+expanded+1024**3:
            raise ValueError("Not enough private staging space for delivery")
        archive = bundle/".delivery-artifact.zip"
        (downloader or _download_delivery_archive)(artifact["url"], archive, artifact["size_bytes"], artifact["sha256"])
        if (not archive.is_file() or archive.is_symlink() or archive.stat().st_size != artifact["size_bytes"]
                or digest(archive) != artifact["sha256"]):
            raise ValueError("Delivery ZIP bytes differ from the GitHub artifact digest")
        destination = bundle/".delivery-payload"
        extract_delivery_zip(archive, destination, bundle, index)
        result = verify_bundle(destination, trust_path)
        if canonical(result) != canonical(index):
            raise ValueError("Hydrated delivery changed its signed release identity")
        return destination
    except DeliveryReferenceError as error:
        raise error from None
    except Exception:
        raise DeliveryReferenceError("Delivery reference verification failed; the previous channel remains selected") from None


class _BoundedPublicationTarInfo(tarfile.TarInfo):
    def _charge_header(self, archive):
        total = getattr(archive, "_ljqc_header_bytes", 0) + self.size
        if self.size > 65536 or total > 4 * 1024**2:
            raise ValueError("Publication extended header exceeds its limit")
        archive._ljqc_header_bytes = total

    def _proc_pax(self, archive):
        self._charge_header(archive)
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        self._charge_header(archive)
        return super()._proc_gnulong(archive)


def receive(staging_root: Path) -> Path:
    """Receive regular publication files from stdin; never interpret shell text."""
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".incoming-", dir=staging_root))
    seen, total = set(), 0
    try:
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|", tarinfo=_BoundedPublicationTarInfo) as archive:
            for member in archive:
                if len(seen) >= 32 or not member.isfile() or member.name in seen or member.sparse is not None:
                    raise ValueError("Only a small regular-file publication bundle is accepted")
                if member.name not in ("release-index.json", DELIVERY_REFERENCE):
                    safe_relative(member.name)
                if (member.name == DELIVERY_REFERENCE and member.size > 65536) or (member.name.endswith(".json") and member.size > 262144):
                    raise ValueError("Publication metadata exceeds its size limit")
                total += member.size
                if total > 12 * 1024**3 or member.size < 0:
                    raise ValueError("Publication bundle exceeds its size limit")
                if shutil.disk_usage(temporary).free < member.size + 1024**3:
                    raise ValueError("Not enough staging space for this publication")
                path = temporary / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                content = archive.extractfile(member)
                if content is None:
                    raise ValueError("Missing publication content")
                with path.open("xb") as destination:
                    shutil.copyfileobj(content, destination)
                seen.add(member.name)
        if "release-index.json" not in seen:
            raise ValueError("Publication index is missing")
        return temporary
    except BaseException:
        shutil.rmtree(temporary)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    signing = sub.add_parser("sign")
    signing.add_argument("--inputs", type=Path, required=True)
    signing.add_argument("--output", type=Path, required=True)
    signing.add_argument("--trust", type=Path, default=ROOT / "resources/desktop_update_trust.json")
    signing.add_argument("--private-key-file", type=Path)
    signing.add_argument("--key-id", default="release-2026-11")
    signing.add_argument("--expires-days", type=int, default=14)
    signing.add_argument("--sequence", type=int)
    refreshing = sub.add_parser("refresh-metadata")
    refreshing.add_argument("--output", type=Path, required=True)
    refreshing.add_argument("--trust", type=Path, default=ROOT / "resources/desktop_update_trust.json")
    refreshing.add_argument("--private-key-file", type=Path)
    refreshing.add_argument("--key-id", default="release-2026-11")
    refreshing.add_argument("--expires-days", type=int, default=14)
    publishing = sub.add_parser("promote")
    publishing.add_argument("--bundle", type=Path, required=True)
    publishing.add_argument("--public-root", type=Path, default=Path("/var/www/ljqc-public"))
    publishing.add_argument("--trust", type=Path, default=ROOT / "resources/desktop_update_trust.json")
    receiving = sub.add_parser("receive-and-promote")
    receiving.add_argument("--staging-root", type=Path, default=Path("/var/lib/ljqc-update-publisher"))
    receiving.add_argument("--public-root", type=Path, default=Path("/var/www/ljqc-public"))
    receiving.add_argument("--trust", type=Path, default=ROOT / "resources/desktop_update_trust.json")
    packing = sub.add_parser("bundle")
    packing.add_argument("--source", type=Path, required=True)
    packing.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "sign":
        result = sign_candidates(args.inputs, args.output, args.trust, key_path=args.private_key_file,
                                 key_id=args.key_id, lifetime_days=args.expires_days, sequence=args.sequence)
        print(json.dumps({"status": "signed_not_published", "release_id": result["release_id"]}))
    elif args.command == "refresh-metadata":
        result = refresh_metadata(args.output, args.trust, key_path=args.private_key_file,
                                  key_id=args.key_id, lifetime_days=args.expires_days)
        print(json.dumps({"status": "metadata_refreshed_not_published", "release_id": result["release_id"], "sequence": result["sequence"]}))
    elif args.command == "promote":
        print(json.dumps(promote(args.bundle, args.public_root, args.trust)))
    elif args.command == "receive-and-promote":
        # A forced SSH command must ignore arbitrary original command strings.
        if os.environ.get("SSH_ORIGINAL_COMMAND", "") not in ("", "ljqc-update-upload"):
            raise ValueError("This SSH identity only accepts ljqc-update-upload")
        bundle = receive(args.staging_root)
        if (bundle/DELIVERY_REFERENCE).exists():
            try:
                payload = hydrate_delivery_reference(bundle, args.public_root, args.trust)
                result = promote(payload, args.public_root, args.trust)
            except DeliveryReferenceError as error:
                raise error from None
            except Exception:
                raise DeliveryReferenceError("Reference delivery did not publish; the previous channel remains selected") from None
            finally:
                # Includes the short-lived reference URL, downloaded ZIP and
                # private hydrated payload; no SAS persists after this command.
                shutil.rmtree(bundle)
        else:
            result = promote(bundle, args.public_root, args.trust)
            shutil.rmtree(bundle)
        print(json.dumps(result))
    else:
        with tarfile.open(args.output, "x") as archive:
            for path in sorted(args.source.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Publication bundles cannot contain symbolic links")
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(args.source).as_posix(), recursive=False)


if __name__ == "__main__":
    main()
