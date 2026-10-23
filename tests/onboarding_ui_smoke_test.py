"""Resumable first-use navigation through the real application and existing editors."""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.onboarding_service import get_onboarding_state
from services.settings_service import get_report_settings
from tests.app_entry_smoke_test import stored_business_state


def business_state():
    """Guide progress may change; navigation must not write business records."""
    state = stored_business_state()
    state.pop('app_settings', None)
    # The current workbench refreshes this touch timestamp on each render.
    # Keep the complete binding and source snapshot in the comparison.
    for row in state.get('qc_workbench_bindings', []):
        row['updated_at'] = None
    return state


class OnboardingUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        keys = ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')
        self.original = {key: getattr(db, key) for key in keys}
        db.STORAGE_CONFIG_PATH = self.root / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        self.new_database('guide')

    def tearDown(self):
        for key, value in self.original.items():
            setattr(db, key, value)
        self.temp.cleanup()

    def new_database(self, name):
        db.DB_PATH = db.DEFAULT_DB_PATH = self.root / (name + '.db')
        db.init_db()

    def app(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assert_clean(app)
        return app

    def assert_clean(self, app):
        self.assertFalse(list(app.exception), [item.message for item in app.exception])

    def click(self, app, key):
        app.button(key=key).click().run()
        self.assert_clean(app)
        return app

    def assert_guide(self, app, step):
        self.assert_clean(app)
        self.assertTrue(app.session_state['onboarding_open'])
        self.assertEqual(step, get_onboarding_state()['step'])
        dialogs = list(app.get('dialog'))
        self.assertEqual(1, len(dialogs))
        self.assertEqual('首次引导', dialogs[0].proto.dialog.title)
        self.assertTrue(any(button.key == 'onboarding_skip' for button in app.button))

    def assert_primary(self, app, key):
        self.assertEqual('primary', app.button(key=key).proto.type)

    def assert_no_guide(self, app):
        self.assert_clean(app)
        self.assertFalse(app.session_state.filtered_state.get('onboarding_open', False))
        self.assertFalse(any(button.key == 'onboarding_skip' for button in app.button))

    def test_empty_database_opens_native_dialog_and_saves_forward_and_back_across_sessions(self):
        app = self.app()
        self.assert_guide(app, 'unit')
        self.assertTrue(app.button(key='onboarding_back').disabled)
        before = business_state()
        self.click(app, 'onboarding_next')
        self.assert_guide(app, 'materials')
        app = self.app()
        self.assert_guide(app, 'materials')
        self.click(app, 'onboarding_back')
        self.assert_guide(app, 'unit')
        self.assert_guide(self.app(), 'unit')
        self.assertEqual(before, business_state())

    def test_guide_has_three_concrete_steps_and_finishes_at_new_project(self):
        app = self.app()
        steps = [(button.key, button.label) for button in app.button
                 if (button.key or '').startswith('onboarding_step_')]
        self.assertEqual([
            ('onboarding_step_unit', '实验室信息'),
            ('onboarding_step_materials', '仪器试剂管理'),
            ('onboarding_step_project', '新建项目'),
        ], steps)
        self.assertTrue(any(item.value == '第 1 步，共 3 步' for item in app.caption))
        self.assertEqual('填写实验室信息', app.button(key='onboarding_unit').label)
        self.assertFalse(any(button.key == 'onboarding_verification' for button in app.button))
        self.click(app, 'onboarding_next')
        self.assert_guide(app, 'materials')
        self.click(app, 'onboarding_next')
        self.assert_guide(app, 'project')
        self.assertTrue(any(item.value == '第 3 步，共 3 步' for item in app.caption))
        self.assertTrue(any(button.key == 'onboarding_finish' for button in app.button))
        self.assertFalse(any(button.key == 'onboarding_next' for button in app.button))

    def test_skip_keeps_progress_and_header_button_resumes_in_new_session(self):
        app = self.app()
        before = business_state()
        self.click(app, 'onboarding_next')
        self.click(app, 'onboarding_skip')
        self.assert_no_guide(app)
        self.assertEqual('materials', get_onboarding_state()['step'])
        self.assertEqual('skipped', get_onboarding_state()['status'])
        app = self.app()
        self.assert_no_guide(app)
        self.assertEqual('首次引导', app.button(key='open_onboarding').label)
        self.assert_primary(app, 'open_onboarding')
        self.click(app, 'open_onboarding')
        self.assert_guide(app, 'materials')
        self.assertEqual(before, business_state())

    def test_any_existing_project_including_draft_and_disabled_does_not_auto_open(self):
        cases = ('lj', 'zscore', 'instant', 'draft', 'disabled_draft')
        for kind in cases:
            with self.subTest(kind=kind):
                self.new_database(kind)
                if kind == 'lj':
                    db.create_project('已有单水平项目')
                elif kind == 'zscore':
                    db.create_zscore_project('已有多水平项目', 2)
                elif kind == 'instant':
                    db.create_instant_project('已有即时法项目')
                else:
                    with db.get_connection() as connection:
                        connection.execute(
                            'INSERT INTO qc_project_templates (uid, template_name, status, is_disabled) '
                            'VALUES (?, ?, ?, ?)', (kind, '已登记项目', 'draft', int(kind == 'disabled_draft')))
                app = self.app()
                self.assert_no_guide(app)
                before = business_state()
                self.click(app, 'open_onboarding')
                self.assert_guide(app, 'unit')
                self.click(app, 'onboarding_skip')
                self.assert_no_guide(self.app())
                self.assertEqual(before, business_state())

    def test_manual_guide_opens_settings_saves_unit_information_and_returns(self):
        app = self.app()
        self.click(app, 'onboarding_skip')
        self.click(app, 'open_onboarding')
        before = business_state()
        self.click(app, 'onboarding_unit')
        self.assert_no_guide(app)
        self.assertTrue(app.session_state['show_settings_page'])
        self.assertEqual(0, len(app.get('dialog')))
        self.assert_primary(app, 'onboarding_continue')
        for key, value in (
            ('settings_lab_name', '测试实验室'), ('settings_department_name', '检验科'),
            ('settings_qc_owner_name', '张检验'), ('settings_reviewer_name', '李复核'),
        ):
            app.text_input(key=key).set_value(value)
        self.click(app, 'save_system_settings')
        settings = get_report_settings()
        self.assertEqual(('测试实验室', '检验科', '张检验', '李复核'),
                         (settings.lab_name, settings.department_name, settings.qc_owner_name, settings.reviewer_name))
        self.click(app, 'onboarding_continue')
        self.assert_guide(app, 'unit')
        self.assertTrue(any(item.value == '已填写实验室信息。' for item in app.caption))
        self.click(app, 'onboarding_next')
        self.assert_guide(app, 'materials')
        self.assertEqual(before, business_state())

    def test_material_entries_open_existing_pages(self):
        app = self.app()
        before = business_state()
        self.click(app, 'onboarding_step_materials')
        for key, page, kind in (
            ('onboarding_instrument', 'show_reference_management_page', 'instrument'),
            ('onboarding_reagent', 'show_reference_management_page', 'reagent'),
            ('onboarding_qc', 'show_qc_materials_page', None),
        ):
            with self.subTest(entry=key):
                self.click(app, key)
                self.assert_no_guide(app)
                self.assertTrue(app.session_state[page])
                if kind:
                    self.assertEqual(kind, app.session_state['reference_management_kind'])
                self.assertEqual(0, len(app.get('dialog')))
                self.click(app, 'onboarding_continue')
                self.assert_guide(app, 'materials')
        self.assertEqual(before, business_state())

    def test_checklist_is_the_only_dialog_and_cancel_returns_to_guide(self):
        app = self.app()
        before = business_state()
        self.click(app, 'onboarding_step_materials')
        self.assertTrue(any('单位资料准备' in item.value for item in app.markdown))
        self.assertFalse(any('Excel' in item.label for item in app.expander))
        self.assertIn('单位资料准备', app.button(key='onboarding_checklist').label)
        self.click(app, 'onboarding_checklist')
        self.assert_no_guide(app)
        self.assertTrue(app.session_state['lab_preparation_dialog'])
        self.assertEqual(1, len(app.get('dialog')))
        self.assertEqual('单位资料准备', app.get('dialog')[0].proto.dialog.title)
        self.assertTrue(app.button(key='open_onboarding').disabled)
        self.click(app, 'lab_prep_cancel')
        self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)
        self.click(app, 'onboarding_continue')
        self.assert_guide(app, 'materials')
        self.assertEqual(before, business_state())

    def test_permanent_preparation_entry_opens_settings_and_cancel_returns_home(self):
        app = self.app()
        self.click(app, 'onboarding_skip')
        before = business_state()
        menu = next(item for item in app.get('popover') if item.proto.popover.label == '资料与批次')
        self.assertEqual('单位资料准备', menu.button(key='open_lab_preparation').label)
        self.click(app, 'open_lab_preparation')
        self.assertTrue(app.session_state['show_settings_page'])
        self.assertFalse(app.session_state['global_management_menu'])
        self.assertEqual(1, len(app.get('dialog')))
        self.assertEqual('单位资料准备', app.get('dialog')[0].proto.dialog.title)
        token = app.session_state['lab_preparation_dialog']['token']
        self.assertTrue(app.button(key='open_lab_preparation').disabled)
        self.assertEqual(token, app.session_state['lab_preparation_dialog']['token'])
        self.click(app, 'lab_prep_cancel')
        self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)
        self.assertTrue(app.session_state['show_settings_page'])
        self.assertEqual(0, len(app.get('dialog')))
        self.assertFalse(app.button(key='lab_prep_open').disabled)
        self.click(app, 'close_settings_page')
        self.assertFalse(app.session_state['show_settings_page'])
        self.assertTrue(any(button.key == 'home_create_project' for button in app.button))
        self.assertEqual(before, business_state())

    def test_new_project_cancel_returns_without_creating_project_or_quality_confirmation(self):
        app = self.app()
        before = business_state()
        self.click(app, 'onboarding_step_project')
        self.click(app, 'onboarding_project')
        self.assert_no_guide(app)
        self.assertTrue(app.session_state['project_workspace_dialog'])
        self.assertEqual(1, len(app.get('dialog')))
        self.assertTrue(app.button(key='open_onboarding').disabled)
        self.click(app, 'project_dialog_cancel')
        self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
        self.click(app, 'onboarding_continue')
        self.assert_guide(app, 'project')
        self.assertEqual(before, business_state())

    def test_project_save_enters_current_batch_and_can_resume_guide(self):
        from pages.main_page import LJ_ENTRY_LABEL
        from services.quick_project_setup_service import prepare_quick_project_setup
        from tests.quick_project_setup_smoke_test import metadata_values

        values = metadata_values('lj', 1)
        app = self.app()
        self.click(app, 'onboarding_step_project')
        self.click(app, 'onboarding_project')
        context = app.session_state['project_workspace_dialog']
        request_key = context['token']
        # The shortcut's own UI tests cover entering each field. Exercise its real
        # reviewed preview and save here to verify the guide's workbench handoff.
        context.update(draft=values, quick_step='preview',
                       quick_preview=prepare_quick_project_setup(values))
        app.run()
        self.assert_clean(app)
        self.click(app, 'quick_save')
        self.assert_no_guide(app)
        self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
        with db.get_connection() as connection:
            row = connection.execute('SELECT receipt_json FROM qc_quick_setup_receipts WHERE request_key=?',
                                     (request_key,)).fetchone()
            self.assertIsNotNone(row)
            receipt = json.loads(row[0])
        binding = receipt['binding']
        self.assertEqual(LJ_ENTRY_LABEL, app.session_state['top_level_method_selector'])
        self.assertEqual('当前批次', app.session_state['lj_workbench_tabs'])
        self.assertEqual(binding['runtime_project_id'], app.session_state['selected_project_id'])
        self.assertEqual(binding['runtime_batch_id'], app.session_state['selected_batch_id'])
        self.assertTrue(db.get_results(binding['runtime_batch_id']).empty)
        saved = business_state()
        self.click(app, 'onboarding_continue')
        self.assert_guide(app, 'project')
        self.assertEqual(saved, business_state())
        self.assert_no_guide(self.app())

    def test_completion_stays_closed_and_can_still_be_opened_manually(self):
        app = self.app()
        before = business_state()
        self.click(app, 'onboarding_step_project')
        self.click(app, 'onboarding_finish')
        self.assert_no_guide(app)
        self.assertEqual('completed', get_onboarding_state()['status'])
        self.assertEqual('主页', app.session_state['top_level_method_selector'])
        app = self.app()
        self.assert_no_guide(app)
        self.click(app, 'open_onboarding')
        self.assert_guide(app, 'project')
        self.assertEqual(before, business_state())

    def test_progress_save_failure_keeps_original_step_open_and_does_not_navigate(self):
        for key in ('onboarding_next', 'onboarding_skip', 'onboarding_unit'):
            with self.subTest(action=key):
                self.new_database(key)
                app = self.app()
                before = business_state()
                state = get_onboarding_state()
                with patch('ui.onboarding.save_onboarding_progress',
                           side_effect=sqlite3.OperationalError('injected progress write failure')):
                    self.click(app, key)
                self.assert_guide(app, 'unit')
                self.assertEqual(state, get_onboarding_state())
                self.assertTrue(any(item.value == '引导进度未保存，请重试。' for item in app.error))
                self.assertFalse(app.session_state.filtered_state.get('show_settings_page', False))
                self.assertEqual(before, business_state())

    def test_existing_batch_editors_disable_guide_and_suppress_auto_open(self):
        for flag in ('batch_workspace_create', 'batch_status_confirmation'):
            with self.subTest(editor=flag):
                self.new_database(flag)
                app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30)
                app.session_state[flag] = {'onboarding_test': True}
                app.run()
                self.assert_clean(app)
                self.assert_no_guide(app)
                self.assertTrue(app.button(key='open_onboarding').disabled)


if __name__ == '__main__':
    unittest.main(verbosity=2)
