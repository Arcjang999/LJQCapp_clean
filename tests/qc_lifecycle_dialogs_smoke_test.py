"""Disposable-db regressions for QC lot dialog operations and actual material identity."""
from datetime import datetime
import json
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.lot_lifecycle_service import record_lot_verification, workbench_systems
from services.material_workflow_service import register_control_material
from services.project_config_service import (activate_lot_config, activate_project_template, create_lot_config_from_template,
    create_project_template, list_lot_config_items, save_lot_item_levels, save_template_items)
from services.qc_lifecycle_edit_service import (
    get_qc_binding_context, get_qc_config_context, parallel_target, replacement_materials,
    save_qc_lifecycle_action,
)
from tests.material_workflow_smoke_test import mixed_fixture
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project


def dump():
    with get_connection() as c:
        return '\n'.join(c.iterdump())


def rejected(action, text):
    before = dump()
    try:
        action()
    except ValueError as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError('Expected rejection')
    assert dump() == before


def fixture():
    f = mixed_fixture()
    workbench_systems()
    return f


def legacy_fixture():
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    data = _seed_v11_configuration_dependencies()
    template = create_project_template(template_name='同批号多检验项目', lab_instrument_id=data['lab_instrument_id'],
        qc_material_id=data['qc_material_id'], default_reagent_id=data['reagent_id'])
    save_template_items(template, [dict(test_item_id=test, qc_method=method, input_value_type='raw',
        unit_id=data['unit_id'], method_id=data['method_id'], reagent_id=data['reagent_id'], level_count=1, target_n=20)
        for test, method in [(data['lj_item_id'], 'lj'), (data['zscore_item_id'], 'instant')]])
    confirm_fixture_project(template)
    activate_project_template(template)
    config = create_lot_config_from_template(template_id=template, qc_material_lot_id=data['source_lot_id'])
    for item in list_lot_config_items(config).to_dict('records'):
        save_lot_item_levels(item['id'], [dict(qc_level_id=data['source_levels'][0], target_source='building')])
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings()
    sync_instant_workbench_bindings()
    workbench_systems()
    with get_connection() as c:
        bindings = {row['qc_method']: dict(row) for row in c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (config,))}
    return dict(data=data, config_id=config, template_id=template, bindings=bindings)


def verify(context, lot_id, when='2026-09-01', conclusion='pass'):
    return record_lot_verification(template_item_id=context['source']['project_template_item_id'],
        system_id=context['source']['system_id'], qc_lot_id=lot_id, conclusion=conclusion,
        evidence='逐批验证依据\n结果符合要求', confirmed_by='测试人员', confirmed_at=when)


def page():
    import streamlit as st
    from ui.qc_lifecycle_workspace import render_qc_lifecycle_workspace, render_pending_qc_lifecycle_dialog
    from ui.qc_replacement_workspace import render_pending_qc_replacement_dialog
    render_qc_lifecycle_workspace()
    if st.session_state.get('qc_lifecycle_dialog'):
        render_pending_qc_lifecycle_dialog()
    else:
        render_pending_qc_replacement_dialog()


def navigable_page():
    import streamlit as st
    from ui.qc_lifecycle_workspace import render_qc_lifecycle_workspace, render_pending_qc_lifecycle_dialog
    if st.toggle('显示质控品页面', value=True, key='qcl_test_show'):
        render_qc_lifecycle_workspace()
        render_pending_qc_lifecycle_dialog()


def integrated_page():
    from pages.lot_lifecycle_section import render_lot_management
    render_lot_management()


def start(f, method='zscore'):
    at = AppTest.from_function(page, default_timeout=15)
    at.session_state['qc_lifecycle_config_id'] = f['config_id']
    at.session_state['qc_lifecycle_binding_id'] = f['bindings'][method]['id']
    at.run()
    clean(at)
    return at


def clean(at):
    assert not list(at.exception), [str(e) for e in at.exception]


def prefix(at):
    return 'qcl_' + at.session_state['qc_lifecycle_dialog']['token'] + '_'


