"""Exercise row-selection callbacks on small isolated handling fixtures."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services import out_of_control_service as service
from streamlit.testing.v1 import AppTest
from tests.out_of_control_service_smoke_test import seed_engineering_sources

APP = 'from pages.out_of_control_page import render_out_of_control_page\nrender_out_of_control_page()'


class HandlingRowSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.original = {key: getattr(db, key) for key in
                         ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(self.temporary.name) / 'isolated.db'
        db.STORAGE_CONFIG_PATH = Path(self.temporary.name) / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        self.sources = seed_engineering_sources()
        with db.get_connection() as connection:
            connection.execute('UPDATE zscore_runs SET test_time=? WHERE id=?',
                               ('2026-09-02 09:10:00', self.sources['z3'][1]))
        # Open fixture events up front: subsequent selection and viewing must
        # not create events, revisions or alter original detection evidence.
        for row in service.list_pending()['items']:
            service.open_event(row['source_type'], row['source_id'], 'fixture-' + row['candidate_key'], '登记员')
        self.before = self.dump()

    def tearDown(self):
        self.assertEqual(self.before, self.dump())
        for key, value in self.original.items():
            setattr(db, key, value)
        self.temporary.cleanup()

    def dump(self):
        with db.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def healthy(self, app):
        self.assertFalse(list(app.exception), [str(row) for row in app.exception])
        self.assertFalse(list(app.error), [str(row.value) for row in app.error])

    def open(self, **state):
        app = AppTest.from_string(APP, default_timeout=20)
        for key, value in state.items():
            app.session_state[key] = value
        app.run()
        self.healthy(app)
        return app

    def table(self, app, prefix='main'):
        tables = [table for table in app.dataframe if f'ooc_list_table_{prefix}_' in table.proto.id]
        self.assertEqual(1, len(tables))
        return tables[0]

    def select(self, app, row, prefix='main'):
        # Same selection payload as a browser; AppTest has no public dataframe
        # selection method. Row indexes refer to the supplied dataframe order.
        table = self.table(app, prefix)
        state = app._tree.get_widget_states()
        selected = state.widgets.add()
        selected.id = table.proto.id
        selected.string_value = json.dumps({'selection': {'rows': [] if row is None else [row], 'columns': [], 'cells': []}})
        app._run(state)
        self.healthy(app)

    @staticmethod
    def identity(row):
        return row['source_type'], int(row['source_id'])

    def assert_detail(self, app, identity):
        selection = app.session_state['ooc_selection']
        self.assertEqual(identity, (selection['source_type'], selection['source_id']))
        self.assertTrue(selection['from_list'])
        self.assertTrue(any(button.key == 'ooc_detail_back' for button in app.button))

    def test_click_selects_only_and_process_opens_exact_source(self):
        rows = service.list_pending()['items']
        app = self.open()
        self.assertTrue(app.button(key='ooc_continue_main').disabled)
        self.assertEqual('处理', app.button(key='ooc_continue_main').label)
        self.assertFalse(any(widget.label == '选择处理事项' for widget in app.selectbox))
        self.select(app, 1)
        selected = self.identity(rows[1])
        self.assertEqual(selected, app.session_state['ooc_list_selected_main'])
        self.assertNotIn('ooc_selection', app.session_state)
        self.assertFalse(app.button(key='ooc_continue_main').disabled)
        self.assertEqual(self.before, self.dump())
        app.button(key='ooc_continue_main').click().run()
        self.healthy(app)
        self.assert_detail(app, selected)

    def test_cleared_or_invalid_selection_cannot_open_previous_record(self):
        app = self.open()
        self.select(app, 0)
        self.select(app, None)
        self.assertIsNone(app.session_state['ooc_list_selected_main'])
        self.assertTrue(app.button(key='ooc_continue_main').disabled)
        self.select(app, 999)
        self.assertIsNone(app.session_state['ooc_list_selected_main'])
        self.assertTrue(app.button(key='ooc_continue_main').disabled)
        self.assertNotIn('ooc_selection', app.session_state)

    def test_method_filter_removes_selection_instead_of_reusing_row_index(self):
        rows = service.list_pending()['items']
        selected_index = next(i for i, row in enumerate(rows) if row['qc_method'] == 'lj')
        other = next(row for row in rows if row['qc_method'] == 'zscore')
        app = self.open()
        self.select(app, selected_index)
        old_table = self.table(app).proto.id
        app.selectbox(key='ooc_project_filter').set_value(other['project_key']).run()
        self.healthy(app)
        self.assertNotEqual(old_table, self.table(app).proto.id)
        self.assertIsNone(app.session_state['ooc_list_selected_main'])
        self.assertTrue(app.button(key='ooc_continue_main').disabled)
        self.assertNotIn('ooc_selection', app.session_state)
        self.select(app, 0)
        app.button(key='ooc_continue_main').click().run()
        self.healthy(app)
        expected = service.list_pending(project_id=other['project_id'], qc_method='zscore')['items'][0]
        self.assert_detail(app, self.identity(expected))

    def test_reordered_rows_preserve_source_identity_and_rebase_default_row(self):
        actual_list = service.list_pending
        reverse = False
        def ordered(**kwargs):
            result = deepcopy(actual_list(**kwargs))
            if reverse:
                result['items'].reverse()
            return result
        with patch('pages.out_of_control_page.list_pending', side_effect=ordered):
            rows = actual_list()['items']
            app = self.open()
            self.select(app, 0)
            selected = self.identity(rows[0])
            old_table = self.table(app).proto.id
            reverse = True
            app.run()
            self.healthy(app)
            self.assertEqual(selected, app.session_state['ooc_list_selected_main'])
            self.assertNotEqual(old_table, self.table(app).proto.id)
            defaults = json.loads(self.table(app).proto.selection_default)['selection']['rows']
            self.assertEqual([len(rows) - 1], defaults)
            app.button(key='ooc_continue_main').click().run()
            self.healthy(app)
            self.assert_detail(app, selected)

    def test_return_preserves_filters_and_selected_record(self):
        row = next(row for row in service.list_pending()['items'] if row['qc_method'] == 'lj')
        filters = dict(ooc_search='免球蛋白', ooc_statuses=['pending'],
                       ooc_project_filter=row['project_key'], ooc_instrument_filter=row['instrument_id'],
                       ooc_all_dates=False, ooc_date_basis='test_time',
                       ooc_start_date=date(2026, 9, 1), ooc_end_date=date(2026, 9, 1))
        app = self.open(**filters)
        self.select(app, 0)
        selected = app.session_state['ooc_list_selected_main']
        app.button(key='ooc_continue_main').click().run()
        self.healthy(app)
        app.button(key='ooc_detail_back').click().run()
        self.healthy(app)
        for key, value in filters.items():
            self.assertEqual(value, app.session_state[key], key)
        self.assertEqual(selected, app.session_state['ooc_list_selected_main'])
        self.assertEqual([0], json.loads(self.table(app).proto.selection_default)['selection']['rows'])
        self.assertFalse(app.button(key='ooc_continue_main').disabled)
        self.assertNotIn('ooc_selection', app.session_state)

    def test_main_and_cross_day_selections_remain_independent(self):
        kwargs = dict(start_date='2026-09-02', end_date='2026-09-02')
        groups = service.list_pending(**kwargs)
        app = self.open(ooc_all_dates=False, ooc_start_date=date(2026, 9, 2), ooc_end_date=date(2026, 9, 2))
        self.assertTrue(app.button(key='ooc_continue_main').disabled)
        self.assertTrue(app.button(key='ooc_continue_cross_day').disabled)
        self.select(app, 0, 'main')
        main = self.identity(groups['items'][0])
        self.assertTrue(app.button(key='ooc_continue_cross_day').disabled)
        self.select(app, 0, 'cross_day')
        previous = self.identity(groups['cross_day'][0])
        self.assertNotEqual(main, previous)
        self.assertEqual(main, app.session_state['ooc_list_selected_main'])
        self.assertEqual(previous, app.session_state['ooc_list_selected_cross_day'])
        self.assertNotIn('ooc_selection', app.session_state)
        app.button(key='ooc_continue_main').click().run()
        self.healthy(app)
        self.assert_detail(app, main)
        app.button(key='ooc_detail_back').click().run()
        self.healthy(app)
        self.assertEqual(main, app.session_state['ooc_list_selected_main'])
        self.assertEqual(previous, app.session_state['ooc_list_selected_cross_day'])
        app.button(key='ooc_continue_cross_day').click().run()
        self.healthy(app)
        self.assert_detail(app, previous)


if __name__ == '__main__':
    unittest.main(verbosity=2)
