"""Install the fixed 1.01 desktop release with its stable update entry.

The frozen installer pins bundle.json in its Python archive. That inventory
binds the original signed program manifest, immutable patch and complete helper
inventory. Only this bundled base is checked at its original signing time;
normal online updates retain their expiry and anti-replay checks.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

from services.release_info import display_version_label
from .bootstrap import (BootstrapError, default_base_path, install_windows_shortcuts,
                        verify_mac_bundle)
from .data_guard import (assert_attachments, assert_current, atomic_json,
                         database_path, digest_file, logical_digest, snapshot)
from .helper_repair import (_fingerprint, _registration, read_helper_identity,
                            verify_helper_manifest)
from .payload import extract_verified_payload, verify_installed_payload
from .protocol import BASE_RELEASE, UPDATER_VERSION, strict_json, verify_envelope

JOURNAL_NAME = "integrated-installation.json"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _read(path: Path, maximum=1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BootstrapError("安装包文件不完整，请重新下载。")
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    if len(value) > maximum:
        raise BootstrapError("安装包清单超过允许大小。")
    return value


def _relative(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise BootstrapError("安装包文件位置无效。")
    result = root.joinpath(*PurePosixPath(name).parts)
    if result.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise BootstrapError("安装包文件位置无效。")
    return result


def _match_digest(path: Path, expected: str) -> None:
    if not isinstance(expected, str) or _SHA.fullmatch(expected) is None or digest_file(path) != expected:
        raise BootstrapError("安装包文件校验未通过，请重新下载。")


def _fixed_manifest(raw: bytes, trust: dict, target: str) -> dict:
    # The signature is still mandatory. The historic time applies only to the
    # pinned program included in this fixed installer, never to a network reply.
    value = strict_json(raw)
    signed = value.get("signed", {})
    try:
        issued = datetime.fromisoformat(signed["issued_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise BootstrapError("安装包发行时间无法核对。") from error
    return verify_envelope(raw, trust, target=target, installed_build=0,
                           highest_sequence=0, now=issued)


def inspect_bundle(manager, bundle: Path) -> dict:
    """Verify the frozen binding and all installer inputs without writing."""
    bundle = Path(bundle).absolute()
    if bundle.is_symlink() or not bundle.is_dir():
        raise BootstrapError("安装包目录不完整，请重新下载。")
    raw = _read(bundle / "bundle.json")
    if getattr(sys, "frozen", False):
        try:
            from ljqc_integrated_binding import BUNDLE_SHA256
        except ImportError as error:
            raise BootstrapError("安装程序缺少固定发行校验资料。") from error
        if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
            raise BootstrapError("安装包与安装程序不一致，请重新下载。")
    value = strict_json(raw, maximum=1024 * 1024)
    expected = {"format": "ljqc-integrated-installer", "format_version": 1,
                "product": "LJQCApp", "target": manager.target,
                "base_release": BASE_RELEASE}
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        raise BootstrapError("此安装包不适用于当前电脑。")
    if (not isinstance(value.get("version"), str)
            or re.fullmatch(r"\d+\.\d+\.\d+", value["version"]) is None
            or type(value.get("installer_build_number")) is not int
            or type(value.get("program_build_number")) is not int
            or value["installer_build_number"] < value["program_build_number"]
            or value["program_build_number"] <= 2026110606):
        raise BootstrapError("安装包版本记录无效。")
    try:
        manifest_record, program_record, helper_record = value["manifest"], value["program"], value["helper"]
        manifest_path = _relative(bundle, manifest_record["path"])
        archive = _relative(bundle, program_record["path"])
        helper = _relative(bundle, helper_record["path"])
        helper_manifest = _relative(bundle, helper_record["manifest_path"])
        _match_digest(manifest_path, manifest_record["sha256"])
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != program_record["size_bytes"]:
            raise BootstrapError("安装包程序文件不完整。")
        _match_digest(archive, program_record["sha256"])
        _match_digest(helper_manifest, helper_record["manifest_sha256"])
    except (KeyError, TypeError) as error:
        raise BootstrapError("安装包清单不完整。") from error
    manifest_raw = _read(manifest_path)
    manifest = _fixed_manifest(manifest_raw, manager.trust, manager.target)
    if (manifest["version"] != value["version"] or manifest["build_number"] != value["program_build_number"]
            or manifest["artifact"]["sha256"] != program_record["sha256"]
            or manifest["artifact"]["size_bytes"] != program_record["size_bytes"]):
        raise BootstrapError("安装包程序与原签名发行不一致。")
    inventory = verify_helper_manifest(helper, manager.target, manifest_path=helper_manifest)
    identity = inventory["release"]
    if identity["build_number"] != value["installer_build_number"] or identity["version"] != value["version"]:
        raise BootstrapError("安装包更新组件与安装版本不一致。")
    if manager.target == "macos-arm64":
        verify_mac_bundle(helper)
    return {"bundle": bundle, "configuration": value, "bundle_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest": manifest, "manifest_raw": manifest_raw, "archive": archive,
            "helper": helper, "helper_manifest": helper_manifest, "helper_identity": identity}


def _current(manifest: dict, directory: Path, target: str) -> dict:
    current = {"path": str(directory), "entrypoint": manifest["payload"]["entrypoint"],
            "version": manifest["version"], "build_number": manifest["build_number"],
            "target": target, "source_digest": manifest["source_digest"],
            "source_commit": manifest["source_commit"]}
    # Historic recovery journals compare this complete record. Do not invent a
    # display field absent from their original signed manifest.
    if "display_version" in manifest:
        current["display_version"] = display_version_label(manifest)
    return current


def _destination(manager, state: dict) -> Path:
    if state:
        _, _, installed = _registration(manager)
        return installed
    if manager.target == "macos-arm64":
        return Path("/Applications/LJQCApp.app")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise BootstrapError("无法确定当前用户的程序目录。")
    return Path(local).absolute() / "Programs/LJQCAppUpdater"


def _helper_entry(package: Path, target: str) -> Path:
    return package / ("LJQCAppUpdater.exe" if target == "windows-x86_64" else "Contents/MacOS/LJQCAppUpdater")


def _validate_journal(manager, journal: dict) -> tuple[dict, Path, Path, Path, Path]:
    identifier = journal.get("id", "")
    if (journal.get("format") != "ljqc-integrated-installation" or journal.get("format_version") != 1
            or journal.get("target") != manager.target or re.fullmatch(r"[0-9a-f]{32}", identifier) is None):
        raise BootstrapError("上次安装记录需要核对，请保留当前资料。")
    run = manager.directory / "integrated" / identifier
    raw = _read(run / "manifest.json")
    _match_digest(run / "manifest.json", journal.get("manifest_sha256"))
    manifest = _fixed_manifest(raw, manager.trust, manager.target)
    candidate = manager.directory / "versions" / (str(manifest["build_number"]) + "-" + manifest["source_digest"][:12])
    incoming = journal.get("new_state", {}).get("current", {})
    if journal.get("program_changed"):
        if incoming != _current(manifest, candidate, manager.target):
            raise BootstrapError("上次安装的程序记录与原签名发行不一致。")
        verify_installed_payload(candidate, manifest)
        if manager.target == "macos-arm64":
            verify_mac_bundle(candidate / "LJQCApp.app")
    destination = Path(journal.get("helper_destination", ""))
    if not destination.is_absolute() or destination.is_symlink():
        raise BootstrapError("上次安装的更新入口位置无效。")
    if journal.get("old_state"):
        old_entry = journal["old_state"].get("updater_entrypoint")
        if str(_helper_entry(destination, manager.target)) != old_entry:
            raise BootstrapError("上次安装的更新入口与原记录不一致。")
    elif destination != _destination(manager, {}):
        raise BootstrapError("上次安装的更新入口不是默认程序位置。")
    suffix = ".app" if manager.target == "macos-arm64" else ""
    stage = destination.with_name(".LJQCApp-integrated-stage-" + identifier + suffix)
    backup = destination.with_name(".LJQCApp-before-integrated-" + identifier + suffix)
    if stage.is_symlink() or backup.is_symlink():
        raise BootstrapError("上次安装的临时程序位置无效。")
    return manifest, run, destination, stage, backup


def _finish(manager, journal: dict) -> dict:
    manifest, run, destination, stage, backup = _validate_journal(manager, journal)
    path = manager.directory / JOURNAL_NAME
    state = manager.get_state()
    if state not in (journal["old_state"], journal["new_state"]):
        raise BootstrapError("安装期间程序版本已变化，请保留当前资料。")
    if journal.get("replace_helper"):
        helper_manifest = run / "helper-manifest.json"
        _match_digest(helper_manifest, journal["helper_manifest_sha256"])
        if stage.exists():
            verify_helper_manifest(stage, manager.target, manifest_path=helper_manifest)
            if destination.exists():
                if backup.exists() or _fingerprint(destination, manager.target) != journal.get("old_helper_fingerprint"):
                    raise BootstrapError("原更新入口已变化，已保留现有程序。")
                destination.rename(backup)
            elif journal.get("had_helper") and not backup.exists():
                raise BootstrapError("原更新入口保留副本缺失，请保留安装现场。")
            stage.rename(destination)
        if not destination.exists():
            raise BootstrapError("安装程序入口缺失，请重新运行安装包。")
        verify_helper_manifest(destination, manager.target, manifest_path=helper_manifest)
        if manager.target == "macos-arm64":
            verify_mac_bundle(destination)
        if journal["phase"] == "prepared":
            journal["phase"] = "helper-installed"
            atomic_json(path, journal)
    if journal["phase"] != "committed" and journal.get("program_changed") and not journal["receipt"]["is_new"]:
        original = Path(journal["receipt"]["original_db"])
        receipt = journal["receipt"]
        config = manager.root / "config/storage_config.json"
        if ((digest_file(config) if config.exists() else None) != receipt["config_sha256"]
                or database_path(manager.root, require_database=False) != original):
            raise BootstrapError("资料保存位置已变化，已保留当前资料。")
        actual = logical_digest(original) if original.exists() else None
        expected = journal["expected_data_digest"]
        if actual not in (receipt["logical_digest"], expected, None):
            raise BootstrapError("安装中断后资料已有变化，已保留当前资料。")
        if actual != expected:
            prepared = original.with_name("." + original.name + ".integrated-" + journal["id"])
            previous = original.with_name("." + original.name + ".before-integrated-" + journal["id"])
            if prepared.is_symlink() or previous.is_symlink():
                raise BootstrapError("安装资料临时位置无效。")
            candidate = run / "candidate-data/data/qc_lj_app.db"
            if logical_digest(candidate) != expected:
                raise BootstrapError("安装资料副本已变化，原资料保持。")
            if original.exists():
                assert_current(manager.root, receipt)
                if previous.exists():
                    raise BootstrapError("安装资料保留位置已有文件，请保留现场。")
                shutil.copy2(candidate, prepared)
                shutil.copystat(original, prepared, follow_symlinks=False)
                with prepared.open("r+b") as handle:
                    os.fsync(handle.fileno())
                manager._release_reservation()
                with closing(sqlite3.connect(original.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)) as connection:
                    if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                        raise BootstrapError("资料文件仍被占用，请正常关闭软件后重试。")
                assert_current(manager.root, receipt)
                journal["phase"] = "data-switching"
                atomic_json(path, journal)
                original.rename(previous)
            elif (journal.get("phase") != "data-switching" or not previous.is_file()
                  or not prepared.is_file() or logical_digest(prepared) != expected):
                raise BootstrapError("安装资料切换尚未完成，请保留现场。")
            prepared.rename(original)
        assert_attachments(original, receipt["files"])
    updater_entry = _helper_entry(destination, manager.target)
    manager._entry(journal["new_state"]["current"])
    if not updater_entry.is_file():
        raise BootstrapError("自动更新入口缺失，请重新运行安装包。")
    if not journal["old_state"]:
        atomic_json(manager.directory / "bootstrap.json", {
            "format": "ljqc-update-bootstrap", "format_version": 1, "base_release": BASE_RELEASE,
            "target": manager.target, "root": str(manager.root), "updater_version": UPDATER_VERSION,
            "updater_entrypoint": str(updater_entry),
            "original_program": str(manager._entry(journal["new_state"]["base"])),
            "integrated_base_release": manifest["version"] + "-" + str(manifest["build_number"])})
    atomic_json(manager.state_path, journal["new_state"])
    journal["phase"] = "committed"
    atomic_json(path, journal)
    shortcuts = {}
    if manager.target == "windows-x86_64":
        shortcuts = install_windows_shortcuts(updater_entry, Path(journal["old_program_entry"]), manager.root)
    result = {"status": "installed", "version": journal["new_state"]["current"]["version"],
              "program_build_number": journal["new_state"]["current"]["build_number"],
              "installer_build_number": journal["installer_build_number"], "automatic_updates_enabled": True,
              "original_data_retained": True, "backup": journal.get("backup"), **shortcuts}
    atomic_json(run / "installation-receipt.json", result)
    path.unlink()
    return result


def recover_integrated_install(manager, *, already_locked=False) -> dict | None:
    """Finish an interrupted signed installation without restoring old data."""
    path = manager.directory / JOURNAL_NAME
    if not path.exists() and not path.is_symlink():
        return None
    if not already_locked:
        with manager.maintenance():
            return recover_integrated_install(manager, already_locked=True)
    journal = strict_json(_read(path, maximum=4 * 1024 * 1024), maximum=4 * 1024 * 1024)
    return _finish(manager, journal)


def install_integrated(manager, bundle: Path, *, progress=None) -> dict:
    """Install with standard destinations and the existing data/entry locks."""
    report = progress or (lambda message: None)
    report("正在核对安装包…")
    verified = inspect_bundle(manager, bundle)
    from .bootstrap import recover_bootstrap
    from .helper_repair import recover_helper_repair
    recover_bootstrap(manager)
    recover_helper_repair(manager)
    with manager.maintenance():
        recover_integrated_install(manager, already_locked=True)
        manager._recover()
        old_state = manager.get_state()
        manifest = verified["manifest"]
        destination = _destination(manager, old_state)
        if destination.is_symlink():
            raise BootstrapError("程序安装位置不能是目录链接。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_helper = verified["helper"]
        if source_helper.resolve() == destination.resolve() or source_helper.resolve().is_relative_to(destination.resolve()):
            raise BootstrapError("请从单独下载的安装包运行安装。")
        replace_helper = True
        if old_state:
            identity = read_helper_identity(destination, manager.target)
            if identity["build_number"] > verified["helper_identity"]["build_number"]:
                replace_helper = False
            elif identity["build_number"] == verified["helper_identity"]["build_number"] and identity != verified["helper_identity"]:
                raise BootstrapError("相同构建号的更新组件来源不一致。")
        elif destination.exists():
            if manager.target != "macos-arm64":
                raise BootstrapError("自动更新目录已有未登记程序，请保留现场。")
            from .bootstrap import validate_baseline
            validate_baseline(destination, manager.target)
        identifier = uuid.uuid4().hex
        run = manager.directory / "integrated" / identifier
        run.mkdir(parents=True, mode=0o700)
        (run / "manifest.json").write_bytes(verified["manifest_raw"])
        shutil.copy2(verified["helper_manifest"], run / "helper-manifest.json")
        program_changed = not old_state or old_state["current"]["build_number"] < manifest["build_number"]
        if old_state and old_state["current"]["build_number"] == manifest["build_number"]:
            if any(old_state["current"].get(key) != manifest[key] for key in ("version", "source_digest", "source_commit")):
                raise BootstrapError("相同构建号的程序来源不一致。")
        receipt = None
        new_state = dict(old_state)
        if program_changed:
            report("正在安装程序并保留现有资料…")
            program = manager.directory / "versions" / (str(manifest["build_number"]) + "-" + manifest["source_digest"][:12])
            if program.exists():
                verify_installed_payload(program, manifest)
            else:
                extract_verified_payload(verified["archive"], program, manifest)
            if manager.target == "macos-arm64":
                verify_mac_bundle(program / "LJQCApp.app")
            receipt = snapshot(manager.root, run / "backup")
            candidate_db = manager._preflight(program, manifest, run, run / "backup", receipt)
            assert_current(manager.root, receipt)
            current = _current(manifest, program, manager.target)
            if old_state:
                new_state.update(current=current, previous=old_state["current"],
                                 last_update_at=datetime.now(timezone.utc).isoformat())
            else:
                new_state = {"format_version": 1, "base_release": BASE_RELEASE, "target": manager.target,
                             "updater_version": UPDATER_VERSION, "current": current, "base": current,
                             "highest_sequence": manifest["sequence"], "highest_metadata_digest": manifest["_metadata_digest"],
                             "integrated_base_release": manifest["version"] + "-" + str(manifest["build_number"])}
        else:
            manager._entry(new_state["current"])
        updater_entry = _helper_entry(destination, manager.target)
        new_state["updater_entrypoint"] = str(updater_entry)
        metadata = manager.directory / "metadata" / (manifest["_metadata_digest"] + ".json")
        atomic_json(metadata, strict_json(verified["manifest_raw"]))
        suffix = ".app" if manager.target == "macos-arm64" else ""
        stage = destination.with_name(".LJQCApp-integrated-stage-" + identifier + suffix)
        if replace_helper:
            report("正在启用自动更新…")
            shutil.copytree(source_helper, stage, symlinks=manager.target == "macos-arm64")
            verify_helper_manifest(stage, manager.target, manifest_path=run / "helper-manifest.json")
            if manager.target == "macos-arm64":
                verify_mac_bundle(stage)
        old_entry = (manager._entry(old_state["base"]) if old_state
                     else _previous_windows_entry(manager) if manager.target == "windows-x86_64"
                     else default_base_path(manager.target) / "Contents/MacOS/LJQCApp")
        journal = {"format": "ljqc-integrated-installation", "format_version": 1,
                   "id": identifier, "target": manager.target, "phase": "prepared",
                   "installer_build_number": verified["configuration"]["installer_build_number"],
                   "manifest_sha256": hashlib.sha256(verified["manifest_raw"]).hexdigest(),
                   "old_state": old_state, "new_state": new_state, "program_changed": program_changed,
                   "helper_destination": str(destination), "replace_helper": replace_helper,
                   "helper_manifest_sha256": digest_file(run / "helper-manifest.json"),
                   "had_helper": destination.exists(),
                   "old_helper_fingerprint": _fingerprint(destination, manager.target) if replace_helper and destination.exists() else None,
                   "old_program_entry": str(old_entry), "receipt": receipt,
                   "backup": str(run / "backup") if receipt else None,
                   "expected_data_digest": logical_digest(candidate_db) if program_changed else None}
        atomic_json(manager.directory / JOURNAL_NAME, journal)
        return _finish(manager, journal)


def _previous_windows_entry(manager) -> Path:
    """Follow only the two existing LJQCApp shortcuts, never search disks."""
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$shell = New-Object -ComObject WScript.Shell
$files = @((Join-Path $shell.SpecialFolders.Item('Programs') 'LJQCApp.lnk'),
           (Join-Path $shell.SpecialFolders.Item('Desktop') 'LJQCApp.lnk'))
$targets = @()
foreach ($file in $files) {
  if (Test-Path -LiteralPath $file) {
    $target = $shell.CreateShortcut($file).TargetPath
    if ($target) { $targets += $target }
  }
}
ConvertTo-Json -Compress -InputObject @($targets | Select-Object -Unique)
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            text=True, encoding="utf-8", capture_output=True, timeout=45, check=False)
    if result.returncode:
        raise BootstrapError("无法读取现有软件入口，请正常关闭软件后重新安装。")
    try:
        targets = json.loads(result.stdout.lstrip("\ufeff"))
    except ValueError as error:
        raise BootstrapError("现有软件入口无法核对。") from error
    if not isinstance(targets, list) or len(targets) > 1:
        raise BootstrapError("存在多个不同的软件入口，请保留现有安装。")
    if not targets:
        return default_base_path(manager.target) / "LJQCApp.exe"
    previous = Path(targets[0])
    if not previous.is_absolute() or previous.name.lower() != "ljqcapp.exe":
        raise BootstrapError("现有软件入口指向其他程序，已保留原入口。")
    from .bootstrap import validate_baseline
    validate_baseline(previous.parent, manager.target)
    return previous
