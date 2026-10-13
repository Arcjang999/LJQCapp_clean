"""One regular application entry and plain operational help."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from streamlit.testing.v1 import AppTest
from ui.user_guide import load_operation_guide


def stored_business_state():
    """Compare every schema and row, except the known bootstrap touch metadata."""
    with sqlite3.connect(db.get_db_path()) as connection:
        connection.row_factory = sqlite3.Row
        builtin_sources = {row['source_code']: row['id'] for row in connection.execute(
            "SELECT id,source_code FROM md_sources WHERE source_code IN "
            "('LJQC_BUILTIN','WST886_2026','NMPA_UDI')")}
        builtin_items = {row['entity_id'] for row in connection.execute(
            "SELECT entity_id FROM md_source_records WHERE source_id=? AND origin_type='official' "
            "AND entity_type='test_item' AND external_record_id LIKE 'WST886_2026:%'",
            (builtin_sources.get('WST886_2026'),))}
        schema = [tuple(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        state = {'schema': schema}
        for table in [row[1] for row in schema if row[0] == 'table']:
            quoted_table = '"' + table.replace('"', '""') + '"'
            rows = []
            for record in connection.execute(f'SELECT * FROM {quoted_table} ORDER BY rowid'):
                row = dict(record)
                if table == 'md_sources' and row['id'] in builtin_sources.values():
                    row['updated_at'] = None
                elif table == 'md_test_items' and row['id'] in builtin_items and row['origin_type'] == 'official':
                    row['updated_at'] = None
                elif (table == 'md_source_records' and row['source_id'] == builtin_sources.get('WST886_2026')
                      and row['origin_type'] == 'official' and row['entity_type'] == 'test_item'
                      and row['external_record_id'].startswith('WST886_2026:')):
                    row['last_seen_at'] = row['updated_at'] = None
                rows.append(row)
            state[table] = rows
        return state


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
        self.assertFalse(list(app.exception))
        guide = load_operation_guide()
        before = stored_business_state()
        # The empty project list has a direct first-use entry into the same guide.
        starter = next(button for button in app.button if button.label == '查看首次使用步骤')
        starter.click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(app.expander[0].label, guide.sections[0][0])
        self.assertTrue(app.expander[0].proto.expanded)
        app.button(key='user_guide_home').click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(app.session_state['show_user_guide_page'])
        self.assertTrue(any(button.label == '查看首次使用步骤' for button in app.button))

        app.button(key='open_operation_guide_page').click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(app.session_state['user_guide_practical'])
        rendered = '\n\n'.join(item.value for item in app.markdown)
        self.assertIn(guide.introduction, rendered)
        heading_positions = [rendered.index('## ' + title) for title, _ in guide.sections]
        self.assertEqual(heading_positions, sorted(heading_positions))
        self.assertEqual(len(app.get('image')), 3)
        app.button(key='open_user_guide_page').click().run()
        self.assertFalse(app.session_state['user_guide_practical'])
        self.assertFalse(list(app.exception))
        self.assertEqual([item.label for item in app.expander], [title for title, _ in guide.sections])
        app.button(key='user_guide_home').click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(app.session_state['show_user_guide_page'])
        self.assertEqual(before, stored_business_state())


if __name__ == '__main__':
    unittest.main(verbosity=2)
