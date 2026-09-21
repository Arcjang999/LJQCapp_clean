"""Explicit QC replacement drafts, stale selection guards and unchanged source history."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection
from services.material_workflow_service import register_control_material
from services.project_config_service import copy_lot_config, get_lot_config, list_lot_config_items, list_lot_item_levels
from services.qc_replacement_edit_service import get_qc_replacement_context, save_qc_replacement
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config
from tests.material_workflow_smoke_test import mixed_fixture
from tests.project_workspace_smoke_test import assert_clean, select_table_row
from tests.lot_lifecycle_smoke_test import rejected


def snapshot():
    with get_connection() as connection:
        return tuple(connection.iterdump())


def app_page():
    from ui.qc_replacement_workspace import render_qc_replacement_workspace, render_pending_qc_replacement_dialog
    render_qc_replacement_workspace()
    render_pending_qc_replacement_dialog()


def field(app, suffix):
    return 'qcr_' + app.session_state['qc_replacement_dialog']['token'] + '_' + suffix


def open_first(app):
    index = next(i for i, table in enumerate(app.dataframe) if 'qc_replace_table_' in table.proto.id)
    select_table_row(app, 0, index=index)
    app.button(key='qc_replace_open').click().run()
    assert_clean(app)
    return app


def test_stale_source_levels_and_invalid_selection_do_not_create_partial_copy():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        context = get_qc_replacement_context(source)
        before = snapshot()
        rejected(lambda: save_qc_replacement(source_config_id=source,expected_fingerprint=context['fingerprint'],
            mode='lot',target_lot_id=data['source_lot_id']), '不同的新质控品批号')
        assert snapshot() == before
        rejected(lambda: save_qc_replacement(source_config_id=source,expected_fingerprint=context['fingerprint'],
            mode='materials',selections={context['items'][0]['id']:[999999]}), '重新选择')
        assert snapshot() == before
        level_id = context['levels'][context['items'][0]['id']][0]['id']
        with get_connection() as connection:
            connection.execute('UPDATE qc_lot_config_item_levels SET notes=? WHERE id=?',('another window',level_id))
        after_other_edit = snapshot()
        rejected(lambda: save_qc_replacement(source_config_id=source,expected_fingerprint=context['fingerprint'],
            mode='lot',target_lot_id=data['target_lot_id']), '原批次或质控品资料已修改')
        assert snapshot() == after_other_edit


def test_partial_level_dialog_uses_actual_ids_and_preserves_other_materials():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        new_mid = register_control_material(material_id=f['data']['qc_material_id'],level_name='中值',level_code='002',
            lot_no='REPLACEMENT-MID',expiry_date='2029-02-28')
        with get_connection() as connection:
            assert connection.execute('SELECT level_order FROM md_qc_levels WHERE id=?',(new_mid,)).fetchone()[0] == 1
            prior_configs = [dict(row) for row in connection.execute('SELECT * FROM qc_lot_configs')]
            prior_snapshots = [dict(row) for row in connection.execute('SELECT * FROM qc_config_snapshots')]
            prior_bindings = [dict(row) for row in connection.execute('SELECT * FROM qc_workbench_bindings')]
        zitem = next(item for item in list_lot_config_items(f['config_id']).to_dict('records') if item['qc_method']=='zscore')
        app = open_first(AppTest.from_function(app_page,default_timeout=20).run())
        before = snapshot()
        app.multiselect(key=field(app,'items')).set_value([zitem['id']]).run()
        # Product order=1 can legitimately serve the second position in this batch.
        app.selectbox(key=field(app,f'item_{zitem["id"]}_1')).set_value(new_mid).run()
        app.text_input(key=field(app,'name')).set_value('中水平换批试用').run()
        assert_clean(app)
        assert snapshot() == before
        app.button(key='qc_replace_cancel').click().run()
        assert_clean(app)
        app.button(key='qc_replace_continue').click().run()
        assert app.selectbox(key=field(app,f'item_{zitem["id"]}_1')).value == new_mid
        assert app.text_input(key=field(app,'name')).value == '中水平换批试用'
        app.button(key='qc_replace_save').click().run()
        assert_clean(app)
        new_id = app.session_state['v11_pending_copied_config_id']
        assert new_id != f['config_id'] and get_lot_config(new_id)['status']=='draft'
        new_items = list_lot_config_items(new_id)
        assert new_items[new_items.is_enabled==1].qc_method.tolist() == ['zscore']
        selected = new_items[new_items.qc_method=='zscore'].iloc[0]
        assert list_lot_item_levels(int(selected.id)).qc_level_id.tolist() == [f['levels'][0],new_mid,f['levels'][2]]
        with get_connection() as connection:
            assert [dict(row) for row in connection.execute('SELECT * FROM qc_lot_configs WHERE id<>?',(new_id,))] == prior_configs
            assert [dict(row) for row in connection.execute('SELECT * FROM qc_config_snapshots WHERE lot_config_id<>?',(new_id,))] == prior_snapshots
            assert [dict(row) for row in connection.execute('SELECT * FROM qc_workbench_bindings')] == prior_bindings


def test_validation_failure_discard_and_reopen_never_leak_draft():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        app = open_first(AppTest.from_function(app_page,default_timeout=20).run())
        before = snapshot()
        token = app.session_state['qc_replacement_dialog']['token']
        app.radio(key=field(app,'mode')).set_value('lot').run()
        app.text_input(key=field(app,'name')).set_value('未保存新批次')
        app.button(key='qc_replace_save').click().run()
        assert_clean(app)
        assert any('请选择新质控品批号' in item.value for item in app.error)
        assert app.text_input(key=field(app,'name')).value == '未保存新批次'
        assert snapshot() == before
        app.button(key='qc_replace_cancel').click().run()
        app.button(key='qc_replace_discard').click().run()
        assert_clean(app)
        assert snapshot() == before and app.session_state['qc_replace_selected_config'] == source
        app.button(key='qc_replace_open').click().run()
        assert app.session_state['qc_replacement_dialog']['token'] != token
        assert app.text_input(key=field(app,'name')).value == ''
        assert app.radio(key=field(app,'mode')).value == 'materials'


def test_existing_target_opens_without_creating_duplicate():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        target = copy_lot_config(source_lot_config_id=source,target_qc_material_lot_id=data['target_lot_id'])
        app = AppTest.from_function(app_page,default_timeout=20).run()
        app.text_input(key='qc_replace_search').set_value(get_lot_config(source)['config_name']).run()
        index = next(i for i,table in enumerate(app.dataframe) if 'qc_replace_table_' in table.proto.id)
        names = app.dataframe[index].value['批次名称'].tolist()
        select_table_row(app,names.index(get_lot_config(source)['config_name']),index=index)
        app.button(key='qc_replace_open').click().run()
        app.radio(key=field(app,'mode')).set_value('lot').run()
        app.selectbox(key=field(app,'target_lot')).set_value(data['target_lot_id']).run()
        assert_clean(app)
        assert app.button(key='qc_replace_save').disabled
        before = snapshot()
        app.text_input(key=field(app,'name')).set_value('尚未保存的名称')
        app.button(key='qc_replace_open_existing').click().run()
        assert_clean(app)
        assert app.session_state['qc_replacement_dialog']['discard']
        assert snapshot() == before
        app.button(key='qc_replace_continue').click().run()
        assert app.text_input(key=field(app,'name')).value == '尚未保存的名称'
        app.button(key='qc_replace_open_existing').click().run()
        app.button(key='qc_replace_discard').click().run()
        assert app.session_state['v11_pending_existing_config_id'] == target
        assert snapshot() == before


def test_switching_modes_keeps_unsubmitted_material_choices():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        app = open_first(AppTest.from_function(app_page,default_timeout=20).run())
        state = app.session_state['qc_replacement_dialog']
        item = state['context']['items'][0]
        original = state['draft']['selections'][item['id']][0]
        different = next(row['id'] for row in state['context']['materials'] if row['id'] != original)
        before = snapshot()
        app.selectbox(key=field(app,f'item_{item["id"]}_0')).set_value(different)
        app.text_input(key=field(app,'name')).set_value('切换后保留')
        app.radio(key=field(app,'mode')).set_value('lot').run()
        app.selectbox(key=field(app,'target_lot')).set_value(data['target_lot_id'])
        app.radio(key=field(app,'mode')).set_value('materials').run()
        assert_clean(app)
        assert app.selectbox(key=field(app,f'item_{item["id"]}_0')).value == different
        assert app.text_input(key=field(app,'name')).value == '切换后保留'
        app.radio(key=field(app,'mode')).set_value('lot').run()
        assert app.selectbox(key=field(app,'target_lot')).value == data['target_lot_id']
        assert snapshot() == before


if __name__ == '__main__':
    for name,function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS',name,flush=True)
