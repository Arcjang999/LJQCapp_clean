"""Independent isolated 20-assay UI, draft, and result-file boundary checks.

AppTest exercises Python callbacks and reruns. Browser keyboard navigation is
covered separately; these tests deliberately do not claim real Enter/Tab input.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest
import database as db
from services import daily_context_service as contexts
from services import daily_draft_service as drafts
from services import daily_result_io_service as files
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from tests import daily_entry_smoke_test as fixtures


APP = '''
import io
from unittest.mock import patch
import streamlit as st
from ui.common import open_global_page
from ui.daily_navigation import render_daily_return
from ui.daily_entry import render_daily_entry
from ui.daily_overview import render_daily_overview
from pages.settings_page import render_settings_page
from ui.daily_grid import _changed, _pasted
import ui.daily_grid as grid
# Each independent AppTest has its own runtime registry, while Python caches
# the module. Re-register the actual component for this isolated runtime.
grid._GRID=st.components.v2.component('daily_result_grid',html=grid.HTML,css=grid.CSS,js=grid.JS)
def grid_change():
    draft=st.session_state['daily_draft']
    key=st.session_state.get('test_grid_callback_key') or f"daily_grid_{draft['draft_id']}_{draft['edit_version']}_"+','.join(draft['selected'])
    st.session_state[key]=st.session_state['test_grid_payload']
    _changed(key)
def grid_paste():
    draft=st.session_state['daily_draft']
    key=st.session_state.get('test_grid_callback_key') or f"daily_grid_{draft['draft_id']}_{draft['edit_version']}_"+','.join(draft['selected'])
    st.session_state[key]=st.session_state['test_grid_payload']
    _pasted(key)
if st.button('工程入口：系统设置'): open_global_page('show_settings_page')
st.button('工程入口：表格回调',on_click=grid_change)
st.button('工程入口：粘贴回调',on_click=grid_paste)
render_daily_return()
if st.session_state.get('show_settings_page'): render_settings_page()
elif st.session_state.get('show_daily_overview_page'): render_daily_overview()
elif st.session_state.get('show_project_management_page'): st.write('工程验收：设置导航已到达')
else:
    if st.session_state.get('test_upload_bytes'):
        # AppTest cannot upload bytes itself. Supply only the uploaded stream;
        # retain the real file UI, preview, apply, and draft callbacks.
        with patch('streamlit.file_uploader',return_value=io.BytesIO(st.session_state['test_upload_bytes'])):
            render_daily_entry()
    else: render_daily_entry()
'''


class DailyUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {k:getattr(db,k) for k in ('DB_PATH','DEFAULT_DB_PATH','STORAGE_CONFIG_PATH','LEGACY_DB_CANDIDATES')}
        cls.base = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(cls.base.name)/'base.db'
        db.STORAGE_CONFIG_PATH = Path(cls.base.name)/'storage.json'
        db.LEGACY_DB_CANDIDATES=[]
        db.init_db();cls.fixture=fixtures.seed_twenty();cls.base_path=db.DB_PATH

    @classmethod
    def tearDownClass(cls):
        for key,value in cls.original.items():setattr(db,key,value)
        cls.base.cleanup()

    def setUp(self):
        self.temp=TemporaryDirectory();root=Path(self.temp.name)
        db.DB_PATH=db.DEFAULT_DB_PATH=root/'isolated.db'
        db.STORAGE_CONFIG_PATH=root/'storage.json';db.LEGACY_DB_CANDIDATES=[]
        shutil.copyfile(self.base_path,db.DB_PATH)

    def tearDown(self):self.temp.cleanup()

    def context(self):
        f=self.fixture
        return contexts.get_daily_context(f['data']['lab_instrument_id'],f['data']['qc_material_id'],
            f['main_lot'],'2026-09-28 08:00:00',lot_config_id=f['config_id'])

    def draft(self,filled=False):
        draft=drafts.new_draft(self.context(),operator='独立UI验收')
        if filled:
            for n,key in enumerate(draft['values']):draft['values'][key]=str(100+n)
            for key in draft['notes']:draft['notes'][key]='同项各水平统一备注'
        return draft

    def dump(self):
        with db.read_snapshot() as c:return '\n'.join(c.iterdump())

    def healthy(self,app):self.assertFalse(list(app.exception),[e.message for e in app.exception])

    def widget(self,app,kind,label):
        values=[e for e in getattr(app,kind) if e.label==label]
        self.assertEqual(len(values),1,(kind,label));return values[0]

    def click(self,app,label):
        self.widget(app,'button',label).click().run();self.healthy(app)

    def app(self,filled=False):
        app=AppTest.from_string(APP,default_timeout=30)
        app.session_state['show_daily_entry_page']=True
        app.session_state['daily_draft']=self.draft(filled)
        app.session_state['daily_restore_selection']=True
        app.run();self.healthy(app);return app

    def paste_values(self,app):
        draft=app.session_state['daily_draft'];token=draft['draft_id']
        rows=drafts.draft_rows(draft,selected_only=True)
        text='\n'.join(str(100+n) for n,_ in enumerate(rows))
        app.text_area(key='entry_paste_'+token).set_value(text).run()
        self.click(app,'预览粘贴内容');self.click(app,'确认带入本组')

    def test_twenty_callback_enter_without_save_settings_return_and_cancel_clear(self):
        app=self.app();before=self.dump();draft=app.session_state['daily_draft'];token=draft['draft_id']
        first=next(iter(draft['values']));item=first.split(':')[0]
        app.session_state['test_grid_payload']={'values':{first:'123.456','unknown:999':'555'}}
        self.click(app,'工程入口：表格回调')
        self.assertEqual(app.session_state['daily_draft']['values'][first],'123.456')
        self.assertNotIn('unknown:999',app.session_state['daily_draft']['values'])
        app.text_input(key=f'entry_note_{token}_{item}').set_value('尚未保存备注').run()
        app.text_input(key=f'daily_operator_{token}').set_value('返回后仍为同人').run()
        app.text_input(key=f'daily_test_time_{token}').set_value('不是时间，保留输入').run()
        self.assertEqual(before,self.dump())
        expected=deepcopy(app.session_state['daily_draft'])
        self.click(app,'工程入口：系统设置');self.click(app,'返回日常录入')
        for key in ('values','notes','operator','test_time','selected','reagents'):
            self.assertEqual(expected[key],app.session_state['daily_draft'][key])
        self.assertEqual(app.text_input(key=f'entry_note_{token}_{item}').value,'尚未保存备注')
        self.click(app,'清空本组输入');self.click(app,'取消清空')
        self.assertEqual(expected['values'],app.session_state['daily_draft']['values'])
        self.assertEqual(expected['notes'],app.session_state['daily_draft']['notes'])
        self.click(app,'核对并保存')
        self.assertTrue(list(app.error));self.assertEqual(before,self.dump())
        self.assertTrue(any('检测时间无效' in e.value for e in app.error))
        self.assertFalse(any('datetime' in e.value or 'Timestamp' in e.value for e in app.error))
        self.assertEqual(app.session_state['daily_draft']['test_time'],'不是时间，保留输入')

    def test_clear_restore_keeps_visible_notes_and_values(self):
        app=self.app(filled=True);before=self.dump();draft=app.session_state['daily_draft']
        expected=deepcopy(draft);token=draft['draft_id'];item=draft['selected'][0]
        self.click(app,'清空本组输入');self.click(app,'确认清空')
        self.assertTrue(all(v=='' for v in app.session_state['daily_draft']['values'].values()))
        self.assertEqual(app.text_input(key=f'entry_note_{token}_{item}').value,'')
        self.click(app,'恢复清空前输入')
        self.assertEqual(expected['values'],app.session_state['daily_draft']['values'])
        self.assertEqual(expected['notes'],app.session_state['daily_draft']['notes'])
        self.assertEqual(app.text_input(key=f'entry_note_{token}_{item}').value,expected['notes'][item])
        self.assertEqual(before,self.dump())

    def test_twenty_three_new_groups_exact_ids_complete_runs(self):
        app=self.app();submission_ids=set();source_ids=[]
        for group in range(3):
            draft=app.session_state['daily_draft'];submission_ids.add(draft['submission_id']);token=draft['draft_id']
            app.text_input(key=f'daily_test_time_{token}').set_value(f'2026-09-28 {8+group:02d}:00:00').run()
            self.paste_values(app);before=self.dump();self.click(app,'核对并保存')
            self.assertEqual(before,self.dump());self.assertIn('frozen',app.session_state['daily_draft'])
            self.click(app,'确认保存整组');receipt=app.session_state['daily_draft']['receipt']
            self.assertEqual(receipt['count'],20)
            self.assertEqual({str(i['lot_config_item_id']) for i in draft['context']['items']},{str(i['row_key']) for i in receipt['items']})
            source_ids.extend((r['source_type'],r['source_id']) for r in receipt['items'])
            with db.read_snapshot() as c:
                self.assertEqual((group+1)*20,c.execute('SELECT COUNT(*) FROM qc_daily_submission_items').fetchone()[0])
                for row in receipt['items']:
                    if row['qc_method']=='zscore':
                        self.assertEqual(row['level_count'],c.execute('SELECT COUNT(*) FROM zscore_level_results WHERE run_id=?',(row['source_id'],)).fetchone()[0])
            if group<2:
                self.click(app,'再次检测，开始新一组')
                self.assertTrue(all(v=='' for v in app.session_state['daily_draft']['values'].values()))
        self.assertEqual(len(submission_ids),3);self.assertEqual(len(set(source_ids)),60)

    def test_eighteen_item_panel_and_selected_save_keep_explicit_methods(self):
        from services.project_config_service import create_project_template,template_item_rows,save_panel_items,list_template_items
        f=self.fixture
        template=create_project_template(template_name='18项独立配置验收',lab_instrument_id=f['data']['lab_instrument_id'],
            qc_material_id=f['data']['qc_material_id'],default_reagent_id=f['data']['reagent_id'])
        rows=template_item_rows(f['template_id'])[:18]
        for row in rows:row.pop('uid',None)
        save_panel_items(template,rows,expected_revision=1)
        self.assertEqual(len(list_template_items(template)),18)
        self.assertEqual(set(list_template_items(template)['qc_method']),{'lj','zscore','instant'})
        app=self.app(True);draft=app.session_state['daily_draft'];selected=draft['selected'][:18]
        app.multiselect(key='entry_selected_'+draft['draft_id']).set_value(selected).run();self.healthy(app)
        self.click(app,'核对并保存');self.click(app,'确认保存整组')
        receipt=app.session_state['daily_draft']['receipt'];self.assertEqual(receipt['count'],18)
        self.assertEqual({str(r['row_key']) for r in receipt['items']},set(selected))
        with db.read_snapshot() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM qc_daily_submission_items').fetchone()[0],18)
            for row in receipt['items']:
                if row['qc_method']=='zscore':
                    self.assertEqual(row['level_count'],c.execute('SELECT COUNT(*) FROM zscore_level_results WHERE run_id=?',(row['source_id'],)).fetchone()[0])

    def test_paste_preview_cancel_and_changed_selection_require_new_preview(self):
        app=self.app();before=self.dump();draft=app.session_state['daily_draft'];token=draft['draft_id']
        text='\n'.join(str(100+n) for n,_ in enumerate(drafts.draft_rows(draft,selected_only=True)))
        app.text_area(key='entry_paste_'+token).set_value(text).run();self.click(app,'预览粘贴内容')
        self.click(app,'取消粘贴');self.assertTrue(all(v=='' for v in draft['values'].values()))
        self.click(app,'预览粘贴内容')
        app.multiselect(key='entry_selected_'+token).set_value(draft['selected'][:1]).run();self.healthy(app)
        confirm=[b for b in app.button if b.label=='确认带入本组']
        self.assertTrue(not confirm or confirm[0].disabled,'改变已选项目后旧预览仍可带入')
        self.assertEqual(before,self.dump())

    def test_twenty_workbook_exact_roundtrip_and_identity_mutations(self):
        draft=self.draft(True);before=self.dump();data=files.export_daily_workbook(draft)
        book=xlsx_bytes_to_dataframes(data);self.assertEqual(len(book['常规质控结果']),31)
        blank=self.draft();preview=files.preview_daily_workbook(data,blank)
        self.assertTrue(preview['valid'],preview['errors']);files.apply_daily_workbook(blank,preview)
        self.assertEqual(draft['values'],blank['values']);self.assertEqual(draft['notes'],blank['notes'])
        self.assertEqual(draft['reagents'],blank['reagents']);self.assertEqual(before,self.dump())
        changes=[('仪器','仪器','错误仪器'),('项目','项目','错误项目'),
                 ('用途','记录用途','复测'),('非法数值','检测值','nan'),
                 ('不完整',None,None),('重复身份',None,None)]
        for name,column,value in changes:
            with self.subTest(name=name):
                altered=deepcopy(book)
                if column:altered['常规质控结果'].loc[:,column]=value
                elif name=='不完整':altered['常规质控结果']=altered['常规质控结果'].iloc[:-1]
                else:altered['关系'].loc[1:,'定位']=altered['关系'].iloc[1,1]
                result=files.preview_daily_workbook(dataframes_to_xlsx_bytes(altered),self.draft())
                self.assertFalse(result['valid'],name);self.assertEqual(before,self.dump())
        for field,value in [('unit_id',99999),('input_value_type','other'),('qc_level_id',99999),('lot_config_item_id',99999)]:
            altered=deepcopy(book);identity=json.loads(altered['关系'].iloc[1,1]);identity[field]=value
            altered['关系'].iloc[1,1]=json.dumps(identity)
            self.assertFalse(files.preview_daily_workbook(dataframes_to_xlsx_bytes(altered),draft)['valid'])

    def test_invalid_workbook_time_and_stale_preview_rejected_before_apply(self):
        draft=self.draft(True);book=xlsx_bytes_to_dataframes(files.export_daily_workbook(draft))
        book['常规质控结果']['检测时间']='2026-02-30 08:00:00'
        invalid=files.preview_daily_workbook(dataframes_to_xlsx_bytes(book),draft)
        self.assertFalse(invalid['valid'],'文件预览把无效日期标为有效')
        blank=self.draft();preview=files.preview_daily_workbook(files.export_daily_workbook(draft),blank)
        blank['selected']=blank['selected'][:1];before=deepcopy(blank)
        with self.assertRaises(ValueError):files.apply_daily_workbook(blank,preview)
        self.assertEqual(before,blank)

    def test_paste_differing_notes_for_same_run_are_not_silently_overwritten(self):
        draft=self.draft();rows=drafts.draft_rows(draft,selected_only=True)
        text='\n'.join(f'{100+n}\t行备注{n}' for n,_ in enumerate(rows))
        preview=drafts.parse_rectangular_paste(text,rows)
        self.assertFalse(preview['valid'],'同一run的不同备注被末水平覆盖')

    def test_pasted_notes_are_visible_and_grid_paste_only_previews(self):
        app=self.app();before=self.dump();draft=app.session_state['daily_draft'];token=draft['draft_id']
        rows=drafts.draft_rows(draft,selected_only=True)
        text='\n'.join(f'{100+n}\t粘贴后可见备注' for n,_ in enumerate(rows))
        app.session_state['test_grid_payload']={'paste':{'start':rows[0]['key'],'text':text,'nonce':1}}
        self.click(app,'工程入口：粘贴回调')
        self.assertTrue(app.session_state['daily_draft']['paste_preview']['valid'])
        self.assertTrue(all(v=='' for v in app.session_state['daily_draft']['values'].values()))
        self.click(app,'确认带入本组')
        for item in draft['selected']:
            self.assertEqual(app.text_input(key=f'entry_note_{token}_{item}').value,'粘贴后可见备注')
        self.assertEqual(before,self.dump())

    def test_file_ui_preview_cancel_apply_rehydrates_widgets_without_save(self):
        source=self.draft(True);source['operator']='文件中明确检测人';source['test_time']='2026-09-28 10:22:33'
        app=self.app();before=self.dump();draft=app.session_state['daily_draft'];token=draft['draft_id'];item=draft['selected'][0]
        app.session_state['test_upload_bytes']=files.export_daily_workbook(source)
        app.run();self.healthy(app);self.click(app,'预览结果文件');self.click(app,'取消文件预览')
        self.assertTrue(all(v=='' for v in app.session_state['daily_draft']['values'].values()))
        self.click(app,'预览结果文件');self.click(app,'带入结果草稿')
        updated=app.session_state['daily_draft']
        for field in ('values','notes','reagents','operator','test_time'):self.assertEqual(source[field],updated[field])
        self.assertEqual(app.text_input(key=f'entry_note_{token}_{item}').value,source['notes'][item])
        self.assertEqual(app.text_input(key=f'daily_operator_{token}').value,source['operator'])
        self.assertEqual(app.text_input(key=f'daily_test_time_{token}').value,source['test_time'])
        self.assertEqual(before,self.dump())

    def test_file_one_run_cannot_mix_two_individually_valid_reagents(self):
        from services.lot_lifecycle_service import create_reagent_lot,record_lot_verification
        item=next(i for i in self.context()['items'] if i['qc_method']=='zscore')
        second=create_reagent_lot(reagent_id=self.fixture['data']['reagent_id'],lot_no='SECOND-VALID-R',expiry_date='2100-12-31')
        record_lot_verification(template_item_id=item['template_item_id'],system_id=item['system_id'],
            reagent_lot_id=second,conclusion='pass',evidence='同项目第二有效试剂，仅用于文件边界核对',confirmed_by='工程验收',confirmed_at='2026-09-01')
        draft=self.draft(True);book=xlsx_bytes_to_dataframes(files.export_daily_workbook(draft))
        indices=[n for n,r in enumerate(drafts.draft_rows(draft,selected_only=True)) if r['item']['lot_config_item_id']==item['lot_config_item_id']]
        self.assertGreater(len(indices),1)
        book['常规质控结果'].loc[indices[0],'实际试剂批号']='SECOND-VALID-R'
        result=files.preview_daily_workbook(dataframes_to_xlsx_bytes(book),draft)
        self.assertFalse(result['valid'],'单个run两个有效试剂被末水平静默覆盖')

    def test_old_component_callback_cannot_overwrite_new_group(self):
        app=self.app();draft=app.session_state['daily_draft'];first=next(iter(draft['values']))
        app.session_state['test_grid_callback_key']=f"daily_grid_old-draft-id_{draft['edit_version']}_"+','.join(draft['selected'])
        app.session_state['test_grid_payload']={'values':{first:'999'}}
        self.click(app,'工程入口：表格回调')
        self.assertEqual(app.session_state['daily_draft']['values'][first],'')
        app.session_state['test_grid_payload']={'paste':{'start':first,'text':'999\n999','nonce':1}}
        self.click(app,'工程入口：粘贴回调')
        self.assertIsNone(app.session_state['daily_draft'].get('paste_preview'))

    def test_refresh_unavailable_context_keeps_input_and_does_not_crash(self):
        app=self.app(True);expected=deepcopy(app.session_state['daily_draft']['values'])
        token=app.session_state['daily_draft']['draft_id'];first=app.session_state['daily_draft']['selected'][0]
        with db.get_connection() as c:
            c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?',(int(first),))
        self.click(app,'保留输入并重新核对资料')
        self.assertNotIn(first,app.multiselect(key='entry_selected_'+token).value)
        self.assertEqual(len(app.session_state['daily_draft']['context']['items']),19)
        self.assertEqual(expected,app.session_state['daily_draft']['values'])
        with db.get_connection() as c:
            c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE lot_config_id=?',(self.fixture['config_id'],))
        before=self.dump();self.click(app,'保留输入并重新核对资料')
        self.assertEqual(expected,app.session_state['daily_draft']['values'])
        self.assertEqual(before,self.dump())
        save=[b for b in app.button if b.label=='核对并保存']
        self.assertTrue(not save or save[0].disabled)

    def test_instant_overview_keeps_saved_early_record_stage(self):
        from services.daily_entry_service import validate_submission,submit_daily
        from services.daily_overview_service import get_daily_overview
        source_ids=[]
        for n in range(3):
            context=self.context();draft=drafts.new_draft(context,operator='即时历史验收')
            item=next(i for i in context['items'] if i['qc_method']=='instant')
            draft['selected']=[item['row_key']];draft['test_time']=f'2026-09-28 08:0{n}:00'
            # Each new time needs its own current read-only context revision.
            draft['context']=contexts.get_daily_context(test_time=draft['test_time'],**context['selection'])
            for key in draft['values']:draft['values'][key]='110'
            checked=validate_submission(drafts.build_request(draft))
            self.assertTrue(checked['valid'],checked['errors'])
            source_ids.append(submit_daily(checked['frozen_request'])['items'][0]['source_id'])
        before=self.dump();overview=get_daily_overview('2026-09-28',qc_method='instant')
        actual=next(i for i in overview['items'] if i['lot_config_item_id']==item['lot_config_item_id'])
        self.assertEqual(actual['instant_summary']['effective_count'],3)
        self.assertEqual([r['source_id'] for r in actual['records']],source_ids)
        self.assertEqual([r['conclusion'] for r in actual['records']],['尚不足3个有效点','尚不足3个有效点','即时法检验'])
        self.assertEqual([r['effective_sequence'] for r in actual['records']],[1,2,3])
        with db.read_snapshot() as c:
            for record in actual['records']:
                payload=json.loads(c.execute('SELECT e.evaluation_json FROM qc_result_evaluations e JOIN qc_result_contexts x ON x.id=e.context_id WHERE x.instant_result_id=? ORDER BY e.id DESC LIMIT 1',(record['source_id'],)).fetchone()[0])
                self.assertEqual(record['analysis_prompt'],payload['analysis_prompt'])
        self.assertEqual(before,self.dump())

    def test_discard_confirmation_removes_current_cached_draft_only(self):
        app=self.app(True);draft=app.session_state['daily_draft'];before=self.dump();selection=str(draft['context']['selection'])
        app.session_state['daily_saved_drafts']={selection:deepcopy(draft),'other-context':{'keep':True}}
        expected=deepcopy(draft['values']);self.click(app,'放弃本组草稿');self.click(app,'取消放弃')
        self.assertEqual(expected,app.session_state['daily_draft']['values'])
        self.click(app,'放弃本组草稿');self.click(app,'确认放弃草稿')
        self.assertNotIn('daily_draft',app.session_state)
        self.assertNotIn(selection,app.session_state['daily_saved_drafts'])
        self.assertEqual(app.session_state['daily_saved_drafts']['other-context'],{'keep':True})
        self.assertEqual(before,self.dump())

    def test_exact_error_settings_targets_preserve_draft(self):
        from services.daily_context_service import issue
        from uuid import UUID
        return_tokens=set()
        labels={'quality':'核对本项质量目标','parameters':'核对本项均值和标准差',
                'reagent':'核对本项试剂批号与验证','usage':'核对本项质控品使用期间','materials':'核对本项实际材料与水平'}
        fields={'quality':'quality','parameters':'target_profile','reagent':'reagent_lot_id','usage':'usage','materials':'levels'}
        for target in labels:
            with self.subTest(target=target):
                app=self.app(True);draft=app.session_state['daily_draft'];item=draft['context']['items'][0]
                item['issues']=[issue('工程边界：需要核对',row_key=item['row_key'],field=fields[target])]
                app.run();before=self.dump();expected=deepcopy(app.session_state['daily_draft']['values'])
                self.click(app,labels[target]);self.assertTrue(app.session_state['show_project_management_page'])
                token=app.session_state['daily_scroll_restore']['show_daily_entry_page']
                self.assertEqual(UUID(token).hex,token)
                self.assertNotIn(token,return_tokens)
                return_tokens.add(token)
                self.assertEqual(expected,app.session_state['daily_draft']['values'])
                self.assertEqual(app.session_state['daily_settings_focus'],{'lot_config_item_id':item['lot_config_item_id'],'settings_target':target})
                self.assertEqual(app.session_state[f"batch_selected_item_{item['lot_config_id']}"],item['lot_config_item_id'])
                if target in ('quality','materials'):
                    self.assertEqual(app.session_state['v11_management_tabs'],'批次管理')
                else:
                    self.assertEqual(app.session_state['v11_management_tabs'],'批号使用与追溯')
                    expected_tab={'parameters':'均值和标准差管理','reagent':'试剂批号与换批','usage':'新旧批号比对'}[target]
                    self.assertEqual(app.session_state['lot_management_tabs'],expected_tab)
                self.assertEqual(before,self.dump());self.click(app,'返回日常录入')
                self.assertEqual(expected,app.session_state['daily_draft']['values'])

    def test_overview_expanded_record_survives_settings_return(self):
        from services.daily_entry_service import validate_submission,submit_daily
        checked=validate_submission(drafts.build_request(self.draft(True)));self.assertTrue(checked['valid'],checked['errors'])
        receipt=submit_daily(checked['frozen_request']);record=receipt['items'][0]
        from datetime import date
        app=AppTest.from_string(APP,default_timeout=30)
        app.session_state['show_daily_overview_page']=True;app.session_state['daily_day']=date(2026,9,28)
        app.session_state['daily_selected']=f"{record['qc_method']}:{record['runtime_batch_id']}:{record['row_key']}";app.run();self.healthy(app)
        key=f"daily_record_{record['source_type']}_{record['source_id']}"
        expander=next(e for e in app.expander if e.proto.id.endswith('-'+key))
        state=app._tree.get_widget_states();widget=state.widgets.add();widget.id=expander.proto.id;widget.bool_value=False
        app._run(state);self.healthy(app)
        self.assertFalse(app.session_state['daily_overview_values'][key])
        self.click(app,'工程入口：系统设置');self.click(app,'返回今日总览')
        self.assertFalse(app.session_state[key])
        self.assertFalse(next(e for e in app.expander if e.proto.id.endswith('-'+key)).proto.expanded)

    def test_disabled_cross_day_pending_and_full_month_profile_guard(self):
        from contextlib import redirect_stdout
        import io
        from tests.b1_business_chain import seed
        from services.daily_overview_service import get_daily_overview
        from services.lot_lifecycle_service import create_target_profile
        with redirect_stdout(io.StringIO()):fixture=seed(Path(self.temp.name)/'pending-case')
        lj=next(b for b in fixture['bindings'] if b['qc_method']=='lj')
        create_target_profile(method='lj',batch_id=lj['runtime_batch_id'],levels=[{'level_id':'Level 1','mean':10.0,'sd':0.6}],
            source='manual',evidence='第二参数版本的隔离验收',confirmed_by='工程验收',effective_at='2026-09-29')
        with db.read_snapshot() as c:reagent=c.execute('SELECT id FROM md_reagent_lots LIMIT 1').fetchone()[0]
        db.add_result(lj['runtime_batch_id'],'2026-09-29 09:00:00',10,operator='工程验收',lot_selection={'reagent_lot_id':reagent})
        before=self.dump();view=get_daily_overview('2026-09-28')
        quality=next(i['quality'] for i in view['items'] if i['qc_method']=='lj')
        self.assertEqual(quality['rows'],[]);self.assertIn('多个均值和标准差版本',quality['evaluation_reason'])
        self.assertEqual(before,self.dump())
        cross=get_daily_overview('2026-09-30');pending={(r['source_type'],r['source_id']) for r in cross['pending']['cross_day']}
        self.assertTrue(pending)
        selected=cross['items'][0]
        with db.get_connection() as c:c.execute('UPDATE qc_lot_configs SET is_disabled=1')
        before=self.dump();disabled=get_daily_overview('2026-09-30')
        self.assertEqual(disabled['items'],[])
        self.assertEqual(pending,{(r['source_type'],r['source_id']) for r in disabled['pending']['cross_day']})
        filtered=get_daily_overview('2026-09-30',template_id=selected['template_id'],qc_material_id=selected['qc_material_id'],qc_method=selected['qc_method'])
        self.assertTrue(filtered['pending']['cross_day'])
        self.assertTrue(all(r['qc_method']==selected['qc_method'] for r in filtered['pending']['cross_day']))
        self.assertEqual(before,self.dump())
        app=AppTest.from_string(APP,default_timeout=30)
        from datetime import date
        app.session_state['show_daily_overview_page']=True;app.session_state['daily_day']=date(2026,9,30)
        app.run();self.healthy(app)
        self.assertTrue(any(b.key and b.key.startswith('daily_pending_') for b in app.button))


if __name__=='__main__':unittest.main(verbosity=2)
