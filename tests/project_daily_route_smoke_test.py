"""Project-scoped daily UI navigation, on new isolated data only.

AppTest checks routing/callback contracts; browser layout and keyboard are separate.
"""
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
from services.daily_context_service import get_daily_context, list_daily_choices
from tests.daily_entry_smoke_test import seed_twenty
from tests.project_workspace_smoke_test import select_table_row


APP = '''
from pathlib import Path
import streamlit as st
import ui.daily_grid as grid
# AppTest owns a runtime registry per instance, while imports are cached.
grid._GRID=st.components.v2.component('daily_result_grid',html=grid.HTML,css=grid.CSS,js=grid.JS)
exec(compile(Path(APP_PATH).read_text(),APP_PATH,'exec'))
'''.replace('APP_PATH', repr(str(ROOT / 'app.py')))


def seed_second_project(first):
    from services.project_config_service import (
        create_project_template, list_template_items, save_template_items,
        activate_project_template, activate_lot_config,
    )
    from services.material_workflow_service import create_material_config
    from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_lot
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    data = first['data']
    tid = create_project_template(template_name='另一个同仪器同材料项目',
        lab_instrument_id=data['lab_instrument_id'], qc_material_id=data['qc_material_id'],
        default_reagent_id=data['reagent_id'])
    source = list_template_items(first['template_id']).to_dict('records')[:3]
    for row in source:
        row.pop('id', None)
        row.pop('uid', None)
    save_template_items(tid, source)
    confirm_fixture_project(tid)
    activate_project_template(tid)
    rows = list_template_items(tid).to_dict('records')
    config = create_material_config(template_id=tid,
        selections={r['id']: first['levels'][:r['level_count']] for r in rows})
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings()
    sync_zscore_workbench_bindings()
    sync_instant_workbench_bindings()
    return {'template_id': tid, 'config_id': config}


class ProjectDailyRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {k: getattr(db, k) for k in
            ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        cls.base = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(cls.base.name) / 'base.db'
        db.STORAGE_CONFIG_PATH = Path(cls.base.name) / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        from services.product_directory_service import ensure_builtin_bondson_directory
        ensure_builtin_bondson_directory()
        cls.first = seed_twenty()
        cls.second = seed_second_project(cls.first)
        cls.base_path = db.DB_PATH

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.original.items():
            setattr(db, key, value)
        cls.base.cleanup()

    def setUp(self):
        self.temp = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(self.temp.name) / 'isolated.db'
        db.STORAGE_CONFIG_PATH = Path(self.temp.name) / 'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        shutil.copyfile(self.base_path, db.DB_PATH)

    def tearDown(self):
        self.temp.cleanup()

    def dump(self):
        # app.py intentionally reruns idempotent reference-data synchronization.
        # Exclude only its observed bookkeeping timestamps/sequence counters;
        # compare every field of result, project, binding, receipt and other rows.
        bookkeeping = {'md_sources': {'updated_at'},
            'md_source_records': {'last_seen_at', 'updated_at'},
            'md_test_items': {'updated_at'}}
        snapshot = {}
        with db.read_snapshot() as connection:
            for entry in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                table = entry[0]
                rows = []
                for record in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"'):
                    row = dict(record)
                    if table == 'sqlite_sequence' and row['name'] in ('md_sources', 'md_source_records'):
                        continue
                    rows.append({k: v for k, v in row.items() if k not in bookkeeping.get(table, set())})
                snapshot[table] = rows
        return snapshot

    def healthy(self, app):
        self.assertFalse(list(app.exception), [x.message for x in app.exception])

    def app(self):
        app = AppTest.from_string(APP, default_timeout=30).run()
        self.healthy(app)
        return app

    def select_project(self, app, title):
        names = app.dataframe[0].value['项目名称'].tolist()
        select_table_row(app, names.index(title))
        self.healthy(app)
        # A specific item/batch remains selected independently of whole-group entry.
        select_table_row(app, 1)
        self.healthy(app)
        app.button(key='workspace_daily_entry').click().run()
        self.healthy(app)

    def start_group(self, app):
        if 'daily_draft' not in app.session_state:
            app.selectbox(key='entry_lot').set_value(self.first['main_lot']).run()
            self.healthy(app)
            app.button(key='entry_start').click().run()
            self.healthy(app)
        return app.session_state['daily_draft']

