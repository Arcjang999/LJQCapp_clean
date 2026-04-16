from __future__ import annotations

from io import BytesIO
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdf
import pandas as pd
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    create_zscore_batch,
    create_zscore_project,
    init_db,
    list_report_exports,
)
from services.report_service import (
    REPORT_TYPE_ZSCORE_MONTHLY,
    ZSCORE_METHOD_LABEL,
    ZSCORE_REPORT_TITLE,
    build_zscore_monthly_preview_summary,
    build_zscore_monthly_report_package,
    build_zscore_monthly_report_pdf,
    list_zscore_report_month_options,
    save_zscore_monthly_report_snapshot,
)
from services.settings_service import REPORT_SETTINGS_FALLBACKS, save_report_settings_form
from tests.report_pdf_assertions import assert_uniform_a4_pages_without_watermark
from zscore_logic import create_zscore_run, get_template_id_for_level_count, get_zscore_runs


ZSCORE_PAGE_APPTEST_SCRIPT = f"""
import sys
from pathlib import Path

ROOT = Path({str(PROJECT_ROOT)!r})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.zscore_page import render_zscore_page

render_zscore_page()
"""


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "zscore_monthly_report_smoke_test.db"
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


def _make_level_results(level_1: float, level_2: float) -> list[dict[str, float]]:
    return [
        {"level_id": "Level 1", "raw_value": float(level_1)},
        {"level_id": "Level 2", "raw_value": float(level_2)},
    ]


def _make_three_level_results(level_1: float, level_2: float, level_3: float) -> list[dict[str, float]]:
    return [
        {"level_id": "Level 1", "raw_value": float(level_1)},
        {"level_id": "Level 2", "raw_value": float(level_2)},
        {"level_id": "Level 3", "raw_value": float(level_3)},
    ]


def seed_zscore_batch_with_formal_monthly_data() -> tuple[int, int]:
    project_id = create_zscore_project("Z-score 月报项目", level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="AU5800",
        reagent="Chemistry Reagent",
        qc_material="Control A",
        concentration="Normal",
        lot_no="ZS-202604",
        target_n=5,
        level_1_label="水平 1",
        level_2_label="水平 2",
        cv_limit=5.0,
    )
    template_id = get_template_id_for_level_count(2)

    building_runs = [
        ("2026-03-27 08:00:00", (100.0, 150.0), ""),
        ("2026-03-28 08:00:00", (100.2, 150.2), ""),
        ("2026-03-29 08:00:00", (99.8, 149.8), ""),
        ("2026-03-30 08:00:00", (100.1, 150.1), ""),
        ("2026-03-31 08:00:00", (99.9, 149.9), ""),
    ]
    formal_runs = [
        ("2026-04-01 08:00:00", (100.0, 150.0), "month starts in control"),
        ("2026-04-02 08:00:00", (100.4, 150.0), "建议查看run级规则证据"),
        ("2026-04-03 08:00:00", (100.6, 150.0), ""),
    ]

    for test_time, values, note in building_runs + formal_runs:
        create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator="tester",
            level_results=_make_level_results(*values),
            template_id=template_id,
            required_n=5,
            manual_note=note,
        )
    return project_id, batch_id


def seed_zscore_batch_with_building_only_data() -> tuple[int, int]:
    project_id = create_zscore_project("Z-score Building Only", level_count=2, input_value_type="ct")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="QS-7",
        reagent="PCR Reagent",
        qc_material="Ct Control",
        concentration="Normal",
        lot_no="ZS-BUILD",
        target_n=5,
        level_1_label="Low",
        level_2_label="High",
    )
    template_id = get_template_id_for_level_count(2)
    for index, values in enumerate([(24.5, 26.0), (24.6, 26.1), (24.4, 25.9)], start=1):
        create_zscore_run(
            batch_id=batch_id,
            test_time=f"2026-04-0{index} 09:00:00",
            operator="builder",
            level_results=_make_level_results(*values),
            template_id=template_id,
            required_n=5,
        )
    return project_id, batch_id


def seed_three_level_zscore_batch_without_abnormal() -> tuple[int, int]:
    project_id = create_zscore_project("三水平 Z-score 项目", level_count=3, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="AU680",
        reagent="Chemistry Reagent 3L",
        qc_material="Control C",
        concentration="3 Levels",
        lot_no="ZS-3L-202604",
        target_n=5,
        level_1_label="水平 1",
        level_2_label="水平 2",
        level_3_label="水平 3",
        cv_limit=5.0,
    )
    template_id = get_template_id_for_level_count(3)

    building_runs = [
        ("2026-03-27 08:00:00", (100.0, 150.0, 200.0)),
        ("2026-03-28 08:00:00", (100.2, 150.2, 200.2)),
        ("2026-03-29 08:00:00", (99.8, 149.8, 199.8)),
        ("2026-03-30 08:00:00", (100.1, 150.1, 200.1)),
        ("2026-03-31 08:00:00", (99.9, 149.9, 199.9)),
    ]
    formal_runs = [
        ("2026-04-01 08:00:00", (100.0, 150.0, 200.0)),
        ("2026-04-02 08:00:00", (100.3, 150.1, 200.2)),
        ("2026-04-03 08:00:00", (99.9, 149.8, 199.7)),
    ]

    for test_time, values in building_runs + formal_runs:
        create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator="tester-3l",
            level_results=_make_three_level_results(*values),
            template_id=template_id,
            required_n=5,
        )
    return project_id, batch_id


