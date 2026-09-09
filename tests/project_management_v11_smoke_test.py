from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import get_connection, init_db
from pages.main_page import PROJECT_MANAGEMENT_ENTRY_LABEL
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
    create_unit,
)
from services.project_config_service import (
    activate_lot_config,
    activate_project_template,
    copy_lot_config,
    create_lot_config_from_template,
    create_project_template,
    get_lot_config,
    list_config_snapshots,
    list_lot_config_items,
    list_lot_configs,
    list_lot_item_levels,
    list_project_templates,
    save_lot_item_levels,
    save_template_items,
    set_lot_config_disabled,
    set_project_template_disabled,
    validate_lot_config,
    validate_project_template,
)


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "project_management_v11_smoke_test.db"
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


def _seed_v11_configuration_dependencies() -> dict[str, object]:
    manufacturer_id = create_manufacturer(display_name="V11 厂家")
    instrument_model_id = create_instrument_model(
        manufacturer_id=manufacturer_id,
        generic_name="全自动质控分析仪",
        model="V11-9000",
    )
    lab_instrument_id = create_lab_instrument(
        instrument_model_id=instrument_model_id,
        display_name="V11 1号仪器",
        department_name="检验科",
    )
    reagent_id = create_reagent(
        manufacturer_id=manufacturer_id,
        generic_name="V11 配套试剂",
    )
    qc_material_id = create_qc_material(
        manufacturer_id=manufacturer_id,
        generic_name="V11 三水平质控品",
        nominal_level_count=3,
    )
    unit_id = create_unit(symbol="V11-U", unit_name="V11 单位")
    method_id = create_method(method_name="V11 方法")
    lj_item_id = create_test_item(
        chinese_name="V11 单水平项目",
        default_unit_id=unit_id,
    )
    zscore_item_id = create_test_item(
        chinese_name="V11 多水平项目",
        default_unit_id=unit_id,
    )

    def create_lot_with_levels(lot_no: str) -> tuple[int, list[int]]:
        lot_id = create_qc_lot(
            qc_material_id=qc_material_id,
            lot_no=lot_no,
            expiry_date="2028-12-31",
        )
        levels = [
            create_qc_level(
                qc_material_lot_id=lot_id,
                level_name=level_name,
                level_order=order,
            )
            for order, level_name in [(1, "低值"), (2, "中值"), (3, "高值")]
        ]
        return lot_id, levels

    source_lot_id, source_levels = create_lot_with_levels("V11-LOT-001")
    target_lot_id, target_levels = create_lot_with_levels("V11-LOT-002")
    return {
        "lab_instrument_id": lab_instrument_id,
        "reagent_id": reagent_id,
        "qc_material_id": qc_material_id,
        "unit_id": unit_id,
        "method_id": method_id,
        "lj_item_id": lj_item_id,
        "zscore_item_id": zscore_item_id,
        "source_lot_id": source_lot_id,
        "source_levels": source_levels,
        "target_lot_id": target_lot_id,
        "target_levels": target_levels,
    }


