"""One regular application entry and plain operational help."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from streamlit.testing.v1 import AppTest


class AppEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.original = {key: getattr(db, key) for key in
                         ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        db.DB_PATH = db.DEFAULT_DB_PATH = self.root / 'test.db'
        db.STORAGE_CONFIG_PATH = self.root / 'settings.json'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()

    def tearDown(self):
        for key, value in self.original.items():
            setattr(db, key, value)
        self.temp.cleanup()

    def test_old_dataset_flags_do_not_change_normal_entry(self):
        db.save_app_settings({'demo_dataset': 'legacy', 'demo_sample_date': '2000-01-01'})
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(list(app.exception))
        self.assertFalse(any('演示' in x.value or '模拟' in x.value for x in app.caption))
        self.assertNotIn('demo_session_initialized', app.session_state.filtered_state)
        self.assertTrue(any(b.key == 'open_operation_guide_page' for b in app.button))
        self.assertFalse(any(b.key == 'open_demo_guide_page' for b in app.button))

    def test_help_routes_to_current_guide_and_back(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        app.button(key='open_operation_guide_page').click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(app.session_state['user_guide_practical'])
        self.assertTrue(any('## 开始前' in x.value for x in app.markdown))
        app.button(key='open_user_guide_page').click().run()
        self.assertFalse(app.session_state['user_guide_practical'])
        self.assertTrue(any('从项目开始' == x.value for x in app.subheader))


if __name__ == '__main__':
    unittest.main(verbosity=2)
