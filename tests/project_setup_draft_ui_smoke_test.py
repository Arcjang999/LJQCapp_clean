"""Incomplete project setup survives explicit saving and a fresh UI session."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from database import get_connection
from services.project_setup_draft_service import (
    PROJECT_SETUP_DRAFT_KEY, load_project_setup_draft,
)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean, draft_key, workspace_page
from tests.quick_project_setup_smoke_test import database_contents, metadata_values
from tests.quick_project_setup_ui_smoke_test import (
    fill_quality, open_details, product_prefix, quality_prefix, to_preview,
)
from tests.project_dependencies_ui_smoke_test import dependency_prefix


def business_contents():
    """Only the explicit draft setting may differ during a temporary save."""
    contents = database_contents()
    columns, rows = contents['app_settings']
    key_column = next(index for index, row in enumerate(columns) if row[1] == 'key')
    contents['app_settings'] = columns, tuple(
        row for row in rows if row[key_column] != PROJECT_SETUP_DRAFT_KEY)
    return contents


def fresh_app():
    app = AppTest.from_function(workspace_page, default_timeout=30).run()
    assert_clean(app)
    return app


def resume_saved_draft():
    app = fresh_app()
    assert app.button(key='home_create_project').label == '继续新建项目'
    app.button(key='home_create_project').click().run()
    assert_clean(app)
    return app


def save_and_close(app):
    app.button(key='project_save_draft').click().run()
    assert_clean(app)
    assert 'project_workspace_dialog' not in app.session_state.filtered_state
    saved = load_project_setup_draft()
    assert saved is not None
    return deepcopy(saved)


def draft_action_blocked(app):
    actions = [button for button in app.button if button.key == 'project_save_draft']
    return not actions or actions[0].disabled


class ProjectSetupDraftUiTests(unittest.TestCase):
    def test_full_app_startup_shows_saved_project_without_opening_first_use_guide(self):
        with IsolatedDatabase():
            app = fresh_app()
            app.button(key='home_create_project').click().run()
            app.text_input(key=draft_key(app, 'template_name')).set_value('重启后继续的首次项目')
            saved = save_and_close(app)
            app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
            assert_clean(app)
            self.assertFalse(app.session_state.filtered_state.get('onboarding_open', False))
            self.assertFalse(any(button.key == 'onboarding_skip' for button in app.button))
            self.assertEqual('继续新建项目', app.button(key='home_create_project').label)
            self.assertFalse(app.button(key='home_create_project').disabled)
            app.button(key='home_create_project').click().run()
            assert_clean(app)
            self.assertEqual(saved['context']['creation_draft_id'],
                             app.session_state['project_workspace_dialog']['creation_draft_id'])
            self.assertEqual('重启后继续的首次项目',
                             app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertFalse(app.session_state.filtered_state.get('onboarding_open', False))
            self.assertFalse(any(button.key == 'onboarding_skip' for button in app.button))

    def test_incomplete_details_resume_in_a_fresh_session_without_creating_project(self):
        with IsolatedDatabase():
            before = business_contents()
            app = fresh_app()
            app.button(key='home_create_project').click().run()
            app.text_input(key=draft_key(app, 'template_name')).set_value('填写到一半的项目')
            app.text_area(key=draft_key(app, 'notes')).set_value('下次继续补仪器和质控品。')
            saved = save_and_close(app)
            self.assertEqual(before, business_contents())
            self.assertEqual('填写到一半的项目', saved['context']['draft']['template_name'])
            app = resume_saved_draft()
            ctx = app.session_state['project_workspace_dialog']
            self.assertEqual(saved['context']['creation_draft_id'], ctx['creation_draft_id'])
            self.assertEqual(saved['context']['token'], ctx['token'])
            self.assertEqual('填写到一半的项目', app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual('下次继续补仪器和质控品。', app.text_area(key=draft_key(app, 'notes')).value)
            self.assertIsNone(ctx['draft']['lab_instrument_id'])
            self.assertFalse(ctx['draft'].get('product_choice_review'))
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertEqual(before, business_contents())

    def test_unconfirmed_product_inputs_and_selected_lot_resume_without_confirmation(self):
        with IsolatedDatabase():
            values = metadata_values()
            before = business_contents()
            app = fresh_app()
            app.button(key='home_create_project').click().run()
            for field in ('test_item_id', 'lab_instrument_id', 'reagent_id', 'method_id',
                          'unit_id', 'target_statistics_mode'):
                app.selectbox(key=draft_key(app, field)).set_value(values[field]).run()
            token = app.session_state['project_workspace_dialog']['token']
            slot = 'quick_control_' + token + '_0'
            app.selectbox(key=slot+'_product').set_value(values['qc_material_id']).run()
            app.selectbox(key=slot+'_level').set_value(values['level_ids'][0]).run()
            prefix = product_prefix(app)
            app.text_input(key=prefix+'_person').set_value('尚待确认的核对人')
            app.text_area(key=prefix+'_evidence').set_value('已填写但尚未确认的产品依据。')
            save_and_close(app)
            app = resume_saved_draft()
            self.assertEqual(token, app.session_state['project_workspace_dialog']['token'])
            self.assertEqual('尚待确认的核对人', app.text_input(key=prefix+'_person').value)
            self.assertEqual('已填写但尚未确认的产品依据。', app.text_area(key=prefix+'_evidence').value)
            self.assertEqual(values['qc_material_id'], app.selectbox(key=slot+'_product').value)
            self.assertEqual(values['level_ids'][0], app.selectbox(key=slot+'_level').value)
            self.assertFalse(app.session_state['project_workspace_dialog']['draft'].get('product_choice_review'))
            self.assertTrue(app.button(key='quick_details_next').disabled)
            app.button(key=prefix+'_confirm').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='quick_details_next').disabled)
            self.assertEqual(before, business_contents())

    def test_unchecked_quality_keeps_latest_text_date_and_step_after_restart(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.button(key='quick_details_next').click().run()
            prefix = fill_quality(app, values)
            app.checkbox(key=prefix+'_checked').uncheck().run()
            checked_on = date(2026, 10, 20)
            app.date_input(key=prefix+'_search_date').set_value(checked_on)
            app.text_input(key=prefix+'_person').set_value('点击暂存时刚填的姓名')
            app.text_area(key=prefix+'_recorded').set_value('尚未确认的实验室要求。')
            app.text_area(key=prefix+'_evidence').set_value('点击暂存时刚填的说明。')
            before = business_contents()
            saved = save_and_close(app)
            app = resume_saved_draft()
            ctx = app.session_state['project_workspace_dialog']
            self.assertEqual('quality', ctx['quick_step'])
            self.assertEqual(saved['context']['token'], ctx['token'])
            self.assertEqual(prefix, quality_prefix(app))
            self.assertEqual('点击暂存时刚填的姓名', app.text_input(key=prefix+'_person').value)
            self.assertEqual('尚未确认的实验室要求。', app.text_area(key=prefix+'_recorded').value)
            self.assertEqual('点击暂存时刚填的说明。', app.text_area(key=prefix+'_evidence').value)
            self.assertEqual(checked_on, app.date_input(key=prefix+'_search_date').value)
            self.assertFalse(app.checkbox(key=prefix+'_checked').value)
            self.assertTrue(app.button(key='quick_quality_next').disabled)
            self.assertEqual(before, business_contents())

    def test_numeric_quality_preserves_physical_level_concentrations_on_restore(self):
        from services.master_data_service import list_units
        from services.quality_target_service import get_requirement
        from services.quick_project_setup_service import get_quick_setup_context
        from tests.quality_review_fixtures import fixture_conditions

        with IsolatedDatabase():
            values = metadata_values('zscore', 2, numeric=True)
            # The portable glucose-meter standard genuinely depends on each
            # level's concentration; the uniform CRP standard has no such input.
            identifier = 'wst403-2024-082'
            spec = get_requirement(identifier)
            values['unit_id'] = int(list_units().loc[lambda frame: frame.symbol == 'mmol/L', 'id'].iloc[0])
            with get_connection() as connection:
                connection.execute("UPDATE md_test_items SET chinese_name=?,abbreviation='' WHERE id=?",
                                   (spec['name'], values['test_item_id']))
                connection.execute("UPDATE md_methods SET method_name='便携式血糖仪法',method_code='',principle='' WHERE id=?",
                                   (values['method_id'],))
            conditions = fixture_conditions(get_quick_setup_context(values)['item'], clinical=True)
            values['quality'].update(conditions, requirement_id=identifier,
                levels=[dict(level_order=order, concentration=concentration, category='')
                        for order, concentration in enumerate((5.0, 6.0), 1)])
            app = open_details(values)
            app.button(key='quick_details_next').click().run()
            prefix = fill_quality(app, values)
            expected = [4.8, 6.2]
            for order, concentration in enumerate(expected, 1):
                self.assertTrue(any(row.key == f'{prefix}_{identifier}_{order}_concentration'
                                    for row in app.number_input))
                app.number_input(key=f'{prefix}_{identifier}_{order}_concentration').set_value(concentration)
            app.checkbox(key=prefix+'_checked').uncheck()
            before = business_contents()
            save_and_close(app)
            app = resume_saved_draft()
            for order, concentration in enumerate(expected, 1):
                self.assertEqual(concentration,
                    app.number_input(key=f'{prefix}_{identifier}_{order}_concentration').value)
            self.assertFalse(app.checkbox(key=prefix+'_checked').value)
            self.assertEqual(values['level_ids'],
                app.session_state['project_workspace_dialog']['draft']['level_ids'])
            self.assertEqual(before, business_contents())

    def test_returning_to_previously_confirmed_method_does_not_restore_old_checked_state(self):
        from services.master_data_service import create_method

        with IsolatedDatabase():
            values = metadata_values()
            method_b = create_method(method_name='草稿确认复核方法B')
            app = open_details(values)
            app.button(key='quick_details_next').click().run()
            original_prefix = fill_quality(app, values)
            self.assertTrue(app.checkbox(key=original_prefix+'_checked').value)
            before = business_contents()
            save_and_close(app)
            app = resume_saved_draft()
            self.assertTrue(app.checkbox(key=original_prefix+'_checked').value)
            app.button(key='quick_quality_back').click().run()
            app.selectbox(key=draft_key(app, 'method_id')).set_value(method_b).run()
            product = product_prefix(app)
            app.text_input(key=product+'_person').set_value('方法B核对人')
            app.text_area(key=product+'_evidence').set_value('已重新核对方法B的产品适用依据。')
            app.button(key=product+'_confirm').click().run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            changed_prefix = quality_prefix(app)
            self.assertNotEqual(original_prefix, changed_prefix)
            self.assertFalse(app.checkbox(key=changed_prefix+'_checked').value)
            self.assertTrue(app.button(key='quick_quality_next').disabled)
            save_and_close(app)
            app = resume_saved_draft()
            self.assertEqual(changed_prefix, quality_prefix(app))
            self.assertFalse(app.checkbox(key=changed_prefix+'_checked').value)
            app.button(key='quick_quality_back').click().run()
            app.selectbox(key=draft_key(app, 'method_id')).set_value(values['method_id']).run()
            app.text_input(key=product+'_person').set_value('恢复方法A核对人')
            app.text_area(key=product+'_evidence').set_value('已重新核对恢复后方法A的产品适用依据。')
            app.button(key=product+'_confirm').click().run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual(original_prefix, quality_prefix(app))
            self.assertFalse(app.checkbox(key=original_prefix+'_checked').value)
            self.assertTrue(app.button(key='quick_quality_next').disabled)
            save_and_close(app)
            app = resume_saved_draft()
            self.assertEqual(original_prefix, quality_prefix(app))
            self.assertFalse(app.checkbox(key=original_prefix+'_checked').value)
            self.assertEqual(before, business_contents())

    def test_cancel_allows_continue_temporary_save_and_explicit_discard(self):
        with IsolatedDatabase():
            app = open_details(metadata_values())
            before = business_contents()
            name = '取消前刚填的项目名'
            app.text_input(key=draft_key(app, 'template_name')).set_value(name)
            app.button(key='project_dialog_cancel').click().run()
            assert_clean(app)
            self.assertTrue(app.session_state['project_workspace_dialog']['discard'])
            self.assertTrue(any(button.key == 'project_save_draft' for button in app.button))
            app.button(key='project_continue_edit').click().run()
            assert_clean(app)
            self.assertEqual(name, app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertIsNone(load_project_setup_draft())
            app.button(key='project_dialog_cancel').click().run()
            save_and_close(app)
            self.assertEqual(before, business_contents())
            app = resume_saved_draft()
            self.assertEqual(name, app.text_input(key=draft_key(app, 'template_name')).value)
            app.button(key='project_dialog_cancel').click().run()
            app.button(key='project_discard_changes').click().run()
            assert_clean(app)
            self.assertIsNone(load_project_setup_draft())
            self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
            app = fresh_app()
            self.assertNotEqual('继续新建项目', app.button(key='home_create_project').label)
            app.button(key='home_create_project').click().run()
            self.assertEqual('', app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual(before, business_contents())

    def test_saved_preview_reopens_quality_and_checks_current_reference_state(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            to_preview(app, values)
            save_and_close(app)
            with get_connection() as connection:
                connection.execute('UPDATE lab_instruments SET is_disabled=1 WHERE id=?',
                                   (values['lab_instrument_id'],))
            before = business_contents()
            app = resume_saved_draft()
            self.assertEqual('quality', app.session_state['project_workspace_dialog']['quick_step'])
            self.assertFalse(any(button.key == 'quick_save' for button in app.button))
            self.assertTrue(any('仪器已停用' in row.value for row in app.error))
            self.assertTrue(any(button.key == 'quick_invalid_back' for button in app.button))
            self.assertEqual(before, business_contents())

    def test_resumed_three_methods_save_once_and_clear_creation_draft(self):
        for method, levels in (('lj', 1), ('zscore', 2), ('instant', 1)):
            with self.subTest(method=method), IsolatedDatabase():
                values = metadata_values(method, levels)
                app = open_details(values)
                to_preview(app, values)
                save_and_close(app)
                app = resume_saved_draft()
                self.assertEqual('quality', app.session_state['project_workspace_dialog']['quick_step'])
                app.button(key='quick_quality_next').click().run()
                assert_clean(app)
                self.assertEqual('preview', app.session_state['project_workspace_dialog']['quick_step'])
                app.button(key='quick_save').click().run()
                assert_clean(app)
                self.assertIsNone(load_project_setup_draft())
                self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
                with get_connection() as connection:
                    self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])
                    self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])
                    bindings = connection.execute('SELECT * FROM qc_workbench_bindings').fetchall()
                    self.assertEqual(1, len(bindings))
                    self.assertEqual(method, bindings[0]['qc_method'])
                    self.assertEqual('active', bindings[0]['binding_status'])

    def test_final_save_rolls_back_when_draft_cleanup_fails_and_retry_saves_once(self):
        import sqlite3
        from unittest.mock import patch

        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            to_preview(app, values)
            saved = save_and_close(app)
            app = resume_saved_draft()
            app.button(key='quick_quality_next').click().run()
            assert_clean(app)
            self.assertEqual('preview', app.session_state['project_workspace_dialog']['quick_step'])
            before = database_contents()
            with patch('ui.project_setup_draft.clear_project_setup_draft',
                       side_effect=sqlite3.OperationalError('Injected clear failure')):
                app.button(key='quick_save').click().run()
            assert_clean(app)
            self.assertEqual('preview', app.session_state['project_workspace_dialog']['quick_step'])
            self.assertTrue(app.error)
            self.assertEqual(before, database_contents())
            self.assertEqual(saved, load_project_setup_draft())
            app.button(key='quick_save').click().run()
            assert_clean(app)
            self.assertIsNone(load_project_setup_draft())
            self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
            with get_connection() as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])

    def test_switch_to_group_keeps_creation_id_and_restores_group_before_save(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            before = business_contents()
            first = save_and_close(app)
            app = resume_saved_draft()
            app.button(key='quick_use_group').click().run()
            app.button(key='setup_mode_change').click().run()
            assert_clean(app)
            self.assertEqual('project', app.session_state['project_workspace_dialog']['kind'])
            app.text_input(key=draft_key(app, 'template_name')).set_value('暂存的多检验项目')
            app.text_area(key=draft_key(app, 'notes')).set_value('多项项目中尚未确认的说明。')
            second = save_and_close(app)
            self.assertEqual(first['context']['creation_draft_id'], second['context']['creation_draft_id'])
            self.assertNotEqual(first['context']['token'], second['context']['token'])
            self.assertEqual(before, business_contents())
            app = resume_saved_draft()
            self.assertEqual('project', app.session_state['project_workspace_dialog']['kind'])
            self.assertEqual('暂存的多检验项目', app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual('多项项目中尚未确认的说明。', app.text_area(key=draft_key(app, 'notes')).value)
            prefix = product_prefix(app)
            app.text_input(key=prefix+'_person').set_value('多项目核对人')
            app.text_area(key=prefix+'_evidence').set_value('已核对本仪器及默认试剂和质控品。')
            app.button(key=prefix+'_confirm').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key='project_dialog_save').disabled)
            app.button(key='project_dialog_save').click().run()
            assert_clean(app)
            self.assertIsNone(load_project_setup_draft())
            self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)

    def test_pending_reference_and_relation_editors_block_temporary_save(self):
        for child in ('reference', 'relation', 'material'):
            with self.subTest(child=child), IsolatedDatabase():
                app = open_details(metadata_values())
                before = business_contents()
                if child == 'reference':
                    prefix = dependency_prefix(app)
                    app.selectbox(key=prefix+'_type').set_value('method').run()
                    app.button(key=prefix+'_open').click().run()
                elif child == 'relation':
                    app.button(key=product_prefix(app)+'_relation_add').click().run()
                else:
                    token = app.session_state['project_workspace_dialog']['token']
                    app.button(key='quick_control_'+token+'_0_new_lot').click().run()
                assert_clean(app)
                self.assertTrue(draft_action_blocked(app))
                self.assertIsNone(load_project_setup_draft())
                self.assertEqual(before, business_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
