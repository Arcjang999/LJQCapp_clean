"""One-time, recoverable adoption of the immutable desktop 1.00 release.

The bootstrap changes program entry points only. It never imports database.py,
initializes a database, rewrites a storage choice, or edits a sealed app bundle.
"""
from __future__ import annotations

from contextlib import nullcontext
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import uuid

BASE_RELEASE = "1.0.0-2026110606"
BASE_COMMIT = "d07a57771475a9367e0face03d14f7629b97fe04"
BASE_DIGEST = "0a259ec3cae769bd959c9f970d2ae56de09969dd1eda3b03ca5a56c8fa301866"
UPDATER_VERSION = 1
BASE_MANIFEST_SHA256 = {
    "windows-x86_64": "ca241123bbbc9facc5bb0787f0208a0d5cc950c43f769d4678c059cbd04c3531",
    "macos-arm64": "309859f1803f2d783c538712b9199dff69f1427c8b659a7d4f18839c10c72fc0",
}


class BootstrapError(RuntimeError):
    """A recoverable adoption failure suitable for the update dialog."""


def resource_path(relative: str) -> Path:
    candidates = [Path(__file__).resolve().parents[1]]
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        candidates = [Path(getattr(sys, "_MEIPASS", executable.parent)),
                      executable.parent / "_internal", executable.parent,
                      executable.parent.parent / "Resources", *candidates]
    for root in candidates:
        path = root / relative
        if path.is_file():
            return path
    raise BootstrapError("更新组件文件不完整，请重新下载更新接入补丁。")


def native_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    if sys.platform == "darwin" and machine == "arm64":
        return "macos-arm64"
    raise BootstrapError("请选择适合当前电脑的 Windows 64 位或 Apple 芯片 Mac 更新组件。")


def default_data_root(target: str) -> Path:
    from services.storage_paths import get_storage_paths
    system = {"windows-x86_64": "win32", "macos-arm64": "darwin"}[target]
    return get_storage_paths(frozen=True, platform=system).root


def default_base_path(target: str) -> Path:
    if target == "windows-x86_64":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise BootstrapError("无法确定用户程序目录，请选择已安装的 LJQCApp。")
        return Path(local) / "Programs/LJQCApp/versions" / (BASE_RELEASE + "-" + BASE_DIGEST[:12])
    return Path("/Applications/LJQCApp.app")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_baseline_manifest(target: str) -> dict:
    if target not in BASE_MANIFEST_SHA256:
        raise BootstrapError("更新组件的平台无法识别。")
    path = resource_path("resources/desktop_baselines/" + target + ".json.gz")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BASE_MANIFEST_SHA256[target]:
        raise BootstrapError("基础版本校验资料已损坏，请重新下载更新接入补丁。")
    try:
        manifest = json.loads(gzip.decompress(raw))
    except (ValueError, OSError) as error:
        raise BootstrapError("基础版本校验资料无法读取。") from error
    release = manifest.get("release", {})
    if (manifest.get("format") != "ljqc-program-payload" or manifest.get("format_version") != 1
            or release.get("version") != "1.0.0" or release.get("build_number") != 2026110606
            or release.get("source_digest") != BASE_DIGEST or release.get("git_commit") != BASE_COMMIT
            or release.get("target") != target or release.get("source_dirty") is not False
            or manifest.get("mutable_data_included") is not False):
        raise BootstrapError("基础版本校验资料与 1.00 发行不一致。")
    return manifest


