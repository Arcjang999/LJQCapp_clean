"""Material identity, reference protection and dialog lifecycle on disposable databases."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.material_catalog_service import (
    get_control_material, get_material_product, list_control_materials,
    set_control_material_disabled, update_control_material, update_material_product,
)
from services.material_workflow_service import register_control_material
from services.master_data_service import create_manufacturer, create_qc_material
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies


def rejected(call, text):
    try:
        call()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError('Expected rejection')


def material_edit(level_id, **changes):
    row = get_control_material(level_id)
    args = dict(level_name=row['level_name'], level_code=row['level_code'],
                catalog_no=row['specification_catalog_no'], lot_no=row['lot_no'],
                expiry_date=row['expiry_date'], concentration_note=row['concentration_label'],
                expected_version=row['edit_version'])
    args.update(changes)
    update_control_material(level_id, **args)


def seed_product():
    manufacturer = create_manufacturer(display_name='材料测试厂家')
    return create_qc_material(manufacturer_id=manufacturer, generic_name='材料测试产品')


def test_identity_atomicity_and_restore():
    with TemporaryDatabaseContext():
        product = seed_product()
        first = register_control_material(material_id=product, level_name='低值', level_code='001',
            lot_no='LOT-A', expiry_date='2028-01-01')
        second = register_control_material(material_id=product, level_name='低值', level_code='L02',
            lot_no='LOT-A', expiry_date='2028-01-01')
        third = register_control_material(material_id=product, level_name='高值', level_code='H03',
            lot_no='LOT-B', expiry_date='2028-02-01')
        assert get_control_material(first)['level_code'] == '001'
        assert get_control_material(first)['qc_material_lot_id'] == get_control_material(second)['qc_material_lot_id']
        assert get_control_material(third)['qc_material_lot_id'] != get_control_material(second)['qc_material_lot_id']
        with get_connection() as c:
            counts = tuple(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                           ('md_qc_material_specs', 'md_qc_material_lots', 'md_qc_levels'))
        rejected(lambda: register_control_material(material_id=product, level_name='未保存规格', level_code='004',
            lot_no=' lot-a ', expiry_date='2029-01-01'), '不同效期')
        with get_connection() as c:
            assert counts == tuple(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                                   ('md_qc_material_specs', 'md_qc_material_lots', 'md_qc_levels'))
        rejected(lambda: material_edit(first, expiry_date='2029-01-01'), '其它浓度水平')
        material_edit(third, lot_no='LOT-C', level_code='0003')
        assert get_control_material(third)['lot_no'] == 'LOT-C'
        assert get_control_material(first)['expiry_date'] == '2028-01-01'
        row = get_control_material(first)
        set_control_material_disabled(first, is_disabled=True, reason='登记核对', expected_version=row['edit_version'])
        assert first not in list_control_materials(product)['id'].tolist()
        rejected(lambda: register_control_material(material_id=product, level_name='低值', level_code='001',
            lot_no='LOT-A', expiry_date='2028-01-01'), '已停用的记录请先恢复')
        row = get_control_material(first)
        set_control_material_disabled(first, is_disabled=False, expected_version=row['edit_version'])
        assert first in list_control_materials(product)['id'].tolist()
        stale = row['edit_version']
        rejected(lambda: material_edit(first, expected_version=stale, concentration_note='不应保存'), '资料已修改')


def test_referenced_material_keeps_identity():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        from services.project_config_service import create_project_template, save_template_items, activate_project_template, create_lot_config_from_template
        from tests.quality_review_fixtures import confirm_fixture_project
        tid = create_project_template(template_name='材料引用保护', lab_instrument_id=data['lab_instrument_id'],
            qc_material_id=data['qc_material_id'], default_reagent_id=data['reagent_id'])
        save_template_items(tid, [dict(test_item_id=data['lj_item_id'], qc_method='lj', input_value_type='raw',
            unit_id=data['unit_id'], method_id=data['method_id'], reagent_id=data['reagent_id'], level_count=1, target_n=20)])
        confirm_fixture_project(tid)
        activate_project_template(tid)
        create_lot_config_from_template(template_id=tid, qc_material_lot_id=data['source_lot_id'])
        lid = data['source_levels'][0]
        assert get_control_material(lid)['identity_locked']
        rejected(lambda: material_edit(lid, level_code='新的编号'), '不能直接修改')
        material_edit(lid, concentration_note='补充厂家资料说明')
        assert get_control_material(lid)['concentration_label'] == '补充厂家资料说明'
        product = get_material_product(data['qc_material_id'])
        rejected(lambda: update_material_product(product['id'], generic_name='错误覆盖', expected_version=product['edit_version']), '不能直接修改')
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_lot_configs').fetchone()[0] == 1


def catalogue_page():
    from ui.material_catalog import render_material_catalog
    render_material_catalog()


def assert_clean(app):
    assert not list(app.exception), [str(e) for e in app.exception]


def test_dialog_save_cancel_and_confirm():
    with TemporaryDatabaseContext():
        product = seed_product()
        app = AppTest.from_function(catalogue_page, default_timeout=15).run()
        assert_clean(app)
        app.button(key='material_catalog_add').click().run()
        assert_clean(app)
        prefix = f'material_edit_{app.session_state["material_dialog_nonce"]}'
        app.text_input(key=f'{prefix}_new_name').set_value('高值')
        app.text_input(key=f'{prefix}_new_code').set_value('001')
        app.text_input(key=f'{prefix}_lot').set_value('UI-LOT')
        app.button(key=f'{prefix}_save').click().run()
        assert_clean(app)
        assert list_control_materials(product).empty
        assert app.text_input(key=f'{prefix}_new_code').value == '001'
        app.date_input(key=f'{prefix}_expiry').set_value(date(2028, 1, 1))
        app.button(key=f'{prefix}_save').click().run()
        assert_clean(app)
        records = list_control_materials(product)
        assert len(records) == 1
        lid = int(records.iloc[0]['id'])
        assert app.session_state['material_catalog_selected_id'] == lid
        app.button(key='material_catalog_edit').click().run()
        prefix = f'material_edit_{app.session_state["material_dialog_nonce"]}'
        app.text_input(key=f'{prefix}_lot').set_value('DO-NOT-SAVE')
        app.button(key=f'{prefix}_cancel').click().run()
        assert_clean(app)
        assert get_control_material(lid)['lot_no'] == 'UI-LOT'
        assert app.text_input(key=f'{prefix}_lot').value == 'DO-NOT-SAVE'
        app.button(key=f'{prefix}_discard_confirm').click().run()
        assert_clean(app)
        assert get_control_material(lid)['lot_no'] == 'UI-LOT'
        app.button(key='material_catalog_status').click().run()
        prefix = f'material_status_{app.session_state["material_dialog_nonce"]}'
        app.button(key=f'{prefix}_confirm').click().run()
        assert_clean(app)
        assert not get_control_material(lid)['is_disabled']
        app.text_area(key=f'{prefix}_reason').set_value('测试停用')
        app.button(key=f'{prefix}_confirm').click().run()
        assert_clean(app)
        assert get_control_material(lid)['is_disabled']
        assert app.session_state['material_catalog_selected_id'] == lid
        app.button(key='material_catalog_status').click().run()
        prefix = f'material_status_{app.session_state["material_dialog_nonce"]}'
        app.button(key=f'{prefix}_confirm').click().run()
        assert_clean(app)
        assert not get_control_material(lid)['is_disabled']
        app.text_input(key='material_catalog_search').set_value('001').run()
        app.button(key='material_catalog_edit').click().run()
        prefix = f'material_edit_{app.session_state["material_dialog_nonce"]}'
        assert app.text_input(key=f'{prefix}_lot').value == 'UI-LOT'
        app.text_input(key=f'{prefix}_new_code').set_value('L02')
        app.button(key=f'{prefix}_save').click().run()
        assert_clean(app)
        assert get_control_material(lid)['level_code'] == 'L02'
        assert app.text_input(key='material_catalog_search').value == '001'
        assert app.session_state['material_catalog_selected_id'] == lid
        assert len(app.dataframe) == 1


if __name__ == '__main__':
    test_identity_atomicity_and_restore()
    test_referenced_material_keeps_identity()
    test_dialog_save_cancel_and_confirm()
    print('material_catalog_smoke_test passed')
