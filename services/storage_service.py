from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import wraps
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Iterable
from uuid import uuid4
import zipfile

import database


BACKUP_FILE_PREFIX = "qc_lj_app_backup"
PRE_RESTORE_BACKUP_PREFIX = "qc_lj_app_pre_restore"
SQLITE_FILE_TYPES = [("数据备份文件", "*.zip *.db"), ("所有文件", "*.*")]
BACKUP_FORMAT_VERSION = 1
MAX_BACKUP_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class DatabaseLocationStatus:
    db_path: Path
    db_dir: Path
    default_db_path: Path
    configured_db_path: Path | None
    config_path: Path
    default_backup_dir: Path
    exists: bool
    is_readable: bool
    is_valid_sqlite: bool
    size_bytes: int
    status_text: str


@dataclass(frozen=True)
class StorageOperationResult:
    target_path: Path
    message: str
    restart_required: bool = True
    config_path: Path | None = None
    protection_backup_path: Path | None = None
    warnings: tuple[str, ...] = ()


def get_database_location_status() -> DatabaseLocationStatus:
    db_path = database.get_db_path()
    exists = db_path.exists()
    is_readable = exists and os.access(db_path, os.R_OK)
    is_valid_sqlite = False
    status_text = "当前数据文件可用。"
    if exists and is_readable:
        validation = validate_sqlite_database(db_path)
        is_valid_sqlite = validation[0]
        if not is_valid_sqlite:
            status_text = validation[1]
    elif exists:
        status_text = "当前数据文件存在，但无法读取。"
    else:
        status_text = "当前尚无数据文件，将在首次使用时自动建立。"

    size_bytes = 0
    if exists:
        try:
            size_bytes = int(db_path.stat().st_size)
        except OSError:
            size_bytes = 0

    return DatabaseLocationStatus(
        db_path=db_path,
        db_dir=db_path.parent,
        default_db_path=database.get_default_db_path(),
        configured_db_path=database.get_configured_db_path(),
        config_path=database.get_storage_config_path(),
        default_backup_dir=get_default_backup_dir(),
        exists=exists,
        is_readable=is_readable,
        is_valid_sqlite=is_valid_sqlite,
        size_bytes=size_bytes,
        status_text=status_text,
    )


def get_default_backup_dir() -> Path:
    return database.get_storage_config_path().parent / "backups"


def open_folder_in_system(path: Path) -> None:
    target = Path(path)
    if not target.exists():
        raise RuntimeError(f"目标路径不存在：{target}")

    try:
        if sys.platform == "win32":
            opener = getattr(os, "startfile", None)
            if opener is not None:
                opener(str(target))
            else:
                subprocess.run(["explorer", str(target)], check=True, capture_output=True)
        else:
            command = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([command, str(target)], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"无法打开文件夹，请在系统文件管理器中打开：{target}") from exc


def choose_directory_via_dialog(*, initial_dir: Path | None = None, title: str) -> Path | None:
    return _open_native_path_dialog(
        mode="directory",
        initial_dir=initial_dir,
        title=title,
    )


def choose_backup_file_via_dialog(*, initial_dir: Path | None = None, title: str) -> Path | None:
    return _open_native_path_dialog(
        mode="file",
        initial_dir=initial_dir,
        title=title,
    )