def test_zscore_monthly_report_builds_pdf_and_snapshot() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_zscore_batch_with_formal_monthly_data()
        package = build_zscore_monthly_report_package(batch_id, "2026-04")
        preview_summary = dict(build_zscore_monthly_preview_summary(package.report))
        template_id = get_template_id_for_level_count(2)
        monthly_runs = [
            run
            for run in get_zscore_runs(batch_id, template_id)
            if str(pd.Timestamp(run["test_time"]).to_period("M")) == "2026-04"
        ]
        abnormal_run_map = {
            int(run.get("test_sequence") or run.get("run_id") or run.get("id") or 0): run
            for run in monthly_runs
            if str(run.get("run_status") or "") in {"warning", "reject"}
        }

        assert package.report.title == ZSCORE_REPORT_TITLE
        assert package.report.method_label == ZSCORE_METHOD_LABEL
        assert package.report.report_period_label == "2026-04-01 至 2026-04-30"
        assert package.report.input_value_type_label == "真实检测值"
        assert package.report.basic_info.level_count_label == "2 水平"
        assert package.report.statistics.formal_count == 3
        assert package.report.statistics.in_control_count == 1
        assert package.report.statistics.warning_count == 1
        assert package.report.statistics.out_of_control_count == 1
        assert preview_summary["当前阶段"] == "正式质控"
        assert preview_summary["全部水平已完成建靶"] == "是"
        assert len(package.report.level_statistics) == 2
        assert all(item.monthly_count == 3 for item in package.report.level_statistics)
        assert len(package.report.abnormal_records) == 2
        assert "本次检测结论为最终判定" in package.report.abnormal_summary_text
        assert "说明规则触发情况" in package.report.abnormal_summary_text
        for record in package.report.abnormal_records:
            source_run = abnormal_run_map[record.run_sequence]
            expected_conclusion = {
                "warning": "警告",
                "reject": "失控",
            }[str(source_run.get("run_status") or "")]
            assert record.run_conclusion == expected_conclusion
            assert "水平" in record.level_evidence
            assert record.level_evidence != record.run_conclusion
            assert "run" not in record.level_evidence.lower()
            assert "level" not in record.level_evidence.lower()
        assert package.report.abnormal_records[0].manual_note == "建议查看本次检测规则触发证据"
        assert package.report.corrective_actions == ["建议查看本次检测规则触发证据", "未填写"]
        assert "失控检测记录" in package.report.overview_text
        assert "多水平月度质控报告" in package.report.file_name
        assert not package.monthly_plot_df.empty

        pdf_bytes = build_zscore_monthly_report_pdf(package)
        assert pdf_bytes.startswith(b"%PDF")

        reader = assert_uniform_a4_pages_without_watermark(pdf_bytes)
        pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages).lower()
        assert len(reader.pages) == 6
        assert str(reader.metadata.get("/Subject", "")) == REPORT_TYPE_ZSCORE_MONTHLY
        assert "run级" not in pdf_text
        assert "level明细" not in pdf_text
        assert "查看run" not in pdf_text
        assert " run " not in f" {pdf_text} "
        assert "level " not in f" {pdf_text} "

        snapshot_id = save_zscore_monthly_report_snapshot(package)
        assert snapshot_id > 0

        report_exports_df = list_report_exports(
            report_type=REPORT_TYPE_ZSCORE_MONTHLY,
            batch_id=batch_id,
        )
        assert len(report_exports_df) == 1
        latest_export = report_exports_df.iloc[0]
        assert latest_export["report_month"] == "2026-04"
        assert latest_export["formal_count"] == 3
        assert latest_export["warning_count"] == 1
        assert latest_export["out_of_control_count"] == 1
        assert latest_export["summary_json"]["title"] == ZSCORE_REPORT_TITLE
        assert latest_export["summary_json"]["report_period_label"] == "2026-04-01 至 2026-04-30"
        assert latest_export["summary_json"]["abnormal_records"][0]["run_conclusion"] in {"警告", "失控"}
        assert "水平" in latest_export["summary_json"]["abnormal_records"][0]["level_evidence"]
        assert (
            latest_export["summary_json"]["abnormal_records"][0]["manual_note"]
            == "建议查看本次检测规则触发证据"
        )


def test_zscore_monthly_report_requires_formal_data() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_zscore_batch_with_building_only_data()
        month_options = list_zscore_report_month_options(batch_id)
        assert month_options == ["2026-04"]
        try:
            build_zscore_monthly_report_package(batch_id, "2026-04")
        except ValueError as exc:
            assert "所选月份无正式期数据" in str(exc)
        else:
            raise AssertionError("expected build_zscore_monthly_report_package to reject building-only months")


