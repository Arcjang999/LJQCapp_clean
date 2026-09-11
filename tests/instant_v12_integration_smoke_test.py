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
from database import get_connection, get_instant_batch, get_instant_results, get_batch, get_results, init_db
from services.instant_workbench_service import (
    sync_instant_workbench_bindings, list_instant_workbench_projects, list_instant_workbench_batches,
)
from services.instant_service import (
    save_instant_result, build_instant_workbench_context, confirm_instant_transfer_to_lj, disable_instant_result,
)
from services.project_config_service import (
    activate_lot_config, set_lot_config_disabled, set_project_template_disabled, activate_project_template,
    create_lot_config_from_template, list_lot_config_items, save_lot_item_levels,
)
from services.master_data_service import create_qc_lot, create_qc_level
from services.report_service import build_lj_monthly_report_package, build_lj_monthly_report_pdf
from services.value_type_service import parse_project_input_value
from services.workbench_config_service import list_lj_workbench_batches
from tests.instant_v12_fixtures import seed_instant_configuration


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


def entry(fixture, index, value=None):
    mode = get_instant_batch(fixture["batch_id"])["input_value_type"]
    value = (100 + .1 * index) if value is None else value
    numeric, log, error = parse_project_input_value(str(value), mode)
    assert error is None
    return save_instant_result(batch_id=fixture["batch_id"], test_time=f"2026-09-11 08:{index:02d}:00",
                               operator="集成测试", value=numeric, log_value=log)


def rejected(action, message):
    try:
        action()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("Expected rejection: " + message)


def test_value_types_three_point_rule_and_manual_transfer():
    for mode, base in (("raw", 100), ("ct", 25), ("log", -3)):
        with IsolatedDatabase():
            f = seed_instant_configuration(input_value_type=mode)
            batch = get_instant_batch(f["batch_id"])
            assert batch["input_value_type"] == mode and batch["target_n"] == 20
            assert batch["concentration"] == "常规浓度" and batch["level_name"] == "常规水平"
            for i in range(2):
                entry(f, i, base + .1 * i)
            assert not build_instant_workbench_context(f["batch_id"])["summary"]["si_ready"]
            entry(f, 2, base + .2)
            assert build_instant_workbench_context(f["batch_id"])["summary"]["si_ready"]
            rejected(lambda: confirm_instant_transfer_to_lj(f["batch_id"]), "有效点不足")
            for i in range(3, 21):
                entry(f, i, base + .1 * i)
            assert get_instant_batch(f["batch_id"])["transfer_status"] == "not_transferred"
            result = confirm_instant_transfer_to_lj(f["batch_id"])
            assert result["building_count"] == 20 and result["formal_count"] == 1
            target = get_batch(result["target_batch_id"])
            assert target["input_value_type"] == mode
            source_values = get_instant_results(f["batch_id"])["value"].tolist()
            assert get_results(target["id"])["value"].tolist() == source_values
            rejected(lambda: entry(f, 22, base), "只读")
            rejected(lambda: disable_instant_result(int(get_instant_results(f["batch_id"]).iloc[0]["id"])), "只读")
            rejected(lambda: confirm_instant_transfer_to_lj(f["batch_id"]), "已转入")


def test_ineligible_configs_and_legacy_data_are_preserved():
    with IsolatedDatabase():
        old_project = database.create_instant_project("旧即时法")
        old_batch = database.create_instant_batch(project_id=old_project, instrument="旧", reagent="旧", qc_material="旧", concentration="旧", lot_no="OLD")
        manual = seed_instant_configuration(target_source="manufacturer")
        assert manual["batch_id"] is None and list_instant_workbench_projects().empty
        assert any("本批次建靶" in issue["issue"] for issue in sync_instant_workbench_bindings())
        f = seed_instant_configuration(name="有效项目", instrument="有效仪器")
        with get_connection() as c:
            c.execute("UPDATE md_qc_levels SET is_disabled = 1 WHERE id = ?", (f["level_id"],))
        assert any("有效水平" in issue["issue"] for issue in sync_instant_workbench_bindings())
        assert list_instant_workbench_projects().empty
        assert get_instant_batch(old_batch)["lot_no"] == "OLD"
        with get_connection() as c:
            assert c.execute("SELECT count(*) FROM instant_batches").fetchone()[0] == 2


