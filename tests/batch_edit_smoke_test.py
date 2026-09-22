"""Isolated batch dialog save, concurrency, actual material and history checks."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection
from services.batch_edit_service import get_batch_item_context, save_batch_item_settings
from services.material_workflow_service import copy_material_config, register_control_material
from services.project_config_service import (
    activate_lot_config, get_lot_config, list_lot_config_items, set_lot_config_disabled,
)
from services.quality_target_service import adopt_requirement, decode, validate_lot_goal
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.instant_v12_integration_smoke_test import IsolatedDatabase, rejected
from tests.quality_review_fixtures import confirm_fixture_lot, fixture_conditions
from tests.quality_review_smoke_test import copied_lot
from tests.zscore_v12_fixtures import seed_zscore_configuration


def draft_fixture(material_mode=True):
    if not material_mode:
        source = seed_instant_configuration(name='批次参数旧格式回归', cv_limit=2.5)
        config, item = copied_lot(source)
        return source, get_batch_item_context(item)
    source = seed_zscore_configuration(name='批次参数回归', level_count=2, cv_limit=2.5)
    product = get_lot_config(source['config_id'])['qc_material_id']
    actual = [register_control_material(material_id=product, level_name=name, level_code=code,
                lot_no=lot, expiry_date=expiry)
        for name, code, lot, expiry in [('低值', '01', 'EDIT-LOW', '2028-01-31'),
                                       ('高值', '02', 'EDIT-HIGH', '2028-12-31')]]
    config = copy_material_config(source_config_id=source['config_id'],
                                 selections={source['item_id']: actual})
    item = int(list_lot_config_items(config).iloc[0]['id'])
    return source, get_batch_item_context(item)


def assignments(context, ids=None):
    ids = ids or [row['qc_level_id'] for row in context['levels']]
    return [dict(qc_level_id=level, target_source='manual', target_mean=100*(index+1),
                 target_sd=2*(index+1), target_confirmed=True, notes=f'确认参数 {index+1}')
            for index, level in enumerate(ids)]


def database_state():
    with get_connection() as connection:
        return tuple(connection.iterdump())


def adopt_crp(context):
    from services.quality_target_service import item_context
    # Synthetic CRP fixture with the catalog's mg/L unit and explicit evidence.
    with get_connection() as connection:
        connection.execute("UPDATE md_test_items SET chinese_name='CRP' WHERE id=?",
                           (context['item']['test_item_id'],))
    return adopt_requirement('lot', context['item']['id'], 'wst403-2024-047',
        **fixture_conditions(item_context('lot', context['item']['id']), clinical=True),
        confirmed_by='批次参数验收人', evidence='隔离 CRP 夹具，免疫比浊方法、mg/L 和材料适用范围已核对。',
        levels=[dict(level_order=index+1, concentration=100*(index+1), category='')
                for index in range(len(context['levels']))])


def test_context_includes_actual_materials_and_legacy_availability():
    with IsolatedDatabase():
        source, current = draft_fixture()
        assert current['editable'] and not current['read_only_reason'] and current['binding'] is None
        assert current['item']['lot_config_id'] == current['config']['id']
        assert [(row['level_code'], row['lot_no'], row['expiry_date']) for row in current['levels']] == [
            ('01', 'EDIT-LOW', '2028-01-31'), ('02', 'EDIT-HIGH', '2028-12-31')]
        actual_ids = {row['qc_level_id'] for row in current['levels']}
        assert actual_ids <= {row['id'] for row in current['available_levels']}
        assert set(source['level_ids']) <= {row['id'] for row in current['available_levels']}
        assert all(row['target_mean'] is None or isinstance(row['target_mean'], (float, int))
                   for row in current['levels'])
    with IsolatedDatabase():
        source, current = draft_fixture(False)
        assert current['editable']
        assert {row['qc_material_lot_id'] for row in current['available_levels']} == {
            current['config']['qc_material_lot_id']}
        assert source['level_id'] not in {row['id'] for row in current['available_levels']}
        rejected(lambda: get_batch_item_context(999999), '未找到')


def test_parameter_and_cv_save_is_explicit_and_rejects_stale_revision():
    with IsolatedDatabase():
        _, current = draft_fixture(False)
        item = current['item']['id']
        changed = save_batch_item_settings(item, assignments(current), expected_revision=current['revision'],
            cv_limit=99, source_text='此字段本次不应保存', update_cv=False)
        assert changed['item']['cv_limit'] == 2.5
        assert changed['item']['quality_target_source_text'] == current['item']['quality_target_source_text']
        assert changed['levels'][0]['target_mean'] == 100 and changed['levels'][0]['target_sd'] == 2
        before = database_state()
        rejected(lambda: save_batch_item_settings(item, assignments(current),
            expected_revision=current['revision'], cv_limit=2.25, update_cv=True), '已修改')
        assert database_state() == before
        saved = save_batch_item_settings(item, assignments(changed), expected_revision=changed['revision'],
            cv_limit=2.25, source_text='SOP-BATCH-2026', update_cv=True)
        assert saved['item']['cv_limit'] == 2.25
        assert saved['item']['quality_target_source_text'] == 'SOP-BATCH-2026'
        assert saved['levels'][0]['target_source'] == 'manual'
        assert saved['levels'][0]['target_confirmed'] == 1
        assert validate_lot_goal(item)  # The earlier quality review cannot cover a changed CV.
        confirm_fixture_lot(saved['config']['id'])
        assert not validate_lot_goal(item)
        activate_lot_config(saved['config']['id'])


def test_late_cv_failure_rolls_back_parameters_revision_and_snapshots():
    with IsolatedDatabase():
        _, current = draft_fixture()
        before = database_state()
        rejected(lambda: save_batch_item_settings(current['item']['id'], assignments(current),
            expected_revision=current['revision'], cv_limit=-1, update_cv=True), '大于 0')
        assert database_state() == before
        adopt_crp(current)
        current = get_batch_item_context(current['item']['id'])
        before = database_state()
        rejected(lambda: save_batch_item_settings(current['item']['id'], assignments(current),
            expected_revision=current['revision'], cv_limit=2.25, update_cv=True), '已选择质量目标')
        assert database_state() == before


def test_actual_material_reassignment_retains_goal_but_requires_confirmation():
    with IsolatedDatabase():
        _, current = draft_fixture()
        original_goal = adopt_crp(current)
        item = current['item']['id']
        current = get_batch_item_context(item)
        unchanged = save_batch_item_settings(item, assignments(current), expected_revision=current['revision'])
        assert decode(unchanged['item']['quality_goal_json']) == original_goal
        assert not validate_lot_goal(item)
        replacement = register_control_material(material_id=current['config']['qc_material_id'],
            level_name='低值', level_code='01', lot_no='EDIT-NEXT-LOW', expiry_date='2029-01-31')
        wanted = [current['levels'][1]['qc_level_id'], replacement]
        changed = save_batch_item_settings(item, assignments(unchanged, wanted),
                                          expected_revision=unchanged['revision'])
        assert [row['qc_level_id'] for row in changed['levels']] == wanted
        assert [row['lot_no'] for row in changed['levels']] == ['EDIT-HIGH', 'EDIT-NEXT-LOW']
        assert [row['target_mean'] for row in changed['levels']] == [100, 200]
        assert [row['target_sd'] for row in changed['levels']] == [2, 4]
        pending = decode(changed['item']['quality_goal_json'])
        assert pending['spec'] == original_goal['spec'] and pending['pending']
        assert validate_lot_goal(item)
        rejected(lambda: activate_lot_config(changed['config']['id']), '质量目标')
        reviewed = adopt_crp(changed)
        assert reviewed['spec'] == original_goal['spec'] and not validate_lot_goal(item)
        assert [row['qc_level_id'] for row in decode(get_batch_item_context(item)['item']['quality_review_json'])['levels']] == wanted
        activate_lot_config(changed['config']['id'])
        assert not get_batch_item_context(item)['editable']


def test_used_batch_remains_read_only_after_disable_and_restore():
    for material_mode in (False, True):
        with IsolatedDatabase():
            source, current = draft_fixture(material_mode)
            # Real historical configuration includes its original runtime binding.
            item, config = source['item_id'], source['config_id']
            if material_mode:
                from services.zscore_workbench_service import sync_zscore_workbench_bindings
                item, config = current['item']['id'], current['config']['id']
                save_batch_item_settings(item, assignments(current), expected_revision=current['revision'])
                confirm_fixture_lot(config)
                activate_lot_config(config)
                sync_zscore_workbench_bindings()
            original = get_batch_item_context(item)
            assert not original['editable'] and original['binding']
            set_lot_config_disabled(config, is_disabled=True, reason='只读恢复验收')
            assert not get_batch_item_context(item)['editable']
            set_lot_config_disabled(config, is_disabled=False)
            restored = get_batch_item_context(item)
            assert restored['config']['status'] == 'draft' and restored['config']['activated_at']
            assert not restored['editable'] and restored['levels'] == original['levels']
            before = database_state()
            rejected(lambda: save_batch_item_settings(item, assignments(restored),
                expected_revision=restored['revision']), '不能修改')
            assert database_state() == before
            # A binding by itself also protects imported / older used history.
            with get_connection() as connection:
                connection.execute('UPDATE qc_lot_configs SET activated_at=NULL WHERE id=?', (config,))
            assert not get_batch_item_context(item)['editable']


def test_each_read_only_condition_is_enforced_by_save_service():
    for changes, reason in [
        ("UPDATE qc_lot_configs SET status='active' WHERE id=?", '已确认'),
        ("UPDATE qc_lot_configs SET activated_at='2026-09-20' WHERE id=?", '不能修改'),
        ("UPDATE qc_lot_configs SET is_disabled=1 WHERE id=?", '已停用'),
        ("UPDATE qc_lot_config_items SET is_disabled=1 WHERE id=?", '已停用'),
    ]:
        with IsolatedDatabase():
            _, current = draft_fixture(False)
            item = current['item']['id']
            target = item if 'config_items' in changes else current['config']['id']
            with get_connection() as connection:
                connection.execute(changes, (target,))
            context = get_batch_item_context(item)
            assert not context['editable'] and context['levels']
            before = database_state()
            rejected(lambda: save_batch_item_settings(item, assignments(context),
                expected_revision=context['revision']), reason)
            assert database_state() == before


def quality_form(item):
    return AppTest.from_string(f'''
import streamlit as st
from services.batch_edit_service import get_batch_item_context
from ui.quality_targets import render_adoption
if 'expected' not in st.session_state:
    st.session_state.expected = get_batch_item_context({item})['revision']
def saved():
    st.session_state.saved = True
if not st.session_state.get('saved'):
    render_adoption('lot', {item}, embedded=True, expected_revision=st.session_state.expected, on_saved=saved)
''', default_timeout=30).run()


def test_quality_dialog_recorded_save_checks_revision_and_calls_back_after_commit():
    with IsolatedDatabase():
        _, current = draft_fixture(False)
        item, prefix = current['item']['id'], f"quality_lot_{current['item']['id']}"
        app = quality_form(item)
        app.text_input(key=prefix+'_source_name').set_value('参数编辑 SOP')
        app.checkbox(key=prefix+'_confirmed').check().run()
        current = save_batch_item_settings(item, assignments(current), expected_revision=current['revision'])
        before = database_state()
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and any('已修改' in error.value for error in app.error)
        assert not app.session_state.filtered_state.get('saved') and database_state() == before
        app.session_state['expected'] = current['revision']
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and app.session_state['saved']
        assert decode(get_batch_item_context(item)['item']['quality_review_json'])['recorded']['source_name'] == '参数编辑 SOP'
        assert not validate_lot_goal(item)


def test_quality_dialog_standard_save_uses_the_same_revision_guard():
    with IsolatedDatabase():
        _, current = draft_fixture()
        adopt_crp(current)
        item, prefix = current['item']['id'], f"quality_lot_{current['item']['id']}"
        current = get_batch_item_context(item)
        app = quality_form(item)
        app.checkbox(key=prefix+'_confirmed').check().run()
        app.text_input(key=prefix+'_person').set_value('新确认人')
        current = save_batch_item_settings(item, assignments(current), expected_revision=current['revision'])
        before = database_state()
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and any('已修改' in error.value for error in app.error)
        assert database_state() == before and not app.session_state.filtered_state.get('saved')
        app.session_state['expected'] = current['revision']
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and app.session_state['saved']
        assert decode(get_batch_item_context(item)['item']['quality_goal_json'])['confirmed_by'] == '新确认人'
        assert not validate_lot_goal(item)


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