def test_context_reads_and_material_choices_keep_actual_identity():
    with TemporaryDatabaseContext():
        f = fixture()
        new_high = register_control_material(material_id=f['data']['qc_material_id'], level_name='高值',
            level_code='003', lot_no='NEXT-HIGH', expiry_date='2028-12-31')
        before = dump()
        context = get_qc_binding_context(f['bindings']['zscore']['id'])
        choices = replacement_materials(context, 2)
        assert new_high in [row['id'] for row in choices]
        assert next(row['level_order'] for row in choices if row['id'] == new_high) == 1
        assert len(context['actual_lots']) == 3
        assert set(lot['lot_no'] for lot in context['actual_lots'].values()) == {'ACTUAL-LOW', 'ACTUAL-MID', 'ACTUAL-HIGH'}
        assert get_qc_binding_context(f['bindings']['lj']['id'])['actual_lots'].keys() != {context['config']['qc_material_lot_id']}
        get_qc_config_context(f['config_id'])
        assert dump() == before, 'Reading lists/details cannot write provenance'


def test_verification_dialog_failure_cancel_and_fresh_tokens():
    with TemporaryDatabaseContext():
        f = fixture()
        at = start(f)
        at.button(key='qc_lifecycle_verify').click().run()
        p = prefix(at)
        context = at.session_state['qc_lifecycle_dialog']['context']
        lot_id = next(iter(context['actual_lots']))
        before = dump()
        at.selectbox(key=p+'lot').set_value(lot_id).run()
        at.selectbox(key=p+'conclusion').set_value('pass').run()
        at.text_area(key=p+'evidence').set_value('验证记录第一行\n第二行').run()
        assert dump() == before
        at.button(key='qcl_save').click().run()
        assert at.error and at.text_area(key=p+'evidence').value == '验证记录第一行\n第二行'
        assert dump() == before
        at.text_input(key=p+'person').set_value('确认人员').run()
        at.button(key='qcl_cancel').click().run()
        assert dump() == before
        at.button(key='qcl_continue').click().run()
        assert at.text_input(key=p+'person').value == '确认人员'
        at.button(key='qcl_save').click().run()
        clean(at)
        assert 'qc_lifecycle_dialog' not in at.session_state
        with get_connection() as c:
            row = c.execute('SELECT * FROM qc_lot_verifications WHERE qc_lot_id=?', (lot_id,)).fetchone()
            assert row['evidence'] == '验证记录第一行\n第二行' and row['confirmed_by'] == '确认人员'
        at.button(key='qc_lifecycle_verify').click().run()
        assert prefix(at) != p and at.text_input(key=prefix(at)+'person').value == ''


def test_verification_changed_and_batch_edits_reject_old_windows():
    with TemporaryDatabaseContext():
        f = fixture()
        bid = f['bindings']['zscore']['id']
        context = get_qc_binding_context(bid)
        lot_id = next(iter(context['actual_lots']))
        verify(context, lot_id)
        rejected(lambda: save_qc_lifecycle_action('state', bid, dict(state='parallel', verification_ids={},
            effective_at='2026-09-04', operator='测试人员', reason='旧窗口不可提交'), expected_fingerprint=context['fingerprint']), '已修改')
        context = get_qc_binding_context(bid)
        with get_connection() as c:
            c.execute('UPDATE qc_workbench_bindings SET updated_at=? WHERE id=?', ('2099-01-01', bid))
        assert get_qc_binding_context(bid)['fingerprint'] == context['fingerprint'], 'Routine sync timestamp is not a semantic edit'
        with get_connection() as c:
            c.execute('UPDATE qc_lot_configs SET notes=? WHERE id=?', ('批次刚修改', f['config_id']))
        rejected(lambda: save_qc_lifecycle_action('state', bid, dict(state='parallel', verification_ids={},
            effective_at='2026-09-04', operator='测试人员', reason='旧窗口不可提交'), expected_fingerprint=context['fingerprint']), '已修改')


def test_state_dialog_all_actual_lots_cancel_and_effective_time():
    with TemporaryDatabaseContext():
        f = fixture()
        at = start(f)
        at.button(key='qc_lifecycle_state').click().run()
        p = prefix(at)
        context = at.session_state['qc_lifecycle_dialog']['context']
        assert len([box for box in at.selectbox if box.key.startswith(p+'verification_')]) == 3
        before = dump()
        at.text_input(key=p+'person').set_value('测试人员').run()
        at.text_area(key=p+'reason').set_value('状态核对').run()
        at.button(key='qcl_save').click().run()
        assert any('全部实际质控批号' in e.value for e in at.error)
        assert dump() == before
        at.selectbox(key=p+'state').set_value('ended').run()
        at.button(key='qcl_cancel').click().run()
        at.button(key='qcl_discard').click().run()
        clean(at)
        assert dump() == before
        bid = f['bindings']['zscore']['id']
        context = get_qc_binding_context(bid)
        save_qc_lifecycle_action('state', bid, dict(state='ended', verification_ids={}, effective_at='2099-01-01',
            operator='测试人员', reason='未来停止计划'), expected_fingerprint=context['fingerprint'])
        current = get_qc_binding_context(bid)
        assert current['state'] == 'active' and current['state_plan']['state'] == 'ended'


