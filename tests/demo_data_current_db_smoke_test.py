from __future__ import annotations

import gc
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import create_project, get_connection, init_db
from scripts.generate_demo_qc_data import DEFAULT_SEED, load_demo_data


class TemporaryCurrentAppDbContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory(ignore_cleanup_errors=True)
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
        gc.collect()
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def _list_project_names() -> list[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name FROM projects ORDER BY id ASC"
        ).fetchall()
    return [str(row["name"]) for row in rows]


def _list_instant_project_names() -> list[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name FROM instant_projects ORDER BY id ASC"
        ).fetchall()
    return [str(row["name"]) for row in rows]


def test_current_app_db_mode_creates_backup_and_preserves_real_data() -> None:
    with TemporaryCurrentAppDbContext() as context:
        current_db_path = context.root / "current-app-data" / "qc_lj_app.db"
        database.save_db_path_config(current_db_path)
        resolved_path = database.refresh_db_path_from_config()
        assert resolved_path == current_db_path

        init_db()
        create_project("REAL-LAB-PROJECT", input_value_type="raw")

        result = load_demo_data(use_current_app_db=True, seed=DEFAULT_SEED, on_conflict="append")

        assert result.db_path == current_db_path.resolve()
        assert result.backup_path is not None
        assert result.backup_path.exists()
        assert result.backup_path.parent == current_db_path.parent.resolve()
        assert "storage configuration" in result.resolution_detail.lower()

        config_payload = json.loads(database.get_storage_config_path().read_text(encoding="utf-8"))
        assert config_payload["database_path"] == str(current_db_path.resolve())

        project_names = _list_project_names()
        instant_project_names = _list_instant_project_names()
        assert "REAL-LAB-PROJECT" in project_names
        assert any(name.startswith("[DEMO] LJ") for name in project_names)
        assert any(name.startswith("[DEMO] ZS") for name in project_names)
        assert any(name.startswith("[DEMO] Instant") for name in instant_project_names)

        with sqlite3.connect(str(result.backup_path)) as backup_connection:
            backup_names = [
                str(row[0])
                for row in backup_connection.execute("SELECT name FROM projects ORDER BY id ASC").fetchall()
            ]
        assert backup_names == ["REAL-LAB-PROJECT"]


def test_replace_demo_only_does_not_delete_non_demo_projects() -> None:
    with TemporaryCurrentAppDbContext() as context:
        current_db_path = context.root / "configured" / "qc_lj_app.db"
        database.save_db_path_config(current_db_path)
        database.refresh_db_path_from_config()
        init_db()
        create_project("REAL-PROJECT-KEEP", input_value_type="raw")

        load_demo_data(use_current_app_db=True, seed=DEFAULT_SEED, on_conflict="append")
        append_result = load_demo_data(use_current_app_db=True, seed=DEFAULT_SEED, on_conflict="append")
        assert any(summary.action == "appended" for summary in append_result.summaries)

        replace_result = load_demo_data(use_current_app_db=True, seed=DEFAULT_SEED, on_conflict="replace-demo-only")
        assert replace_result.backup_path is not None and replace_result.backup_path.exists()

        project_names = _list_project_names()
        instant_project_names = _list_instant_project_names()
        assert "REAL-PROJECT-KEEP" in project_names
        assert not any(name.endswith("#2") and name.startswith("[DEMO] LJ") for name in project_names)
        assert not any(name.endswith("#2") and name.startswith("[DEMO] ZS") for name in project_names)
        assert not any(name.endswith("#2") and name.startswith("[DEMO] Instant") for name in instant_project_names)


if __name__ == "__main__":
    test_current_app_db_mode_creates_backup_and_preserves_real_data()
    test_replace_demo_only_does_not_delete_non_demo_projects()
    print("demo_data_current_db_smoke_test passed")
