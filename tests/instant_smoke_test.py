from __future__ import annotations

import sys
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    create_batch,
    create_instant_batch,
    create_instant_project,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    get_batch,
    get_instant_batch,
    get_instant_results,
    get_results,
    init_db,
)
from pages.lj_sections import build_lj_workbench_context
from plotting import plot_instant_chart
from services.instant_service import (
    build_instant_workbench_context,
    calculate_instant_si_test,
    confirm_instant_transfer_to_lj,
    disable_instant_result,
    keep_instant_result,
    restore_instant_result,
    save_instant_result,
)


BASE_TIME = "2026-04-13 08:{:02d}:00"
INSTANT_PAGE_APPTEST_SCRIPT = f"""
import sys
from pathlib import Path

ROOT = Path({str(PROJECT_ROOT)!r})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.instant_page import render_instant_page

render_instant_page()
"""

LJ_PAGE_APPTEST_SCRIPT = f"""
import sys
from pathlib import Path

ROOT = Path({str(PROJECT_ROOT)!r})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.lj_page import render_lj_page

render_lj_page()
"""

ZSCORE_PAGE_APPTEST_SCRIPT = f"""
import sys
from pathlib import Path

ROOT = Path({str(PROJECT_ROOT)!r})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.zscore_page import render_zscore_page

render_zscore_page()
"""

APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "instant_smoke_test.db"
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


def bootstrap_batch(
    *,
    project_name: str,
    input_value_type: str = "raw",
) -> tuple[int, int]:
    project_id = create_instant_project(project_name, input_value_type=input_value_type)
    batch_id = create_instant_batch(
        project_id=project_id,
        instrument="Inst-A",
        reagent="Reagent-A",
        qc_material="QC-A",
        concentration="Normal",
        lot_no=f"{project_name[:8]}-LOT",
    )
    return project_id, batch_id


def seed_instant_results(
    batch_id: int,
    values: list[float],
    *,
    operator_prefix: str = "tester",
) -> None:
    for minute, value in enumerate(values, start=0):
        save_instant_result(
            batch_id=batch_id,
            test_time=BASE_TIME.format(minute),
            operator=f"{operator_prefix}-{minute}",
            value=float(value),
            log_value=None,
        )


def test_instant_si_starts_after_third_effective_point() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant Raw Project", input_value_type="raw")
        save_instant_result(
            batch_id=batch_id,
            test_time=BASE_TIME.format(0),
            operator="tester-1",
            value=100.0,
            log_value=2.0,
        )
        save_instant_result(
            batch_id=batch_id,
            test_time=BASE_TIME.format(1),
            operator="tester-2",
            value=101.0,
            log_value=2.004321,
        )

        context = build_instant_workbench_context(batch_id)
        assert context["summary"]["effective_count"] == 2
        assert context["summary"]["latest_status"] == "继续累计"
        assert context["summary"]["si_ready"] is False

        save_instant_result(
            batch_id=batch_id,
            test_time=BASE_TIME.format(2),
            operator="tester-3",
            value=102.0,
            log_value=2.0086,
        )
        context = build_instant_workbench_context(batch_id)
        assert context["summary"]["effective_count"] == 3
        assert context["summary"]["si_ready"] is True
        assert context["summary"]["si_upper"] == 1.0
        assert context["summary"]["si_lower"] == 1.0
        assert context["summary"]["si_n2s"] == 1.15
        assert context["summary"]["si_n3s"] == 1.16
        assert context["summary"]["latest_status"] == "有效点"
        analysis_df = context["analysis_df"]
        assert analysis_df["si_n3s"].notna().all()