def test_disable_restore_and_stale_actions():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        for i in range(20):
            entry(f, i)
        set_lot_config_disabled(f["config_id"], is_disabled=True, reason="测试")
        # Reject an already-open page even before the next workbench synchronization.
        rejected(lambda: entry(f, 21), "停用或变更")
        rejected(lambda: confirm_instant_transfer_to_lj(f["batch_id"]), "停用或变更")
        sync_instant_workbench_bindings()
        assert list_instant_workbench_projects().empty
        set_lot_config_disabled(f["config_id"], is_disabled=False)
        activate_lot_config(f["config_id"])
        assert sync_instant_workbench_bindings() == []
        assert list_instant_workbench_batches(f["project_id"])["id"].tolist() == [f["batch_id"]]
        assert len(get_instant_results(f["batch_id"])) == 20
        set_project_template_disabled(f["template_id"], is_disabled=True, reason="测试")
        rejected(lambda: entry(f, 21), "停用或变更")
        sync_instant_workbench_bindings()
        assert list_instant_workbench_projects().empty
        set_project_template_disabled(f["template_id"], is_disabled=False)
        activate_project_template(f["template_id"])
        sync_instant_workbench_bindings()
        with get_connection() as c:
            c.execute("UPDATE md_reagents SET is_disabled = 1 WHERE id = ?", (f["reagent_id"],))
        rejected(lambda: entry(f, 21), "停用或变更")
        sync_instant_workbench_bindings()
        assert list_instant_workbench_projects().empty


def test_frozen_context_survives_dictionary_changes_and_transfer():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        for i in range(21):
            entry(f, i)
        original = dict(get_instant_batch(f["batch_id"]))
        with get_connection() as c:
            c.execute("UPDATE md_test_items SET chinese_name = '项目改名' WHERE id = ?", (f["test_item_id"],))
            c.execute("UPDATE md_units SET symbol = 'changed' WHERE id = ?", (f["unit_id"],))
            c.execute("UPDATE md_methods SET method_name = '方法改名' WHERE id = ?", (f["method_id"],))
            c.execute("UPDATE md_reagents SET generic_name = '试剂改名' WHERE id = ?", (f["reagent_id"],))
            c.execute("UPDATE md_qc_material_lots SET expiry_date = '2030-01-01' WHERE id = ?", (f["lot_id"],))
        activate_lot_config(f["config_id"])
        assert sync_instant_workbench_bindings() == []
        after = dict(get_instant_batch(f["batch_id"]))
        for key in ("project_name", "instrument", "reagent", "qc_material", "unit_symbol", "method_name", "v11_expiry_date", "config_snapshot_id"):
            assert after[key] == original[key], key
        transfer = confirm_instant_transfer_to_lj(f["batch_id"])
        target = get_batch(transfer["target_batch_id"])
        init_db()
        init_db()
        assert len(get_results(target["id"])) == 21
        assert len(get_instant_results(f["batch_id"])) == 21
        assert get_batch(target["id"])["source_config_snapshot_json"] == target["source_config_snapshot_json"]
        assert target["unit_symbol"] == "mg/L" and target["cv_limit"] == 5
        assert target["config_snapshot_id"] == original["config_snapshot_id"]
        frozen = json.loads(target["source_config_snapshot_json"])
        assert frozen["lot_config_item_id"] == f["item_id"]
        with get_connection() as c:
            c.execute("UPDATE instant_projects SET name = '来源项目改名' WHERE id = ?", (f["project_id"],))
            c.execute("UPDATE instant_batches SET lot_no = '来源批号改名' WHERE id = ?", (f["batch_id"],))
        assert get_batch(target["id"])["source_instant_project_name"] == original["project_name"]
        assert get_batch(target["id"])["source_instant_batch_lot_no"] == original["lot_no"]
        set_lot_config_disabled(f["config_id"], is_disabled=True, reason="测试转入后追溯")
        sync_instant_workbench_bindings()
        target_options = list_lj_workbench_batches(transfer["target_project_id"])
        assert target_options.iloc[0]["unit_symbol"] == "mg/L"
        package = build_lj_monthly_report_package(target["id"], "2026-09")
        info = package.report.basic_info
        assert info.unit_symbol == "mg/L" and info.detection_method == "V12 即时法 检测法"
        assert info.project_name == original["project_name"] and info.config_snapshot_id == original["config_snapshot_id"]
        assert "即时法" in info.target_source_label
        assert original["v11_config_name"] in info.target_source_detail
        assert build_lj_monthly_report_pdf(package).startswith(b"%PDF")
        assert package.report.to_snapshot_summary()["basic_info"]["config_snapshot_id"] == original["config_snapshot_id"]


