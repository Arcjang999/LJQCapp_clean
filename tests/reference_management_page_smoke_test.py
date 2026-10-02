"""Reference management and product-scoped reagent lots on isolated databases."""
from datetime import date
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

import database
from services import master_data_service as master
from services.lot_lifecycle_service import create_reagent_lot, record_lot_verification
from services.project_config_service import create_project_template
from services.settings_service import get_report_settings
from tests.master_data_dialogs_smoke_test import field_key, fill
from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
from tests.project_workspace_smoke_test import select_table_row
from tests.reagent_lifecycle_dialogs_smoke_test import fixture as reagent_fixture


class ReferenceManagementPageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.original = {name: getattr(database, name) for name in
                         ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        database.DB_PATH = database.DEFAULT_DB_PATH = root / 'reference.db'
        database.STORAGE_CONFIG_PATH = root / 'storage.json'
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()

    def tearDown(self):
        for name, value in self.original.items():
            setattr(database, name, value)
        self.temporary.cleanup()

    def dump(self):
        with database.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def clean(self, app):
        self.assertFalse(list(app.exception), [str(error.value) for error in app.exception])

    def app(self, kind):
        app = AppTest.from_string(f'''
import streamlit as st
from pages.reference_management_page import render_reference_management_page
from pages.settings_page import render_settings_page
st.session_state.setdefault('reference_management_kind', {kind!r})
st.session_state.setdefault('show_reference_management_page', True)
if st.session_state.get('show_settings_page'):
    render_settings_page()
else:
    render_reference_management_page()
''', default_timeout=30).run()
        self.clean(app)
        return app

    def table(self, app, prefix):
        return next(table for table in app.dataframe if prefix in table.proto.id)

    def select(self, app, prefix, column, value):
        index = next(index for index, table in enumerate(app.dataframe) if prefix in table.proto.id)
        values = app.dataframe[index].value[column].tolist()
        select_table_row(app, values.index(value), index=index)
        self.clean(app)

    def seed_manufacturers(self):
        names = {'instrument': '仪器专用厂家', 'reagent': '试剂专用厂家', 'qc_material': '质控品专用厂家'}
        ids = {category: master.create_manufacturer(display_name=name, categories=[category])
               for category, name in names.items()}
        master.create_manufacturer(display_name='多类经营厂家', categories=['instrument', 'reagent'])
        master.create_manufacturer(display_name='尚未分类厂家')
        return names, ids

    def test_manufacturer_categories_show_independent_lists_and_clear_other_selection(self):
        names, ids = self.seed_manufacturers()
        before = self.dump()
        app = self.app('manufacturer')
        self.assertEqual(app.radio(key='reference_manufacturer_category').options,
                         ['仪器厂家', '试剂厂家', '质控品厂家'])
        for category in ('instrument', 'reagent', 'qc_material'):
            app.radio(key='reference_manufacturer_category').set_value(category).run()
            self.clean(app)
            expected = {names[category]}
            if category in ('instrument', 'reagent'):
                expected.add('多类经营厂家')
            shown = self.table(app, 'md_table_manufacturer_').value
            self.assertEqual(set(shown['厂家名称']), expected)
            self.assertIsNone(app.session_state['md_selected_manufacturer'])
            self.assertTrue(app.button(key='md_edit_manufacturer').disabled)
            self.select(app, 'md_table_manufacturer_', '厂家名称', names[category])
            self.assertEqual(app.session_state['md_selected_manufacturer'], ids[category])
        self.assertEqual(before, self.dump())

