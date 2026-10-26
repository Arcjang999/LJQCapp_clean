"""Setup blockers explain their matching action without weakening validation."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.project_config_service import list_lot_config_items, set_project_template_disabled
from services.project_workspace_service import save_project_details
from tests.master_data_dialogs_smoke_test import snapshot
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config,
)
from tests.project_workspace_smoke_test import assert_clean, workspace_page, values as project_values
from tests.project_dependencies_ui_smoke_test import dependency_prefix
from tests.quick_project_setup_smoke_test import metadata_values
from tests.quick_project_setup_ui_smoke_test import open_details, product_prefix


def batch_workspace_page():
    from ui.batch_workspace import render_batch_workspace
    render_batch_workspace()


def feedback(app):
    return '\n'.join(item.value for kind in ('info', 'caption', 'warning') for item in getattr(app, kind))


class SetupActionFeedbackTests(unittest.TestCase):
    def test_new_group_save_explains_unconfirmed_product_and_clears_after_confirmation(self):
        with TemporaryDatabaseContext():
            app = open_details(metadata_values())
            app.button(key='quick_use_group').click().run()
            app.button(key='setup_mode_change').click().run()
            assert_clean(app)
            self.assertTrue(app.button(key='project_dialog_save').disabled)
            self.assertIn('“保存项目”暂不可用', feedback(app))
            self.assertIn('确认所选质控品', feedback(app))
            before = snapshot()
            prefix = product_prefix(app)
            app.text_input(key=prefix+'_person').set_value('操作提示核对人')
            app.text_area(key=prefix+'_evidence').set_value('已核对本仪器和试剂的适用性')
            app.button(key=prefix+'_confirm').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='project_dialog_save').disabled)
            self.assertNotIn('“保存项目”暂不可用', feedback(app))
            self.assertEqual(before, snapshot())

    def test_product_manual_missing_fields_name_the_exact_inputs(self):
        with TemporaryDatabaseContext():
            app = open_details(metadata_values())
            prefix = product_prefix(app)
            ctx = app.session_state['project_workspace_dialog']
            ctx['draft'].pop('product_choice_review', None)
            app.text_input(key=prefix+'_person').set_value('')
            app.text_area(key=prefix+'_evidence').set_value('')
            before = snapshot()
            app.run()
            assert_clean(app)
            self.assertIn('请填写上方的“质控品确认人”、“为什么选择此质控品”', feedback(app))
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertEqual(before, snapshot())

    def test_panel_source_preview_explains_required_selection(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            tid, _ = _build_active_source_config(data)
            app = AppTest.from_function(workspace_page, default_timeout=30)
            app.session_state['workspace_project_id'] = tid
            app.run()
            app.button(key='workspace_add_panel').click().run()
            token = app.session_state['project_workspace_dialog']['token']
            app.radio(key='panel_source_'+token).set_value('选择检验项目').run()
            assert_clean(app)
            self.assertTrue(app.button(key='panel_source_preview').disabled)
            self.assertIn('“预览整组带入”暂不可用。请在“本次检验项目”中至少选择一项', feedback(app))
            before = snapshot()
            key = 'panel_source_tests_选择检验项目'+token
            app.multiselect(key=key).set_value([data['lj_item_id']]).run()
            assert_clean(app)
            self.assertFalse(app.button(key='panel_source_preview').disabled)
            self.assertNotIn('“预览整组带入”暂不可用', feedback(app))
            self.assertEqual(before, snapshot())

    def test_changed_manual_product_evidence_requires_current_confirmation(self):
        with TemporaryDatabaseContext():
            app = open_details(metadata_values())
            prefix = product_prefix(app)
            self.assertFalse(app.button(key='quick_details_next').disabled)
            before = snapshot()
            app.text_area(key=prefix+'_evidence').set_value('本次重新核对后填写的新依据').run()
            assert_clean(app)
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertIn('质控品确认人或使用说明已修改', feedback(app))
            self.assertEqual('本次重新核对后填写的新依据', app.text_area(key=prefix+'_evidence').value)
            self.assertEqual('人工选品核对人', app.text_input(key=prefix+'_person').value)
            self.assertNotIn('product_choice_review', app.session_state['project_workspace_dialog']['draft'])
            app.button(key=prefix+'_confirm').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='quick_details_next').disabled)
            review = app.session_state['project_workspace_dialog']['draft']['product_choice_review']
            self.assertEqual('本次重新核对后填写的新依据', review['evidence'])
            self.assertNotIn('质控品确认人或使用说明已修改', feedback(app))
            self.assertEqual(before, snapshot())

    def test_reference_registration_explains_blocked_parent_actions(self):
        with TemporaryDatabaseContext():
            app = open_details(metadata_values())
            prefix = dependency_prefix(app)
            app.selectbox(key=prefix+'_type').set_value('method').run()
            before = snapshot()
            app.button(key=prefix+'_open').click().run()
            assert_clean(app)
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertTrue(app.button(key='project_dialog_cancel').disabled)
            self.assertIn('正在登记方法学。请先在本区域点击“保存”或“取消”', feedback(app))
            app.button(key='md_dialog_cancel').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='quick_details_next').disabled)
            self.assertNotIn('正在登记方法学。', feedback(app))
            self.assertEqual(before, snapshot())

    def test_batch_creation_distinguishes_unconfirmed_and_disabled_projects(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            tid = save_project_details(project_values(data, '尚未确认的项目'))
            app = AppTest.from_function(batch_workspace_page, default_timeout=30).run()
            app.selectbox(key='batch_project_filter').set_value(tid).run()
            assert_clean(app)
            self.assertTrue(app.button(key='batch_create').disabled)
            self.assertIn('所选项目的设置尚未确认', feedback(app))
            self.assertIn('返回主页', feedback(app))
            self.assertIn('确认项目设置', feedback(app))
            set_project_template_disabled(tid, is_disabled=True, reason='核对停用项目提示')
            app.run()
            assert_clean(app)
            self.assertTrue(app.button(key='batch_create').disabled)
            self.assertIn('所选项目已停用', feedback(app))
            self.assertIn('在“编辑项目”中恢复', feedback(app))
            self.assertNotIn('所选项目的设置尚未确认', feedback(app))

    def test_readonly_missing_levels_does_not_point_to_disabled_edit_button(self):
        with TemporaryDatabaseContext():
            app = AppTest.from_string('''
from unittest.mock import patch
from ui.batch_workspace import render_batch_item_detail
context={'item': {'test_item_name':'只读项目','qc_method':'lj','method_name':'方法',
 'reagent_name':'试剂','unit_symbol':'U/L','quality_goal_json':'{}','quality_review_json':'{}','cv_limit':None},
 'levels':[],'binding':None,'editable':False,'read_only_reason':'本批次设置已固定。'}
with patch('services.batch_edit_service.get_batch_item_context',return_value=context):
 render_batch_item_detail(1)
''', default_timeout=30).run()
            assert_clean(app)
            self.assertTrue(app.button(key='batch_edit_levels_1').disabled)
            self.assertIn('当前批次仅供查看，不能在这里补填', feedback(app))
            self.assertNotIn('尚未选择质控品水平，请点击“编辑水平设置”', feedback(app))

    def test_panel_product_confirmation_explains_unchecked_confirmation(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            tid, _ = _build_active_source_config(data)
            app = AppTest.from_string('''
import streamlit as st
from services.project_config_service import get_project_template,list_template_items
from ui.qc_product_choice import render_panel_product_review
tid=st.session_state['fixture_template']
ctx={'token':'feedback','draft':{'rows':list_template_items(tid).to_dict('records')}}
render_panel_product_review(ctx,get_project_template(tid))
''', default_timeout=30)
            app.session_state['fixture_template'] = tid
            before = snapshot()
            app.run()
            assert_clean(app)
            self.assertTrue(app.button(key='panel_product_feedback_confirm').disabled)
            self.assertIn('勾选“已确认此质控品可用于以上各项的试剂和检测方法”', feedback(app))
            app.checkbox(key='panel_product_feedback_checked').check().run()
            assert_clean(app)
            self.assertFalse(app.button(key='panel_product_feedback_confirm').disabled)
            self.assertNotIn('请核对以上项目，并勾选', feedback(app))
            self.assertEqual(before, snapshot())

    def test_replacement_save_explains_empty_item_selection(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            _, config_id = _build_active_source_config(data)
            app = AppTest.from_string('''
from services.project_config_service import list_lot_configs
from ui.materials import render_material_replacement
render_material_replacement(list_lot_configs(include_disabled=True))
''', default_timeout=30).run()
            app.selectbox(key='material_copy_source').set_value(config_id).run()
            assert_clean(app)
            before = snapshot()
            app.multiselect(key=f'material_copy_items_{config_id}').set_value([]).run()
            assert_clean(app)
            self.assertTrue(app.button(key='material_copy_save').disabled)
            self.assertIn('“保存新批次”暂不可用。请在上方“需要换批的检验项目”中至少选择一项', feedback(app))
            item_id = int(list_lot_config_items(config_id).iloc[0].id)
            app.multiselect(key=f'material_copy_items_{config_id}').set_value([item_id]).run()
            assert_clean(app)
            self.assertFalse(app.button(key='material_copy_save').disabled)
            self.assertNotIn('“保存新批次”暂不可用', feedback(app))
            self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
