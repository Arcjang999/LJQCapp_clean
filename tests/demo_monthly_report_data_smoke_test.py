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
from services.demo_data_service import (
    DEMO_PREFIX,
    delete_demo_data,
    seed_demo_data,
    validate_demo_data,
)
from services.report_service import (
    build_lj_monthly_report_package,
    build_zscore_monthly_report_package,
)


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tempdir.name)
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = self.root / "demo_monthly_report_data_smoke.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        gc.collect()
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def _find_batch_id(project_name: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT batches.id
            FROM batches
            INNER JOIN projects ON projects.id = batches.project_id
            WHERE projects.name = ?
            """,
            (project_name,),
        ).fetchone()
    assert row is not None, project_name
    return int(row["id"])


def _table_count(table_name: str) -> int:
    with get_connection() as connection:
        row = connection.execute(f"SELECT COUNT(1) AS count_value FROM {table_name}").fetchone()
    return int(row["count_value"])


def _demo_project_count() -> int:
    with get_connection() as connection:
        project_count = connection.execute(
            "SELECT COUNT(1) AS count_value FROM projects WHERE name LIKE ?",
            (f"{DEMO_PREFIX}%",),
        ).fetchone()["count_value"]
        instant_count = connection.execute(
            "SELECT COUNT(1) AS count_value FROM instant_projects WHERE name LIKE ?",
            (f"{DEMO_PREFIX}%",),
        ).fetchone()["count_value"]
    return int(project_count) + int(instant_count)


def _assert_lj_report(project_name: str, month: str, *, abnormal_expected: bool) -> None:
    package = build_lj_monthly_report_package(_find_batch_id(project_name), month)
    assert package.report.statistics.formal_count == 20
    if abnormal_expected:
        assert package.report.abnormal_records
        assert all(record.manual_note.strip() for record in package.report.abnormal_records)
        assert package.report.corrective_actions
        assert "未填写" not in "\n".join(package.report.corrective_actions)


def _assert_zscore_report(project_name: str, month: str, *, abnormal_expected: bool) -> None:
    package = build_zscore_monthly_report_package(_find_batch_id(project_name), month)
    assert package.report.statistics.formal_count == 20
    if abnormal_expected:
        assert package.report.abnormal_records
        assert all(record.manual_note.strip() for record in package.report.abnormal_records)
        assert package.report.corrective_actions
        assert "未填写" not in "\n".join(package.report.corrective_actions)


def test_full_profile_monthly_report_demo_data() -> None:
    with TemporaryDatabaseContext():
        seed_result = seed_demo_data(profile="full", replace_demo=True)
        assert len(seed_result.datasets) == 8
        assert seed_result.report_snapshots_created == 12

        validation = validate_demo_data(profile="full")
        assert validation.checked_datasets == 8
        assert validation.checked_report_packages == 12
        assert validation.checked_pdf_bytes > 0

        for month in ["2026-04", "2026-05"]:
            _assert_lj_report("【演示】LJ-MR-01 两个月月报-稳定运行", month, abnormal_expected=False)
            _assert_lj_report("【演示】LJ-MR-02 两个月月报-含失控与纠正措施", month, abnormal_expected=True)
            _assert_zscore_report("【演示】Z2-MR-01 双水平两个月月报-稳定运行", month, abnormal_expected=False)
            _assert_zscore_report("【演示】Z2-MR-02 双水平两个月月报-含失控与纠正措施", month, abnormal_expected=True)
            _assert_zscore_report("【演示】Z3-MR-01 三水平两个月月报-稳定运行", month, abnormal_expected=False)
            _assert_zscore_report("【演示】Z3-MR-02 三水平两个月月报-含失控与纠正措施", month, abnormal_expected=True)

        assert _table_count("report_exports") == 12


def test_delete_demo_only_keeps_non_demo_data() -> None:
    with TemporaryDatabaseContext():
        create_project("REAL-PROJECT-KEEP", input_value_type="raw")
        seed_demo_data(profile="full", replace_demo=True)
        assert _demo_project_count() == 8

        delete_result = delete_demo_data()
        assert delete_result.total_projects == 8
        assert _demo_project_count() == 0

        with get_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(1) AS count_value FROM projects WHERE name = ?",
                ("REAL-PROJECT-KEEP",),
            ).fetchone()
        assert int(row["count_value"]) == 1


def test_dry_run_does_not_write_database() -> None:
    with TemporaryDatabaseContext():
        before_counts = {
            table_name: _table_count(table_name)
            for table_name in ["projects", "instant_projects", "batches", "results", "zscore_runs", "report_exports"]
        }
        dry_run_result = seed_demo_data(profile="full", replace_demo=True, dry_run=True)
        after_counts = {
            table_name: _table_count(table_name)
            for table_name in ["projects", "instant_projects", "batches", "results", "zscore_runs", "report_exports"]
        }
        assert dry_run_result.dry_run is True
        assert len(dry_run_result.datasets) == 8
        assert before_counts == after_counts
        assert _demo_project_count() == 0


if __name__ == "__main__":
    test_full_profile_monthly_report_demo_data()
    test_delete_demo_only_keeps_non_demo_data()
    test_dry_run_does_not_write_database()
    print("demo_monthly_report_data_smoke_test passed")
