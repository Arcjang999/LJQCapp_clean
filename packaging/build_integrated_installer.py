"""Package the accepted 1.01 program with an automatically installed updater.

The already signed program archive is copied verbatim.  Only the small updater
and installer are frozen from this source; no business program is rebuilt.
"""
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

from desktop_updates.helper_repair import write_helper_manifest
from desktop_updates.protocol import BASE_RELEASE, DEFAULT_ORIGIN, strict_json, verify_envelope
from release_payload import digest_file, validate_payload
from services.release_info import get_release_info

PROGRAM_VERSION = "1.0.1"
PROGRAM_BUILD = 2026110901
INSTALLER_BUILD = 2026111001


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify_program(manifest_path: Path, patch_path: Path, target: str) -> tuple[dict, bytes]:
    raw = manifest_path.read_bytes()
    envelope = strict_json(raw)
    # This fixed, embedded base remains installable after channel metadata
    # expires.  Its original signature is checked at issuance, then the exact
    # manifest/bundle bytes are bound into the frozen installer.  Online
    # UpdateManager checks retain their normal expiry and rollback rules.
    issued = datetime.fromisoformat(envelope["signed"]["issued_at"].replace("Z", "+00:00"))
    trust = strict_json((ROOT / "resources/desktop_update_trust.json").read_bytes())
    manifest = verify_envelope(raw, trust, target=target, installed_build=0,
                               highest_sequence=0, now=issued)
    if manifest["version"] != PROGRAM_VERSION or manifest["build_number"] != PROGRAM_BUILD:
        raise ValueError("The integrated 1.01 base must contain the accepted build 2026110901")
    if (not patch_path.is_file() or patch_path.is_symlink()
            or patch_path.stat().st_size != manifest["artifact"]["size_bytes"]
            or digest_file(patch_path) != manifest["artifact"]["sha256"]):
        raise ValueError("The program archive differs from its signed release")
    return manifest, raw


