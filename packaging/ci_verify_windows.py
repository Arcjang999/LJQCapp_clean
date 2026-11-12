"""Verify a real Windows build and export only installer delivery artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packaging"), str(ROOT)]
from release_payload import MANIFEST_NAME, assert_protected_payload, validate_payload
from services.release_info import get_release_info
from services.runtime_lock import ServiceLock


def read_json(path: Path) -> dict:
    # PowerShell 5.1 Set-Content -Encoding UTF8 includes a BOM.
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Expected an object in " + str(path))
    return payload


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def assert_x64_pe(path: Path) -> None:
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("Not a PE executable: " + str(path))
        handle.seek(0x3C)
        offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(offset)
        if handle.read(4) != b"PE\0\0" or struct.unpack("<H", handle.read(2))[0] != 0x8664:
            raise ValueError("Expected a Windows x64 executable: " + str(path))


def verify_build(build_root: Path) -> tuple[Path, dict, Path]:
    receipts = sorted(build_root.rglob("build_receipt.json"))
    if len(receipts) != 1:
        raise ValueError(f"Expected exactly one Windows build receipt, found {len(receipts)}")
    receipt_path = receipts[0]
    receipt = read_json(receipt_path)
    if receipt.get("status") != "installer_built" or receipt.get("mutable_data_included") is not False:
        raise ValueError("This build did not produce a verified data-free Setup installer")
    metadata = receipt["release"]
    if metadata.get("target") != "windows-x86_64":
        raise ValueError("Windows build has the wrong platform metadata")
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        current = get_release_info(ROOT, include_source_state=True).as_dict()
        if current["source_dirty"] or current["git_commit"] != os.environ.get("GITHUB_SHA"):
            raise ValueError("CI verification must use the clean source checkout for GITHUB_SHA")
        identity_keys = ("version", "build_number", "data_format_version", "git_commit", "source_digest", "source_dirty")
        if any(metadata.get(key) != current[key] for key in identity_keys):
            raise ValueError("Windows receipt does not match the current CI source identity")
    payload = Path(receipt["staging"]).resolve()
    manifest = read_json(payload / MANIFEST_NAME)
    assert_protected_payload(payload)
    if manifest.get("files") != validate_payload(payload):
        raise ValueError("The assembled program differs from its payload manifest")
    keys = ("version", "build_number", "data_format_version", "git_commit", "source_digest", "source_dirty", "target")
    for candidate in (receipt_path.parent / "build_info.json", payload / "build_info.json",
            payload / "_internal/build_info.json", payload / "_internal/app/build_info.json",
            payload / "_internal/app/_internal/build_info.json"):
        embedded = read_json(candidate)
        if any(embedded.get(key) != metadata.get(key) for key in keys):
            raise ValueError("Mismatched embedded release identity: " + str(candidate))
    if any(manifest.get("release", {}).get(key) != metadata.get(key) for key in keys):
        raise ValueError("Payload manifest carries another release identity")
    for executable in (payload / "LJQCApp.exe", payload / "_internal/app/LJQCAppService.exe"):
        assert_x64_pe(executable)
    setup_rows = [row for row in receipt.get("artifacts", []) if str(row.get("path", "")).endswith("-Setup.exe")]
    if len(setup_rows) != 1:
        raise ValueError("Expected one compiled Setup.exe in the build receipt")
    setup = Path(setup_rows[0]["path"]).resolve()
    if setup.parent != receipt_path.parent.resolve() or not setup.is_file():
        raise ValueError("Setup executable is missing or outside its build directory")
    if digest_file(setup) != setup_rows[0]["sha256"]:
        raise ValueError("Setup.exe differs from the recorded SHA-256")
    if (receipt_path.parent / MANIFEST_NAME).read_bytes() != (payload / MANIFEST_NAME).read_bytes():
        raise ValueError("Exported payload manifest does not match the program")
    return receipt_path, receipt, setup


def verify_service(payload: Path, evidence_dir: Path) -> dict:
    qa = Path(tempfile.mkdtemp(prefix="ljqc-windows-ci-qa-"))
    data_root = qa / "data-root"
    runtime = data_root / "runtime"
    runtime.mkdir(parents=True)
    temporary = qa / "temp"
    temporary.mkdir()
    request = runtime / ("shutdown-" + uuid.uuid4().hex + ".request")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    executable = payload / "_internal/app/LJQCAppService.exe"
    env = {**os.environ, "LJQC_DATA_ROOT": str(data_root), "LJQC_SHUTDOWN_FILE": str(request),
           "LJQC_DEPLOYMENT_MODE": "desktop", "PYTHONUTF8": "1", "MPLCONFIGDIR": str(qa / "mpl"),
           "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary)}
    log_path = evidence_dir / "frozen-service.log"
    process = None
    start = time.monotonic()
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen([str(executable), "--port", str(port), "--address", "127.0.0.1"],
                cwd=executable.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Frozen service exited before becoming healthy")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Frozen service did not become healthy within 60 seconds")
                time.sleep(0.25)
            lock = ServiceLock(runtime)
            try:
                try:
                    lock.acquire()
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError("Running service did not hold its data-root lock")
            finally:
                lock.close()
            request.write_text("stop\n", encoding="ascii")
            process.wait(timeout=30)
        if process.returncode != 0:
            raise RuntimeError(f"Frozen service exited with code {process.returncode}")
        lock = ServiceLock(runtime)
        try:
            lock.acquire()
        finally:
            lock.close()
        with socket.socket() as probe:
            probe.settimeout(2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Frozen service port remained open after exit")
        if request.exists():
            raise RuntimeError("Frozen service left its shutdown request behind")
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if "Traceback (most recent call last)" in log_text:
            raise RuntimeError("Frozen service emitted an unhandled traceback")
        return {"status": "passed", "qa_root": str(qa), "health_endpoint": "passed",
                "graceful_exit_code": process.returncode, "data_lock_held_while_running": True,
                "data_lock_released_after_exit": True, "port_closed": True,
                "shutdown_request_cleaned": True, "elapsed_seconds": round(time.monotonic() - start, 2),
                "business_database_initialized": any(data_root.rglob("*.db")),
                "scope": "Headless service startup and shutdown; no browser business session was opened"}
    finally:
        if process is not None and process.poll() is None:
            # Retain all QA files while an unfinished process still owns them.
            # Never call taskkill /F to make a failed shutdown appear successful.
            request.write_text("stop\n", encoding="ascii")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, default=ROOT / "output/github-build/windows")
    parser.add_argument("--output", type=Path, default=ROOT / "output/ci-artifacts/windows")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Windows frozen-program verification must run on Windows")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {"status": "failed", "host": platform.platform(),
        "native_desktop_installation_validation": "pending-windows-10-and-11",
        "algorithm_conformance_validation": "not-assessed", "public_release": False}
    try:
        receipt_path, receipt, setup = verify_build(args.build_root.resolve())
        service = verify_service(Path(receipt["staging"]), output)
        for name in ("build_info.json", "build_receipt.json", MANIFEST_NAME, "windows-build-environment.txt"):
            shutil.copy2(receipt_path.parent / name, output / name)
        for path in receipt_path.parent.glob("*-version*.txt"):
            shutil.copy2(path, output / path.name)
        prep = os.environ.get("LJQC_CI_PREPARATION_RECEIPT", "")
        if prep:
            shutil.copy2(prep, output / "windows-ci-preparation.json")
            for path in Path(prep).parent.glob("inno-compiler-probe-*.log"):
                shutil.copy2(path, output / path.name)
        shutil.copy2(setup, output / setup.name)
        result.update(status="passed", release=receipt["release"], frozen_service=service,
            installer={"file": setup.name, "size_bytes": setup.stat().st_size, "sha256": digest_file(setup)},
            program_zip_exported=False, mutable_data_exported=False)
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "ci-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
