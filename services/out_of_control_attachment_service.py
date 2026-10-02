"""Immutable event attachments; upload staging is separate from revision references.

An upload is limited to 20 MiB and a saved revision to 20 files / 100 MiB.
Identical bytes share an immutable disk object. Repeated uploads with identical
event/name/description reuse their identifier; changing metadata creates a new
identifier. Only explicitly saved revisions acquire references. Unreferenced
staging can be discarded, while historical references are never deleted here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import time
from typing import Iterable
from uuid import uuid4
import zipfile

import database


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REVISION_BYTES = 100 * 1024 * 1024
MAX_REVISION_FILES = 20
ALLOWED_EXTENSIONS = ("pdf", "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "csv", "xlsx")
UPLOAD_POLICY_TEXT = "支持PDF、PNG、JPG、GIF、BMP、TIFF、CSV和XLSX；每份不超过20MB，每次最多20份、合计不超过100MB。相同内容保留原文件，不覆盖同名资料。"


class AttachmentError(ValueError):
    pass


def ensure_attachment_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS qc_managed_files (
        file_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind = 'attachment'),
        event_id INTEGER NOT NULL REFERENCES qc_ooc_events(id),
        original_name TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
        sha256 TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        uploaded_by TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS qc_event_revision_attachments (
        event_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL,
        file_id TEXT NOT NULL REFERENCES qc_managed_files(file_id),
        PRIMARY KEY(event_id, revision_no, file_id),
        FOREIGN KEY(event_id, revision_no)
            REFERENCES qc_ooc_revisions(event_id, revision_no)
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_managed_files_event ON qc_managed_files(event_id)")


def get_managed_files_directory(db_path: Path | None = None) -> Path:
    path = Path(db_path) if db_path is not None else database.get_db_path()
    return path.parent / f"{path.stem}_files"


def resolve_managed_path(relative_path: str, *, db_path: Path | None = None) -> Path:
    text = str(relative_path)
    rel = PurePosixPath(text)
    if (not text or "\\" in text or ":" in text or rel.is_absolute()
            or any(part in ("", ".", "..") for part in text.split("/"))):
        raise AttachmentError("资料保存位置无效，请重新选择资料。")
    root = get_managed_files_directory(db_path)
    if root.is_symlink():
        raise AttachmentError("资料保存位置无效，请核对数据文件夹。")
    target = root.joinpath(*rel.parts)
    cursor = target
    while cursor != root:
        if cursor.is_symlink():
            raise AttachmentError("资料保存位置无效，请核对数据文件夹。")
        cursor = cursor.parent
    if not target.resolve().is_relative_to(root.resolve()):
        raise AttachmentError("资料保存位置无效，请核对数据文件夹。")
    return target


def _require_event(connection: sqlite3.Connection, event_id: int) -> None:
    if not connection.execute("SELECT 1 FROM qc_ooc_events WHERE id = ?", (int(event_id),)).fetchone():
        raise AttachmentError("未找到对应处理记录，请重新打开后添加资料。")


def _validate_filename(filename: str) -> tuple[str, str]:
    name = str(filename).strip()
    if (not name or len(name) > 240 or "/" in name or "\\" in name
            or any(ord(char) < 32 for char in name) or name in (".", "..")):
        raise AttachmentError("文件名称无效，请使用普通文件名后重新上传。")
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentError("不支持此文件格式，请选择PDF、静态图片、CSV或XLSX。")
    return name, ext


def _validate_content(ext: str, payload: bytes) -> str:
    if not payload or len(payload) > MAX_FILE_BYTES:
        raise AttachmentError("资料为空或超过20MB，请检查文件后重新上传。")
    if ext == "pdf":
        if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-2048:]:
            raise AttachmentError("文件内容与PDF格式不符，请检查后重新上传。")
        return "application/pdf"
    image_types = {"png": ("PNG", "image/png"), "jpg": ("JPEG", "image/jpeg"),
                   "jpeg": ("JPEG", "image/jpeg"), "gif": ("GIF", "image/gif"),
                   "bmp": ("BMP", "image/bmp"), "tif": ("TIFF", "image/tiff"),
                   "tiff": ("TIFF", "image/tiff")}
    if ext in image_types:
        try:
            from PIL import Image
            with Image.open(BytesIO(payload)) as picture:
                if picture.format != image_types[ext][0] or picture.width * picture.height > 40_000_000:
                    raise ValueError("image format or size")
                picture.verify()
        except Exception as exc:
            raise AttachmentError("图片内容或尺寸不符合要求，请检查后重新上传。") from exc
        return image_types[ext][1]
    if ext == "xlsx":
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                entries = archive.infolist()
                names = {item.filename for item in entries}
                if (len(entries) > 5000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024
                        or not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names)
                        or any(name.lower().endswith("vbaproject.bin") for name in names)):
                    raise ValueError("unsupported workbook")
                if archive.testzip() is not None:
                    raise ValueError("corrupt workbook")
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise AttachmentError("表格内容与XLSX格式不符，请检查后重新上传。") from exc
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == "csv":
        if b"\x00" in payload or payload.startswith((b"MZ", b"\x7fELF")):
            raise AttachmentError("文件内容与CSV格式不符，请检查后重新上传。")
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content = payload.decode(encoding)
                if not content.strip() or any(ord(c) < 32 and c not in "\r\n\t" for c in content):
                    continue
                return "text/csv"
            except UnicodeDecodeError:
                continue
        raise AttachmentError("无法识别CSV文本，请使用UTF-8或常用中文编码保存后重试。")
    raise AttachmentError("不支持此文件格式。")


def _availability(record: dict, *, db_path: Path | None = None) -> tuple[str, str]:
    try:
        path = resolve_managed_path(record["relative_path"], db_path=db_path)
        payload = path.read_bytes()
        if len(payload) != record["size_bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
            return "corrupt", "资料内容已变化，请核对原文件或从完整备份恢复。"
        return "available", "资料可下载。"
    except FileNotFoundError:
        return "missing", "资料文件未找到，请从完整备份恢复；原记录仍保留。"
    except (OSError, AttachmentError):
        return "unreadable", "资料暂时无法读取，请核对保存位置和读取权限。"


def _as_record(row: sqlite3.Row | dict, *, check_file: bool = True) -> dict:
    record = dict(row)
    record["id"] = record["attachment_id"] = record["file_id"]
    if check_file:
        state, message = _availability(record)
        record.update(available=state == "available", availability=state, availability_message=message)
    return record


def stage_attachment(event_id: int, filename: str, data: bytes, *, uploaded_by: str,
                     description: str = "") -> dict:
    name, ext = _validate_filename(filename)
    payload = bytes(data)
    media_type = _validate_content(ext, payload)
    actor = str(uploaded_by).strip()
    if not actor:
        raise AttachmentError("请填写资料上传人。")
    note = str(description).strip()
    if len(note) > 4000:
        raise AttachmentError("资料说明过长，请缩短后保存。")
    digest = hashlib.sha256(payload).hexdigest()
    relative_path = f"attachments/{digest[:2]}/{digest}.{ext}"
    target = resolve_managed_path(relative_path)
    created_file = False
    try:
        with database.atomic_write() as connection:
            _require_event(connection, event_id)
            _cleanup_orphan_uploads(connection, minimum_age_seconds=24 * 60 * 60)
            existing = connection.execute("""SELECT * FROM qc_managed_files
                WHERE event_id = ? AND sha256 = ? AND original_name = ? AND description = ?
                ORDER BY uploaded_at LIMIT 1""", (event_id, digest, name, note)).fetchone()
            if existing:
                record = _as_record(existing)
                if not record["available"]:
                    raise AttachmentError(record["availability_message"])
                record["reused"] = True
                return record
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != payload:
                    raise AttachmentError("已保存资料的内容校验未通过，请核对后重试。")
            else:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                        raise AttachmentError("资料保存校验未通过，请重新上传。")
                    temporary.replace(target)
                    created_file = True
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            file_id = uuid4().hex
            connection.execute("""INSERT INTO qc_managed_files
                (file_id,kind,event_id,original_name,media_type,size_bytes,sha256,relative_path,uploaded_at,uploaded_by,description)
                VALUES (?,'attachment',?,?,?,?,?,?,?,?,?)""",
                (file_id, event_id, name, media_type, len(payload), digest, relative_path,
                 datetime.now(timezone.utc).isoformat(), actor, note))
            record = _as_record(connection.execute("SELECT * FROM qc_managed_files WHERE file_id=?", (file_id,)).fetchone())
            record["reused"] = False
        return record
    except Exception:
        if created_file:
            # A failed insertion has no formal reference; an existing immutable
            # object is never removed by another upload's error cleanup.
            with database.get_connection() as connection:
                referenced = connection.execute("SELECT 1 FROM qc_managed_files WHERE relative_path=?", (relative_path,)).fetchone()
            if not referenced:
                target.unlink(missing_ok=True)
        raise


def validate_attachment_ids(connection: sqlite3.Connection, event_id: int,
                            attachment_ids: Iterable[str]) -> list[str]:
    if isinstance(attachment_ids, (str, bytes)):
        raise AttachmentError("资料选择无效，请重新选择附件。")
    ids = list(dict.fromkeys(str(value) for value in attachment_ids))
    if len(ids) > MAX_REVISION_FILES:
        raise AttachmentError("一次处理记录最多保存20份资料。")
    total = 0
    for file_id in ids:
        row = connection.execute("SELECT * FROM qc_managed_files WHERE file_id=? AND event_id=?",
                                 (file_id, int(event_id))).fetchone()
        if row is None:
            raise AttachmentError("所选资料不属于本次处理记录，请重新选择。")
        record = _as_record(row)
        if not record["available"]:
            raise AttachmentError(record["availability_message"])
        total += record["size_bytes"]
    if total > MAX_REVISION_BYTES:
        raise AttachmentError("本次资料合计超过100MB，请减少资料后保存。")
    return ids


def link_revision_attachments(connection: sqlite3.Connection, event_id: int,
                              revision_no: int, attachment_ids: Iterable[str]) -> None:
    ids = validate_attachment_ids(connection, event_id, attachment_ids)
    connection.executemany("""INSERT OR IGNORE INTO qc_event_revision_attachments
        (event_id,revision_no,file_id) VALUES (?,?,?)""", [(event_id, revision_no, file_id) for file_id in ids])


def list_attachments(event_id: int, revision_no: int | None = None) -> list[dict]:
    with database.get_connection() as connection:
        _require_event(connection, event_id)
        if revision_no is None:
            revision_no = int(connection.execute("SELECT current_revision_no FROM qc_ooc_events WHERE id=?", (event_id,)).fetchone()[0])
        rows = connection.execute("""SELECT f.* FROM qc_event_revision_attachments a
            JOIN qc_managed_files f ON f.file_id=a.file_id AND f.event_id=a.event_id
            WHERE a.event_id=? AND a.revision_no=? ORDER BY f.uploaded_at,f.file_id""",
            (event_id, revision_no)).fetchall()
    return [_as_record(row) for row in rows]


def list_staged_attachments(event_id: int) -> list[dict]:
    with database.get_connection() as connection:
        _require_event(connection, event_id)
        rows = connection.execute("""SELECT f.* FROM qc_managed_files f WHERE event_id=?
            AND NOT EXISTS(SELECT 1 FROM qc_event_revision_attachments a WHERE a.file_id=f.file_id)
            ORDER BY uploaded_at,file_id""", (event_id,)).fetchall()
    return [_as_record(row) for row in rows]


def read_attachment(event_id: int, attachment_id: str, revision_no: int | None = None) -> bytes:
    records = list_attachments(event_id, revision_no)
    record = next((item for item in records if item["file_id"] == attachment_id), None)
    if record is None:
        raise AttachmentError("所选资料不属于本次处理版本，请重新打开资料清单。")
    if not record["available"]:
        raise AttachmentError(record["availability_message"])
    try:
        payload = resolve_managed_path(record["relative_path"]).read_bytes()
    except OSError as exc:
        raise AttachmentError("资料暂时无法读取，请核对保存位置和读取权限。") from exc
    if len(payload) != record["size_bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise AttachmentError("资料内容已变化，请核对原文件或从完整备份恢复。")
    return payload


def discard_staged_attachments(event_id: int, attachment_ids: Iterable[str]) -> int:
    discarded = 0
    with database.atomic_write() as connection:
        _require_event(connection, event_id)
        for file_id in set(attachment_ids):
            row = connection.execute("SELECT * FROM qc_managed_files WHERE file_id=? AND event_id=?",
                                     (str(file_id), event_id)).fetchone()
            if row is None:
                raise AttachmentError("所选资料不属于本次处理记录。")
            if connection.execute("SELECT 1 FROM qc_event_revision_attachments WHERE file_id=?", (str(file_id),)).fetchone():
                continue
            # Keep immutable bytes even on cancellation: another saved metadata
            # record may share them; unregistered orphan bytes never enter a
            # backup or a user's attachment list.
            connection.execute("DELETE FROM qc_managed_files WHERE file_id=?", (str(file_id),))
            discarded += 1
    return discarded


def _cleanup_orphan_uploads(connection: sqlite3.Connection, *, minimum_age_seconds: int) -> int:
    """Reclaim crash/cancel leftovers only; saved/staged metadata always wins.

    Old immutable objects without ANY metadata row, and abandoned temporary
    uploads older than 24 hours, are safe to reclaim under the same writer lock
    used by uploads and snapshots. Recovery originals are deliberately excluded.
    """
    root = get_managed_files_directory()
    directory = root / "attachments"
    if not directory.exists() or root.is_symlink() or directory.is_symlink():
        return 0
    registered = {row[0] for row in connection.execute("SELECT relative_path FROM qc_managed_files")}
    removed = 0
    cutoff = time.time() - minimum_age_seconds
    try:
        folders = list(directory.iterdir())
    except OSError:
        return 0
    for folder in folders:
        if folder.is_symlink() or not folder.is_dir() or len(folder.name) != 2:
            continue
        try:
            paths = list(folder.iterdir())
        except OSError:
            continue
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            generated_name = (len(path.stem) == 64 and all(c in "0123456789abcdef" for c in path.stem)
                              and path.suffix.lstrip(".") in ALLOWED_EXTENSIONS)
            if not path.name.startswith(".upload-") and not generated_name:
                continue
            try:
                if relative not in registered and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                # Maintenance must not prevent an unrelated valid upload.
                continue
    return removed


def cleanup_unreferenced_uploads() -> int:
    """Explicit maintenance using the same conservative 24-hour grace period."""
    with database.atomic_write() as connection:
        return _cleanup_orphan_uploads(connection, minimum_age_seconds=24 * 60 * 60)
