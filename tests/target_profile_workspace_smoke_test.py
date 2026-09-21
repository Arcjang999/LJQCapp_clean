"""Isolated parameter-version service and explicit dialog workflow checks."""
from datetime import datetime
from pathlib import Path
import json
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import add_result, get_connection, init_db
from services.lot_lifecycle_service import (
    create_level_combination, create_target_profile, record_lot_verification,
    set_qc_usage_state, source_context, target_profile,
)
from services.master_data_service import create_qc_level, create_qc_lot
from services.project_config_service import activate_lot_config, set_lot_config_disabled
from services.target_profile_edit_service import (
    get_target_profile_context, list_target_profile_batches, save_target_profile_settings,
)
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.instant_v12_integration_smoke_test import IsolatedDatabase, rejected
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.lot_lifecycle_smoke_test import lj
from tests.quality_review_fixtures import confirm_fixture_lot
from tests.zscore_v12_fixtures import seed_zscore_configuration


def fixture(method='lj', count=1):
    seeded = None
    if method == 'lj':
        batch = lj()
    else:
        seeded = seed_zscore_configuration(name=f'参数弹窗{count}水平项目', level_count=count)
        batch = seeded['batch_id']
    with get_connection() as connection:
        binding = dict(connection.execute('SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?', (method, batch)).fetchone())
    return binding, seeded


def parameters(context, offset=0):
    return [dict(level_id=row['level_id'], mean=100*(index+1)+offset, sd=2*(index+1))
            for index, row in enumerate(context['levels'])]


def save(context, when='2026-09-01', **overrides):
    arguments = dict(expected_fingerprint=context['fingerprint'], source='manual',
        evidence='临时数据：已逐项确认均值和标准差及检测系统适用性。', confirmed_by='参数验收人',
        effective_at=when, confirmed=True)
    arguments.update(overrides)
    return save_target_profile_settings(context['binding']['id'], parameters(context), **arguments)


def database_state():
    with get_connection() as connection:
        return tuple(connection.iterdump())


def result_state():
    with get_connection() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
            for table in ('results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
                          'qc_result_context_levels', 'qc_result_evaluations')}


def make_app(binding_id=None):
    app = AppTest.from_string('''
import streamlit as st
from ui.target_profile_workspace import render_target_profile_workspace, render_pending_target_profile_dialog
if 'show_targets' not in st.session_state:
    st.session_state.show_targets = True
if st.button('切换页面', key='leave_targets'):
    st.session_state.show_targets = not st.session_state.show_targets
if st.session_state.show_targets:
    render_target_profile_workspace()
    render_pending_target_profile_dialog()
''', default_timeout=30)
    if binding_id is not None:
        app.session_state['target_profile_selected_binding'] = binding_id
    return app.run()


def key(app, field):
    return 'target_profile_' + app.session_state['target_profile_dialog']['token'] + '_' + field


def open_dialog(app):
    app.button(key='target_profile_open').click().run()
    assert not app.exception
    return app


def fill(app, count=1, mean=100.0, sd=2.0):
    for order in range(1, count+1):
        app.number_input(key=key(app, f'{order}_mean')).set_value(mean*order)
        app.number_input(key=key(app, f'{order}_sd')).set_value(sd*order)
    app.text_area(key=key(app, 'evidence')).set_value('弹窗逐水平参数确认依据')
    app.text_input(key=key(app, 'confirmed_by')).set_value('弹窗确认人')
    app.checkbox(key=key(app, 'confirmed')).check()
    return app


def test_reading_contexts_is_read_only_and_lists_real_binding_ids():
    with IsolatedDatabase():
        single, _ = fixture()
        multi, _ = fixture('zscore', 3)
        instant = seed_instant_configuration(name='列表中排除即时法', instrument='即时法独立仪器')
        with get_connection() as connection:
            instant_binding = connection.execute('SELECT id FROM qc_workbench_bindings WHERE lot_config_item_id=?', (instant['item_id'],)).fetchone()[0]
        before = database_state()
        contexts = list_target_profile_batches()
        assert {context['binding']['id'] for context in contexts} == {single['id'], multi['id']}
        assert all(context['editable'] for context in contexts)
        first = get_target_profile_context(single['id'])
        assert get_target_profile_context(single['id'])['fingerprint'] == first['fingerprint']
        rejected(lambda: get_target_profile_context(instant_binding), '单水平或多水平')
        assert database_state() == before


