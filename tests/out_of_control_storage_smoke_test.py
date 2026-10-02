"""Isolated filesystem/SQLite acceptance for T1-08 and T1-09."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from services import out_of_control_attachment_service as attachments
from services import storage_service as storage


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class IsolatedStorage:
    def __enter__(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = {name: getattr(database, name) for name in
                         ("DB_PATH", "DEFAULT_DB_PATH", "STORAGE_CONFIG_PATH", "LEGACY_DB_CANDIDATES")}
        database.DB_PATH = database.DEFAULT_DB_PATH = self.root / "original" / "qc.db"
        database.STORAGE_CONFIG_PATH = self.root / "config" / "storage_config.json"
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()
        with database.atomic_write() as connection:
            attachments.ensure_attachment_schema(connection)
            for event_id in (1, 2):
                connection.execute("""INSERT INTO qc_ooc_events
                    (id,source_type,source_id,qc_method,original_classification,origin_snapshot_json,opened_by,opened_at)
                    VALUES (?,'lj_result',?,'lj','reject','{}','工程验收','2026-09-28')""", (event_id, event_id))
                self.add_revision(connection, event_id, 1)
            connection.execute("CREATE TABLE storage_test_report (id INTEGER PRIMARY KEY,pdf BLOB)")
            connection.execute("INSERT INTO storage_test_report VALUES (1,?)", (PDF,))
        return self

    @staticmethod
    def add_revision(connection, event_id, revision_no):
        connection.execute("""INSERT INTO qc_ooc_revisions
            (event_id,revision_no,status,action,content_json,saved_by,saved_at)
            VALUES (?,?,'in_progress','save_draft','{}','工程验收','2026-09-28')""", (event_id, revision_no))
        connection.execute("UPDATE qc_ooc_events SET current_revision_no=? WHERE id=?", (revision_no, event_id))

    def save(self, ids, revision=2, event=1):
        with database.atomic_write() as connection:
            self.add_revision(connection, event, revision)
            attachments.link_revision_attachments(connection, event, revision, ids)

    def __exit__(self, *args):
        for name, value in self.original.items():
            setattr(database, name, value)
        self.temporary.cleanup()


def expect_error(operation, types=(ValueError, RuntimeError, OSError)):
    try:
        operation()
    except types:
        return
    raise AssertionError("Expected operation rejection")


def xlsx_bytes():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("[Content_Types].xml", (2026, 1, 1, 0, 0, 0)), '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr(zipfile.ZipInfo("xl/workbook.xml", (2026, 1, 1, 0, 0, 0)), '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>')
    return buffer.getvalue()


def test_upload_validation_staging_identity_and_history():
    with IsolatedStorage() as context:
        first = attachments.stage_attachment(1, "复测.pdf", PDF, uploaded_by="检验员", description="复测原件")
        assert attachments.list_attachments(1) == []
        assert len(attachments.list_staged_attachments(1)) == 1
        duplicate = attachments.stage_attachment(1, "复测.pdf", PDF, uploaded_by="检验员", description="复测原件")
        assert duplicate["id"] == first["id"] and duplicate["reused"]
        sheet = attachments.stage_attachment(1, "结果.xlsx", xlsx_bytes(), uploaded_by="检验员")
        a = attachments.stage_attachment(1, "结果.csv", b"value\n1\n", uploaded_by="检验员")
        b = attachments.stage_attachment(1, "结果.csv", b"value\n2\n", uploaded_by="检验员")
        assert a["id"] != b["id"] and a["relative_path"] != b["relative_path"]
        renamed = attachments.stage_attachment(2, "另一个事件.pdf", PDF, uploaded_by="检验员")
        assert renamed["relative_path"] == first["relative_path"]
        for filename, data in [("../../结果.csv", b"x\n1"), ("/tmp/结果.pdf", PDF),
                               ("报告.pdf", b"MZ executable"), ("script.py", b"print(1)"),
                               ("结果.xlsx", b"MZ executable"), ("大文件.csv", b"x" * (attachments.MAX_FILE_BYTES + 1))]:
            expect_error(lambda: attachments.stage_attachment(1, filename, data, uploaded_by="检验员"))
        with database.atomic_write() as connection:
            expect_error(lambda: attachments.validate_attachment_ids(connection, 1, [renamed["id"]]))
            expect_error(lambda: attachments.validate_attachment_ids(connection, 1, ["forged-id"]))
        context.save([first["id"], sheet["id"], a["id"]])
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        assert attachments.read_attachment(1, sheet["id"], 2) == xlsx_bytes()
        context.save([b["id"]], revision=3)
        assert [row["id"] for row in attachments.list_attachments(1)] == [b["id"]]
        assert {row["id"] for row in attachments.list_attachments(1, 2)} == {first["id"], sheet["id"], a["id"]}
        expect_error(lambda: attachments.read_attachment(2, first["id"]))
        assert attachments.discard_staged_attachments(1, [first["id"]]) == 0
        assert attachments.discard_staged_attachments(2, [renamed["id"]]) == 1
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        database.init_db()  # Repeat initialization represents reopening the app.
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        with patch.object(Path, "replace", side_effect=OSError("injected write failure")):
            expect_error(lambda: attachments.stage_attachment(1, "failed.csv", b"x\n3", uploaded_by="检验员"))
        assert not list(attachments.get_managed_files_directory().rglob(".upload-*"))
        assert all(row["original_name"] != "failed.csv" for row in attachments.list_staged_attachments(1))
        path = attachments.resolve_managed_path(first["relative_path"])
        original_bytes = path.read_bytes()
        path.write_bytes(b"changed")
        assert next(r for r in attachments.list_attachments(1, 2) if r["id"] == first["id"])["availability"] == "corrupt"
        expect_error(lambda: attachments.read_attachment(1, first["id"], 2))
        path.write_bytes(original_bytes)
        path.unlink()
        assert next(r for r in attachments.list_attachments(1, 2) if r["id"] == first["id"])["availability"] == "missing"
        expect_error(lambda: storage.create_database_backup(context.root / "bad-backup"))
        assert not list((context.root / "bad-backup").glob("*.zip"))


def test_save_rollback_and_path_boundaries():
    with IsolatedStorage() as context:
        item = attachments.stage_attachment(1, "data.csv", b"x\n1", uploaded_by="检验员")
        try:
            with database.atomic_write() as connection:
                context.add_revision(connection, 1, 2)
                attachments.link_revision_attachments(connection, 1, 2, [item["id"]])
                raise RuntimeError("injected version failure")
        except RuntimeError:
            pass
        assert attachments.list_attachments(1) == []
        assert len(attachments.list_staged_attachments(1)) == 1
        for path in ("../escape", "/tmp/escape", "a/../../escape", "a\\..\\escape", "C:/escape"):
            expect_error(lambda: attachments.resolve_managed_path(path))
        outside = context.root / "outside"
        outside.mkdir()
        (attachments.get_managed_files_directory() / "bad-link").symlink_to(outside, target_is_directory=True)
        expect_error(lambda: attachments.resolve_managed_path("bad-link/file.csv"))


def test_static_images_cancel_cleanup_and_unreadable_state():
    from PIL import Image
    with IsolatedStorage() as context:
        image = BytesIO()
        Image.new("RGB", (2, 2), (25, 50, 75)).save(image, format="PNG")
        item = attachments.stage_attachment(1, "照片.png", image.getvalue(), uploaded_by="检验员")
        context.save([item["id"]])
        expect_error(lambda: attachments.stage_attachment(1, "照片.jpg", image.getvalue(), uploaded_by="检验员"))
        original_read = Path.read_bytes
        image_path = attachments.resolve_managed_path(item["relative_path"])
        def no_read(path):
            if path == image_path:
                raise PermissionError("injected denied read")
            return original_read(path)
        with patch.object(Path, "read_bytes", no_read):
            assert attachments.list_attachments(1)[0]["availability"] == "unreadable"
            expect_error(lambda: attachments.read_attachment(1, item["id"]))
        cancelled = attachments.stage_attachment(1, "取消.csv", b"x\n9", uploaded_by="检验员")
        cancelled_path = attachments.resolve_managed_path(cancelled["relative_path"])
        assert attachments.discard_staged_attachments(1, [cancelled["id"]]) == 1
        os.utime(cancelled_path, (1, 1))
        os.utime(image_path, (1, 1))
        assert attachments.cleanup_unreferenced_uploads() == 1
        assert not cancelled_path.exists() and image_path.exists()


def rewrite_zip(source, target, *, transform=None, extra=None, omit=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as modified:
        for name in original.namelist():
            if name == omit:
                continue
            data = original.read(name)
            modified.writestr(name, transform(name, data) if transform else data)
        if extra:
            modified.writestr(extra, b"escape")


def test_complete_backup_restore_migration_and_corruption():
    with IsolatedStorage() as context:
        first = attachments.stage_attachment(1, "复测.pdf", PDF, uploaded_by="检验员")
        second = attachments.stage_attachment(1, "结果.xlsx", xlsx_bytes(), uploaded_by="检验员")
        context.save([first["id"], second["id"]])
        backup = storage.create_database_backup(context.root / "backups")
        assert backup.target_path.suffix == ".zip"
        assert storage.validate_backup_file(backup.target_path)[0]
        with zipfile.ZipFile(backup.target_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert len(manifest["files"]) == 2
        new_dir = context.root / "moved"
        new_dir.mkdir()
        old_path = database.get_db_path()
        moved = storage.migrate_database_to_directory(new_dir)
        database.refresh_db_path_from_config()
        assert old_path.exists() and database.get_db_path().resolve() == moved.target_path.resolve()
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        assert attachments.read_attachment(1, second["id"], 2) == xlsx_bytes()
        with database.atomic_write() as connection:
            connection.execute("DELETE FROM storage_test_report")
        restored = storage.restore_database_from_backup_file(backup.target_path)
        assert restored.protection_backup_path.exists()
        with database.get_connection() as connection:
            assert bytes(connection.execute("SELECT pdf FROM storage_test_report").fetchone()[0]) == PDF
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        invalid = context.root / "invalid.zip"
        rewrite_zip(backup.target_path, invalid,
                    transform=lambda name, data: b"corrupted" if name.startswith("files/") else data)
        expect_error(lambda: storage.restore_database_from_backup_file(invalid))
        assert attachments.read_attachment(1, first["id"], 2) == PDF
        traversal = context.root / "traversal.zip"
        rewrite_zip(backup.target_path, traversal, extra="../escaped.csv")
        assert not storage.validate_backup_file(traversal)[0]
        expect_error(lambda: storage.restore_database_from_backup_file(traversal))
        assert not (context.root / "escaped.csv").exists()
        incomplete = context.root / "incomplete.zip"
        rewrite_zip(backup.target_path, incomplete, omit="files/" + first["relative_path"])
        assert not storage.validate_backup_file(incomplete)[0]
        expect_error(lambda: storage.migrate_database_to_directory(new_dir))


def test_raw_database_restore_warns_and_keeps_sources():
    with IsolatedStorage() as context:
        item = attachments.stage_attachment(1, "data.csv", b"x\n1", uploaded_by="检验员")
        context.save([item["id"]])
        raw = context.root / "database_only.db"
        storage._copy_database_snapshot(database.get_db_path(), raw, overwrite=False)
        assert storage.validate_backup_file(raw)[0]
        target = context.root / "independent" / "qc.db"
        database.DB_PATH = target
        database.init_db()
        result = storage.restore_database_from_backup_file(raw)
        assert result.warnings and "未提供附件" in result.warnings[0]
        assert attachments.list_attachments(1, 2)[0]["availability"] == "missing"
        assert attachments.resolve_managed_path(item["relative_path"], db_path=context.root / "original" / "qc.db").exists()


def test_good_backup_repairs_missing_and_corrupt_current_files():
    with IsolatedStorage() as context:
        first = attachments.stage_attachment(1, "复测.pdf", PDF, uploaded_by="检验员")
        second = attachments.stage_attachment(1, "结果.xlsx", xlsx_bytes(), uploaded_by="检验员")
        context.save([first["id"], second["id"]])
        complete = storage.create_database_backup(context.root / "backups")
        attachments.resolve_managed_path(first["relative_path"]).write_bytes(b"external corrupt bytes")
        attachments.resolve_managed_path(second["relative_path"]).unlink()
        repaired = storage.restore_database_from_backup_file(complete.target_path)
        assert attachments.read_attachment(1, first["id"]) == PDF
        assert attachments.read_attachment(1, second["id"]) == xlsx_bytes()
        assert repaired.warnings and "2份资料缺失或损坏" in repaired.warnings[0]
        with zipfile.ZipFile(repaired.protection_backup_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert not manifest["complete"] and len(manifest["missing_files"]) == 2
        originals = list((attachments.get_managed_files_directory() / "recovery_originals").rglob("*.pdf"))
        assert len(originals) == 1 and originals[0].read_bytes() == b"external corrupt bytes"
        assert storage.validate_backup_file(repaired.protection_backup_path)[0]


def test_configuration_copy_and_restore_switch_rollback():
    with IsolatedStorage() as context:
        item = attachments.stage_attachment(1, "data.csv", b"x\n1", uploaded_by="检验员")
        context.save([item["id"]])
        original_db = database.get_db_path()
        database.save_db_path_config(original_db)
        original_config = database.get_storage_config_path().read_bytes()
        target = context.root / "failed-move"
        target.mkdir()
        real_save = database.save_db_path_config
        def fail_config(path):
            real_save(path)
            raise OSError("injected config switch failure")
        with patch.object(database, "save_db_path_config", side_effect=fail_config):
            expect_error(lambda: storage.migrate_database_to_directory(target))
        assert database.get_storage_config_path().read_bytes() == original_config
        assert not (target / original_db.name).exists()
        assert attachments.read_attachment(1, item["id"]) == b"x\n1"

        copy_target = context.root / "copy-failure"
        copy_target.mkdir()
        real_copyfile = shutil.copyfile
        def fail_copy(source, destination, *args, **kwargs):
            if ".qc-migration-" in str(destination) and "/files/" in str(destination):
                raise OSError("injected file copy failure")
            return real_copyfile(source, destination, *args, **kwargs)
        with patch.object(shutil, "copyfile", side_effect=fail_copy):
            expect_error(lambda: storage.migrate_database_to_directory(copy_target))
        assert database.get_storage_config_path().read_bytes() == original_config
        assert not (copy_target / original_db.name).exists()
        backup = storage.create_database_backup(context.root / "backups")
        with database.atomic_write() as connection:
            connection.execute("UPDATE storage_test_report SET pdf=?", (b"current-report",))
        real_snapshot = storage._copy_database_snapshot
        failed = False
        def fail_after_switch(source, destination, *, overwrite):
            nonlocal failed
            real_snapshot(source, destination, overwrite=overwrite)
            if Path(source).name == "data.db" and Path(destination) == original_db and not failed:
                failed = True
                raise OSError("injected after database switch")
        with patch.object(storage, "_copy_database_snapshot", side_effect=fail_after_switch):
            expect_error(lambda: storage.restore_database_from_backup_file(backup.target_path))
        with database.get_connection() as connection:
            assert bytes(connection.execute("SELECT pdf FROM storage_test_report").fetchone()[0]) == b"current-report"
        assert attachments.read_attachment(1, item["id"]) == b"x\n1"


def test_real_event_revision_and_archived_pdf_round_trip():
    from services import out_of_control_service as events
    from services import out_of_control_report_service as reports
    from tests.out_of_control_service_smoke_test import seed_engineering_sources
    with IsolatedStorage() as context:
        with database.atomic_write() as connection:
            connection.execute("DELETE FROM qc_ooc_revisions")
            connection.execute("DELETE FROM qc_ooc_events")
        sources = seed_engineering_sources()
        event = events.open_event(*sources["lj"], "attachment-integration-open", "检验员")
        uploaded = attachments.stage_attachment(event["event_id"], "复测资料.csv", "检测值\n10\n".encode(), uploaded_by="检验员")
        assert attachments.list_attachments(event["event_id"]) == []
        events.save_handling(event["event_id"], event["revision_no"], "attachment-integration-save", "save_draft",
                             {**event["content"], "attachment_ids": [uploaded["id"]]}, "检验员")
        assert attachments.read_attachment(event["event_id"], uploaded["id"]) == "检测值\n10\n".encode()
        archived = reports.generate_event_report(event["event_id"])
        assert archived["package"]["attachments"][0]["id"] == uploaded["id"]
        pdf_bytes = reports.read_event_report(archived["report_id"])
        assert pdf_bytes.startswith(b"%PDF-")
        complete = storage.create_database_backup(context.root / "integration-backup")
        database.DB_PATH = context.root / "fresh-install" / "qc.db"
        database.init_db()
        storage.restore_database_from_backup_file(complete.target_path)
        assert reports.read_event_report(archived["report_id"]) == pdf_bytes
        assert attachments.read_attachment(event["event_id"], uploaded["id"]) == "检测值\n10\n".encode()
        assert events.get_event(event["event_id"])["revision_no"] == 2


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(json.dumps({"result": "passed", "isolated_groups": len(tests), "real_database_used": False}))
