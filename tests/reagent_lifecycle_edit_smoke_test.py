"""Reagent dialog operations keep lot history, result snapshots and atomicity."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import add_result, get_connection, init_db
from services.instant_service import save_instant_result
from services.lot_lifecycle_service import result_lot_options, set_qc_usage_state, workbench_systems
from services.master_data_service import create_reagent, set_master_entity_disabled
from services.reagent_lifecycle_edit_service import (
    build_reagent_switch_preview, get_reagent_correction_context, get_reagent_workspace_context,
    save_reagent_correction, save_reagent_registration, save_reagent_switch, save_reagent_verification,
)
from tests.material_workflow_smoke_test import mixed_fixture
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext
from zscore_logic import create_zscore_run


def dump():
    with get_connection() as c:
        return '\n'.join(c.iterdump())


def rejected(action, message):
    before = dump()
    try:
        action()
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError('Expected ValueError: ' + message)
    assert dump() == before, 'Rejected logical operation must not partly write any table'


def fixture():
    data = mixed_fixture()
    workbench_systems()
    context = get_reagent_workspace_context()
    methods = {binding['project_template_item_id']: method for method, binding in data['bindings'].items()}
    data['systems'] = {methods[system['template_item_id']]: system for system in context['systems']}
    assert set(data['systems']) == {'lj', 'zscore', 'instant'}
    assert all(system['available'] for system in data['systems'].values())
    return data


def register(data, name, expiry='2028-12-31', reagent_id=None):
    context = get_reagent_workspace_context()
    return save_reagent_registration(dict(reagent_id=reagent_id or data['data']['reagent_id'],
        lot_no=name, expiry_date=expiry, source_text='厂家资料\n实验室登记'), expected_fingerprint=context['fingerprint'])


def verification(system, lot_id, conclusion='pass', when='2026-09-01'):
    context = get_reagent_workspace_context()
    return save_reagent_verification(dict(template_item_id=system['template_item_id'], system_id=system['id'],
        reagent_lot_id=lot_id, conclusion=conclusion, evidence='验证依据\n逐项核对', confirmed_by='确认人员', confirmed_at=when),
        expected_fingerprint=context['fingerprint'])


def switch_values(context, lot_id, system_ids, when='2026-09-04'):
    return dict(selections=build_reagent_switch_preview(context, reagent_lot_id=lot_id, system_ids=system_ids, effective_at=when),
        effective_at=when, operator='换批人员', reason='逐项验证通过，原均值和标准差仍适用', confirmed=True)


def switch(lot_id, system_ids, when='2026-09-04'):
    context = get_reagent_workspace_context()
    return save_reagent_switch(switch_values(context, lot_id, system_ids, when), expected_fingerprint=context['fingerprint'])


def frozen_rows():
    tables = ('results', 'instant_results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
              'qc_result_context_levels', 'qc_result_evaluations', 'qc_config_snapshots', 'qc_target_profiles')
    with get_connection() as c:
        return {table: [tuple(row) for row in c.execute(f'SELECT * FROM {table} ORDER BY id')] for table in tables}


def test_context_is_read_only_and_sync_clock_not_stale():
    with TemporaryDatabaseContext():
        data = fixture()
        before = dump()
        context = get_reagent_workspace_context()
        assert context['all_bindings'] and len(context['systems']) == 3
        assert all('snapshot' in row and 'label' in row for row in context['systems'])
        assert '多水平法' in data['systems']['zscore']['label']
        assert dump() == before
        with get_connection() as c:
            c.execute("UPDATE qc_workbench_bindings SET updated_at='2099-01-01'")
        assert get_reagent_workspace_context()['fingerprint'] == context['fingerprint']
        set_qc_usage_state(lot_config_item_id=data['bindings']['lj']['lot_config_item_id'], state='parallel',
            effective_at='2026-09-02', operator='确认人员', reason='保留所有类型历史')
        current = get_reagent_workspace_context()
        assert any(event['event_type'] == 'parallel' for event in current['history_events'])
        assert not current['events']


def test_registration_validation_duplicate_and_stale_are_atomic():
    with TemporaryDatabaseContext():
        data = fixture()
        context = get_reagent_workspace_context()
        values = dict(reagent_id=data['data']['reagent_id'], lot_no='R-FIRST', expiry_date=None)
        rejected(lambda: save_reagent_registration(values, expected_fingerprint=context['fingerprint']), '效期')
        values['expiry_date'] = 'not-a-date'
        rejected(lambda: save_reagent_registration(values, expected_fingerprint=context['fingerprint']), '有效')
        lot = register(data, 'R-FIRST')
        rejected(lambda: save_reagent_registration(dict(reagent_id=data['data']['reagent_id'], lot_no='R-STALE', expiry_date='2028-12-31'),
            expected_fingerprint=context['fingerprint']), '已修改')
        rejected(lambda: register(data, 'r-first'), '已存在')
        with get_connection() as c:
            assert c.execute('SELECT source_text FROM md_reagent_lots WHERE id=?', (lot,)).fetchone()[0] == '厂家资料\n实验室登记'
        # Expired lots can still be registered as factual records; use-time rules decide applicability.
        register(data, 'R-HISTORICAL', expiry='2020-01-01')


def test_seed_timestamp_refresh_allows_save_but_business_edits_stay_stale():
    with TemporaryDatabaseContext():
        data = fixture()
        with get_connection() as c:
            c.execute("UPDATE md_test_items SET updated_at='2000-01-01 00:00:00' WHERE origin_type='official'")
        opened = get_reagent_workspace_context()
        init_db()
        with get_connection() as c:
            assert c.execute("SELECT COUNT(*) FROM md_test_items WHERE origin_type='official' AND updated_at='2000-01-01 00:00:00'").fetchone()[0] == 0
        assert get_reagent_workspace_context()['fingerprint'] == opened['fingerprint']
        save_reagent_registration(dict(reagent_id=data['data']['reagent_id'], lot_no='SEED-REFRESH-OK', expiry_date='2028-12-31'),
            expected_fingerprint=opened['fingerprint'])
        # Even without a timestamp change, a real notes edit must invalidate the guard.
        opened = get_reagent_workspace_context()
        with get_connection() as c:
            c.execute('UPDATE md_test_items SET notes=? WHERE id=?', ('另一个窗口已补充项目备注', data['data']['lj_item_id']))
        rejected(lambda: save_reagent_registration(dict(reagent_id=data['data']['reagent_id'], lot_no='BUSINESS-STALE', expiry_date='2028-12-31'),
            expected_fingerprint=opened['fingerprint']), '已修改')


def test_verification_product_and_disabled_identity_are_rejected():
    with TemporaryDatabaseContext():
        data = fixture()
        other = create_reagent(generic_name='另一试剂产品')
        wrong_lot = register(data, 'R-OTHER', reagent_id=other)
        system = data['systems']['lj']
        rejected(lambda: verification(system, wrong_lot), '不属于')
        lot = register(data, 'R-CURRENT')
        with get_connection() as c:
            c.execute('UPDATE md_reagent_lots SET is_disabled=1 WHERE id=?', (lot,))
        rejected(lambda: verification(system, lot), '已停用')
        with get_connection() as c:
            c.execute('UPDATE md_reagent_lots SET is_disabled=0 WHERE id=?', (lot,))
        set_master_entity_disabled('method', data['data']['method_id'], is_disabled=True, reason='校验停用')
        current = get_reagent_workspace_context()
        assert not next(row for row in current['systems'] if row['id'] == system['id'])['available']
        rejected(lambda: verification(system, lot), '停用或变更')
        set_master_entity_disabled('method', data['data']['method_id'], is_disabled=False)
        verification(system, lot)


def test_multi_system_switch_failure_rolls_back_every_selection():
    with TemporaryDatabaseContext():
        data = fixture()
        lot = register(data, 'R-MULTI')
        first, second = data['systems']['lj'], data['systems']['zscore']
        verification(first, lot)
        verification(second, lot, 'fail')
        context = get_reagent_workspace_context()
        values = switch_values(context, lot, [first['id'], second['id']])
        assert values['selections'][0]['conclusion'] == 'pass' and values['selections'][1]['conclusion'] == 'fail'
        rejected(lambda: save_reagent_switch(values, expected_fingerprint=context['fingerprint']), '已通过的验证')
        verification(second, lot, 'pass', when='2026-09-02')
        context = get_reagent_workspace_context()
        values = switch_values(context, lot, [first['id'], second['id']])
        values['selections'][1]['expected_revision'] = -1
        rejected(lambda: save_reagent_switch(values, expected_fingerprint=context['fingerprint']), '使用记录已修改')
        values = switch_values(context, lot, [first['id'], second['id']])
        values['confirmed'] = False
        rejected(lambda: save_reagent_switch(values, expected_fingerprint=context['fingerprint']), '请先核对')
        values['confirmed'] = True
        event_ids = save_reagent_switch(values, expected_fingerprint=context['fingerprint'])
        assert len(event_ids) == 2
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_reagent_lot_usage').fetchone()[0] == 2
            assert c.execute("SELECT COUNT(*) FROM qc_lot_change_events WHERE event_type='reagent'").fetchone()[0] == 2
            assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_latest_verification_and_dates_preserve_time_semantics():
    with TemporaryDatabaseContext():
        data = fixture()
        system = data['systems']['lj']
        lot = register(data, 'R-TIME')
        old_pass = verification(system, lot, 'pass', '2026-09-02')
        latest_fail = verification(system, lot, 'fail', '2026-09-10')
        context = get_reagent_workspace_context()
        earlier = switch_values(context, lot, [system['id']], '2026-09-03')
        assert earlier['selections'][0]['verification_id'] == old_pass
        save_reagent_switch(earlier, expected_fingerprint=context['fingerprint'])
        context = get_reagent_workspace_context()
        later = switch_values(context, lot, [system['id']], '2026-09-11')
        assert later['selections'][0]['verification_id'] == latest_fail
        later['selections'][0]['verification_id'] = old_pass
        rejected(lambda: save_reagent_switch(later, expected_fingerprint=context['fingerprint']), '后续结论')
        too_early = switch_values(context, lot, [system['id']], '2026-09-01')
        rejected(lambda: save_reagent_switch(too_early, expected_fingerprint=context['fingerprint']), '已通过的验证')
        expiry_lot = register(data, 'R-EXPIRY', expiry='2026-09-02')
        verification(system, expiry_lot, when='2026-09-01')
        rejected(lambda: switch(expiry_lot, [system['id']], when='2026-09-03'), '晚于效期')


def test_future_switch_keeps_current_choice_and_all_historical_results():
    with TemporaryDatabaseContext():
        data = fixture()
        old = register(data, 'R-OLD', expiry='2100-12-31')
        new = register(data, 'R-FUTURE', expiry='2100-12-31')
        systems = list(data['systems'].values())
        for system in systems:
            verification(system, old)
            verification(system, new)
        switch(old, [system['id'] for system in systems], '2026-09-02')
        add_result(data['bindings']['lj']['runtime_batch_id'], '2026-09-03 08:00', 100, operator='检测人', lot_selection={'reagent_lot_id': old})
        save_instant_result(batch_id=data['bindings']['instant']['runtime_batch_id'], test_time='2026-09-03 08:00',
            value=100, log_value=None, operator='检测人', lot_selection={'reagent_lot_id': old})
        create_zscore_run(batch_id=data['bindings']['zscore']['runtime_batch_id'], test_time='2026-09-03 08:00',
            operator='检测人', template_id='3_level_threes', level_results=[dict(level_id=f'Level {i+1}', raw_value=100*(i+1)) for i in range(3)],
            lot_selection={'reagent_lot_id': old})
        before = frozen_rows()
        switch(new, [system['id'] for system in systems], '2099-01-01')
        assert frozen_rows() == before
        for method, binding in data['bindings'].items():
            assert result_lot_options(method, binding['runtime_batch_id'], '2026-09-20')['suggested_lot_id'] == old
            assert result_lot_options(method, binding['runtime_batch_id'], '2099-01-01')['suggested_lot_id'] == new


def test_correction_stale_then_reopen_allows_append_without_rewriting():
    with TemporaryDatabaseContext():
        data = fixture()
        system = data['systems']['lj']
        old = register(data, 'R-A')
        new = register(data, 'R-B')
        old_v = verification(system, old)
        new_v = verification(system, new)
        event = switch(old, [system['id']], '2026-09-02')[0]
        add_result(data['bindings']['lj']['runtime_batch_id'], '2026-09-03', 100, operator='检测人', lot_selection={'reagent_lot_id': old})
        before = frozen_rows()
        context = get_reagent_correction_context(event)
        original = dict(context['event'])
        values = dict(reagent_lot_id=new, verification_id=new_v, effective_at='2026-09-04',
            operator='更正人', reason='实际试剂批号核对', confirmed=True)
        denied = {**values, 'confirmed': False}
        rejected(lambda: save_reagent_correction(event, denied, expected_fingerprint=context['fingerprint']), '请先核对')
        appended = save_reagent_correction(event, values, expected_fingerprint=context['fingerprint'])
        assert frozen_rows() == before
        rejected(lambda: save_reagent_correction(event, values, expected_fingerprint=context['fingerprint']), '已修改')
        reopened = get_reagent_correction_context(event)
        assert reopened['can_correct'] and reopened['has_corrections']
        again = save_reagent_correction(event, {**values, 'reagent_lot_id': old, 'verification_id': old_v, 'effective_at': '2026-09-05'},
            expected_fingerprint=reopened['fingerprint'])
        assert frozen_rows() == before
        with get_connection() as c:
            assert dict(c.execute('SELECT * FROM qc_lot_change_events WHERE id=?', (event,)).fetchone()) == original
            for identifier in (appended, again):
                correction = dict(c.execute('SELECT * FROM qc_lot_change_events WHERE id=?', (identifier,)).fetchone())
                assert correction['event_type'] == 'correction' and correction['corrects_event_id'] == event


def test_correction_rejects_cross_product_and_stale_verification():
    with TemporaryDatabaseContext():
        data = fixture()
        system = data['systems']['lj']
        lot = register(data, 'R-CORRECTION')
        passed = verification(system, lot)
        event = switch(lot, [system['id']], '2026-09-02')[0]
        verification(system, lot, 'fail', '2026-09-03')
        context = get_reagent_correction_context(event)
        values = dict(reagent_lot_id=lot, verification_id=passed, effective_at='2026-09-04', operator='更正人', reason='不能绕过失败验证', confirmed=True)
        rejected(lambda: save_reagent_correction(event, values, expected_fingerprint=context['fingerprint']), '后续结论')
        other = create_reagent(generic_name='更正不可跨用试剂')
        foreign = register(data, 'R-FOREIGN', reagent_id=other)
        context = get_reagent_correction_context(event)
        rejected(lambda: save_reagent_correction(event, {**values, 'reagent_lot_id': foreign}, expected_fingerprint=context['fingerprint']), '不属于')


if __name__ == '__main__':
    tests = [test_context_is_read_only_and_sync_clock_not_stale, test_registration_validation_duplicate_and_stale_are_atomic,
        test_seed_timestamp_refresh_allows_save_but_business_edits_stay_stale,
        test_verification_product_and_disabled_identity_are_rejected, test_multi_system_switch_failure_rolls_back_every_selection,
        test_latest_verification_and_dates_preserve_time_semantics, test_future_switch_keeps_current_choice_and_all_historical_results,
        test_correction_stale_then_reopen_allows_append_without_rewriting, test_correction_rejects_cross_product_and_stale_verification]
    for test in tests:
        test()
        print('PASS ' + test.__name__)
    print(f'All {len(tests)} reagent lifecycle edit smoke tests passed.')