def validate_baseline(path: Path, target: str, *, manifest: dict | None = None,
                      verify_platform_signature: bool = True) -> dict:
    """Hash every known baseline file using an embedded, pinned receipt.

    ``manifest`` is an internal fixture injection, never a user CLI option.
    macOS receipts describe the disk-image root; only its app is installed.
    """
    path = Path(path).expanduser()
    if path.is_symlink():
        raise BootstrapError("请选择实际安装的程序，不能选择程序替身或符号链接。")
    if target == "windows-x86_64" and path.name.lower() == "ljqcapp.exe":
        path = path.parent
    path = path.resolve()
    if not path.is_dir():
        raise BootstrapError("未找到所选程序，请先安装 1.00 基础包。")
    if target == "macos-arm64" and path.suffix != ".app":
        raise BootstrapError("请选择已经安装的 LJQCApp.app。")
    manifest = manifest if manifest is not None else read_baseline_manifest(target)
    release = manifest.get("release", {})
    if any(release.get(key) != value for key, value in {
            "version": "1.0.0", "build_number": 2026110606,
            "git_commit": BASE_COMMIT, "source_digest": BASE_DIGEST,
            "target": target, "source_dirty": False}.items()):
        raise BootstrapError("所选程序不是当前支持的 1.00 基础版本。")
    expected = set()
    for record in manifest.get("files", []):
        name = record.get("path", "")
        if target == "macos-arm64":
            if not name.startswith("LJQCApp.app/"):
                continue
            name = name[len("LJQCApp.app/"):]
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or ".." in relative.parts or "\\" in name
                or any(":" in part for part in relative.parts)):
            raise BootstrapError("基础版本校验资料包含无效路径。")
        local = path.joinpath(*relative.parts)
        expected.add(relative.as_posix())
        try:
            if not local.resolve(strict=True).is_relative_to(path):
                raise BootstrapError("程序含有越出安装目录的链接，请重新安装原始基础包。")
            if record.get("kind") == "symlink":
                if not local.is_symlink() or local.readlink().as_posix() != record.get("target"):
                    raise BootstrapError("程序链接不完整，请重新安装原始基础包。")
            elif not local.is_file() or local.is_symlink():
                raise BootstrapError("程序文件不完整，请重新安装原始基础包。")
            if "sha256" in record:
                if local.stat().st_size != record["size_bytes"] or _digest(local) != record["sha256"]:
                    raise BootstrapError("程序文件与 1.00 基础包不一致，请重新安装原始基础包。")
        except (OSError, RuntimeError) as error:
            if isinstance(error, BootstrapError):
                raise
            raise BootstrapError("程序文件无法核对，请关闭原程序并检查文件权限。") from error
    if not expected:
        raise BootstrapError("基础版本校验资料未列出程序文件。")
    actual = {item.relative_to(path).as_posix() for item in path.rglob("*")
              if item.is_file() or item.is_symlink()}
    # The Windows installer includes its own receipt. Finder may add .DS_Store;
    # neither is executable content nor a replacement for a pinned receipt.
    ignored = {name for name in actual if PurePosixPath(name).name == ".DS_Store"}
    if target == "windows-x86_64":
        ignored.add("release_payload_manifest.json")
    if actual - expected - ignored:
        raise BootstrapError("程序目录有未识别文件，请重新安装原始 1.00 基础包后接入更新。")
    entrypoint = ("LJQCApp.exe" if target == "windows-x86_64" else "Contents/MacOS/LJQCApp")
    if not (path / entrypoint).is_file():
        raise BootstrapError("程序启动入口缺失，请重新安装原始基础包。")
    if target == "macos-arm64" and verify_platform_signature:
        verify_mac_bundle(path)
    return {"path": str(path), "entrypoint": entrypoint, **release}


def verify_mac_bundle(path: Path) -> None:
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)],
                            capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise BootstrapError("Mac 应用校验未通过，请保留现有程序并重新下载原始文件。")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def helper_source() -> Path:
    if not getattr(sys, "frozen", False):
        raise BootstrapError("请使用对应系统的更新接入补丁进行安装。")
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
        raise BootstrapError("更新组件应用目录不完整。")
    return executable.parent


