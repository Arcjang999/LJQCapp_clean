"""First-method preferences and tutorial acknowledgement never rewrite business data."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.onboarding_service import (
    ONBOARDING_SETTINGS_KEY, ONBOARDING_METHOD_SETTINGS_KEY,
    WORKBENCH_TUTORIAL_SETTINGS_KEYS, first_project_method, get_onboarding_method,
    get_onboarding_state, mark_workbench_tutorial_seen, save_onboarding_method,
    save_onboarding_progress, should_auto_show_onboarding, should_show_workbench_tutorial,
)


class WorkbenchTutorialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = {key: getattr(db, key) for key in (
            'DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        db.STORAGE_CONFIG_PATH = self.root / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        self.library('first')

    def tearDown(self):
        for key, value in self.original.items():
            setattr(db, key, value)
        self.temporary.cleanup()

    def library(self, name):
        db.DB_PATH = db.DEFAULT_DB_PATH = self.root / (name + '.db')
        db.init_db()

    def business(self):
        with db.read_snapshot() as connection:
            return '\n'.join(line for line in connection.iterdump() if 'app_settings' not in line)

    def test_selection_has_no_default_and_only_changes_separate_settings(self):
        before = self.business()
        self.assertIsNone(get_onboarding_method())
        self.assertIsNone(first_project_method())
        self.assertFalse(should_show_workbench_tutorial('lj'))
        self.assertFalse(should_show_workbench_tutorial('zscore'))
        self.assertEqual('zscore', save_onboarding_method('zscore'))
        self.assertEqual('zscore', first_project_method())
        self.assertEqual({'version': 1, 'step': 'unit', 'status': 'active'}, get_onboarding_state())
        self.assertTrue(should_show_workbench_tutorial('lj'))
        self.assertTrue(should_show_workbench_tutorial('zscore'))
        self.assertEqual(before, self.business())

    def test_unknown_progress_extensions_survive_and_separate_keys_remain_independent(self):
        original = {'version': 1, 'step': 'materials', 'status': 'skipped', 'future_note': {'keep': [1, 2]}}
        db.save_app_settings({ONBOARDING_SETTINGS_KEY: json.dumps(original)})
        self.assertFalse(should_auto_show_onboarding())
        save_onboarding_method('lj')
        save_onboarding_progress('project', status='completed')
        mark_workbench_tutorial_seen('zscore')
        stored = db.get_app_settings()
        self.assertEqual(original['future_note'], json.loads(stored[ONBOARDING_SETTINGS_KEY])['future_note'])
        self.assertEqual({'version': 1, 'step': 'project', 'status': 'completed'}, get_onboarding_state())
        self.assertEqual('lj', get_onboarding_method())
        self.assertTrue(should_show_workbench_tutorial('lj'))
        self.assertFalse(should_show_workbench_tutorial('zscore'))
        self.assertFalse(should_auto_show_onboarding())

    def test_first_draft_prefill_expires_after_any_project_and_does_not_lock_method(self):
        from ui.project_dialogs import open_project_dialog
        save_onboarding_method('zscore')
        session = {}
        fake_st = SimpleNamespace(session_state=session, rerun=lambda *args, **kwargs: None)
        before = self.business()
        with patch('ui.project_dialogs.st', fake_st):
            open_project_dialog('project')
            draft = session['project_workspace_dialog']['draft']
            self.assertEqual('zscore', draft['qc_method'])
            self.assertEqual(2, draft['level_count'])
            self.assertEqual([None, None], draft['level_ids'])
            draft['qc_method'] = 'lj'
            self.assertEqual('lj', draft['qc_method'])
            self.assertEqual('zscore', get_onboarding_method())
            self.assertEqual(before, self.business())
            with db.get_connection() as connection:
                connection.execute("INSERT INTO qc_project_templates(uid,template_name,status) VALUES('first','客户首项','draft')")
            self.assertIsNone(first_project_method())
            open_project_dialog('project')
            self.assertEqual('lj', session['project_workspace_dialog']['draft']['qc_method'])

    def test_actual_workbench_method_and_database_own_the_acknowledgement(self):
        save_onboarding_method('lj')
        before = self.business()
        mark_workbench_tutorial_seen('zscore')
        self.assertTrue(should_show_workbench_tutorial('lj'))
        self.assertFalse(should_show_workbench_tutorial('zscore'))
        self.assertEqual(before, self.business())
        self.library('second')
        self.assertIsNone(get_onboarding_method())
        self.assertFalse(should_show_workbench_tutorial('lj'))
        save_onboarding_method('zscore')
        self.assertTrue(should_show_workbench_tutorial('zscore'))
        self.library('first')
        self.assertEqual('lj', get_onboarding_method())
        self.assertFalse(should_show_workbench_tutorial('zscore'))
        self.assertTrue(should_show_workbench_tutorial('lj'))

    def test_old_business_library_missing_tutorial_settings_does_not_restart_first_use(self):
        with db.get_connection() as connection:
            connection.execute("INSERT INTO projects(name) VALUES('已有单水平')")
        before = self.business()
        self.assertFalse(should_auto_show_onboarding())
        self.assertFalse(should_show_workbench_tutorial('lj'))
        self.assertFalse(should_show_workbench_tutorial('zscore'))
        with self.assertRaises(ValueError):
            save_onboarding_method('zscore')
        self.assertEqual(before, self.business())
        self.assertIsNone(get_onboarding_method())

    def test_skip_and_completed_state_and_invalid_calls_are_not_rewritten_by_tutorials(self):
        for status in ('skipped', 'completed'):
            save_onboarding_progress('materials', status=status)
            before = get_onboarding_state()
            mark_workbench_tutorial_seen('lj')
            self.assertEqual(before, get_onboarding_state())
        settings = db.get_app_settings()
        for method in ('instant', '', 'other'):
            with self.assertRaises(ValueError):
                save_onboarding_method(method)
            with self.assertRaises(ValueError):
                mark_workbench_tutorial_seen(method)
        self.assertEqual(settings, db.get_app_settings())

    def test_manual_help_revisits_both_methods_without_writing_settings(self):
        from streamlit.testing.v1 import AppTest
        save_onboarding_method('lj')
        mark_workbench_tutorial_seen('lj')
        before = db.get_app_settings()
        app = AppTest.from_string('''from ui.workbench_tutorial import render_workbench_tutorial_page
render_workbench_tutorial_page()
''', default_timeout=30).run()
        self.assertFalse(list(app.exception), [item.message for item in app.exception])
        self.assertEqual(['单水平（L-J）', '多水平法'], [tab.label for tab in app.tabs])
        self.assertTrue(any('保存检测结果' in item.value for item in app.markdown))
        self.assertTrue(any('保存本次检测' in item.value for item in app.markdown))
        self.assertEqual(before, db.get_app_settings())

    def test_help_menu_route_and_return_keep_existing_progress(self):
        from streamlit.testing.v1 import AppTest
        with db.get_connection() as connection:
            connection.execute("INSERT INTO projects(name) VALUES('已有单水平')")
        save_onboarding_progress('materials', status='skipped')
        before = db.get_app_settings()
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(list(app.exception), [item.message for item in app.exception])
        app.button(key='open_workbench_tutorial_page').click().run()
        self.assertFalse(list(app.exception), [item.message for item in app.exception])
        self.assertTrue(app.session_state['show_workbench_tutorial_page'])
        self.assertEqual(['单水平（L-J）', '多水平法'], [tab.label for tab in app.tabs])
        app.button(key='workbench_tutorial_home').click().run()
        self.assertFalse(list(app.exception), [item.message for item in app.exception])
        self.assertFalse(app.session_state['show_workbench_tutorial_page'])
        self.assertEqual(before, db.get_app_settings())


if __name__ == '__main__':
    unittest.main(verbosity=2)