def test_calculate_instant_si_test_cases() -> None:
    early = calculate_instant_si_test([100, 101])
    assert early["evaluation_ready"] is False
    assert early["status"] == "accumulating"

    in_control = calculate_instant_si_test([100, 101, 102])
    assert in_control["evaluation_ready"] is True
    assert in_control["mean"] == 101.0
    assert in_control["sd"] == 1.0
    assert in_control["si_upper"] == 1.0
    assert in_control["si_lower"] == 1.0
    assert in_control["n2s"] == 1.15
    assert in_control["n3s"] == 1.16
    assert in_control["status"] == "in_control"

    warning = calculate_instant_si_test([95, 95, 96, 100])
    assert math.isclose(float(warning["si_upper"]), 1.4703, rel_tol=1e-4)
    assert warning["n2s"] == 1.46
    assert warning["n3s"] == 1.49
    assert warning["status"] == "warning"

    high_reject = calculate_instant_si_test([100, 100, 100, 120])
    assert high_reject["status"] == "reject"
    assert high_reject["trigger_side"] == "max"
    assert high_reject["is_suspect"] is True
    assert high_reject["si_upper"] == 1.5

    low_reject = calculate_instant_si_test([80, 100, 100, 100])
    assert low_reject["status"] == "reject"
    assert low_reject["trigger_side"] == "min"
    assert low_reject["is_suspect"] is True
    assert low_reject["si_lower"] == 1.5

    over_table = calculate_instant_si_test([100 + index for index in range(21)])
    assert over_table["evaluation_ready"] is False
    assert over_table["reason"] == "over_table_limit"
    assert over_table["n2s"] is None
    assert over_table["n3s"] is None


def test_instant_summary_exposes_si_method_and_parameters() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant Meta Project", input_value_type="raw")
        for minute, value in enumerate([100.0, 101.5, 103.0], start=0):
            save_instant_result(
                batch_id=batch_id,
                test_time=BASE_TIME.format(minute),
                operator=f"tester-{minute}",
                value=value,
                log_value=None,
            )

        context = build_instant_workbench_context(batch_id)
        summary = context["summary"]
        assert summary["instant_method_label"] == "即刻法 SI 值判定"
        assert summary["instant_method_formula"] == "SI上限 = (X最大值 - x̄) / s；SI下限 = (x̄ - X最小值) / s"
        assert summary["si_ready"] is True
        meta_labels = [label for label, _ in summary["latest_meta"]]
        for required_label in ["n", "均值", "SD", "SI上限", "SI下限", "n2s", "n3s"]:
            assert required_label in meta_labels


def test_ct_label_and_chart_axis_follow_project_value_type() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant Ct Project", input_value_type="ct")
        for minute, value in enumerate([24.5, 24.7, 24.6], start=0):
            save_instant_result(
                batch_id=batch_id,
                test_time=BASE_TIME.format(minute),
                operator=f"ct-tester-{minute}",
                value=value,
                log_value=None,
            )

        context = build_instant_workbench_context(batch_id)
        assert context["input_value_type_label"] == "Ct值"
        figure = plot_instant_chart(
            context["analysis_df"],
            context["summary"],
            "Ct Figure",
            y_axis_label=context["input_value_type_label"],
        )
        assert figure.axes[0].get_ylabel() == "Ct值"
        plt.close(figure)


def test_disable_restore_and_transfer_hint() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant 20 Project", input_value_type="raw")
        for minute, value in enumerate(range(100, 120), start=0):
            save_instant_result(
                batch_id=batch_id,
                test_time=BASE_TIME.format(minute),
                operator=f"tester-{minute}",
                value=float(value),
                log_value=None,
            )

        context = build_instant_workbench_context(batch_id)
        assert context["summary"]["effective_count"] == 20
        assert context["summary"]["transfer_ready"] is True
        assert context["summary"]["transfer_message"] == "已达到 20 个有效点，可确认转入 LJ 法。"

        target_result_id = int(context["analysis_df"].iloc[0]["id"])
        disable_instant_result(target_result_id)
        context = build_instant_workbench_context(batch_id)
        assert context["summary"]["effective_count"] == 19
        assert context["summary"]["disabled_count"] == 1
        assert context["summary"]["transfer_ready"] is False

        restore_instant_result(target_result_id)
        context = build_instant_workbench_context(batch_id)
        assert context["summary"]["effective_count"] == 20
        assert context["summary"]["transfer_ready"] is True