def install_windows_shortcuts(helper: Path, old_entry: Path, root: Path) -> dict:
    """Use COM paths rather than guessing localized or OneDrive Desktop paths."""
    payload = {"helper": str(helper), "old_entry": str(old_entry),
               "arguments": subprocess.list2cmdline(["--data-root", str(root)])}
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$p = ConvertFrom-Json ([Console]::In.ReadToEnd())
$shell = New-Object -ComObject WScript.Shell
$locations = @((Join-Path $shell.SpecialFolders.Item('Programs') 'LJQCApp.lnk'))
$desktop = Join-Path $shell.SpecialFolders.Item('Desktop') 'LJQCApp.lnk'
if (Test-Path -LiteralPath $desktop) { $locations += $desktop }
$changed = @()
foreach ($file in $locations) {
  $link = $shell.CreateShortcut($file)
  if ((Test-Path -LiteralPath $file) -and $link.TargetPath -and
      ($link.TargetPath -ine $p.old_entry) -and ($link.TargetPath -ine $p.helper)) {
    throw 'LJQCApp shortcut points to another installation.'
  }
  $link.TargetPath = $p.helper
  $link.Arguments = $p.arguments
  $link.WorkingDirectory = Split-Path -Parent $p.helper
  $link.Description = 'LJQCApp'
  $link.Save()
  if ($shell.CreateShortcut($file).TargetPath -ine $p.helper) { throw 'Shortcut verification failed.' }
  $changed += $file
}
ConvertTo-Json -Compress -InputObject @{ shortcuts = $changed }
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            input=json.dumps(payload, ensure_ascii=False), text=True, encoding="utf-8",
                            capture_output=True, timeout=45, check=False)
    if result.returncode:
        raise BootstrapError("启动入口未能接续，请保留现有程序并重新运行更新接入补丁。")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except ValueError as error:
        raise BootstrapError("启动入口校验失败，请重新运行更新接入补丁。") from error


