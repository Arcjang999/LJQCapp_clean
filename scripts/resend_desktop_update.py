"""Resend one already signed, natively accepted release; never build or sign.

Only the protected main workflow may call this entry. All GitHub downloads use
the gh client's authenticated redirect handling; tokens and temporary redirect
URLs are neither read nor printed. Original signed metadata bytes are retained.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from desktop_updates.protocol import TARGETS, strict_json
from publish_desktop_update import (_bounded_zip_directory, digest, read_object,
                                   validate_delivery_url, verify_bundle, verify_index)

REPOSITORY = "Arcjang999/LJQCapp_clean"
REPOSITORY_ID = 1193577748
WORKFLOW = ".github/workflows/desktop-updates.yml"
TRANSFER = "Transfer only through the restricted receiving command"
PUBLISH_JOB = "Sign and optionally publish the verified pair / sign_and_transfer"
MAX_ZIP_BYTES = 4 * 1024**3
MAX_SIGNED_BYTES = 12 * 1024**3
MAX_MEMBERS = 32
MAX_METADATA_BYTES = 262144
UPLOAD_TIMEOUT = 18000
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
COMMON_STEPS = ("Run guarded source checks", "Run updater failure and recovery checks",
    "Export and verify actual cumulative patch",
    "Verify installed helper HTTPS and real 1.00 cumulative update adoption",
    "Retain verified candidates (not a server publication)")
TARGET_STEPS = {
    "windows-x86_64": ("Prepare bootstrap installer compiler", "Build native Windows program without base Setup",
                      "Build Windows bootstrap and verify frozen production HTTPS"),
    "macos-arm64": ("Build native Mac program without base DMG", "Build Mac bootstrap and verify frozen production HTTPS")}
SIGN_STEPS = ("Identify available publication capabilities", "Install signing and explicit HTTPS trust dependencies",
    "Get both native candidates from this successful run", "Sign exactly the verified platform pair",
    "Retain signed publication files")


@dataclass(frozen=True)
class Expected:
    run_id: int
    attempt: int
    artifact_id: int
    artifact_sha256: str
    source_commit: str
    source_digest: str
    build_number: int
    version: str

    def validate(self):
        if (any(type(value) is not int or not 0 < value < 2**63 for value in
                (self.run_id, self.attempt, self.artifact_id, self.build_number))
                or not HEX64.fullmatch(self.artifact_sha256) or not HEX40.fullmatch(self.source_commit)
                or not HEX64.fullmatch(self.source_digest)
                or re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", self.version) is None):
            raise ValueError("The explicitly selected release identity is invalid")


def require_main_dispatch() -> Path:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REPOSITORY_ID") != str(REPOSITORY_ID)
            or Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve() != ROOT or sys.platform != "linux"):
        raise ValueError("Resending requires the explicit protected main GitHub dispatch")
    temporary = Path(os.environ.get("RUNNER_TEMP", ""))
    if (not temporary.is_absolute() or not temporary.is_dir() or temporary.is_symlink()
            or temporary.resolve().is_relative_to(ROOT)):
        raise ValueError("The isolated GitHub temporary directory is invalid")
    return temporary.resolve()


def gh_environment() -> dict:
    environment = dict(os.environ)
    # Debug HTTP output can contain temporary redirect URLs. Do not enable it.
    for name in ("GH_DEBUG", "GH_TRACE", "GH_HOST", "DEBUG"):
        environment.pop(name, None)
    for name in ("DEPLOY_KEY", "KNOWN_HOSTS", "SIGNING_KEY", "LJQC_UPDATE_SIGNING_KEY"):
        environment.pop(name, None)
    environment["GH_PROMPT_DISABLED"] = "true"
    return environment


def api_json(endpoint: str) -> dict:
    result = subprocess.run(["gh", "api", "--hostname", "github.com", endpoint], capture_output=True,
                            env=gh_environment(), timeout=60, check=False)
    if result.returncode or len(result.stdout) > 2 * 1024**2:
        raise ValueError("GitHub source metadata could not be read within limits")
    return strict_json(result.stdout, maximum=2 * 1024**2)


def _required_steps(job: dict, required: tuple[str, ...]) -> None:
    steps = job.get("steps")
    if not isinstance(steps, list) or len(steps) > 100:
        raise ValueError("Required native acceptance steps are missing")
    for name in required:
        matches = [step for step in steps if step.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise ValueError("A required native/signing acceptance step did not succeed")


def validate_source(run: dict, jobs_page: dict, artifacts_page: dict, expected: Expected) -> dict:
    expected.validate()
    if (run.get("id") != expected.run_id or run.get("run_attempt") != expected.attempt
            or run.get("status") != "completed" or run.get("conclusion") not in ("success", "failure")
            or run.get("path") != WORKFLOW or run.get("head_branch") != "main"
            or run.get("event") not in ("push", "workflow_dispatch")
            or run.get("head_sha") != expected.source_commit
            or any(run.get(key, {}).get("full_name") != REPOSITORY
                   or run.get(key, {}).get("id") != REPOSITORY_ID for key in ("repository", "head_repository"))):
        raise ValueError("The selected original run is not an accepted main source")
    jobs = jobs_page.get("jobs", [])
    if jobs_page.get("total_count") != len(jobs) or len(jobs) != 3:
        raise ValueError("The selected run must contain exactly the two native jobs and publication")
    required_names = {target + " patch and update bootstrap" for target in TARGETS} | {PUBLISH_JOB}
    if {job.get("name") for job in jobs} != required_names:
        raise ValueError("The selected run has unexpected native/publication jobs")
    for job in jobs:
        if (job.get("run_id") != expected.run_id or job.get("head_sha") != expected.source_commit
                or job.get("status") != "completed"):
            raise ValueError("A job belongs to another source or remains incomplete")
    for target in TARGETS:
        job = next(job for job in jobs if job["name"] == target + " patch and update bootstrap")
        if job.get("conclusion") != "success":
            raise ValueError("Both original native jobs must have succeeded")
        _required_steps(job, COMMON_STEPS + TARGET_STEPS[target])
    publication = next(job for job in jobs if job["name"] == PUBLISH_JOB)
    _required_steps(publication, SIGN_STEPS)
    transfers = [step for step in publication["steps"] if step.get("name") == TRANSFER]
    if (len(transfers) != 1 or transfers[0].get("status") != "completed"
            or transfers[0].get("conclusion") not in ("success", "failure")):
        raise ValueError("The original restricted transfer was not completed")
    failures = [(job["name"], step.get("name")) for job in jobs for step in job["steps"]
                if step.get("conclusion") in ("failure", "cancelled", "timed_out")]
    expected_failures = [] if run["conclusion"] == "success" else [(PUBLISH_JOB, TRANSFER)]
    if (failures != expected_failures or publication.get("conclusion") != run["conclusion"]
            or transfers[0]["conclusion"] != run["conclusion"]):
        raise ValueError("Only a restricted transfer failure may be reused")
    artifacts = artifacts_page.get("artifacts", [])
    if artifacts_page.get("total_count") != len(artifacts) or len(artifacts) > 100:
        raise ValueError("Incomplete artifact metadata cannot authorize resending")
    name = f"LJQCUpdate-signed-{run['run_number']}-{expected.attempt}"
    matches = [artifact for artifact in artifacts if artifact.get("name") == name]
    if len(matches) != 1:
        raise ValueError("Exactly one signed artifact for the selected attempt is required")
    artifact = validate_artifact(matches[0], expected, run["run_number"])
    if artifact["size_in_bytes"] > MAX_ZIP_BYTES:
        raise ValueError("Manual ZIP reuse exceeds the reviewed non-ZIP64 artifact boundary")
    return artifact


def validate_artifact(artifact: dict, expected: Expected, run_number: int) -> dict:
    expected.validate()
    origin = artifact.get("workflow_run", {})
    if (type(run_number) is not int or run_number < 1
            or artifact.get("name") != f"LJQCUpdate-signed-{run_number}-{expected.attempt}"
            or artifact.get("id") != expected.artifact_id or artifact.get("expired") is not False
            or artifact.get("digest") != "sha256:" + expected.artifact_sha256
            or type(artifact.get("size_in_bytes")) is not int or not 0 < artifact["size_in_bytes"] <= MAX_SIGNED_BYTES
            or origin.get("id") != expected.run_id or origin.get("head_sha") != expected.source_commit
            or origin.get("head_branch") != "main" or origin.get("repository_id") != REPOSITORY_ID
            or origin.get("head_repository_id") != REPOSITORY_ID):
        raise ValueError("The signed artifact identity does not match the accepted original run")
    return artifact


class ReferenceUnavailable(ValueError):
    """Choose the existing raw transport before any SSH attempt is made."""


class _NoApiRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The GitHub token must never follow the artifact-storage redirect.
        return None


def artifact_location(artifact_id: int) -> str:
    from desktop_updates.transport import _https_context
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ReferenceUnavailable("Artifact delivery reference is unavailable")
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
    request = Request(url, headers={"Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LJQCApp accepted release delivery"})
    opener = build_opener(_NoApiRedirect(), HTTPSHandler(context=_https_context()))
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        try:
            if error.code != 302:
                raise ReferenceUnavailable("Artifact delivery reference is unavailable") from None
            location = error.headers.get("Location")
        finally:
            error.close()
    except (OSError, URLError, ValueError):
        raise ReferenceUnavailable("Artifact delivery reference is unavailable") from None
    else:
        response.close()
        raise ReferenceUnavailable("Artifact API did not return a restricted redirect")
    try:
        return validate_delivery_url(location)
    except (ValueError, TypeError):
        # A future unapproved vendor shard uses the original long raw transfer.
        # Neither the URL nor its SAS signature is copied to a log or receipt.
        raise ReferenceUnavailable("Artifact storage is outside the approved reference boundary") from None


def prepare_reference(bundle: Path, index: dict, work: Path, expected: Expected, artifact: dict) -> Path:
    # Obtain this short-lived read-only blob URL after local signature/SHA
    # validation, immediately before sending the tiny private metadata tar.
    if artifact["size_in_bytes"] > 4 * 1024**3:
        raise ReferenceUnavailable("The accepted archive needs the existing raw transport")
    location = artifact_location(expected.artifact_id)
    reference = {"format": "ljqc-desktop-delivery-reference", "format_version": 1,
        "artifact": {"url": location, "size_bytes": artifact["size_in_bytes"],
                     "sha256": expected.artifact_sha256, "kind": "github-artifact-zip"},
        "source": {"repository": REPOSITORY, "run_id": expected.run_id,
                   "run_attempt": expected.attempt, "artifact_id": expected.artifact_id},
        "index_sha256": digest(bundle / "release-index.json")}
    directory = work / "delivery-reference"
    directory.mkdir(mode=0o700, exist_ok=False)
    path = directory / "delivery-reference.json"
    with path.open("x", encoding="utf-8") as handle:
        path.chmod(0o600)
        json.dump(reference, handle, separators=(",", ":")); handle.write("\n")
    for name in ("release-index.json", *(target + "/latest.json" for target in sorted(TARGETS))):
        destination = directory / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            destination.chmod(0o600)
            handle.write((bundle / name).read_bytes())
    return directory


def download_zip(artifact: dict, destination: Path, expected: Expected) -> None:
    """Read authenticated redirected bytes with hard length and time bounds."""
    process = subprocess.Popen(["gh", "api", "--hostname", "github.com", f"repos/{REPOSITORY}/actions/artifacts/{expected.artifact_id}/zip"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=gh_environment())
    checksum, count, deadline = hashlib.sha256(), 0, time.monotonic() + 900
    try:
        with selectors.DefaultSelector() as selector, destination.open("xb") as output:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Signed artifact download timed out")
                for key, _ in selector.select(min(1, remaining)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > artifact["size_in_bytes"] or count > MAX_ZIP_BYTES:
                        raise ValueError("Signed artifact exceeds its authenticated download length")
                    checksum.update(chunk)
                    output.write(chunk)
            if process.wait(timeout=30) or count != artifact["size_in_bytes"] or checksum.hexdigest() != expected.artifact_sha256:
                raise ValueError("The downloaded signed ZIP failed its GitHub size/hash identity")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def validate_index_identity(index: dict, expected: Expected) -> None:
    values = {"source_commit": expected.source_commit, "source_digest": expected.source_digest,
              "build_number": expected.build_number, "version": expected.version,
              "release_id": f"{expected.version}-{expected.build_number}-{expected.source_digest[:12]}"}
    if any(index.get(key) != value for key, value in values.items()):
        raise ValueError("Signed release provenance differs from the selected accepted native source")


def extract_signed_zip(archive: Path, destination: Path, trust_path: Path, expected: Expected) -> dict:
    """Exact signed file set; no directory, link, duplicate or unsafe ZIP names."""
    trust = read_object(trust_path)
    _bounded_zip_directory(archive)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not 0 < len(members) <= MAX_MEMBERS:
            raise ValueError("The signed ZIP has an invalid member count")
        names, folded, total = set(), set(), 0
        for member in members:
            name, path = member.filename, PurePosixPath(member.filename)
            unix_mode = member.external_attr >> 16
            mode = stat.S_IFMT(unix_mode)
            if (len(name) > 256 or path.is_absolute() or "\\" in name or ":" in name
                    or getattr(member, "orig_filename", name) != name or unix_mode & 0o7000
                    or any(ord(char) < 33 or ord(char) > 126 for char in name)
                    or any(part in (".", "..") for part in name.split("/")) or path.as_posix() != name
                    or member.is_dir() or member.external_attr & 0x10 or mode not in (0, stat.S_IFREG)
                    or member.flag_bits & 1 or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or name.casefold() in folded or member.file_size < 1 or member.file_size > 4 * 1024**3
                    or member.compress_size > MAX_ZIP_BYTES):
                raise ValueError("The signed ZIP contains an unsafe or duplicate file")
            if name != "release-index.json" and (len(path.parts) != 2 or path.parts[0] not in TARGETS
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,200}", path.name) is None):
                raise ValueError("The signed ZIP contains an unexpected publication path")
            if path.suffix == ".json" and member.file_size > MAX_METADATA_BYTES:
                raise ValueError("Signed metadata exceeds the update protocol limit")
            names.add(name); folded.add(name.casefold()); total += member.file_size
        if total > MAX_ZIP_BYTES or "release-index.json" not in names:
            raise ValueError("The signed ZIP exceeds its total size or lacks the release index")
        if shutil.disk_usage(destination.parent).free < total * 2 + 1024**3:
            raise ValueError("Insufficient isolated disk space for exact signed extraction and transfer")
        index = verify_index(strict_json(source.read("release-index.json")), trust)
        validate_index_identity(index, expected)
        records = index.get("files")
        if not isinstance(records, list) or len(records) != 6 or names != {"release-index.json"} | {record["path"] for record in records}:
            raise ValueError("The ZIP file set differs from the authenticated release index")
        signed_sizes = {record["path"]: record["size_bytes"] for record in records}
        for member in members:
            if member.filename != "release-index.json" and member.file_size != signed_sizes[member.filename]:
                raise ValueError("ZIP member length differs from its signed record before extraction")
        destination.mkdir(mode=0o700, exist_ok=False)
        for member in members:
            output = destination / member.filename
            output.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with source.open(member) as input_file, output.open("xb") as handle:
                for chunk in iter(lambda: input_file.read(1024**2), b""):
                    count += len(chunk)
                    if count > member.file_size:
                        raise ValueError("Extracted signed file exceeds the ZIP identity")
                    handle.write(chunk)
            if count != member.file_size:
                raise ValueError("An extracted signed file is incomplete")
    verified = verify_bundle(destination, trust_path)
    validate_index_identity(verified, expected)
    return verified


def transfer(bundle: Path, index: dict, work: Path, trust_path: Path, *,
             expected: Expected | None = None, artifact: dict | None = None,
             force_raw: bool = False) -> dict:
    trust = read_object(trust_path)
    host, user = os.environ.get("DEPLOY_HOST"), os.environ.get("DEPLOY_USER")
    if host != urlsplit(trust["origin"]).hostname or host != "159.75.243.120" or user != "ljqc-update":
        raise ValueError("The restricted receiver host or dedicated identity differs from production trust")
    if not os.environ.get("DEPLOY_KEY") or not os.environ.get("KNOWN_HOSTS"):
        raise ValueError("The existing restricted transfer credentials are not configured")
    # Recheck all signed bytes immediately before constructing the transport tar.
    if verify_bundle(bundle, trust_path) != index:
        raise ValueError("Signed publication contents changed before transfer")
    transport, contents = ("raw-selected" if force_raw else "raw"), bundle
    # Metadata-only refresh is already the original tiny three-file tar. Full
    # native publications use one freshly authorized official blob reference.
    if not force_raw and not index.get("metadata_only") and expected is not None and artifact is not None:
        try:
            contents = prepare_reference(bundle, index, work, expected, artifact)
            transport = "github-artifact-reference"
        except ReferenceUnavailable:
            # Select the existing long raw transport before attempting SSH.
            # There is no SSH retry, credential change or broader host policy.
            transport = "raw-fallback"
    archive = work / "publication.tar"
    with tarfile.open(archive, "x") as output:
        for path in sorted(contents.rglob("*")):
            if path.is_symlink():
                raise ValueError("Publication transport cannot contain links")
            if path.is_file():
                output.add(path, arcname=path.relative_to(contents).as_posix(), recursive=False)
    identity, hosts = work / "identity", work / "known_hosts"
    for path, value in ((identity, os.environ["DEPLOY_KEY"]), (hosts, os.environ["KNOWN_HOSTS"])):
        with path.open("x", encoding="utf-8") as handle:
            path.chmod(0o600)
            handle.write(value.rstrip() + "\n")
    with archive.open("rb") as incoming:
        result = subprocess.run(["ssh", "-i", str(identity), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(hosts),
            "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=6",
            user + "@" + host, "ljqc-update-upload"], stdin=incoming, capture_output=True,
            timeout=UPLOAD_TIMEOUT, check=False)
    if result.returncode:
        raise ValueError("The restricted receiver did not complete publication")
    receipt = strict_json(result.stdout)
    if (set(receipt) != {"status", "release_id", "sequence", "base_installer_changed", "business_data_access"}
            or receipt.get("status") != "published" or receipt.get("release_id") != index["release_id"]
            or type(receipt.get("sequence")) is not int
            or receipt.get("sequence") != index["sequence"] or receipt.get("base_installer_changed") is not False
            or receipt.get("business_data_access") is not False):
        raise ValueError("The receiver did not return the exact selected immutable-base publication receipt")
    return {**receipt, "transport": transport}


def publish_current(bundle: Path, trust_path: Path, *, artifact_id: int, artifact_sha256: str) -> dict:
    """Shared normal-publish sender after the existing native/signing job gates."""
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REPOSITORY_ID") != str(REPOSITORY_ID)):
        raise ValueError("Current publication requires the protected original main workflow")
    index = verify_bundle(bundle, trust_path)
    temporary = Path(os.environ["RUNNER_TEMP"])
    if not temporary.is_absolute() or not temporary.is_dir() or temporary.is_symlink():
        raise ValueError("The publication temporary directory is invalid")
    expected, artifact = None, None
    if not index.get("metadata_only"):
        expected = Expected(int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]),
            artifact_id, artifact_sha256.removeprefix("sha256:"), os.environ["GITHUB_SHA"],
            index["source_digest"], index["build_number"], index["version"])
        expected.validate()
        validate_index_identity(index, expected)
        run = api_json(f"repos/{REPOSITORY}/actions/runs/{expected.run_id}")
        if (run.get("id") != expected.run_id or run.get("run_attempt") != expected.attempt
                or run.get("head_sha") != expected.source_commit or run.get("head_branch") != "main"
                or run.get("repository", {}).get("id") != REPOSITORY_ID
                or run.get("head_repository", {}).get("id") != REPOSITORY_ID):
            raise ValueError("Current signed publication has a different original run identity")
        artifact = validate_artifact(api_json(f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"),
                                     expected, run["run_number"])
    with tempfile.TemporaryDirectory(prefix="ljqc-publish-", dir=temporary) as directory:
        work = Path(directory)
        work.chmod(0o700)
        return transfer(bundle, index, work, trust_path, expected=expected, artifact=artifact, force_raw=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-id", "attempt", "artifact-id", "build-number"):
        parser.add_argument("--" + name, type=int, required=True)
    for name in ("artifact-sha256", "source-commit", "source-digest", "version"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output-receipt", type=Path, required=True)
    parser.add_argument("--raw-transfer", choices=("true", "false"), default="true")
    args = parser.parse_args()
    temporary = require_main_dispatch()
    expected = Expected(args.run_id, args.attempt, args.artifact_id, args.artifact_sha256,
                        args.source_commit, args.source_digest, args.build_number, args.version)
    expected.validate()
    base = f"repos/{REPOSITORY}/actions/runs/{expected.run_id}"
    run = api_json(base)
    jobs = api_json(base + f"/attempts/{expected.attempt}/jobs?per_page=100")
    artifacts = api_json(base + "/artifacts?per_page=100")
    artifact = validate_source(run, jobs, artifacts, expected)
    output = args.output_receipt.absolute()
    if not output.is_relative_to(ROOT / "output") or output.exists() or output.is_symlink():
        raise ValueError("The bounded result file must be a new checkout output file")
    trust_path = ROOT / "resources/desktop_update_trust.json"
    with tempfile.TemporaryDirectory(prefix="ljqc-signed-resend-", dir=temporary) as directory:
        work = Path(directory)
        work.chmod(0o700)
        archive, bundle = work / "accepted.zip", work / "signed"
        if shutil.disk_usage(work).free < artifact["size_in_bytes"] * 3 + 1024**3:
            raise ValueError("Insufficient isolated disk for the selected signed publication")
        download_zip(artifact, archive, expected)
        index = extract_signed_zip(archive, bundle, trust_path, expected)
        receipt = transfer(bundle, index, work, trust_path, expected=expected, artifact=artifact,
                           force_raw=args.raw_transfer == "true")
        result = {"status": "published", "source_run_id": expected.run_id, "source_attempt": expected.attempt,
            "artifact_id": expected.artifact_id, "artifact_sha256": expected.artifact_sha256,
            "source_commit": expected.source_commit, "source_digest": expected.source_digest,
            "build_number": expected.build_number, "release_id": index["release_id"], "sequence": index["sequence"],
            "original_signed_bytes_preserved": True, "signed_index_sha256": digest(bundle / "release-index.json"),
            "native_rebuilt": False, "release_resigned": False, "server_receipt": receipt}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2); handle.write("\n")
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print gh/ssh stderr, credentials, redirect URLs or raw metadata.
        print(json.dumps({"status": "failed", "reason": type(error).__name__,
                          "message": "Accepted release resend did not complete; no successful publication is claimed."}))
        raise SystemExit(1)
