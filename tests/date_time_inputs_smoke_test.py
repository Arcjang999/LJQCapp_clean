"""Date/time controls show selectable minutes without altering untouched records."""
from datetime import datetime, timezone
import unittest

from streamlit.testing.v1 import AppTest

from ui.date_time_inputs import ceil_minute, minute_value


class MinuteInputTest(unittest.TestCase):
    def test_empty_and_invalid_values_stay_empty(self):
        for value in (None, '', 'invalid', '2026-99-99'):
            self.assertIsNone(minute_value(value))
            self.assertIsNone(ceil_minute(value))

    def test_local_minute_and_exact_lower_bound(self):
        value = datetime(2026, 11, 9, 11, 6, 22, 123456)
        self.assertEqual(datetime(2026, 11, 9, 11, 6), minute_value(value))
        self.assertEqual(datetime(2026, 11, 9, 11, 7), ceil_minute(value))
        self.assertEqual(datetime(2026, 11, 9, 11, 6), ceil_minute(value.replace(second=0, microsecond=0)))

    def test_timezone_and_day_boundary(self):
        utc = datetime(2026, 11, 9, 15, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(datetime(2026, 11, 9, 23, 59), minute_value(utc))
        self.assertEqual(datetime(2026, 11, 10), ceil_minute(utc))

    def test_picker_uses_minutes_and_preserves_an_untouched_record(self):
        app = AppTest.from_string("""
from datetime import datetime
import streamlit as st
from ui.date_time_inputs import minute_datetime_input
original = datetime(2026, 11, 9, 11, 6, 22, 123456)
if 'when' not in st.session_state: st.session_state['when'] = original
st.session_state['returned'] = minute_datetime_input('检测时间', key='when', preserve_original=original)
st.text_input('备注')
""", default_timeout=30).run()
        self.assertFalse(list(app.exception))
        picker = app.datetime_input[0]
        self.assertEqual(60, picker.proto.step)
        self.assertEqual('YYYY-MM-DD', picker.proto.format)
        self.assertEqual(datetime(2026, 11, 9, 11, 6), picker.value)
        app.text_input[0].set_value('只改备注').run()
        self.assertEqual(datetime(2026, 11, 9, 11, 6, 22, 123456), app.session_state['returned'])
        app.datetime_input[0].set_value(datetime(2026, 11, 9, 11, 7)).run()
        self.assertFalse(list(app.exception))
        self.assertEqual(datetime(2026, 11, 9, 11, 7), app.session_state['returned'])

    def test_empty_control_and_callbacks_keep_draft_without_saving(self):
        app = AppTest.from_string("""
import streamlit as st
from ui.date_time_inputs import minute_datetime_input
if 'draft' not in st.session_state: st.session_state['draft'] = None
if 'saved' not in st.session_state: st.session_state['saved'] = None
def remember(): st.session_state['draft'] = st.session_state['when']
minute_datetime_input('生效时间', value=None, key='when', on_change=remember)
if st.button('保存'): st.session_state['saved'] = st.session_state['draft']
""", default_timeout=30).run()
        self.assertFalse(list(app.exception))
        self.assertIsNone(app.datetime_input[0].value)
        chosen = datetime(2026, 11, 9, 11, 6)
        app.datetime_input[0].set_value(chosen).run()
        self.assertEqual(chosen, app.session_state['draft'])
        self.assertIsNone(app.session_state['saved'])
        app.button[0].click().run()
        self.assertEqual(chosen, app.session_state['saved'])
        app.datetime_input[0].set_value(None).run()
        self.assertIsNone(app.session_state['draft'])
        self.assertEqual(chosen, app.session_state['saved'])

    def test_dates_outside_current_decade_survive_other_field_changes(self):
        for original in (datetime(2001, 1, 1, 12, 34, 56), datetime(2099, 1, 1, 12, 34, 56)):
            with self.subTest(original=original):
                app = AppTest.from_string("""
import streamlit as st
from ui.date_time_inputs import minute_datetime_input
minute_datetime_input('生效时间', value=None, key='when')
st.text_input('依据')
""", default_timeout=30)
                app.session_state['when'] = original
                app.run()
                app.text_input[0].set_value('其他字段修改').run()
                self.assertFalse(list(app.exception))
                self.assertEqual(original.replace(second=0), app.datetime_input[0].value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