def test_name_validation_scopes_are_method_and_project_local() -> None:
    with TemporaryDatabaseContext():
        lj_project_id = create_project("同名项目", input_value_type="raw")
        zscore_project_id = create_zscore_project("同名项目", level_count=2, input_value_type="raw")
        instant_project_id = create_instant_project("同名项目", input_value_type="raw")
        assert lj_project_id > 0
        assert zscore_project_id > 0
        assert instant_project_id > 0

        for create_same_method_project in [
            lambda: create_project("同名项目", input_value_type="raw"),
            lambda: create_zscore_project("同名项目", level_count=2, input_value_type="raw"),
            lambda: create_instant_project("同名项目", input_value_type="raw"),
        ]:
            try:
                create_same_method_project()
            except ValueError:
                pass
            else:
                raise AssertionError("同一方法学下的重复项目名称应被拦截。")

        lj_project_b = create_project("LJ-项目-B", input_value_type="raw")
        create_batch(
            instrument="Inst-LJ-A",
            reagent="Reagent-LJ-A",
            qc_material="QC-LJ-A",
            concentration="Normal",
            lot_no="LOT-SHARED",
            target_n=20,
            project_id=lj_project_id,
        )
        create_batch(
            instrument="Inst-LJ-B",
            reagent="Reagent-LJ-B",
            qc_material="QC-LJ-B",
            concentration="Normal",
            lot_no="LOT-SHARED",
            target_n=20,
            project_id=lj_project_b,
        )
        try:
            create_batch(
                instrument="Inst-LJ-C",
                reagent="Reagent-LJ-C",
                qc_material="QC-LJ-C",
                concentration="Normal",
                lot_no="LOT-SHARED",
                target_n=20,
                project_id=lj_project_id,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("同一项目下的重复批次名称应被拦截。")

        zscore_project_b = create_zscore_project("Z-score-项目-B", level_count=2, input_value_type="raw")
        create_zscore_batch(
            instrument="Inst-Z-A",
            reagent="Reagent-Z-A",
            qc_material="QC-Z-A",
            concentration="High",
            lot_no="Z-LOT-SHARED",
            target_n=20,
            project_id=zscore_project_id,
        )
        create_zscore_batch(
            instrument="Inst-Z-B",
            reagent="Reagent-Z-B",
            qc_material="QC-Z-B",
            concentration="High",
            lot_no="Z-LOT-SHARED",
            target_n=20,
            project_id=zscore_project_b,
        )
        try:
            create_zscore_batch(
                instrument="Inst-Z-C",
                reagent="Reagent-Z-C",
                qc_material="QC-Z-C",
                concentration="High",
                lot_no="Z-LOT-SHARED",
                target_n=20,
                project_id=zscore_project_id,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("同一 Z-score 项目下的重复批次名称应被拦截。")

        instant_project_b = create_instant_project("即时法-项目-B", input_value_type="raw")
        create_instant_batch(
            project_id=instant_project_id,
            instrument="Inst-I-A",
            reagent="Reagent-I-A",
            qc_material="QC-I-A",
            concentration="Low",
            lot_no="I-LOT-SHARED",
        )
        create_instant_batch(
            project_id=instant_project_b,
            instrument="Inst-I-B",
            reagent="Reagent-I-B",
            qc_material="QC-I-B",
            concentration="Low",
            lot_no="I-LOT-SHARED",
        )
        try:
            create_instant_batch(
                project_id=instant_project_id,
                instrument="Inst-I-C",
                reagent="Reagent-I-C",
                qc_material="QC-I-C",
                concentration="Low",
                lot_no="I-LOT-SHARED",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("同一即时法项目下的重复批次名称应被拦截。")


def test_confirm_transfer_to_lj_with_exactly_twenty_effective_points() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant Transfer Exact", input_value_type="raw")
        seed_instant_results(batch_id, [100.0 + 0.2 * minute for minute in range(20)])

        preview_context = build_instant_workbench_context(batch_id)
        assert preview_context["transfer_state"]["eligible"] is True

        transfer_result = confirm_instant_transfer_to_lj(batch_id)
        assert transfer_result["transferred_effective_count"] == 20
        assert transfer_result["building_count"] == 20
        assert transfer_result["formal_count"] == 0

        source_batch = get_instant_batch(batch_id)
        assert source_batch["transfer_status"] == "transferred"
        assert int(source_batch["transferred_to_lj_batch_id"]) == transfer_result["target_batch_id"]

        lj_batch = get_batch(transfer_result["target_batch_id"])
        assert lj_batch["source_method"] == "instant"
        assert int(lj_batch["source_instant_batch_id"]) == batch_id
        assert int(lj_batch["source_instant_project_id"]) == int(source_batch["project_id"])

        lj_results = get_results(transfer_result["target_batch_id"], include_manual_note=True)
        assert len(lj_results) == 20

        lj_context = build_lj_workbench_context(transfer_result["target_batch_id"])
        assert lj_context["stats"]["target_ready"] is True
        assert int((lj_context["qc_df"]["phase"] == "建靶数据").sum()) == 20
        assert int((lj_context["qc_df"]["phase"] == "正式数据").sum()) == 0

        try:
            confirm_instant_transfer_to_lj(batch_id)
        except ValueError as exc:
            assert "已转入 LJ 法" in str(exc)
        else:
            raise AssertionError("同一即时法批次转入成功后，不应允许再次转入。")


def test_confirm_transfer_to_lj_splits_building_and_formal_points() -> None:
    with TemporaryDatabaseContext():
        _, batch_id = bootstrap_batch(project_name="Instant Transfer Formal", input_value_type="ct")
        seed_instant_results(batch_id, [25.0 + 0.1 * minute for minute in range(23)], operator_prefix="ct-user")

        transfer_result = confirm_instant_transfer_to_lj(batch_id)
        assert transfer_result["building_count"] == 20
        assert transfer_result["formal_count"] == 3

        lj_context = build_lj_workbench_context(transfer_result["target_batch_id"])
        qc_df = lj_context["qc_df"].sort_values(["test_time", "id"]).reset_index(drop=True)
        assert len(qc_df) == 23
        assert int((qc_df["phase"] == "建靶数据").sum()) == 20
        assert int((qc_df["phase"] == "正式数据").sum()) == 3
        assert str(lj_context["input_value_type"]) == "ct"
        assert str(lj_context["input_value_type_label"]) == "Ct值"


def test_transfer_excludes_disabled_points_and_blocks_pending_outliers() -> None:
    with TemporaryDatabaseContext():
        _, blocked_batch_id = bootstrap_batch(project_name="Instant Pending Review", input_value_type="raw")
        blocked_values = [100.0 + 0.05 * minute for minute in range(19)] + [130.0]
        seed_instant_results(blocked_batch_id, blocked_values, operator_prefix="blocked")

        blocked_context = build_instant_workbench_context(blocked_batch_id)
        assert blocked_context["summary"]["pending_outlier_review_count"] == 1
        assert blocked_context["transfer_state"]["eligible"] is False
        suspect_rows = blocked_context["analysis_df"][
            (blocked_context["analysis_df"]["is_outlier_suspect"] == 1)
            & (blocked_context["analysis_df"]["manual_status"] == "pending_review")
        ]
        assert len(suspect_rows) == 1
        try:
            confirm_instant_transfer_to_lj(blocked_batch_id)
        except ValueError as exc:
            assert "待处理疑似离群点" in str(exc)
        else:
            raise AssertionError("存在待处理疑似离群点时，应拒绝转入。")

        keep_instant_result(int(suspect_rows.iloc[0]["id"]))
        kept_context = build_instant_workbench_context(blocked_batch_id)
        assert kept_context["summary"]["pending_outlier_review_count"] == 0
        assert kept_context["transfer_state"]["eligible"] is True

        _, filtered_batch_id = bootstrap_batch(project_name="Instant Disabled Point", input_value_type="raw")
        seed_instant_results(filtered_batch_id, [100.0 + 0.25 * minute for minute in range(21)], operator_prefix="filtered")
        first_context = build_instant_workbench_context(filtered_batch_id)
        disabled_result_id = int(first_context["analysis_df"].iloc[0]["id"])
        disable_instant_result(disabled_result_id)

        transfer_result = confirm_instant_transfer_to_lj(filtered_batch_id)
        lj_results = get_results(transfer_result["target_batch_id"], include_manual_note=True)
        assert len(lj_results) == 20
        transferred_values = lj_results["value"].round(4).tolist()
        assert round(float(first_context["analysis_df"].iloc[0]["value"]), 4) not in transferred_values


def test_instant_page_entry_save_round_trip() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = bootstrap_batch(project_name="Instant AppTest", input_value_type="raw")
        save_instant_result(
            batch_id=batch_id,
            test_time=BASE_TIME.format(0),
            operator="seed-user",
            value=120.0,
            log_value=None,
        )
        at = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT)
        at.session_state["instant_selected_project_id"] = project_id
        at.session_state["instant_selected_batch_id"] = batch_id
        at.run()
        assert any(expander.label == "即刻法 SI 值说明" for expander in at.expander)

        at.selectbox(key="instant_entry_operator").set_value("seed-user").run()
        at.text_input(key="instant_entry_value").set_value("123.456").run()
        at.button(key="instant_entry_save_button").click().run()

        results_df = get_instant_results(batch_id)
        assert len(results_df) == 2
        matched_df = results_df[results_df["value"].round(3) == 123.456]
        assert len(matched_df) == 1
        assert str(matched_df.iloc[0]["operator"]) == "seed-user"
        assert not list(at.exception)


def test_instant_page_uses_business_labels_and_single_judgment_area() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = bootstrap_batch(project_name="AlphaProject", input_value_type="ct")
        for minute, value in enumerate([100.0, 110.0, 105.0, 99.0, 90.0, 85.0, 103.0, 60.0], start=0):
            save_instant_result(
                batch_id=batch_id,
                test_time=BASE_TIME.format(minute),
                operator="tester",
                value=value,
                log_value=None,
            )

        at = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT)
        at.session_state["instant_selected_project_id"] = project_id
        at.session_state["instant_selected_batch_id"] = batch_id
        at.run()

        assert len(at.warning) == 0
        project_options = list(at.selectbox(key="instant_project_selector").options)
        batch_options = list(at.selectbox(key="instant_batch_selector").options)
        assert project_options == ["请选择即时法项目", "AlphaProject | Ct值"]
        assert batch_options[0] == "请选择即时法批次"
        assert batch_options[1].startswith("质控批号：AlphaPro-LOT")
        assert "项目 1" not in batch_options[1]
        assert "批次 1" not in batch_options[1]

        project_table = at.dataframe[0].value
        batch_table = at.dataframe[1].value
        assert list(project_table.columns) == ["项目名称", "输入值类型", "创建时间"]
        assert list(batch_table.columns) == ["质控品批号", "仪器", "试剂", "质控品", "浓度", "创建时间"]
        assert "编号" not in project_table.columns
        assert "编号" not in batch_table.columns

        caption_values = [item.value for item in at.caption]
        assert not any("当前批次：1" in value for value in caption_values)
        assert any("质控批号 AlphaPro-LOT" in value for value in caption_values)


def test_transferred_instant_page_is_read_only_and_lj_page_shows_source() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = bootstrap_batch(project_name="Instant Transfer UI", input_value_type="raw")
        seed_instant_results(batch_id, [100.0 + 0.15 * minute for minute in range(20)], operator_prefix="ui-user")
        transfer_result = confirm_instant_transfer_to_lj(batch_id)

        instant_at = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT)
        instant_at.session_state["instant_selected_project_id"] = project_id
        instant_at.session_state["instant_selected_batch_id"] = batch_id
        instant_at.run()

        button_keys = [button.key for button in instant_at.button]
        assert "instant_entry_save_button" not in button_keys
        assert any("已转入 LJ 法" in str(item.value) for item in instant_at.success)
        assert any(button.key == "instant_go_to_transferred_lj_batch" for button in instant_at.button)

        lj_at = AppTest.from_string(LJ_PAGE_APPTEST_SCRIPT)
        lj_at.session_state["selected_project_id"] = transfer_result["target_project_id"]
        lj_at.session_state["selected_batch_id"] = transfer_result["target_batch_id"]
        lj_at.run()
        assert any("来源：即时法" in str(item.value) for item in lj_at.info)
        assert any("转入时间" in str(item.value) for item in lj_at.info)


