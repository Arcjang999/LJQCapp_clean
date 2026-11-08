"""Build and probe the separate Windows/Mac one-time update bootstrap."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from release_payload import digest_file, validate_payload
from services.release_info import get_release_info


def build_bootstrap(output: Path, *, inno: Path | None = None, chinese_messages: Path | None = None) -> dict:
    target = "windows-x86_64" if sys.platform == "win32" else "macos-arm64"
    if sys.platform not in ("win32", "darwin") or (sys.platform == "darwin" and platform.machine() != "arm64"):
        raise ValueError("The update bootstrap must be built on its native Windows x64 or Mac arm64 host")
    metadata = get_release_info(ROOT, include_source_state=True).as_dict()
    metadata["target"] = target
    if metadata["source_dirty"]:
        raise ValueError("Bootstrap builds require a clean source checkpoint")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ljqc-bootstrap-build-") as temporary:
        work = Path(temporary)
        metadata_file = work / "build_info.json"
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        env = {**os.environ, "LJQC_UPDATE_BUILD_INFO": str(metadata_file),
               "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache")}
        # Keep diagnostic text/JSON even when a frozen/native probe fails.
        # No temporary app, private key or business data is copied here.
        logs = output / "bootstrap-verification-logs"
        logs.mkdir(exist_ok=False)
        def run(arguments: list[str], name: str, *, extra_env: dict | None = None, timeout: int = 1200):
            with (logs / (name + ".log")).open("wb") as log:
                subprocess.run(arguments, cwd=ROOT, env=extra_env or env, stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=timeout)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
             str(ROOT / "packaging/LJQCUpdateBootstrap.spec")], "freeze-bootstrap")
        package = work / "dist" / ("LJQCAppUpdater" if target == "windows-x86_64" else "LJQCAppUpdater.app")
        if not package.is_dir():
            raise ValueError("The frozen bootstrap package was not produced")
        validate_payload(package)
        executable = package / ("LJQCAppUpdater.exe" if target == "windows-x86_64" else "Contents/MacOS/LJQCAppUpdater")
        if target == "macos-arm64":
            # Build in an unmanaged temp directory and preserve quarantine.
            for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
                result = subprocess.run(["xattr", "-dr", attribute, str(package)], capture_output=True)
                if result.returncode not in (0, 1):
                    raise ValueError("Cannot remove incompatible bundle presentation metadata")
            run(["codesign", "--force", "--deep", "--sign", "-", str(package)], "sign-bootstrap")
            run(["codesign", "--verify", "--deep", "--strict", str(package)], "verify-bootstrap-signature")
        from desktop_updates.helper_repair import verify_helper_manifest, write_helper_manifest
        if target == "windows-x86_64":
            write_helper_manifest(package, target)
            helper_manifest_path = package / "update_helper_manifest.json"
            helper_manifest = verify_helper_manifest(package, target)
        qa = work / "probe-data"
        probe_env = {**env, "LJQC_DATA_ROOT": str(qa)}
        for command in ("--version", "--self-test"):
            run([str(executable), command], "probe-" + command.lstrip("-"), extra_env=probe_env, timeout=60)
            if qa.exists():
                raise ValueError("A read-only bootstrap probe unexpectedly created a data directory")
        ui_probe = {"status": "not-run-outside-github"}
        tls_probe = {"status": "not-run-outside-github"}
        if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
            from ci_verify_update import verify_frozen_tls
            tls_probe = verify_frozen_tls(executable, env=probe_env,
                result_file=logs / "native-tls-probe.json", log_file=logs / "probe-native-tls.log")
            if qa.exists():
                raise ValueError("The frozen helper TLS probe unexpectedly created business storage")
            ui_result = work / "ui-probe-result.json"
            run([str(executable), "--ui-self-test", "--probe-result", str(ui_result)],
                "probe-native-ui", extra_env={**probe_env, "QT_QPA_PLATFORM": "offscreen"}, timeout=30)
            ui_probe = json.loads(ui_result.read_text(encoding="utf-8"))
            if (ui_probe.get("status") != "passed" or ui_probe.get("ui_closed") is not True
                    or ui_probe.get("worker_joined") is not True
                    or ui_probe.get("native_gui_initialized") is not True
                    or ui_probe.get("repair_mode_ui_fields_hidden") is not True
                    or ui_probe.get("business_storage_opened") is not False
                    or not {"check", "download", "apply", "launch"}.issubset(set(ui_probe.get("stages", [])))):
                raise ValueError("Native helper UI/thread lifecycle did not pass its explicit probe")
            if qa.exists():
                raise ValueError("The helper UI lifecycle probe unexpectedly created business storage")
            shutil.copyfile(ui_result, logs / "native-ui-probe.json")
        if target == "windows-x86_64":
            if not inno or not inno.is_file() or not chinese_messages or not chinese_messages.is_file():
                raise ValueError("Inno Setup and its verified Chinese messages file are required")
            filename = f"LJQCApp-UpdateBootstrap-1-{metadata['build_number']}-windows-x64-Setup.exe"
            artifact = output / filename
            if artifact.exists():
                raise ValueError("Refusing to replace an immutable bootstrap artifact")
            run([str(inno), f"/DSourceDir={package}", f"/DOutputDir={output}",
                 f"/DOutputBase={artifact.stem}", f"/DBuildNumber={metadata['build_number']}",
                 f"/DChineseMessagesFile={chinese_messages}", str(ROOT / "packaging/update_bootstrap_windows.iss")],
                "compile-bootstrap", timeout=600)
        else:
            image = work / "image"
            image.mkdir()
            shutil.copytree(package, image / package.name, symlinks=True)
            # A complete byte manifest must sit beside the signed .app. Adding
            # it inside after codesign would invalidate the application's seal.
            write_helper_manifest(image / package.name, target)
            helper_manifest_path = image / "update_helper_manifest.json"
            helper_manifest = verify_helper_manifest(image / package.name, target)
            (image / "更新接入说明.txt").write_text(
                "LJQCApp 1.00 更新接入补丁\n\n"
                "先正常退出原软件，再打开 LJQCAppUpdater。首次接入选择已安装的 LJQCApp；"
                "已接入的电脑会修复更新组件，保留当前程序、入口及资料。\n"
                "接入会保留原资料并建立更新入口；以后每次启动可检查和下载累计补丁。\n"
                "请先保存完整备份。此组件尚待 Apple Developer ID 签名和公证，"
                "首次打开仍可能显示未验证开发者提示；遵循软件网页下载说明，不关闭系统保护。\n",
                encoding="utf-8")
            filename = f"LJQCApp-UpdateBootstrap-1-{metadata['build_number']}-macos-arm64-pending-signing.dmg"
            artifact = output / filename
            if artifact.exists():
                raise ValueError("Refusing to replace an immutable bootstrap artifact")
            run(["hdiutil", "create", "-volname", "LJQCApp 更新接入", "-srcfolder", str(image),
                 "-format", "UDZO", "-ov", str(artifact)], "create-bootstrap-dmg", timeout=600)
            run(["hdiutil", "verify", str(artifact)], "verify-bootstrap-dmg", timeout=120)
        if not artifact.is_file():
            raise ValueError("Bootstrap installer artifact was not produced")
        current = get_release_info(ROOT, include_source_state=True).as_dict()
        if any(current[field] != metadata[field] for field in ("git_commit", "source_digest", "source_dirty")):
            raise ValueError("Source changed while building the bootstrap")
        receipt = {"status": "passed", "target": target, "updater_version": 1,
            "release": metadata, "built_at": datetime.now(timezone.utc).isoformat(),
            "artifact": {"file": artifact.name, "size_bytes": artifact.stat().st_size,
                         "sha256": digest_file(artifact)},
            "read_only_cli_probes": "passed", "business_database_access": False,
            "native_ui_lifecycle_probe": ui_probe,
            "native_production_tls_probe": tls_probe,
            "helper_integrity_manifest": {"sha256": digest_file(helper_manifest_path),
                                          "file_count": len(helper_manifest["files"]),
                                          "inside_signed_mac_app": False},
            "installer_payload_subdirectory": "payload-" + str(metadata["build_number"]) if target == "windows-x86_64" else None,
            "native_gui_installation_validation": "not-performed-by-this-script",
            "code_signing": "ad-hoc-pending-developer-id" if target == "macos-arm64" else "unsigned-development",
            "base_installer_rebuilt": False}
        (output / "bootstrap-build-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inno-compiler", type=Path)
    parser.add_argument("--chinese-messages", type=Path)
    args = parser.parse_args()
    result = build_bootstrap(args.output, inno=args.inno_compiler, chinese_messages=args.chinese_messages)
    print(json.dumps({"status": result["status"], "target": result["target"], "artifact": result["artifact"]}))


if __name__ == "__main__":
    main()
