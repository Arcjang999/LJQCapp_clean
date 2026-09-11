from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database
from database import get_connection, get_zscore_batch, init_db, create_zscore_project, create_zscore_batch
from services.project_config_service import (
    activate_lot_config, set_lot_config_disabled, set_project_template_disabled, save_lot_item_levels,
    create_lot_config_from_template, list_lot_config_items,
)
from services.master_data_service import create_qc_lot, create_qc_level
from services.zscore_workbench_service import (
    sync_zscore_workbench_bindings, list_zscore_workbench_projects, list_zscore_workbench_batches,
)
from services.report_service import build_zscore_monthly_report_package, build_zscore_monthly_report_pdf, save_zscore_monthly_report_snapshot
from tests.zscore_v12_fixtures import seed_zscore_configuration
from zscore_logic import create_zscore_run, get_zscore_runs, get_template_id_for_level_count, disable_zscore_building_run, restore_zscore_building_run


class IsolatedDatabase:
    def __enter__(self):
        self.temp = TemporaryDirectory()
        self.original = database.DB_PATH, database.LEGACY_DB_CANDIDATES
        database.DB_PATH = Path(self.temp.name) / "integration.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, *args):
        database.DB_PATH, database.LEGACY_DB_CANDIDATES = self.original
        self.temp.cleanup()


def add_run(fixture, index, level_count=2, outlier=False):
    offsets = [0, 1, -1, .5, -.5, .1, .2]
    return create_zscore_run(
        batch_id=fixture["batch_id"], test_time=f"2026-09-{index+1:02d} 09:00:00", operator="集成测试",
        level_results=[{"level_id": f"Level {i+1}", "raw_value": 100 * (i+1) + (20 if outlier and i == 0 else offsets[index % len(offsets)])} for i in range(level_count)],
        template_id=get_template_id_for_level_count(level_count), required_n=5,
    )


def test_two_and_three_levels_build_formal_and_report():
    for count in (2, 3):
        with IsolatedDatabase():
            fixture = seed_zscore_configuration(level_count=count)
            batch = get_zscore_batch(fixture["batch_id"])
            for index in range(5):
                assert add_run(fixture, index, count)["phase"] == "target_building"
            formal = add_run(fixture, 5, count, outlier=True)
            assert formal["phase"] == "formal_qc" and formal["run_status"] == "reject"
            before = get_zscore_runs(fixture["batch_id"], get_template_id_for_level_count(count))
            for _ in range(3):
                assert sync_zscore_workbench_bindings() == []
            with get_connection() as connection:
                for table in ("projects", "batches", "qc_workbench_bindings"):
                    assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
            assert len(get_zscore_runs(fixture["batch_id"], get_template_id_for_level_count(count))) == len(before)
            package = build_zscore_monthly_report_package(fixture["batch_id"], "2026-09")
            info = package.report.basic_info
            assert info.unit_symbol == "mg/L" and info.detection_method == "V12 Z-score 检测法"
            assert info.instrument == batch["instrument"] and info.reagent == batch["reagent"]
            assert info.qc_material == batch["qc_material"] and info.lot_no == batch["lot_no"]
            assert info.config_snapshot_id and info.target_source_label == "新版配置：本批次建靶值"
            assert len(package.active_levels) == count
            assert build_zscore_monthly_report_pdf(package).startswith(b"%PDF")
            save_zscore_monthly_report_snapshot(package)


def test_ineligible_and_legacy_projects_stay_hidden():
    with IsolatedDatabase():
        legacy = create_zscore_project("旧测试项目", 2)
        create_zscore_batch(project_id=legacy, instrument="旧", reagent="旧", qc_material="旧", concentration="旧", lot_no="OLD", target_n=5)
        manual = seed_zscore_configuration(name="人工靶值", target_source="manual")
        assert manual["batch_id"] is not None
        issues = sync_zscore_workbench_bindings()
        assert not issues
        assert list_zscore_workbench_projects()["name"].tolist()==["人工靶值"]
        fixture = seed_zscore_configuration(name="新版配置", instrument="新版配置仪器")
        with get_connection() as connection:
            connection.execute("UPDATE md_qc_levels SET is_disabled = 1 WHERE id = ?", (fixture["level_ids"][0],))
        issues = sync_zscore_workbench_bindings()
        assert any("有效水平" in issue["issue"] for issue in issues)
        assert list_zscore_workbench_projects()["name"].tolist()==["人工靶值"]
        with get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 3


