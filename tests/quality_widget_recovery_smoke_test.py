"""Preserve new standard uploads through a real upstream wizard rejection."""
from datetime import date
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from pypdf import PdfWriter
from streamlit.testing.v1 import AppTest
from services.project_config_service import create_project_template
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean,draft_key
from tests.quick_project_setup_smoke_test import database_contents,metadata_values
from tests.quick_project_setup_ui_smoke_test import open_details,fill_quality,quality_prefix


def pdf_bytes():
    stream=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    writer.write(stream);return stream.getvalue()


class QualityWidgetRecoveryTest(unittest.TestCase):
    def registered_form(self):
        values=metadata_values();app=open_details(values)
        app.button(key='quick_details_next').click().run()
        prefix=fill_quality(app,values)
        app.selectbox(key=prefix+'_search_conclusion').set_value('registered').run()
        fields=dict(standard='LAB-UPLOAD-2026',name='隔离上传核对文件',version='1',
            effective_date=date.today().isoformat(),source_url='https://www.nhc.gov.cn/wjw/s9492/wsbz.shtml',
            source_clause='隔离标准条款',source_page='1',verified_by='文件核对人',
            checked_on=date.today().isoformat())
        for field,value in fields.items():
            app.text_input(key=f'{prefix}_registered_0_{field}').set_value(value)
        app.text_area(key=prefix+'_registered_0_requirement').set_value('按本实验室的对照安排核对检测。')
        app.text_area(key=prefix+'_registered_0_evidence').set_value('本条款的方法、标本和尺度与当前项目一致。')
        app.run();assert_clean(app)
        return values,app,prefix

    def reject_new_upload(self,values,app,prefix,contents,*,index=0):
        # AppTest cannot submit a file-uploader event. Provide only that stream
        # at the state-snapshot boundary; all wizard and validation code runs.
        import streamlit as st
        from ui.quick_project_setup import _remember_quality_inputs
        def snapshot_with_upload(ctx):
            inputs=dict(st.session_state.items())
            inputs[prefix+f'_registered_{index}_file']=io.BytesIO(contents)
            with patch.object(type(st.session_state),'items',return_value=list(inputs.items())):
                _remember_quality_inputs(ctx)
        create_project_template(template_name=values['template_name'],
            lab_instrument_id=values['lab_instrument_id'],qc_material_id=values['qc_material_id'])
        before=database_contents()
        with patch('ui.quick_project_setup._remember_quality_inputs',side_effect=snapshot_with_upload):
            app.run();assert_clean(app)
        self.assertTrue(any('同名项目' in row.value for row in app.error))
        self.assertEqual(before,database_contents())
        return before

    def fix_name_and_return(self,app,prefix):
        app.button(key='quick_invalid_back').click().run()
        app.text_input(key=draft_key(app,'template_name')).set_value('修正后的隔离项目').run()
        app.button(key='quick_details_next').click().run();assert_clean(app)
        self.assertEqual(prefix,quality_prefix(app))

    def test_new_valid_upload_survives_upstream_name_conflict_without_restoring_file_widget(self):
        with IsolatedDatabase():
            values,app,prefix=self.registered_form();contents=pdf_bytes()
            before=self.reject_new_upload(values,app,prefix,contents)
            self.fix_name_and_return(app,prefix)
            ctx=app.session_state['project_workspace_dialog'];row=ctx['draft']['quality']['registered_standards'][0]
            self.assertEqual(hashlib.sha256(contents).hexdigest(),row['sha256'])
            self.assertEqual('隔离上传核对文件',row['name'])
            self.assertTrue(any('已保留上次上传的标准全文' in row.value for row in app.caption))
            self.assertFalse(any(key.endswith('_file') for key in ctx['quick_quality_inputs']['values']))
            self.assertIsNone(app.session_state[prefix+'_registered_0_file'])
            app.button(key='quick_quality_next').click().run();assert_clean(app)
            self.assertEqual('preview',app.session_state['project_workspace_dialog']['quick_step'])
            self.assertEqual(before,database_contents())

    def test_new_invalid_upload_clears_old_digest_and_keeps_visible_reupload_error(self):
        with IsolatedDatabase():
            values,app,prefix=self.registered_form()
            old_digest=hashlib.sha256(pdf_bytes()).hexdigest()
            app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][0]['sha256']=old_digest
            app.run();assert_clean(app)
            before=self.reject_new_upload(values,app,prefix,b'not a PDF')
            self.fix_name_and_return(app,prefix)
            row=app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][0]
            self.assertEqual('',row['sha256'])
            self.assertTrue(any('请上传可打开的标准全文 PDF 文件' in row.value for row in app.error))
            self.assertFalse(any('已保留上次上传的标准全文' in row.value for row in app.caption))
            self.assertFalse(any('upload_error' in key for key in row))
            app.run();assert_clean(app)
            self.assertTrue(any('请上传可打开的标准全文 PDF 文件' in row.value for row in app.error))
            app.button(key='quick_quality_next').click().run();assert_clean(app)
            self.assertEqual('quality',app.session_state['project_workspace_dialog']['quick_step'])
            contents=pdf_bytes()
            with patch('streamlit.file_uploader',return_value=io.BytesIO(contents)):
                app.run();assert_clean(app)
            app.run();assert_clean(app)
            row=app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][0]
            self.assertEqual(hashlib.sha256(contents).hexdigest(),row['sha256'])
            self.assertFalse(any('请上传可打开的标准全文 PDF 文件' in row.value for row in app.error))
            self.assertEqual(before,database_contents())

    def test_changed_detection_identity_does_not_reuse_a_recovered_upload(self):
        with IsolatedDatabase():
            values,app,prefix=self.registered_form()
            self.reject_new_upload(values,app,prefix,pdf_bytes())
            self.fix_name_and_return(app,prefix)
            app.button(key='quick_quality_back').click().run()
            app.selectbox(key=draft_key(app,'input_value_type')).set_value('ct').run()
            app.button(key='quick_details_next').click().run();assert_clean(app)
            ctx=app.session_state['project_workspace_dialog']
            self.assertNotEqual(prefix,quality_prefix(app))
            self.assertEqual([],ctx['draft']['quality']['registered_standards'])
            self.assertTrue(all(key.startswith(quality_prefix(app)+'_') for key in ctx['quick_quality_inputs']['values']))

    def test_same_identity_with_different_token_discards_old_widget_snapshot(self):
        app=AppTest.from_string('''
import streamlit as st
from ui.quick_project_setup import _remember_quality_inputs
ctx=dict(token='current',quick_quality_identity='same',draft={'quality':{}},
 quick_quality_inputs=dict(identity='same',token='earlier',values={
  'quick_quality_earlier_same_person':'不应带入的旧确认人',
  'quick_quality_earlier_same_registered_0_upload_error':'旧错误'}))
_remember_quality_inputs(ctx)
st.session_state['captured']=ctx['quick_quality_inputs']
''').run()
        assert_clean(app)
        self.assertEqual(dict(identity='same',token='current',values={}),app.session_state['captured'])

    def test_reduced_standard_count_does_not_restore_removed_upload_or_error(self):
        with IsolatedDatabase():
            values,app,prefix=self.registered_form()
            app.number_input(key=prefix+'_registered_count').set_value(2).run()
            app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][1]['sha256']='a'*64
            app.run();assert_clean(app)
            app.number_input(key=prefix+'_registered_count').set_value(1)
            self.reject_new_upload(values,app,prefix,b'invalid upload from removed row',index=1)
            self.fix_name_and_return(app,prefix)
            ctx=app.session_state['project_workspace_dialog']
            self.assertEqual(1,len(ctx['draft']['quality']['registered_standards']))
            self.assertFalse(any('_registered_1_' in key for key in ctx['quick_quality_inputs']['values']))
            app.number_input(key=prefix+'_registered_count').set_value(2).run();assert_clean(app)
            row=app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][1]
            self.assertEqual('',row['sha256'])
            self.assertFalse(any('请上传可打开的标准全文 PDF 文件' in row.value for row in app.error))

    def test_disabling_standard_lookup_does_not_reuse_old_file_when_reenabled(self):
        with IsolatedDatabase():
            values,app,prefix=self.registered_form()
            app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][0]['sha256']='b'*64
            app.run();assert_clean(app)
            app.checkbox(key=prefix+'_advanced_search').uncheck()
            self.reject_new_upload(values,app,prefix,b'invalid hidden upload')
            self.fix_name_and_return(app,prefix)
            ctx=app.session_state['project_workspace_dialog']
            self.assertEqual([],ctx['draft']['quality']['registered_standards'])
            self.assertFalse(any('_registered_0_' in key for key in ctx['quick_quality_inputs']['values']))
            app.checkbox(key=prefix+'_advanced_search').check().run();assert_clean(app)
            row=app.session_state['project_workspace_dialog']['draft']['quality']['registered_standards'][0]
            self.assertEqual('',row['sha256'])
            self.assertFalse(any('请上传可打开的标准全文 PDF 文件' in row.value for row in app.error))


if __name__=='__main__':
    unittest.main(verbosity=2)
