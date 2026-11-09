"""Supplementary standard dates use calendar selection and keep ISO provenance."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from streamlit.testing.v1 import AppTest
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean
from tests.quick_project_setup_smoke_test import database_contents


APP='''
import streamlit as st
from ui.quality_applicability import render_registered_sources
from services.quality_applicability_service import validate_registered_sources
st.session_state['source_rows']=render_registered_sources(st.session_state.get('prior',[]),'standards')
if st.button('检查标准日期'):
    try:
        st.session_state['validated']=validate_registered_sources(st.session_state['source_rows'],{})
    except ValueError as error:
        st.error(str(error))
'''


def source():
    return dict(standard='LAB-CALENDAR-2026',name='补充标准日期检查',version='1',
        effective_date=(date.today()-timedelta(days=20)).isoformat(),withdrawn_on='',
        checked_on=date.today().isoformat(),source_url='https://www.nhc.gov.cn/wjw/s9492/wsbz.shtml',
        sha256='a'*64,source_clause='1',source_page='1',verified_by='标准核对人',
        requirement_text='按本次检测条件核对对照结果。',kind='process',
        applicability_evidence='本次方法、标本及检测用途与此条款一致。')


class RegisteredStandardDatesUITest(unittest.TestCase):
    def test_new_dates_start_empty_and_do_not_create_provenance(self):
        with IsolatedDatabase():
            before=database_contents()
            app=AppTest.from_string(APP).run();assert_clean(app)
            for field in ('effective_date','withdrawn_on','checked_on'):
                self.assertIsNone(app.date_input(key='standards_registered_0_'+field).value)
                self.assertEqual('',app.session_state['source_rows'][0][field])
            self.assertFalse(any('日期' in item.label for item in app.text_input))
            app.button[0].click().run();assert_clean(app)
            self.assertTrue(any('实施日' in item.value for item in app.error))
            self.assertEqual(before,database_contents())

    def test_selected_dates_validate_and_optional_withdrawal_can_be_cleared(self):
        with IsolatedDatabase():
            before=database_contents()
            original=source();app=AppTest.from_string(APP)
            app.session_state['prior']=[original]
            app.run();assert_clean(app)
            effective=date.today()-timedelta(days=5)
            withdrawn=date.today()+timedelta(days=30)
            app.date_input(key='standards_registered_0_effective_date').set_value(effective)
            app.date_input(key='standards_registered_0_withdrawn_on').set_value(withdrawn)
            app.button[0].click().run();assert_clean(app)
            validated=app.session_state['validated'][0]
            self.assertEqual(effective.isoformat(),validated['effective_date'])
            self.assertEqual(withdrawn.isoformat(),validated['withdrawn_on'])
            app.date_input(key='standards_registered_0_withdrawn_on').set_value(None)
            app.button[0].click().run();assert_clean(app)
            self.assertEqual('',app.session_state['validated'][0]['withdrawn_on'])
            self.assertEqual(original['checked_on'],app.session_state['validated'][0]['checked_on'])
            self.assertEqual(before,database_contents())

    def test_saved_string_widget_snapshot_restores_as_date_and_keeps_missing_empty(self):
        with IsolatedDatabase():
            app=AppTest.from_string(APP)
            original=source()
            app.session_state['prior']=[original]
            for field in ('effective_date','checked_on','withdrawn_on'):
                app.session_state['standards_registered_0_'+field]=original[field]
            app.run();assert_clean(app)
            self.assertEqual(date.fromisoformat(original['effective_date']),
                app.date_input(key='standards_registered_0_effective_date').value)
            self.assertEqual(date.fromisoformat(original['checked_on']),
                app.date_input(key='standards_registered_0_checked_on').value)
            self.assertIsNone(app.date_input(key='standards_registered_0_withdrawn_on').value)
            app.date_input(key='standards_registered_0_checked_on').set_value(None)
            app.button[0].click().run();assert_clean(app)
            self.assertEqual('',app.session_state['source_rows'][0]['checked_on'])
            self.assertTrue(any('核查人' in item.value for item in app.error))

    def test_historical_and_future_dates_survive_other_edits_and_can_be_cleared(self):
        with IsolatedDatabase():
            before=database_contents()
            original=source()
            app=AppTest.from_string(APP)
            app.session_state['prior']=[original]
            app.run();assert_clean(app)
            chosen={'effective_date':date(2001,1,2),'checked_on':date(2001,1,3),
                'withdrawn_on':date(2099,12,30)}
            for field,value in chosen.items():
                app.date_input(key='standards_registered_0_'+field).set_value(value)
            app.text_input(key='standards_registered_0_version').set_value('2').run()
            assert_clean(app)
            for field,value in chosen.items():
                self.assertEqual(value,app.date_input(key='standards_registered_0_'+field).value)
                self.assertEqual(value.isoformat(),app.session_state['source_rows'][0][field])
            app.button[0].click().run();assert_clean(app)
            self.assertFalse(app.error)
            self.assertEqual('2001-01-02',app.session_state['validated'][0]['effective_date'])
            self.assertEqual('2099-12-30',app.session_state['validated'][0]['withdrawn_on'])
            for field in chosen:
                app.date_input(key='standards_registered_0_'+field).set_value(None)
            app.text_input(key='standards_registered_0_version').set_value('3').run()
            assert_clean(app)
            for field in chosen:
                self.assertIsNone(app.date_input(key='standards_registered_0_'+field).value)
                self.assertEqual('',app.session_state['source_rows'][0][field])
            self.assertEqual(before,database_contents())

    def test_future_confirmation_date_is_kept_for_correction_and_rejected_by_service(self):
        with IsolatedDatabase():
            before=database_contents()
            app=AppTest.from_string(APP)
            app.session_state['prior']=[source()]
            app.run();assert_clean(app)
            app.date_input(key='standards_registered_0_checked_on').set_value(date(2099,12,30))
            app.text_input(key='standards_registered_0_name').set_value('待纠正的核查日期').run()
            assert_clean(app)
            self.assertEqual(date(2099,12,30),app.date_input(key='standards_registered_0_checked_on').value)
            app.button[0].click().run();assert_clean(app)
            self.assertTrue(any('核查日期晚于今天' in item.value for item in app.error))
            self.assertEqual(date(2099,12,30),app.date_input(key='standards_registered_0_checked_on').value)
            self.assertEqual(before,database_contents())


if __name__=='__main__':
    unittest.main(verbosity=2)