    def test_new_manufacturer_defaults_to_current_category_and_saves_that_role(self):
        self.seed_manufacturers()
        app = self.app('manufacturer')
        for category in ('instrument', 'reagent', 'qc_material'):
            app.radio(key='reference_manufacturer_category').set_value(category).run()
            app.button(key='md_create_manufacturer').click().run()
            self.clean(app)
            self.assertEqual(app.multiselect(key=field_key(app, 'categories')).value, [category])
            name = '新增类别验收-' + category
            app.text_input(key=field_key(app, 'display_name')).set_value(name)
            app.multiselect(key=field_key(app, 'categories')).set_value([])
            before = self.dump()
            app.button(key='md_dialog_save').click().run()
            self.clean(app)
            self.assertTrue(any('请至少选择一类' in error.value for error in app.error))
            self.assertEqual(before, self.dump())
            app.multiselect(key=field_key(app, 'categories')).set_value([category])
            app.button(key='md_dialog_save').click().run()
            self.clean(app)
            self.assertFalse(list(app.error))
            identifier = app.session_state['md_selected_manufacturer']
            self.assertEqual(master.get_manufacturer_categories(identifier), [category])
            self.assertIn(name, self.table(app, 'md_table_manufacturer_').value['厂家名称'].tolist())

    def test_instruments_reuse_lab_settings_and_show_only_selected_instrument_projects(self):
        data = _seed_v11_configuration_dependencies()
        first = master.list_lab_instruments().iloc[0]
        model_id = int(master.list_instrument_models().iloc[0]['id'])
        other = master.create_lab_instrument(instrument_model_id=model_id,
            display_name='另一台仪器', department_name='另一科室')
        common = dict(qc_material_id=data['qc_material_id'])
        first_projects = {'一号仪器项目甲', '一号仪器项目乙'}
        for name in first_projects:
            create_project_template(template_name=name, lab_instrument_id=data['lab_instrument_id'], **common)
        create_project_template(template_name='另一仪器专属项目', lab_instrument_id=other, **common)
        app = self.app('instrument')
        self.assertFalse(app.tabs)
        self.assertFalse(any('md_table_instrument_model_' in table.proto.id for table in app.dataframe))
        self.assertFalse(any(button.key == 'md_create_instrument_model' for button in app.button))
        before = self.dump()
        self.select(app, 'md_table_lab_instrument_', '仪器名称', first.display_name)
        shown = next(table.value for table in app.dataframe if {'项目', '检验项目数'} <= set(table.value.columns))
        self.assertEqual(set(shown['项目']), first_projects)
        choices = app.selectbox(key='instrument_project_' + str(data['lab_instrument_id'])).options
        self.assertEqual(set(choices), {'请选择项目', *first_projects})
        self.select(app, 'md_table_lab_instrument_', '仪器名称', '另一台仪器')
        shown = next(table.value for table in app.dataframe if {'项目', '检验项目数'} <= set(table.value.columns))
        self.assertEqual(shown['项目'].tolist(), ['另一仪器专属项目'])
        self.assertEqual(before, self.dump())
        app.button(key='md_edit_lab_instrument').click().run()
        self.clean(app)
        self.assertTrue(app.selectbox(key=field_key(app, 'manufacturer_id')).disabled)
        self.assertTrue(app.text_input(key=field_key(app, 'model')).disabled)
        self.assertFalse(app.text_input(key=field_key(app, 'location')).disabled)
        self.assertFalse(any(box.key == field_key(app, 'instrument_model_id') for box in app.selectbox))
        app.text_input(key=field_key(app, 'location')).set_value('仪器调整后的位置')
        app.button(key='md_dialog_save').click().run()
        self.clean(app)
        self.assertFalse(list(app.error), [error.value for error in app.error])
        with database.read_snapshot() as connection:
            changed = connection.execute('SELECT instrument_model_id,location FROM lab_instruments WHERE id=?', (other,)).fetchone()
            self.assertEqual(tuple(changed), (model_id, '仪器调整后的位置'))
        app.button(key='md_status_lab_instrument').click().run()
        self.clean(app)
        app.text_area(key=field_key(app, 'reason')).set_value('暂停使用本仪器')
        app.button(key='md_status_confirm').click().run()
        self.clean(app)
        app.checkbox(key='md_show_disabled_lab_instrument').check().run()
        self.select(app, 'md_table_lab_instrument_', '仪器名称', '另一台仪器')
        self.assertEqual(app.button(key='md_status_lab_instrument').label, '恢复仪器')
        shown = next(table.value for table in app.dataframe if {'项目', '检验项目数'} <= set(table.value.columns))
        self.assertEqual(shown['项目'].tolist(), ['另一仪器专属项目'])
        app.button(key='instrument_lab_settings').click().run()
        self.clean(app)
        self.assertTrue(app.session_state['show_settings_page'])
        self.assertFalse(app.session_state['show_reference_management_page'])
        app.text_input(key='settings_lab_name').set_value('仪器管理验收实验室')
        app.text_input(key='settings_department_name').set_value('验收检验科')
        app.button(key='save_system_settings').click().run()
        self.clean(app)
        self.assertEqual(get_report_settings().lab_name, '仪器管理验收实验室')
        reopened = self.app('instrument')
        self.assertTrue(any('仪器管理验收实验室' in str(item.value) and '验收检验科' in str(item.value)
                            for item in reopened.info))

