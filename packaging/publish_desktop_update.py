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
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
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
                if member.name != "release-index.json":
                    safe_relative(member.name)
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
        result = promote(bundle, args.public_root, args.trust)
        # Only this invocation's validated private staging directory is removed;
        # immutable public releases and every previous program remain untouched.
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