def test_current_and_future_versions_prefill_current_and_preserve_saved_results():
    with IsolatedDatabase():
        binding, _ = fixture()
        context = get_target_profile_context(binding['id'])
        current_id = save(context)
        add_result(binding['runtime_batch_id'], '2026-09-02', 101, operator='参数回归')
        before = result_state()
        context = get_target_profile_context(binding['id'])
        future = create_target_profile(method='lj', batch_id=binding['runtime_batch_id'],
            levels=[dict(level_id='Level 1', mean=200, sd=4)], source='revision',
            evidence='未来参数计划', confirmed_by='未来确认人', effective_at='2099-01-01')
        assert result_state() == before
        context = get_target_profile_context(binding['id'])
        assert context['current']['id'] == current_id
        assert context['levels'][0]['mean'] == 100 and context['levels'][0]['sd'] == 2
        assert [profile['id'] for profile in context['profiles']] == [current_id, future]
        assert context['initial_time'] > datetime(2099, 1, 1)
        app = make_app(binding['id'])
        assert not app.exception
        assert any('尚未生效' in str(frame.value) for frame in app.dataframe)
        open_dialog(app)
        assert app.number_input(key=key(app, '1_mean')).value == 100
        assert app.number_input(key=key(app, '1_sd')).value == 2
        assert result_state() == before


def test_stale_profiles_config_and_level_contexts_reject_atomically():
    for change in ('profile', 'config', 'levels'):
        with IsolatedDatabase():
            binding, _ = fixture()
            context = get_target_profile_context(binding['id'])
            if change == 'profile':
                save(context)
            else:
                with get_connection() as connection:
                    if change == 'config':
                        connection.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1 WHERE id=?', (binding['lot_config_id'],))
                    else:
                        connection.execute("UPDATE qc_lot_config_item_levels SET notes='另一窗口修订' WHERE lot_config_item_id=?", (binding['lot_config_item_id'],))
            before = database_state()
            rejected(lambda: save(context, '2026-09-03'), '已修改')
            assert database_state() == before


def test_invalid_parameters_and_late_failure_leave_no_partial_version_or_event():
    with IsolatedDatabase():
        binding, _ = fixture('zscore', 2)
        context = get_target_profile_context(binding['id'])
        before = database_state()
        rejected(lambda: save(context, confirmed=False), '先确认')
        rejected(lambda: save_target_profile_settings(binding['id'], [dict(level_id='Level 1', mean=100, sd=2)],
            expected_fingerprint=context['fingerprint'], source='manual', evidence='完整水平校验',
            confirmed_by='回归', effective_at='2026-09-01', confirmed=True), '全部水平')
        rejected(lambda: save_target_profile_settings(binding['id'], [dict(level_id='Level 1', mean=None, sd=2)],
            expected_fingerprint=context['fingerprint'], source='manual', evidence='空均值校验',
            confirmed_by='回归', effective_at='2026-09-01', confirmed=True), '填写全部')
        assert database_state() == before
        def fail_after_write(**kwargs):
            create_target_profile(**kwargs)
            raise ValueError('模拟保存中断')
        with patch('services.target_profile_edit_service.create_target_profile', side_effect=fail_after_write):
            rejected(lambda: save(context), '模拟保存中断')
        assert database_state() == before