    def test_unified_instrument_cancel_creates_nothing_and_same_manufacturer_model_is_reused(self):
        manufacturer = master.create_manufacturer(display_name='统一仪器厂家', categories=['instrument'])
        master.create_manufacturer(display_name='只供应试剂的厂家', categories=['reagent'])
        app = self.app('instrument')
        self.assertFalse(app.tabs)
        self.assertFalse(any('md_table_instrument_model_' in table.proto.id for table in app.dataframe))
        before = self.dump()
        values = dict(manufacturer_id=manufacturer, model='LAB-UNIFIED-200', display_name='统一登记一号仪器',
            asset_code='UNIFIED-ASSET-1', serial_number='UNIFIED-SN-1', department_name='免疫室',
            instrument_group='常规组', location='二楼', notes='统一表单仪器备注')
        app.button(key='md_create_lab_instrument').click().run()
        self.clean(app)
        self.assertNotIn('只供应试剂的厂家', app.selectbox(key=field_key(app, 'manufacturer_id')).options)
        self.assertFalse(any(box.key == field_key(app, 'instrument_model_id') for box in app.selectbox))
        fill(app, values).run()
        self.clean(app)
        self.assertEqual(before, self.dump())
        app.button(key='md_dialog_cancel').click().run()
        self.clean(app)
        app.button(key='md_dialog_discard').click().run()
        self.clean(app)
        self.assertEqual(before, self.dump())
        identifiers = []
        for number in (1, 2):
            app.button(key='md_create_lab_instrument').click().run()
            self.clean(app)
            entered = {**values, 'display_name': f'统一登记{number}号仪器',
                'asset_code': f'UNIFIED-ASSET-{number}', 'serial_number': f'UNIFIED-SN-{number}'}
            fill(app, entered).run()
            self.clean(app)
            app.button(key='md_dialog_save').click().run()
            self.clean(app)
            self.assertFalse(list(app.error), [error.value for error in app.error])
            identifiers.append(app.session_state['md_selected_lab_instrument'])
            with database.read_snapshot() as connection:
                models = connection.execute('SELECT id,manufacturer_id,model FROM md_instrument_models').fetchall()
                self.assertEqual(len(models), 1)
                self.assertEqual(tuple(models[0])[1:], (manufacturer, values['model']))
                instrument = connection.execute('SELECT * FROM lab_instruments WHERE id=?', (identifiers[-1],)).fetchone()
                self.assertEqual(instrument['instrument_model_id'], models[0]['id'])
                for key, value in entered.items():
                    if key not in {'manufacturer_id', 'model'}:
                        self.assertEqual(instrument[key], value, key)
        self.assertEqual(len(set(identifiers)), 2)
        self.assertEqual(len(self.table(app, 'md_table_lab_instrument_').value), 2)

