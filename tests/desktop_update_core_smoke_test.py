"""Isolated signature, download and hostile-archive updater regression tests."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from desktop_updates.protocol import BASE_RELEASE, UpdateError, canonical_json, validate_trust, verify_envelope
from desktop_updates.payload import extract_verified_payload, verify_installed_payload
from desktop_updates import payload as payload_module
from desktop_updates import transport

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
KEY = Ed25519PrivateKey.generate()
TRUST = {"format_version": 1, "product": "LJQCApp", "channel": "dev", "base_release": BASE_RELEASE,
         "origin": "https://159.75.243.120", "keys": {"test": base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}}


def make_manifest(target="windows-x86_64"):
    return {"format": "ljqc-desktop-update", "format_version": 1, "product": "LJQCApp",
            "edition": "desktop", "channel": "dev", "target": target, "base_release": BASE_RELEASE,
            "version": "1.0.1", "build_number": 2026110701, "sequence": 2026110701,
            "issued_at": (NOW - timedelta(hours=1)).isoformat(), "expires_at": (NOW + timedelta(days=7)).isoformat(),
            "min_updater_version": 1, "min_os_version": "10.0.17763" if target == "windows-x86_64" else "13.0",
            "data_format_version": 1, "source_commit": "a" * 40, "source_digest": "b" * 64,
            "artifact": {"url": "https://159.75.243.120/downloads/patches/base/program.tar.gz", "kind": "tar.gz",
                         "size_bytes": 1024, "unpacked_size_bytes": 8192, "sha256": "c" * 64},
            "payload": {"entrypoint": "LJQCApp.exe" if target == "windows-x86_64" else "LJQCApp.app/Contents/MacOS/LJQCApp",
                        "manifest_sha256": "d" * 64}}


def signed(manifest):
    return canonical_json({"signed": manifest, "key_id": "test", "signature": base64.b64encode(KEY.sign(canonical_json(manifest))).decode()})


def verify(raw, **kwargs):
    return verify_envelope(raw, TRUST, target=kwargs.pop("target", "windows-x86_64"),
                           installed_build=kwargs.pop("installed_build", 2026110606),
                           highest_sequence=kwargs.pop("highest_sequence", 0), now=NOW, **kwargs)


def make_archive(directory, *, target="windows-x86_64", extra=None, file_bytes=b"test-program", mutate_record=None,
                 entry_mode=0o755, include_frameworks=True):
    manifest = make_manifest(target)
    entry = manifest["payload"]["entrypoint"]
    files = [(entry, file_bytes, entry_mode, "file", None)]
    if target == "macos-arm64" and include_frameworks:
        files += [("LJQCApp.app/Contents/Frameworks/Qt/Versions/A/Qt", b"framework", 0o755, "file", None),
                  ("LJQCApp.app/Contents/Frameworks/Qt/Versions/Current", b"", 0o777, "symlink", "A"),
                  ("LJQCApp.app/Contents/Frameworks/Qt/Qt", b"framework", 0o777, "symlink", "Versions/Current/Qt")]
    records = []
    for name, data, mode, kind, link in files:
        record = {"path": name, "kind": kind}
        if kind == "file" or data:
            record.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if kind == "file":
            record["mode"] = mode
        else:
            record["target"] = link
        records.append(record)
    if mutate_record:
        mutate_record(records)
    release = {key: manifest[key] for key in ("version", "build_number", "target", "source_digest", "data_format_version")}
    release.update(git_commit=manifest["source_commit"], source_dirty=False)
    payload = {"format": "ljqc-program-payload", "format_version": 1, "release": release,
               "mutable_data_included": False, "files": records, "file_count": len(records)}
    payload_bytes = canonical_json(payload)
    manifest["payload"]["manifest_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    files.append(("release_payload_manifest.json", payload_bytes, 0o644, "file", None))
    if extra:
        files += extra
    archive = Path(directory) / "program.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data, mode, kind, link in files:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = link
                info.size = 0
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = link
            else:
                info.size = len(data)
            bundle.addfile(info, io.BytesIO(data) if kind == "file" else None)
    manifest["artifact"].update(size_bytes=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                                unpacked_size_bytes=sum(len(data) for _, data, _, kind, _ in files if kind == "file"))
    return archive, manifest


class SignatureTests(unittest.TestCase):
    def test_valid_both_targets(self):
        for target in ("windows-x86_64", "macos-arm64"):
            with self.subTest(target=target):
                result = verify(signed(make_manifest(target)), target=target)
                self.assertEqual(result["base_release"], BASE_RELEASE)
                self.assertEqual(len(result["_metadata_digest"]), 64)

    def test_corrupt_embedded_trust_rejected(self):
        for key, value in (("format_version", True), ("base_release", "other"), ("origin", "http://159.75.243.120"),
                           ("keys", {"test": {"public_key": "not-a-string"}})):
            with self.subTest(key=key), self.assertRaises(UpdateError):
                validate_trust({**TRUST, key: value})

    def test_signature_modified_and_unknown_key(self):
        envelope = json.loads(signed(make_manifest()))
        envelope["signed"]["version"] = "1.0.9"
        with self.assertRaises(UpdateError):
            verify(canonical_json(envelope))
        envelope["key_id"] = "server-supplied-key"
        with self.assertRaises(UpdateError):
            verify(canonical_json(envelope))

    def test_duplicate_outer_and_nested_fields(self):
        raw = signed(make_manifest())
        for value in (raw[:-1] + b',"key_id":"test"}', raw.replace(b'"version":"1.0.1"', b'"version":"1.0.1","version":"1.0.1"')):
            with self.assertRaises(UpdateError):
                verify(value)

    def test_replay_and_build_downgrade(self):
        with self.assertRaises(UpdateError):
            verify(signed(make_manifest()), highest_sequence=2026110702)
        with self.assertRaises(UpdateError):
            verify(signed(make_manifest()), installed_build=2026110702)

    def test_wrong_target_base_channel_format_and_minimum_updater(self):
        for key, value in (("target", "macos-arm64"), ("base_release", "other"), ("channel", "stable"),
                           ("data_format_version", 2), ("min_updater_version", 2), ("format_version", True)):
            with self.subTest(key=key), self.assertRaises(UpdateError):
                manifest = make_manifest()
                manifest[key] = value
                verify(signed(manifest))

    def test_expired_future_and_excessive_lifetime(self):
        for key, value in (("expires_at", (NOW - timedelta(seconds=1)).isoformat()),
                           ("issued_at", (NOW + timedelta(minutes=6)).isoformat()),
                           ("expires_at", (NOW + timedelta(days=32)).isoformat())):
            with self.subTest(key=key, value=value), self.assertRaises(UpdateError):
                manifest = make_manifest()
                manifest[key] = value
                verify(signed(manifest))

    def test_untrusted_url_and_size_limits(self):
        for key, value in (("url", "http://159.75.243.120/downloads/patches/x"), ("url", "https://evil.example/x"),
                           ("url", "https://159.75.243.120/downloads/releases/x"),
                           ("url", "https://159.75.243.120/downloads/patches/%2e%2e/private"), ("size_bytes", 5 * 1024**3),
                           ("unpacked_size_bytes", 13 * 1024**3), ("size_bytes", True)):
            with self.subTest(key=key, value=value), self.assertRaises(UpdateError):
                manifest = make_manifest()
                manifest["artifact"][key] = value
                verify(signed(manifest))


class PayloadTests(unittest.TestCase):
    def test_complete_windows_and_mac_framework_links(self):
        targets = ("windows-x86_64",) if sys.platform == "win32" else ("windows-x86_64", "macos-arm64")
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                archive, manifest = make_archive(temporary, target=target)
                result = extract_verified_payload(archive, Path(temporary) / "candidate", manifest)
                self.assertFalse(result["mutable_data_included"])
                self.assertEqual((Path(temporary) / "candidate" / manifest["payload"]["entrypoint"]).read_bytes(), b"test-program")

    def test_mac_entry_mode_remains_required_when_host_cannot_express_posix_modes(self):
        for entry_mode in (0o755, 0o644):
            with self.subTest(entry_mode=oct(entry_mode)), tempfile.TemporaryDirectory() as temporary:
                archive, manifest = make_archive(temporary, target="macos-arm64", entry_mode=entry_mode,
                                                 include_frameworks=False)
                # These are fixed ordinary fixture files, copied with the same
                # non-executable stat mode returned by Windows. No symlinks or
                # native Mac executable are needed to exercise cross-host QA.
                candidate = Path(temporary) / "cross-host-candidate"
                candidate.mkdir()
                with tarfile.open(archive, "r:gz") as bundle:
                    self.assertEqual(bundle.getmember(manifest["payload"]["entrypoint"]).mode, entry_mode)
                    for member in bundle:
                        target = candidate / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(member) as content:
                            target.write_bytes(content.read())
                entries = {path.relative_to(candidate).as_posix():
                           {"kind": "directory" if path.is_dir() else "file", "mode": 0o644}
                           for path in candidate.rglob("*")}
                if entry_mode == 0o755:
                    payload_module._verify_extracted(candidate, manifest, entries, check_modes=False)
                    extract_verified_payload(archive, Path(temporary) / "verified", manifest)
                else:
                    with self.assertRaises(UpdateError):
                        payload_module._verify_extracted(candidate, manifest, entries, check_modes=False)
                    with self.assertRaises(UpdateError):
                        extract_verified_payload(archive, Path(temporary) / "rejected", manifest)

    def test_installed_candidate_revalidated_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = make_archive(temporary)
            candidate = Path(temporary) / "candidate"
            extract_verified_payload(archive, candidate, manifest)
            self.assertEqual(verify_installed_payload(candidate, manifest)["release"]["version"], "1.0.1")
            (candidate / "extra.bin").write_bytes(b"extra")
            with self.assertRaises(UpdateError):
                verify_installed_payload(candidate, manifest)
            (candidate / "extra.bin").unlink()
            (candidate / "LJQCApp.exe").write_bytes(b"tampered")
            with self.assertRaises(UpdateError):
                verify_installed_payload(candidate, manifest)

    def _reject_extra(self, extra):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = make_archive(temporary, extra=extra)
            candidate = Path(temporary) / "candidate"
            with self.assertRaises(UpdateError):
                extract_verified_payload(archive, candidate, manifest)
            self.assertFalse(candidate.exists())

    def test_traversal_absolute_duplicate_case_and_ads(self):
        for name in ("../escape", "/absolute", "a/../escape", "LJQCApp.exe", "ljqcapp.EXE", "c:stream", "a\\escape", "CON", "dot."):
            with self.subTest(name=name):
                self._reject_extra([(name, b"bad", 0o644, "file", None)])

    def test_external_cyclic_links_and_hardlinks(self):
        for name, link, kind in (("outside", "../escape", "symlink"), ("cycle", "cycle", "symlink"),
                                 ("absolute", "/tmp/escape", "symlink"), ("hard", "LJQCApp.exe", "hardlink")):
            with self.subTest(name=name):
                self._reject_extra([(name, b"", 0o777 if kind == "symlink" else 0o644, kind, link)])

    def test_link_parent_never_writes_outside(self):
        self._reject_extra([("linked", b"", 0o777, "symlink", "../escape"), ("linked/file", b"bad", 0o644, "file", None)])

    def test_database_and_config_forbidden(self):
        for name in ("data/test.db", "storage_config.json", "backups/record.txt", "report_files/data.bin"):
            with self.subTest(name=name):
                self._reject_extra([(name, b"bad", 0o644, "file", None)])
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = make_archive(temporary, file_bytes=b"SQLite format 3\x00bad")
            with self.assertRaises(UpdateError):
                extract_verified_payload(archive, Path(temporary) / "candidate", manifest)

    def test_source_file_hash_and_mode_mismatch(self):
        for mutate in (lambda records: records[0].update(sha256="0" * 64),
                       lambda records: records[0].update(mode=0o644)):
            with tempfile.TemporaryDirectory() as temporary:
                archive, manifest = make_archive(temporary, mutate_record=mutate)
                with self.assertRaises(UpdateError):
                    extract_verified_payload(archive, Path(temporary) / "candidate", manifest)

    def test_declared_expansion_limit_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = make_archive(temporary)
            manifest["artifact"]["unpacked_size_bytes"] = 1
            with self.assertRaises(UpdateError):
                extract_verified_payload(archive, Path(temporary) / "candidate", manifest)
            candidate = Path(temporary) / "existing"
            candidate.mkdir()
            (candidate / "keep").write_text("unchanged")
            with self.assertRaises(UpdateError):
                extract_verified_payload(archive, candidate, manifest)
            self.assertEqual((candidate / "keep").read_text(), "unchanged")

    def test_artifact_tamper_does_not_create_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = make_archive(temporary)
            archive.write_bytes(archive.read_bytes() + b"changed")
            with self.assertRaises(UpdateError):
                extract_verified_payload(archive, Path(temporary) / "candidate", manifest)
            self.assertFalse((Path(temporary) / "candidate").exists())


class Response(io.BytesIO):
    def __init__(self, data, declared=None, url="https://159.75.243.120/downloads/patches/program.tar.gz"):
        super().__init__(data)
        self.status = 200
        self.headers = {} if declared is None else {"Content-Length": str(declared)}
        self.url = url
    def geturl(self):
        return self.url


class TransportTests(unittest.TestCase):
    def _manifest(self, data):
        manifest = make_manifest()
        manifest["artifact"].update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return manifest

    def test_download_exact_bytes_atomic_cache_and_stale_part_restart(self):
        data = b"real bytes"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "program.tar.gz"
            path.with_name(path.name + ".part").write_bytes(b"stale partial")
            with patch.object(transport, "_open_https", return_value=Response(data, len(data))):
                transport.download_artifact(self._manifest(data), path)
            self.assertEqual(path.read_bytes(), data)
            with patch.object(transport, "_open_https", side_effect=AssertionError("cached")):
                self.assertEqual(transport.download_artifact(self._manifest(data), path), path)

    def test_truncated_download_retries_but_never_promotes(self):
        data = b"complete program"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "program.tar.gz"
            with patch.object(transport, "_open_https", side_effect=lambda *_: Response(data[:3], len(data))) as opened:
                with self.assertRaises(UpdateError):
                    transport.download_artifact(self._manifest(data), path)
            self.assertEqual(opened.call_count, 2)
            self.assertFalse(path.exists())

    def test_extra_bytes_and_wrong_hash_not_promoted(self):
        for wrong in (b"program extra", b"pROGRAM"):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "program.tar.gz"
                with patch.object(transport, "_open_https", return_value=Response(wrong)):
                    with self.assertRaises(UpdateError):
                        transport.download_artifact(self._manifest(b"program"), path)
                self.assertFalse(path.exists())

    def test_external_or_downgraded_redirect_rejected(self):
        handler = transport._OriginRedirects("https://159.75.243.120")
        for url in ("https://evil.example/payload", "http://159.75.243.120/payload", "file:///tmp/payload"):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                handler.redirect_request(None, None, 302, "", {}, url)

    def test_metadata_reader_rejects_arbitrary_server_paths(self):
        with patch.object(transport, "_open_https", side_effect=AssertionError("must not fetch")):
            with self.assertRaises(UpdateError):
                transport.fetch_metadata_url(TRUST, "https://159.75.243.120/private/config")

    def test_header_stall_obeys_caller_deadline_and_limits_inflight_reads(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def stalled(*args):
            entered.set()
            release.wait(1)
            finished.set()
            return b"{}"
        started = time.monotonic()
        try:
            with patch.object(transport, "MANIFEST_TIMEOUT_SECONDS", .03), \
                 patch.object(transport, "_read_metadata", side_effect=stalled):
                with self.assertRaises(UpdateError):
                    transport.fetch_manifest(TRUST, "windows-x86_64")
                self.assertTrue(entered.is_set())
                with self.assertRaises(UpdateError):
                    transport.fetch_manifest(TRUST, "windows-x86_64")
                self.assertLess(time.monotonic() - started, .5)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
            # The worker releases the gate just after returning from the stub.
            with transport._METADATA_GATE:
                pass

    def test_slow_metadata_total_deadline_keeps_current_program(self):
        response = Response(b"metadata bytes", 14)
        with patch.object(transport, "_open_https", return_value=response), \
             patch.object(transport.time, "monotonic", side_effect=[0, 1, 6]):
            with self.assertRaises(UpdateError):
                transport.fetch_manifest(TRUST, "windows-x86_64")

    def test_manifest_path_is_fixed_and_bounded(self):
        expected = "https://159.75.243.120/updates/desktop/dev/windows-x86_64/latest.json"
        def opened(url, origin, **kwargs):
            self.assertEqual(kwargs.get("timeout"), 5)
            self.assertEqual(url, expected)
            return Response(b"{}", 2, url)
        with patch.object(transport, "_open_https", side_effect=opened):
            self.assertEqual(transport.fetch_manifest(TRUST, "windows-x86_64"), b"{}")
        with patch.object(transport, "_open_https", return_value=Response(b"x" * 100, 100)):
            with self.assertRaises(UpdateError):
                transport.fetch_manifest({**TRUST, "max_manifest_bytes": 32}, "windows-x86_64")


if __name__ == "__main__":
    unittest.main()
