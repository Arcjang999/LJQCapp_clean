"""Product-specific entry points must keep registration in the active editor."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

from services.material_workflow_service import available_materials
from services.master_data_service import list_manufacturers, list_qc_materials
from tests.material_catalog_smoke_test import catalogue_page
from database import get_connection
from tests.quick_project_setup_smoke_test import metadata_values
from tests.quick_project_setup_ui_smoke_test import open_details
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config,
)
from tests.qc_replacement_dialogs_smoke_test import app_page
from tests.project_workspace_smoke_test import assert_clean


class RelatedMaterialNavigationTests(unittest.TestCase):
    def test_project_registers_product_and_manufacturer_in_one_window(self):
        with TemporaryDatabaseContext():
            values = metadata_values()
            app = open_details(values)
            parent = app.session_state['project_workspace_dialog']
            key = 'project_dependency_' + parent['token']
            app.selectbox(key=key + '_type').set_value('qc_material').run()
            app.button(key=key + '_open').click().run()
            assert_clean(app)
            self.assertEqual(1, len(app.get('dialog')))
            editor = app.session_state['project_workspace_dialog']['dependency_registration']['editor']
            prefix = 'product_edit_' + editor['nonce']
            app.text_input(key=prefix + '_generic_name').set_value('建项时补充的质控品')
            app.button(key=prefix + '_add_manufacturer').click().run()
            child = editor['manufacturer_editor']
            child_prefix = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=child_prefix + 'display_name').set_value('建项时补充的厂家')
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            self.assertEqual('建项时补充的质控品', app.text_input(key=prefix + '_generic_name').value)
            app.button(key=prefix + '_save').click().run()
            assert_clean(app)
            draft = app.session_state['project_workspace_dialog']['draft']
            products = list_qc_materials()
            product = products.loc[products['id'] == draft['qc_material_id']].iloc[0]
            self.assertEqual('建项时补充的质控品', product['generic_name'])
            self.assertNotEqual(values['qc_material_id'], draft['qc_material_id'])
            self.assertEqual(values['template_name'], draft['template_name'])
            self.assertEqual(values['config_name'], draft['config_name'])
            self.assertEqual('完整项目草稿备注', draft['notes'])
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertEqual(1, len(app.get('dialog')))
            with get_connection() as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])

    def test_product_can_register_a_manufacturer_without_losing_product_draft(self):
        with TemporaryDatabaseContext():
            app = AppTest.from_function(catalogue_page, default_timeout=20).run()
            app.button(key='material_catalog_add_product').click().run()
            state = app.session_state['material_dialog']
            prefix = 'product_edit_' + str(state['nonce'])
            app.text_input(key=prefix + '_generic_name').set_value('现场准备的质控品')
            app.text_input(key=prefix + '_catalog_no').set_value('NEW-CONTROL-001')
            app.text_area(key=prefix + '_notes').set_value('登记厂家后继续保留')
            app.button(key=prefix + '_add_manufacturer').click().run()
            assert_clean(app)
            self.assertEqual(1, len(app.get('dialog')))
            child = app.session_state['material_dialog']['manufacturer_editor']
            key = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=key + 'display_name').set_value('新质控品厂商')
            app.multiselect(key=key + 'categories').set_value(['instrument'])
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            self.assertTrue(any('请保留' in item.value for item in app.error))
            self.assertTrue(list_qc_materials().empty)
            self.assertTrue(list_manufacturers().empty)
            app.multiselect(key=key + 'categories').set_value(['qc_material'])
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            self.assertEqual('现场准备的质控品', app.text_input(key=prefix + '_generic_name').value)
            self.assertEqual('NEW-CONTROL-001', app.text_input(key=prefix + '_catalog_no').value)
            self.assertEqual('登记厂家后继续保留', app.text_area(key=prefix + '_notes').value)
            maker = int(list_manufacturers().iloc[0]['id'])
            self.assertEqual(maker, app.selectbox(key=prefix + '_manufacturer').value)
            self.assertTrue(list_qc_materials().empty)
            app.button(key=prefix + '_save').click().run()
            assert_clean(app)
            self.assertEqual('现场准备的质控品', list_qc_materials().iloc[0]['generic_name'])

    def test_cancel_manufacturer_returns_product_text_without_writes(self):
        with TemporaryDatabaseContext():
            app = AppTest.from_function(catalogue_page, default_timeout=20).run()
            app.button(key='material_catalog_add_product').click().run()
            state = app.session_state['material_dialog']
            prefix = 'product_edit_' + str(state['nonce'])
            app.text_input(key=prefix + '_generic_name').set_value('取消厂家后保留的产品')
            app.button(key=prefix + '_add_manufacturer').click().run()
            child = app.session_state['material_dialog']['manufacturer_editor']
            key = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=key + 'display_name').set_value('未保存厂家').run()
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_continue').click().run()
            self.assertEqual('未保存厂家', app.text_input(key=key + 'display_name').value)
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_discard').click().run()
            assert_clean(app)
            self.assertEqual('取消厂家后保留的产品', app.text_input(key=prefix + '_generic_name').value)
            self.assertTrue(list_qc_materials().empty)
            self.assertTrue(list_manufacturers().empty)

    def test_list_registration_opens_one_bound_dialog_and_cancel_keeps_source(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            _, source = _build_active_source_config(data)
            app = AppTest.from_function(app_page, default_timeout=20)
            app.session_state['qc_replace_selected_config'] = source
            app.run()
            before = available_materials(data['qc_material_id']).to_json(orient='records')
            app.button(key=f'qc_replace_list_register_{source}_register').click().run()
            assert_clean(app)
            self.assertEqual(1, len(app.get('dialog')))
            parent = app.session_state['qc_replacement_dialog']
            self.assertEqual(source, parent['config_id'])
            key = 'qc_replace_register_' + parent['token']
            state = app.session_state[key + '_registration']
            self.assertEqual(data['qc_material_id'], state['product_id'])
            self.assertTrue(app.button(key='qc_replace_cancel').disabled)
            self.assertTrue(app.button(key='qc_replace_save').disabled)
            editor = 'material_edit_' + state['nonce']
            app.text_input(key=editor + '_new_name').set_value('未保存的浓度水平').run()
            app.button(key=editor + '_cancel').click().run()
            app.button(key=editor + '_continue').click().run()
            assert_clean(app)
            self.assertEqual('未保存的浓度水平', app.text_input(key=editor + '_new_name').value)
            app.button(key=editor + '_cancel').click().run()
            app.button(key=editor + '_discard_confirm').click().run()
            assert_clean(app)
            self.assertEqual(source, app.session_state['qc_replacement_dialog']['config_id'])
            self.assertFalse(app.button(key='qc_replace_cancel').disabled)
            self.assertEqual(before, available_materials(data['qc_material_id']).to_json(orient='records'))


if __name__ == '__main__':
    unittest.main()
