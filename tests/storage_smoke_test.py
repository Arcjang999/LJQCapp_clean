from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import create_project, get_connection, init_db
from services.storage_service import (
    create_database_backup,
    migrate_database_to_directory,
    restore_database_from_backup_file,
    validate_sqlite_database,
)


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryStorageContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self._original_db_path = database.DB_PATH
        self._original_default_db_path = database.DEFAULT_DB_PATH
        self._original_storage_config_path = database.STORAGE_CONFIG_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)

        database.DEFAULT_DB_PATH = self.root / "runtime" / "qc_lj_app.db"
        database.STORAGE_CONFIG_PATH = self.root / "config" / "storage_config.json"
        database.DB_PATH = database.DEFAULT_DB_PATH
        database.LEGACY_DB_CANDIDATES = []
        database.clear_db_path_config()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.DEFAULT_DB_PATH = self._original_default_db_path
        database.STORAGE_CONFIG_PATH = self._original_storage_config_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def test_default_path_startup_without_external_config() -> None:
    with TemporaryStorageContext():
        database.clear_db_path_config()
        resolved_path = database.refresh_db_path_from_config()

        assert resolved_path == database.DEFAULT_DB_PATH
        init_db()
        assert database.get_db_path() == database.get_default_db_path()
        assert database.get_db_path().exists()

        at = AppTest.from_file(APP_FILE_PATH)
        at.run()
        assert not list(at.exception)


def test_database_migration_updates_external_path_config_and_survives_restart() -> None:
    with TemporaryStorageContext() as context:
        init_db()
        old_db_path = database.get_db_path()
        create_project("Storage Migration Project", input_value_type="raw")

        target_dir = context.root / "migrated"
        target_dir.mkdir(parents=True, exist_ok=True)
        result = migrate_database_to_directory(target_dir)

        assert old_db_path.exists()
        assert result.target_path.exists()
        config_payload = json.loads(database.get_storage_config_path().read_text(encoding="utf-8"))
        assert config_payload["database_path"] == str(result.target_path.resolve())

        database.refresh_db_path_from_config()
        assert database.get_db_path() == result.target_path
        init_db()
        with get_connection() as connection:
            rows = connection.execute("SELECT name FROM projects ORDER BY id ASC").fetchall()
        assert [str(row["name"]) for row in rows] == ["Storage Migration Project"]


def test_manual_backup_creates_valid_timestamped_database_file() -> None:
    with TemporaryStorageContext() as context:
        init_db()
        create_project("Backup Project", input_value_type="raw")

        result = create_database_backup(context.root / "manual_backups")

        assert result.target_path.exists()
        assert result.target_path.name.startswith("qc_lj_app_backup_")
        assert result.target_path.suffix == ".db"
        assert result.target_path.stat().st_size > 0
        assert validate_sqlite_database(result.target_path)[0] is True


def test_restore_creates_protection_backup_and_restores_previous_snapshot() -> None:
    with TemporaryStorageContext():
        init_db()
        create_project("Before Restore", input_value_type="raw")
        backup_result = create_database_backup()
        create_project("After Backup", input_value_type="raw")

        with get_connection() as connection:
            names_before_restore = [
                str(row["name"])
                for row in connection.execute("SELECT name FROM projects ORDER BY id ASC").fetchall()
            ]
        assert names_before_restore == ["Before Restore", "After Backup"]

        restore_result = restore_database_from_backup_file(backup_result.target_path)

        assert restore_result.protection_backup_path is not None
        assert restore_result.protection_backup_path.exists()
        with get_connection() as connection:
            names_after_restore = [
                str(row["name"])
                for row in connection.execute("SELECT name FROM projects ORDER BY id ASC").fetchall()
            ]
        assert names_after_restore == ["Before Restore"]

        at = AppTest.from_file(APP_FILE_PATH)
        at.run()
        assert not list(at.exception)


if __name__ == "__main__":
    test_default_path_startup_without_external_config()
    test_database_migration_updates_external_path_config_and_survives_restart()
    test_manual_backup_creates_valid_timestamped_database_file()
    test_restore_creates_protection_backup_and_restores_previous_snapshot()
    print("storage_smoke_test passed")
