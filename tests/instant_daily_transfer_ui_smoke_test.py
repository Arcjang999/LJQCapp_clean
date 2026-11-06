"""Transferred runtime routes refresh only unsaved daily drafts, on isolated data."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from streamlit.testing.v1 import AppTest
import database as db
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft,build_request
from services.daily_entry_service import validate_submission,submit_daily
from services.instant_service import confirm_instant_transfer_to_lj,save_instant_result
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.instant_v12_integration_smoke_test import entry
from tests.project_daily_route_smoke_test import APP


class InstantDailyTransferUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original={key:getattr(db,key) for key in
            ('DB_PATH','DEFAULT_DB_PATH','STORAGE_CONFIG_PATH','LEGACY_DB_CANDIDATES')}
        cls.base=TemporaryDirectory()
        db.DB_PATH=db.DEFAULT_DB_PATH=Path(cls.base.name)/'base.db'
        db.STORAGE_CONFIG_PATH=Path(cls.base.name)/'storage.json';db.LEGACY_DB_CANDIDATES=[]
        db.init_db()
        cls.fixture=seed_instant_configuration(name='即时转L-J整组UI验收')
        for index in range(20):entry(cls.fixture,index)
        from services.lot_lifecycle_service import (
            workbench_systems,create_reagent_lot,record_lot_verification,switch_reagent_lots)
        system=workbench_systems()[0]
        cls.reagent=create_reagent_lot(reagent_id=cls.fixture['reagent_id'],
            lot_no='TRANSFER-UI-REAGENT',expiry_date='2028-12-31')
        verification=record_lot_verification(template_item_id=system['template_item_id'],
            system_id=system['id'],reagent_lot_id=cls.reagent,conclusion='pass',
            evidence='独立合成样例',confirmed_by='UI验收',confirmed_at='2026-09-01')
        switch_reagent_lots(selections=[dict(template_item_id=system['template_item_id'],
            system_id=system['id'],reagent_lot_id=cls.reagent,verification_id=verification,expected_revision=0)],
            effective_at='2026-09-01',operator='UI验收',reason='独立合成样例')
        cls.base_path=db.DB_PATH

    @classmethod
    def tearDownClass(cls):
        for key,value in cls.original.items():setattr(db,key,value)
        cls.base.cleanup()

    def setUp(self):
        self.temp=TemporaryDirectory()
        db.DB_PATH=db.DEFAULT_DB_PATH=Path(self.temp.name)/'isolated.db'
        db.STORAGE_CONFIG_PATH=Path(self.temp.name)/'storage.json';db.LEGACY_DB_CANDIDATES=[]
        shutil.copyfile(self.base_path,db.DB_PATH)

    def tearDown(self):self.temp.cleanup()

    def context(self):
        f=self.fixture
        return get_daily_context(f['instrument_id'],f['material_id'],f['lot_id'],
            '2026-09-12 08:00:00',template_id=f['template_id'],lot_config_id=f['config_id'])

    def draft(self):
        draft=new_draft(self.context(),operator='未保存检测人')
        draft['values']={key:'102.88' for key in draft['values']}
        draft['notes']={key:'未保存备注' for key in draft['notes']}
        validation=validate_submission(build_request(draft))
        self.assertTrue(validation['valid'],validation['errors'])
        draft['frozen']=validation['frozen_request']
        draft['paste_preview']={'valid':True,'rows':[]}
        draft['file_preview']={'valid':True,'rows':[]}
        return draft

    def healthy(self,app):self.assertFalse(list(app.exception),[item.message for item in app.exception])

    def click(self,app,key):
        app.button(key=key).click().run();self.healthy(app)

    def app(self,draft=None):
        app=AppTest.from_string(APP,default_timeout=40)
        app.session_state['daily_entry_template_id']=self.fixture['template_id']
        app.session_state['workspace_project_id']=self.fixture['template_id']
        if draft:
            app.session_state['show_daily_entry_page']=True
            app.session_state['daily_draft']=deepcopy(draft)
            app.session_state['daily_restore_selection']=True
        app.run();self.healthy(app)
        return app

    def assert_followed(self,app,original,target):
        draft=app.session_state['daily_draft'];item=draft['context']['items'][0]
        self.assertEqual(item['qc_method'],'lj')
        self.assertEqual(item['runtime_batch_id'],target['target_batch_id'])
        self.assertEqual(item['runtime_project_id'],target['target_project_id'])
        for key in ('draft_id','submission_id','values','notes','operator','test_time','selected','reagents'):
            self.assertEqual(original[key],draft[key],key)
        self.assertIsNone(draft['frozen'])
        self.assertNotIn('paste_preview',draft);self.assertNotIn('file_preview',draft)
        self.assertGreater(draft['edit_version'],original['edit_version'])
        self.assertFalse(any(button.key=='entry_commit' for button in app.button))
        options=app.multiselect(key='entry_selected_'+draft['draft_id']).options
        self.assertTrue(all('L-J' in label for label in options),options)
        self.assertTrue(any('输入已保留' in info.value for info in app.info))
        return draft

    def transfer_in_workbench(self,app):
        self.click(app,'home_open_batch')
        self.click(app,'open_instant_transfer_dialog')
        self.click(app,f'confirm_instant_transfer_dialog_{self.fixture["batch_id"]}')
        target=dict(target_project_id=app.session_state['instant_transfer_target_project_id'],
            target_batch_id=app.session_state['instant_transfer_target_batch_id'])
        self.click(app,'workspace_switch_context')
        return target

    def test_workbench_transfer_return_keeps_inputs_requires_recheck_and_saves_only_lj(self):
        original=self.draft()
        # Previews are separately tested below; keep this full UI route realistic.
        original.pop('paste_preview');original.pop('file_preview')
        app=self.app(original)
        self.assertTrue(any(button.key=='entry_commit' for button in app.button))
        self.click(app,'entry_project_home')
        target=self.transfer_in_workbench(app)
        self.click(app,'workspace_daily_entry')
        self.assert_followed(app,original,target)
        self.click(app,'entry_validate')
        self.assertFalse(list(app.error),[error.value for error in app.error])
        self.click(app,'entry_commit')
        receipt=app.session_state['daily_draft']['receipt']
        self.assertEqual(receipt['items'][0]['qc_method'],'lj')
        with db.read_snapshot() as connection:
            self.assertEqual(20,connection.execute('SELECT COUNT(*) FROM instant_results WHERE batch_id=?',
                (self.fixture['batch_id'],)).fetchone()[0])
            self.assertEqual(21,connection.execute('SELECT COUNT(*) FROM results WHERE batch_id=?',
                (target['target_batch_id'],)).fetchone()[0])
            saved=connection.execute('SELECT value,operator,manual_note,test_time FROM results WHERE batch_id=? ORDER BY id DESC LIMIT 1',
                (target['target_batch_id'],)).fetchone()
            self.assertEqual((102.88,original['operator'],'未保存备注',original['test_time']),tuple(saved))

    def test_saved_draft_restore_and_external_transfer_drop_old_previews(self):
        original=self.draft()
        target=confirm_instant_transfer_to_lj(self.fixture['batch_id'])
        app=self.app()
        app.session_state['daily_saved_drafts']={str(original['context']['selection']):deepcopy(original)}
        self.click(app,'workspace_daily_entry')
        self.assert_followed(app,original,target)
        # Repeated renders retain the migrated identity and any subsequent input.
        draft=app.session_state['daily_draft'];version=draft['edit_version']
        app.text_input(key='daily_operator_'+draft['draft_id']).set_value('转入后继续填写').run()
        self.healthy(app)
        self.assertEqual(app.session_state['daily_draft']['edit_version'],version)
        self.assertEqual(app.session_state['daily_draft']['operator'],'转入后继续填写')

    def test_active_draft_follows_transfer_from_another_session_on_rerender(self):
        original=self.draft();original.pop('paste_preview');original.pop('file_preview')
        app=self.app(original)
        target=confirm_instant_transfer_to_lj(self.fixture['batch_id'])
        app.run();self.healthy(app)
        self.assert_followed(app,original,target)

    def test_same_selection_activate_refreshes_saved_route_and_retains_invalid_time(self):
        original=self.draft();original['test_time']='尚未填写完成'
        target=confirm_instant_transfer_to_lj(self.fixture['batch_id'])
        app=AppTest.from_string('''
import streamlit as st
from ui.daily_entry import _activate
if st.button('恢复草稿'):_activate(st.session_state['fresh_context'])
''')
        app.session_state['daily_draft']=deepcopy(original)
        app.session_state['fresh_context']=self.context()
        app.run();self.healthy(app)
        app.button[0].click().run();self.healthy(app)
        draft=app.session_state['daily_draft']
        self.assertEqual(draft['context']['items'][0]['qc_method'],'lj')
        self.assertEqual(draft['context']['items'][0]['runtime_batch_id'],target['target_batch_id'])
        self.assertEqual(draft['values'],original['values'])
        self.assertEqual(draft['test_time'],'尚未填写完成')
        self.assertIsNone(draft['frozen'])
        self.assertNotIn('paste_preview',draft);self.assertNotIn('file_preview',draft)

    def test_saved_receipt_keeps_original_snapshot_and_new_run_uses_lj(self):
        original=self.draft();original.pop('paste_preview');original.pop('file_preview')
        original['receipt']=submit_daily(original['frozen'])
        target=confirm_instant_transfer_to_lj(self.fixture['batch_id'])
        app=self.app(original)
        self.assertEqual(app.session_state['daily_draft'],original)
        self.assertEqual(app.session_state['daily_draft']['context']['items'][0]['qc_method'],'instant')
        self.assertEqual(app.session_state['daily_draft']['receipt']['items'][0]['qc_method'],'instant')
        # A later run uses current routing while the saved receipt remains frozen.
        next(button for button in app.button if button.label=='再次检测，开始新一组').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['daily_draft']['context']['items'][0]['qc_method'],'lj')
        self.assertEqual(app.session_state['daily_draft']['context']['items'][0]['runtime_batch_id'],target['target_batch_id'])

    def test_ordinary_instant_history_change_does_not_replace_draft_context(self):
        original=self.draft();original.pop('paste_preview');original.pop('file_preview')
        item=original['context']['items'][0]
        save_instant_result(batch_id=self.fixture['batch_id'],test_time='2026-09-11 08:20:00',
            operator='后续同方法检测',value=102,log_value=None,
            lot_selection={'reagent_lot_id':self.reagent,'expected_revision':item['selection_revision']})
        app=self.app(original)
        self.assertEqual(app.session_state['daily_draft'],original)
        self.assertFalse(any('输入已保留' in info.value for info in app.info))


if __name__=='__main__':unittest.main(verbosity=2)