def test_instant_transfer_navigation_uses_pending_intent_and_opens_target_lj_batch() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = bootstrap_batch(project_name="Instant Nav UI", input_value_type="raw")
        seed_instant_results(batch_id, [100.0 + 0.12 * minute for minute in range(20)], operator_prefix="nav-user")
        transfer_result = confirm_instant_transfer_to_lj(batch_id)

        at = AppTest.from_file(APP_FILE_PATH)
        at.session_state["top_level_method_selector"] = "即时法"
        at.session_state["instant_selected_project_id"] = project_id
        at.session_state["instant_selected_batch_id"] = batch_id
        at.run()
        assert not list(at.exception)

        at.button(key="instant_go_to_transferred_lj_batch").click().run()
        assert not list(at.exception)
        assert at.radio(key="top_level_method_selector").value == "单水平（LJ法）"
        assert int(at.session_state["selected_project_id"]) == transfer_result["target_project_id"]
        assert int(at.session_state["selected_batch_id"]) == transfer_result["target_batch_id"]
        filtered_state = at.session_state.filtered_state
        assert filtered_state.get("pending_navigation_source") in (None, "")
        assert filtered_state.get("pending_lj_project_id") in (None, "")
        assert filtered_state.get("pending_lj_batch_id") in (None, "")
        assert any("来源：即时法" in str(item.value) for item in at.info)

        reopened_at = AppTest.from_file(APP_FILE_PATH)
        reopened_at.session_state["top_level_method_selector"] = "单水平（LJ法）"
        reopened_at.session_state["selected_project_id"] = transfer_result["target_project_id"]
        reopened_at.session_state["selected_batch_id"] = transfer_result["target_batch_id"]
        reopened_at.run()
        assert not list(reopened_at.exception)
        assert reopened_at.radio(key="top_level_method_selector").value == "单水平（LJ法）"
        assert int(reopened_at.session_state["selected_project_id"]) == transfer_result["target_project_id"]
        assert int(reopened_at.session_state["selected_batch_id"]) == transfer_result["target_batch_id"]
        assert reopened_at.session_state.filtered_state.get("pending_navigation_source") in (None, "")


