"""Temporary-database checks for history selection and append-only correction dialogs."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import add_result, get_connection
from services.lot_lifecycle_service import create_target_profile, record_lot_verification
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.lot_lifecycle_smoke_test import lj, verified, switch


def seed():
    batch = lj()
    source, first, first_verification = verified('lj', batch, '历史试剂-A')
    _, second, second_verification = verified('lj', batch, '历史试剂-B')
    first_event = switch(source, first, first_verification, '2026-09-02')
    second_event = switch(source, second, second_verification, '2026-09-04')
    create_target_profile(method='lj', batch_id=batch, levels=[dict(level_id='Level 1', mean=100, sd=2)],
        source='manual', evidence='历史只读参数依据', confirmed_by='测试确认人', effective_at='2026-09-02')
    add_result(batch, '2026-09-03', 101, operator='原操作者', lot_selection={'reagent_lot_id': first})
    with get_connection() as connection:
        binding = connection.execute("SELECT id FROM qc_workbench_bindings WHERE qc_method='lj' AND runtime_batch_id=?", (batch,)).fetchone()[0]
        target_event = connection.execute("SELECT id FROM qc_lot_change_events WHERE event_type='target'").fetchone()[0]
    return dict(batch=batch, binding=binding, source=source, first=first, first_verification=first_verification,
        second=second, second_verification=second_verification, first_event=first_event, event=second_event, target_event=target_event)


def database_state():
    with get_connection() as connection:
        return tuple(connection.iterdump())


def result_history():
    with get_connection() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
            for table in ('results', 'qc_result_contexts', 'qc_result_context_levels', 'qc_result_evaluations', 'qc_target_profiles')}


def events():
    with get_connection() as connection:
        return {row['id']: dict(row) for row in connection.execute('SELECT * FROM qc_lot_change_events ORDER BY id')}


def make_app(selected=None):
    app = AppTest.from_string('''
import streamlit as st
from ui.reagent_history_workspace import render_reagent_history_workspace, render_pending_reagent_history_dialog
if 'show_history' not in st.session_state:
    st.session_state.show_history = True
if st.button('切换页面', key='leave_history'):
    st.session_state.show_history = not st.session_state.show_history
if st.session_state.show_history:
    render_reagent_history_workspace()
    render_pending_reagent_history_dialog()
''', default_timeout=30)
    if selected is not None:
        app.session_state['reagent_history_selected_event'] = selected
    return app.run()


def key(app, field):
    return 'reagent_history_' + app.session_state['reagent_history_dialog']['token'] + '_' + field


def open_dialog(app):
    app.button(key='reagent_history_open').click().run()
    assert not app.exception
    return app


def fill(app, fixture):
    app.selectbox(key=key(app, 'reagent_lot_id')).set_value(fixture['first']).run()
    app.selectbox(key=key(app, 'verification_id')).set_value(fixture['first_verification'])
    app.text_input(key=key(app, 'operator')).set_value('更正核对人')
    app.text_area(key=key(app, 'reason')).set_value('查原始工作记录确认继续使用 A 批。')
    app.checkbox(key=key(app, 'confirmed')).check()
    return app


def test_all_event_types_are_read_only_and_original_result_provenance_remains_available():
    with IsolatedDatabase():
        fixture = seed()
        before = database_state()
        app = make_app(fixture['target_event'])
        assert not app.exception
        assert not [button for button in app.button if button.key == 'reagent_history_open']
        assert {'试剂换批', '均值和标准差确认'} <= set(app.dataframe[0].value['操作'])
        app.selectbox(key='reagent_history_result_binding').set_value(fixture['binding']).run()
        assert not app.exception
        assert any(expander.label == '实际批号与历史判定追溯' for expander in app.expander)
        assert any('历史试剂-A' in str(table.value) for table in app.dataframe)
        assert database_state() == before


def test_typing_cancel_continue_failure_and_save_preserve_original_events_and_results():
    with IsolatedDatabase():
        fixture = seed()
        before = database_state()
        old_events, old_results = events(), result_history()
        app = open_dialog(make_app(fixture['event']))
        fill(app, fixture).run()  # Ordinary input rerun (including a browser Enter commit) is not a save.
        assert not app.exception and database_state() == before
        assert not list(app.get('form'))
        app.button(key='reagent_history_cancel').click().run()
        assert app.button(key='reagent_history_continue')
        app.button(key='reagent_history_continue').click().run()
        assert app.text_input(key=key(app, 'operator')).value == '更正核对人'
        assert app.text_area(key=key(app, 'reason')).value == '查原始工作记录确认继续使用 A 批。'
        assert app.selectbox(key=key(app, 'reagent_lot_id')).value == fixture['first']
        assert app.checkbox(key=key(app, 'confirmed')).value
        with patch('ui.reagent_history_workspace.save_reagent_correction', side_effect=ValueError('模拟保存失败')):
            app.button(key='reagent_history_save').click().run()
        assert any('模拟保存失败' in error.value for error in app.error)
        assert app.text_input(key=key(app, 'operator')).value == '更正核对人'
        assert database_state() == before
        app.button(key='reagent_history_save').click().run()
        assert not app.exception
        current = events()
        assert len(current) == len(old_events) + 1
        for identifier, event in old_events.items():
            assert current[identifier] == event
        new_id = max(current)
        assert current[new_id]['event_type'] == 'correction' and current[new_id]['corrects_event_id'] == fixture['event']
        assert current[new_id]['next_id'] == fixture['first']
        assert app.session_state['reagent_history_selected_event'] == new_id
        assert 'reagent_history_dialog' not in app.session_state
        assert result_history() == old_results
        with get_connection() as connection:
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_cancel_discard_and_reopen_use_independent_drafts_and_require_explicit_confirmation():
    with IsolatedDatabase():
        fixture = seed()
        before = database_state()
        app = open_dialog(make_app(fixture['event']))
        token = app.session_state['reagent_history_dialog']['token']
        app.text_input(key=key(app, 'operator')).set_value('未保存内容').run()
        app.button(key='reagent_history_cancel').click().run()
        app.button(key='reagent_history_discard').click().run()
        assert 'reagent_history_dialog' not in app.session_state
        app.session_state['reagent_history_selected_event'] = fixture['first_event']
        app.run()
        open_dialog(app)
        assert app.session_state['reagent_history_dialog']['token'] != token
        assert app.selectbox(key=key(app, 'reagent_lot_id')).value == fixture['first']
        assert app.text_input(key=key(app, 'operator')).value == ''
        assert app.text_area(key=key(app, 'reason')).value == ''
        app.button(key='reagent_history_save').click().run()
        assert any('明确确认' in error.value for error in app.error)
        assert database_state() == before


def test_stale_dialog_refuses_changed_verification_and_keeps_input():
    with IsolatedDatabase():
        fixture = seed()
        app = open_dialog(make_app(fixture['event']))
        fill(app, fixture).run()
        record_lot_verification(template_item_id=fixture['source']['project_template_item_id'],
            system_id=fixture['source']['system_id'], reagent_lot_id=fixture['first'], conclusion='fail',
            evidence='后续核对未通过', confirmed_by='另一确认人', confirmed_at='2026-09-03')
        before = database_state()
        app.button(key='reagent_history_save').click().run()
        assert not app.exception
        assert any('已修改' in error.value for error in app.error)
        assert app.text_area(key=key(app, 'reason')).value == '查原始工作记录确认继续使用 A 批。'
        assert app.selectbox(key=key(app, 'reagent_lot_id')).value == fixture['first']
        assert database_state() == before


def test_filters_selection_and_provenance_survive_page_return_and_save_selects_new_record():
    with IsolatedDatabase():
        fixture = seed()
        with get_connection() as connection:
            connection.execute('UPDATE qc_lot_change_events SET reason=? WHERE id=?', ('原事件专用筛选词', fixture['event']))
        app = make_app(fixture['event'])
        app.text_input(key='reagent_history_search').set_value('原事件专用筛选词')
        app.selectbox(key='reagent_history_system').set_value(fixture['source']['system_id'])
        app.selectbox(key='reagent_history_result_binding').set_value(fixture['binding']).run()
        app.button(key='leave_history').click().run()
        app.button(key='leave_history').click().run()
        assert not app.exception
        assert app.text_input(key='reagent_history_search').value == '原事件专用筛选词'
        assert app.selectbox(key='reagent_history_system').value == fixture['source']['system_id']
        assert app.selectbox(key='reagent_history_result_binding').value == fixture['binding']
        assert app.session_state['reagent_history_selected_event'] == fixture['event']
        open_dialog(app)
        fill(app, fixture)
        app.button(key='reagent_history_save').click().run()
        new_id = max(events())
        assert app.session_state['reagent_history_selected_event'] == new_id
        assert app.text_input(key='reagent_history_search').value == ''
        assert app.selectbox(key='reagent_history_system').value == fixture['source']['system_id']
        assert any(f'事件 {new_id}｜' in caption.value for caption in app.caption)


def test_stopped_historical_system_keeps_details_and_correction_targets_use_actual_ids():
    with IsolatedDatabase():
        fixture = seed()
        with get_connection() as connection:
            connection.execute("UPDATE qc_workbench_bindings SET binding_status='inactive'")
            connection.execute('UPDATE md_reagent_lots SET is_disabled=1 WHERE id=?', (fixture['second'],))
        before = database_state()
        app = open_dialog(make_app(fixture['event']))
        assert not app.exception
        assert '历史试剂-B' in ' '.join(element.value for element in app.markdown)
        choices = app.selectbox(key=key(app, 'reagent_lot_id'))
        assert any('历史试剂-A' in label for label in choices.options)
        assert not any('历史试剂-B' in label for label in choices.options)
        fill(app, fixture).run()
        assert database_state() == before
        app.button(key='reagent_history_save').click().run()
        assert not app.exception and len(events()) == 4


def test_future_event_and_actual_level_lots_display_without_changing_time_semantics():
    with IsolatedDatabase():
        fixture = seed()
        future_id = switch(fixture['source'], fixture['first'], fixture['first_verification'], '2028-01-01')
        with get_connection() as connection:
            binding = connection.execute('SELECT * FROM qc_workbench_bindings WHERE id=?', (fixture['binding'],)).fetchone()
            snapshot = json.loads(binding['source_snapshot_json'])
            snapshot['lot_no'] = '不得作为实际批号显示的锚点'
            snapshot['levels'] = [dict(row, lot_no=f'实际浓度批号-{index}') for index, row in enumerate(snapshot['levels'], 1)]
            # This display-only fixture simulates a saved mixed-lot historical snapshot.
            snapshot['levels'].append(dict(snapshot['levels'][0], lot_no='实际浓度批号-2'))
            connection.execute('UPDATE qc_workbench_bindings SET source_snapshot_json=? WHERE id=?',
                (json.dumps(snapshot), fixture['binding']))
        before = database_state()
        app = make_app(future_id)
        assert not app.exception
        frame = app.dataframe[0].value
        assert frame.loc[frame['事件编号'] == future_id, '时间状态'].iloc[0] == '尚未生效'
        assert any('尚未生效' in caption.value for caption in app.caption)
        labels = app.selectbox(key='reagent_history_result_binding').options
        assert any('实际浓度批号-1；实际浓度批号-2' in label for label in labels)
        assert not any('不得作为实际批号' in label for label in labels)
        assert database_state() == before


def test_fast_typing_then_cancel_preserves_draft_and_unchanged_cancel_closes_directly():
    with IsolatedDatabase():
        fixture = seed()
        before = database_state()
        app = open_dialog(make_app(fixture['event']))
        app.button(key='reagent_history_cancel').click().run()
        assert 'reagent_history_dialog' not in app.session_state
        open_dialog(app)
        # Both the latest text and the cancel click arrive in one rerun.
        app.text_area(key=key(app, 'reason')).set_value('快速输入后取消')
        app.button(key='reagent_history_cancel').click().run()
        app.button(key='reagent_history_continue').click().run()
        assert app.text_area(key=key(app, 'reason')).value == '快速输入后取消'
        assert database_state() == before


def test_new_dialog_uses_latest_verification_and_correction_can_be_appended_again():
    with IsolatedDatabase():
        fixture = seed()
        app = open_dialog(make_app(fixture['event']))
        fill(app, fixture)
        app.button(key='reagent_history_save').click().run()
        first_correction = max(events())
        app.session_state['reagent_history_selected_event'] = fixture['event']
        app.run()
        assert not app.button(key='reagent_history_open').disabled
        open_dialog(app)
        fill(app, fixture)
        app.button(key='reagent_history_save').click().run()
        second_correction = max(events())
        assert first_correction != second_correction
        assert events()[second_correction]['corrects_event_id'] == fixture['event']
        record_lot_verification(template_item_id=fixture['source']['project_template_item_id'],
            system_id=fixture['source']['system_id'], reagent_lot_id=fixture['first'], conclusion='fail',
            evidence='之后的验证不通过', confirmed_by='测试确认人', confirmed_at='2026-09-03')
        app.run()
        open_dialog(app)
        assert app.selectbox(key=key(app, 'verification_id')).value is None
        assert app.selectbox(key=key(app, 'verification_id')).options == ['请选择验证记录']
        app.button(key='reagent_history_cancel').click().run()
        assert 'reagent_history_dialog' not in app.session_state


def test_empty_effective_time_renders_rejects_save_and_allows_cancel_without_losing_inputs():
    from ui.reagent_history_workspace import _applicable_verifications, _effective_time
    with IsolatedDatabase():
        fixture = seed()
        app = open_dialog(make_app(fixture['event']))
        fill(app, fixture).run()
        before = database_state()
        # Exercise the actual widget's empty value; it must not silently become "now".
        app.get('date_time_input')[0].set_value(None).run()
        assert not app.exception
        assert any('请填写有效的生效时间' in info.value for info in app.info)
        assert app.text_input(key=key(app, 'operator')).value == '更正核对人'
        app.button(key='reagent_history_save').click().run()
        assert not app.exception and any('请填写有效的生效时间' in error.value for error in app.error)
        assert app.text_area(key=key(app, 'reason')).value == '查原始工作记录确认继续使用 A 批。'
        assert app.session_state['reagent_history_dialog']['draft']['effective_at'] is None
        app.button(key='reagent_history_cancel').click().run()
        app.button(key='reagent_history_continue').click().run()
        assert not app.exception
        assert app.text_input(key=key(app, 'operator')).value == '更正核对人'
        assert app.session_state['reagent_history_dialog']['draft']['effective_at'] is None
        context = app.session_state['reagent_history_dialog']['context']
        assert _applicable_verifications(context, fixture['first'], '无效时间') == {}
        assert _effective_time('无效时间') is None
        app.button(key='reagent_history_cancel').click().run()
        app.button(key='reagent_history_discard').click().run()
        assert not app.exception and 'reagent_history_dialog' not in app.session_state
        assert database_state() == before


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name)