def test_disable_reactivate_preserves_results_and_frozen_snapshot():
    with IsolatedDatabase():
        fixture = seed_zscore_configuration()
        add_run(fixture, 0)
        original = dict(get_zscore_batch(fixture["batch_id"]))
        set_lot_config_disabled(fixture["config_id"], is_disabled=True, reason="回归测试")
        sync_zscore_workbench_bindings()
        assert list_zscore_workbench_projects().empty
        set_lot_config_disabled(fixture["config_id"], is_disabled=False)
        activate_lot_config(fixture["config_id"])
        assert sync_zscore_workbench_bindings() == []
        assert list_zscore_workbench_batches(fixture["project_id"])["id"].tolist() == [fixture["batch_id"]]
        set_project_template_disabled(fixture["template_id"], is_disabled=True, reason="模板停用")
        sync_zscore_workbench_bindings()
        assert list_zscore_workbench_projects().empty
        set_project_template_disabled(fixture["template_id"], is_disabled=False)
        from services.project_config_service import activate_project_template
        activate_project_template(fixture["template_id"])
        with get_connection() as connection:
            connection.execute("UPDATE md_test_items SET chinese_name = '已改名' WHERE id = ?", (fixture["test_item_id"],))
        activate_lot_config(fixture["config_id"])
        sync_zscore_workbench_bindings()
        after = dict(get_zscore_batch(fixture["batch_id"]))
        assert after["project_name"] == original["project_name"]
        assert after["config_snapshot_id"] == original["config_snapshot_id"]
        assert len(get_zscore_runs(fixture["batch_id"], "2_level_classic")) == 1


def test_run_maintenance_and_reordered_levels_cannot_reinterpret_results():
    with IsolatedDatabase():
        fixture = seed_zscore_configuration()
        for index in range(3):
            add_run(fixture, index)
        runs = get_zscore_runs(fixture["batch_id"], "2_level_classic")
        run_id = runs[0]["run_id"]
        disable_zscore_building_run(run_id)
        with get_connection() as connection:
            flags = connection.execute("SELECT is_building_included FROM zscore_level_results WHERE run_id = ?", (run_id,)).fetchall()
            assert [row[0] for row in flags] == [0, 0]
        restore_zscore_building_run(run_id)
        old_labels = get_zscore_batch(fixture["batch_id"])["level_1_label"]
        save_lot_item_levels(fixture["item_id"], list(reversed(fixture["assignments"])))
        activate_lot_config(fixture["config_id"])
        issues = sync_zscore_workbench_bindings()
        assert any("已有检测记录" in issue["issue"] for issue in issues)
        assert list_zscore_workbench_projects().empty
        assert get_zscore_batch(fixture["batch_id"])["level_1_label"] == old_labels
        assert len(get_zscore_runs(fixture["batch_id"], "2_level_classic")) == 3


