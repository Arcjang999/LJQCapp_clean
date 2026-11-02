"""Physical-control registration retains drafts and keeps stale-source guards."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

from database import get_connection
from services.material_registration_refresh_service import can_refresh_registered_materials
from services.material_workflow_service import available_materials, register_control_material
from services.project_config_service import copy_lot_config, list_lot_configs, list_lot_config_items, list_lot_item_levels
from services.qc_lifecycle_edit_service import get_qc_binding_context, get_qc_config_context
from services.qc_replacement_edit_service import get_qc_replacement_context
from tests.batch_workspace_smoke_test import workspace_page
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config,
)
from tests.project_workspace_smoke_test import assert_clean
from tests.qc_lifecycle_dialogs_smoke_test import fixture, legacy_fixture, start, prefix, page
from tests.qc_replacement_dialogs_smoke_test import app_page, field, open_first


def legacy_replacement_page():
    from services.project_config_service import list_lot_configs
    from ui.materials import render_material_replacement
    render_material_replacement(list_lot_configs())


def register_in_place(app, key, lot='NEW-REGISTERED', name='新水平', code='NEW', button_index=0):
    buttons = [button for button in app.button if button.key == key + '_register']
    if not buttons:
        buttons = [button for button in app.button if (button.key or '').endswith('_new_lot')]
    buttons[button_index].click().run()
    assert_clean(app)
    state = app.session_state[key + '_registration']
    editor = 'material_edit_' + state['nonce']
    if any(widget.key == editor + '_new_name' for widget in app.text_input):
        app.text_input(key=editor + '_new_name').set_value(name)
        app.text_input(key=editor + '_new_code').set_value(code)
    app.text_input(key=editor + '_lot').set_value(lot)
    app.date_input(key=editor + '_expiry').set_value(date(2029, 12, 31))
    app.button(key=editor + '_save').click().run()
    assert_clean(app)
    return int(available_materials(state['product_id']).query('lot_no == @lot').iloc[0]['id'])


def test_batch_creation_registration_keeps_names_and_existing_choices():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid, _ = _build_active_source_config(data)
        app = AppTest.from_function(workspace_page, default_timeout=20).run()
        app.selectbox(key='batch_project_filter').set_value(tid).run()
        app.button(key='batch_create').click().run()
        controls = [widget for widget in app.selectbox if (widget.key or '').startswith('create_material_') and widget.key.endswith('_level')]
        controls[0].set_value(data['source_levels'][0]).run()
        selected_key, target_key = controls[0].key, controls[1].key
        assert not any(widget.key == 'v11_create_config_name' for widget in app.text_input)
        configs = list_lot_configs(include_disabled=True).to_json(orient='records')
        new_id = register_in_place(app, f'material_registration_create_{tid}', button_index=1)
        assert app.selectbox(key=selected_key).value == data['source_levels'][0]
        assert app.selectbox(key=target_key).value == new_id
        assert list_lot_configs(include_disabled=True).to_json(orient='records') == configs


def test_batch_registration_cancel_keeps_the_parent_draft():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid, _ = _build_active_source_config(data)
        app = AppTest.from_function(workspace_page, default_timeout=20).run()
        app.selectbox(key='batch_project_filter').set_value(tid).run()
        app.button(key='batch_create').click().run()
        control = next(widget for widget in app.selectbox if (widget.key or '').startswith('create_material_') and widget.key.endswith('_level'))
        selected_key = control.key
        control.set_value(data['source_levels'][0]).run()
        key = f'material_registration_create_{tid}'
        before = available_materials(data['qc_material_id']).to_json(orient='records')
        button = next(widget for widget in app.button if (widget.key or '').endswith('_new_lot'))
        button.click().run()
        editor = 'material_edit_' + app.session_state[key + '_registration']['nonce']
        assert not any(widget.key in ('v11_create_config_button', 'batch_create_cancel') for widget in app.button)
        app.button(key=editor + '_cancel').click().run()
        assert_clean(app)
        assert app.selectbox(key=selected_key).value == data['source_levels'][0]
        assert available_materials(data['qc_material_id']).to_json(orient='records') == before
        app.button(key='batch_create_cancel').click().run()
        app.button(key='batch_create_continue').click().run()
        assert_clean(app)
        assert app.selectbox(key=selected_key).value == data['source_levels'][0]


def test_legacy_replacement_locks_source_while_registration_is_pending():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        app = AppTest.from_function(legacy_replacement_page, default_timeout=20).run()
        app.selectbox(key='material_copy_source').set_value(source).run()
        key = f'material_registration_copy_{source}'
        next(widget for widget in app.button if (widget.key or '').endswith('_new_lot')).click().run()
        assert_clean(app)
        assert not any(widget.key == 'material_copy_source' for widget in app.selectbox)
        assert not any(widget.key == 'material_copy_save' for widget in app.button)
        state = app.session_state[key + '_registration']
        assert state['product_id'] == data['qc_material_id']
        editor = 'material_edit_' + state['nonce']
        app.button(key=editor + '_cancel').click().run()
        assert_clean(app)
        assert app.selectbox(key='material_copy_source').value == source


def test_replacement_registration_refreshes_candidates_and_preserves_draft():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        app = open_first(AppTest.from_function(app_page, default_timeout=20).run())
        state = app.session_state['qc_replacement_dialog']
        item_id = int(state['context']['items'][0]['id'])
        app.multiselect(key=field(app, 'items')).set_value([item_id]).run()
        original = app.selectbox(key=field(app, f'item_{item_id}_0_level')).value
        previous_fingerprint = state['fingerprint']
        new_id = register_in_place(app, 'qc_replace_register_' + state['token'])
        state = app.session_state['qc_replacement_dialog']
        assert state['fingerprint'] != previous_fingerprint
        assert state['draft']['item_ids'] == [item_id]
        assert app.selectbox(key=field(app, f'item_{item_id}_0_level')).value == new_id
        app.button(key='qc_replace_save').click().run()
        assert_clean(app)
        new_config = app.session_state['v11_pending_copied_config_id']
        assert new_config != source
        item = list_lot_config_items(new_config).query('is_enabled == 1').iloc[0]
        assert list_lot_item_levels(int(item['id'])).qc_level_id.tolist() == [new_id]


def test_replacement_registration_cannot_accept_concurrent_source_changes():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        app = open_first(AppTest.from_function(app_page, default_timeout=20).run())
        state = app.session_state['qc_replacement_dialog']
        original_fingerprint = state['fingerprint']
        item_id = int(state['context']['items'][0]['id'])
        app.multiselect(key=field(app, 'items')).set_value([item_id]).run()
        with get_connection() as connection:
            connection.execute('UPDATE qc_lot_config_item_levels SET notes=? WHERE lot_config_item_id=?',
                               ('并发修改原批次', item_id))
        new_id = register_in_place(app, 'qc_replace_register_' + state['token'])
        assert app.session_state['qc_replacement_dialog']['fingerprint'] == original_fingerprint
        assert new_id not in [row['id'] for row in app.session_state['qc_replacement_dialog']['context']['materials']]
        app.button(key='qc_replace_save').click().run()
        assert_clean(app)
        assert any('原批次或质控品资料已修改' in error.value for error in app.error)
        assert len(list_lot_configs()) == 1


def test_replacement_existing_target_and_mode_are_locked_during_registration():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        copy_lot_config(source_lot_config_id=source, target_qc_material_lot_id=data['target_lot_id'])
        app = AppTest.from_function(app_page, default_timeout=20)
        app.session_state['qc_replace_selected_config'] = source
        app.run()
        app.button(key='qc_replace_open').click().run()
        app.radio(key=field(app, 'mode')).set_value('lot').run()
        app.selectbox(key=field(app, 'target_lot')).set_value(data['target_lot_id']).run()
        assert not app.button(key='qc_replace_open_existing').disabled
        token = app.session_state['qc_replacement_dialog']['token']
        registration_key = 'qc_replace_register_' + token
        app.button(key=registration_key + '_register').click().run()
        assert_clean(app)
        assert not any(widget.key == 'qc_replace_open_existing' for widget in app.button)
        assert not any(widget.key == field(app, 'mode') for widget in app.radio)
        editor = 'material_edit_' + app.session_state[registration_key + '_registration']['nonce']
        app.button(key=editor + '_cancel').click().run()
        assert_clean(app)
        assert not app.button(key='qc_replace_open_existing').disabled
        assert not app.radio(key=field(app, 'mode')).disabled
        assert app.selectbox(key=field(app, 'target_lot')).value == data['target_lot_id']


def test_lifecycle_verification_registration_preserves_evidence_and_can_save():
    with TemporaryDatabaseContext():
        f = fixture()
        app = start(f)
        app.button(key='qc_lifecycle_verify').click().run()
        p = prefix(app)
        app.selectbox(key=p + 'conclusion').set_value('pass').run()
        app.text_area(key=p + 'evidence').set_value('登记新批号之前填写的依据')
        app.text_input(key=p + 'person').set_value('原确认人员')
        token = app.session_state['qc_lifecycle_dialog']['token']
        new_level = register_in_place(app, 'qc_lifecycle_register_' + token)
        context = app.session_state['qc_lifecycle_dialog']['context']
        new_lot = next(row['qc_material_lot_id'] for row in context['levels'] if row['id'] == new_level)
        assert app.selectbox(key=p + 'lot').value == new_lot
        assert app.text_area(key=p + 'evidence').value == '登记新批号之前填写的依据'
        assert app.text_input(key=p + 'person').value == '原确认人员'
        app.button(key='qcl_save').click().run()
        assert_clean(app)
        assert not app.error, [error.value for error in app.error]
        with get_connection() as connection:
            record = connection.execute('SELECT * FROM qc_lot_verifications WHERE qc_lot_id=?', (new_lot,)).fetchone()
            assert record['evidence'] == '登记新批号之前填写的依据'


def test_lifecycle_prepare_registration_keeps_reason_and_accepts_new_lot():
    with TemporaryDatabaseContext():
        f = legacy_fixture()
        app = AppTest.from_function(page, default_timeout=20)
        app.session_state['qc_lifecycle_config_id'] = f['config_id']
        app.run()
        app.button(key='qc_lifecycle_prepare').click().run()
        p = prefix(app)
        app.text_input(key=p + 'person').set_value('换批确认人员')
        app.text_area(key=p + 'reason').set_value('登记前填写的换批依据')
        token = app.session_state['qc_lifecycle_dialog']['token']
        new_level = register_in_place(app, 'qc_lifecycle_register_' + token)
        context = app.session_state['qc_lifecycle_dialog']['context']
        new_lot = next(row['qc_material_lot_id'] for row in context['levels'] if row['id'] == new_level)
        assert app.selectbox(key=p + 'target').value == new_lot
        assert app.text_area(key=p + 'reason').value == '登记前填写的换批依据'
        assert app.text_input(key=p + 'person').value == '换批确认人员'
        options = context['items']
        app.multiselect(key=p + 'items').set_value([row['source_template_item_id'] for row in options]).run()
        app.button(key='qcl_save').click().run()
        assert_clean(app)
        assert not app.error, [error.value for error in app.error]
        assert app.session_state['v11_pending_existing_config_id'] != f['config_id']


def test_lifecycle_existing_target_is_locked_during_registration():
    with TemporaryDatabaseContext():
        f = legacy_fixture()
        target_lot = f['data']['target_lot_id']
        copy_lot_config(source_lot_config_id=f['config_id'], target_qc_material_lot_id=target_lot)
        app = AppTest.from_function(page, default_timeout=20)
        app.session_state['qc_lifecycle_config_id'] = f['config_id']
        app.run()
        app.button(key='qc_lifecycle_prepare').click().run()
        p = prefix(app)
        app.selectbox(key=p + 'target').set_value(target_lot).run()
        assert not app.button(key='qcl_open_existing').disabled
        token = app.session_state['qc_lifecycle_dialog']['token']
        registration_key = 'qc_lifecycle_register_' + token
        app.button(key=registration_key + '_register').click().run()
        assert_clean(app)
        assert not any(button.key in ('qcl_open_existing', 'qcl_cancel', 'qcl_save') for button in app.button)
        editor = 'material_edit_' + app.session_state[registration_key + '_registration']['nonce']
        app.button(key=editor + '_cancel').click().run()
        assert_clean(app)
        assert not app.button(key='qcl_open_existing').disabled
        assert app.selectbox(key=p + 'target').value == target_lot


def test_refresh_guard_only_accepts_additions_to_material_rows():
    with TemporaryDatabaseContext():
        f = fixture()
        config_id, binding_id = f['config_id'], f['bindings']['lj']['id']
        getters = [lambda: get_qc_replacement_context(config_id), lambda: get_qc_config_context(config_id),
                   lambda: get_qc_binding_context(binding_id)]
        previous = [getter() for getter in getters]
        register_control_material(material_id=f['data']['qc_material_id'], level_name='新水平',
                                  lot_no='APPEND-ONLY', expiry_date='2029-12-31')
        current = [getter() for getter in getters]
        assert all(can_refresh_registered_materials(before, after) for before, after in zip(previous, current))
        with get_connection() as connection:
            connection.execute('UPDATE md_qc_levels SET concentration_label=? WHERE id=?',
                               ('已有材料真实变化', f['levels'][0]))
        assert all(not can_refresh_registered_materials(before, getter()) for before, getter in zip(previous, getters))
        previous = [getter() for getter in getters]
        with get_connection() as connection:
            connection.execute('UPDATE qc_lot_config_item_levels SET notes=? WHERE lot_config_item_id=?',
                               ('原批次真实变化', f['bindings']['lj']['lot_config_item_id']))
        assert all(not can_refresh_registered_materials(before, getter()) for before, getter in zip(previous, getters))


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
