"""One-click home navigation through the real app, using isolated business data."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from streamlit.testing.v1 import AppTest
from tests.app_entry_smoke_test import stored_business_state
from tests.daily_entry_smoke_test import seed_twenty
from tests.project_workspace_smoke_test import select_table_row
from ui.common import GLOBAL_PAGE_SESSION_KEYS


APP = '''
from pathlib import Path
import streamlit as st
import ui.daily_grid as grid
# Each independent AppTest needs the actual component in its runtime registry.
grid._GRID=st.components.v2.component('daily_result_grid',html=grid.HTML,css=grid.CSS,js=grid.JS)
exec(compile(Path(APP_PATH).read_text(),APP_PATH,'exec'))
'''.replace('APP_PATH', repr(str(ROOT / 'app.py')))


class HomeNavigationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {key: getattr(db, key) for key in
                        ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        cls.base = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(cls.base.name) / 'base.db'
        db.STORAGE_CONFIG_PATH = Path(cls.base.name) / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        from services.product_directory_service import ensure_builtin_bondson_directory
        ensure_builtin_bondson_directory()
        cls.fixture = seed_twenty()
        cls.base_path = db.DB_PATH
        with db.read_snapshot() as connection:
            cls.bindings = {row['qc_method']: dict(row) for row in connection.execute(
                'SELECT * FROM qc_workbench_bindings WHERE lot_config_id=? ORDER BY id DESC',
                (cls.fixture['config_id'],))}

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.original.items():
            setattr(db, key, value)
        cls.base.cleanup()

    def setUp(self):
        self.temp = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(self.temp.name) / 'navigation.db'
        db.STORAGE_CONFIG_PATH = Path(self.temp.name) / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        shutil.copyfile(self.base_path, db.DB_PATH)

    def tearDown(self):
        self.temp.cleanup()

    def healthy(self, app):
        self.assertFalse(list(app.exception), [item.message for item in app.exception])

    def app(self):
        app = AppTest.from_string(APP, default_timeout=30).run()
        self.healthy(app)
        return app

    def assert_home(self, app):
        self.healthy(app)
        state = app.session_state.filtered_state
        self.assertEqual('主页', state['top_level_method_selector'])
        self.assertNotIn('workspace_project_id', state)
        self.assertNotIn('daily_return_page', state)
        self.assertFalse(any(state.get(key) for key in GLOBAL_PAGE_SESSION_KEYS))
        self.assertTrue(any(button.key == 'home_create_project' for button in app.button))
        self.assertTrue(any(button.key == 'home_open_project' for button in app.button))
        self.assertFalse(any(button.key == 'workspace_back' for button in app.button))
        self.assertEqual('返回主页', app.button(key='global_return_home').label)

    def return_home(self, app):
        # Capture after rendering the source page: its existing workbench refresh
        # behavior is independent of the navigation action under test.
        before = stored_business_state()
        app.button(key='global_return_home').click().run()
        self.assert_home(app)
        self.assertEqual(before, stored_business_state())

    def open_fixture_project(self, app):
        names = app.dataframe[0].value['项目名称'].tolist()
        select_table_row(app, names.index('日常20项工程验收'))
        app.button(key='home_open_project').click().run()
        self.healthy(app)
        self.assertEqual(self.fixture['template_id'], app.session_state['workspace_project_id'])

    def test_all_global_pages_return_to_initial_home_in_one_click(self):
        app = self.app()
        for page in GLOBAL_PAGE_SESSION_KEYS:
            with self.subTest(page=page):
                app.session_state['workspace_project_id'] = self.fixture['template_id']
                app.session_state['daily_entry_template_id'] = self.fixture['template_id']
                app.session_state['pending_top_level_method'] = '单水平（LJ）'
                app.session_state['daily_return_page'] = 'show_daily_overview_page'
                app.session_state[page] = True
                app.run()
                self.healthy(app)
                self.assertTrue(app.session_state[page])
                self.return_home(app)
        # These share one global flag but have separate list renderers.
        for kind in ('instrument', 'reagent'):
            with self.subTest(reference_kind=kind):
                app.session_state['reference_management_kind'] = kind
                app.session_state['show_reference_management_page'] = True
                app.run()
                self.healthy(app)
                self.return_home(app)

    def test_all_configured_method_workbenches_return_in_one_click(self):
        app = self.app()
        for method, label, prefix in (('lj', '单水平（LJ）', ''),
                                      ('zscore', '多水平法', 'zscore_'),
                                      ('instant', '即时法', 'instant_')):
            with self.subTest(method=method):
                binding = self.bindings[method]
                app.session_state['workspace_project_id'] = self.fixture['template_id']
                app.session_state['pending_top_level_method'] = label
                app.session_state[prefix + 'selected_project_id'] = binding['runtime_project_id']
                app.session_state[prefix + 'selected_batch_id'] = binding['runtime_batch_id']
                app.run()
                self.healthy(app)
                self.assertEqual(label, app.session_state['top_level_method_selector'])
                self.assertTrue(any(button.key == 'back_project_home' for button in app.button))
                self.return_home(app)
                self.assertEqual(binding['runtime_project_id'],
                                 app.session_state[prefix + 'selected_project_id'])
                self.assertEqual(binding['runtime_batch_id'],
                                 app.session_state[prefix + 'selected_batch_id'])

    def test_project_details_return_preserves_list_selection_and_filters(self):
        app = self.app()
        instrument = self.fixture['data']['lab_instrument_id']
        app.text_input(key='home_project_filter_search').set_value('日常20项').run()
        app.selectbox(key='home_project_filter_instrument').set_value(instrument).run()
        self.open_fixture_project(app)
        self.return_home(app)
        self.assertEqual(self.fixture['template_id'], app.session_state['home_selected_project_id'])
        self.assertEqual('日常20项', app.text_input(key='home_project_filter_search').value)
        self.assertEqual(instrument, app.selectbox(key='home_project_filter_instrument').value)
        self.assertFalse(app.button(key='home_open_project').disabled)
        app.button(key='home_open_project').click().run()
        self.healthy(app)
        self.assertEqual(self.fixture['template_id'], app.session_state['workspace_project_id'])

    def test_daily_draft_and_submission_id_survive_home_and_reopen(self):
        app = self.app()
        self.open_fixture_project(app)
        app.button(key='workspace_daily_entry').click().run()
        self.healthy(app)
        app.selectbox(key='entry_lot').set_value(self.fixture['main_lot']).run()
        app.button(key='entry_start').click().run()
        self.healthy(app)
        draft = app.session_state['daily_draft']
        token = draft['draft_id']
        app.text_input(key='daily_operator_' + token).set_value('返回主页前的未保存输入').run()
        app.text_area(key='entry_paste_' + token).set_value(
            '\n'.join('101.25' for _ in draft['values'])).run()
        next(button for button in app.button if button.label == '预览粘贴内容').click().run()
        next(button for button in app.button if button.label == '确认带入本组').click().run()
        self.healthy(app)
        expected = deepcopy(app.session_state['daily_draft'])
        saved_drafts = {'retained_draft': deepcopy(expected)}
        app.session_state['daily_saved_drafts'] = deepcopy(saved_drafts)
        self.return_home(app)
        self.assertEqual(saved_drafts, app.session_state['daily_saved_drafts'])
        for field in ('draft_id', 'submission_id', 'values', 'selected', 'operator', 'notes', 'reagents'):
            self.assertEqual(expected[field], app.session_state['daily_draft'][field], field)
        app.button(key='home_open_project').click().run()
        app.button(key='workspace_daily_entry').click().run()
        self.healthy(app)
        for field in ('draft_id', 'submission_id', 'values', 'selected', 'operator', 'notes', 'reagents'):
            self.assertEqual(expected[field], app.session_state['daily_draft'][field], field)
        self.assertEqual(expected['operator'], app.text_input(key='daily_operator_' + token).value)

    def test_home_callback_wins_over_pending_transfer_and_closes_top_menus(self):
        app = self.app()
        binding = self.bindings['lj']
        # AppTest runs the real registered on_click callback before app.py consumes
        # pending transfer routing, matching Streamlit's browser event ordering.
        app.session_state['workspace_project_id'] = self.fixture['template_id']
        app.session_state['pending_navigation_source'] = 'instant_transfer'
        app.session_state['pending_lj_project_id'] = binding['runtime_project_id']
        app.session_state['pending_lj_batch_id'] = binding['runtime_batch_id']
        app.session_state['pending_top_level_method'] = '即时法'
        app.session_state['onboarding_open'] = True
        app.session_state['onboarding_return_pending'] = True
        app.session_state['global_management_menu'] = True
        app.session_state['global_help_menu'] = True
        self.return_home(app)
        state = app.session_state.filtered_state
        for key in ('pending_navigation_source', 'pending_lj_project_id', 'pending_lj_batch_id'):
            self.assertNotIn(key, state)
        self.assertFalse(state['onboarding_open'])
        self.assertFalse(state['global_management_menu'])
        self.assertFalse(state['global_help_menu'])
        self.assertTrue(state['onboarding_return_pending'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