def test_zscore_monthly_report_outputs_three_single_level_chart_pages() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_three_level_zscore_batch_without_abnormal()
        package = build_zscore_monthly_report_package(batch_id, "2026-04")

        assert package.report.basic_info.level_count_label.startswith("3")
        assert len(package.active_levels) == 3
        assert len(package.report.level_statistics) == 3
        assert package.report.abnormal_records == []

        pdf_bytes = build_zscore_monthly_report_pdf(package)
        reader = assert_uniform_a4_pages_without_watermark(pdf_bytes)

        assert len(reader.pages) == 6


def test_zscore_monthly_report_page_exposes_generate_and_download_flow() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = seed_zscore_batch_with_formal_monthly_data()
        report_scope = f"zscore_monthly_report_{batch_id}"
        at = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT)
        at.session_state["zscore_selected_project_id"] = project_id
        at.session_state["zscore_selected_batch_id"] = batch_id
        at.run()

        assert not list(at.exception)
        assert any(selectbox.key == f"{report_scope}_month" for selectbox in at.selectbox)

        at.selectbox(key=f"{report_scope}_month").set_value("2026-04").run()
        at.button(key=f"{report_scope}_generate").click().run(timeout=10)

        assert not list(at.exception)
        download_elements = at.get("download_button")
        caption_values = [str(item.value) for item in at.caption]
        abnormal_tables = [
            dataframe.value
            for dataframe in at.dataframe
            if {
                "检测时间",
                "检测序号",
                "本次检测结论",
                "各水平触发证据",
                "手动备注",
            }.issubset(set(dataframe.value.columns))
        ]
        assert any(element.label == "下载 PDF" for element in download_elements)
        assert any("报告期间：2026-04-01 至 2026-04-30" in value for value in caption_values)
        assert not any("run" in value.lower() or "level" in value.lower() for value in caption_values)
        assert abnormal_tables
        abnormal_df = abnormal_tables[0]
        assert len(abnormal_df) == 2
        assert set(abnormal_df["本次检测结论"].tolist()) == {"警告", "失控"}
        assert all("水平" in str(value) for value in abnormal_df["各水平触发证据"].tolist())
        assert not any("run" in str(column).lower() or "level" in str(column).lower() for column in abnormal_df.columns)
        assert "建议查看本次检测规则触发证据" in abnormal_df["手动备注"].tolist()
        assert not any("run" in str(value).lower() for value in abnormal_df["手动备注"].tolist())


def test_zscore_monthly_report_reads_saved_system_settings_and_falls_back_on_empty_values() -> None:
    with TemporaryDatabaseContext():
        save_report_settings_form(
            {
                "lab_name": "StarLab",
                "department_name": "Chem Immuno",
                "qc_owner_name": "Carol QC",
                "reviewer_name": "David Review",
                "report_statement": "Monthly multi-level report for QC archive only.",
            }
        )
        _project_id, batch_id = seed_zscore_batch_with_formal_monthly_data()
        package = build_zscore_monthly_report_package(batch_id, "2026-04")

        assert package.report.basic_info.lab_name == "StarLab"
        assert package.report.basic_info.department_name == "Chem Immuno"
        assert package.report.basic_info.qc_owner_name == "Carol QC"
        assert package.report.basic_info.reviewer_name == "David Review"
        assert package.report.declaration == "Monthly multi-level report for QC archive only."

        pdf_bytes = build_zscore_monthly_report_pdf(package)
        assert pdf_bytes.startswith(b"%PDF")

        save_report_settings_form(
            {
                "lab_name": "",
                "department_name": "",
                "qc_owner_name": "",
                "reviewer_name": "",
                "report_statement": "",
            }
        )
        fallback_package = build_zscore_monthly_report_package(batch_id, "2026-04")
        assert fallback_package.report.basic_info.lab_name == REPORT_SETTINGS_FALLBACKS["lab_name"]
        assert fallback_package.report.basic_info.department_name == REPORT_SETTINGS_FALLBACKS["department_name"]
        assert fallback_package.report.basic_info.qc_owner_name == REPORT_SETTINGS_FALLBACKS["qc_owner_name"]
        assert fallback_package.report.basic_info.reviewer_name == REPORT_SETTINGS_FALLBACKS["reviewer_name"]
        assert fallback_package.report.declaration == REPORT_SETTINGS_FALLBACKS["report_statement"]


if __name__ == "__main__":
    test_zscore_monthly_report_builds_pdf_and_snapshot()
    test_zscore_monthly_report_requires_formal_data()
    test_zscore_monthly_report_outputs_three_single_level_chart_pages()
    test_zscore_monthly_report_page_exposes_generate_and_download_flow()
    test_zscore_monthly_report_reads_saved_system_settings_and_falls_back_on_empty_values()
    print("zscore_monthly_report_smoke_test passed")
