"""Linked setup actions retain drafts and open the exact saved project/item.

All data is created in a temporary database. AppTest exercises the actual
destination page, including batch selection, rather than just route flags.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from streamlit.testing.v1 import AppTest
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft
from services.project_config_service import create_project_template, get_lot_config, list_lot_config_items
from tests.batch_dialogs_smoke_test import seed_draft
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


APP = '''
import pandas as pd
import streamlit as st
from ui.daily_navigation import render_daily_return
from pages.project_management_page import render_project_management_page
render_daily_return()
if st.session_state.get('show_project_management_page'):
    render_project_management_page()
elif st.session_state.get('test_view') == 'overview':
    from ui.daily_overview import render_daily_overview
    render_daily_overview()
elif st.session_state.get('test_view') == 'daily':
    from ui.daily_entry import render_daily_entry
    render_daily_entry()
elif st.session_state.get('test_view') == 'quality':
    from pages.quality_targets_page import render_quality_targets_page
    render_quality_targets_page()
elif st.session_state.get('test_view') == 'workspace':
    from ui.project_navigation import render_project_navigation
    render_project_navigation()
elif st.session_state.get('test_view') == 'lj':
    from pages.lj_config_section import render_lj_v12_configuration_selection
    render_lj_v12_configuration_selection(st.container(),pd.DataFrame(),None,pd.DataFrame(),None)
elif st.session_state.get('test_view') == 'zscore':
    from pages.zscore_config_section import render_zscore_v12_configuration_selection
    render_zscore_v12_configuration_selection(st.container(),pd.DataFrame(),None,pd.DataFrame(),None,[])
elif st.session_state.get('test_view') == 'instant':
    from pages.instant_config_section import render_instant_v12_configuration_selection
    render_instant_v12_configuration_selection(st.container(),pd.DataFrame(),None,pd.DataFrame(),None,[])
else:
    if st.button('打开设置',key='test_open_setup'):
        from ui.project_navigation import open_project_setup
        try:open_project_setup(**st.session_state['test_setup_args'])
        except ValueError as error:st.error(str(error))
'''


class RelatedNavigationTests(unittest.TestCase):
    def setUp(self):
        self.isolated=IsolatedDatabase()
        self.isolated.__enter__()

    def tearDown(self):
        self.isolated.__exit__(None,None,None)

    def seed(self):
        fixture=seed_draft()
        fixture['config']=dict(get_lot_config(fixture['config_id']))
        fixture['rows']=list_lot_config_items(fixture['config_id']).to_dict('records')
        with db.read_snapshot() as connection:
            sources={row['id']:row['source_template_item_id'] for row in connection.execute(
                'SELECT id,source_template_item_id FROM qc_lot_config_items WHERE lot_config_id=?',
                (fixture['config_id'],))}
        for row in fixture['rows']:row['source_template_item_id']=sources[row['id']]
        return fixture

    def snapshot(self):
        with db.read_snapshot() as connection:return '\n'.join(connection.iterdump())

    def healthy(self,app):
        self.assertFalse(list(app.exception),[error.message for error in app.exception])

    def screen(self,view,**state):
        app=AppTest.from_string(APP,default_timeout=30)
        app.session_state['test_view']=view
        for key,value in state.items():app.session_state[key]=value
        app.run();self.healthy(app)
        return app

    def test_overview_settings_open_selected_item_in_actual_batch_page(self):
        f=self.seed();item=f['rows'][1]
        before=self.snapshot()
        app=self.screen('overview',show_daily_overview_page=True)
        app.selectbox(key='daily_selected').set_value(
            f"{item['qc_method']}:None:{item['id']}").run()
        self.healthy(app)
        next(button for button in app.button if button.label=='查看批次设置').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state[f"batch_selected_item_{f['config_id']}"],item['id'])
        self.assertTrue(any(button.key==f"batch_edit_levels_{item['id']}" for button in app.button))
        self.assertTrue(any(title.value==item['test_item_name'] for title in app.subheader))
        self.assertEqual(before,self.snapshot())

    def test_workspace_settings_retain_exact_item_when_batch_has_several_items(self):
        f=self.seed();item=f['rows'][1];tid=f['config']['template_id']
        before=self.snapshot()
        app=self.screen('workspace',workspace_project_id=tid,
            **{f'workspace_item_{tid}':item['source_template_item_id']})
        app.button(key='workspace_batch_settings').click().run();self.healthy(app)
        self.assertEqual(app.session_state[f"batch_selected_item_{f['config_id']}"],item['id'])
        self.assertTrue(any(button.key==f"batch_edit_quality_{item['id']}" for button in app.button))
        self.assertEqual(before,self.snapshot())

    def test_empty_project_daily_action_preserves_values_and_request_on_return(self):
        f=self.seed();config=f['config']
        tid=create_project_template(template_name='尚未建立批次的项目',
            lab_instrument_id=config['lab_instrument_id'],qc_material_id=config['qc_material_id'])
        with db.read_snapshot() as connection:
            lot=connection.execute('SELECT id FROM md_qc_material_lots WHERE qc_material_id=?',
                (config['qc_material_id'],)).fetchone()[0]
        context=get_daily_context(config['lab_instrument_id'],config['qc_material_id'],lot,
            '2026-10-23 08:00:00',template_id=tid)
        draft=new_draft(context,operator='保留填写人')
        draft['values']={'previous:level':'123.45'}
        draft['notes']={'previous':'未保存备注'}
        before=self.snapshot()
        app=self.screen('daily',show_daily_entry_page=True,daily_entry_template_id=tid,daily_draft=deepcopy(draft),
            v11_pending_copied_config_id=f['config_id'])
        app.button(key='entry_empty_setup').click().run();self.healthy(app)
        self.assertEqual(app.session_state['batch_project_filter'],tid)
        self.assertNotIn('v11_pending_copied_config_id',app.session_state)
        self.assertEqual(app.session_state['daily_draft'],draft)
        self.assertFalse(any(title.value==f['config']['config_name'] for title in app.subheader))
        app.button(key='daily_return').click().run();self.healthy(app)
        self.assertEqual(app.session_state['daily_draft'],draft)
        self.assertEqual(before,self.snapshot())

    def test_quality_selection_clears_removed_item_and_routes_by_selected_project(self):
        f=self.seed();item=f['rows'][1];tid=f['config']['template_id']
        before=self.snapshot()
        app=self.screen('quality',show_quality_targets_page=True,quality_project_selector=999999)
        self.assertIsNone(app.selectbox(key='quality_project_selector').value)
        app.selectbox(key='quality_project_selector').set_value(item['source_template_item_id']).run()
        app.session_state['v11_pending_existing_config_id']=f['config_id']
        app.button(key='quality_open_lot_management').click().run();self.healthy(app)
        self.assertEqual(app.session_state['batch_project_filter'],tid)
        self.assertNotIn('v11_pending_existing_config_id',app.session_state)
        self.assertIsNone(app.session_state['v11_selected_lot_config_id'])
        self.assertEqual(before,self.snapshot())

    def test_quality_empty_state_has_project_workspace_action(self):
        app=self.screen('quality',show_quality_targets_page=True)
        app.button(key='quality_project_empty_workspace').click().run();self.healthy(app)
        self.assertFalse(app.session_state['show_quality_targets_page'])
        self.assertEqual(app.session_state['pending_top_level_method'],'主页')

    def test_three_method_empty_states_open_scoped_setup_without_writes(self):
        f=self.seed();tid=f['config']['template_id'];before=self.snapshot()
        for method in ('lj','zscore','instant'):
            with self.subTest(method=method):
                app=self.screen(method,workspace_project_id=tid)
                app.button(key=method+'_empty_setup').click().run();self.healthy(app)
                self.assertEqual(app.session_state['batch_project_filter'],tid)
                self.assertTrue(any(box.key=='batch_project_filter' for box in app.selectbox))
                self.assertEqual(before,self.snapshot())

    def test_mismatched_batch_or_item_does_not_change_navigation_or_draft(self):
        f=self.seed();config=f['config'];item=f['rows'][1]
        other=create_project_template(template_name='其他项目',lab_instrument_id=config['lab_instrument_id'],
            qc_material_id=config['qc_material_id'])
        before=self.snapshot()
        invalid=[dict(template_id=other,config_id=f['config_id']),
            dict(template_id=config['template_id'],config_id=f['config_id'],lot_config_item_id=999999),
            dict(template_id=config['template_id'],config_id=f['config_id'],lot_config_item_id=item['id'],system_id=999999)]
        for args in invalid:
            with self.subTest(args=args):
                saved={'values':{'current':'88.1'},'submission_id':'keep-request'}
                app=self.screen('setup',test_setup_args=args,daily_draft=deepcopy(saved),
                    batch_project_filter=other,v11_pending_existing_config_id=f['config_id'])
                app.button(key='test_open_setup').click().run();self.healthy(app)
                self.assertTrue(app.error)
                self.assertEqual(app.session_state['daily_draft'],saved)
                self.assertEqual(app.session_state['batch_project_filter'],other)
                self.assertEqual(app.session_state['v11_pending_existing_config_id'],f['config_id'])
                self.assertNotIn('show_project_management_page',app.session_state)
                self.assertEqual(before,self.snapshot())


if __name__=='__main__':unittest.main()
