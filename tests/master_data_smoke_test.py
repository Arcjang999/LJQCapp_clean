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
from pages.main_page import MASTER_DATA_ENTRY_LABEL
from services.master_data_service import (
    create_alias,
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
    list_lab_instruments,
    list_manufacturers,
    list_qc_levels,
    list_qc_lots,
    list_test_items,
    set_master_entity_disabled,
)
from tests.project_workspace_smoke_test import select_table_row


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "master_data_smoke_test.db"
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


def _seed_complete_master_data() -> dict[str, int]:
    manufacturer_id = create_manufacturer(
        display_name="示例诊断",
        legal_name="示例诊断技术有限公司",
        country_or_region="中国",
    )
    unit_id = create_unit(symbol="ng/mL", unit_name="纳克每毫升")
    method_id = create_method(method_name="化学发光法", method_category="免疫")
    test_item_id = create_test_item(
        chinese_name="示例分析物",
        standard_code="LJQC-DEMO-001",
        abbreviation="DEMO",
        default_unit_id=unit_id,
    )
    create_alias(
        entity_type="test_item",
        entity_id=test_item_id,
        alias_text="示例项目别名",
        alias_type="custom",
    )
    instrument_model_id = create_instrument_model(
        manufacturer_id=manufacturer_id,
        generic_name="全自动分析仪",
        model="Demo 8000",
    )
    lab_instrument_id = create_lab_instrument(
        instrument_model_id=instrument_model_id,
        display_name="免疫室 1 号机",
        department_name="免疫室",
    )
    reagent_id = create_reagent(
        manufacturer_id=manufacturer_id,
        generic_name="示例分析物检测试剂",
        trade_name="Demo Reagent",
    )
    qc_material_id = create_qc_material(
        manufacturer_id=manufacturer_id,
        generic_name="示例多水平质控品",
        nominal_level_count=3,
    )
    qc_lot_id = create_qc_lot(
        qc_material_id=qc_material_id,
        lot_no="QC-2026-001",
        expiry_date="2027-12-31",
    )
    level_ids = [
        create_qc_level(
            qc_material_lot_id=qc_lot_id,
            level_name=level_name,
            level_order=level_order,
        )
        for level_order, level_name in [(1, "低值"), (2, "中值"), (3, "高值")]
    ]
    return {
        "manufacturer_id": manufacturer_id,
        "unit_id": unit_id,
        "method_id": method_id,
        "test_item_id": test_item_id,
        "instrument_model_id": instrument_model_id,
        "lab_instrument_id": lab_instrument_id,
        "reagent_id": reagent_id,
        "qc_material_id": qc_material_id,
        "qc_lot_id": qc_lot_id,
        "level_1_id": level_ids[0],
        "level_2_id": level_ids[1],
        "level_3_id": level_ids[2],
    }


def test_v11_schema_seeds_and_master_data_round_trip() -> None:
    with TemporaryDatabaseContext():
        with get_connection() as connection:
            table_names = {
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                ).fetchall()
            }
            assert "md_test_items" in table_names
            assert "qc_project_templates" in table_names
            assert connection.execute("SELECT COUNT(*) FROM md_units").fetchone()[0] >= 9
            assert connection.execute("SELECT COUNT(*) FROM md_methods").fetchone()[0] >= 5
            assert connection.execute(
                "SELECT COUNT(*) FROM md_test_items WHERE origin_type = 'official'"
            ).fetchone()[0] == 296
            assert connection.execute(
                "SELECT COUNT(*) FROM md_source_records WHERE entity_type = 'test_item'"
            ).fetchone()[0] == 296
            assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0

        official_item = list_test_items(query="0100101A")
        assert official_item["chinese_name"].tolist() == ["白细胞计数"]
        init_db()
        with get_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM md_test_items WHERE origin_type = 'official'"
            ).fetchone()[0] == 296
        seeded = _seed_complete_master_data()
        assert seeded["lab_instrument_id"] in list_lab_instruments()["id"].astype(int).tolist()
        assert seeded["qc_lot_id"] in list_qc_lots()["id"].astype(int).tolist()
        assert len(list_qc_levels(seeded["qc_lot_id"]).index) == 3
        alias_matches = list_test_items(query="示例项目别名")
        assert alias_matches["id"].astype(int).tolist() == [seeded["test_item_id"]]

        set_master_entity_disabled(
            "manufacturer",
            seeded["manufacturer_id"],
            is_disabled=True,
            reason="smoke test",
        )
        assert seeded["manufacturer_id"] not in list_manufacturers()["id"].astype(int).tolist()
        disabled = list_manufacturers(include_disabled=True)
        row = disabled[disabled["id"].astype(int) == seeded["manufacturer_id"]].iloc[0]
        assert int(row["is_disabled"]) == 1