def test_ended_inactive_and_restored_history_remain_read_only():
    for state in ('ended', 'inactive', 'restored'):
        with IsolatedDatabase():
            binding, _ = fixture()
            context = get_target_profile_context(binding['id'])
            saved = save(context)
            if state == 'ended':
                set_qc_usage_state(lot_config_item_id=binding['lot_config_item_id'], state='ended',
                    effective_at='2026-09-02', operator='回归', reason='停止使用')
            elif state == 'inactive':
                with get_connection() as connection:
                    connection.execute("UPDATE qc_workbench_bindings SET binding_status='inactive' WHERE id=?", (binding['id'],))
            else:
                set_lot_config_disabled(binding['lot_config_id'], is_disabled=True, reason='停用验收')
                set_lot_config_disabled(binding['lot_config_id'], is_disabled=False)
            context = get_target_profile_context(binding['id'])
            assert not context['editable'] and context['current']['id'] == saved
            before = database_state()
            rejected(lambda: save(context, '2026-09-03'), context['read_only_reason'])
            app = make_app(binding['id'])
            assert not app.exception and app.button(key='target_profile_open').disabled
            assert database_state() == before


def test_retained_levels_prefill_original_profile_and_current_profile_takes_precedence():
    with IsolatedDatabase():
        binding, original = fixture('zscore', 3)
        current = get_target_profile_context(binding['id'])
        save(current)
        with get_connection() as connection:
            source, _, _ = source_context(connection, 'zscore', binding['runtime_batch_id'])
        lot = create_qc_lot(qc_material_id=source['identity'][1], lot_no='PROFILE-HIGH-NEXT', expiry_date='2028-12-31')
        level = create_qc_level(qc_material_lot_id=lot, level_name='新高值', level_order=3)
        verification = record_lot_verification(template_item_id=source['project_template_item_id'], system_id=source['system_id'],
            qc_lot_id=lot, conclusion='pass', evidence='高值批间平行通过', confirmed_by='回归', confirmed_at='2026-09-02')
        new = create_level_combination(source_batch_id=binding['runtime_batch_id'],
            level_ids=original['level_ids'][:2]+[level], verification_ids={level: verification},
            operator='回归', reason='只换高值', effective_at='2026-09-03')
        confirm_fixture_lot(new)
        activate_lot_config(new)
        sync_zscore_workbench_bindings()
        with get_connection() as connection:
            new_binding = connection.execute('SELECT id FROM qc_workbench_bindings WHERE lot_config_id=?', (new,)).fetchone()[0]
        context = get_target_profile_context(new_binding)
        assert [row['retained_version'] for row in context['levels']] == [1, 1, None]
        assert [row['mean'] for row in context['levels'][:2]] == [100, 200]
        assert context['levels'][2]['qc_level_id'] == level and context['levels'][2]['mean'] is None
        app = open_dialog(make_app(new_binding))
        assert app.number_input(key=key(app, '1_mean')).value == 100
        assert app.number_input(key=key(app, '2_mean')).value == 200
        assert app.number_input(key=key(app, '3_mean')).value is None
        fill(app, count=3, mean=111, sd=3)
        app.button(key='target_profile_save').click().run()
        assert not app.exception and 'target_profile_dialog' not in app.session_state.filtered_state
        after = get_target_profile_context(new_binding)
        assert [row['mean'] for row in after['levels']] == [111, 222, 333]
        assert all(row['retained_version'] is None for row in after['levels'])


def test_dialog_typing_cancel_failure_and_save_preserve_explicit_workflow():
    with IsolatedDatabase():
        binding, _ = fixture()
        app = open_dialog(make_app(binding['id']))
        before = database_state()
        fill(app, mean=123.45, sd=3.25).run()
        assert not app.exception and database_state() == before
        app.checkbox(key=key(app, 'confirmed')).uncheck()
        app.button(key='target_profile_save').click().run()
        assert app.error and database_state() == before
        app.button(key='target_profile_cancel').click().run()
        assert database_state() == before
        app.button(key='target_profile_continue').click().run()
        assert app.number_input(key=key(app, '1_mean')).value == 123.45
        assert app.text_area(key=key(app, 'evidence')).value == '弹窗逐水平参数确认依据'
        app.checkbox(key=key(app, 'confirmed')).check()
        app.button(key='target_profile_save').click().run()
        assert not app.exception and 'target_profile_dialog' not in app.session_state.filtered_state
        assert app.session_state['target_profile_selected_binding'] == binding['id']
        context = get_target_profile_context(binding['id'])
        assert context['current']['levels'][0]['mean'] == 123.45
        assert app.session_state[f"target_profile_selected_version_{binding['id']}"] == context['current']['id']
        open_dialog(app)
        fill(app, mean=555)
        app.button(key='target_profile_cancel').click().run()
        app.button(key='target_profile_discard').click().run()
        open_dialog(app)
        assert app.number_input(key=key(app, '1_mean')).value == 123.45


