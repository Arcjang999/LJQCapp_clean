"""Real reagent settings routes capture unsaved entry fields and keep saved provenance."""
from datetime import date, datetime
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

from database import get_connection
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.lot_lifecycle_smoke_test import lj, verified, switch


def make_app(method, batch_id):
    app = AppTest.from_string('''
import streamlit as st
from datetime import datetime
from pages.lot_lifecycle_section import (
    restore_result_lot_entry_draft, render_result_lot_choice, render_result_lot_preparation,
    _render_result_lot_entry_return,
)
from ui.reagent_lifecycle_workspace import render_reagent_lifecycle_workspace, render_pending_reagent_lifecycle_dialog
method, batch = st.session_state['method'], st.session_state['batch']
prefix = {'lj': '', 'zscore': 'zscore_', 'instant': 'instant_'}[method]
key = method + '_entry'
if st.session_state.get('show_project_management_page'):
    _render_result_lot_entry_return()
    render_reagent_lifecycle_workspace()
    render_pending_reagent_lifecycle_dialog()
else:
    restore_result_lot_entry_draft(method, batch)
    st.session_state.setdefault(prefix+'entry_batch_id', batch)
    st.session_state.setdefault(prefix+'entry_test_time', datetime(2026, 9, 3, 8))
    def fields():
        when = st.datetime_input('检测时间', key=prefix+'entry_test_time')
        st.text_input('检测人', key=prefix+'entry_operator')
        if method == 'zscore':
            st.text_input('水平 1', key='zscore_level1_value')
            st.text_input('水平 2', key='zscore_level2_value')
        else:
            st.text_input('检测值', key=prefix+'entry_value')
        return render_result_lot_choice(method, batch, when, key)
    if method == 'zscore':
        with st.form('entry'):
            selection = fields()
            if st.form_submit_button('保存本次检测', key='probe_save'):
                st.session_state['save_clicked'] = True
            render_result_lot_preparation(method, batch, key, in_form=True)
    else:
        selection = fields()
        render_result_lot_preparation(method, batch, key)
    st.json(selection)
''', default_timeout=30)
    app.session_state['method'] = method
    app.session_state['batch'] = batch_id
    return app.run()


def clean(app):
    assert not app.exception, [error.value for error in app.exception]


def result_counts():
    with get_connection() as connection:
        return tuple(connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                     for table in ('results', 'zscore_runs', 'instant_results', 'qc_result_contexts'))


