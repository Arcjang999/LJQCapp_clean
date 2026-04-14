from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from typing import Iterable

import database


BACKUP_FILE_PREFIX = "qc_lj_app_backup"
PRE_RESTORE_BACKUP_PREFIX = "qc_lj_app_pre_restore"
SQLITE_FILE_TYPES = [("SQLite 数据库", "*.db"), ("所有文件", "*.*")]


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


def get_database_location_status() -> DatabaseLocationStatus:
    db_path = database.get_db_path()
    exists = db_path.exists()
    is_readable = exists and os.access(db_path, os.R_OK)
    is_valid_sqlite = False
    status_text = "当前数据库文件可用。"
    if exists and is_readable:
        validation = validate_sqlite_database(db_path)
        is_valid_sqlite = validation[0]
        if not is_valid_sqlite:
            status_text = validation[1]
    elif exists:
        status_text = "当前数据库文件存在，但无法读取。"
    else:
        status_text = "当前数据库文件尚不存在，应用会在需要时自动初始化。"

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
        os.startfile(str(target))  # type: ignore[attr-defined]
    except AttributeError:
        subprocess.run(["explorer", str(target)], check=True)
    except OSError as exc:
        raise RuntimeError(f"无法打开文件夹：{target}") from exc


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
        return False, f"无法打开 SQLite 数据库：{db_path}"
    try:
        quick_check_rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return False, f"数据库校验失败：{db_path}"
    finally:
        connection.close()

    normalized_rows = [str(row[0] or "").strip().lower() for row in quick_check_rows]
    if normalized_rows == ["ok"]:
        return True, "数据库校验通过。"
    return False, "数据库结构校验未通过，请选择有效备份文件。"


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


def migrate_database_to_directory(target_dir: Path) -> StorageOperationResult:
    database.init_db()
    source_db_path = database.get_db_path()
    destination_dir = Path(target_dir)
    validation = validate_directory_writable(destination_dir, create_if_missing=False)
    if not validation[0]:
        raise RuntimeError(validation[1])

    destination_db_path = destination_dir / source_db_path.name
    if destination_db_path.resolve() == source_db_path.resolve():
        raise RuntimeError("所选目录与当前数据库目录相同，无需迁移。")
    if destination_db_path.exists():
        raise RuntimeError(f"目标目录已存在同名数据库文件：{destination_db_path}")

    _copy_database_snapshot(source_db_path, destination_db_path, overwrite=False)
    validation = validate_sqlite_database(destination_db_path)
    if not validation[0]:
        raise RuntimeError(validation[1])

    config_path = database.save_db_path_config(destination_db_path)
    return StorageOperationResult(
        target_path=destination_db_path,
        config_path=config_path,
        message=f"数据库已复制到新位置：{destination_db_path}。请重启应用后生效。",
    )


def create_database_backup(target_dir: Path | None = None) -> StorageOperationResult:
    database.init_db()
    source_db_path = database.get_db_path()
    destination_dir = Path(target_dir) if target_dir is not None else get_default_backup_dir()
    validation = validate_directory_writable(destination_dir, create_if_missing=True)
    if not validation[0]:
        raise RuntimeError(validation[1])

    backup_path = _build_timestamped_db_path(destination_dir, BACKUP_FILE_PREFIX)
    _copy_database_snapshot(source_db_path, backup_path, overwrite=False)
    return StorageOperationResult(
        target_path=backup_path,
        message=f"数据库备份已生成：{backup_path}",
        restart_required=False,
    )


def restore_database_from_backup_file(backup_file: Path) -> StorageOperationResult:
    database.init_db()
    backup_path = Path(backup_file)
    validation = validate_sqlite_database(backup_path)
    if not validation[0]:
        raise RuntimeError(validation[1])

    current_db_path = database.get_db_path()
    protection_backup = create_database_backup(get_default_backup_dir())

    try:
        _copy_database_snapshot(backup_path, current_db_path, overwrite=True)
        _cleanup_paths([*_iter_db_sidecar_paths(current_db_path)])
    except Exception:
        raise

    return StorageOperationResult(
        target_path=current_db_path,
        protection_backup_path=protection_backup.target_path,
        message=(
            f"数据库已从备份恢复到当前路径：{current_db_path}。"
            f"恢复前的保护性备份已保存到：{protection_backup.target_path}。请重启应用后生效。"
        ),
    )


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
        raise RuntimeError("无法加载系统文件选择窗口，请检查本机 tkinter 环境。") from exc

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
        raise RuntimeError("系统原生选择窗口调用失败，请稍后重试。") from exc
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
        raise RuntimeError(f"源数据库文件不存在：{source_path}")
    if source_path.resolve() == destination_path.resolve():
        raise RuntimeError("源数据库与目标数据库不能是同一个文件。")
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


def _build_timestamped_db_path(target_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = Path(target_dir) / f"{prefix}_{timestamp}.db"
    if not base_path.exists():
        return base_path

    counter = 1
    while True:
        candidate = Path(target_dir) / f"{prefix}_{timestamp}_{counter}.db"
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
