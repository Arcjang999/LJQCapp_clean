"""Old settings -> different QC lot -> reviewable draft, with original results intact."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection, add_result, get_results
from services.project_config_service import copy_lot_config, get_lot_config, list_lot_configs, list_lot_config_items, list_lot_item_levels
from services.workbench_config_service import sync_lj_workbench_bindings
from services.lot_lifecycle_service import create_target_profile
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config
from tests.lot_lifecycle_smoke_test import rejected


def test_reject_same_lot_without_creating_a_draft():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid, source_id = _build_active_source_config(data)
        before = list_lot_configs(tid).to_dict('records')
        rejected(lambda: copy_lot_config(source_lot_config_id=source_id,
            target_qc_material_lot_id=data['source_lot_id']), '不同的新质控品批号')
        assert list_lot_configs(tid).to_dict('records') == before


def test_ui_guides_to_new_draft_and_preserves_old_results():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid, source_id = _build_active_source_config(data)
        sync_lj_workbench_bindings()
        with get_connection() as connection:
            batch_id = connection.execute("SELECT runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_id=? AND qc_method='lj'", (source_id,)).fetchone()[0]
        create_target_profile(method='lj', batch_id=batch_id,
            levels=[{'level_id': 'Level 1', 'mean': 10, 'sd': .5}],
            source='manual', evidence='测试参数确认', confirmed_by='测试人', effective_at='2026-09-01')
        add_result(batch_id, '2026-09-11 08:00:00', 10.25, operator='复制流程测试')
        before_results = get_results(batch_id).to_dict('records')
        before_source = dict(get_lot_config(source_id))
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=15)
        app.session_state['show_project_management_page'] = True
        app.session_state['v11_management_tabs'] = '更换质控品批次'
        app.run()
        assert not list(app.exception)
        source = app.selectbox(key='v11_copy_source_config')
        source.select_index(1).run()
        target = app.selectbox(key=f'v11_copy_target_lot_{source_id}')
        assert not any('V11-LOT-001' in value for value in target.options)
        assert any('V11-LOT-002' in value for value in target.options)
        target.select_index(1).run()
        assert not list(app.exception)
        app.button(key='v11_copy_config_button').click().run()
        assert not list(app.exception)
        assert app.session_state['v11_management_tabs'] == '批次管理'
        new_id = int(app.session_state['v11_selected_lot_config_id'])
        assert new_id != source_id
        new = get_lot_config(new_id)
        assert new['status'] == 'draft' and new['qc_material_lot_id'] == data['target_lot_id']
        assert 'V11-LOT-002' in app.selectbox(key='v11_lot_config_selector').value
        assert any('核对水平和靶值' in item.value for item in app.success)
        assert 'v11_copy_config_button' not in [button.key for button in app.button]
        assert any(button.key == 'v11_copy_register_lot' for button in app.button)
        items = list_lot_config_items(new_id)
        lj_item = items[items.qc_method == 'lj'].iloc[0]
        assert lj_item.cv_limit == 5
        level = list_lot_item_levels(int(lj_item.id)).iloc[0]
        assert level.target_source == 'copied_pending' and level.target_confirmed == 0
        assert level.target_mean == 10 and level.target_sd == .5
        assert get_results(batch_id).to_dict('records') == before_results
        assert dict(get_lot_config(source_id)) == before_source
        with get_connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM qc_workbench_bindings WHERE lot_config_id=?', (new_id,)).fetchone()[0] == 0


if __name__ == '__main__':
    test_reject_same_lot_without_creating_a_draft()
    test_ui_guides_to_new_draft_and_preserves_old_results()
    print('copy_batch_workflow_smoke_test passed')