    def test_reagent_product_opens_its_lots_and_all_dialogs_cancel_without_writes(self):
        data, systems, lot = reagent_fixture()
        create_reagent_lot(reagent_id=data['reagent_id'], lot_no='REF-NEXT', expiry_date='2100-12-31')
        other_reagent = master.create_reagent(manufacturer_id=data['manufacturer_id'], generic_name='另一试剂产品')
        create_reagent_lot(reagent_id=other_reagent, lot_no='OTHER-ONLY', expiry_date='2100-12-31')
        record_lot_verification(template_item_id=systems[0]['template_item_id'], system_id=systems[0]['id'],
            reagent_lot_id=lot, conclusion='pass', evidence='隔离验收验证依据',
            confirmed_by='隔离验收人', confirmed_at='2026-09-01')
        before = self.dump()
        app = self.app('reagent')
        self.assertFalse(app.tabs)
        self.assertFalse(any('reagent_lots_' in table.proto.id for table in app.dataframe))
        self.assertFalse(any(button.key == 'reagent_register' for button in app.button))
        for key in ('md_edit_reagent', 'md_status_reagent', 'reagent_manage_selected_lots'):
            self.assertTrue(app.button(key=key).disabled)
        self.select(app, 'md_table_reagent_', '试剂名称', 'V11 配套试剂')
        self.assertEqual(app.session_state['md_selected_reagent'], data['reagent_id'])
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertFalse(any('reagent_lots_' in table.proto.id for table in app.dataframe))
        self.assertFalse(app.button(key='md_edit_reagent').disabled)
        self.assertFalse(app.button(key='md_status_reagent').disabled)
        self.assertFalse(app.button(key='reagent_manage_selected_lots').disabled)
        self.assertEqual(before, self.dump())
        app.button(key='reagent_manage_selected_lots').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['reference_reagent_product_id'], data['reagent_id'])
        self.assertFalse(any('md_table_reagent_' in table.proto.id for table in app.dataframe))
        self.assertFalse(any(box.key == 'reagent_product_filter' for box in app.selectbox))
        self.assertTrue(any(button.key == 'reagent_back_to_products' for button in app.button))
        self.assertEqual(set(self.table(app, 'reagent_lots_').value['试剂批号']), {'REAGENT-DIALOG-OLD', 'REF-NEXT'})
        self.select(app, 'reagent_lots_', '试剂批号', 'REAGENT-DIALOG-OLD')
        self.assertEqual(app.session_state['reagent_selected_lot'], lot)
        for key, kind in (('reagent_register', 'register'), ('reagent_verify', 'verify'), ('reagent_switch', 'switch')):
            self.assertFalse(app.button(key=key).disabled)
            app.button(key=key).click().run()
            self.clean(app)
            modal = app.session_state['reagent_lifecycle_dialog']
            self.assertEqual(modal['kind'], kind)
            prefix = 'rgl_' + modal['token'] + '_'
            if kind == 'register':
                self.assertEqual(modal['bound_product_id'], data['reagent_id'])
                self.assertEqual(modal['draft']['product'], data['reagent_id'])
                self.assertFalse(any(box.key == prefix + 'product' for box in app.selectbox))
                app.text_input(key=prefix + 'lot_no').set_value('取消后不应存在的批号').run()
            elif kind == 'verify':
                app.selectbox(key=prefix + 'system').set_value(systems[0]['id'])
                app.selectbox(key=prefix + 'conclusion').set_value('pass')
                app.text_area(key=prefix + 'evidence').set_value('取消后不应保存的验证依据').run()
            else:
                app.multiselect(key=prefix + 'systems').set_value([systems[0]['id']]).run()
                app.text_area(key=prefix + 'reason').set_value('取消后不应切换使用').run()
                preview = next(table.value for table in app.dataframe if '新试剂批号' in table.value.columns)
                self.assertEqual(preview['新试剂批号'].tolist(), ['REAGENT-DIALOG-OLD'])
            self.clean(app)
            self.assertEqual(before, self.dump())
            app.button(key='reagent_cancel').click().run()
            self.clean(app)
            self.assertTrue(app.session_state['reagent_lifecycle_dialog']['discard'])
            app.button(key='reagent_discard').click().run()
            self.clean(app)
            self.assertNotIn('reagent_lifecycle_dialog', app.session_state.filtered_state)
            self.assertEqual(app.session_state['reagent_selected_lot'], lot)
            self.assertEqual(app.session_state['reference_reagent_product_id'], data['reagent_id'])
            self.assertEqual(before, self.dump())
        app.text_input(key='reagent_search').set_value('REAGENT-DIALOG-OLD').run()
        self.clean(app)
        self.assertEqual(self.table(app, 'reagent_lots_').value['试剂批号'].tolist(), ['REAGENT-DIALOG-OLD'])
        app.button(key='reagent_back_to_products').click().run()
        self.clean(app)
        self.assertFalse(any('reagent_lots_' in table.proto.id for table in app.dataframe))
        self.assertEqual(app.session_state['md_selected_reagent'], data['reagent_id'])
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.select(app, 'md_table_reagent_', '试剂名称', '另一试剂产品')
        self.assertEqual(app.session_state['md_selected_reagent'], other_reagent)
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        app.button(key='reagent_manage_selected_lots').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['reference_reagent_product_id'], other_reagent)
        self.assertFalse(any(box.key == 'reagent_product_filter' for box in app.selectbox))
        self.assertEqual(app.text_input(key='reagent_search').value, '')
        self.assertEqual(self.table(app, 'reagent_lots_').value['试剂批号'].tolist(), ['OTHER-ONLY'])
        self.assertIsNone(app.session_state['reagent_selected_lot'])
        self.assertEqual(before, self.dump())

    def test_new_reagent_stays_in_filtered_list_then_registers_only_its_bound_lot(self):
        data = _seed_v11_configuration_dependencies()
        app = self.app('reagent')
        app.text_input(key='md_search_reagent').set_value('V11 配套试剂').run()
        app.button(key='md_create_reagent').click().run()
        self.clean(app)
        app.selectbox(key=field_key(app, 'manufacturer_id')).set_value(data['manufacturer_id'])
        app.text_input(key=field_key(app, 'generic_name')).set_value('新登记的空批号试剂')
        app.button(key='md_dialog_save').click().run()
        self.clean(app)
        self.assertFalse(list(app.error))
        product_id = app.session_state['md_selected_reagent']
        self.assertNotEqual(product_id, data['reagent_id'])
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')
        self.assertIn('新登记的空批号试剂', self.table(app, 'md_table_reagent_').value['试剂名称'].tolist())
        self.assertFalse(any('reagent_lots_' in table.proto.id for table in app.dataframe))
        app.button(key='md_edit_reagent').click().run()
        self.clean(app)
        app.text_input(key=field_key(app, 'generic_name')).set_value('编辑后的空批号试剂')
        app.button(key='md_dialog_save').click().run()
        self.clean(app)
        self.assertFalse(list(app.error))
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')
        self.assertIn('编辑后的空批号试剂', self.table(app, 'md_table_reagent_').value['试剂名称'].tolist())
        app.button(key='reagent_manage_selected_lots').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['reference_reagent_product_id'], product_id)
        self.assertTrue(any(item.value == '编辑后的空批号试剂' for item in app.subheader))
        self.assertFalse(any('reagent_lots_' in table.proto.id for table in app.dataframe))
        self.assertFalse(app.button(key='reagent_register').disabled)
        app.button(key='reagent_register').click().run()
        self.clean(app)
        modal = app.session_state['reagent_lifecycle_dialog']
        self.assertEqual(modal['bound_product_id'], product_id)
        self.assertEqual(modal['draft']['product'], product_id)
        prefix = 'rgl_' + modal['token'] + '_'
        self.assertFalse(any(box.key == prefix + 'product' for box in app.selectbox))
        app.text_input(key=prefix + 'lot_no').set_value('NEW-PRODUCT-LOT')
        app.date_input(key=prefix + 'expiry').set_value(date(2029, 12, 31))
        app.button(key='reagent_save').click().run()
        self.clean(app)
        self.assertFalse(list(app.error), [item.value for item in app.error])
        self.assertEqual(app.session_state['reference_reagent_product_id'], product_id)
        self.assertEqual(self.table(app, 'reagent_lots_').value['试剂批号'].tolist(), ['NEW-PRODUCT-LOT'])
        with database.read_snapshot() as connection:
            rows = connection.execute('SELECT reagent_id,lot_no FROM md_reagent_lots').fetchall()
            self.assertEqual([tuple(row) for row in rows], [(product_id, 'NEW-PRODUCT-LOT')])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM qc_lot_change_events').fetchone()[0], 0)
        app.button(key='reagent_back_to_products').click().run()
        self.clean(app)
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')
        self.assertIn('编辑后的空批号试剂', self.table(app, 'md_table_reagent_').value['试剂名称'].tolist())

    def test_reagent_list_edit_cancel_disable_and_restore_keep_selection_and_history(self):
        data, systems, lot = reagent_fixture()
        product_id = data['reagent_id']
        record_lot_verification(template_item_id=systems[0]['template_item_id'], system_id=systems[0]['id'],
            reagent_lot_id=lot, conclusion='pass', evidence='列表维护前已保存的验证依据',
            confirmed_by='记录核对人', confirmed_at='2026-09-01')
        with database.read_snapshot() as connection:
            history = {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)]
                       for table in ('md_reagent_lots', 'qc_lot_verifications', 'qc_lot_change_events')}
        app = self.app('reagent')
        app.text_input(key='md_search_reagent').set_value('V11 配套试剂').run()
        self.select(app, 'md_table_reagent_', '试剂名称', 'V11 配套试剂')
        before = self.dump()
        app.button(key='md_edit_reagent').click().run()
        self.clean(app)
        original_notes = app.text_area(key=field_key(app, 'notes')).value
        app.text_area(key=field_key(app, 'notes')).set_value('应放弃的试剂备注').run()
        self.assertEqual(before, self.dump())
        app.button(key='md_dialog_cancel').click().run()
        self.clean(app)
        self.assertTrue(app.session_state['master_data_dialog']['discard'])
        app.button(key='md_dialog_discard').click().run()
        self.clean(app)
        self.assertEqual(before, self.dump())
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        app.button(key='md_edit_reagent').click().run()
        self.assertEqual(app.text_area(key=field_key(app, 'notes')).value, original_notes)
        app.text_area(key=field_key(app, 'notes')).set_value('主列表保存的试剂备注')
        app.button(key='md_dialog_save').click().run()
        self.clean(app)
        self.assertFalse(list(app.error))
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')

        before = self.dump()
        app.button(key='md_status_reagent').click().run()
        self.clean(app)
        app.text_area(key=field_key(app, 'reason')).set_value('取消停用，不应写入').run()
        app.button(key='md_status_cancel').click().run()
        self.clean(app)
        self.assertEqual(before, self.dump())
        app.button(key='md_status_reagent').click().run()
        app.button(key='md_status_confirm').click().run()
        self.clean(app)
        self.assertTrue(list(app.error))
        self.assertEqual(before, self.dump())
        app.text_area(key=field_key(app, 'reason')).set_value('试剂停止采购')
        app.button(key='md_status_confirm').click().run()
        self.clean(app)
        self.assertFalse(list(app.error))
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')
        self.assertTrue(app.checkbox(key='md_show_disabled_reagent').value)
        self.assertTrue(app.button(key='md_edit_reagent').disabled)
        self.assertEqual(app.button(key='md_status_reagent').label, '恢复试剂')
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        shown = self.table(app, 'md_table_reagent_').value
        self.assertEqual(shown['状态'].tolist(), ['已停用'])

        before = self.dump()
        app.button(key='md_status_reagent').click().run()
        app.button(key='md_status_cancel').click().run()
        self.clean(app)
        self.assertEqual(before, self.dump())
        app.button(key='md_status_reagent').click().run()
        app.button(key='md_status_confirm').click().run()
        self.clean(app)
        self.assertFalse(list(app.error))
        self.assertEqual(app.session_state['md_selected_reagent'], product_id)
        self.assertFalse(app.button(key='md_edit_reagent').disabled)
        self.assertEqual(app.button(key='md_status_reagent').label, '停用试剂')
        self.assertEqual(app.text_input(key='md_search_reagent').value, 'V11 配套试剂')
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        with database.read_snapshot() as connection:
            record = connection.execute('SELECT is_disabled,notes FROM md_reagents WHERE id=?', (product_id,)).fetchone()
            self.assertEqual(tuple(record), (0, '主列表保存的试剂备注'))
            for table, records in history.items():
                self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM ' + table)], records, table)

    def test_reagent_filter_clears_hidden_selection_until_another_product_is_chosen(self):
        data = _seed_v11_configuration_dependencies()
        other = master.create_reagent(manufacturer_id=data['manufacturer_id'], generic_name='另一试剂产品')
        before = self.dump()
        app = self.app('reagent')
        self.select(app, 'md_table_reagent_', '试剂名称', 'V11 配套试剂')
        app.text_input(key='md_search_reagent').set_value('另一试剂产品').run()
        self.clean(app)
        self.assertIsNone(app.session_state['md_selected_reagent'])
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        for key in ('md_edit_reagent', 'md_status_reagent', 'reagent_manage_selected_lots'):
            self.assertTrue(app.button(key=key).disabled)
        self.assertEqual(self.table(app, 'md_table_reagent_').value['试剂名称'].tolist(), ['另一试剂产品'])
        self.select(app, 'md_table_reagent_', '试剂名称', '另一试剂产品')
        self.assertEqual(app.session_state['md_selected_reagent'], other)
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        app.button(key='reagent_manage_selected_lots').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['reference_reagent_product_id'], other)
        app.button(key='reagent_back_to_products').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['md_selected_reagent'], other)
        self.assertEqual(app.text_input(key='md_search_reagent').value, '另一试剂产品')
        self.assertEqual(before, self.dump())

    def test_disabled_reagent_cannot_register_but_its_lots_and_verifications_remain_readable(self):
        data, systems, lot = reagent_fixture()
        record_lot_verification(template_item_id=systems[0]['template_item_id'], system_id=systems[0]['id'],
            reagent_lot_id=lot, conclusion='pass', evidence='停用后仍可核对的验证依据',
            confirmed_by='历史确认人', confirmed_at='2026-09-01')
        master.set_master_entity_disabled('reagent', data['reagent_id'], is_disabled=True, reason='停用验收')
        before = self.dump()
        app = self.app('reagent')
        app.checkbox(key='md_show_disabled_reagent').check().run()
        self.clean(app)
        self.select(app, 'md_table_reagent_', '试剂名称', 'V11 配套试剂')
        self.assertEqual(app.session_state['md_selected_reagent'], data['reagent_id'])
        self.assertNotIn('reference_reagent_product_id', app.session_state.filtered_state)
        self.assertTrue(app.button(key='md_edit_reagent').disabled)
        self.assertEqual(app.button(key='md_status_reagent').label, '恢复试剂')
        app.button(key='reagent_manage_selected_lots').click().run()
        self.clean(app)
        self.assertEqual(app.session_state['reference_reagent_product_id'], data['reagent_id'])
        self.assertTrue(app.button(key='reagent_register').disabled)
        app.checkbox(key='reagent_show_disabled').check().run()
        self.clean(app)
        self.assertEqual(self.table(app, 'reagent_lots_').value['试剂批号'].tolist(), ['REAGENT-DIALOG-OLD'])
        self.select(app, 'reagent_lots_', '试剂批号', 'REAGENT-DIALOG-OLD')
        self.assertTrue(app.button(key='reagent_verify').disabled)
        self.assertTrue(app.button(key='reagent_switch').disabled)
        history = next(table.value for table in app.dataframe if '验证依据' in table.value.columns)
        self.assertEqual(history['验证依据'].tolist(), ['停用后仍可核对的验证依据'])
        self.assertEqual(history['确认人'].tolist(), ['历史确认人'])
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