def test_lj_page_uses_business_labels_and_marks_instant_origin() -> None:
    with TemporaryDatabaseContext():
        create_project("LJ普通项目", input_value_type="raw")
        project_id, batch_id = bootstrap_batch(project_name="TransferUI", input_value_type="raw")
        seed_instant_results(batch_id, [100.0 + 0.1 * minute for minute in range(20)], operator_prefix="origin")
        transfer_result = confirm_instant_transfer_to_lj(batch_id)

        at = AppTest.from_string(LJ_PAGE_APPTEST_SCRIPT)
        at.session_state["selected_project_id"] = transfer_result["target_project_id"]
        at.session_state["selected_batch_id"] = transfer_result["target_batch_id"]
        at.run()

        project_options = list(at.selectbox(key="v12_lj_project_selector").options)
        batch_options = list(at.selectbox(key="v12_lj_batch_selector").options)
        assert not any(option.startswith("项目 ") for option in project_options[1:])
        assert any("由即时法转入" in option for option in project_options[1:])
        assert not any("LJ普通项目" in option for option in project_options[1:])
        assert not any(option.startswith("批次 ") for option in batch_options[1:])
        assert any("由即时法转入" in option for option in batch_options[1:])
        assert any(option.startswith("质控批号：Transfer-LOT") for option in batch_options[1:])

        batch_tables = [
            element.value
            for element in at.dataframe
            if "质控品批号" in element.value.columns and "来源" in element.value.columns
        ]
        assert batch_tables
        batch_table = batch_tables[0]
        assert "编号" not in batch_table.columns
        assert batch_table["来源"].tolist() == ["由即时法转入"]
        assert any("来源：即时法" in str(item.value) for item in at.info)
        expander_states = {str(expander.label): bool(expander.proto.expanded) for expander in at.expander}
        assert expander_states["当前批次检测记录"] is False
        assert expander_states["导出与导入"] is False


