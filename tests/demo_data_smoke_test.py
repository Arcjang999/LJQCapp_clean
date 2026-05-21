from __future__ import annotations

import gc
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import create_project, get_connection, init_db
from services.demo_data_service import delete_demo_data, seed_demo_data, validate_demo_data


class TemporaryDatabase:
    def __enter__(self):
        self._tempdir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tempdir.name) / "demo_data_smoke.db"
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = self.db_path
        database.LEGACY_DB_CANDIDATES = []
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        gc.collect()
        self._tempdir.cleanup()


def _table_counts() -> dict[str, int]:
    connection = get_connection()
    try:
        counts = {}
        for table_name in [
            "projects",
            "batches",
            "results",
            "zscore_runs",
            "zscore_level_results",
            "instant_projects",
            "instant_batches",
            "instant_results",
        ]:
            counts[table_name] = int(
                connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()[0]
            )
        return counts
    finally:
        connection.close()


def _project_names() -> list[str]:
    connection = get_connection()
    try:
        rows = connection.execute("SELECT name FROM projects ORDER BY id ASC").fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        connection.close()


def _instant_project_names() -> list[str]:
    connection = get_connection()
    try:
        rows = connection.execute("SELECT name FROM instant_projects ORDER BY id ASC").fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        connection.close()


def _demo_project_count() -> int:
    return sum(name.startswith("【演示】") for name in [*_project_names(), *_instant_project_names()])


def test_demo_data_service_smoke() -> None:
    with TemporaryDatabase():
        init_db()
        create_project("真实项目-保留", input_value_type="raw")

        full_result = seed_demo_data(profile="full", replace_demo=True)
        assert full_result["validation"]["ok"] is True
        assert full_result["created_project_count"] == 12
        assert validate_demo_data(profile="full")["ok"] is True

        delete_result = delete_demo_data()
        assert delete_result["deleted"]["projects"] == 10
        assert delete_result["deleted"]["instant_projects"] == 2
        assert _demo_project_count() == 0
        assert "真实项目-保留" in _project_names()

        basic_result = seed_demo_data(profile="basic")
        assert basic_result["validation"]["ok"] is True
        assert basic_result["created_project_count"] == 6
        assert validate_demo_data(profile="basic")["ok"] is True
        assert "真实项目-保留" in _project_names()

        replace_result = seed_demo_data(profile="basic", replace_demo=True)
        assert replace_result["validation"]["ok"] is True
        assert replace_result["non_demo_preserved"] is True
        assert _demo_project_count() == 6
        assert "真实项目-保留" in _project_names()

        counts_before_dry_run = _table_counts()
        dry_run_result = seed_demo_data(profile="full", replace_demo=True, dry_run=True)
        counts_after_dry_run = _table_counts()
        assert dry_run_result["dry_run"] is True
        assert counts_before_dry_run == counts_after_dry_run

        reset_seed_result = seed_demo_data(profile="basic", reset_all=True)
        assert reset_seed_result["validation"]["ok"] is True
        assert _demo_project_count() == 6
        assert "真实项目-保留" not in _project_names()


if __name__ == "__main__":
    test_demo_data_service_smoke()
    print("demo_data_smoke_test passed")

