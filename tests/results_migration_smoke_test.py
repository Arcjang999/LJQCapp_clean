from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import create_batch, create_project, get_connection, init_db


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "results_migration_smoke_test.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def _downgrade_results_table_to_legacy_schema(batch_id: int) -> None:
    with get_connection() as connection:
        connection.execute("DROP TABLE results")
        connection.execute(
            """
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                test_time TEXT NOT NULL,
                operator TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL,
                log_value REAL,
                reagent_lot_changed INTEGER NOT NULL DEFAULT 0,
                manual_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            INSERT INTO results (
                batch_id,
                test_time,
                operator,
                value,
                log_value,
                reagent_lot_changed,
                manual_note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(batch_id),
                "2026-04-13 08:00:00",
                "legacy-user",
                123.45,
                2.0915,
                1,
                "legacy note",
                "2026-04-13 08:01:02",
            ),
        )


def test_legacy_results_table_migrates_and_app_starts() -> None:
    with TemporaryDatabaseContext():
        project_id = create_project("Migration Smoke Project", input_value_type="raw")
        batch_id = create_batch(
            instrument="Migration Inst",
            reagent="Migration Reagent",
            qc_material="Migration QC",
            concentration="Normal",
            lot_no="MIG-LOT-001",
            target_n=20,
            project_id=project_id,
        )
        _downgrade_results_table_to_legacy_schema(batch_id)

        init_db()

        with get_connection() as connection:
            result_columns = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(results)").fetchall()
            ]
            expected_columns = [
                "id",
                "batch_id",
                "test_time",
                "operator",
                "value",
                "log_value",
                "reagent_lot_changed",
                "is_building_included",
                "is_outlier_suspect",
                "outlier_status",
                "outlier_method",
                "grubbs_statistic",
                "grubbs_threshold",
                "manual_status",
                "handled_at",
                "manual_note",
                "created_at",
            ]
            assert result_columns == expected_columns

            migrated_row = connection.execute(
                """
                SELECT *
                FROM results
                ORDER BY id ASC
                """
            ).fetchone()

        assert migrated_row is not None
        assert int(migrated_row["batch_id"]) == batch_id
        assert str(migrated_row["operator"]) == "legacy-user"
        assert float(migrated_row["value"]) == 123.45
        assert int(migrated_row["reagent_lot_changed"]) == 1
        assert int(migrated_row["is_building_included"]) == 1
        assert int(migrated_row["is_outlier_suspect"]) == 0
        assert str(migrated_row["outlier_status"]) == "normal"
        assert str(migrated_row["outlier_method"]) == ""
        assert migrated_row["grubbs_statistic"] is None
        assert migrated_row["grubbs_threshold"] is None
        assert str(migrated_row["manual_status"]) == "normal"
        assert migrated_row["handled_at"] is None
        assert str(migrated_row["manual_note"]) == "legacy note"
        assert str(migrated_row["created_at"]) == "2026-04-13 08:01:02"

        at = AppTest.from_file(APP_FILE_PATH)
        at.run()
        assert not list(at.exception)
        assert at.radio(key="top_level_method_selector").value == "主页"


def run_all_tests() -> None:
    test_legacy_results_table_migrates_and_app_starts()
    print("PASS test_legacy_results_table_migrates_and_app_starts")
    print("All 1 results migration smoke tests passed.")


if __name__ == "__main__":
    run_all_tests()