def test_partial_replacement_dialog_uses_high_material_at_product_order_one():
    with TemporaryDatabaseContext():
        f = fixture()
        new_high = register_control_material(material_id=f['data']['qc_material_id'], level_name='高值', level_code='003',
            lot_no='NEW-HIGH-ORDER-ONE', expiry_date='2028-12-31')
        bid = f['bindings']['zscore']['id']
        context = get_qc_binding_context(bid)
        with get_connection() as c:
            new_lot = c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (new_high,)).fetchone()[0]
            originals = [tuple(row) for row in c.execute('SELECT * FROM qc_lot_config_item_levels WHERE lot_config_item_id=?', (context['binding']['lot_config_item_id'],))]
        verification = verify(context, new_lot)
        at = start(f)
        at.button(key='qc_lifecycle_combination').click().run()
        p = prefix(at)
        assert new_high in at.selectbox(key=p+'level_2').options or any('NEW-HIGH-ORDER-ONE' in str(v) for v in at.selectbox(key=p+'level_2').options)
        at.selectbox(key=p+'level_2').set_value(new_high).run()
        at.selectbox(key=p+'verification_2').set_value(verification).run()
        at.text_input(key=p+'person').set_value('换批确认人').run()
        at.text_area(key=p+'reason').set_value('高浓度批号更换').run()
        at.button(key='qcl_save').click().run()
        clean(at)
        assert not at.error, [e.value for e in at.error]
        new_config = at.session_state['v11_pending_existing_config_id']
        assert new_config != f['config_id']
        with get_connection() as c:
            levels = c.execute('''SELECT a.qc_level_id FROM qc_lot_config_item_levels a JOIN qc_lot_config_items i ON i.id=a.lot_config_item_id
                WHERE i.lot_config_id=? AND i.is_enabled=1 AND a.is_disabled=0 ORDER BY a.level_order''', (new_config,)).fetchall()
            assert [r[0] for r in levels] == f['levels'][:2] + [new_high]
            assert originals == [tuple(row) for row in c.execute('SELECT * FROM qc_lot_config_item_levels WHERE lot_config_item_id=?', (context['binding']['lot_config_item_id'],))]
            assert c.execute('SELECT status FROM qc_lot_configs WHERE id=?', (new_config,)).fetchone()[0] == 'draft'


def test_prepare_and_append_pending_items_without_duplicate_config():
    with TemporaryDatabaseContext():
        f = legacy_fixture()
        levels = [register_control_material(material_id=f['data']['qc_material_id'], level_name=name, level_code=code,
            lot_no='ALL-NEW', expiry_date='2028-12-31') for name, code in [('低值', '001'), ('中值', '002'), ('高值', '003')]]
        with get_connection() as c:
            target = c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (levels[0],)).fetchone()[0]
        at = start(f, method='lj')
        at.button(key='qc_lifecycle_prepare').click().run()
        p = prefix(at)
        at.selectbox(key=p+'target').set_value(target).run()
        source_item = f['bindings']['lj']['project_template_item_id']
        at.multiselect(key=p+'items').set_value([source_item]).run()
        at.text_input(key=p+'person').set_value('换批确认人').run()
        at.text_area(key=p+'reason').set_value('先更换一个检验项目').run()
        at.button(key='qcl_save').click().run()
        clean(at)
        assert not at.error, [e.value for e in at.error]
        new = at.session_state['v11_pending_existing_config_id']
        current = get_qc_config_context(f['config_id'])
        existing, remaining = parallel_target(current, target)
        assert existing['id'] == new and existing['status'] == 'draft'
        assert source_item not in {row['source_template_item_id'] for row in remaining}
        another = f['bindings']['instant']['project_template_item_id']
        result = save_qc_lifecycle_action('prepare', f['config_id'], dict(target_qc_lot_id=target,
            template_item_ids=[another], operator='确认人员', reason='追加另一个检验项目', effective_at='2026-09-21'), expected_fingerprint=current['fingerprint'])
        assert result == new
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_lot_configs WHERE template_id=? AND qc_material_lot_id=? AND combination_key=\'\'',
                (f['template_id'], target)).fetchone()[0] == 1
            assert {r[0] for r in c.execute('SELECT source_template_item_id FROM qc_lot_config_items WHERE lot_config_id=? AND is_enabled=1', (new,))} == {source_item, another}