def test_zscore_page_uses_business_labels_in_management_and_context() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("ZAlpha", level_count=2, input_value_type="ct")
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Z-Inst",
            reagent="Z-Reagent",
            qc_material="Z-QC",
            concentration="High",
            lot_no="ZLOT-01",
            target_n=20,
            level_1_label="低值",
            level_2_label="高值",
        )

        at = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT)
        at.session_state["zscore_selected_project_id"] = project_id
        at.session_state["zscore_selected_batch_id"] = batch_id
        at.run()

        project_options = list(at.selectbox(key="zscore_project_selector").options)
        batch_options = list(at.selectbox(key="zscore_batch_selector").options)
        assert project_options == ["请选择 Z-score 项目", "ZAlpha | 2 水平 | Ct值"]
        assert batch_options[0] == "请选择 Z-score 批次"
        assert batch_options[1].startswith("质控批号：ZLOT-01")
        assert "项目 1" not in project_options[1]
        assert "批次 1" not in batch_options[1]

        project_table = at.dataframe[0].value
        batch_table = at.dataframe[1].value
        assert list(project_table.columns) == ["项目名称", "水平数", "输入值类型", "创建时间"]
        assert list(batch_table.columns) == ["质控品批号", "水平数", "仪器", "试剂", "质控品", "浓度", "创建时间"]
        assert "编号" not in project_table.columns
        assert "编号" not in batch_table.columns

        text_values = [str(item.value) for item in at.text]
        assert any("质控品批号：ZLOT-01" in value for value in text_values)
        assert not any("批次：1" in value for value in text_values)


def run_all_tests() -> None:
    test_functions = [
        test_instant_si_starts_after_third_effective_point,
        test_calculate_instant_si_test_cases,
        test_instant_summary_exposes_si_method_and_parameters,
        test_ct_label_and_chart_axis_follow_project_value_type,
        test_disable_restore_and_transfer_hint,
        test_name_validation_scopes_are_method_and_project_local,
        test_confirm_transfer_to_lj_with_exactly_twenty_effective_points,
        test_confirm_transfer_to_lj_splits_building_and_formal_points,
        test_transfer_excludes_disabled_points_and_blocks_pending_outliers,
        test_instant_page_entry_save_round_trip,
        test_instant_page_uses_business_labels_and_single_judgment_area,
        test_transferred_instant_page_is_read_only_and_lj_page_shows_source,
        test_instant_transfer_navigation_uses_pending_intent_and_opens_target_lj_batch,
        test_lj_page_uses_business_labels_and_marks_instant_origin,
        test_zscore_page_uses_business_labels_in_management_and_context,
    ]
    for test_func in test_functions:
        test_func()
        print(f"PASS {test_func.__name__}")
    print(f"All {len(test_functions)} instant smoke tests passed.")


if __name__ == "__main__":
    run_all_tests()