def test_multiple_lots_reuse_project_with_separate_results():
    with IsolatedDatabase():
        fixture = seed_zscore_configuration()
        add_run(fixture, 0)
        with get_connection() as connection:
            material = connection.execute("SELECT qc_material_id FROM qc_lot_configs WHERE id = ?", (fixture["config_id"],)).fetchone()[0]
        lot = create_qc_lot(qc_material_id=material, lot_no="SECOND", expiry_date="2029-01-01")
        levels = [create_qc_level(qc_material_lot_id=lot, level_name=f"新水平{i+1}", level_order=i+1) for i in range(2)]
        config = create_lot_config_from_template(template_id=fixture["template_id"], qc_material_lot_id=lot, config_name="第二批号")
        item = int(list_lot_config_items(config).iloc[0]["id"])
        save_lot_item_levels(item, [{"qc_level_id": level, "target_source": "building"} for level in levels])
        activate_lot_config(config)
        sync_zscore_workbench_bindings()
        assert len(list_zscore_workbench_projects()) == 1
        batches = list_zscore_workbench_batches(fixture["project_id"])
        assert len(batches) == 2
        new_batch = next(i for i in batches.id if i != fixture["batch_id"])
        assert get_zscore_runs(new_batch, "2_level_classic") == []


def test_input_value_type_is_inherited_and_locked():
    for value_type in ("ct", "log"):
        with IsolatedDatabase():
            fixture = seed_zscore_configuration(input_value_type=value_type)
            assert get_zscore_batch(fixture["batch_id"])["input_value_type"] == value_type
            # Simulate a configuration import changing a bound project's type before entry.
            with get_connection() as connection:
                connection.execute("UPDATE qc_lot_config_items SET input_value_type = 'raw' WHERE id = ?", (fixture["item_id"],))
            activate_lot_config(fixture["config_id"])
            issues = sync_zscore_workbench_bindings()
            assert any("输入值类型和水平数不能变更" in issue["issue"] for issue in issues)
            assert list_zscore_workbench_projects().empty
            assert get_zscore_batch(fixture["batch_id"])["input_value_type"] == value_type


def test_page_selection_and_disabled_selection_are_consistent():
    with IsolatedDatabase():
        fixture = seed_zscore_configuration(level_count=3)
        second = seed_zscore_configuration(name="第二个项目", instrument="第二台仪器")
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10)
        at.session_state["top_level_method_selector"] = "多水平（Z-score法）"
        at.run()
        assert not list(at.exception)
        assert not any(button.label in ("创建项目", "创建批次") for button in at.button)
        selector = at.selectbox(key="v12_zscore_project_selector")
        selector.set_value(selector.options[1]).run()
        selector = at.selectbox(key="v12_zscore_batch_selector")
        selector.set_value(selector.options[1]).run()
        assert not list(at.exception)
        assert at.session_state["zscore_selected_batch_id"] == fixture["batch_id"]
        text = " ".join(str(item.value) for item in at.markdown) + " " + " ".join(item.proto.body for item in at.get("html"))
        assert "mg/L" in text and "V12 Z-score 检测法" in text
        selector = at.selectbox(key="v12_zscore_project_selector")
        selector.set_value(next(option for option in selector.options if "第二个项目" in option)).run()
        assert at.session_state["zscore_selected_project_id"] == second["project_id"]
        assert at.session_state["zscore_selected_batch_id"] is None
        assert at.selectbox(key="v12_zscore_batch_selector").value == "请选择已启用的批号配置"
        selector = at.selectbox(key="v12_zscore_project_selector")
        selector.set_value(next(option for option in selector.options if "V12 Z-score" in option)).run()
        selector = at.selectbox(key="v12_zscore_batch_selector")
        selector.set_value(selector.options[1]).run()
        set_lot_config_disabled(fixture["config_id"], is_disabled=True, reason="停用")
        at.run()
        assert not list(at.exception)
        assert at.session_state["zscore_selected_batch_id"] is None
        assert not any(button.label == "保存本次检测" for button in at.button)


if __name__ == "__main__":
    tests = [test_two_and_three_levels_build_formal_and_report, test_ineligible_and_legacy_projects_stay_hidden,
             test_disable_reactivate_preserves_results_and_frozen_snapshot,
             test_run_maintenance_and_reordered_levels_cannot_reinterpret_results,
             test_multiple_lots_reuse_project_with_separate_results,
             test_input_value_type_is_inherited_and_locked,
             test_page_selection_and_disabled_selection_are_consistent]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.2 Z-score integration smoke tests passed.")
