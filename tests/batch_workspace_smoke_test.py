"""Real-ID batch selection and creation drafts on isolated data."""
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.project_config_service import copy_lot_config, list_lot_configs, list_lot_config_items
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config
from tests.project_workspace_smoke_test import select_table_row, assert_clean


def workspace_page():
    from ui.batch_workspace import render_batch_workspace
    render_batch_workspace()


def test_filtered_lists_edit_the_selected_batch_and_item():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source = _build_active_source_config(data)
        first = copy_lot_config(source_lot_config_id=source, target_qc_material_lot_id=data['target_lot_id'], config_name='Alpha 批次')
        second = copy_lot_config(source_lot_config_id=source, target_qc_material_lot_id=data['target_lot_id'], config_name='Beta 批次', combination_key='second')
        app = AppTest.from_function(workspace_page, default_timeout=15).run()
        assert_clean(app)
        names = app.dataframe[0].value['批次名称'].tolist()
        select_table_row(app, names.index('Beta 批次'))
        assert app.session_state['v11_selected_lot_config_id'] == second
        select_table_row(app, 1, index=1)
        iid = int(list_lot_config_items(second).iloc[1].id)
        assert app.session_state[f'batch_selected_item_{second}'] == iid
        assert not list(app.number_input)
        app.button(key=f'batch_edit_levels_{iid}').click().run()
        assert_clean(app)
        assert app.session_state['batch_item_dialog']['context']['config']['id'] == second
        assert app.session_state['batch_item_dialog']['item_id'] == iid
        app.button(key='batch_levels_cancel').click().run()
        assert_clean(app)
        assert app.session_state['v11_selected_lot_config_id'] == second
        app.text_input(key='batch_search').set_value('Alpha').run()
        assert app.session_state['v11_selected_lot_config_id'] is None
        assert not any(button.key.startswith('batch_edit_levels_') for button in app.button)
        select_table_row(app, 0)
        assert app.session_state['v11_selected_lot_config_id'] == first
        assert app.dataframe[0].value['批次名称'].tolist() == ['Alpha 批次']


def test_create_failure_continue_cancel_and_reopen():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid, _ = _build_active_source_config(data)
        before = list_lot_configs(include_disabled=True).to_json(orient='records')
        app = AppTest.from_function(workspace_page, default_timeout=15).run()
        app.selectbox(key='batch_project_filter').set_value(tid).run()
        app.button(key='batch_create').click().run()
        assert_clean(app)
        app.text_input(key='v11_create_config_name').set_value('未保存新批次')
        app.button(key='v11_create_config_button').click().run()
        assert_clean(app)
        assert list(app.error)
        assert list_lot_configs(include_disabled=True).to_json(orient='records') == before
        app.button(key='batch_create_cancel').click().run()
        assert_clean(app)
        app.button(key='batch_create_continue').click().run()
        assert_clean(app)
        assert app.text_input(key='v11_create_config_name').value == '未保存新批次'
        app.button(key='batch_create_cancel').click().run()
        app.button(key='batch_create_discard').click().run()
        assert_clean(app)
        assert list_lot_configs(include_disabled=True).to_json(orient='records') == before
        app.button(key='batch_create').click().run()
        assert app.text_input(key='v11_create_config_name').value == ''
        app.button(key='batch_create_cancel').click().run()
        assert_clean(app)
        assert 'batch_workspace_create' not in app.session_state


if __name__ == '__main__':
    test_filtered_lists_edit_the_selected_batch_and_item()
    test_create_failure_continue_cancel_and_reopen()
    print('batch_workspace_smoke_test passed')
