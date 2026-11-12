"""One regular application entry and plain operational help."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from streamlit.testing.v1 import AppTest
from ui.user_guide import load_operation_guide
from services.release_info import get_release_info
from services.runtime_edition import get_runtime_edition


class RuntimeEditionTest(unittest.TestCase):
    def test_packaged_desktop_cannot_become_web_from_its_parent_environment(self):
        self.assertEqual(get_runtime_edition(environ={"LJQC_DEPLOYMENT_MODE": "cloud"}, frozen=True), "desktop")

    def test_explicit_modes_and_plain_source_are_distinct(self):
        for mode, expected in (("cloud", "web"), ("desktop", "desktop"),
                               (" CLOUD ", "web"), ("", "source"), ("unknown", "source")):
            with self.subTest(mode=mode):
                self.assertEqual(get_runtime_edition(environ={"LJQC_DEPLOYMENT_MODE": mode}, frozen=False), expected)


def stored_business_state():
    """Compare every schema and row, except the known bootstrap touch metadata."""
    with closing(sqlite3.connect(db.get_db_path())) as connection:
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
        self.assertEqual(len(app.get('image')), 5)
        app.button(key='open_user_guide_page').click().run()
        self.assertFalse(app.session_state['user_guide_practical'])
        self.assertFalse(list(app.exception))
        self.assertEqual([item.label for item in app.expander], [title for title, _ in guide.sections])
        app.button(key='user_guide_home').click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(app.session_state['show_user_guide_page'])
        self.assertEqual(before, stored_business_state())

    @patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": "cloud"})
    def test_software_downloads_help_entry_preserves_data_and_session_drafts(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any('网页版 · 版本 ' in item.value for item in app.markdown))
        if any(button.key == 'onboarding_skip' for button in app.button):
            app.button(key='onboarding_skip').click().run()
        draft = {'draft_id': 'download-navigation-draft', 'values': {'one': '123.45'}}
        app.session_state['daily_saved_drafts'] = {'retained': draft}
        before = stored_business_state()

        app.button(key='open_software_downloads_page').click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(app.session_state['show_software_downloads_page'])
        self.assertFalse(app.session_state['show_user_guide_page'])
        self.assertEqual({'retained': draft}, app.session_state['daily_saved_drafts'])
        self.assertEqual(before, stored_business_state())

        app.button(key='software_downloads_home').click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(app.session_state['show_software_downloads_page'])
        self.assertTrue(any(button.key == 'home_create_project' for button in app.button))
        self.assertEqual({'retained': draft}, app.session_state['daily_saved_drafts'])
        self.assertEqual(before, stored_business_state())

    @patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": "desktop"})
    def test_desktop_has_no_download_entry_and_discards_stale_download_route(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(list(app.exception))
        if any(button.key == 'onboarding_skip' for button in app.button):
            app.button(key='onboarding_skip').click().run()
        self.assertFalse(any(button.key == 'open_software_downloads_page' for button in app.button))
        release = get_release_info()
        self.assertTrue(any(f'桌面版 · 版本 {release.display_version or release.version} · 构建 {release.build_number}' in item.value
                            for item in app.markdown))
        draft = {'retained': {'draft_id': 'desktop-stale-route', 'values': {'one': '321'}}}
        app.session_state['daily_saved_drafts'] = draft
        before = stored_business_state()
        app.session_state['show_software_downloads_page'] = True
        app.run()
        self.assertFalse(list(app.exception))
        self.assertFalse(app.session_state['show_software_downloads_page'])
        self.assertTrue(any(button.key == 'home_create_project' for button in app.button))
        self.assertEqual(app.session_state['daily_saved_drafts'], draft)
        self.assertEqual(before, stored_business_state())

    @patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": ""})
    def test_plain_source_is_labeled_separately_and_has_no_download_entry(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any('源码运行版 · 版本 ' in item.value for item in app.markdown))
        self.assertFalse(any(button.key == 'open_software_downloads_page' for button in app.button))


if __name__ == '__main__':
    unittest.main(verbosity=2)