def validate_sqlite_database(path: Path) -> tuple[bool, str]:
    db_path = Path(path)
    if not db_path.exists():
        return False, f"文件不存在：{db_path}"
    try:
        resolved_path = db_path.resolve()
        connection = sqlite3.connect(f"{resolved_path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"无法打开数据备份文件：{db_path}"
    try:
        quick_check_rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return False, f"数据文件检查未通过：{db_path}"
    finally:
        connection.close()

    normalized_rows = [str(row[0] or "").strip().lower() for row in quick_check_rows]
    if normalized_rows == ["ok"]:
        return True, "数据文件检查通过。"
    return False, "数据文件检查未通过，请选择有效备份文件。"


def validate_directory_writable(path: Path, *, create_if_missing: bool = False) -> tuple[bool, str]:
    target_dir = Path(path)
    if create_if_missing:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"无法创建目录：{target_dir}"
    if not target_dir.exists() or not target_dir.is_dir():
        return False, f"目录不存在：{target_dir}"

    try:
        with tempfile.NamedTemporaryFile(dir=target_dir, prefix="ljqc_write_test_", delete=True):
            pass
    except OSError:
        return False, f"目录不可写：{target_dir}"
    return True, "目录可写。"


def validate_backup_file(path: Path) -> tuple[bool, str]:
    """Validate a supported database or complete bundle without changing data."""
    try:
        with tempfile.TemporaryDirectory(prefix="qc-backup-validation-") as temporary:
            manifest, complete = _read_backup_into(Path(path), Path(temporary))
        if not complete and manifest["files"]:
            return True, "数据备份可恢复，但未包含附件文件；缺失资料需另从完整备份恢复。"
        if manifest.get("missing_files"):
            return True, "这是有缺失资料的保护性备份；可恢复数据，缺失资料需另从完整备份补回。"
        return True, "备份文件校验通过。"
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        return False, str(exc) if isinstance(exc, RuntimeError) else "无法读取备份文件，请检查后重试。"


def _friendly_storage_errors(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError("数据操作未完成，请检查文件夹读写权限和可用空间后重试；如已生成保护性备份，请妥善保留。") from exc
    return run


@_friendly_storage_errors
def migrate_database_to_directory(target_dir: Path) -> StorageOperationResult:
    database.init_db()
    source_db_path = database.get_db_path()
    destination_dir = Path(target_dir)
    validation = validate_directory_writable(destination_dir, create_if_missing=False)
    if not validation[0]:
        raise RuntimeError(validation[1])

    destination_db_path = destination_dir / source_db_path.name
    if destination_db_path.resolve() == source_db_path.resolve():
        raise RuntimeError("所选文件夹与当前保存位置相同，无需更改。")
    from services.out_of_control_attachment_service import get_managed_files_directory
    if destination_db_path.exists() or get_managed_files_directory(destination_db_path).exists():
        raise RuntimeError(f"目标文件夹已存在同名数据文件：{destination_db_path}")
    config_path = database.get_storage_config_path()
    original_config = config_path.read_bytes() if config_path.exists() else None
    created_paths: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".qc-migration-", dir=destination_dir) as staging:
            snapshot = Path(staging)
            _prepare_complete_snapshot(snapshot)
            created_paths = _install_snapshot_files(snapshot, destination_db_path)
            _copy_database_snapshot(snapshot / "data.db", destination_db_path, overwrite=False)
            config_path = database.save_db_path_config(destination_db_path)
    except Exception:
        _cleanup_paths([destination_db_path, *_iter_db_sidecar_paths(destination_db_path), *created_paths])
        # This directory was required not to exist before this migration.
        # Remove only the new incomplete destination, never the old location.
        shutil.rmtree(get_managed_files_directory(destination_db_path), ignore_errors=True)
        _restore_config_bytes(config_path, original_config)
        raise
    return StorageOperationResult(
        target_path=destination_db_path,
        config_path=config_path,
        message=f"数据已复制到新位置：{destination_db_path}。请重新打开软件后使用。",
    )


@_friendly_storage_errors
def create_database_backup(target_dir: Path | None = None) -> StorageOperationResult:
    return _create_backup(target_dir, allow_missing=False, prefix=BACKUP_FILE_PREFIX)


def _create_backup(target_dir: Path | None, *, allow_missing: bool, prefix: str) -> StorageOperationResult:
    database.init_db()
    destination_dir = Path(target_dir) if target_dir is not None else get_default_backup_dir()
    validation = validate_directory_writable(destination_dir, create_if_missing=True)
    if not validation[0]:
        raise RuntimeError(validation[1])

    with tempfile.TemporaryDirectory(prefix=".qc-backup-", dir=destination_dir) as staging:
        snapshot = Path(staging)
        manifest = _prepare_complete_snapshot(snapshot, allow_missing=allow_missing)
        suffix = ".zip" if manifest["files"] or manifest.get("missing_files") else ".db"
        backup_path = _build_timestamped_db_path(destination_dir, prefix, suffix=suffix)
        temporary_output = destination_dir / f".qc-backup-{uuid4().hex}.tmp"
        try:
            if suffix == ".zip":
                with zipfile.ZipFile(temporary_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(snapshot / "data.db", "data.db")
                    archive.write(snapshot / "manifest.json", "manifest.json")
                    for entry in manifest["files"]:
                        archive.write(snapshot / "files" / entry["path"], "files/" + entry["path"])
                # Validate the finished artifact, not only its source tree.
                with tempfile.TemporaryDirectory(prefix=".qc-check-", dir=destination_dir) as check:
                    _read_backup_into(temporary_output, Path(check))
            else:
                shutil.copyfile(snapshot / "data.db", temporary_output)
                if not validate_sqlite_database(temporary_output)[0]:
                    raise RuntimeError("备份校验未通过，请重试。")
            temporary_output.replace(backup_path)
        finally:
            temporary_output.unlink(missing_ok=True)
    return StorageOperationResult(
        target_path=backup_path,
        message=(f"完整数据备份已生成：{backup_path}。数据库、已保存报告和登记资料均已核对。"
                 if not manifest.get("missing_files") else f"保护性备份已生成：{backup_path}；当前缺失或损坏资料已列明。"),
        restart_required=False,
        warnings=((f"当前有{len(manifest['missing_files'])}份资料缺失或损坏，保护性备份已保留数据库及可读取资料。",)
                  if manifest.get("missing_files") else ()),
    )


@_friendly_storage_errors
def restore_database_from_backup_file(backup_file: Path) -> StorageOperationResult:
    database.init_db()
    backup_path = Path(backup_file)
    current_db_path = database.get_db_path()
    warnings: tuple[str, ...] = ()
    with tempfile.TemporaryDirectory(prefix=".qc-restore-", dir=current_db_path.parent) as staging:
        snapshot = Path(staging)
        manifest, is_complete = _read_backup_into(backup_path, snapshot)
        if not is_complete and manifest["files"]:
            warnings = ("此次为仅数据库备份，未提供附件文件；缺失资料请另从完整备份恢复。",)
        elif manifest.get("missing_files"):
            warnings = (f"此次保护性备份中有{len(manifest['missing_files'])}份资料未包含，缺失资料需另从完整备份恢复。",)
        # Protection must succeed before touching any currently usable data.
        # Existing missing files must not prevent a good backup repairing them.
        # A partial protection is explicit and is never advertised as complete.
        protection_backup = _create_backup(get_default_backup_dir(), allow_missing=True, prefix=PRE_RESTORE_BACKUP_PREFIX)
        warnings += protection_backup.warnings
        rollback_db = snapshot / "before_restore.db"
        _copy_database_snapshot(current_db_path, rollback_db, overwrite=False)
        created_paths: list[Path] = []
        replaced_files: list[tuple[Path, Path]] = []
        try:
            if is_complete:
                created_paths = _install_snapshot_files(snapshot, current_db_path,
                                                       entries=manifest["files"], replaced_files=replaced_files)
            # Existing files are immutable and retained. Installing a superset
            # before SQLite's transactional replacement leaves both the old and
            # restored references readable at every successful switch.
            _copy_database_snapshot(snapshot / "data.db", current_db_path, overwrite=True)
        except Exception:
            _copy_database_snapshot(rollback_db, current_db_path, overwrite=True)
            _cleanup_paths(created_paths)
            for target, previous in reversed(replaced_files):
                shutil.copyfile(previous, target)
            raise

    return StorageOperationResult(
        target_path=current_db_path,
        protection_backup_path=protection_backup.target_path,
        message=(
            f"数据已从备份恢复到当前保存位置：{current_db_path}。"
            f"恢复前的保护性备份已保存到：{protection_backup.target_path}。请重新打开软件后使用。"
            + (" " + " ".join(warnings) if warnings else "")
        ),
        warnings=warnings,
    )


def _restore_config_bytes(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(previous)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_file_records(db_path: Path) -> list[dict]:
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "qc_managed_files" not in tables:
            return []
        return [dict(row) for row in connection.execute("SELECT relative_path,size_bytes,sha256 FROM qc_managed_files")]


def _check_relative_path(value: object) -> str:
    text = str(value)
    path = PurePosixPath(text)
    if (not text or path.is_absolute() or "\\" in text or ":" in text
            or any(part in ("", ".", "..") for part in text.split("/"))):
        raise RuntimeError("备份中的资料保存位置无效，未替换当前数据。")
    return text


def _deduplicate_manifest(records: list[dict]) -> list[dict]:
    entries: dict[str, dict] = {}
    for record in records:
        relative = _check_relative_path(record["relative_path"])
        entry = {"path": relative, "size": int(record["size_bytes"]), "sha256": str(record["sha256"])}
        if entry["size"] <= 0 or not re_full_sha256(entry["sha256"]):
            raise RuntimeError("资料清单无效，请核对后重新备份。")
        if relative in entries and entries[relative] != entry:
            raise RuntimeError("资料清单存在不一致，未生成备份。")
        entries[relative] = entry
    return sorted(entries.values(), key=lambda item: item["path"])


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _verify_file(path: Path, entry: dict) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("备份所需资料未找到，请核对或先恢复缺失资料。")
        if path.stat().st_size != int(entry["size"]) or _digest_file(path) != entry["sha256"]:
            raise RuntimeError("资料内容校验未通过，当前数据未替换。")
    except OSError as exc:
        raise RuntimeError("无法读取备份所需资料，请核对文件夹权限。") from exc


def _prepare_complete_snapshot(destination: Path, *, allow_missing: bool = False) -> dict:
    from services.out_of_control_attachment_service import resolve_managed_path
    destination.mkdir(parents=True, exist_ok=True)
    # The writer reservation prevents uploads/discards/revisions during capture;
    # PDF reports live in SQLite and are captured by the same DB snapshot.
    with database.atomic_write():
        _copy_database_snapshot(database.get_db_path(), destination / "data.db", overwrite=False)
        registered = _deduplicate_manifest(_managed_file_records(destination / "data.db"))
        files, missing_files = [], []
        for entry in registered:
            try:
                source = resolve_managed_path(entry["path"])
                _verify_file(source, entry)
            except (ValueError, RuntimeError, OSError) as exc:
                if not allow_missing:
                    raise RuntimeError("备份所需资料缺失、损坏或无法读取，请核对或从完整备份恢复。") from exc
                missing_files.append(entry)
                continue
            target = destination / "files" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            _verify_file(target, entry)
            files.append(entry)
    db_entry = {"path": "data.db", "size": (destination / "data.db").stat().st_size,
                "sha256": _digest_file(destination / "data.db")}
    manifest = {"format": "ljqc-complete-backup", "version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now().isoformat(), "database": db_entry, "files": files,
                "missing_files": missing_files, "complete": not missing_files}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _read_backup_into(backup_path: Path, destination: Path) -> tuple[dict, bool]:
    destination.mkdir(parents=True, exist_ok=True)
    if not zipfile.is_zipfile(backup_path):
        validation = validate_sqlite_database(backup_path)
        if not validation[0]:
            raise RuntimeError(validation[1])
        _copy_database_snapshot(backup_path, destination / "data.db", overwrite=False)
        _validate_supported_database(destination / "data.db")
        return {"files": _deduplicate_manifest(_managed_file_records(destination / "data.db"))}, False
    try:
        with zipfile.ZipFile(backup_path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) > 100_000 or len(names) != len(set(names))
                    or sum(entry.file_size for entry in entries) > MAX_BACKUP_BYTES
                    or not {"data.db", "manifest.json"}.issubset(names)):
                raise RuntimeError("备份包结构或大小不符合要求，未替换当前数据。")
            for item in entries:
                _check_relative_path(item.filename)
                if item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise RuntimeError("备份包包含不支持的资料，未替换当前数据。")
            if archive.getinfo("manifest.json").file_size > 32 * 1024 * 1024:
                raise RuntimeError("备份清单过大，未替换当前数据。")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "ljqc-complete-backup" or manifest.get("version") != BACKUP_FORMAT_VERSION:
                raise RuntimeError("不支持此备份版本，请选择本软件生成的备份。")
            expected = {"data.db", "manifest.json"}
            for entry in manifest["files"]:
                expected.add("files/" + _check_relative_path(entry["path"]))
            if set(names) != expected or len(manifest["files"]) != len(expected) - 2:
                raise RuntimeError("备份清单与资料不一致，未替换当前数据。")
            for name in names:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            _verify_file(destination / "data.db", manifest["database"])
            _validate_supported_database(destination / "data.db")
            registered = _deduplicate_manifest(_managed_file_records(destination / "data.db"))
            listed = manifest["files"] + manifest.get("missing_files", [])
            if (sorted(listed, key=lambda item: item["path"]) != registered
                    or bool(manifest.get("missing_files")) == bool(manifest.get("complete", True))):
                raise RuntimeError("备份中的报告或附件清单与检测资料不一致。请重新选择完整备份文件；当前数据未更改。")
            for entry in manifest["files"]:
                _verify_file(destination / "files" / entry["path"], entry)
            return manifest, True
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, EOFError) as exc:
        raise RuntimeError("备份包损坏或清单无效，未替换当前数据。") from exc


def _validate_supported_database(path: Path) -> None:
    if not validate_sqlite_database(path)[0]:
        raise RuntimeError("备份中的数据库检查未通过。")
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"projects", "batches", "results"}.issubset(tables):
        raise RuntimeError("此文件不包含可恢复的质控业务数据。")