def _build_active_source_config(data: dict[str, object]) -> tuple[int, int]:
    template_id = create_project_template(
        template_name="V11 多项目模板",
        lab_instrument_id=int(data["lab_instrument_id"]),
        qc_material_id=int(data["qc_material_id"]),
    )
    save_template_items(
        template_id,
        [
            {
                "test_item_id": int(data["lj_item_id"]),
                "qc_method": "lj",
                "input_value_type": "raw",
                "unit_id": int(data["unit_id"]),
                "method_id": int(data["method_id"]),
                "reagent_id": int(data["reagent_id"]),
                "level_count": 1,
                "target_n": 20,
                "cv_limit": 5.0,
                "sort_order": 1,
            },
            {
                "test_item_id": int(data["zscore_item_id"]),
                "qc_method": "zscore",
                "input_value_type": "raw",
                "unit_id": int(data["unit_id"]),
                "method_id": int(data["method_id"]),
                "reagent_id": int(data["reagent_id"]),
                "level_count": 3,
                "target_n": 20,
                "sort_order": 2,
            },
        ],
    )
    assert validate_project_template(template_id) == []
    activate_project_template(template_id)

    lot_config_id = create_lot_config_from_template(
        template_id=template_id,
        qc_material_lot_id=int(data["source_lot_id"]),
    )
    items = list_lot_config_items(lot_config_id)
    lj_item = items[items["qc_method"] == "lj"].iloc[0]
    zscore_item = items[items["qc_method"] == "zscore"].iloc[0]
    source_levels = list(data["source_levels"])
    save_lot_item_levels(
        int(lj_item["id"]),
        [
            {
                "qc_level_id": int(source_levels[0]),
                "target_source": "manufacturer",
                "target_mean": 10.0,
                "target_sd": 0.5,
                "target_confirmed": True,
            }
        ],
    )
    save_lot_item_levels(
        int(zscore_item["id"]),
        [
            {
                "qc_level_id": int(level_id),
                "target_source": "building",
            }
            for level_id in source_levels
        ],
    )
    assert validate_lot_config(lot_config_id) == []
    activate_lot_config(lot_config_id)
    return template_id, lot_config_id


def test_template_lot_copy_and_snapshot_round_trip() -> None:
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        template_id, source_config_id = _build_active_source_config(data)
        source = get_lot_config(source_config_id)
        assert str(source["status"]) == "active"
        assert len(list_config_snapshots(source_config_id).index) == 4

        copied_config_id = copy_lot_config(
            source_lot_config_id=source_config_id,
            target_qc_material_lot_id=int(data["target_lot_id"]),
        )
        copied = get_lot_config(copied_config_id)
        assert int(copied["copied_from_config_id"]) == source_config_id
        assert str(copied["status"]) == "draft"
        copied_items = list_lot_config_items(copied_config_id)
        assert len(copied_items.index) == 2
        copied_lj_item = copied_items[copied_items["qc_method"] == "lj"].iloc[0]
        copied_lj_levels = list_lot_item_levels(int(copied_lj_item["id"]))
        assert copied_lj_levels.iloc[0]["target_source"] == "copied_pending"
        assert int(copied_lj_levels.iloc[0]["target_confirmed"]) == 0
        assert any("待确认" in error for error in validate_lot_config(copied_config_id))
        assert len(list_config_snapshots(copied_config_id).index) == 1

        set_lot_config_disabled(copied_config_id, is_disabled=True, reason="smoke")
        assert copied_config_id not in list_lot_configs()["id"].astype(int).tolist()
        set_lot_config_disabled(copied_config_id, is_disabled=False)
        assert str(get_lot_config(copied_config_id)["status"]) == "draft"
        assert len(list_config_snapshots(copied_config_id).index) == 3

        set_project_template_disabled(template_id, is_disabled=True, reason="smoke")
        assert template_id not in list_project_templates()["id"].astype(int).tolist()
        set_project_template_disabled(template_id, is_disabled=False)
        assert template_id in list_project_templates()["id"].astype(int).tolist()

        configs = list_lot_configs(template_id)
        assert set(configs["id"].astype(int).tolist()) == {
            source_config_id,
            copied_config_id,
        }
        with get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_project_management_page_starts_from_new_navigation() -> None:
    with TemporaryDatabaseContext():
        _seed_v11_configuration_dependencies()
        at = AppTest.from_file(APP_FILE_PATH)
        at.run()
        assert not list(at.exception)
        assert PROJECT_MANAGEMENT_ENTRY_LABEL not in at.radio(key="top_level_method_selector").options
        at.button(key="open_project_management_page").click().run()
        assert not list(at.exception)
        assert len(at.tabs) == 4
        assert any(button.key == "close_project_management_page" for button in at.button)


if __name__ == "__main__":
    tests = [
        test_template_lot_copy_and_snapshot_round_trip,
        test_project_management_page_starts_from_new_navigation,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.1 project management smoke tests passed.")
