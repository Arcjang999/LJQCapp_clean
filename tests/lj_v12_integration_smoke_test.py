from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import add_result, create_batch, create_project, get_batch, get_connection, init_db
from pages.main_page import LJ_ENTRY_LABEL
from pages.lj_sections import build_lj_workbench_context
from services.master_data_service import (
    create_instrument_model,
    create_lab_instrument,
    create_manufacturer,
    create_method,
    create_qc_level,
    create_qc_lot,
    create_qc_material,
    create_reagent,
    create_test_item,
    list_units,
)
from services.project_config_service import (
    activate_lot_config,
    activate_project_template,
    create_lot_config_from_template,
    create_project_template,
    list_lot_config_items,
    save_lot_item_levels,
    save_template_items,
)
from services.report_service import build_lj_monthly_report_package
from services.workbench_config_service import (
    list_lj_workbench_batches,
    list_lj_workbench_projects,
    sync_lj_workbench_bindings,
)


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "lj_v12_integration_smoke_test.db"
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


def _seed_active_lj_configuration() -> int:
    manufacturer_id = create_manufacturer(display_name="V12 LJ 厂家")
    instrument_model_id = create_instrument_model(
        manufacturer_id=manufacturer_id,
        generic_name="全自动生化分析仪",
        model="V12-LJ-9000",
    )
    lab_instrument_id = create_lab_instrument(
        instrument_model_id=instrument_model_id,
        display_name="V12 LJ 1号仪器",
        department_name="检验科",
    )
    reagent_id = create_reagent(
        manufacturer_id=manufacturer_id,
        generic_name="V12 LJ 配套试剂",
    )
    qc_material_id = create_qc_material(
        manufacturer_id=manufacturer_id,
        generic_name="V12 LJ 单水平质控品",
        nominal_level_count=1,
    )
    lot_id = create_qc_lot(
        qc_material_id=qc_material_id,
        lot_no="V12-LJ-LOT-001",
        expiry_date="2028-12-31",
    )
    level_id = create_qc_level(
        qc_material_lot_id=lot_id,
        level_name="水平1",
        level_order=1,
        concentration_label="中值",
    )
    unit_id = int(list_units().loc[lambda frame: frame["symbol"] == "mg/L", "id"].iloc[0])
    method_id = create_method(method_name="V12 免疫比浊法")
    test_item_id = create_test_item(
        chinese_name="V12 C反应蛋白",
        abbreviation="CRP-V12",
        default_unit_id=unit_id,
    )
    template_id = create_project_template(
        template_name="V12 LJ 生化模板",
        lab_instrument_id=lab_instrument_id,
        qc_material_id=qc_material_id,
    )
    save_template_items(
        template_id,
        [
            {
                "test_item_id": test_item_id,
                "qc_method": "lj",
                "input_value_type": "raw",
                "unit_id": unit_id,
                "method_id": method_id,
                "reagent_id": reagent_id,
                "level_count": 1,
                "target_n": 5,
                "cv_limit": 5.0,
                "sort_order": 1,
            }
        ],
    )
    activate_project_template(template_id)
    lot_config_id = create_lot_config_from_template(
        template_id=template_id,
        qc_material_lot_id=lot_id,
        config_name="V12 LJ 批号配置",
    )
    lot_config_item_id = int(list_lot_config_items(lot_config_id).iloc[0]["id"])
    save_lot_item_levels(
        lot_config_item_id,
        [
            {
                "qc_level_id": level_id,
                "target_source": "building",
            }
        ],
    )
    activate_lot_config(lot_config_id)
    return lot_config_id


def test_v12_lj_binding_ignores_old_projects_and_keeps_algorithms() -> None:
    with TemporaryDatabaseContext():
        old_project_id = create_project("旧测试项目", input_value_type="raw")
        create_batch(
            project_id=old_project_id,
            instrument="旧仪器",
            reagent="旧试剂",
            qc_material="旧质控品",
            concentration="旧水平",
            lot_no="OLD-LOT",
            target_n=5,
        )
        _seed_active_lj_configuration()

        assert sync_lj_workbench_bindings() == 1
        projects = list_lj_workbench_projects()
        assert projects["name"].tolist() == ["V12 C反应蛋白"]
        assert "旧测试项目" not in projects["name"].tolist()
        runtime_project_id = int(projects.iloc[0]["id"])
        batches = list_lj_workbench_batches(runtime_project_id)
        assert len(batches.index) == 1
        runtime_batch_id = int(batches.iloc[0]["id"])
        sync_lj_workbench_bindings()
        with get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM qc_workbench_bindings").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 2

        batch = get_batch(runtime_batch_id)
        assert batch["project_name"] == "V12 C反应蛋白"
        assert batch["lot_no"] == "V12-LJ-LOT-001"
        assert batch["unit_symbol"] == "mg/L"
        assert batch["method_name"] == "V12 免疫比浊法"
        assert batch["source_method"] == "v11"

        building_values = [100.0, 100.2, 99.8, 100.1, 99.9]
        for index, value in enumerate(building_values):
            add_result(
                batch_id=runtime_batch_id,
                test_time=f"2026-03-28 08:{index:02d}:00",
                operator=f"builder-{index}",
                value=value,
                log_value=None,
            )
        add_result(
            batch_id=runtime_batch_id,
            test_time="2026-04-01 08:00:00",
            operator="formal-1",
            value=100.0,
            log_value=None,
        )

        context = build_lj_workbench_context(runtime_batch_id)
        assert bool(context["stats"]["target_ready"])
        assert len(context["qc_df"].index) == 6
        report = build_lj_monthly_report_package(runtime_batch_id, "2026-04").report
        assert report.basic_info.unit_symbol == "mg/L"
        assert report.basic_info.detection_method == "V12 免疫比浊法"
        assert report.basic_info.target_source_label == "新版配置：本批次建靶值"


def test_v12_lj_page_uses_global_configuration_selection() -> None:
    with TemporaryDatabaseContext():
        _seed_active_lj_configuration()
        app = AppTest.from_file(APP_FILE_PATH)
        app.run()
        assert not list(app.exception)
        app.radio(key="top_level_method_selector").set_value(LJ_ENTRY_LABEL).run()
        assert not list(app.exception)
        assert any(selectbox.key == "v12_lj_project_selector" for selectbox in app.selectbox)
        assert not any(button.label == "创建项目" for button in app.button)
        assert not any(button.label == "创建批次" for button in app.button)

        project_selector = app.selectbox(key="v12_lj_project_selector")
        project_selector.set_value(project_selector.options[1]).run()
        assert not list(app.exception)
        batch_selector = app.selectbox(key="v12_lj_batch_selector")
        batch_selector.set_value(batch_selector.options[1]).run()
        assert not list(app.exception)
        assert any(button.key == "lj_open_v11_project_management" for button in app.button)


if __name__ == "__main__":
    tests = [
        test_v12_lj_binding_ignores_old_projects_and_keeps_algorithms,
        test_v12_lj_page_uses_global_configuration_selection,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.2 LJ integration smoke tests passed.")