def install_bootstrap(manager, base: Path, helper: Path, *, windows_destination: Path | None = None,
                      validate=validate_baseline, shortcut_writer=install_windows_shortcuts,
                      signature_checker=verify_mac_bundle) -> dict:
    """Adopt a selected baseline with a persistent, narrowly scoped journal.

    All paths/side effects can be injected for native isolated acceptance. On
    macOS the old app is renamed beside itself so the swap is same-filesystem.
    The baseline remains byte-identical, including its sealed signature.
    """
    target = manager.target
    root = Path(manager.root).resolve()
    updates = root / "updates"
    helper = Path(helper).resolve()
    base = Path(base).expanduser().absolute()
    if base.is_symlink() or helper.is_symlink() or not helper.is_dir():
        raise BootstrapError("请选择完整的原程序及更新组件文件夹。")
    journal_path = updates / "bootstrap-journal.json"
    receipt_path = updates / "bootstrap.json"
    with manager.maintenance():
        if receipt_path.exists():
            from .helper_repair import repair_registered_helper
            return repair_registered_helper(manager, helper, already_locked=True,
                                            signature_checker=signature_checker)
        if journal_path.exists():
            recover_bootstrap(manager, already_locked=True)
            if receipt_path.exists():
                from .helper_repair import repair_registered_helper
                return repair_registered_helper(manager, helper, already_locked=True,
                                                signature_checker=signature_checker)
        # Legacy build02 did not carry a helper manifest; its original public
        # artifact is separately pinned by native adoption acceptance. New
        # components must have their post-signing complete inventory.
        from .helper_repair import read_helper_identity, verify_helper_manifest, MIN_INVENTORIED_BUILD
        helper_identity = read_helper_identity(helper, target)
        if helper_identity["build_number"] >= MIN_INVENTORIED_BUILD:
            verify_helper_manifest(helper, target)
        identity = validate(base, target)
        base = Path(identity["path"])
        if helper == base or helper.is_relative_to(base) or base.is_relative_to(helper):
            raise BootstrapError("请从单独下载的更新接入补丁运行，不能选择原程序作为更新组件。")
        journal = {"format": "ljqc-bootstrap-journal", "format_version": 1,
                   "target": target, "base": str(base), "phase": "prepared",
                   "updater_version": UPDATER_VERSION}
        temporary = None
        try:
            if target == "macos-arm64":
                signature_checker(helper)
                temporary = base.with_name(".LJQCApp-update-stage-" + uuid.uuid4().hex + ".app")
                backup = base.with_name(".LJQCApp-base-2026110606-" + uuid.uuid4().hex + ".app")
                shutil.copytree(helper, temporary, symlinks=True)
                signature_checker(temporary)
                journal.update(stage=str(temporary), backup=str(backup), installed=str(base))
                _atomic_json(journal_path, journal)
                base.rename(backup)
                journal["phase"] = "base-retained"
                _atomic_json(journal_path, journal)
                temporary.rename(base)
                journal["phase"] = "entry-installed"
                _atomic_json(journal_path, journal)
                current_root = backup.parent
                current_entry = backup.name + "/Contents/MacOS/LJQCApp"
                updater_entry = base / "Contents/MacOS/LJQCAppUpdater"
            elif target == "windows-x86_64":
                local = os.environ.get("LOCALAPPDATA", "")
                destination = (Path(windows_destination) if windows_destination is not None
                               else Path(local) / "Programs/LJQCAppUpdater")
                if not local and windows_destination is None:
                    raise BootstrapError("无法确定用户程序目录，未改动原程序。")
                destination = destination.absolute()
                if destination.exists():
                    raise BootstrapError("更新组件目录已经存在，请核对上次接入记录。")
                if destination == helper or destination.is_relative_to(helper) or helper.is_relative_to(destination):
                    raise BootstrapError("请从单独的更新接入补丁启动接入，不能覆盖正在运行的更新组件。")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(".LJQCApp-update-stage-" + uuid.uuid4().hex)
                shutil.copytree(helper, temporary, symlinks=False)
                updater_entry = destination / "LJQCAppUpdater.exe"
                if not (temporary / "LJQCAppUpdater.exe").is_file():
                    raise BootstrapError("更新组件启动文件缺失。")
                journal.update(stage=str(temporary), installed=str(destination))
                _atomic_json(journal_path, journal)
                temporary.rename(destination)
                journal["phase"] = "entry-installed"
                _atomic_json(journal_path, journal)
                # Register state first: interrupted shortcut installation can
                # safely resume without a shortcut ever pointing at no state.
                current_root, current_entry = base, "LJQCApp.exe"
            else:
                raise BootstrapError("更新组件的平台无法识别。")
            if not updater_entry.is_file():
                raise BootstrapError("更新组件启动文件缺失，原程序将保留。")
            journal.update(phase="registration-started", current_root=str(current_root),
                           current_entrypoint=current_entry, updater_entrypoint=str(updater_entry))
            _atomic_json(journal_path, journal)
            manager.register_base(current_root, current_entry, target=target,
                                  source_digest=BASE_DIGEST, updater_entrypoint=str(updater_entry),
                                  already_locked=True)
            journal["phase"] = "registered"
            _atomic_json(journal_path, journal)
            shortcut_result = (shortcut_writer(updater_entry, base / "LJQCApp.exe", root)
                               if target == "windows-x86_64" else {})
            receipt = {"format": "ljqc-update-bootstrap", "format_version": 1,
                       "base_release": BASE_RELEASE, "target": target, "root": str(root),
                       "updater_version": UPDATER_VERSION, "updater_entrypoint": str(updater_entry),
                       "original_program": str(current_root / current_entry), **shortcut_result}
            _atomic_json(receipt_path, receipt)
            journal["phase"] = "complete"
            _atomic_json(journal_path, journal)
            return receipt
        except BaseException:
            # Leave a registered entry intact for retry; it points at the
            # byte-identical original program. Earlier Mac stages are undone.
            state = manager.get_state().get("current", {})
            registered = (bool(journal.get("current_root")) and journal.get("current_root") == state.get("path")
                          and journal.get("current_entrypoint") == state.get("entrypoint"))
            if journal.get("phase") == "registered" or registered:
                journal["phase"] = "registered"
                _atomic_json(journal_path, journal)
            else:
                _restore_unregistered(journal)
                journal["phase"] = "rolled-back"
                _atomic_json(journal_path, journal)
            raise
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary)


def _restore_unregistered(journal: dict) -> None:
    if journal.get("target") == "windows-x86_64":
        installed = Path(journal["installed"]) if journal.get("installed") else None
        if installed is not None and installed.exists():
            installed.rename(installed.with_name(".LJQCApp-update-failed-" + uuid.uuid4().hex))
        return
    if journal.get("target") != "macos-arm64" or not journal.get("backup"):
        return
    original, backup = Path(journal["base"]), Path(journal["backup"])
    if backup.exists():
        if original.exists():
            failed = original.with_name(".LJQCApp-update-failed-" + uuid.uuid4().hex + ".app")
            original.rename(failed)
        backup.rename(original)