def test_search_selection_return_and_multiple_dialogs_keep_separate_drafts():
    with IsolatedDatabase():
        first, _ = fixture()
        second, _ = fixture('zscore', 2)
        app = make_app(first['id'])
        first_context = get_target_profile_context(first['id'])
        app.selectbox(key='target_profile_project').set_value(first_context['template']['id'])
        app.text_input(key='target_profile_search').set_value(first_context['levels'][0]['lot_no']).run()
        app.button(key='leave_targets').click().run()
        app.button(key='leave_targets').click().run()
        assert not app.exception
        assert app.text_input(key='target_profile_search').value == first_context['levels'][0]['lot_no']
        assert app.session_state['target_profile_selected_binding'] == first['id']
        open_dialog(app)
        first_token = app.session_state['target_profile_dialog']['token']
        fill(app, mean=765)
        app.button(key='target_profile_cancel').click().run()
        app.button(key='target_profile_discard').click().run()
        app.selectbox(key='target_profile_project').set_value(None)
        app.text_input(key='target_profile_search').set_value('').run()
        app.session_state['target_profile_selected_binding'] = second['id']
        app.run()
        open_dialog(app)
        assert app.session_state['target_profile_dialog']['token'] != first_token
        assert app.number_input(key=key(app, '1_mean')).value != 765
        app.button(key='target_profile_cancel').click().run()
        app.text_input(key='target_profile_search').set_value('没有这个批次').run()
        assert not app.exception and app.session_state['target_profile_selected_binding'] is None
        assert not any(button.key == 'target_profile_open' for button in app.button)


def test_dialog_open_before_new_profile_rejects_stale_save_without_losing_inputs():
    with IsolatedDatabase():
        binding, _ = fixture()
        app = open_dialog(make_app(binding['id']))
        fill(app, mean=456)
        save(get_target_profile_context(binding['id']))
        before = database_state()
        app.button(key='target_profile_save').click().run()
        assert not app.exception and app.error and database_state() == before
        assert app.number_input(key=key(app, '1_mean')).value == 456
        assert any('已修改' in error.value for error in app.error)


def test_real_lifecycle_page_sync_keeps_parameter_fingerprint_stable():
    with IsolatedDatabase():
        single, _ = fixture()
        multi, _ = fixture('zscore', 2)
        with get_connection() as connection:
            connection.execute("UPDATE qc_workbench_bindings SET updated_at='2000-01-01 00:00:00'")
        before = {binding['id']: get_target_profile_context(binding['id'])['fingerprint']
                  for binding in (single, multi)}
        app = AppTest.from_string('from pages.lot_lifecycle_section import render_lot_management\nrender_lot_management()',
                                 default_timeout=30).run()
        assert not app.exception
        for binding in (single, multi):
            context = get_target_profile_context(binding['id'])
            assert context['binding']['updated_at'] != '2000-01-01 00:00:00'
            assert context['fingerprint'] == before[binding['id']]


def test_official_item_reseeding_keeps_open_parameter_context_valid_but_notes_changes_do_not():
    from services.master_data_edit_service import get_master_record_context, save_master_record
    with IsolatedDatabase():
        with get_connection() as connection:
            identifier = connection.execute("SELECT id FROM md_test_items WHERE origin_type='official' ORDER BY id LIMIT 1").fetchone()[0]
            connection.execute("UPDATE md_test_items SET updated_at='2000-01-01 00:00:00' WHERE id=?", (identifier,))
        with patch('tests.lj_v12_integration_smoke_test.create_test_item', return_value=identifier):
            binding, _ = fixture()
        opened = get_target_profile_context(binding['id'])
        original = get_master_record_context('test_item', identifier)['record']
        init_db()
        init_db()
        refreshed = get_target_profile_context(binding['id'])
        source = get_master_record_context('test_item', identifier)['record']
        assert {field for field in source if source[field] != original[field]} == {'updated_at'}
        assert refreshed['fingerprint'] == opened['fingerprint']
        profile = save(opened)
        assert get_target_profile_context(binding['id'])['current']['id'] == profile
        stale = get_target_profile_context(binding['id'])
        record = get_master_record_context('test_item', identifier)
        save_master_record('test_item', {'notes': '另一窗口补充的业务依据'}, entity_id=identifier,
            expected_fingerprint=record['fingerprint'])
        before = database_state()
        rejected(lambda: save(stale, when='2026-09-02'), '已修改')
        assert database_state() == before


