"""Form prerequisite messages follow current editability and available records."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest


class ExtendedFormFeedbackTest(unittest.TestCase):
    def healthy(self, app):
        self.assertFalse(list(app.exception), [error.message for error in app.exception])

    def has_info(self, app, text):
        return any(text in item.value for item in app.info)

    def test_unconfirmed_readonly_parameters_do_not_offer_an_unavailable_edit(self):
        app = AppTest.from_string('''
import streamlit as st
from ui.target_profile_workspace import _render_detail
editable=st.session_state.get('test_editable',False)
_render_detail({'snapshot':{'test_item_name':'项目甲'},'binding':{'id':1},
 'template':{'template_name':'项目甲'},'config':{'config_name':'批次甲'},'profiles':[],
 'current':None,'editable':editable,'read_only_reason':'此批次已停用，均值和标准差仅供查询。'})
''').run()
        self.healthy(app)
        self.assertTrue(app.button(key='target_profile_open').disabled)
        self.assertTrue(self.has_info(app, '此批次已停用'))
        self.assertFalse(self.has_info(app, '点击“确认均值和标准差”'))
        app.session_state['test_editable'] = True
        app.run()
        self.healthy(app)
        self.assertFalse(app.button(key='target_profile_open').disabled)
        self.assertTrue(self.has_info(app, '点击“确认均值和标准差”'))

    def test_manufacturer_reference_button_retains_readonly_reason_and_busy_guidance(self):
        app = AppTest.from_string('''
from contextlib import nullcontext
from unittest.mock import patch
import streamlit as st
from ui.zscore_reference_dialog import render_zscore_reference_summary
def writable(*args,**kwargs):
 if not st.session_state.get('test_writable',False):raise ValueError('此批次已停止使用，仅供查询。')
with patch('database.read_snapshot',return_value=nullcontext(None)),patch('ui.zscore_reference_dialog.require_writable',side_effect=writable):
 render_zscore_reference_summary(1,'2_level_classic',5,{'Level 1':{}},['Level 1'],{'Level 1':'低值'})
''').run()
        self.healthy(app)
        self.assertTrue(app.button(key='zscore_reference_open_1').disabled)
        self.assertTrue(self.has_info(app, '此批次已停止使用'))
        app.session_state['test_writable'] = True
        app.run()
        self.assertFalse(app.button(key='zscore_reference_open_1').disabled)
        self.assertFalse(self.has_info(app, '此批次已停止使用'))
        app.session_state['qc_transfer_dialog'] = {'mode':'import'}
        app.run()
        self.assertTrue(app.button(key='zscore_reference_open_1').disabled)
        self.assertTrue(self.has_info(app, '请先完成或取消当前打开'))

    def test_empty_qc_verification_choice_has_a_real_registration_route(self):
        app = AppTest.from_string('''
import streamlit as st
from ui.qc_lifecycle_workspace import _verification_choice
if 'test_state' not in st.session_state:
 st.session_state['test_state']={'token':'form-test','draft':{'verification':None}}
rows=[{'id':7,'conclusion':'pass','confirmed_at':'2026-10-01 08:00:00','confirmed_by':'核对人','evidence':'检查记录'}] if st.session_state.get('test_registered') else []
_verification_choice(st.session_state['test_state'],'verification','关联验证：QC-A *',rows)
''').run()
        self.healthy(app)
        self.assertTrue(self.has_info(app, '在“登记批号验证”中登记该批号'))
        self.assertEqual(['请选择验证记录'], app.selectbox[0].options)
        app.session_state['test_registered'] = True
        app.run()
        self.healthy(app)
        self.assertFalse(self.has_info(app, '还没有验证记录'))
        app.selectbox[0].set_value(7).run()
        self.assertEqual(7, app.session_state['test_state']['draft']['verification'])

    def test_missing_reagent_verification_explains_cancel_and_registration_without_losing_inputs(self):
        app = AppTest.from_string('''
from datetime import datetime
from unittest.mock import patch
import streamlit as st
from ui.reagent_history_workspace import _render_editor
if 'test_state' not in st.session_state:
 draft={'reagent_lot_id':1,'effective_at':datetime(2026,10,2,8),'verification_id':None,'operator':'','reason':'','confirmed':False}
 st.session_state['test_state']={'token':'history-test','event_id':1,'discard':False,'fingerprint':'fixed',
  'context':{'event':{'system_id':4},'system':{'snapshot':{'identity':[None]*7+[99]}},
   'lots':[{'id':1,'reagent_id':99,'lot_no':'R-A','expiry_date':'2100-12-31'}],'verifications':[]},
  'draft':draft,'initial':dict(draft)}
state=st.session_state['test_state']
if st.session_state.get('test_registered'):
 state['context']['verifications']=[{'id':7,'system_id':4,'reagent_lot_id':1,'conclusion':'pass','confirmed_at':'2026-10-01 08:00:00','confirmed_by':'核对人','evidence':'检查记录'}]
with patch('ui.reagent_history_workspace._render_event'):
 _render_editor(state,{'can_correct':True,'fingerprint':'fixed'})
''').run()
        self.healthy(app)
        self.assertTrue(self.has_info(app, '先点击“取消”退出更正'))
        self.assertTrue(self.has_info(app, '“登记批号验证”中填写并保存'))
        app.text_input(key='reagent_history_history-test_operator').set_value('尚未保存的人员').run()
        app.text_area(key='reagent_history_history-test_reason').set_value('尚未保存的原因').run()
        app.session_state['test_registered'] = True
        app.run()
        self.healthy(app)
        self.assertFalse(self.has_info(app, '没有可采用的通过验证'))
        self.assertEqual('尚未保存的人员', app.text_input(key='reagent_history_history-test_operator').value)
        self.assertEqual('尚未保存的原因', app.text_area(key='reagent_history_history-test_reason').value)
        self.assertEqual(2, len(app.selectbox(key='reagent_history_history-test_verification_id').options))

    def test_unrelated_configuration_issues_do_not_claim_all_batches_are_blocked(self):
        for method in ('zscore','instant'):
            with self.subTest(method=method):
                app = AppTest.from_string(f'''
import streamlit as st
import pandas as pd
from pages.{method}_config_section import render_{method}_v12_configuration_selection
projects=pd.DataFrame([] if st.session_state.get('test_empty') else [dict(id=1,name='可用项目',instrument_name='仪器甲',level_count=2,input_value_type='raw')])
render_{method}_v12_configuration_selection(st.container(),projects,None,pd.DataFrame(),None,
 [dict(config_name='另一个未完成批次',test_item_name='项目乙',lot_no='QC-B',issue='尚未选择水平')])
''').run()
                self.healthy(app)
                warning = '\n'.join(item.value for item in app.warning)
                self.assertIn('设置已确认的其他批次仍可选择', warning)
                self.assertNotIn('暂不能录入', warning)
                self.assertFalse(next(item for item in app.expander if item.label=='查看待检查项目').proto.expanded)
                app.session_state['test_empty'] = True
                app.run()
                self.healthy(app)
                self.assertTrue(next(item for item in app.expander if item.label=='查看待检查项目').proto.expanded)
                self.assertTrue(any('请先完成并确认所需批次' in item.value for item in app.warning))
                self.assertFalse(any('其他批次仍可选择' in item.value for item in app.warning))

    def test_inactive_binding_routes_draft_and_previously_confirmed_batches_differently(self):
        app = AppTest.from_string('''
import streamlit as st
from ui.qc_lifecycle_workspace import _render_binding_detail
_render_binding_detail(dict(binding=dict(id=1,qc_method='zscore',binding_status=st.session_state.get('binding_status','inactive')),
 config=dict(status=st.session_state.get('config_status','draft')),source={},state='active',profile=None,
 state_plan=None,actual_lots={},verifications=[],lots=[]))
''').run()
        self.healthy(app)
        self.assertTrue(app.button(key='qc_lifecycle_combination').disabled)
        self.assertTrue(self.has_info(app,'点击“确认批次设置”'))
        app.session_state['config_status']='active'
        app.run();self.healthy(app)
        self.assertTrue(app.button(key='qc_lifecycle_combination').disabled)
        self.assertFalse(self.has_info(app,'点击“确认批次设置”'))
        self.assertTrue(self.has_info(app,'打开批次设置”查看具体原因'))
        app.session_state['binding_status']='active'
        app.run();self.healthy(app)
        self.assertFalse(app.button(key='qc_lifecycle_combination').disabled)
        self.assertFalse(self.has_info(app,'暂不可用'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
