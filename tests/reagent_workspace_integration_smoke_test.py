"""Real app navigation, queued dialogs and immutable history on isolated databases."""
from datetime import date, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from database import add_result, get_connection, get_results
from qc_logic import calculate_qc_results
from services.lot_lifecycle_service import create_target_profile, result_lot_options
from tests.lot_lifecycle_smoke_test import IsolatedDatabase, lj, verified, switch
from tests.project_workspace_smoke_test import assert_clean, select_table_row


def fixture():
    batch = lj()
    source, alpha, alpha_verification = verified('lj', batch, 'APP-ALPHA')
    _, beta, beta_verification = verified('lj', batch, 'APP-BETA')
    with get_connection() as connection:
        binding = dict(connection.execute("SELECT * FROM qc_workbench_bindings WHERE qc_method='lj' AND runtime_batch_id=?", (batch,)).fetchone())
    return dict(batch=batch, source=source, alpha=alpha, beta=beta,
                alpha_verification=alpha_verification, beta_verification=beta_verification, binding=binding)


def real_app():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=25).run()
    assert_clean(app)
    app.button(key='open_project_management_page').click().run()
    app.session_state['v11_management_tabs'] = '批号使用与追溯'
    app.session_state['lot_management_tabs'] = '试剂批号与换批'
    app.run()
    assert_clean(app)
    return app


def choose_row(app, prefix, column, value):
    index = next(i for i, table in enumerate(app.dataframe) if prefix in table.proto.id)
    select_table_row(app, app.dataframe[index].value[column].tolist().index(value), index=index)
    return app


def reagent_key(app, field):
    return 'rgl_' + app.session_state['reagent_lifecycle_dialog']['token'] + '_' + field


def business_state():
    # Routine workbench sync refreshes its timestamp. Business rows below must
    # remain exactly unchanged by typing, cancelling or rejected submissions.
    with get_connection() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('md_reagent_lots', 'qc_lot_verifications', 'qc_reagent_lot_usage',
                              'qc_lot_change_events', 'qc_target_profiles', 'results', 'zscore_runs',
                              'instant_results', 'qc_result_contexts', 'qc_result_context_levels',
                              'qc_result_evaluations', 'qc_lot_configs', 'qc_lot_config_items',
                              'qc_config_snapshots')}


def retained_history():
    all_rows = business_state()
    return {key: value for key, value in all_rows.items()
            if key not in ('md_reagent_lots', 'qc_lot_verifications', 'qc_reagent_lot_usage', 'qc_lot_change_events')}


def test_real_page_register_return_and_hidden_selection_remain_consistent():
    with IsolatedDatabase():
        f = fixture()
        app = real_app()
        app.text_input(key='reagent_search').set_value('BETA').run()
        app.selectbox(key='reagent_product_filter').set_value(f['source']['identity'][7]).run()
        app.checkbox(key='reagent_show_disabled').check().run()
        choose_row(app, 'reagent_lots_', '试剂批号', 'APP-BETA')
        assert app.session_state['reagent_selected_lot'] == f['beta']
        before = business_state()
        app.button(key='close_project_management_page').click().run()
        app.button(key='open_project_management_page').click().run()
        assert_clean(app)
        assert app.session_state['v11_management_tabs'] == '批号使用与追溯'
        assert app.session_state['lot_management_tabs'] == '试剂批号与换批'
        assert app.text_input(key='reagent_search').value == 'BETA'
        assert app.checkbox(key='reagent_show_disabled').value
        assert app.selectbox(key='reagent_product_filter').value == f['source']['identity'][7]
        assert app.session_state['reagent_selected_lot'] == f['beta']
        assert business_state() == before
        app.button(key='reagent_register').click().run()
        assert app.selectbox(key=reagent_key(app, 'product')).value == f['source']['identity'][7]
        app.text_input(key=reagent_key(app, 'lot_no')).set_value('APP-REGISTERED').run()
        app.date_input(key=reagent_key(app, 'expiry')).set_value(date(2029, 12, 31)).run()
        app.text_area(key=reagent_key(app, 'source')).set_value('厂家资料第一行\n来源第二行').run()
        assert business_state() == before
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_continue').click().run()
        assert app.text_input(key=reagent_key(app, 'lot_no')).value == 'APP-REGISTERED'
        app.button(key='reagent_save').click().run()
        assert_clean(app)
        assert not app.error, [error.value for error in app.error]
        assert 'reagent_lifecycle_dialog' not in app.session_state
        with get_connection() as connection:
            saved = dict(connection.execute("SELECT * FROM md_reagent_lots WHERE lot_no='APP-REGISTERED'").fetchone())
        assert saved['source_text'] == '厂家资料第一行\n来源第二行'
        assert app.session_state['reagent_selected_lot'] == saved['id']
        assert app.text_input(key='reagent_search').value == ''
        app.text_input(key='reagent_search').set_value('APP-ALPHA').run()
        assert app.session_state['reagent_selected_lot'] is None
        assert not any(button.key in ('reagent_verify', 'reagent_switch') for button in app.button)
        choose_row(app, 'reagent_lots_', '试剂批号', 'APP-ALPHA')
        assert app.session_state['reagent_selected_lot'] == f['alpha']


