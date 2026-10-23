"""Project dependency entry uses the live editor without abandoning parent drafts."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

from database import get_connection
from services.master_data_edit_service import get_master_record_context
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.master_data_dialogs_smoke_test import make_app, open_dialog, fill, field_key, snapshot
from tests.project_workspace_smoke_test import workspace_page, assert_clean, draft_key
from tests.project_workspace_smoke_test import values as project_values
from services.project_workspace_service import save_project_details
from tests.quick_project_setup_smoke_test import metadata_values
from tests.quick_project_setup_ui_smoke_test import open_details, product_prefix


def dependency_prefix(app):
    return 'project_dependency_' + app.session_state['project_workspace_dialog']['token']


def inline_key(app, field):
    editor = app.session_state['project_workspace_dialog']['dependency_registration']['editor']
    while editor.get('dependency'):
        editor = editor['dependency']['state']
    return 'md_dialog_' + editor['token'] + '_' + field


class ProjectDependenciesUiTests(unittest.TestCase):
    def test_mode_switch_reads_latest_payload_before_both_confirmations(self):
        with IsolatedDatabase():
            app = open_details(metadata_values())
            for button, expected, name in (('quick_use_group', 'project', '点击切换时刚填的组名'),
                                           ('group_use_quick', 'quick', '点击切换时刚填的单项名')):
                app.text_input(key=draft_key(app, 'template_name')).set_value(name)
                app.text_input(key=draft_key(app, 'project_group')).set_value('同次点击提交的分组')
                app.text_area(key=draft_key(app, 'notes')).set_value('同次点击提交的备注')
                app.button(key=button).click().run()
                assert_clean(app)
                draft = app.session_state['project_workspace_dialog']['draft']
                self.assertEqual(name, draft['template_name'])
                self.assertEqual('同次点击提交的分组', draft['project_group'])
                self.assertEqual('同次点击提交的备注', draft['notes'])
                app.button(key='setup_mode_change').click().run()
                assert_clean(app)
                ctx = app.session_state['project_workspace_dialog']
                self.assertEqual(expected, ctx['kind'])
                self.assertEqual(name, app.text_input(key=draft_key(app, 'template_name')).value)
                self.assertEqual('同次点击提交的备注', app.text_area(key=draft_key(app, 'notes')).value)

    def test_group_and_item_save_wait_for_unfinished_product_relation(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.button(key='quick_use_group').click().run()
            app.button(key='setup_mode_change').click().run()
            assert_clean(app)
            prefix = product_prefix(app)
            app.text_input(key=prefix + '_person').set_value('分组质控品核对人')
            app.text_area(key=prefix + '_evidence').set_value('已核对本仪器和试剂的适用性')
            app.button(key=prefix + '_confirm').click().run()
            self.assertFalse(app.button(key='project_dialog_save').disabled)
            app.button(key=prefix + '_relation_add').click().run()
            self.assertTrue(app.button(key='project_dialog_save').disabled)
            editor = app.session_state['project_workspace_dialog'][prefix + '_editor']
            ep = prefix + '_' + editor['token']
            app.button(key=ep + '_cancel').click().run()
            self.assertFalse(app.button(key='project_dialog_save').disabled)

            tid = save_project_details(project_values(values, '单项配套记录检查项目'))
            app = AppTest.from_function(workspace_page, default_timeout=30)
            app.session_state['workspace_project_id'] = tid
            app.run()
            app.button(key='workspace_add_item').click().run()
            for field in ('test_item_id', 'method_id', 'unit_id'):
                app.selectbox(key=draft_key(app, field)).set_value(values[field]).run()
            ctx = app.session_state['project_workspace_dialog']
            prefix = 'item_product_' + ctx['token']
            app.button(key=prefix + '_relation_add').click().run()
            assert_clean(app)
            self.assertTrue(app.button(key='project_item_save').disabled)
            editor = app.session_state['project_workspace_dialog'][prefix + '_editor']
            app.button(key=prefix + '_' + editor['token'] + '_cancel').click().run()
            self.assertFalse(app.button(key='project_item_save').disabled)

    def test_empty_panel_coverage_has_direct_selection_and_inline_reference_entry(self):
        with IsolatedDatabase():
            values = metadata_values()
            tid = save_project_details(project_values(values, '整组资料依赖检查项目'))
            app = AppTest.from_function(workspace_page, default_timeout=30)
            app.session_state['workspace_project_id'] = tid
            app.run()
            app.button(key='workspace_add_panel').click().run()
            ctx = app.session_state['project_workspace_dialog']
            token = ctx['token']
            app.radio(key='panel_source_' + token).set_value('质控品适用检验项目').run()
            app.button(key='panel_no_coverage_choose_' + token).click().run()
            assert_clean(app)
            self.assertEqual('选择检验项目', app.radio(key='panel_source_' + token).value)
            prefix = dependency_prefix(app)
            app.selectbox(key=prefix + '_type').set_value('test_item').run()
            app.button(key=prefix + '_open').click().run()
            self.assertTrue(app.button(key='panel_source_cancel').disabled)
            self.assertTrue(app.button(key='panel_source_preview').disabled)
            app.text_input(key=inline_key(app, 'chinese_name')).set_value('整组中补充的检测项')
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            selection = app.multiselect(key='panel_source_tests_选择检验项目' + token)
            self.assertTrue(any('整组中补充的检测项' in label for label in selection.options))
            self.assertEqual('source', app.session_state['project_workspace_dialog']['draft']['stage'])

    def test_new_instrument_and_manufacturer_keep_both_parent_drafts(self):
        with IsolatedDatabase():
            app = AppTest.from_function(workspace_page, default_timeout=30).run()
            app.button(key='home_create_project').click().run()
            name_key = draft_key(app, 'template_name')
            app.text_input(key=name_key).set_value('补资料期间保留项目名称').run()
            self.assertTrue(any('暂无可选资料' in message.value for message in app.info))
            prefix = dependency_prefix(app)
            app.selectbox(key=prefix + '_type').set_value('lab_instrument').run()
            before = snapshot()
            app.button(key=prefix + '_open').click().run()
            assert_clean(app)
            self.assertTrue(app.button(key='project_dialog_cancel').disabled)
            self.assertTrue(app.button(key='quick_use_group').disabled)
            self.assertTrue(app.button(key='quick_details_next').disabled)
            instrument_key = inline_key(app, 'display_name')
            app.text_input(key=instrument_key).set_value('补资料新仪器')
            app.text_input(key=inline_key(app, 'model')).set_value('DEP-01')
            app.text_area(key=inline_key(app, 'notes')).set_value('仪器填写内容不能丢').run()
            editor = app.session_state['project_workspace_dialog']['dependency_registration']['editor']
            original = deepcopy(editor['draft'])
            maker_button = 'md_dialog_' + editor['token'] + '_add_manufacturer'
            app.button(key=maker_button).click().run()
            app.text_input(key=inline_key(app, 'display_name')).set_value('现场补登仪器厂家')
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            editor = app.session_state['project_workspace_dialog']['dependency_registration']['editor']
            maker = editor['draft']['manufacturer_id']
            self.assertEqual('现场补登仪器厂家', get_master_record_context('manufacturer', maker)['record']['display_name'])
            self.assertEqual(['instrument'], get_master_record_context('manufacturer', maker)['record']['categories'])
            for field in ('display_name', 'model', 'notes'):
                self.assertEqual(original[field], editor['draft'][field])
            self.assertEqual(original['notes'], app.text_area(key=inline_key(app, 'notes')).value)
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            draft = app.session_state['project_workspace_dialog']['draft']
            self.assertEqual('补资料期间保留项目名称', app.text_input(key=name_key).value)
            self.assertEqual('补资料新仪器', get_master_record_context('lab_instrument', draft['lab_instrument_id'])['record']['display_name'])
            self.assertEqual(draft['lab_instrument_id'], app.selectbox(key=draft_key(app, 'lab_instrument_id')).value)
            self.assertFalse(app.button(key='project_dialog_cancel').disabled)
            with get_connection() as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])
            self.assertNotEqual(before, snapshot())

    def test_cancel_inline_method_preserves_project_and_writes_nothing(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            original = deepcopy(app.session_state['project_workspace_dialog']['draft'])
            prefix = dependency_prefix(app)
            app.selectbox(key=prefix + '_type').set_value('method').run()
            before = snapshot()
            app.button(key=prefix + '_open').click().run()
            app.text_input(key=inline_key(app, 'method_name')).set_value('尚未保存的方法学').run()
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_continue').click().run()
            self.assertEqual('尚未保存的方法学', app.text_input(key=inline_key(app, 'method_name')).value)
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_discard').click().run()
            assert_clean(app)
            self.assertEqual(original, app.session_state['project_workspace_dialog']['draft'])
            self.assertEqual(before, snapshot())
            self.assertFalse(app.button(key='quick_details_next').disabled)

    def test_unfinished_relation_blocks_forward_and_preserves_values_on_cancel(self):
        with IsolatedDatabase():
            app = open_details(metadata_values())
            prefix = product_prefix(app)
            app.button(key=prefix + '_relation_add').click().run()
            self.assertTrue(app.button(key='quick_details_next').disabled)
            editor = app.session_state['project_workspace_dialog'][prefix + '_editor']
            ep = prefix + '_' + editor['token']
            app.text_input(key=ep + '_scope_note').set_value('尚未保存的适用范围').run()
            self.assertTrue(app.button(key=dependency_prefix(app) + '_open').disabled)
            app.selectbox(key=draft_key(app, 'reagent_id')).set_value(None).run()
            assert_clean(app)
            self.assertEqual('尚未保存的适用范围', app.text_input(key=ep + '_scope_note').value)
            app.button(key=ep + '_cancel').click().run()
            app.button(key=ep + '_keep').click().run()
            self.assertEqual('尚未保存的适用范围', app.text_input(key=ep + '_scope_note').value)
            app.button(key=ep + '_cancel').click().run()
            app.button(key=ep + '_discard').click().run()
            assert_clean(app)
            self.assertNotIn(prefix + '_editor', app.session_state['project_workspace_dialog'])
            self.assertTrue(app.button(key='quick_details_next').disabled)

    def test_inline_product_is_selected_and_requires_its_own_batch(self):
        from services.material_catalog_service import get_material_product
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values, candidate=True)
            original_name = app.text_input(key=draft_key(app, 'template_name')).value
            prefix = dependency_prefix(app)
            app.selectbox(key=prefix + '_type').set_value('qc_material').run()
            app.button(key=prefix + '_open').click().run()
            editor = app.session_state['project_workspace_dialog']['dependency_registration']['editor']
            ep = 'product_edit_' + editor['nonce']
            maker = get_material_product(values['qc_material_id'])['manufacturer_id']
            app.selectbox(key=ep + '_manufacturer').set_value(maker)
            app.text_input(key=ep + '_generic_name').set_value('建项中新增的产品')
            app.text_area(key=ep + '_notes').set_value('新增产品备注')
            app.button(key=ep + '_save').click().run()
            assert_clean(app)
            draft = app.session_state['project_workspace_dialog']['draft']
            self.assertEqual('建项中新增的产品', get_material_product(draft['qc_material_id'])['generic_name'])
            self.assertEqual(draft['qc_material_id'], app.selectbox(key=product_prefix(app) + '_product').value)
            self.assertEqual('人工选品', app.radio(key=product_prefix(app) + '_mode').value)
            self.assertEqual(original_name, app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual([], draft['level_ids'])
            self.assertNotIn('product_choice_review', draft)
            self.assertTrue(app.button(key='quick_details_next').disabled)

    def test_default_unit_dependency_returns_to_unsaved_test_item(self):
        with IsolatedDatabase():
            app = open_dialog(make_app({'record': dict(entity_type='test_item')}))
            fill(app, {'chinese_name': '补充单位时保留检测项', 'notes': '检测项备注'}).run()
            parent_token = app.session_state['master_data_dialog']['token']
            app.button(key='md_dialog_' + parent_token + '_add_unit').click().run()
            child = app.session_state['master_data_dialog']['dependency']['state']
            ep = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=ep + 'symbol').set_value('DEP-unit/L')
            app.text_input(key=ep + 'unit_name').set_value('补充计量单位')
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            parent = app.session_state['master_data_dialog']
            self.assertEqual(parent_token, parent['token'])
            self.assertEqual('补充单位时保留检测项', app.text_input(key=field_key(app, 'chinese_name')).value)
            unit = parent['draft']['default_unit_id']
            self.assertEqual(unit, app.selectbox(key=field_key(app, 'default_unit_id')).value)
            self.assertEqual('DEP-unit/L', get_master_record_context('unit', unit)['record']['symbol'])
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            identifier = app.session_state['md_selected_test_item']
            self.assertEqual(unit, get_master_record_context('test_item', identifier)['record']['default_unit_id'])

    def test_master_dependency_cancel_and_wrong_category_keep_parent(self):
        with IsolatedDatabase():
            app = open_dialog(make_app({'record': dict(entity_type='reagent')}))
            fill(app, {'generic_name': '补厂家时保留试剂', 'notes': '保留试剂备注'}).run()
            original = deepcopy(app.session_state['master_data_dialog']['draft'])
            parent_token = app.session_state['master_data_dialog']['token']
            app.button(key='md_dialog_' + parent_token + '_add_manufacturer').click().run()
            child = app.session_state['master_data_dialog']['dependency']['state']
            child_prefix = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=child_prefix + 'display_name').set_value('试剂现场登记厂家')
            app.multiselect(key=child_prefix + 'categories').set_value(['instrument'])
            before = snapshot()
            app.button(key='md_dialog_save').click().run()
            assert_clean(app)
            self.assertTrue(any('试剂厂家' in message.value for message in app.error))
            self.assertEqual(before, snapshot())
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_discard').click().run()
            assert_clean(app)
            self.assertEqual(parent_token, app.session_state['master_data_dialog']['token'])
            self.assertEqual(original, app.session_state['master_data_dialog']['draft'])
            self.assertEqual('保留试剂备注', app.text_area(key=field_key(app, 'notes')).value)
            self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