def _install_snapshot_files(snapshot: Path, destination_db: Path, *, entries: list[dict] | None = None,
                            replaced_files: list[tuple[Path, Path]] | None = None) -> list[Path]:
    from services.out_of_control_attachment_service import resolve_managed_path
    created: list[Path] = []
    try:
        for entry in entries if entries is not None else _deduplicate_manifest(_managed_file_records(snapshot / "data.db")):
            source = snapshot / "files" / entry["path"]
            _verify_file(source, entry)
            try:
                target = resolve_managed_path(entry["path"], db_path=destination_db)
            except ValueError as exc:
                raise RuntimeError("资料保存位置无效，请核对数据文件夹。") from exc
            existed = target.exists()
            if target.exists():
                try:
                    _verify_file(target, entry)
                    continue
                except RuntimeError:
                    if replaced_files is None:
                        raise
                    previous = resolve_managed_path(f"recovery_originals/{uuid4().hex}/{target.name}", db_path=destination_db)
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, previous)
                    replaced_files.append((target, previous))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".restore-{uuid4().hex}.tmp"
            try:
                shutil.copyfile(source, temporary)
                _verify_file(temporary, entry)
                temporary.replace(target)
                if not existed:
                    created.append(target)
            finally:
                temporary.unlink(missing_ok=True)
        return created
    except Exception:
        _cleanup_paths(created)
        for target, previous in reversed(replaced_files or []):
            shutil.copyfile(previous, target)
        raise