def build_integrated_installer(manifest_path: Path, patch_path: Path, output: Path, *,
                               installer_build: int = INSTALLER_BUILD,
                               inno: Path | None = None,
                               chinese_messages: Path | None = None) -> dict:
    target = "windows-x86_64" if sys.platform == "win32" else "macos-arm64"
    if (sys.platform not in ("win32", "darwin")
            or (sys.platform == "darwin" and platform.machine() != "arm64")):
        raise ValueError("Integrated installers must be built on native Windows x64 or Apple Silicon")
    if type(installer_build) is not int or installer_build <= PROGRAM_BUILD:
        raise ValueError("The integrated packaging build must be newer than the accepted program")
    source = get_release_info(ROOT, include_source_state=True).as_dict()
    if source["source_dirty"]:
        raise ValueError("Integrated installer builds require a clean source checkpoint")
    manifest, manifest_bytes = _verify_program(manifest_path.resolve(), patch_path.resolve(), target)
    metadata = {**source, "version": PROGRAM_VERSION, "app_version": PROGRAM_VERSION,
                "build_number": installer_build, "target": target,
                "build_time": datetime.now(timezone.utc).isoformat()}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "build-logs"
    logs.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="ljqc-integrated-") as temporary:
        work = Path(temporary)
        metadata_path = work / "build_info.json"
        _write_json(metadata_path, metadata)
        env = {**os.environ, "LJQC_UPDATE_BUILD_INFO": str(metadata_path),
               "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache")}

        def run(arguments: list[str], name: str, *, timeout: int = 1200) -> None:
            with (logs / (name + ".log")).open("wb") as log:
                subprocess.run(arguments, cwd=ROOT, env=env, stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=timeout)

        if target == "macos-arm64":
            iconset = work / "LJQCApp.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                    run(["sips", "-z", str(size * scale), str(size * scale),
                         str(ROOT / "assets/app_icon.png"), "--out", str(iconset / name)],
                        "icon-" + name)
            icon = work / "LJQCApp.icns"
            run(["iconutil", "-c", "icns", str(iconset), "-o", str(icon)], "create-icon")
            env["LJQC_MAC_ICON"] = str(icon)

        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             "--distpath", str(work / "helper-dist"), "--workpath", str(work / "helper-work"),
             str(ROOT / "packaging/LJQCUpdateBootstrap.spec")], "freeze-updater")
        helper = work / "helper-dist" / ("LJQCAppUpdater" if target == "windows-x86_64"
                                            else "LJQCAppUpdater.app")
        if not helper.is_dir():
            raise ValueError("The updater application was not produced")
        validate_payload(helper)
        if target == "macos-arm64":
            for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
                result = subprocess.run(["xattr", "-dr", attribute, str(helper)], capture_output=True)
                if result.returncode not in (0, 1):
                    raise ValueError("Cannot prepare the updater bundle for signing")
            run(["codesign", "--force", "--deep", "--sign", "-", str(helper)], "sign-updater")
            run(["codesign", "--verify", "--deep", "--strict", str(helper)], "verify-updater-signature")
        helper_inventory = write_helper_manifest(helper, target)
        helper_manifest = (helper / "update_helper_manifest.json" if target == "windows-x86_64"
                           else helper.parent / "update_helper_manifest.json")

        bundle = work / "integrated-bundle"
        (bundle / "helper").mkdir(parents=True)
        shutil.copytree(helper, bundle / "helper" / helper.name, symlinks=True)
        if target == "macos-arm64":
            shutil.copy2(helper_manifest, bundle / "helper/update_helper_manifest.json")
        (bundle / "manifest.json").write_bytes(manifest_bytes)
        shutil.copyfile(patch_path, bundle / "program.tar.gz")
        helper_manifest_relative = ("helper/LJQCAppUpdater/update_helper_manifest.json"
                                    if target == "windows-x86_64"
                                    else "helper/update_helper_manifest.json")
        bundle_identity = {
            "format": "ljqc-integrated-installer", "format_version": 1,
            "product": "LJQCApp", "target": target, "version": PROGRAM_VERSION,
            "installer_build_number": installer_build, "program_build_number": PROGRAM_BUILD,
            "base_release": BASE_RELEASE,
            "manifest": {"path": "manifest.json", "sha256": digest_file(bundle / "manifest.json")},
            "program": {"path": "program.tar.gz", "sha256": manifest["artifact"]["sha256"],
                        "size_bytes": manifest["artifact"]["size_bytes"]},
            "helper": {"path": "helper/" + helper.name,
                       "manifest_path": helper_manifest_relative,
                       "manifest_sha256": digest_file(helper_manifest)},
        }
        _write_json(bundle / "bundle.json", bundle_identity)
        env["LJQC_INTEGRATED_BUNDLE"] = str(bundle)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             "--distpath", str(work / "installer-dist"), "--workpath", str(work / "installer-work"),
             str(ROOT / "packaging/LJQCIntegratedInstaller.spec")], "freeze-installer")
        installer = work / "installer-dist" / ("LJQCAppInstaller" if target == "windows-x86_64"
                                                 else "LJQCAppInstaller.app")
        if not installer.is_dir():
            raise ValueError("The integrated installer application was not produced")
        destination = installer / ("_internal/integrated-bundle" if target == "windows-x86_64"
                                   else "Contents/Resources/integrated-bundle")
        # Copy after freezing to preserve every nested helper byte and link.
        # The final outer Mac seal is made only after these resources exist.
        shutil.copytree(bundle, destination, symlinks=True)
        if target == "windows-x86_64":
            if not inno or not inno.is_file() or not chinese_messages or not chinese_messages.is_file():
                raise ValueError("Inno Setup and its Chinese messages file are required")
            filename = f"LJQCApp-{PROGRAM_VERSION}-{installer_build}-windows-x64-Setup.exe"
            artifact = output / filename
            if artifact.exists():
                raise ValueError("Refusing to replace an existing integrated installer")
            run([str(inno), f"/DSourceDir={installer}", f"/DOutputDir={output}",
                 f"/DOutputBase={artifact.stem}", f"/DAppVersion={PROGRAM_VERSION}",
                 f"/DBuildNumber={installer_build}", f"/DChineseMessagesFile={chinese_messages}",
                 str(ROOT / "packaging/integrated_windows.iss")], "compile-integrated-setup")
        else:
            for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
                result = subprocess.run(["xattr", "-dr", attribute, str(installer)], capture_output=True)
                if result.returncode not in (0, 1):
                    raise ValueError("Cannot prepare the integrated bundle for signing")
            # The nested updater has already been sealed and inventoried.
            # Signing only the outer bundle retains those immutable bytes.
            run(["codesign", "--force", "--sign", "-", str(installer)], "sign-installer")
            run(["codesign", "--verify", "--deep", "--strict", str(installer)], "verify-installer-signature")
            image = work / "image"
            image.mkdir()
            shutil.copytree(installer, image / installer.name, symlinks=True)
            (image / "安装说明.txt").write_text(
                "LJQCApp 1.01\n\n打开 LJQCAppInstaller，点击安装。程序和自动更新器一起安装，"
                "无需选择程序或更新器位置。安装后从“应用程序”中的 LJQCApp 打开。\n"
                "已有用户请先保存工作并关闭软件，原有资料继续保留。\n"
                "后续更新由软件从服务器获取，无需再次下载完整安装包。\n"
                "本开发版尚待 Apple Developer ID 签名和公证。\n", encoding="utf-8")
            filename = f"LJQCApp-{PROGRAM_VERSION}-{installer_build}-macos-arm64-pending-signing.dmg"
            artifact = output / filename
            if artifact.exists():
                raise ValueError("Refusing to replace an existing integrated installer")
            run(["hdiutil", "create", "-volname", "LJQCApp 1.01 安装", "-srcfolder", str(image),
                 "-format", "UDZO", "-ov", str(artifact)], "create-integrated-dmg")

        if not artifact.is_file():
            raise ValueError("The integrated installer artifact was not produced")
        current = get_release_info(ROOT, include_source_state=True).as_dict()
        if any(current[key] != source[key] for key in ("git_commit", "source_digest", "source_dirty")):
            raise ValueError("Source changed while building the integrated installer")
        record = {
            "published": True, "target": target, "version": PROGRAM_VERSION,
            "build_number": installer_build, "program_build_number": PROGRAM_BUILD,
            "published_at": datetime.now(timezone.utc).date().isoformat(),
            "url": f"{DEFAULT_ORIGIN}/downloads/releases/{PROGRAM_VERSION}/{installer_build}/{filename}",
            "sha256": digest_file(artifact), "size_bytes": artifact.stat().st_size,
            "updater_integrated": True,
        }
        _write_json(output / "download-record.json", record)
        receipt = {
            "status": "built_not_published", "target": target,
            "installer_release": metadata, "program_release": {
                "version": manifest["version"], "build_number": manifest["build_number"],
                "source_commit": manifest["source_commit"], "source_digest": manifest["source_digest"],
                "sha256": manifest["artifact"]["sha256"],
            },
            "artifact": {"file": filename, "sha256": record["sha256"],
                         "size_bytes": record["size_bytes"]},
            "bundle_sha256": digest_file(bundle / "bundle.json"),
            "updater_integrated": True, "program_archive_rebuilt": False,
            "helper_file_count": len(helper_inventory["files"]),
            "business_storage_opened": False, "additional_native_validation": "not_requested",
            "code_signing": ("ad-hoc-pending-developer-id" if target == "macos-arm64"
                             else "unsigned-development"),
        }
        _write_json(output / "integrated-build-receipt.json", receipt)
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--installer-build", type=int, default=INSTALLER_BUILD)
    parser.add_argument("--inno-compiler", type=Path)
    parser.add_argument("--chinese-messages", type=Path)
    args = parser.parse_args()
    result = build_integrated_installer(args.manifest, args.patch, args.output,
        installer_build=args.installer_build, inno=args.inno_compiler,
        chinese_messages=args.chinese_messages)
    print(json.dumps({"status": result["status"], "target": result["target"],
                      "artifact": result["artifact"]}))


if __name__ == "__main__":
    main()
