"""Verify native cumulative program archives in isolated data directories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from build_update_patch import export_patch
from release_payload import digest_file
from services.release_info import get_release_info
from services.runtime_lock import ServiceLock
from desktop_updates.payload import extract_verified_payload


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a build receipt object")
    return value


def verify_frozen_tls(executable: Path, *, env: dict, result_file: Path, log_file: Path) -> dict:
    """Only CI may execute this actual frozen-helper network acceptance."""
    if (os.environ.get("GITHUB_ACTIONS", "").lower() != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Frozen helper TLS acceptance is restricted to ephemeral GitHub runners")
    import certifi
    probe_env = {**env, "SSL_CERT_FILE": str(result_file.parent / "absent-ambient-ca.pem"),
                 "SSL_CERT_DIR": str(result_file.parent / "absent-ambient-ca-directory")}
    with log_file.open("wb") as log:
        subprocess.run([str(executable), "--tls-self-test", "--probe-result", str(result_file)],
            cwd=executable.parent, env=probe_env, stdout=log, stderr=subprocess.STDOUT,
            check=True, timeout=90)
    result = read_json(result_file)
    if (result.get("status") != "passed" or result.get("frozen") is not True
            or result.get("bundled_ca") is not True or result.get("trusted_ca_count", 0) < 1
            or result.get("certifi_version") != certifi.__version__
            or result.get("ca_sha256") != digest_file(Path(certifi.where()))
            or result.get("hostname_verification") is not True
            or result.get("certificate_verification") != "CERT_REQUIRED"
            or result.get("minimum_tls_version") not in ("TLSv1_2", "TLSv1_3")
            or result.get("unknown_self_signed_rejected") is not True
            or result.get("release_index_signature_verified") is not True
            or result.get("production_origin") != "https://159.75.243.120"
            or result.get("business_storage_opened") is not False
            or set(result.get("platform_manifests", {})) != {"windows-x86_64", "macos-arm64"}
            or any(item.get("signature_verified") is not True
                   for item in result.get("platform_manifests", {}).values())):
        raise ValueError("The actual frozen helper did not pass production HTTPS/signature and untrusted-CA acceptance")
    return result


def find_program(build_root: Path, target: str) -> tuple[Path, dict]:
    if target == "windows-x86_64":
        paths = list(build_root.rglob("build_receipt.json"))
        if len(paths) != 1:
            raise ValueError("Expected one Windows program build receipt")
        receipt = read_json(paths[0])
        if receipt.get("status") != "payload_built" or receipt.get("mutable_data_included") is not False:
            raise ValueError("Daily patch builds must use SkipInstaller and exclude mutable data")
        payload, metadata = Path(receipt["staging"]), receipt["release"]
    else:
        paths = list(build_root.glob("*/artifact.json"))
        if len(paths) != 1:
            raise ValueError("Expected one Mac program build receipt")
        receipt = read_json(paths[0])
        if receipt.get("artifact_kind") != "program-payload" or receipt.get("base_installer_rebuilt") is not False:
            raise ValueError("Daily Mac builds must use skip-dmg")
        payload, metadata = Path(receipt["payload_root"]), read_json(paths[0].parent / "build_info.json")
    expected = get_release_info(ROOT, include_source_state=True).as_dict()
    for key in ("version", "build_number", "data_format_version", "git_commit", "source_digest", "source_dirty"):
        if metadata.get(key) != expected[key]:
            raise ValueError("Native build source differs from the checked-out source: " + key)
    if metadata.get("target") != target or metadata.get("source_dirty") is not False:
        raise ValueError("Native program source/target is not publishable")
    if os.environ.get("GITHUB_ACTIONS") == "true" and metadata["git_commit"] != os.environ.get("GITHUB_SHA"):
        raise ValueError("Native source does not match GITHUB_SHA")
    return payload, metadata


def verify_native_payload(program: Path, target: str, output: Path) -> dict:
    """Probe the actual extracted archive, not a separate pre-export directory."""
    desktop = program / ("LJQCApp.exe" if target == "windows-x86_64" else "LJQCApp.app/Contents/MacOS/LJQCApp")
    service = program / ("_internal/app/LJQCAppService.exe" if target == "windows-x86_64" else "LJQCApp.app/Contents/Resources/app/LJQCAppService")
    import certifi
    ca_sha = digest_file(Path(certifi.where()))
    packaged_roots = [path for path in program.rglob("cacert.pem") if path.parent.name == "certifi" and path.is_file()]
    if not packaged_roots or any(digest_file(path) != ca_sha for path in packaged_roots):
        raise ValueError("The native program did not retain the pinned certifi root bundle")
    qa = Path(tempfile.mkdtemp(prefix="ljqc-native-update-qa-"))
    data = qa / "data"
    runtime = data / "runtime"
    runtime.mkdir(parents=True)
    request = runtime / ("shutdown-" + uuid.uuid4().hex + ".request")
    env = {**os.environ, "LJQC_DATA_ROOT": str(data), "LJQC_DEPLOYMENT_MODE": "desktop",
           "LJQC_SHUTDOWN_FILE": str(request), "PYTHONUTF8": "1"}
    if target == "macos-arm64":
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(program / "LJQCApp.app")], check=True, capture_output=True)
        for executable in (desktop, service):
            result = subprocess.run(["lipo", "-archs", str(executable)], capture_output=True, text=True, check=True)
            if result.stdout.strip() != "arm64":
                raise ValueError("A Mac executable has the wrong architecture")
    else:
        from ci_verify_windows import assert_x64_pe
        assert_x64_pe(desktop)
        assert_x64_pe(service)
    for label, executable in (("desktop", desktop), ("service", service)):
        with (output / (label + "-version.log")).open("wb") as log:
            subprocess.run([str(executable), "--version"], cwd=executable.parent, env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=45)
    if list(data.rglob("*.db")):
        raise ValueError("Read-only version commands created a database")
    preflight_root = qa / "preflight-data"
    preflight_root.mkdir()
    (preflight_root / "update-candidate.json").write_text(json.dumps({
        "format": "ljqc-update-candidate", "base_release": "1.0.0-2026110606"}), encoding="utf-8")
    result_file = preflight_root / "preflight.json"
    with (output / "frozen-preflight.log").open("wb") as log:
        subprocess.run([str(service), "--update-preflight", "--update-result", str(result_file)],
            cwd=service.parent, env={**env, "LJQC_DATA_ROOT": str(preflight_root), "LJQC_UPDATE_PREFLIGHT": "1"},
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    preflight = read_json(result_file)
    recorded = read_json(program / "release_payload_manifest.json")["release"]
    if preflight.get("status") != "passed" or any(preflight.get("release", {}).get(field) != recorded[field]
            for field in ("version", "build_number", "git_commit", "source_digest", "target")):
        raise ValueError("Frozen preflight does not match the exported program identity")
    import sqlite3
    with sqlite3.connect((preflight_root / "data/qc_lj_app.db").as_uri() + "?mode=ro", uri=True) as database:
        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Frozen preflight database is not intact")
        for table in ("projects", "batches", "results"):
            if database.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]:
                raise ValueError("Frozen preflight unexpectedly installed customer/demo business rows")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = None
    try:
        with (output / "frozen-service.log").open("wb") as log:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if target == "windows-x86_64" else {"start_new_session": True}
            process = subprocess.Popen([str(service), "--port", str(port), "--address", "127.0.0.1"],
                                       cwd=service.parent, env=env, stdout=log, stderr=subprocess.STDOUT, **options)
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise ValueError("Extracted patch service exited before becoming healthy")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise ValueError("Extracted patch service failed its health check")
                time.sleep(0.25)
            lock = ServiceLock(runtime)
            try:
                try:
                    lock.acquire()
                except RuntimeError:
                    pass
                else:
                    raise ValueError("The running native service did not hold its data lock")
            finally:
                lock.close()
            request.write_text("stop\n", encoding="ascii")
            process.wait(timeout=30)
        if process.returncode:
            raise ValueError("Extracted patch service did not exit normally")
        if "Traceback" in (output / "frozen-service.log").read_text(encoding="utf-8", errors="replace"):
            raise ValueError("Extracted patch service emitted a traceback")
        lock = ServiceLock(runtime)
        try:
            lock.acquire()
        finally:
            lock.close()
        if request.exists() or list(data.rglob("*.db")):
            raise ValueError("Headless service probe left shutdown state or opened business data")
        with socket.socket() as probe:
            probe.settimeout(2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError("The native service port is still open")
        return {"status": "passed", "native_architecture": "passed", "cli_versions": "passed",
                "service_health": "passed", "graceful_exit": "passed", "locks_released": True,
                "business_database_created": False, "gui_installation_tested": False,
                "isolated_empty_database_preflight": "passed", "data_migration_tested": False,
                "bundled_ca_files": [path.relative_to(program).as_posix() for path in packaged_roots],
                "bundled_ca_sha256": ca_sha, "certifi_version": certifi.__version__}
    finally:
        if process is not None and process.poll() is None:
            request.write_text("stop\n", encoding="ascii")
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # Never force-terminate a service to make a check pass.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--target", choices=("windows-x86_64", "macos-arm64"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual_target = "windows-x86_64" if sys.platform == "win32" else "macos-arm64" if sys.platform == "darwin" and platform.machine() == "arm64" else None
    if actual_target != args.target:
        raise ValueError("Patch acceptance must run on the matching native platform")
    payload, metadata = find_program(args.build_root, args.target)
    candidate = export_patch(payload, metadata, args.output)
    name = candidate["artifact"]["url"].rsplit("/", 1)[-1]
    with tempfile.TemporaryDirectory(prefix="ljqc-extracted-update-") as temporary:
        extracted = Path(temporary) / "program"
        extract_verified_payload(args.output / name, extracted, candidate)
        result = verify_native_payload(extracted, args.target, args.output)
    receipt = {"status": "passed", "release": metadata, "base_release": candidate["base_release"],
               "artifact": {"file": name, "sha256": digest_file(args.output / name)},
               "extracted_native_program": result, "signed": False, "published": False,
               "base_installer_rebuilt": False, "production_data_access": False}
    (args.output / "patch-ci-verification.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "target": args.target, "build_number": metadata["build_number"]}))


if __name__ == "__main__":
    main()