def _validate_journal(journal: dict, target: str) -> None:
    if (journal.get("format") != "ljqc-bootstrap-journal" or journal.get("format_version") != 1
            or journal.get("target") != target or journal.get("phase") not in
            {"prepared", "base-retained", "entry-installed", "registration-started", "registered", "complete", "rolled-back"}):
        raise BootstrapError("更新接入恢复记录无法识别，原程序未改动。")
    base = Path(journal.get("base", ""))
    if not base.is_absolute() or base == base.parent:
        raise BootstrapError("更新接入恢复路径无效，原程序未改动。")
    installed = Path(journal.get("installed", str(base)))
    if not installed.is_absolute():
        raise BootstrapError("更新接入恢复路径无效，原程序未改动。")
    if target == "macos-arm64":
        if base.suffix != ".app" or installed != base:
            raise BootstrapError("更新接入恢复路径无效，原程序未改动。")
        if journal.get("backup"):
            backup = Path(journal["backup"])
            if backup.parent != base.parent or not re.fullmatch(r"\.LJQCApp-base-2026110606-[0-9a-f]{32}\.app", backup.name):
                raise BootstrapError("更新接入恢复路径无效，原程序未改动。")
    elif installed.name != "LJQCAppUpdater":
        raise BootstrapError("更新接入恢复路径无效，原程序未改动。")
    if journal.get("stage"):
        stage = Path(journal["stage"])
        suffix = r"\.app" if target == "macos-arm64" else ""
        if stage.parent != installed.parent or not re.fullmatch(r"\.LJQCApp-update-stage-[0-9a-f]{32}" + suffix, stage.name):
            raise BootstrapError("更新接入恢复路径无效，原程序未改动。")


def recover_bootstrap(manager, *, already_locked: bool = False,
                      shortcut_writer=install_windows_shortcuts) -> dict | None:
    """Recover only paths named in this user's bootstrap journal.

    Registered adoption is resumed, rather than rolling state behind a working
    shortcut back to another entry. No database or business file is touched.
    """
    updates = Path(manager.root) / "updates"
    path = updates / "bootstrap-journal.json"
    # Routine startup and manual checks must not acquire program maintenance
    # locks once adoption has completed; a manual check may run beside the app.
    if not path.exists():
        return None
    initial = json.loads(path.read_text(encoding="utf-8"))
    _validate_journal(initial, manager.target)
    if initial.get("phase") in ("complete", "rolled-back"):
        return initial
    with nullcontext() if already_locked else manager.maintenance():
        if not path.exists():
            return None
        journal = json.loads(path.read_text(encoding="utf-8"))
        _validate_journal(journal, manager.target)
        if journal.get("phase") == "complete":
            return journal
        state = manager.get_state().get("current", {})
        registered = (bool(journal.get("current_root")) and journal.get("current_root") == state.get("path")
                      and journal.get("current_entrypoint") == state.get("entrypoint"))
        if journal.get("phase") == "registered" or registered:
            installed = Path(journal["installed"])
            updater = (installed / "LJQCAppUpdater.exe" if manager.target == "windows-x86_64"
                       else installed / "Contents/MacOS/LJQCAppUpdater")
            if not updater.is_file() or not manager.get_state().get("current"):
                raise BootstrapError("更新接入需要核对原程序入口，请保留接入记录。")
            shortcuts = (shortcut_writer(updater, Path(journal["base"]) / "LJQCApp.exe", Path(manager.root))
                         if manager.target == "windows-x86_64" else {})
            receipt = {"format": "ljqc-update-bootstrap", "format_version": 1,
                       "base_release": BASE_RELEASE, "target": manager.target,
                       "root": str(manager.root), "updater_version": UPDATER_VERSION,
                       "updater_entrypoint": str(updater), **shortcuts}
            _atomic_json(updates / "bootstrap.json", receipt)
            journal["phase"] = "complete"
        else:
            _restore_unregistered(journal)
            stage = Path(journal["stage"]) if journal.get("stage") else None
            if stage is not None and stage.exists():
                shutil.rmtree(stage)
            journal["phase"] = "rolled-back"
        _atomic_json(path, journal)
        return journal