def _open_native_path_dialog(
    *,
    mode: str,
    initial_dir: Path | None,
    title: str,
) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("无法打开文件选择窗口，请重新打开软件后重试。") from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        initialdir = str(initial_dir) if initial_dir is not None else str(database.get_db_path().parent)
        if mode == "directory":
            selected = filedialog.askdirectory(
                title=title,
                initialdir=initialdir,
                mustexist=True,
                parent=root,
            )
        else:
            selected = filedialog.askopenfilename(
                title=title,
                initialdir=initialdir,
                filetypes=SQLITE_FILE_TYPES,
                parent=root,
            )
    except Exception as exc:
        raise RuntimeError("无法打开文件选择窗口，请稍后重试。") from exc
    finally:
        if root is not None:
            root.destroy()

    cleaned = str(selected or "").strip()
    if not cleaned:
        return None
    return Path(cleaned)


def _copy_database_snapshot(source_db_path: Path, destination_db_path: Path, *, overwrite: bool) -> None:
    source_path = Path(source_db_path)
    destination_path = Path(destination_db_path)
    if not source_path.exists():
        raise RuntimeError(f"原数据文件不存在：{source_path}")
    if source_path.resolve() == destination_path.resolve():
        raise RuntimeError("请选择与原数据文件不同的保存位置。")
    if destination_path.exists() and not overwrite:
        raise RuntimeError(f"目标文件已存在：{destination_path}")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(str(source_path)) as source_connection:
            with sqlite3.connect(str(destination_path)) as destination_connection:
                source_connection.backup(destination_connection)

        validation = validate_sqlite_database(destination_path)
        if not validation[0]:
            raise RuntimeError(validation[1])
        _cleanup_paths([*_iter_db_sidecar_paths(destination_path)])
    except Exception:
        raise


def _build_timestamped_db_path(target_dir: Path, prefix: str, *, suffix: str = ".db") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = Path(target_dir) / f"{prefix}_{timestamp}{suffix}"
    if not base_path.exists():
        return base_path

    counter = 1
    while True:
        candidate = Path(target_dir) / f"{prefix}_{timestamp}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _iter_db_sidecar_paths(db_path: Path) -> Iterable[Path]:
    base = str(db_path)
    yield Path(f"{base}-wal")
    yield Path(f"{base}-shm")
    yield Path(f"{base}-journal")


def _cleanup_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            continue
        except PermissionError:
            continue
