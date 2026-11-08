"""Narrow, recoverable repair of the public build02 updater's missing CA.

Only an independent, strictly newer inventoried helper may replace a registered
helper of the same updater protocol. Build02 is the exact legacy exception.
The original bootstrap receipt and the complete application state are retained
byte-for-byte; this never re-registers the base, resets versions, or moves data.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid

from .bootstrap import (BASE_RELEASE, UPDATER_VERSION, BootstrapError,
                        _atomic_json, resource_path, verify_mac_bundle)

HELPER_MANIFEST_NAME = "update_helper_manifest.json"
OLD_HELPER_BUILD = 2026110702
MIN_INVENTORIED_BUILD = 2026110801
REPAIR_HELPER_BUILD = MIN_INVENTORIED_BUILD  # First certificate-repair release.
OLD_HELPER_COMMIT = "2adb7721060582308164a4b10a13d8d3c66dd14f"
OLD_HELPER_DIGEST = "9b8100574296fa4d1083d9b91013d34276d57f65c8a7f59bba1e3596dcbabc72"
_SHA = re.compile(r"[0-9a-f]{64}")
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRERELEASE = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(_NUMBER + r"\." + _NUMBER + r"\." + _NUMBER +
    r"(?:-" + _PRERELEASE + r"(?:\." + _PRERELEASE + r")*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


class MissingHelperManifest(BootstrapError):
    """A separately mounted Mac source may be selected; tampering is distinct."""

    def __init__(self, package: Path, manifest: Path):
        self.package = Path(package)
        self.manifest_path = Path(manifest)
        super().__init__("请在刚打开的更新接入磁盘中选择 LJQCAppUpdater 应用。")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _read(path: Path, maximum=10 * 1024 * 1024) -> dict:
    if path.is_symlink():
        raise BootstrapError("更新组件修复记录不能是符号链接。")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise BootstrapError("更新组件修复记录超过允许大小。")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise BootstrapError("更新组件修复记录无法识别。")
    return value


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _save(path: Path, value: dict) -> None:
    _atomic_json(path, value)
    _sync_directory(path.parent)


def _sync_stage(package: Path) -> None:
    """Make copied bytes durable before the registered helper is renamed."""
    directories = [package]
    for path in package.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            # Windows FlushFileBuffers needs a writable handle. A read-only
            # source copy safely aborts before touching the installed helper;
            # permissions are never broadened to make the repair proceed.
            with path.open("r+b" if os.name == "nt" else "rb") as handle:
                os.fsync(handle.fileno())
    for directory in reversed(directories):
        _sync_directory(directory)


def _resource(package: Path, target: str, filename: str) -> Path:
    roots = ([package / "_internal", package] if target == "windows-x86_64"
             else [package / "Contents/Resources"])
    for root in roots:
        candidate = root / filename
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise BootstrapError("更新接入补丁缺少完整发行资料，请重新下载。")


def read_helper_identity(package: Path, target: str) -> dict:
    """Read public build provenance, not the user's application/database state."""
    package = Path(package)
    if target not in ("windows-x86_64", "macos-arm64") or package.is_symlink() or not package.is_dir():
        raise BootstrapError("更新接入组件目录无效。")
    release = _read(_resource(package, target, "build_info.json"), maximum=65536)
    commit = release.get("git_commit", release.get("source_commit"))
    version = release.get("version")
    if (not isinstance(version, str) or len(version) > 64 or _VERSION.fullmatch(version) is None
            or release.get("target") != target
            or type(release.get("build_number")) is not int
            or not 0 < release["build_number"] <= 2**63 - 1
            or type(release.get("data_format_version")) is not int
            or release["data_format_version"] != 1 or release.get("source_dirty") is not False
            or not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or not isinstance(release.get("source_digest"), str)
            or _SHA.fullmatch(release["source_digest"]) is None):
        raise BootstrapError("更新接入组件的发行来源无法核对，当前组件未更改。")
    if release["build_number"] == OLD_HELPER_BUILD:
        if (version != "1.0.1" or commit != OLD_HELPER_COMMIT
                or release["source_digest"] != OLD_HELPER_DIGEST):
            raise BootstrapError("旧更新组件与已公开的 02 发行不一致。")
    elif release["build_number"] < MIN_INVENTORIED_BUILD:
        raise BootstrapError("此接入补丁不属于受支持的完整清单发行。")
    trust = _resource(package, target, "resources/desktop_update_trust.json")
    expected_trust = resource_path("resources/desktop_update_trust.json")
    if trust.read_bytes() != expected_trust.read_bytes():
        raise BootstrapError("更新接入组件的发行公钥或服务器设置不一致。")
    return {"version": release["version"], "build_number": release["build_number"],
            "target": target, "source_commit": commit, "source_digest": release["source_digest"],
            "data_format_version": 1, "source_dirty": False,
            "base_release": BASE_RELEASE, "updater_version": UPDATER_VERSION,
            "trust_sha256": _digest(trust)}


