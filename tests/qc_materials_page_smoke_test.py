"""The shared catalogue, focused dialogs and cancellation on disposable databases."""
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest
from tests.project_workspace_smoke_test import select_table_row
from tests import product_directory_smoke_test as fixture
from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies, _build_active_source_config
from services.product_directory_service import list_directory_products, get_product_relationships
from services.master_data_service import create_qc_material, create_test_item
from services.material_catalog_service import get_material_product, list_control_materials


def isolated_page():
    import streamlit as st
    if st.session_state.get('show_project_management_page'):
        from ui.qc_replacement_workspace import render_pending_qc_replacement_dialog
        render_pending_qc_replacement_dialog()
    else:
        from pages.qc_materials_page import render_qc_materials_page
        render_qc_materials_page()


class QCMaterialsPageTest(unittest.TestCase):
    setUp = fixture.DirectoryTest.setUp
    tearDown = fixture.DirectoryTest.tearDown
    dump = fixture.DirectoryTest.dump
    publish = fixture.DirectoryTest.publish

    def healthy(self, app):
        self.assertFalse(list(app.exception), str(list(app.exception)))

    def app(self):
        app = AppTest.from_function(isolated_page, default_timeout=30).run()
        self.healthy(app)
        return app

    def select_product(self, app, query):
        app.text_input(key='product_catalogue_search').set_value(query).run()
        index = next(i for i, table in enumerate(app.dataframe) if 'product_catalogue_table_' in table.proto.id)
        select_table_row(app, 0, index=index)
        self.healthy(app)
        return app.session_state['product_catalogue_selected_id']

    def click(self, app, key):
        app.button(key=key).click().run()
        self.healthy(app)

    def test_shared_catalogue_selection_stays_on_page_and_gates_actions(self):
        _, release = self.publish()
        record = list_directory_products(release['release_id']).iloc[-1]
        before = self.dump()
        for app in [self.app(), AppTest.from_string('from pages.master_data_page import render_master_data_page\nrender_master_data_page()', default_timeout=30).run()]:
            self.healthy(app)
            self.assertNotIn('批号与关联项目', [tab.label for tab in app.tabs])
            self.assertTrue(any('产品编号' in frame.value and len(frame.value) == 1054 for frame in app.dataframe))
            self.assertTrue(app.button(key='product_catalogue_batches').disabled)
            self.assertTrue(app.button(key='material_catalog_edit_product').disabled)
            self.assertEqual(self.select_product(app, record['product_code']), int(record['product_id']))
            self.assertNotIn('material_dialog', app.session_state)
            self.assertFalse(app.button(key='product_catalogue_batches').disabled)
            self.assertFalse(any(b.key == 'material_catalog_add' for b in app.button))
            self.click(app, 'product_catalogue_batches')
            self.assertEqual(app.session_state['material_dialog']['product_id'], int(record['product_id']))
            self.assertFalse(any(b.label == '选择检验项目' for b in app.button))
            self.click(app, 'material_manager_close')
            self.assertEqual(app.text_input(key='product_catalogue_search').value, record['product_code'])
            self.assertEqual(app.session_state['product_catalogue_selected_id'], int(record['product_id']))
            app.text_input(key='product_catalogue_search').set_value('不存在的产品编号999').run()
            self.assertTrue(app.button(key='product_catalogue_batches').disabled)
            self.assertIsNone(app.session_state['product_catalogue_selected_id'])
        self.assertEqual(before, self.dump())

    def test_add_edit_disable_restore_on_main_page(self):
        app = self.app()
        self.click(app, 'material_catalog_add_product')
        prefix = f"product_edit_{app.session_state['material_dialog_nonce']}"
        app.selectbox(key=prefix+'_manufacturer').set_value(self.manufacturer)
        app.text_input(key=prefix+'_generic_name').set_value('名录人工新增产品')
        app.text_input(key=prefix+'_catalog_no').set_value('LOCAL-9001')
        self.click(app, prefix+'_save')
        product = app.session_state['product_catalogue_selected_id']
        self.assertEqual(get_material_product(product)['generic_name'], '名录人工新增产品')
        self.assertTrue(any('名录人工新增产品' in frame.value.get('产品名称', []).tolist() for frame in app.dataframe))
        self.click(app, 'material_catalog_edit_product')
        prefix = f"product_edit_{app.session_state['material_dialog_nonce']}"
        app.text_input(key=prefix+'_generic_name').set_value('名录编辑后产品')
        self.click(app, prefix+'_save')
        self.assertEqual(get_material_product(product)['generic_name'], '名录编辑后产品')
        self.click(app, 'material_catalog_status_product')
        prefix = f"material_status_{app.session_state['material_dialog_nonce']}"
        self.click(app, prefix+'_confirm')
        self.assertFalse(get_material_product(product)['is_disabled'])
        app.text_area(key=prefix+'_reason').set_value('停止采购')
        self.click(app, prefix+'_confirm')
        self.assertTrue(get_material_product(product)['is_disabled'])
        self.assertEqual(app.button(key='material_catalog_status_product').label, '恢复质控品')
        self.assertTrue(app.button(key='material_catalog_edit_product').disabled)
        self.click(app, 'material_catalog_status_product')
        prefix = f"material_status_{app.session_state['material_dialog_nonce']}"
        self.click(app, prefix+'_confirm')
        self.assertFalse(get_material_product(product)['is_disabled'])

    def test_batch_editor_returns_to_same_product_and_preserves_search(self):
        first = create_qc_material(manufacturer_id=self.manufacturer, generic_name='批次产品甲', catalog_no='LOT-9001')
        other = create_qc_material(manufacturer_id=self.manufacturer, generic_name='批次产品乙', catalog_no='LOT-9002')
        app = self.app()
        self.select_product(app, 'LOT-9001')
        self.click(app, 'product_catalogue_batches')
        app.text_input(key='material_catalog_search').set_value('原搜索条件').run()
        before = self.dump()
        self.click(app, 'material_catalog_add')
        prefix = f"material_edit_{app.session_state['material_dialog_nonce']}"
        self.click(app, prefix+'_cancel')
        self.assertEqual(app.session_state['material_dialog']['kind'], 'batches')
        self.assertEqual(app.text_input(key='material_catalog_search').value, '原搜索条件')
        self.assertEqual(before, self.dump())
        self.click(app, 'material_catalog_add')
        prefix = f"material_edit_{app.session_state['material_dialog_nonce']}"
        app.text_input(key=prefix+'_new_name').set_value('低值')
        app.text_input(key=prefix+'_lot').set_value('REAL-LOT-A')
        app.date_input(key=prefix+'_expiry').set_value(date(2028, 1, 1))
        self.click(app, prefix+'_save')
        self.assertEqual(app.session_state['material_dialog']['kind'], 'batches')
        self.assertEqual(len(list_control_materials(first)), 1)
        self.assertTrue(list_control_materials(other).empty)
        self.assertEqual(app.text_input(key='material_catalog_search').value, '原搜索条件')
        self.assertTrue(any('批号' in t.value and 'REAL-LOT-A' in t.value['批号'].tolist() for t in app.dataframe))
        self.click(app, 'material_catalog_status')
        prefix = f"material_status_{app.session_state['material_dialog_nonce']}"
        app.text_area(key=prefix+'_reason').set_value('停用核对')
        self.click(app, prefix+'_confirm')
        self.assertEqual(app.button(key='material_catalog_status').label, '恢复')
        self.click(app, 'material_manager_close')
        self.assertEqual(app.session_state['product_catalogue_selected_id'], first)
        self.select_product(app, 'LOT-9002')
        self.click(app, 'product_catalogue_batches')
        self.assertEqual(app.text_input(key='material_catalog_search').value, '')
        self.assertFalse(any('批号' in t.value and 'REAL-LOT-A' in t.value['批号'].tolist() for t in app.dataframe))

    def test_relationship_picker_draft_cancel_save_and_dirty_close(self):
        product = create_qc_material(manufacturer_id=self.manufacturer, generic_name='关联产品', catalog_no='COVER-9001')
        item = create_test_item(chinese_name='关联测试项目', standard_code='COVER-ITEM')
        app = self.app()
        self.select_product(app, 'COVER-9001')
        self.click(app, 'product_catalogue_relationships')
        self.assertFalse(any(b.key == 'material_catalog_add' for b in app.button))
        actor = next(w for w in app.text_input if w.label == '核对人')
        actor.set_value('核对员')
        next(w for w in app.text_area if w.label == '参考资料').set_value('产品说明书')
        before = self.dump()
        next(b for b in app.button if b.label == '选择检验项目').click().run()
        self.healthy(app)
        state = app.session_state['material_dialog']
        prefix = f"coverage_picker_{state['nonce']}"
        app.text_input(key=prefix+'_search').set_value('COVER-ITEM').run()
        index = next(i for i, table in enumerate(app.dataframe) if 'coverage_picker_' in table.proto.id)
        select_table_row(app, 0, index=index)
        self.click(app, prefix+'_confirm')
        self.assertEqual(before, self.dump())
        self.assertEqual(app.session_state['material_dialog']['kind'], 'relationships')
        self.assertEqual(next(w for w in app.text_input if w.label == '核对人').value, '核对员')
        self.assertEqual(next(w for w in app.text_area if w.label == '参考资料').value, '产品说明书')
        self.click(app, 'material_manager_close')
        self.assertTrue(any('尚未保存' in w.value for w in app.warning))
        next(b for b in app.button if b.label == '保存适用项目').click().run()
        self.healthy(app)
        self.assertFalse(any('尚未保存' in w.value for w in app.warning))
        self.assertEqual([r['test_item_id'] for r in get_product_relationships(product)['coverage'] if not r['is_disabled']], [item])
        self.click(app, 'material_manager_close')
        self.assertNotIn('material_dialog', app.session_state)
        self.click(app, 'product_catalogue_relationships')
        before = self.dump()
        next(w for w in app.text_input if w.label == '核对人').set_value('未保存人').run()
        self.click(app, 'material_manager_close')
        self.click(app, 'material_manager_discard')
        self.assertEqual(before, self.dump())
        self.click(app, 'product_catalogue_relationships')
        self.assertEqual(next(w for w in app.text_input if w.label == '核对人').value, '')
        next(w for w in app.text_input if w.label == '核对人').set_value('重复核对').run()
        next(w for w in app.text_area if w.label == '参考资料').set_value('相同关联无需变更').run()
        self.click(app, 'material_manager_close')
        next(b for b in app.button if b.label == '保存适用项目').click().run()
        self.healthy(app)
        self.assertFalse(any('尚未保存' in w.value for w in app.warning))
        self.click(app, 'material_manager_close')
        self.assertNotIn('material_dialog', app.session_state)

    def test_usage_and_replacement_navigation_close_current_dialog(self):
        data = _seed_v11_configuration_dependencies()
        template_id, config_id = _build_active_source_config(data)
        product = int(data['qc_material_id'])
        before = self.dump()
        for action, manager in [('project', 'relationships'), ('config', 'batches'), ('replace', 'batches')]:
            app = self.app()
            self.select_product(app, get_material_product(product)['generic_name'])
            self.click(app, 'product_catalogue_'+manager)
            key = f'material_usage_{"replace" if action == "replace" else "open_"+action}_{product}'
            self.click(app, key)
            self.assertNotIn('material_dialog', app.session_state)
            if action == 'project':
                self.assertEqual(app.session_state['workspace_project_id'], template_id)
            elif action == 'config':
                self.assertEqual(app.session_state['v11_pending_existing_config_id'], config_id)
            else:
                self.assertEqual(app.session_state['qc_replacement_dialog']['config_id'], config_id)
                self.click(app, 'qc_replace_cancel')
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
