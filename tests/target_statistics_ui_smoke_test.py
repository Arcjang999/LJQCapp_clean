"""Required per-item policy and explicitly opened, cancellable parameter switches."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import add_result, get_connection
from services.target_statistics_policy_service import get_target_statistics_policy
from services.time_service import local_now
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean, draft_key
from tests.quick_project_setup_smoke_test import metadata_values, database_contents
from tests.quick_project_setup_ui_smoke_test import open_details, to_preview, quality_prefix
from tests.target_profile_workspace_smoke_test import fixture, make_app, save, database_state, result_state, key
from services.target_profile_edit_service import get_target_profile_context
from tests.project_workspace_smoke_test import workspace_page, values as project_values
from services.project_workspace_service import save_project_details
from services.master_data_service import create_test_item
from services.project_config_service import save_template_items, get_project_template
from tests.quality_review_fixtures import confirm_fixture_project


def statistics_fixture():
    binding, _ = fixture()
    save(get_target_profile_context(binding['id']))
    now = local_now()
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (month - timedelta(days=1)).replace(day=1)
    for day, value in enumerate((98, 99, 100, 101, 102), 1):
        add_result(binding['runtime_batch_id'], (previous + timedelta(days=day, hours=8)).isoformat(sep=' '),
            value, operator='统计UI检查')
    add_result(binding['runtime_batch_id'], month.isoformat(sep=' '), 103, operator='统计UI检查')
    return binding, month


def open_statistics(app):
    app.button(key='target_statistics_open').click().run()
    assert_clean(app)
    return app


def select_statistics(app, strategy):
    app.selectbox(key=key(app, 'statistics_mode')).set_value(strategy).run()
    assert_clean(app)
    return app.session_state['target_profile_dialog']['candidate']


def confirm_fields(app):
    app.text_input(key=key(app, 'confirmed_by')).set_value('统计切换确认人')
    app.text_area(key=key(app, 'evidence')).set_value('已核对本次统计范围、全部水平及新参数')
    app.checkbox(key=key(app, 'confirmed')).check().run()
    assert_clean(app)


class TargetStatisticsUiTests(unittest.TestCase):
    def test_full_management_page_routes_batch_and_lifecycle_parameter_dialog_once(self):
        with IsolatedDatabase():
            binding, _ = statistics_fixture()
            app = AppTest.from_string('from pages.project_management_page import render_project_management_page\nrender_project_management_page()', default_timeout=30)
            app.session_state['v11_management_tabs'] = '批次管理'
            app.session_state['v11_selected_lot_config_id'] = binding['lot_config_id']
            app.session_state[f"batch_selected_item_{binding['lot_config_id']}"] = binding['lot_config_item_id']
            app.session_state['target_profile_selected_binding'] = binding['id']
            app.run()
            assert_clean(app)
            before_results = result_state()
            profiles = len(get_target_profile_context(binding['id'])['profiles'])
            app.button(key=f"batch_switch_statistics_{binding['lot_config_item_id']}").click().run()
            assert_clean(app)
            self.assertEqual(1, sum(button.key == 'target_statistics_save' for button in app.button))
            self.assertEqual(binding['id'], app.session_state['target_profile_dialog']['binding_id'])
            app.button(key='target_statistics_cancel').click().run()
            assert_clean(app)
            self.assertEqual(profiles, len(get_target_profile_context(binding['id'])['profiles']))
            self.assertEqual('', get_target_statistics_policy('lot', binding['lot_config_item_id'])['mode'])
            app.session_state['v11_management_tabs'] = '批号使用与追溯'
            app.session_state['lot_management_tabs'] = '均值和标准差管理'
            app.run()
            app.button(key='target_profile_open').click().run()
            assert_clean(app)
            self.assertEqual(1, sum(button.key == 'target_profile_save' for button in app.button))
            app.button(key='target_profile_cancel').click().run()
            assert_clean(app)
            self.assertEqual(before_results, result_state())

    def test_confirm_project_cannot_bypass_unselected_imported_policy(self):
        with IsolatedDatabase():
            values = metadata_values()
            project = save_project_details(project_values(values, '导入后确认统计方式'))
            save_template_items(project, [dict(test_item_id=values['test_item_id'], qc_method='lj',
                input_value_type='raw', unit_id=values['unit_id'], method_id=values['method_id'],
                reagent_id=values['reagent_id'], level_count=1, target_n=20)])
            confirm_fixture_project(project)
            app = AppTest.from_function(workspace_page, default_timeout=25)
            app.session_state['workspace_project_id'] = project
            before = database_state()
            app.run()
            assert_clean(app)
            self.assertTrue(app.button(key='workspace_confirm_project').disabled)
            self.assertTrue(any('请编辑检验项目并选择均值和SD统计方式' in row.value for row in app.warning))
            self.assertEqual(before, database_state())
            app.button(key='workspace_edit_item').click().run()
            assert_clean(app)
            app.selectbox(key=draft_key(app, 'target_statistics_mode')).set_value('monthly').run()
            app.button(key='project_item_save').click().run()
            assert_clean(app)
            app.button(key='project_quality_close').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='workspace_confirm_project').disabled)
            app.button(key='workspace_confirm_project').click().run()
            assert_clean(app)
            self.assertEqual('active', get_project_template(project)['status'])

    def test_legacy_unset_item_mode_is_unselected_and_requires_a_real_choice(self):
        with IsolatedDatabase():
            binding, _ = fixture()
            context = get_target_profile_context(binding['id'])
            self.assertEqual('', get_target_statistics_policy('project', binding['project_template_item_id'])['mode'])
            app = AppTest.from_function(workspace_page, default_timeout=25)
            app.session_state['workspace_project_id'] = context['template']['id']
            app.session_state[f"workspace_item_{context['template']['id']}"] = binding['project_template_item_id']
            before = database_state()
            app.run()
            self.assertFalse(any('workspace_confirm_project' == button.key for button in app.button))
            self.assertFalse(any('统计方式' in row.value for row in [*app.warning, *app.info]))
            self.assertEqual(before, database_state())
            app.button(key='workspace_edit_item').click().run()
            assert_clean(app)
            selector = app.selectbox(key=draft_key(app, 'target_statistics_mode'))
            self.assertIsNone(selector.value)
            self.assertTrue(app.button(key='project_item_save').disabled)
            before = database_state()
            selector.set_value('monthly').run()
            assert_clean(app)
            self.assertEqual(before, database_state())

    def test_active_batch_has_a_direct_switch_entry_even_when_material_edits_are_frozen(self):
        with IsolatedDatabase():
            binding, _ = statistics_fixture()
            app = AppTest.from_string('from ui.batch_workspace import render_batch_workspace\nrender_batch_workspace()', default_timeout=25)
            app.session_state['v11_selected_lot_config_id'] = binding['lot_config_id']
            app.session_state[f"batch_selected_item_{binding['lot_config_id']}"] = binding['lot_config_item_id']
            before = database_state()
            with patch('services.target_statistics_service.get_target_statistics_candidate', side_effect=AssertionError('Listing must not calculate')):
                app.run()
                assert_clean(app)
            self.assertTrue(app.button(key=f"batch_edit_levels_{binding['lot_config_item_id']}").disabled)
            button = app.button(key=f"batch_switch_statistics_{binding['lot_config_item_id']}")
            self.assertFalse(button.disabled)
            button.click().run()
            assert_clean(app)
            self.assertEqual(binding['id'], app.session_state['target_profile_dialog']['binding_id'])
            app.button(key='target_statistics_cancel').click().run()
            assert_clean(app)
            self.assertEqual(before, database_state())

    def test_single_item_and_panel_require_individual_policy_choices(self):
        with IsolatedDatabase():
            values = metadata_values()
            project = save_project_details(project_values(values, '逐项统计方式选择'))
            second = create_test_item(chinese_name='第二统计方式项目', default_unit_id=values['unit_id'])
            app = AppTest.from_function(workspace_page, default_timeout=25)
            app.session_state['workspace_project_id'] = project
            app.run()
            app.button(key='workspace_add_item').click().run()
            assert_clean(app)
            self.assertIsNone(app.selectbox(key=draft_key(app, 'target_statistics_mode')).value)
            self.assertTrue(app.button(key='project_item_save').disabled)
            app.button(key='project_dialog_cancel').click().run()
            if any(button.key == 'project_discard_changes' for button in app.button):
                app.button(key='project_discard_changes').click().run()
            app.button(key='workspace_add_panel').click().run()
            token = app.session_state['project_workspace_dialog']['token']
            app.radio(key='panel_source_' + token).set_value('选择检验项目').run()
            app.multiselect(key='panel_source_tests_选择检验项目' + token).set_value([values['test_item_id'], second]).run()
            app.selectbox(key='panel_default_' + token + '_unit_id').set_value(values['unit_id']).run()
            app.button(key='panel_source_preview').click().run()
            assert_clean(app)
            ctx = app.session_state['project_workspace_dialog']
            self.assertTrue(all(not row.get('target_statistics_mode') for row in ctx['draft']['rows']))
            self.assertTrue(app.button(key='panel_save').disabled)
            names = [row['row_key'] for row in ctx['draft']['rows']]
            app.multiselect(key='panel_bulk_rows_' + token).set_value(names).run()
            app.multiselect(key='panel_bulk_fields_' + token).set_value(['target_statistics_mode']).run()
            app.selectbox(key='panel_default_' + token + '_target_statistics_mode').set_value('monthly').run()
            app.button(key='panel_bulk_preview').click().run()
            app.button(key='panel_bulk_apply').click().run()
            assert_clean(app)
            self.assertEqual(['monthly', 'monthly'],
                [row['target_statistics_mode'] for row in app.session_state['project_workspace_dialog']['draft']['rows']])
            app.multiselect(key='panel_bulk_rows_' + token).set_value([names[1]]).run()
            app.multiselect(key='panel_bulk_fields_' + token).set_value(['target_statistics_mode']).run()
            app.selectbox(key='panel_default_' + token + '_target_statistics_mode').set_value('cumulative').run()
            app.checkbox(key='panel_bulk_overwrite_' + token).check().run()
            before = database_contents()
            app.button(key='panel_bulk_preview').click().run()
            app.button(key='panel_bulk_apply').click().run()
            assert_clean(app)
            self.assertEqual(['monthly', 'cumulative'],
                [row['target_statistics_mode'] for row in app.session_state['project_workspace_dialog']['draft']['rows']])
            self.assertEqual(before, database_contents())

    def test_quick_item_has_no_default_and_saves_explicit_instant_policy(self):
        with IsolatedDatabase():
            values = metadata_values('instant')
            values['target_statistics_mode'] = None
            app = open_details(values)
            selector = app.selectbox(key=draft_key(app, 'target_statistics_mode'))
            self.assertIsNone(selector.value)
            self.assertEqual(['月度统计', '累计统计'], selector.options)
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertTrue(any('请选择月度统计或累计统计' in row.value for row in app.caption))
            self.assertIn('上一个自然月', selector.proto.help)
            self.assertIn('同一质控品批号、同一水平', selector.proto.help)
            self.assertIn('手动确认切换后才启用', selector.proto.help)
            selector.set_value('monthly').run()
            values['target_statistics_mode'] = 'monthly'
            to_preview(app, values)
            app.button(key='quick_save').click().run()
            assert_clean(app)
            with get_connection() as c:
                project_item = c.execute('SELECT id FROM qc_project_template_items').fetchone()[0]
                lot_item = c.execute('SELECT id FROM qc_lot_config_items').fetchone()[0]
                profiles = c.execute('SELECT COUNT(*) FROM qc_target_profiles').fetchone()[0]
            self.assertEqual('monthly', get_target_statistics_policy('project', project_item)['mode'])
            self.assertEqual('monthly', get_target_statistics_policy('lot', lot_item)['mode'])
            self.assertEqual(0, profiles)

    def test_statistics_choice_does_not_clear_detection_quality_draft(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 2)
            app = open_details(values)
            to_preview(app, values)
            ctx = app.session_state['project_workspace_dialog']
            original, identity = deepcopy(ctx['draft']['quality']), ctx['quick_quality_identity']
            before = database_contents()
            app.button(key='quick_preview_details').click().run()
            app.selectbox(key=draft_key(app, 'target_statistics_mode')).set_value('monthly').run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            ctx = app.session_state['project_workspace_dialog']
            self.assertEqual(identity, ctx['quick_quality_identity'])
            self.assertEqual(original, ctx['draft']['quality'])
            self.assertTrue(app.checkbox(key=quality_prefix(app) + '_checked').value)
            self.assertEqual(before, database_contents())

    def test_workspace_does_not_calculate_or_prompt_until_explicit_open_and_cancel_keeps_policy(self):
        with IsolatedDatabase():
            binding, _ = statistics_fixture()
            before = database_state()
            with patch('services.target_statistics_service.get_target_statistics_candidate', side_effect=AssertionError('Unopened statistics must not run')):
                app = make_app(binding['id'])
                assert_clean(app)
                self.assertNotIn('本次均值', '\n'.join(str(frame.value) for frame in app.dataframe))
                open_statistics(app)
                self.assertIsNone(app.selectbox(key=key(app, 'statistics_mode')).value)
            select_statistics(app, 'monthly')
            self.assertEqual(before, database_state())
            app.button(key='target_statistics_cancel').click().run()
            assert_clean(app)
            self.assertEqual(before, database_state())
            self.assertEqual('', get_target_statistics_policy('lot', binding['lot_config_item_id'])['mode'])

    def test_candidate_comparison_switch_and_confirmation_create_one_version_only(self):
        with IsolatedDatabase():
            binding, month = statistics_fixture()
            app = open_statistics(make_app(binding['id']))
            monthly = select_statistics(app, 'monthly')
            self.assertEqual(5, monthly['levels'][0]['n'])
            table = next(frame.value for frame in app.dataframe if '本次均值' in frame.value.columns)
            self.assertEqual(100.0, table.iloc[0]['当前均值'])
            self.assertEqual(100.0, table.iloc[0]['本次均值'])
            self.assertIn('V12-LJ-LOT-001', table.iloc[0]['质控品与原厂批号'])
            self.assertNotIn('效期', table.iloc[0]['质控品与原厂批号'])
            self.assertNotIn('厂家', table.iloc[0]['质控品与原厂批号'])
            self.assertTrue(any((month - timedelta(days=1)).date().isoformat() in row.value for row in app.caption))
            candidate = select_statistics(app, 'cumulative')
            import re
            self.assertFalse(any(re.search(r'\d{2}:\d{2}:\d{2}\.\d+', row.value) for row in app.caption))
            self.assertTrue(any(expander.label == '产品名称和厂家' for expander in app.expander))
            self.assertEqual(6, candidate['levels'][0]['n'])
            self.assertFalse(app.checkbox(key=key(app, 'confirmed')).value)
            before_results = result_state()
            confirm_fields(app)
            self.assertFalse(app.button(key='target_statistics_save').disabled)
            app.button(key='target_statistics_save').click().run()
            assert_clean(app)
            self.assertFalse(app.error, [row.value for row in app.error])
            self.assertNotIn('target_profile_dialog', app.session_state)
            context = get_target_profile_context(binding['id'])
            self.assertEqual(2, len(context['profiles']))
            self.assertAlmostEqual(candidate['levels'][0]['mean'], context['current']['levels'][0]['mean'])
            self.assertAlmostEqual(candidate['levels'][0]['sd'], context['current']['levels'][0]['sd'])
            self.assertEqual('cumulative', get_target_statistics_policy('lot', binding['lot_config_item_id'])['mode'])
            self.assertEqual('', get_target_statistics_policy('project', binding['project_template_item_id'])['mode'])
            self.assertEqual(before_results, result_state())
            self.assertTrue(any('来源：累计统计' in row.value for row in app.caption))

    def test_changed_source_rejects_old_candidate_and_refresh_keeps_inputs(self):
        with IsolatedDatabase():
            binding, month = statistics_fixture()
            app = open_statistics(make_app(binding['id']))
            select_statistics(app, 'cumulative')
            confirm_fields(app)
            add_result(binding['runtime_batch_id'], (month + timedelta(hours=1)).isoformat(sep=' '), 101, operator='另一窗口录入')
            before = database_state()
            app.button(key='target_statistics_save').click().run()
            assert_clean(app)
            self.assertTrue(any('已变化' in row.value for row in app.error))
            self.assertEqual(before, database_state())
            self.assertEqual('统计切换确认人', app.text_input(key=key(app, 'confirmed_by')).value)
            app.button(key='target_statistics_refresh').click().run()
            assert_clean(app)
            self.assertFalse(app.checkbox(key=key(app, 'confirmed')).value)
            self.assertEqual('统计切换确认人', app.text_input(key=key(app, 'confirmed_by')).value)
            self.assertEqual(7, app.session_state['target_profile_dialog']['candidate']['levels'][0]['n'])
            app.button(key='target_statistics_cancel').click().run()
            self.assertEqual(before, database_state())

    def test_insufficient_data_cannot_enable_a_statistical_version(self):
        with IsolatedDatabase():
            binding, _ = fixture()
            app = open_statistics(make_app(binding['id']))
            candidate = select_statistics(app, 'cumulative')
            self.assertFalse(candidate['ready'])
            before = database_state()
            confirm_fields(app)
            self.assertTrue(app.button(key='target_statistics_save').disabled)
            self.assertEqual(before, database_state())


if __name__ == '__main__':
    unittest.main(verbosity=2)