def _inventory(package: Path, target: str) -> list[dict]:
    root = package.resolve()
    records = []
    for path in sorted(package.rglob("*")):
        name = path.relative_to(package).as_posix()
        if target == "windows-x86_64" and name == HELPER_MANIFEST_NAME:
            continue
        if path.suffix.lower() in (".db", ".sqlite", ".sqlite3") or path.name in (
                "storage_config.json", "receiver-signing-key.pem"):
            raise BootstrapError("更新组件中不能包含业务资料或私密配置。")
        if path.is_symlink():
            link = path.readlink()
            if link.is_absolute() or not path.resolve(strict=True).is_relative_to(root):
                raise BootstrapError("更新组件包含越出程序目录的链接。")
            records.append({"path": name, "kind": "symlink", "target": link.as_posix()})
        elif path.is_dir():
            continue
        elif path.is_file():
            records.append({"path": name, "kind": "file", "size_bytes": path.stat().st_size,
                            "sha256": _digest(path), "mode": stat.S_IMODE(path.stat().st_mode)})
        else:
            raise BootstrapError("更新组件包含无法识别的文件类型。")
    return records


def _manifest_path(package: Path, target: str) -> Path:
    return (package / HELPER_MANIFEST_NAME if target == "windows-x86_64"
            else package.parent / HELPER_MANIFEST_NAME)


def helper_manifest_path(package: Path, target: str) -> Path:
    """Return the explicit expected inventory location without scanning disks."""
    return _manifest_path(Path(package), target)


def write_helper_manifest(package: Path, target: str, output: Path | None = None) -> dict:
    """Build-only inventory after final signing; Mac inventory lives outside .app."""
    package = Path(package).resolve()
    release = read_helper_identity(package, target)
    if release["build_number"] < MIN_INVENTORIED_BUILD:
        raise BootstrapError("完整校验清单只用于受支持的现代更新组件。")
    destination = Path(output) if output is not None else _manifest_path(package, target)
    if destination.exists() or destination.is_symlink():
        raise BootstrapError("更新组件完整校验清单已存在，不能覆盖。")
    if target == "macos-arm64" and destination.resolve().is_relative_to(package):
        raise BootstrapError("Mac 完整校验清单必须放在已签名应用之外。")
    value = {"format": "ljqc-update-helper-payload", "format_version": 1,
             "release": release, "files": _inventory(package, target), "mutable_data_included": False}
    _save(destination, value)
    return value


def verify_helper_manifest(package: Path, target: str, *, manifest_path: Path | None = None) -> dict:
    package = Path(package)
    path = Path(manifest_path) if manifest_path is not None else _manifest_path(package, target)
    if not path.exists() and not path.is_symlink():
        raise MissingHelperManifest(package, path)
    manifest = _read(path)
    if (manifest.get("format") != "ljqc-update-helper-payload" or manifest.get("format_version") != 1
            or manifest.get("mutable_data_included") is not False
            or manifest.get("release") != read_helper_identity(package, target)
            or not isinstance(manifest.get("files"), list) or not manifest["files"]
            or manifest["files"] != _inventory(package, target)):
        raise BootstrapError("更新接入补丁的完整文件校验未通过，当前组件未更改。")
    return manifest


