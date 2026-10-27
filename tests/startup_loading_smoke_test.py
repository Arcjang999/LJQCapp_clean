"""The project home stays usable before loading charts or optional workbenches."""
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupLoadingTest(unittest.TestCase):
    def test_home_defers_optional_pages_and_navigation_still_opens_them(self):
        # A fresh process detects accidental imports even when other UI tests
        # have already loaded every workbench in their own interpreter.
        script = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import database as db
from streamlit.testing.v1 import AppTest

with TemporaryDirectory() as folder:
    db.DB_PATH = db.DEFAULT_DB_PATH = Path(folder) / 'startup.db'
    db.STORAGE_CONFIG_PATH = Path(folder) / 'storage.json'
    db.LEGACY_DB_CANDIDATES = []
    app = AppTest.from_file('app.py', default_timeout=30).run()
    assert not app.exception, [e.message for e in app.exception]
    optional = ('instant', 'lj', 'zscore', 'master_data', 'project_management',
                'report_history', 'settings', 'quality_targets')
    assert not {f'pages.{name}_page' for name in optional} & sys.modules.keys()
    assert 'matplotlib' not in sys.modules
    assert any(button.key == 'home_create_project' for button in app.button)

    app.button(key='open_system_settings').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert 'pages.settings_page' in sys.modules
    assert 'pages.lj_page' not in sys.modules
    app.button(key='close_settings_page').click().run()
    assert not app.exception
    assert any(button.key == 'home_create_project' for button in app.button)

    for flag, module in [('show_quality_targets_page', 'quality_targets'),
                         ('show_report_history_page', 'report_history'),
                         ('show_master_data_page', 'master_data'),
                         ('show_project_management_page', 'project_management')]:
        app.session_state[flag] = True
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        assert f'pages.{module}_page' in sys.modules
        app.session_state[flag] = False
        app.run()
        assert not app.exception
        assert any(button.key == 'home_create_project' for button in app.button)

    for label, module in [('单水平（L-J）', 'lj'), ('多水平法', 'zscore'), ('即时法', 'instant')]:
        app.session_state['top_level_method_selector'] = label
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        assert f'pages.{module}_page' in sys.modules
        app.button(key='back_project_home').click().run()
        assert not app.exception
        assert any(button.key == 'home_create_project' for button in app.button)
'''
        result = subprocess.run(
            [sys.executable, '-c', script], cwd=ROOT,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
