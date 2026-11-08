"""Publication-control boundaries only; no network, SSH, Qt or native binaries."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "packaging"), str(ROOT / "tests")]
import resend_desktop_update as resend
import desktop_update_packaging_smoke_test as fixtures


class ResendControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-resend-control-qa-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.expected = resend.Expected(42433269255, 1, 13599005609, "e" * 64, "c" * 40, "d" * 64, 2026110701, "1.0.1")

    def api_fixture(self):
        identity = {"id": resend.REPOSITORY_ID, "full_name": resend.REPOSITORY}
        run = {"id": self.expected.run_id, "run_attempt": 1, "run_number": 3, "status": "completed",
            "conclusion": "failure", "event": "workflow_dispatch", "path": resend.WORKFLOW,
            "head_branch": "main", "head_sha": self.expected.source_commit,
            "repository": identity.copy(), "head_repository": identity.copy()}
        def job(name, names, conclusion="success"):
            return {"name": name, "status": "completed", "conclusion": conclusion,
                "run_id": self.expected.run_id, "head_sha": self.expected.source_commit,
                "steps": [{"name": name, "status": "completed", "conclusion": "success"} for name in names]}
        jobs = [job(target + " patch and update bootstrap", resend.COMMON_STEPS + resend.TARGET_STEPS[target])
                for target in sorted(resend.TARGETS)]
        publication = job(resend.PUBLISH_JOB, resend.SIGN_STEPS + (resend.TRANSFER,), "failure")
        publication["steps"][-1]["conclusion"] = "failure"
        jobs.append(publication)
        artifact = {"id": self.expected.artifact_id, "name": "LJQCUpdate-signed-3-1", "expired": False,
            "size_in_bytes": 1000, "digest": "sha256:" + self.expected.artifact_sha256,
            "workflow_run": {"id": self.expected.run_id, "head_sha": self.expected.source_commit,
                "head_branch": "main", "repository_id": resend.REPOSITORY_ID,
                "head_repository_id": resend.REPOSITORY_ID}}
        return run, {"total_count": 3, "jobs": jobs}, {"total_count": 1, "artifacts": [artifact]}

    def signed_fixture(self):
        fixture = fixtures.PatchPackagingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bundle = fixture.signed_pair()
        archive = self.root / "accepted.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as output:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(bundle).as_posix())
        return fixture, bundle, archive

    def blob_url(self):
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        return ("https://productionresultssa6.blob.core.windows.net/actions-results/"
            "72f93b92-71b0-4539-81b9-ff8b465fc8e3/workflow-job-run-2ab8a248-5781-5443-92d1-06230f47d371/"
            "artifacts/" + "a" * 64 + ".zip?" + urlencode({"sp": "r", "sr": "b", "se": expiry, "sig": "inert-QA-signature"}))

    def test_realistic_main_native_success_and_only_transfer_failure_are_accepted(self):
        run, jobs, artifacts = self.api_fixture()
        self.assertEqual(resend.validate_source(run, jobs, artifacts, self.expected)["id"], self.expected.artifact_id)
        run["event"] = "push"
        self.assertEqual(resend.validate_source(run, jobs, artifacts, self.expected)["id"], self.expected.artifact_id)

    def test_other_repo_branch_pull_request_attempt_and_incomplete_run_are_refused(self):
        mutations = (("repository", {"id": 123, "full_name": "elsewhere/project"}), ("head_repository", {}),
            ("head_branch", "feature"), ("event", "pull_request"), ("path", ".github/workflows/other.yml"),
            ("run_attempt", 2), ("head_sha", "0" * 40), ("status", "in_progress"), ("conclusion", "cancelled"))
        for name, value in mutations:
            with self.subTest(field=name):
                run, jobs, artifacts = self.api_fixture(); run[name] = value
                with self.assertRaises(ValueError):
                    resend.validate_source(run, jobs, artifacts, self.expected)

    def test_skipped_native_adoption_or_signing_and_additional_failure_are_refused(self):
        for mutation in ("native-failed", "adoption-skipped", "sign-skipped", "other-failure", "transfer-cancelled", "wrong-job-source", "missing-job", "partial-page"):
            with self.subTest(mutation=mutation):
                run, jobs, artifacts = self.api_fixture()
                if mutation == "native-failed": jobs["jobs"][0]["conclusion"] = "failure"
                elif mutation == "adoption-skipped":
                    next(s for s in jobs["jobs"][0]["steps"] if s["name"] == resend.COMMON_STEPS[3])["conclusion"] = "skipped"
                elif mutation == "sign-skipped": jobs["jobs"][-1]["steps"][3]["conclusion"] = "skipped"
                elif mutation == "other-failure": jobs["jobs"][-1]["steps"][0]["conclusion"] = "failure"
                elif mutation == "transfer-cancelled": jobs["jobs"][-1]["steps"][-1]["conclusion"] = "cancelled"
                elif mutation == "wrong-job-source": jobs["jobs"][0]["head_sha"] = "0" * 40
                elif mutation == "missing-job": jobs["jobs"].pop()
                else: jobs["total_count"] = 4
                with self.assertRaises(ValueError):
                    resend.validate_source(run, jobs, artifacts, self.expected)

    def test_wrong_duplicate_expired_or_unbound_artifact_is_refused(self):
        for mutation in ("id", "digest", "name", "expired", "run", "head", "fork", "duplicate", "incomplete"):
            with self.subTest(mutation=mutation):
                run, jobs, artifacts = self.api_fixture(); artifact = artifacts["artifacts"][0]
                if mutation == "id": artifact["id"] += 1
                elif mutation == "digest": artifact["digest"] = "sha256:" + "0" * 64
                elif mutation == "name": artifact["name"] = "LJQCUpdate-signed-3-2"
                elif mutation == "expired": artifact["expired"] = True
                elif mutation == "run": artifact["workflow_run"]["id"] += 1
                elif mutation == "head": artifact["workflow_run"]["head_sha"] = "0" * 40
                elif mutation == "fork": artifact["workflow_run"]["head_repository_id"] = 123
                elif mutation == "duplicate": artifacts["artifacts"].append(copy.deepcopy(artifact)); artifacts["total_count"] = 2
                else: artifacts["total_count"] = 2
                with self.assertRaises(ValueError):
                    resend.validate_source(run, jobs, artifacts, self.expected)

    def test_signed_bundle_exact_bytes_and_original_source_are_retained(self):
        fixture, bundle, archive = self.signed_fixture()
        destination = self.root / "extracted"
        index = resend.extract_signed_zip(archive, destination, fixture.trust_file, self.expected)
        self.assertEqual(index["source_commit"], self.expected.source_commit)
        for path in bundle.rglob("*"):
            if path.is_file():self.assertEqual(path.read_bytes(), (destination / path.relative_to(bundle)).read_bytes())
        wrong = resend.Expected(**{**self.expected.__dict__, "source_digest": "0" * 64})
        with self.assertRaises(ValueError):
            resend.extract_signed_zip(archive, self.root / "wrong-source", fixture.trust_file, wrong)

    def test_zip_traversal_links_duplicates_encryption_and_limits_fail_before_extraction(self):
        for number, (name, mode, flags, size) in enumerate((
            ("../outside", stat.S_IFREG, 0, 1), ("/absolute", stat.S_IFREG, 0, 1),
            ("windows-x86_64\\evil", stat.S_IFREG, 0, 1), ("C:/evil", stat.S_IFREG, 0, 1),
            ("windows-x86_64/evil.exe", stat.S_IFLNK, 0, 1),
            ("windows-x86_64/evil.exe", stat.S_IFREG | 0o4000, 0, 1),
            ("windows-x86_64/a.exe\x00hidden", stat.S_IFREG, 0, 1),
            ("windows-x86_64/evil.exe", stat.S_IFREG, 1, 1),
            ("windows-x86_64/evil.exe", stat.S_IFREG, 0, 4 * 1024**3 + 1),
            ("release-index.json", stat.S_IFREG, 0, 262145))):
            with self.subTest(name=name, mode=mode, flags=flags, size=size):
                member = zipfile.ZipInfo(name); member.external_attr = mode << 16
                member.flag_bits = flags; member.file_size = size; member.compress_size = 1
                fake = Mock(); fake.infolist.return_value = [member]
                manager = Mock(); manager.__enter__ = Mock(return_value=fake); manager.__exit__ = Mock(return_value=False)
                trust = {"keys": {}}
                with patch.object(resend.zipfile, "ZipFile", return_value=manager), patch.object(resend, "read_object", return_value=trust), \
                        patch.object(resend, "_bounded_zip_directory"):
                    with self.assertRaises(ValueError):
                        resend.extract_signed_zip(self.root / "never-opened", self.root / str(number), self.root / "trust", self.expected)
                fake.read.assert_not_called()
        fixture, bundle, archive = self.signed_fixture()
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("WINDOWS-X86_64/LATEST.JSON", b"duplicate case alias")
        with self.assertRaises(ValueError):
            resend.extract_signed_zip(archive, self.root / "duplicate", fixture.trust_file, self.expected)

    def test_reference_has_only_four_files_and_preserves_original_metadata(self):
        fixture, bundle, _ = self.signed_fixture()
        index = resend.verify_bundle(bundle, fixture.trust_file)
        with patch.object(resend, "artifact_location", return_value=self.blob_url()):
            directory = resend.prepare_reference(bundle, index, self.root, self.expected, {"size_in_bytes": 1000})
        files = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        self.assertEqual(files, {"delivery-reference.json", "release-index.json", "windows-x86_64/latest.json", "macos-arm64/latest.json"})
        for name in files - {"delivery-reference.json"}:
            self.assertEqual((directory / name).read_bytes(), (bundle / name).read_bytes())
        reference = json.loads((directory / "delivery-reference.json").read_text())
        self.assertEqual(reference["index_sha256"], resend.digest(bundle / "release-index.json"))
        self.assertEqual(reference["source"]["run_id"], self.expected.run_id)

    def test_github_token_is_only_used_at_api_and_never_follows_blob_redirect(self):
        location = self.blob_url()
        failure = HTTPError("https://api.github.com/fixed", 302, "Found", {"Location": location}, BytesIO())
        opener = Mock(); opener.open.side_effect = failure
        with patch.dict(os.environ, GH_TOKEN="inert-QA-token"), patch.object(resend, "build_opener", return_value=opener):
            self.assertEqual(resend.artifact_location(self.expected.artifact_id), location)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://api.github.com/repos/" + resend.REPOSITORY + "/"))
        self.assertIsNone(resend._NoApiRedirect().redirect_request(None, None, 302, "", {}, location))

    def test_unsupported_blob_shard_uses_one_original_raw_ssh_attempt(self):
        fixture, bundle, _ = self.signed_fixture(); index = resend.verify_bundle(bundle, fixture.trust_file)
        receipt = {"status": "published", "release_id": index["release_id"], "sequence": index["sequence"],
                   "base_installer_changed": False, "business_data_access": False}
        with patch.dict(os.environ, DEPLOY_HOST="159.75.243.120", DEPLOY_USER="ljqc-update", DEPLOY_KEY="inert", KNOWN_HOSTS="inert"), \
                patch.object(resend, "artifact_location", side_effect=resend.ReferenceUnavailable("unsupported")), \
                patch.object(resend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(receipt).encode(), b"")) as ssh:
            result = resend.transfer(bundle, index, self.root, fixture.trust_file, expected=self.expected, artifact={"size_in_bytes": 1000})
        self.assertEqual(result["transport"], "raw-fallback"); self.assertEqual(ssh.call_count, 1)
        self.assertEqual(ssh.call_args.kwargs["timeout"], 7200)
        self.assertEqual(ssh.call_args.args[0][-1], "ljqc-update-upload")
        with tarfile.open(self.root / "publication.tar") as archive:
            self.assertNotIn("delivery-reference.json", archive.getnames())

    def test_local_entry_and_gh_debug_or_deploy_secret_propagation_are_refused(self):
        with patch.dict(os.environ, GITHUB_ACTIONS="false"):
            with self.assertRaises(ValueError):resend.require_main_dispatch()
        with patch.dict(os.environ, GH_DEBUG="api", GH_HOST="untrusted.invalid", DEPLOY_KEY="inert", KNOWN_HOSTS="inert", SIGNING_KEY="inert"):
            environment = resend.gh_environment()
        for name in ("GH_DEBUG", "GH_HOST", "DEPLOY_KEY", "KNOWN_HOSTS", "SIGNING_KEY"):
            self.assertNotIn(name, environment)

    def test_zip_directory_allocation_is_bounded_before_zipfile_is_opened(self):
        archive = self.root / "oversized-directory.zip"
        archive.write_bytes(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 7, 7, 2**30, 0, 0))
        with patch.object(resend, "read_object", return_value={}), patch.object(resend.zipfile, "ZipFile") as parser:
            with self.assertRaises(ValueError):
                resend.extract_signed_zip(archive, self.root / "bad-directory", self.root / "trust", self.expected)
            parser.assert_not_called()

    def test_signed_member_size_mismatch_is_rejected_before_any_extraction(self):
        fixture, bundle, archive = self.signed_fixture()
        changed = self.root / "changed-size.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(changed, "x") as output:
            for member in source.infolist():
                raw = source.read(member)
                if member.filename.endswith(".tar.gz"):raw += b"unsigned extra byte"
                output.writestr(member.filename, raw)
        destination = self.root / "must-not-be-created"
        with self.assertRaisesRegex(ValueError, "before extraction"):
            resend.extract_signed_zip(changed, destination, fixture.trust_file, self.expected)
        self.assertFalse(destination.exists())

    def test_large_reference_falls_back_before_requesting_a_short_url(self):
        with patch.object(resend, "artifact_location") as location:
            with self.assertRaises(resend.ReferenceUnavailable):
                resend.prepare_reference(self.root, {}, self.root, self.expected, {"size_in_bytes": 4 * 1024**3 + 1})
            location.assert_not_called()

    def test_explicit_raw_selection_never_requests_a_sas_or_retries_ssh(self):
        fixture, bundle, _ = self.signed_fixture(); index = resend.verify_bundle(bundle, fixture.trust_file)
        receipt = {"status": "published", "release_id": index["release_id"], "sequence": index["sequence"],
                   "base_installer_changed": False, "business_data_access": False}
        with patch.dict(os.environ, DEPLOY_HOST="159.75.243.120", DEPLOY_USER="ljqc-update", DEPLOY_KEY="inert", KNOWN_HOSTS="inert"), \
                patch.object(resend, "artifact_location") as location, \
                patch.object(resend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(receipt).encode(), b"")) as ssh:
            result = resend.transfer(bundle, index, self.root, fixture.trust_file, expected=self.expected,
                                     artifact={"size_in_bytes": 1000}, force_raw=True)
        location.assert_not_called(); self.assertEqual(ssh.call_count, 1)
        self.assertEqual(result["transport"], "raw-selected")

    def test_unknown_server_receipt_fields_are_not_returned_or_printed(self):
        fixture, bundle, _ = self.signed_fixture(); index = resend.verify_bundle(bundle, fixture.trust_file)
        receipt = {"status": "published", "release_id": index["release_id"], "sequence": index["sequence"],
                   "base_installer_changed": False, "business_data_access": False, "url": "must-not-be-exported"}
        with patch.dict(os.environ, DEPLOY_HOST="159.75.243.120", DEPLOY_USER="ljqc-update", DEPLOY_KEY="inert", KNOWN_HOSTS="inert"), \
                patch.object(resend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(receipt).encode(), b"")):
            with self.assertRaisesRegex(ValueError, "exact selected"):
                resend.transfer(bundle, index, self.root, fixture.trust_file, force_raw=True)

    def test_metadata_only_refresh_keeps_three_original_raw_files_without_native_artifact_lookup(self):
        fixture, bundle, _ = self.signed_fixture()
        def fetch(url):
            if url.endswith("release-index.json"):
                return (bundle / "release-index.json").read_bytes()
            target = next(target for target in resend.TARGETS if "/" + target + "/" in url)
            return (bundle / target / "latest.json").read_bytes()
        refreshed = self.root / "refreshed"
        fixtures.publication.refresh_metadata(refreshed, fixture.trust_file, key_path=fixture.key_file, fetch=fetch)
        self.assertEqual(len([path for path in refreshed.rglob("*") if path.is_file()]), 3)
        with patch.dict(os.environ, GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main", GITHUB_REPOSITORY=resend.REPOSITORY,
                        GITHUB_REPOSITORY_ID=str(resend.REPOSITORY_ID), RUNNER_TEMP=str(self.root), GITHUB_SHA="0" * 40), \
                patch.object(resend, "api_json") as api, patch.object(resend, "transfer", return_value={"status": "published"}) as transfer:
            resend.publish_current(refreshed, fixture.trust_file, artifact_id=1, artifact_sha256="unused")
        api.assert_not_called()
        self.assertIsNone(transfer.call_args.kwargs["expected"])
        self.assertIsNone(transfer.call_args.kwargs["artifact"])

    def test_normal_publication_selects_raw_after_the_same_artifact_source_verification(self):
        fixture, bundle, _ = self.signed_fixture()
        run, _, artifacts = self.api_fixture()
        artifact = artifacts["artifacts"][0]
        with patch.dict(os.environ, GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main", GITHUB_REPOSITORY=resend.REPOSITORY,
                        GITHUB_REPOSITORY_ID=str(resend.REPOSITORY_ID), RUNNER_TEMP=str(self.root),
                        GITHUB_SHA=self.expected.source_commit, GITHUB_RUN_ID=str(self.expected.run_id), GITHUB_RUN_ATTEMPT="1"), \
                patch.object(resend, "api_json", side_effect=[run, artifact]) as api, \
                patch.object(resend, "transfer", return_value={"status": "published"}) as transfer:
            resend.publish_current(bundle, fixture.trust_file, artifact_id=self.expected.artifact_id,
                                   artifact_sha256=self.expected.artifact_sha256)
        self.assertEqual(api.call_count, 2)
        self.assertTrue(transfer.call_args.kwargs["force_raw"])
        self.assertEqual(transfer.call_args.kwargs["expected"], self.expected)
        self.assertEqual(transfer.call_args.kwargs["artifact"], artifact)

    def test_explicit_false_still_sends_one_reference_without_changing_signed_bytes(self):
        fixture, bundle, _ = self.signed_fixture(); index = resend.verify_bundle(bundle, fixture.trust_file)
        original = (bundle / "release-index.json").read_bytes()
        receipt = {"status": "published", "release_id": index["release_id"], "sequence": index["sequence"],
                   "base_installer_changed": False, "business_data_access": False}
        with patch.dict(os.environ, DEPLOY_HOST="159.75.243.120", DEPLOY_USER="ljqc-update", DEPLOY_KEY="inert", KNOWN_HOSTS="inert"), \
                patch.object(resend, "artifact_location", return_value=self.blob_url()) as location, \
                patch.object(resend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(receipt).encode(), b"")) as ssh:
            result = resend.transfer(bundle, index, self.root, fixture.trust_file, expected=self.expected,
                                     artifact={"size_in_bytes": 1000}, force_raw=False)
        self.assertEqual(result["transport"], "github-artifact-reference")
        location.assert_called_once(); self.assertEqual(ssh.call_count, 1)
        self.assertEqual(original, (bundle / "release-index.json").read_bytes())
        with tarfile.open(self.root / "publication.tar") as archive:
            self.assertEqual(set(archive.getnames()), {"delivery-reference.json", "release-index.json",
                                                       "windows-x86_64/latest.json", "macos-arm64/latest.json"})

    def test_workflow_is_manual_main_only_and_uses_two_existing_ssh_secrets(self):
        recipe = (ROOT / ".github/workflows/desktop-update-resend.yml").read_text()
        self.assertIn("github.ref == 'refs/heads/main'", recipe)
        self.assertIn("inputs.publish == true", recipe)
        self.assertIn("group: desktop-update-publication", recipe)
        self.assertIn("timeout-minutes: 150", recipe)
        self.assertNotIn("SIGNING_KEY", recipe)
        self.assertNotIn("build_update_bootstrap", recipe)
        normal = (ROOT / ".github/workflows/desktop-update-publish.yml").read_text()
        self.assertIn("from resend_desktop_update import publish_current", normal)
        for workflow in ("desktop-build.yml", "desktop-updates.yml", "desktop-update-refresh.yml", "desktop-update-publish.yml"):
            self.assertIn("actions: read", (ROOT / ".github/workflows" / workflow).read_text())


if __name__ == "__main__":
    unittest.main()