def _registration(manager) -> tuple[dict, dict, Path]:
    state = manager.get_state()
    receipt = _read(Path(manager.root) / "updates/bootstrap.json")
    if (not isinstance(state.get("current"), dict) or not isinstance(state.get("base"), dict)
            or state.get("base_release") != BASE_RELEASE or state.get("target") != manager.target
            or receipt.get("base_release") != BASE_RELEASE or receipt.get("target") != manager.target
            or receipt.get("format") != "ljqc-update-bootstrap" or receipt.get("updater_version") != 1):
        raise BootstrapError("已有更新接入记录需要核对，未重新初始化原程序或资料。")
    entry = state.get("updater_entrypoint")
    if not isinstance(entry, str) or receipt.get("updater_entrypoint") != entry:
        raise BootstrapError("已有更新启动入口不一致，未更改原程序。")
    executable = Path(entry)
    if not executable.is_absolute() or executable.is_symlink() or not executable.is_file():
        raise BootstrapError("已有更新组件启动入口缺失，未更改原资料。")
    if manager.target == "windows-x86_64":
        package = executable.parent
        if executable.name != "LJQCAppUpdater.exe" or package.name != "LJQCAppUpdater":
            raise BootstrapError("已有更新组件位置无法核对。")
    else:
        package = executable.parents[2]
        if package.suffix != ".app" or executable.relative_to(package).as_posix() != "Contents/MacOS/LJQCAppUpdater":
            raise BootstrapError("已有 Mac 更新组件位置无法核对。")
    if package.is_symlink():
        raise BootstrapError("已有更新组件不能是目录链接。")
    return state, receipt, package.resolve()


def needs_helper_repair(manager, helper: Path) -> bool:
    """Read-only detection for a separately opened replacement DMG or Setup."""
    state, receipt, installed = _registration(manager)
    source = Path(helper)
    if source.is_symlink():
        raise BootstrapError("请使用独立下载的完整更新接入补丁。")
    source = source.resolve()
    if source == installed:
        return False
    new, old = read_helper_identity(source, manager.target), read_helper_identity(installed, manager.target)
    if new["build_number"] < old["build_number"]:
        raise BootstrapError("此接入补丁较旧，已保留当前更新组件。")
    if new["build_number"] == old["build_number"]:
        if new != old:
            raise BootstrapError("相同构建号的更新组件来源不一致。")
        return False
    if (new["build_number"] < MIN_INVENTORIED_BUILD
            or source.is_relative_to(installed) or installed.is_relative_to(source)):
        raise BootstrapError("请选择独立下载且构建号更高的完整更新组件。")
    try:
        verify_helper_manifest(source, manager.target)
    except MissingHelperManifest:
        # Metadata is sufficient to show the explicit source chooser. The
        # actual repair still requires a complete verified source before any
        # move, including when invoked without a GUI.
        return True
    return True