def test_qc_reagent_and_target_drafts_queue_one_dialog_without_losing_values():
    with IsolatedDatabase():
        f = fixture()
        app = real_app()
        app.session_state['target_profile_selected_binding'] = f['binding']['id']
        app.session_state['qc_lifecycle_config_id'] = f['binding']['lot_config_id']
        app.session_state['qc_lifecycle_binding_id'] = f['binding']['id']
        app.run()
        before = business_state()
        app.button(key='target_profile_open').click().run()
        target_token = app.session_state['target_profile_dialog']['token']
        target_evidence = 'target_profile_' + target_token + '_evidence'
        app.text_area(key=target_evidence).set_value('待继续核对的均值和标准差').run()
        app.button(key='reagent_register').click().run()
        app.text_input(key=reagent_key(app, 'lot_no')).set_value('QUEUED-REAGENT').run()
        reagent_token = app.session_state['reagent_lifecycle_dialog']['token']
        assert 'target_profile_dialog' in app.session_state
        assert 'target_profile_save' not in [button.key for button in app.button]
        app.button(key='qc_lifecycle_verify').click().run()
        assert_clean(app)
        assert all(key in app.session_state for key in ('target_profile_dialog', 'reagent_lifecycle_dialog', 'qc_lifecycle_dialog'))
        assert {button.key for button in app.button} & {'qcl_save', 'reagent_save', 'target_profile_save'} == {'qcl_save'}
        qc_prefix = 'qcl_' + app.session_state['qc_lifecycle_dialog']['token'] + '_'
        app.text_input(key=qc_prefix + 'person').set_value('尚未保存的质控品确认人').run()
        app.button(key='qcl_cancel').click().run()
        app.button(key='qcl_discard').click().run()
        assert_clean(app)
        assert 'qc_lifecycle_dialog' not in app.session_state
        assert app.session_state['reagent_lifecycle_dialog']['token'] == reagent_token
        assert app.text_input(key=reagent_key(app, 'lot_no')).value == 'QUEUED-REAGENT'
        assert {button.key for button in app.button} & {'qcl_save', 'reagent_save', 'target_profile_save'} == {'reagent_save'}
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_discard').click().run()
        assert_clean(app)
        assert app.session_state['target_profile_dialog']['token'] == target_token
        assert app.text_area(key=target_evidence).value == '待继续核对的均值和标准差'
        assert {button.key for button in app.button} & {'qcl_save', 'reagent_save', 'target_profile_save'} == {'target_profile_save'}
        app.button(key='target_profile_cancel').click().run()
        app.button(key='target_profile_discard').click().run()
        assert_clean(app)
        assert not any(key in app.session_state for key in ('target_profile_dialog', 'reagent_lifecycle_dialog', 'qc_lifecycle_dialog'))
        assert business_state() == before