def test_multiple_lots_share_project_without_merging_results():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        for i in range(20):
            entry(f, i)
        first_transfer = confirm_instant_transfer_to_lj(f["batch_id"])
        lot = create_qc_lot(qc_material_id=f["material_id"], lot_no="SECOND", expiry_date="2029-01-01")
        level = create_qc_level(qc_material_lot_id=lot, level_name="新水平", level_order=1)
        config = create_lot_config_from_template(template_id=f["template_id"], qc_material_lot_id=lot, config_name="第二批号")
        item = int(list_lot_config_items(config).iloc[0]["id"])
        save_lot_item_levels(item, [{"qc_level_id": level, "target_source": "building"}])
        activate_lot_config(config)
        for _ in range(3):
            assert sync_instant_workbench_bindings() == []
        assert len(list_instant_workbench_projects()) == 1
        batches = list_instant_workbench_batches(f["project_id"])
        assert len(batches) == 2
        second_batch = next(i for i in batches.id if i != f["batch_id"])
        assert get_instant_results(second_batch).empty
        second = {"batch_id": second_batch}
        for i in range(20):
            entry(second, i)
        second_transfer = confirm_instant_transfer_to_lj(second_batch)
        assert first_transfer["target_project_id"] == second_transfer["target_project_id"]
        assert first_transfer["target_batch_id"] != second_transfer["target_batch_id"]
        assert len(get_results(first_transfer["target_batch_id"])) == 20
        with get_connection() as c:
            assert c.execute("SELECT count(*) FROM instant_projects").fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM instant_batches").fetchone()[0] == 2


def test_changed_identity_is_blocked_and_empty_context_can_refresh():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        with get_connection() as c:
            c.execute("UPDATE md_methods SET method_name = '录入前改名' WHERE id = ?", (f["method_id"],))
        activate_lot_config(f["config_id"])
        sync_instant_workbench_bindings()
        assert get_instant_batch(f["batch_id"])["method_name"] == "录入前改名"
        entry(f, 0)
        with get_connection() as c:
            c.execute("UPDATE qc_lot_config_items SET cv_limit = 10 WHERE id = ?", (f["item_id"],))
        activate_lot_config(f["config_id"])
        assert any("已有检测记录" in issue["issue"] for issue in sync_instant_workbench_bindings())
        assert list_instant_workbench_projects().empty
        assert get_instant_batch(f["batch_id"])["cv_limit"] == 5
        assert len(get_instant_results(f["batch_id"])) == 1
    with IsolatedDatabase():
        f = seed_instant_configuration(input_value_type="ct")
        with get_connection() as c:
            c.execute("UPDATE qc_lot_config_items SET input_value_type = 'raw' WHERE id = ?", (f["item_id"],))
        activate_lot_config(f["config_id"])
        assert any("输入值类型不可更改" in issue["issue"] for issue in sync_instant_workbench_bindings())
        assert get_instant_batch(f["batch_id"])["input_value_type"] == "ct"