def _fingerprint(package: Path, target: str) -> str:
    value = json.dumps(_inventory(package, target), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _guard_unchanged(manager, journal: dict) -> None:
    root = Path(manager.root) / "updates"
    if (_digest(root / "state.json") != journal["state_sha256"]
            or _digest(root / "bootstrap.json") != journal["bootstrap_sha256"]):
        raise BootstrapError("修复期间的更新登记发生变化，已保留现场及原资料。")


def _journal_paths(manager, journal: dict) -> tuple[Path, Path, Path]:
    identifier = journal.get("id", "")
    if (journal.get("format") != "ljqc-helper-repair" or journal.get("format_version") != 1
            or journal.get("target") != manager.target or journal.get("base_release") != BASE_RELEASE
            or not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None
            or journal.get("phase") not in ("prepared", "old-retained", "new-installed", "complete", "rolled-back")):
        raise BootstrapError("更新组件修复恢复记录无法识别。")
    state = manager.get_state()
    entry = Path(state.get("updater_entrypoint", ""))
    installed = entry.parent if manager.target == "windows-x86_64" else entry.parents[2]
    suffix = ".app" if manager.target == "macos-arm64" else ""
    stage = installed.with_name(".LJQCApp-helper-stage-" + identifier + suffix)
    backup = installed.with_name(".LJQCApp-helper-backup-" + identifier + suffix)
    if (not installed.is_absolute() or journal.get("installed") != str(installed)
            or journal.get("stage") != str(stage) or journal.get("backup") != str(backup)
            or any(path.is_symlink() for path in (installed, stage, backup))):
        raise BootstrapError("更新组件修复路径无法核对，未移动任何程序。")
    return installed, stage, backup


def _rollback(manager, journal: dict) -> None:
    installed, stage, backup = _journal_paths(manager, journal)
    _guard_unchanged(manager, journal)
    if backup.exists():
        if _fingerprint(backup, manager.target) != journal["old_helper_fingerprint"]:
            raise BootstrapError("原更新组件备份发生变化，请保留现场并联系维护人员。")
        if installed.exists():
            failed = installed.with_name(".LJQCApp-helper-failed-" + uuid.uuid4().hex +
                                         (".app" if manager.target == "macos-arm64" else ""))
            installed.rename(failed)
        backup.rename(installed)
        _sync_directory(installed.parent)
    elif not installed.exists() or _fingerprint(installed, manager.target) != journal["old_helper_fingerprint"]:
        raise BootstrapError("原更新组件尚待人工恢复，未改动原始资料。")
    if stage.exists():
        shutil.rmtree(stage)
    journal.update(phase="rolled-back", status="failed-current-helper-retained")
    _save(Path(manager.root) / "updates/helper-repair.json", journal)


def recover_helper_repair(manager, *, already_locked=False) -> dict | None:
    path = Path(manager.root) / "updates/helper-repair.json"
    if not path.exists():
        return None
    journal = _read(path)
    _journal_paths(manager, journal)
    if journal["phase"] in ("complete", "rolled-back"):
        return journal
    with nullcontext() if already_locked else manager.maintenance():
        journal = _read(path)
        _rollback(manager, journal)
    return journal


def repair_registered_helper(manager, helper: Path, *, already_locked=False,
                             signature_checker=verify_mac_bundle) -> dict:
    """Repair the stable helper in place; retain all application state and data."""
    with nullcontext() if already_locked else manager.maintenance():
        recover_helper_repair(manager, already_locked=True)
        state, receipt, installed = _registration(manager)
        if not needs_helper_repair(manager, helper):
            return receipt
        source = Path(helper).resolve()
        manifest = verify_helper_manifest(source, manager.target)
        new = manifest["release"]
        if manager.target == "macos-arm64":
            signature_checker(installed)
            signature_checker(source)
        identifier = uuid.uuid4().hex
        suffix = ".app" if manager.target == "macos-arm64" else ""
        stage = installed.with_name(".LJQCApp-helper-stage-" + identifier + suffix)
        backup = installed.with_name(".LJQCApp-helper-backup-" + identifier + suffix)
        updates = Path(manager.root) / "updates"
        saved_manifest = updates / ("helper-repair-" + identifier + "-manifest.json")
        _save(saved_manifest, manifest)
        journal = {"format": "ljqc-helper-repair", "format_version": 1, "id": identifier,
                   "base_release": BASE_RELEASE, "target": manager.target, "phase": "prepared",
                   "installed": str(installed), "stage": str(stage), "backup": str(backup),
                   "old_helper_fingerprint": _fingerprint(installed, manager.target),
                   "old_helper": read_helper_identity(installed, manager.target), "new_helper": new,
                   "state_sha256": _digest(updates / "state.json"),
                   "bootstrap_sha256": _digest(updates / "bootstrap.json"),
                   "manifest": str(saved_manifest), "manifest_sha256": _digest(saved_manifest)}
        path = updates / "helper-repair.json"
        _save(path, journal)
        committed = False
        try:
            shutil.copytree(source, stage, symlinks=True)
            verify_helper_manifest(stage, manager.target, manifest_path=saved_manifest)
            if manager.target == "macos-arm64":
                signature_checker(stage)
            _sync_stage(stage)
            _guard_unchanged(manager, journal)
            installed.rename(backup)
            _sync_directory(installed.parent)
            journal["phase"] = "old-retained"
            _save(path, journal)
            stage.rename(installed)
            _sync_directory(installed.parent)
            journal["phase"] = "new-installed"
            _save(path, journal)
            verify_helper_manifest(installed, manager.target, manifest_path=saved_manifest)
            if manager.target == "macos-arm64":
                signature_checker(installed)
            _guard_unchanged(manager, journal)
            journal.update(phase="complete", status="passed", state_unchanged=True,
                           bootstrap_receipt_unchanged=True, original_base_untouched=True)
            _save(path, journal)
            committed = True
            return receipt
        except BaseException:
            if not committed:
                _rollback(manager, journal)
            raise