def test_modern_prepare_uses_shared_material_replacement_dialog():
    with TemporaryDatabaseContext():
        f = fixture()
        at = start(f)
        assert at.button(key='qc_lifecycle_prepare').label == '选择各水平的新批号'
        before = dump()
        at.button(key='qc_lifecycle_prepare').click().run()
        clean(at)
        assert 'qc_replacement_dialog' in at.session_state and 'qc_lifecycle_dialog' not in at.session_state
        assert at.session_state['qc_replacement_dialog']['config_id'] == f['config_id']
        assert dump() == before


def test_filters_selection_return_and_hidden_selection_clear():
    with TemporaryDatabaseContext():
        f = fixture()
        at = AppTest.from_function(navigable_page, default_timeout=15)
        at.session_state['qc_lifecycle_config_id'] = f['config_id']
        at.session_state['qc_lifecycle_binding_id'] = f['bindings']['zscore']['id']
        at.run()
        at.text_input(key='qc_lifecycle_search').set_value('ACTUAL-HIGH').run()
        at.selectbox(key='qc_lifecycle_project').set_value(f['template_id']).run()
        at.toggle(key='qcl_test_show').set_value(False).run()
        at.toggle(key='qcl_test_show').set_value(True).run()
        clean(at)
        assert at.text_input(key='qc_lifecycle_search').value == 'ACTUAL-HIGH'
        assert at.selectbox(key='qc_lifecycle_project').value == f['template_id']
        assert at.session_state['qc_lifecycle_config_id'] == f['config_id']
        assert at.session_state['qc_lifecycle_binding_id'] == f['bindings']['zscore']['id']
        at.text_input(key='qc_lifecycle_search').set_value('完全不存在的批号').run()
        assert at.session_state['qc_lifecycle_config_id'] is None
        assert not any(button.key == 'qc_lifecycle_prepare' for button in at.button)


def test_integrated_page_sync_does_not_invalidate_dialog():
    with TemporaryDatabaseContext():
        f = fixture()
        at = AppTest.from_function(integrated_page, default_timeout=20)
        at.session_state['qc_lifecycle_config_id'] = f['config_id']
        at.session_state['qc_lifecycle_binding_id'] = f['bindings']['lj']['id']
        at.run()
        clean(at)
        at.button(key='qc_lifecycle_verify').click().run()
        p = prefix(at)
        context = at.session_state['qc_lifecycle_dialog']['context']
        lot = next(iter(context['actual_lots']))
        at.selectbox(key=p+'lot').set_value(lot).run()
        at.selectbox(key=p+'conclusion').set_value('pass').run()
        at.text_area(key=p+'evidence').set_value('集成页面验证').run()
        at.text_input(key=p+'person').set_value('确认人员').run()
        at.button(key='qcl_cancel').click().run()
        at.button(key='qcl_continue').click().run()
        at.button(key='qcl_save').click().run()
        clean(at)
        assert not at.error, [error.value for error in at.error]
        assert 'qc_lifecycle_dialog' not in at.session_state


if __name__ == '__main__':
    tests = [test_context_reads_and_material_choices_keep_actual_identity, test_verification_dialog_failure_cancel_and_fresh_tokens,
        test_verification_changed_and_batch_edits_reject_old_windows, test_state_dialog_all_actual_lots_cancel_and_effective_time,
        test_partial_replacement_dialog_uses_high_material_at_product_order_one, test_prepare_and_append_pending_items_without_duplicate_config,
        test_modern_prepare_uses_shared_material_replacement_dialog, test_filters_selection_return_and_hidden_selection_clear,
        test_integrated_page_sync_does_not_invalidate_dialog]
    for test in tests:
        test()
        print('PASS ' + test.__name__)
    print(f'All {len(tests)} QC lifecycle dialog smoke tests passed.')