def test_parameter_dialog_with_real_init_on_each_rerun_saves_official_item():
    with IsolatedDatabase():
        with get_connection() as connection:
            identifier = connection.execute("SELECT id FROM md_test_items WHERE origin_type='official' ORDER BY id LIMIT 1").fetchone()[0]
        with patch('tests.lj_v12_integration_smoke_test.create_test_item', return_value=identifier):
            binding, _ = fixture()
        app = AppTest.from_string('''
from database import init_db
from ui.target_profile_workspace import render_target_profile_workspace, render_pending_target_profile_dialog
init_db()
render_target_profile_workspace()
render_pending_target_profile_dialog()
''', default_timeout=30)
        app.session_state['target_profile_selected_binding'] = binding['id']
        app.run()
        open_dialog(app)
        fill(app)
        # Ensure reseeding changes the timestamp even if this test completes within one second.
        with get_connection() as connection:
            connection.execute("UPDATE md_test_items SET updated_at='2000-01-01 00:00:00' WHERE id=?", (identifier,))
        app.button(key='target_profile_save').click().run()
        assert not app.exception and not app.error
        assert 'target_profile_dialog' not in app.session_state
        assert get_target_profile_context(binding['id'])['current']['levels'][0]['mean'] == 100


def test_retained_profile_uses_original_material_position_after_reordering():
    from services.material_workflow_service import copy_material_config, register_control_material
    from services.project_config_service import list_lot_config_items, save_lot_item_levels
    with IsolatedDatabase():
        binding, original = fixture('zscore', 3)
        source = get_target_profile_context(binding['id'])
        profile = save(source)
        next_level = register_control_material(material_id=source['config']['qc_material_id'],
            level_name='中值', level_code='02', lot_no='REORDER-MID', expiry_date='2029-01-01')
        selected = [original['level_ids'][2], next_level, original['level_ids'][0]]
        new = copy_material_config(source_config_id=binding['lot_config_id'], selections={binding['lot_config_item_id']: selected})
        item = int(list_lot_config_items(new).iloc[0]['id'])
        save_lot_item_levels(item, [dict(qc_level_id=identifier, target_source='building',
            target_mean=77, target_sd=7) for identifier in selected])
        with get_connection() as connection:
            for identifier in (selected[0], selected[2]):
                connection.execute('INSERT INTO qc_level_combination_members(lot_config_item_id,qc_level_id,source_profile_id) VALUES(?,?,?)',
                                   (item, identifier, profile))
        confirm_fixture_lot(new)
        activate_lot_config(new)
        sync_zscore_workbench_bindings()
        with get_connection() as connection:
            new_binding = connection.execute('SELECT id FROM qc_workbench_bindings WHERE lot_config_id=?', (new,)).fetchone()[0]
        context = get_target_profile_context(new_binding)
        assert [row['mean'] for row in context['levels']] == [300, 77, 100]
        assert [row['sd'] for row in context['levels']] == [6, 7, 2]
        # A missing source mapping must use copied reference values, not guess by new position.
        with get_connection() as connection:
            payload = json.loads(binding['source_snapshot_json'])
            payload['levels'] = [row for row in payload['levels'] if row['qc_level_id'] != selected[0]]
            connection.execute('UPDATE qc_workbench_bindings SET source_snapshot_json=? WHERE id=?',
                               (json.dumps(payload), binding['id']))
        assert get_target_profile_context(new_binding)['levels'][0]['mean'] == 77


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