def test_registration_and_verification_return_preserve_all_entry_drafts_including_unsent_form():
    for method in ('lj', 'zscore', 'instant'):
        with IsolatedDatabase():
            batch = lj() if method == 'lj' else (
                seed_zscore_configuration()['batch_id'] if method == 'zscore' else seed_instant_configuration()['batch_id'])
            with get_connection() as connection:
                binding = dict(connection.execute('''SELECT b.*,c.template_id,i.reagent_id FROM qc_workbench_bindings b
                    JOIN qc_lot_configs c ON c.id=b.lot_config_id JOIN qc_lot_config_items i ON i.id=b.lot_config_item_id
                    WHERE b.qc_method=? AND b.runtime_batch_id=?''', (method, batch)).fetchone())
            app = make_app(method, batch)
            clean(app)
            before = result_counts()
            prefix = {'lj': '', 'zscore': 'zscore_', 'instant': 'instant_'}[method]
            app.text_input(key=prefix+'entry_operator').set_value('保留填写人')
            if method == 'zscore':
                app.text_input(key='zscore_level1_value').set_value('102.37')
                app.text_input(key='zscore_level2_value').set_value('205.68')
            else:
                app.text_input(key=prefix+'entry_value').set_value('102.37')
            # The settings click arrives with the latest inputs, including an unsubmitted zscore form.
            app.button(key=f'{method}_entry_prepare_reagent_{batch}').click().run()
            clean(app)
            assert not app.session_state.filtered_state.get('save_clicked')
            assert app.session_state['reagent_product_filter'] == binding['reagent_id']
            assert app.session_state['v11_selected_template_id'] == binding['template_id']
            assert app.session_state[f"batch_selected_item_{binding['lot_config_id']}"] == binding['lot_config_item_id']
            assert app.session_state['lot_management_tabs'] == '试剂批号与换批'
            app.button(key='reagent_register').click().run()
            state = app.session_state['reagent_lifecycle_dialog']
            token = 'rgl_'+state['token']+'_'
            assert app.selectbox(key=token+'product').value == binding['reagent_id']
            app.text_input(key=token+'lot_no').set_value('RETURN-LOT-'+method)
            app.date_input(key=token+'expiry').set_value(date(2028, 12, 31))
            app.button(key='reagent_save').click().run()
            clean(app)
            lot = app.session_state['reagent_selected_lot']
            app.button(key='reagent_verify').click().run()
            token = 'rgl_'+app.session_state['reagent_lifecycle_dialog']['token']+'_'
            assert app.selectbox(key=token+'system').value == app.session_state['reagent_focus_system_id']
            app.selectbox(key=token+'conclusion').set_value('pass')
            app.text_area(key=token+'evidence').set_value('本项目试剂批号核对记录')
            app.text_input(key=token+'person').set_value('批号确认人')
            app.get('date_time_input')[0].set_value(datetime(2026, 9, 1, 8))
            app.button(key='reagent_save').click().run()
            clean(app)
            assert any('切换到此试剂批号' in notice.value for notice in app.success)
            app.button(key='result_lot_preparation_back').click().run()
            clean(app)
            assert app.text_input(key=prefix+'entry_operator').value == '保留填写人'
            assert app.get('date_time_input')[0].value == datetime(2026, 9, 3, 8)
            if method == 'zscore':
                assert app.text_input(key='zscore_level1_value').value == '102.37'
                assert app.text_input(key='zscore_level2_value').value == '205.68'
            else:
                assert app.text_input(key=prefix+'entry_value').value == '102.37'
            choices = app.selectbox(key=f'{method}_entry_actual_lot_{batch}')
            assert choices.value is None and any('RETURN-LOT-'+method in label for label in choices.options)
            assert 'result_lot_preparation_return' not in app.session_state
            assert result_counts() == before
            with get_connection() as connection:
                assert connection.execute('SELECT COUNT(*) FROM qc_reagent_lot_usage').fetchone()[0] == 0
                assert connection.execute('SELECT reagent_id FROM md_reagent_lots WHERE id=?', (lot,)).fetchone()[0] == binding['reagent_id']


def test_detection_time_change_clears_unavailable_lot_and_never_restores_it_as_a_selection():
    with IsolatedDatabase():
        batch = seed_instant_configuration()['batch_id']
        source, lot, verification = verified('instant', batch, 'TIME-BOUND')
        switch(source, lot, verification)
        app = make_app('instant', batch)
        choice_key = f'instant_entry_actual_lot_{batch}'
        app.selectbox(key=choice_key).set_value(lot).run()
        app.get('date_time_input')[0].set_value(datetime(2029, 1, 1)).run()
        clean(app)
        assert app.session_state[choice_key] is None
        assert any('验证的完成时间、结论和效期' in info.value for info in app.info)
        app.get('date_time_input')[0].set_value(datetime(2026, 9, 3, 8)).run()
        clean(app)
        assert app.selectbox(key=choice_key).value is None
        assert result_counts() == (0, 0, 0, 0)


def test_failed_settings_lookup_keeps_inputs_and_does_not_change_navigation_or_return_state():
    with IsolatedDatabase():
        batch = seed_instant_configuration()['batch_id']
        app = make_app('instant', batch)
        app.text_input(key='instant_entry_operator').set_value('仍在填写')
        app.text_input(key='instant_entry_value').set_value('123.456')
        with patch('pages.lot_lifecycle_section.source_context', side_effect=ValueError('检测设置已更新，请重新核对。')):
            app.button(key=f'instant_entry_prepare_reagent_{batch}').click().run()
        clean(app)
        assert any('检测设置已更新' in error.value for error in app.error)
        assert app.text_input(key='instant_entry_operator').value == '仍在填写'
        assert app.text_input(key='instant_entry_value').value == '123.456'
        assert not app.session_state.filtered_state.get('show_project_management_page')
        assert 'result_lot_preparation_return' not in app.session_state
        assert result_counts() == (0, 0, 0, 0)


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
