"""Search saved events by linked item vocabulary without rewriting their evidence."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services import out_of_control_service as service
from tests.out_of_control_service_smoke_test import seed_engineering_sources


class HandlingSearchFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.old = db.DB_PATH, db.LEGACY_DB_CANDIDATES
        db.DB_PATH = Path(self.tmp.name) / 'isolated.db'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        self.sources = seed_engineering_sources()
        self.event = service.open_event(*self.sources['lj'], 'search-lj', '登记员')
        self.other = service.open_event(*self.sources['z2'], 'search-z', '登记员')
        self.item_id = self.event['origin_snapshot']['config_snapshot']['test_item_id']
        # Live dictionary vocabulary is absent from the original frozen evidence.
        with db.get_connection() as connection:
            connection.execute("UPDATE md_test_items SET abbreviation='HBV',english_name='Hepatitis B virus',is_disabled=1 WHERE id=?", (self.item_id,))
        from services.master_data_service import create_alias
        create_alias(entity_type='test_item', entity_id=self.item_id, alias_text='乙肝核酸')

    def tearDown(self):
        db.DB_PATH, db.LEGACY_DB_CANDIDATES = self.old
        self.tmp.cleanup()

    def dump(self):
        with db.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def test_abbreviation_alias_and_frozen_name_search_are_read_only(self):
        self.assertNotIn('test_item_abbreviation', self.event['origin_snapshot']['config_snapshot'])
        before = self.dump()
        for query in ('HBV', 'hbv', 'ＨＢＶ', '乙肝核酸', 'Hepatitis', '免球蛋白'):
            with self.subTest(query=query):
                rows = service.list_pending(search=query)['items']
                self.assertIn(self.event['event_id'], [r['event_id'] for r in rows])
                self.assertIn(self.other['event_id'], [r['event_id'] for r in rows])
        self.assertFalse(service.list_pending(search='HCV')['items'])
        self.assertEqual(before, self.dump())

    def test_identity_only_source_can_match_linked_item(self):
        kind, identifier = self.sources['z3']
        with db.get_connection() as connection:
            row = connection.execute('SELECT id,config_snapshot_json FROM qc_result_contexts WHERE zscore_run_id=?', (identifier,)).fetchone()
            config = json.loads(row['config_snapshot_json'])
            del config['test_item_id']
            connection.execute('UPDATE qc_result_contexts SET config_snapshot_json=? WHERE id=?', (json.dumps(config), row['id']))
        event = service.open_event(kind, identifier, 'identity-only', '登记员')
        before = self.dump()
        rows = service.list_pending(search='乙肝核酸')['items']
        self.assertIn(event['event_id'], [r['event_id'] for r in rows])
        self.assertEqual(before, self.dump())

    def test_method_and_project_identity_prevent_numeric_id_collisions(self):
        lj, z = self.event['origin_snapshot'], self.other['origin_snapshot']
        self.assertEqual(lj['project_id'], z['project_id'])
        before = self.dump()
        for method in ('lj', 'zscore'):
            rows = service.list_pending(project_id=lj['project_id'], qc_method=method)['items']
            self.assertTrue(rows)
            self.assertEqual({method}, {row['qc_method'] for row in rows})
        self.assertEqual(before, self.dump())

    def test_project_picker_keeps_both_methods_and_filters_separately(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('from pages.out_of_control_page import render_out_of_control_page\nrender_out_of_control_page()', default_timeout=20)
        app.session_state['ooc_search'] = 'hbv'
        app.run()
        self.assertFalse(list(app.exception), str(list(app.exception)))
        options = app.selectbox(key='ooc_project_filter').options
        self.assertEqual(3, len(options))
        project_id = self.event['origin_snapshot']['project_id']
        before = self.dump()
        for method, label in [('lj', '单水平（LJ）'), ('zscore', '多水平法')]:
            app.selectbox(key='ooc_project_filter').set_value(f'{method}:{project_id}').run()
            self.assertFalse(list(app.exception), str(list(app.exception)))
            frames = [widget.value for widget in app.dataframe if '质控方法' in widget.value.columns]
            self.assertEqual(1, len(frames))
            self.assertTrue(all(value.startswith(label) for value in frames[0]['质控方法']))
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