    def return_project_list(self, app):
        app.button(key='entry_project_home').click().run()
        self.healthy(app)
        app.button(key='workspace_back').click().run()
        self.healthy(app)

    def test_home_help_and_internal_routes_without_method_picker(self):
        app = self.app()
        before = self.dump()
        app.session_state['home_project_filter_way'] = 'zscore'
        app.session_state['home_project_filter_method'] = 'V11 方法'
        app.session_state['project_navigation_values'] = {
            'home_project_filter_way': 'zscore', 'home_project_filter_method': 'V11 方法'}
        app.run()
        self.healthy(app)
        self.assertFalse(any(r.key == 'top_level_method_selector' for r in app.radio))
        self.assertFalse(any(box.key in {'home_project_filter_way', 'home_project_filter_method'} for box in app.selectbox))
        self.assertTrue(any(box.key == 'home_project_filter_instrument' for box in app.selectbox))
        self.assertEqual(set(app.dataframe[0].value['项目名称']), {'日常20项工程验收', '另一个同仪器同材料项目'})
        self.assertFalse(any(b.key in {'home_daily_entry', 'open_main_lj_card',
            'open_main_zscore_card', 'open_main_instant_card'} for b in app.button))
        names = app.dataframe[0].value['项目名称'].tolist()
        select_table_row(app, names.index('日常20项工程验收'))
        self.healthy(app)
        self.assertEqual(len(app.dataframe[0].value), 20)
        self.assertEqual(set(app.dataframe[0].value['质控方法']), {'单水平（LJ）', '多水平法', '即时法'})
        app.button(key='workspace_back').click().run()
        self.healthy(app)
        self.assertEqual(before, self.dump())
        app.button(key='open_user_guide_page').click().run()
        self.healthy(app)
        self.assertTrue(app.session_state['show_user_guide_page'])
        app.button(key='user_guide_home').click().run()
        self.healthy(app)
        self.assertFalse(app.session_state['show_user_guide_page'])
        self.assertTrue(any(b.key == 'home_create_project' for b in app.button))
        self.assertEqual(before, self.dump())
        # Method workbenches retain their existing explicit binding preparation;
        # the no-business-write contract above covers home/help navigation.
        for method in ('单水平（LJ）', '多水平法', '即时法'):
            app.session_state['pending_top_level_method'] = method
            app.run()
            self.healthy(app)
            self.assertEqual(app.session_state['top_level_method_selector'], method)
            self.assertFalse(any(r.key == 'top_level_method_selector' for r in app.radio))

    def test_same_instrument_material_project_scope_and_stale_config_rejected(self):
        before = self.dump()
        f = self.first
        choice = dict(lab_instrument_id=f['data']['lab_instrument_id'],
            qc_material_id=f['data']['qc_material_id'], qc_material_lot_id=f['main_lot'],
            test_time='2026-09-28 08:00:00')
        unscoped = get_daily_context(**choice)
        self.assertTrue(unscoped['requires_combination'])
        first = get_daily_context(**choice, template_id=f['template_id'])
        second = get_daily_context(**choice, template_id=self.second['template_id'])
        self.assertEqual({r['template_id'] for r in first['items']}, {f['template_id']})
        self.assertEqual({r['template_id'] for r in second['items']}, {self.second['template_id']})
        self.assertEqual(len(first['items']), 20)
        self.assertEqual(len(second['items']), 3)
        self.assertFalse(get_daily_context(**choice, template_id=f['template_id'],
            lot_config_id=self.second['config_id'])['items'])
        self.assertTrue(list_daily_choices(template_id=f['template_id'])['lots'])
        self.assertFalse(list_daily_choices(template_id=999999)['lots'])
        app = self.app()
        self.select_project(app, '日常20项工程验收')
        self.assertEqual(app.session_state['daily_entry_template_id'], f['template_id'])
        draft = self.start_group(app)
        self.assertEqual(draft['context']['selection']['template_id'], f['template_id'])
        self.assertEqual({r['template_id'] for r in draft['context']['items']}, {f['template_id']})
        self.assertEqual(len(draft['context']['items']), 20)
        self.assertEqual(before, self.dump())