def test_master_data_page_starts_from_new_navigation() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH, default_timeout=15)
        at.run()
        assert not list(at.exception)
        assert MASTER_DATA_ENTRY_LABEL not in at.radio(key="top_level_method_selector").options
        at.button(key="open_master_data_page").click().run()
        assert not list(at.exception)
        subtabs = {'已登记仪器', '仪器型号', '方法学', '单位'}
        labels = [tab.label for tab in at.tabs]
        assert [label for label in labels if label not in subtabs] == ['厂家', '检验项目', '仪器', '试剂', '质控品与批号', '方法学与单位']
        assert subtabs <= set(labels)
        assert any(button.key == "close_master_data_page" for button in at.button)


def test_filtered_manufacturer_edit_and_status_require_explicit_save() -> None:
    with TemporaryDatabaseContext():
        first_id = create_manufacturer(display_name='Alpha厂家')
        selected_id = create_manufacturer(display_name='Beta厂家')

        def stored(entity_id):
            with get_connection() as connection:
                return dict(connection.execute('SELECT * FROM md_manufacturers WHERE id=?', (entity_id,)).fetchone())

        def field_key(field):
            return 'md_dialog_' + at.session_state['master_data_dialog']['token'] + '_' + field

        def select_filtered_row():
            table_index = next(index for index, table in enumerate(at.dataframe)
                               if 'md_table_manufacturer_' in table.proto.id)
            select_table_row(at, 0, index=table_index)
            assert at.session_state['md_selected_manufacturer'] == selected_id

        first_before, selected_before = stored(first_id), stored(selected_id)
        at = AppTest.from_file(APP_FILE_PATH, default_timeout=15).run()
        at.button(key='open_master_data_page').click().run()
        at.text_input(key='md_search_manufacturer').set_value('Beta').run()
        select_filtered_row()
        at.button(key='md_edit_manufacturer').click().run()
        at.text_input(key=field_key('display_name')).set_value('Beta未保存名称')
        at.button(key='md_dialog_cancel').click().run()
        assert not at.exception
        assert stored(first_id) == first_before and stored(selected_id) == selected_before
        at.button(key='md_dialog_discard').click().run()
        assert not at.exception and stored(selected_id) == selected_before

        at.button(key='md_edit_manufacturer').click().run()
        at.text_input(key=field_key('display_name')).set_value('Beta更新名称')
        at.text_area(key=field_key('notes')).set_value('第一行备注\n第二行备注')
        at.button(key='md_dialog_save').click().run()
        assert not at.exception
        assert at.session_state['md_selected_manufacturer'] == selected_id
        assert stored(selected_id)['display_name'] == 'Beta更新名称'
        assert stored(selected_id)['notes'] == '第一行备注\n第二行备注'
        assert stored(first_id) == first_before
        saved = stored(selected_id)

        at.button(key='md_status_manufacturer').click().run()
        at.text_area(key=field_key('reason')).set_value('本次取消，不应停用').run()
        assert stored(selected_id) == saved
        at.button(key='md_status_cancel').click().run()
        assert not at.exception and stored(selected_id) == saved
        at.button(key='md_status_manufacturer').click().run()
        at.button(key='md_status_confirm').click().run()
        assert not at.exception and stored(selected_id) == saved
        assert any('停用原因' in error.value for error in at.error)
        at.text_area(key=field_key('reason')).set_value('停用流程验收')
        at.button(key='md_status_confirm').click().run()
        assert not at.exception
        assert stored(selected_id)['is_disabled'] == 1
        assert stored(selected_id)['disabled_reason'] == '停用流程验收'
        assert stored(first_id) == first_before
        at.checkbox(key='md_show_disabled_manufacturer').check().run()
        select_filtered_row()
        at.button(key='md_status_manufacturer').click().run()
        at.button(key='md_status_cancel').click().run()
        assert not at.exception and stored(selected_id)['is_disabled'] == 1
        at.button(key='md_status_manufacturer').click().run()
        at.button(key='md_status_confirm').click().run()
        assert not at.exception and stored(selected_id)['is_disabled'] == 0
        assert stored(first_id) == first_before
        assert len(list_manufacturers(include_disabled=True)) == 2
        at.button(key='close_master_data_page').click().run()
        assert not at.exception and not at.session_state['show_master_data_page']
        at.button(key='open_master_data_page').click().run()
        assert not at.exception
        assert at.text_input(key='md_search_manufacturer').value == 'Beta'
        assert at.checkbox(key='md_show_disabled_manufacturer').value
        assert at.session_state['md_selected_manufacturer'] == selected_id


if __name__ == "__main__":
    tests = [
        test_v11_schema_seeds_and_master_data_round_trip,
        test_master_data_page_starts_from_new_navigation,
        test_filtered_manufacturer_edit_and_status_require_explicit_save,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} master data smoke tests passed.")