def test_real_switch_and_history_correction_preserve_original_results_and_parameters():
    with IsolatedDatabase():
        f = fixture()
        switch(f['source'], f['alpha'], f['alpha_verification'])
        create_target_profile(method='lj', batch_id=f['batch'], levels=[dict(level_id='Level 1', mean=100, sd=2)],
            source='manual', evidence='原批均值和标准差已核对', confirmed_by='集成验收', effective_at='2026-09-02')
        for minute in range(4):
            add_result(f['batch'], f'2026-09-03 08:0{minute}', 103, operator='集成验收', lot_selection={'reagent_lot_id': f['alpha']})
        original_results = get_results(f['batch']).to_dict('records')
        before = retained_history()
        app = real_app()
        app.text_input(key='reagent_search').set_value('BETA').run()
        choose_row(app, 'reagent_lots_', '试剂批号', 'APP-BETA')
        app.button(key='reagent_switch').click().run()
        app.multiselect(key=reagent_key(app, 'systems')).set_value([f['source']['system_id']]).run()
        app.datetime_input(key=reagent_key(app, 'when')).set_value(datetime(2026, 9, 4)).run()
        app.text_input(key=reagent_key(app, 'person')).set_value('集成验收').run()
        app.text_area(key=reagent_key(app, 'reason')).set_value('集成换批，原参数仍适用').run()
        app.checkbox(key=reagent_key(app, 'confirmed')).check().run()
        app.button(key='reagent_save').click().run()
        assert_clean(app)
        assert not app.error, [error.value for error in app.error]
        assert app.session_state['reagent_selected_lot'] == f['beta']
        assert retained_history() == before
        assert get_results(f['batch']).to_dict('records') == original_results
        assert result_lot_options('lj', f['batch'], '2026-09-05')['suggested_lot_id'] == f['beta']
        with get_connection() as connection:
            original_event = dict(connection.execute("SELECT * FROM qc_lot_change_events WHERE event_type='reagent' ORDER BY id DESC").fetchone())
        app.session_state['lot_management_tabs'] = '历史记录'
        app.run()
        choose_row(app, 'reagent_history_table_', '事件编号', original_event['id'])
        app.button(key='reagent_history_open').click().run()
        prefix = 'reagent_history_' + app.session_state['reagent_history_dialog']['token'] + '_'
        app.selectbox(key=prefix + 'reagent_lot_id').set_value(f['alpha']).run()
        app.selectbox(key=prefix + 'verification_id').set_value(f['alpha_verification']).run()
        app.text_input(key=prefix + 'operator').set_value('更正确认人').run()
        app.text_area(key=prefix + 'reason').set_value('更正使用安排，原记录保留').run()
        app.checkbox(key=prefix + 'confirmed').check().run()
        app.button(key='reagent_history_save').click().run()
        assert_clean(app)
        assert not app.error, [error.value for error in app.error]
        assert 'reagent_history_dialog' not in app.session_state
        assert retained_history() == before
        assert get_results(f['batch']).to_dict('records') == original_results
        with get_connection() as connection:
            assert dict(connection.execute('SELECT * FROM qc_lot_change_events WHERE id=?', (original_event['id'],)).fetchone()) == original_event
            correction = dict(connection.execute("SELECT * FROM qc_lot_change_events WHERE event_type='correction' ORDER BY id DESC").fetchone())
        assert correction['corrects_event_id'] == original_event['id']
        assert app.session_state['reagent_history_selected_event'] == correction['id']
        assert result_lot_options('lj', f['batch'], '2026-09-05')['suggested_lot_id'] == f['alpha']
        evaluated, stats = calculate_qc_results(get_results(f['batch']), 5)
        assert stats['mean'] == 100 and stats['sd'] == 2
        assert '4_1s' in evaluated.iloc[-1].rule_hits


def test_disabling_selected_lot_while_verification_is_open_rejects_without_write():
    with IsolatedDatabase():
        f = fixture()
        app = real_app()
        choose_row(app, 'reagent_lots_', '试剂批号', 'APP-BETA')
        app.button(key='reagent_verify').click().run()
        app.selectbox(key=reagent_key(app, 'system')).set_value(f['source']['system_id']).run()
        app.selectbox(key=reagent_key(app, 'conclusion')).set_value('pass').run()
        app.text_area(key=reagent_key(app, 'evidence')).set_value('未保存的验证依据').run()
        app.text_input(key=reagent_key(app, 'person')).set_value('集成验收').run()
        with get_connection() as connection:
            connection.execute('UPDATE md_reagent_lots SET is_disabled=1 WHERE id=?', (f['beta'],))
        before = business_state()
        app.button(key='reagent_save').click().run()
        assert_clean(app)
        assert app.error and any('重新打开' in error.value for error in app.error)
        assert app.text_area(key=reagent_key(app, 'evidence')).value == '未保存的验证依据'
        assert app.session_state['reagent_selected_lot'] is None
        assert business_state() == before
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_discard').click().run()
        assert_clean(app)
        assert business_state() == before


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