    def test_project_a_to_b_back_and_settings_preserve_unsaved_draft(self):
        app = self.app()
        before = self.dump()
        self.select_project(app, '日常20项工程验收')
        draft = self.start_group(app)
        token = draft['draft_id']
        selected_item = app.session_state['workspace_item_' + str(self.first['template_id'])]
        app.text_input(key='daily_operator_' + token).set_value('甲项目未保存输入').run()
        app.text_area(key='entry_paste_' + token).set_value('\n'.join('101.25' for _ in draft['values'])).run()
        next(b for b in app.button if b.label == '预览粘贴内容').click().run()
        next(b for b in app.button if b.label == '确认带入本组').click().run()
        self.healthy(app)
        expected = deepcopy(app.session_state['daily_draft'])
        self.return_project_list(app)
        self.select_project(app, '另一个同仪器同材料项目')
        self.assertEqual(app.session_state['daily_entry_template_id'], self.second['template_id'])
        self.assertNotIn('daily_draft', app.session_state)
        second = self.start_group(app)
        self.assertEqual(len(second['context']['items']), 3)
        self.assertEqual({i['template_id'] for i in second['context']['items']}, {self.second['template_id']})
        self.assertTrue(all(v == '' for v in second['values'].values()))
        self.return_project_list(app)
        self.select_project(app, '日常20项工程验收')
        restored = self.start_group(app)
        for field in ('draft_id', 'submission_id', 'values', 'selected', 'operator', 'notes', 'reagents'):
            self.assertEqual(restored[field], expected[field], field)
        app.button(key='open_user_guide_page').click().run()
        self.healthy(app)
        self.assertTrue(app.session_state['show_user_guide_page'])
        app.button(key='daily_return').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['daily_draft']['values'], expected['values'])
        app.button(key='open_system_settings').click().run()
        self.healthy(app)
        app.button(key='daily_return').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['daily_entry_template_id'], self.first['template_id'])
        self.assertEqual(app.session_state['workspace_project_id'], self.first['template_id'])
        self.assertEqual(app.session_state['daily_draft']['values'], expected['values'])
        self.assertEqual(app.text_input(key='daily_operator_' + token).value, expected['operator'])
        app.button(key='entry_project_home').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['workspace_item_' + str(self.first['template_id'])], selected_item)
        self.assertEqual(app.session_state['workspace_project_id'], self.first['template_id'])
        self.assertTrue(any(s.key == 'workspace_batch_' + str(selected_item) for s in app.selectbox))
        self.assertEqual(before, self.dump())


    def test_last_used_context_reopens_instead_of_old_dict_insertion_order(self):
        from services.daily_draft_service import new_draft
        f = self.first
        with db.read_snapshot() as connection:
            other_lot = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                (f['levels'][1],)).fetchone()[0]
        common = dict(lab_instrument_id=f['data']['lab_instrument_id'],
            qc_material_id=f['data']['qc_material_id'], test_time='2026-09-28 08:00:00',
            template_id=f['template_id'], lot_config_id=f['config_id'])
        current = new_draft(get_daily_context(**common, qc_material_lot_id=f['main_lot']))
        older = new_draft(get_daily_context(**common, qc_material_lot_id=other_lot))
        current['operator'] = '实际最后离开的草稿'
        older['operator'] = '更早另一个材料范围草稿'
        current_key = str(current['context']['selection'])
        script = """
import streamlit as st
from ui.daily_navigation import prepare_project_daily_entry
if st.button('换项目'):
    prepare_project_daily_entry(st.session_state['test_target'])
"""
        app = AppTest.from_string(script)
        app.session_state['daily_saved_drafts'] = {
            current_key: deepcopy(current),
            str(older['context']['selection']): deepcopy(older),
        }
        app.session_state['daily_draft'] = deepcopy(current)
        app.session_state['test_target'] = self.second['template_id']
        before = self.dump()
        app.run()
        app.button[0].click().run()
        self.healthy(app)
        self.assertNotIn('daily_draft', app.session_state)
        app.session_state['test_target'] = f['template_id']
        app.button[0].click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['daily_draft']['draft_id'], current['draft_id'])
        self.assertEqual(app.session_state['entry_lot'], f['main_lot'])
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
