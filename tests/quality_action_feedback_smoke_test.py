"""Quality gates explain the current missing action without saving draft data."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.quality_target_service import item_context
from tests.quality_applicability_smoke_test import IsolatedDatabase, context, draft
from tests.quick_project_setup_smoke_test import database_contents


APP = '''
import streamlit as st
from ui.quality_targets import render_quality_setup
result=render_quality_setup(st.session_state['test_item'],st.session_state['test_prior'],
    'feedback',quick=st.session_state.get('test_quick',True),levels=st.session_state.get('test_levels',[]))
st.session_state['rendered_quality']=result
st.button('下一步：查看设置',key='test_next',disabled=not result['checked'] or result['blocked'])
'''


def screenshot_item():
    return dict(test_item_name='丙型肝炎病毒RNA', test_aliases=['HCV-RNA'],
        method_name='实时荧光PCR法', method_code='', method_principle='',
        input_value_type='ct', unit_symbol='Ct', level_count=1)


class QualityActionFeedbackTest(unittest.TestCase):
    def app(self, item=None, prior=None, levels=None, *, quick=True, state=None):
        app=AppTest.from_string(APP,default_timeout=30)
        app.session_state['test_item']=item or screenshot_item()
        app.session_state['test_prior']=prior or dict(confirmed_by='Yan',context=dict(
            technique='realtime_pcr',result_kind='quantitative',result_scale='ct',
            purpose='research',specimen='血清'))
        app.session_state['test_levels']=levels or []
        app.session_state['test_quick']=quick
        for key,value in (state or {}).items():
            app.session_state[key]=value
        app.run();self.healthy(app);return app

    def healthy(self, app):
        self.assertFalse(list(app.exception),[row.message for row in app.exception])

    def issues(self, app):
        return app.session_state['rendered_quality']['validation_issues']

    def warnings(self, app):
        return '\n'.join(row.value for row in app.warning)

    def test_reported_ct_research_screen_explains_unchecked_confirmation(self):
        with IsolatedDatabase():
            before=database_contents();app=self.app()
            self.assertTrue(app.button(key='test_next').disabled)
            self.assertEqual(['checked'],[row['field'] for row in self.issues(app)])
            self.assertIn('请勾选上方“已确认检测方法',self.warnings(app))
            self.assertIn('必填 · 请核对本页设置后勾选确认。',[row.value for row in app.caption])
            self.assertNotIn('保存，稍后继续','\n'.join(row.value for row in app.caption))
            app.checkbox(key='feedback_checked').check().run();self.healthy(app)
            self.assertFalse(app.button(key='test_next').disabled)
            self.assertEqual([],self.issues(app))
            self.assertEqual('research',app.selectbox(key='feedback_context_purpose').value)
            self.assertEqual(before,database_contents())

    def test_missing_confirmer_is_marked_and_clears_without_changing_gate_contract(self):
        with IsolatedDatabase():
            app=self.app();app.text_input(key='feedback_person').set_value('   ')
            app.checkbox(key='feedback_checked').check().run();self.healthy(app)
            self.assertEqual(['confirmed_by'],[row['field'] for row in self.issues(app)])
            self.assertIn('必填 · 请填写质量要求确认人。',[row.value for row in app.caption])
            self.assertIn('请填写“质量要求确认人”。',self.warnings(app))
            # Existing service save validation remains authoritative; no new gate.
            self.assertFalse(app.button(key='test_next').disabled)
            app.text_input(key='feedback_person').set_value('填写人').run()
            self.assertEqual([],self.issues(app))

    def test_input_type_conflict_names_the_actual_previous_step_button(self):
        with IsolatedDatabase():
            item=screenshot_item();item['input_value_type']='raw'
            app=self.app(item=item)
            relevant=[row for row in self.issues(app) if row['field']=='input_value_type']
            self.assertTrue(relevant)
            self.assertTrue(all(row['target']=='返回检测设置' for row in relevant))
            self.assertIn('点击“返回检测设置”',self.warnings(app))
            self.assertIn('“输入值类型”',self.warnings(app))
            self.assertTrue(app.button(key='test_next').disabled)
            self.assertFalse(any('保存，稍后继续' in row.value for row in app.caption))

    def test_standard_unit_conflict_is_visible_and_points_to_unit_setting(self):
        with IsolatedDatabase():
            from services.quality_target_service import get_requirement
            spec=get_requirement('wst403-2024-082')
            _,identifier=draft(name=spec['name'],unit='mmol/L',method='便携式血糖仪法')
            item=item_context('project',identifier)
            item['unit_symbol']='g/L'
            conditions=context(technique='clinical_chemistry',specimen='全血')
            app=self.app(item=item,prior=dict(confirmed_by='核对人',context=conditions))
            relevant=[row for row in self.issues(app) if row['field']=='unit_id']
            self.assertTrue(relevant)
            self.assertTrue(all(row['target']=='返回检测设置' for row in relevant))
            self.assertIn('条目要求单位 mmol/L',self.warnings(app))
            self.assertIn('核对“单位”',self.warnings(app))
            self.assertNotIn('具体缺项见上方提示',self.warnings(app))

    def test_incomplete_advanced_lookup_names_its_collapsed_section(self):
        with IsolatedDatabase():
            app=self.app();app.checkbox(key='feedback_checked').check()
            app.checkbox(key='feedback_advanced_search').check().run();self.healthy(app)
            relevant=[row for row in self.issues(app) if row['field']=='standards']
            self.assertTrue(relevant)
            self.assertEqual('高级：查找或补充标准',relevant[0]['target'])
            self.assertIn('请展开“高级：查找或补充标准”',self.warnings(app))
            self.assertIn('标准查找词、官方来源、核查日期、结论及依据',self.warnings(app))
            app.checkbox(key='feedback_advanced_search').uncheck().run()
            self.assertEqual([],self.issues(app))

    def test_recorded_requirement_missing_fields_and_cv_zero_name_exact_controls(self):
        with IsolatedDatabase():
            app=self.app();app.checkbox(key='feedback_checked').check()
            app.radio(key='feedback_mode').set_value('recorded').run();self.healthy(app)
            app.text_input(key='feedback_source_name').set_value('')
            app.text_input(key='feedback_source_version').set_value('')
            app.text_area(key='feedback_recorded').set_value('').run();self.healthy(app)
            fields={row['field'] for row in self.issues(app)}
            self.assertTrue({'source_name','source_version','requirement_text','evidence'}<=fields)
            self.assertIn('“填写其他实验室要求”',self.warnings(app))
            self.assertIn('“质量要求设置说明”',self.warnings(app))
            _,identifier=draft();item=item_context('project',identifier)
            item['test_item_name']='没有预置数值标准的隔离项目'
            item['test_aliases']=[];item['standard_code']=''
            other=self.app(item=item,prior=dict(confirmed_by='核对人',context=context()))
            other.number_input(key='feedback_cv_limit').set_value(0.0).run()
            self.assertIn('“实验室CV上限”须大于0',self.warnings(other))

    def test_missing_level_rule_explains_concentration_or_category(self):
        with IsolatedDatabase():
            from tests.quality_targets_smoke_test import new_lot
            from services.quality_target_service import get_requirement
            identifier,_,_=new_lot()
            spec=get_requirement('wst403-2024-082')
            item=item_context('lot',identifier)
            item.update(test_item_name=spec['name'],test_aliases=[],standard_code='',abbreviation='',
                method_name='便携式血糖仪法',unit_symbol='mmol/L',specimen_type='全血')
            conditions=context();conditions['technique']='clinical_chemistry';conditions['specimen']='全血'
            app=self.app(item=item,prior=dict(confirmed_by='核对人',context=conditions),
                levels=[dict(level_order=1,level_name='水平1')])
            self.assertTrue(any(row['field']=='levels' for row in self.issues(app)))
            self.assertIn('水平 1 的“质控品浓度（按此浓度选择标准限值）”',self.warnings(app))

    def test_project_settings_issues_name_return_project_and_existing_item_editor(self):
        with IsolatedDatabase():
            from services.quality_target_service import get_requirement
            spec=get_requirement('wst403-2024-082')
            _,identifier=draft(name=spec['name'],unit='mmol/L',method='便携式血糖仪法')
            item=item_context('project',identifier);item['unit_symbol']='g/L'
            app=self.app(item=item,quick=False,
                state={'project_workspace_dialog':dict(kind='quality',item_id=identifier)},
                prior=dict(confirmed_by='核对人',context=context(technique='clinical_chemistry',specimen='全血')))
            relevant=[row for row in self.issues(app) if row['field']=='unit_id']
            self.assertTrue(relevant)
            self.assertTrue(all(row['target']=='编辑检验项目' for row in relevant))
            self.assertIn('点击底部“返回项目”',self.warnings(app))
            self.assertIn('选中本检验项目后点击“编辑检验项目”',self.warnings(app))

    def test_lot_settings_issues_explain_fixed_settings_and_new_batch_route(self):
        with IsolatedDatabase():
            from tests.quality_targets_smoke_test import new_lot
            from services.quality_target_service import get_requirement
            identifier,_,_=new_lot();item=item_context('lot',identifier)
            spec=get_requirement('wst403-2024-082')
            item.update(test_item_name=spec['name'],test_aliases=[],standard_code='',abbreviation='',
                method_name='便携式血糖仪法',unit_symbol='g/L',specimen_type='全血')
            app=self.app(item=item,quick=False,
                state={'batch_item_dialog':dict(kind='quality',item_id=identifier)},
                prior=dict(confirmed_by='核对人',context=context(technique='clinical_chemistry',specimen='全血')))
            relevant=[row for row in self.issues(app) if row['field']=='unit_id']
            self.assertTrue(relevant)
            self.assertTrue(all(row['target']=='编辑检验项目后新建批次' for row in relevant))
            self.assertIn('点击底部“取消”',self.warnings(app))
            self.assertIn('本批次的单位、方法学和输入值类型保持创建时的设置',self.warnings(app))
            self.assertIn('请新建批次采用更新后的设置',self.warnings(app))

    def test_optional_process_references_do_not_become_mandatory_adoptions(self):
        with IsolatedDatabase():
            from ui.quality_targets import quality_validation_issues
            from tests.quality_applicability_smoke_test import search
            app=self.app();app.checkbox(key='feedback_checked').check().run()
            result=dict(app.session_state['rendered_quality'],search_record=search(),
                        adopted_standard_ids=[],process_requirements=dict(source_ids=[],requirement_text=''))
            plan=dict(state='sd_cv',reason='',assessments=[dict(id='reference',kind='process',status='applicable')])
            self.assertEqual([],quality_validation_issues(screenshot_item(),result,plan,quick=True))
            result['search_record']=search('no_applicable')
            issues=quality_validation_issues(screenshot_item(),result,plan,quick=True)
            self.assertEqual(['standards'],[row['field'] for row in issues])
            self.assertIn('修改标准查找结论',issues[0]['message'])
            self.assertNotIn('选择本项目适用的全部要求',issues[0]['message'])


if __name__=='__main__':
    unittest.main(verbosity=2)