def test_same_display_name_does_not_merge_unrelated_transfer_projects():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        unrelated = database.create_project("V12 即时法", input_value_type="raw")
        collision = database.create_project("V12 即时法｜V12 仪器｜即时法转入", input_value_type="raw")
        for i in range(20):
            entry(f, i)
        result = confirm_instant_transfer_to_lj(f["batch_id"])
        assert result["target_project_id"] not in (unrelated, collision)


def test_additive_migration_is_repeatable():
    with IsolatedDatabase():
        project = database.create_project("旧 LJ")
        batch = database.create_batch(project_id=project, instrument="旧仪器", reagent="旧试剂", qc_material="旧质控", concentration="低", lot_no="OLD", target_n=20)
        with get_connection() as c:
            c.execute("ALTER TABLE batches DROP COLUMN source_config_snapshot_json")
            c.execute("DELETE FROM schema_migrations WHERE migration_key = 'v1_2_instant_transfer_snapshot_003'")
        init_db()
        init_db()
        assert get_batch(batch)["source_config_snapshot_json"] == "{}"
        assert get_batch(batch)["instrument"] == "旧仪器"
        with get_connection() as c:
            assert c.execute("SELECT count(*) FROM schema_migrations WHERE migration_key = 'v1_2_instant_transfer_snapshot_003'").fetchone()[0] == 1
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_page_selection_changes_clear_batch_and_disabled_actions():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        second = seed_instant_configuration(name="第二项目", instrument="第二仪器")
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10)
        at.session_state["top_level_method_selector"] = "即时法"
        at.run()
        assert not list(at.exception)
        assert not any(button.label in ("创建项目", "创建批次") for button in at.button)
        selector = at.selectbox(key="v12_instant_project_selector")
        selector.set_value(selector.options[1]).run()
        selector = at.selectbox(key="v12_instant_batch_selector")
        selector.set_value(selector.options[1]).run()
        assert at.session_state["instant_selected_batch_id"] == f["batch_id"]
        text = " ".join(item.proto.body for item in at.get("html"))
        assert "mg/L" in text and "V12 即时法 检测法" in text
        selector = at.selectbox(key="v12_instant_project_selector")
        selector.set_value(next(option for option in selector.options if "第二项目" in option)).run()
        assert at.session_state["instant_selected_project_id"] == second["project_id"]
        assert at.session_state["instant_selected_batch_id"] is None
        assert at.selectbox(key="v12_instant_batch_selector").value == "请选择已启用的批次"
        selector = at.selectbox(key="v12_instant_batch_selector")
        selector.set_value(selector.options[1]).run()
        set_lot_config_disabled(second["config_id"], is_disabled=True, reason="测试")
        at.run()
        assert not list(at.exception)
        assert at.session_state["instant_selected_batch_id"] is None
        assert not any(button.key == "instant_entry_save_button" for button in at.button)


def test_transfer_dialog_opens_on_first_click_and_freezes_source():
    with IsolatedDatabase():
        f = seed_instant_configuration()
        for i in range(20):
            entry(f, i)
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10)
        at.session_state["top_level_method_selector"] = "即时法"
        at.session_state["instant_selected_project_id"] = f["project_id"]
        at.session_state["instant_selected_batch_id"] = f["batch_id"]
        at.run()
        at.button(key="open_instant_transfer_dialog").click().run()
        assert not list(at.exception)
        confirm_key = f"confirm_instant_transfer_dialog_{f['batch_id']}"
        assert any(button.key == confirm_key for button in at.button)
        at.button(key=confirm_key).click().run()
        assert not list(at.exception)
        assert get_instant_batch(f["batch_id"])["transfer_status"] == "transferred"
        assert not any(button.key == "instant_entry_save_button" for button in at.button)
        assert any(button.key == "instant_go_to_transferred_lj_batch" for button in at.button)


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.2 Instant integration smoke tests passed.")
