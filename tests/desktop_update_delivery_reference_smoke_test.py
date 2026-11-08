"""Verify bounded delivery references using only temporary inert QA artifacts."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import shutil
import stat
import struct
import sys
import tarfile
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request
from urllib.parse import urlencode
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import publish_desktop_update as publication
from desktop_updates.protocol import UpdateError

REJECTIONS = (ValueError, UpdateError, InvalidSignature, zipfile.BadZipFile)


class DeliveryReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ljqc-reference-qa-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = Ed25519PrivateKey.generate()
        self.trust = {"format_version": 1, "product": "LJQCApp", "channel": "dev",
            "base_release": publication.BASE, "origin": "https://159.75.243.120",
            "keys": {"qa": base64.b64encode(self.key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")}}
        self.trust_path = self.root / "qa-public-trust.json"
        self.trust_path.write_text(json.dumps(self.trust), encoding="utf-8")
        self.original = self.root / "original-signed"
        self.original.mkdir()
        now = datetime.now(timezone.utc)
        self.common = {"version": "1.0.1", "build_number": 2026110802,
            "product": "LJQCApp", "channel": "dev", "base_release": publication.BASE,
            "source_commit": "c" * 40, "source_digest": "d" * 64,
            "issued_at": now.isoformat(), "sequence": int(now.timestamp() * 1_000_000)}
        records, targets = [], {}
        for target in publication.TARGETS:
            directory = self.original / target
            directory.mkdir()
            patch_name = "qa-" + target + "-patch.tar.gz"
            patch_file = directory / patch_name
            patch_file.write_bytes(("inert signed patch fixture: " + target).encode("ascii"))
            artifact = {"kind": "tar.gz", "size_bytes": patch_file.stat().st_size,
                "unpacked_size_bytes": 4096, "sha256": publication.digest(patch_file),
                "url": self.trust["origin"] + "/downloads/patches/" + publication.BASE +
                    "/1.0.1/2026110802/" + target + "/" + patch_name}
            manifest = {**self.common, "format": "ljqc-desktop-update", "format_version": 1,
                "edition": "desktop", "target": target, "data_format_version": 1,
                "min_updater_version": 1, "min_os_version": "10.0" if target.startswith("windows") else "13.0",
                "expires_at": (now + timedelta(days=14)).isoformat(), "artifact": artifact,
                "payload": {"entrypoint": "LJQCApp.exe" if target.startswith("windows") else
                    "LJQCApp.app/Contents/MacOS/LJQCApp", "manifest_sha256": "e" * 64}}
            latest = directory / "latest.json"
            latest.write_bytes(self.signed(manifest))
            targets[target] = {"manifest_sha256": publication.digest(latest),
                "manifest_size_bytes": latest.stat().st_size}
            records.append({"kind": "manifest", "target": target, "path": target + "/latest.json",
                "sha256": publication.digest(latest), "size_bytes": latest.stat().st_size})
            records.append({"kind": "patch", "target": target, "path": target + "/" + patch_name,
                **{key: artifact[key] for key in ("url", "size_bytes", "sha256")}})
            helper = directory / ("qa-" + target + (".exe" if target.startswith("windows") else ".dmg"))
            helper.write_bytes(("inert bootstrap fixture: " + target).encode("ascii"))
            records.append({"kind": "bootstrap", "target": target, "path": target + "/" + helper.name,
                "size_bytes": helper.stat().st_size, "sha256": publication.digest(helper),
                "url": self.trust["origin"] + "/downloads/update-bootstrap/1/2026110802/" + target + "/" + helper.name})
        self.index = {**self.common, "format": "ljqc-desktop-release", "format_version": 1,
            "source_dirty": False, "release_id": "1.0.1-2026110802-" + "d" * 12,
            "targets": targets, "files": records}
        (self.original / "release-index.json").write_bytes(self.signed(self.index))
        publication.verify_bundle(self.original, self.trust_path)
        self.archive = self.make_zip("delivery.zip")
        self.incoming = self.root / "staging/incoming"
        self.incoming.mkdir(parents=True)
        for name in self.metadata_names():
            destination = self.incoming / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.original / name, destination)
        self.vendor_url = ("https://productionresultssa6.blob.core.windows.net/actions-results/"
            "11111111-1111-1111-1111-111111111111/workflow-job-run-22222222-2222-2222-2222-222222222222/"
            "artifacts/" + "a" * 64 + ".zip?" + urlencode({"sp": "r", "sr": "b", "sig": "QA_ONLY_SECRET",
                "se": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")}))
        self.reference = {"format": "ljqc-desktop-delivery-reference", "format_version": 1,
            "index_sha256": publication.digest(self.incoming / "release-index.json"),
            "artifact": {"kind": "github-artifact-zip", "url": self.vendor_url,
                "size_bytes": self.archive.stat().st_size, "sha256": publication.digest(self.archive)},
            "source": {"repository": "Arcjang999/LJQCapp_clean", "run_id": 42433269255,
                "run_attempt": 1, "artifact_id": 123456789}}
        self.write_reference()
        self.public = self.root / "public"
        self.public.mkdir()
        self.downloads = []

    def signed(self, value):
        return publication.canonical(publication.envelope(value, self.key, "qa")) + b"\n"

    @staticmethod
    def metadata_names():
        return ("release-index.json", *(target + "/latest.json" for target in publication.TARGETS))

    def write_reference(self):
        (self.incoming / "delivery-reference.json").write_bytes(publication.canonical(self.reference) + b"\n")

    def make_zip(self, name, *, replace=None, omit=(), extra=()):
        path = self.root / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for original in sorted(self.original.rglob("*")):
                    if not original.is_file():
                        continue
                    relative = original.relative_to(self.original).as_posix()
                    if relative not in omit:
                        archive.writestr(relative, (replace or {}).get(relative, original.read_bytes()))
                for entry, raw in extra:
                    archive.writestr(entry, raw)
        return path

    def downloader(self, url, destination, expected_size, expected_hash):
        self.downloads.append(url)
        self.assertEqual(url, self.vendor_url)
        self.assertEqual(expected_size, self.archive.stat().st_size)
        self.assertEqual(expected_hash, publication.digest(self.archive))
        shutil.copyfile(self.archive, destination)
        return destination

    def hydrate(self):
        return publication.hydrate_delivery_reference(self.incoming, self.public,
            self.trust_path, downloader=self.downloader)

    def assert_no_download(self, action):
        with self.assertRaises(REJECTIONS):
            action()
        self.assertEqual(self.downloads, [])

    def extract(self, archive, name):
        return publication.extract_delivery_zip(archive, self.root / name, self.incoming, self.index)

    def test_valid_reference_downloads_once_and_retains_exact_signed_full_set(self):
        result = self.hydrate()
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(publication.verify_bundle(result, self.trust_path), self.index)
        actual = {path.relative_to(result).as_posix() for path in result.rglob("*") if path.is_file()}
        self.assertEqual(actual, {"release-index.json", *(row["path"] for row in self.index["files"])})
        for name in self.metadata_names():
            self.assertEqual((result / name).read_bytes(), (self.incoming / name).read_bytes())
        self.assertFalse((self.public / "updates/desktop/dev/current").exists())

    def test_bad_index_signature_prevents_any_vendor_request(self):
        path = self.incoming / "release-index.json"
        value = json.loads(path.read_text())
        value["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        path.write_bytes(publication.canonical(value))
        self.reference["index_sha256"] = publication.digest(path)
        self.write_reference()
        self.assert_no_download(self.hydrate)

    def test_bad_platform_signature_prevents_any_vendor_request(self):
        path = self.incoming / "windows-x86_64/latest.json"
        value = json.loads(path.read_text()); value["signed"]["min_updater_version"] = 2
        path.write_bytes(publication.canonical(value))
        self.assert_no_download(self.hydrate)

    def test_valid_signature_cannot_bypass_index_types_time_or_helper_binding(self):
        def wrong_helper_url(value):
            helper = next(row for row in value["files"] if row["kind"] == "bootstrap")
            helper["url"] = helper["url"].replace("/2026110802/", "/2026110803/")
        mutations = (lambda value: value.update(format_version=True),
            lambda value: value.update(build_number=True),
            lambda value: value.update(issued_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()),
            lambda value: value.update(issued_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
            wrong_helper_url)
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = json.loads(json.dumps(self.index)); mutate(value)
                path = self.incoming / "release-index.json"
                path.write_bytes(self.signed(value))
                self.reference["index_sha256"] = publication.digest(path)
                self.write_reference()
                self.assert_no_download(self.hydrate)

    def test_reference_hash_and_schema_must_match_before_any_request(self):
        mutations = [lambda r: r.update(index_sha256="0" * 64),
            lambda r: r.update(format_version=True),
            lambda r: r.update(unexpected="field"),
            lambda r: r["source"].update(repository="somebody/other-project"),
            lambda r: r["source"].update(run_attempt=True),
            lambda r: r["source"].update(artifact_id=0),
            lambda r: r["artifact"].update(sha256="z" * 64),
            lambda r: r["artifact"].update(size_bytes=True),
            lambda r: r["artifact"].update(size_bytes=4 * 1024**3 + 1)]
        original = json.loads(json.dumps(self.reference))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.reference = json.loads(json.dumps(original)); mutate(self.reference); self.write_reference()
                self.assert_no_download(self.hydrate)

    def test_reference_tiny_bundle_must_have_exactly_four_ordinary_files(self):
        (self.incoming / "windows-x86_64/unlisted.json").write_text("{}", encoding="utf-8")
        self.assert_no_download(self.hydrate)

    def test_older_signed_release_is_rejected_before_download(self):
        channel = self.public / "updates/desktop/dev"
        newer = {**self.index, "sequence": self.index["sequence"] + 1}
        selected_name = newer["release_id"] + "-" + str(newer["sequence"])
        selected = channel / "releases" / selected_name
        selected.mkdir(parents=True)
        (selected / "release-index.json").write_bytes(self.signed(newer))
        (channel / "current").symlink_to(Path("releases") / selected_name, target_is_directory=True)
        self.assert_no_download(self.hydrate)

    def test_current_index_external_symlink_is_rejected_before_download(self):
        channel = self.public / "updates/desktop/dev"
        selected_name = self.index["release_id"] + "-" + str(self.index["sequence"])
        selected = channel / "releases" / selected_name
        selected.mkdir(parents=True)
        external = self.root / "outside-public-index.json"
        external.write_bytes(self.signed(self.index))
        (selected / "release-index.json").symlink_to(external)
        (channel / "current").symlink_to(Path("releases") / selected_name, target_is_directory=True)
        self.assert_no_download(self.hydrate)
        self.assertEqual(external.read_bytes(), self.signed(self.index))

    def test_reference_receive_and_original_raw_receive_both_remain_supported(self):
        for reference_mode in (False, True):
            with self.subTest(reference_mode=reference_mode):
                source = self.incoming if reference_mode else self.original
                stream = BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    for path in sorted(source.rglob("*")):
                        if path.is_file():
                            archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
                stream.seek(0)
                with patch.object(publication.sys, "stdin", SimpleNamespace(buffer=stream)):
                    received = publication.receive(self.root / ("raw-receiver" if not reference_mode else "ref-receiver"))
                if reference_mode:
                    result = publication.hydrate_delivery_reference(received, self.public,
                        self.trust_path, downloader=self.downloader)
                    publication.verify_bundle(result, self.trust_path)
                else:
                    self.assertEqual(publication.verify_bundle(received, self.trust_path), self.index)

    def test_zip_missing_extra_and_duplicate_files_are_rejected(self):
        path = self.index["files"][1]["path"]
        archives = [self.make_zip("missing.zip", omit=(path,)),
            self.make_zip("extra.zip", extra=(("windows-x86_64/unlisted.exe", b"extra"),)),
            self.make_zip("duplicate.zip", extra=((path, (self.original / path).read_bytes()),))]
        for number, archive in enumerate(archives):
            with self.subTest(archive=archive.name), self.assertRaises(REJECTIONS):
                self.extract(archive, "bad-full-set-" + str(number))

    def test_zip_unsafe_and_case_conflicting_names_are_rejected(self):
        names = ("../escaped.txt", "/absolute.txt", "C:/escape.txt", "windows-x86_64\\escape.txt",
            "windows-x86_64/../escape.txt", "WINDOWS-X86_64/latest.json")
        for number, name in enumerate(names):
            archive = self.make_zip("bad-path-" + str(number) + ".zip", extra=((name, b"escape"),))
            with self.subTest(name=name), self.assertRaises(REJECTIONS):
                self.extract(archive, "bad-name-" + str(number))
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_zip_links_devices_and_directories_cannot_impersonate_payload_files(self):
        name = self.index["files"][1]["path"]
        for number, mode in enumerate((stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFDIR | 0o755)):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3; entry.external_attr = mode << 16
            archive = self.make_zip("bad-mode-" + str(number) + ".zip", omit=(name,), extra=((entry, b"target"),))
            with self.subTest(mode=mode), self.assertRaises(REJECTIONS):
                self.extract(archive, "bad-mode-output-" + str(number))

    def test_zip_encrypted_flag_is_rejected_without_opening_payload(self):
        archive = self.make_zip("encrypted-flag.zip")
        raw = bytearray(archive.read_bytes())
        local = raw.index(b"PK\x03\x04")
        central = raw.index(b"PK\x01\x02")
        for offset in (local + 6, central + 8):
            flags = struct.unpack_from("<H", raw, offset)[0]
            struct.pack_into("<H", raw, offset, flags | 1)
        archive.write_bytes(raw)
        with self.assertRaises(REJECTIONS):
            self.extract(archive, "encrypted-output")

    def test_zip64_locator_is_rejected_before_zipfile_allocates_directory(self):
        original = self.make_zip("zip64-original.zip")
        raw = original.read_bytes()
        position = raw.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(position, 0)
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
        for comment_size in (0, 65535):
            with self.subTest(comment_size=comment_size):
                end_record = bytearray(raw[position:position + 22])
                struct.pack_into("<H", end_record, 20, comment_size)
                archive = self.root / ("zip64-locator-" + str(comment_size) + ".zip")
                archive.write_bytes(raw[:position] + locator + end_record + b"x" * comment_size)
                with patch.object(publication.zipfile, "ZipFile", side_effect=AssertionError(
                        "An unbounded ZIP64 directory must never reach zipfile construction")) as constructor:
                    with self.assertRaises(ValueError):
                        self.extract(archive, "zip64-output-" + str(comment_size))
                    constructor.assert_not_called()

    def test_zip_file_lengths_and_exact_original_metadata_bytes_are_enforced(self):
        name = self.index["files"][1]["path"]
        cases = ({name: (self.original / name).read_bytes() + b"excess"},
            {"release-index.json": (self.original / "release-index.json").read_bytes() + b" "},
            {"macos-arm64/latest.json": (self.original / "macos-arm64/latest.json").read_bytes().rstrip(b"\n")})
        for number, replacement in enumerate(cases):
            archive = self.make_zip("bad-content-" + str(number) + ".zip", replace=replacement)
            with self.subTest(number=number), self.assertRaises(REJECTIONS):
                self.extract(archive, "bad-content-output-" + str(number))

    def test_vendor_urls_reject_credentials_other_hosts_and_non_https(self):
        self.assertEqual(publication.validate_delivery_url(self.vendor_url), self.vendor_url)
        invalid = (self.vendor_url.replace("https:", "http:", 1),
            "https://127.0.0.1/actions-results/a.zip", "https://169.254.169.254/latest/meta-data",
            "https://productionresultssa6.blob.core.windows.net.evil.example/a.zip",
            "https://evil.blob.core.windows.net/actions-results/a.zip",
            self.vendor_url.replace("https://", "https://user:password@", 1),
            self.vendor_url.replace(".net/", ".net:8443/", 1), self.vendor_url + "#fragment",
            "https://productionresultssa6.blob.core.windows.net/../secret.zip",
            "https://productionresultssa6.blob.core.windows.net/%2e%2e/secret.zip")
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(REJECTIONS):
                publication.validate_delivery_url(url)

    def test_vendor_sas_requires_short_lived_read_only_blob_access(self):
        prefix = self.vendor_url.split("?", 1)[0] + "?"
        now = datetime.now(timezone.utc)
        original = {"sp": "r", "sr": "b", "sig": "QA_ONLY_SECRET",
            "se": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")}
        modifications = ({"sp": "rw"}, {"sr": "c"}, {"sig": ""},
            {"se": (now - timedelta(seconds=10)).isoformat()},
            {"se": (now + timedelta(seconds=10)).isoformat()},
            {"se": (now + timedelta(hours=2)).isoformat()}, {"sig": "secret\n"})
        for change in modifications:
            with self.subTest(change=change), self.assertRaises(REJECTIONS):
                publication.validate_delivery_url(prefix + urlencode({**original, **change}))
        with self.assertRaises(REJECTIONS):
            publication.validate_delivery_url(self.vendor_url + "&sp=r")

    def test_default_downloader_preserves_verification_and_forwards_no_credentials(self):
        raw = self.archive.read_bytes()
        requests = []
        class Response(BytesIO):
            status = 200
            headers = {"Content-Length": str(len(raw)), "Content-Encoding": "identity"}
            def geturl(response):
                return self.vendor_url
        def opened(request, timeout):
            requests.append(request)
            return Response(raw)
        destination = self.root / "mock-https.zip"
        with patch.object(publication, "build_opener", return_value=SimpleNamespace(open=opened)), \
                patch.dict("os.environ", {"GITHUB_TOKEN": "QA_TOKEN_MUST_NOT_BE_FORWARDED"}):
            result = publication._download_delivery_archive(self.vendor_url, destination,
                len(raw), publication.digest(self.archive))
        self.assertEqual(result.read_bytes(), raw)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("authorization", {key.lower() for key, value in requests[0].header_items()})
        self.assertNotIn("QA_TOKEN_MUST_NOT_BE_FORWARDED", str(requests[0].header_items()))

    def test_default_downloader_bounds_bytes_and_redacts_sas_on_failures(self):
        raw = self.archive.read_bytes()
        class Response(BytesIO):
            status = 200
            headers = {"Content-Encoding": "identity"}
            def geturl(response):
                return self.vendor_url
        cases = ((raw[:-1], publication.digest(self.archive)),
            (raw + b"excess", publication.digest(self.archive)), (raw, "0" * 64))
        for number, (received, digest) in enumerate(cases):
            destination = self.root / ("bad-download-" + str(number) + ".zip")
            with patch.object(publication, "build_opener", return_value=SimpleNamespace(
                    open=lambda *args, **kwargs: Response(received))), self.assertRaises(REJECTIONS) as error:
                publication._download_delivery_archive(self.vendor_url, destination, len(raw), digest)
            self.assertFalse(destination.exists())
            self.assertNotIn("QA_ONLY_SECRET", str(error.exception))
        destination = self.root / "network-failure.zip"
        with patch.object(publication, "build_opener", return_value=SimpleNamespace(
                open=lambda *args, **kwargs: (_ for _ in ()).throw(OSError(self.vendor_url)))):
            try:
                publication._download_delivery_archive(self.vendor_url, destination,
                    len(raw), publication.digest(self.archive))
            except ValueError as error:
                self.assertNotIn("QA_ONLY_SECRET", "".join(traceback.format_exception(error)))
            else:
                self.fail("A failed HTTPS request must stop delivery")
        self.assertFalse(destination.exists())

    def test_redirects_revalidate_destination_and_stop_after_three_hops(self):
        redirect = publication._DeliveryRedirects()
        request = Request(self.vendor_url)
        for _ in range(3):
            request = redirect.redirect_request(request, None, 302, "", {}, self.vendor_url)
        with self.assertRaises(REJECTIONS):
            redirect.redirect_request(request, None, 302, "", {}, self.vendor_url)
        with self.assertRaises(REJECTIONS) as error:
            publication._DeliveryRedirects().redirect_request(Request(self.vendor_url), None, 302, "", {},
                "https://127.0.0.1/untrusted?sig=DO_NOT_LOG")
        self.assertNotIn("DO_NOT_LOG", str(error.exception))


if __name__ == "__main__":
    unittest.main()
