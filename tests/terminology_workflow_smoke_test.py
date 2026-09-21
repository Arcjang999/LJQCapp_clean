"""Compatibility and complete QC-lot continuity, using isolated databases."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection, add_result, get_results
from services.export_utils import xlsx_bytes_to_dataframes, dataframes_to_xlsx_bytes
from services.project_config_io_service import preview_project_template_xlsx
from services.project_config_service import copy_lot_config, activate_lot_config, get_lot_config
from tests.quality_review_fixtures import confirm_fixture_lot
from services.master_data_service import create_qc_lot, create_qc_level
from services.lot_lifecycle_service import (source_context, change_qc_lot, set_qc_usage_state,
    record_lot_verification, create_target_profile, effective_qc_state)
from services.workbench_config_service import sync_lj_workbench_bindings
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from services.instant_workbench_service import sync_instant_workbench_bindings
from services.instant_service import save_instant_result
from zscore_logic import create_zscore_run
from qc_logic import calculate_qc_results
from plotting import _filter_view_data
from tests.lot_lifecycle_smoke_test import IsolatedDatabase, lj, rejected
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.project_config_io_smoke_test import _import_workbook
from ui.common import prepare_display_records
from tests.project_workspace_smoke_test import select_table_row

ROOT = Path(__file__).resolve().parents[1]


def test_old_and_new_spreadsheet_headers_remain_equivalent():
    sheets = xlsx_bytes_to_dataframes(_import_workbook())
    expected, errors = preview_project_template_xlsx(dataframes_to_xlsx_bytes(sheets))
    assert not errors
    sheets['项目配置'] = sheets['项目配置'].rename(columns={
        '参数建立点数*': '建靶点数*', '允许不精密度(CV%)': 'CV要求(%)'})
    old, errors = preview_project_template_xlsx(dataframes_to_xlsx_bytes(sheets))
    assert not errors and old.equals(expected)
    sheets['项目配置']['参数建立点数*'] = 20
    rejected(lambda: preview_project_template_xlsx(dataframes_to_xlsx_bytes(sheets)), '同时包含旧列')


def test_display_and_new_chart_name_preserve_internal_phases_and_notes():
    with IsolatedDatabase():
        batch = lj()
        for i, value in enumerate([100, 101, 99, 100.5, 99.5, 100]):
            add_result(batch, f'2026-09-03 08:0{i}', value, operator='检测人', manual_note='原备注：建靶数据不可替换')
        qc, stats = calculate_qc_results(get_results(batch), 5)
        qc["manual_note"] = "原备注：建靶数据不可替换"
        before = qc.copy(deep=True)
        display = prepare_display_records(qc)
        assert '参数建立数据' in display['阶段'].tolist()
        assert any('建靶数据' in str(v) for v in display['备注'])
        assert qc.equals(before)
        assert _filter_view_data(qc, '参数建立图').equals(_filter_view_data(qc, '建靶图'))
        assert len(_filter_view_data(qc, '参数建立图')) == 5


def evidence():
    with get_connection() as c:
        return {table: [tuple(row) for row in c.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('results', 'zscore_runs', 'zscore_level_results', 'instant_results',
                              'qc_result_contexts', 'qc_result_evaluations')}


def test_new_lot_keeps_parallel_data_on_formal_use_for_all_methods():
    for method, count in [('lj', 1), ('zscore', 2), ('zscore', 3), ('instant', 1)]:
        with IsolatedDatabase():
            old_batch = lj() if method == 'lj' else (
                seed_zscore_configuration(level_count=count) if method == 'zscore' else seed_instant_configuration())['batch_id']
            with get_connection() as c:
                source, _, _ = source_context(c, method, old_batch)
            def save(batch, when, value):
                if method == 'lj': return add_result(batch, when, value, operator='验收')
                if method == 'instant': return save_instant_result(batch_id=batch, test_time=when, value=value, log_value=None, operator='验收')
                return create_zscore_run(batch_id=batch, test_time=when, operator='验收',
                    level_results=[{'level_id': f'Level {i}', 'raw_value': value*i} for i in range(1, count+1)],
                    template_id='2_level_classic' if count == 2 else '3_level_threes', required_n=5)
            save(old_batch, '2026-09-02', 50)
            lot = create_qc_lot(qc_material_id=source['identity'][1], lot_no='CONTINUITY-NEW', expiry_date='2028-12-31')
            for i in range(1, count+1):
                create_qc_level(qc_material_lot_id=lot, level_order=i, level_name=f'水平 {i}')
            config = change_qc_lot(source_config_id=source['lot_config_id'], target_qc_lot_id=lot,
                template_item_ids=[source['project_template_item_id']], operator='验收', reason='新批同时使用', effective_at='2026-09-03')
            confirm_fixture_lot(config)
            activate_lot_config(config)
            sync = {'lj': sync_lj_workbench_bindings, 'zscore': sync_zscore_workbench_bindings, 'instant': sync_instant_workbench_bindings}[method]
            sync()
            with get_connection() as c:
                binding = dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (config,)).fetchone())
                assert binding['runtime_batch_id'] != old_batch
                assert effective_qc_state(c, binding['lot_config_item_id'], '2026-09-03') == 'parallel'
            batch = binding['runtime_batch_id']
            for i, value in enumerate([100, 101, 99, 100.5, 99.5]):
                save(batch, f'2026-09-03 08:0{i}', value)
            if method != 'instant':
                create_target_profile(method=method, batch_id=batch,
                    levels=[{'level_id': f'Level {i}', 'mean': 100*i, 'sd': 2*i} for i in range(1, count+1)],
                    source='manual', evidence='新批自身数据核对', confirmed_by='验收', effective_at='2026-09-04')
            vid = record_lot_verification(template_item_id=source['project_template_item_id'], system_id=source['system_id'],
                qc_lot_id=lot, conclusion='pass', evidence='新批验证依据', confirmed_by='验收', confirmed_at='2026-09-04')
            before = evidence()
            set_qc_usage_state(lot_config_item_id=binding['lot_config_item_id'], state='active', effective_at='2026-09-04',
                operator='验收', reason='正式使用新批', verification_id=vid)
            assert evidence() == before
            sync()
            assert evidence() == before
            with get_connection() as c:
                after = dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (config,)).fetchone())
                assert after['runtime_batch_id'] == batch
                assert effective_qc_state(c, binding['lot_config_item_id'], '2026-09-04') == 'active'
            save(batch, '2026-09-05 08:00', 100)
            before_stop = evidence()
            set_qc_usage_state(lot_config_item_id=source['lot_config_item_id'], state='ended', effective_at='2026-09-04',
                operator='验收', reason='旧批停止使用')
            assert evidence() == before_stop
            rejected(lambda: save(old_batch, '2026-09-05', 50), '停止使用')
            with get_connection() as c:
                table = {'lj':'results','zscore':'zscore_runs','instant':'instant_results'}[method]
                assert c.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id=?', (batch,)).fetchone()[0] == 6
                assert c.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id=?', (old_batch,)).fetchone()[0] == 1


def test_existing_new_lot_links_to_review_without_duplicate_creation():
    with IsolatedDatabase():
        batch = lj()
        with get_connection() as c: source, _, _ = source_context(c, 'lj', batch)
        lot = create_qc_lot(qc_material_id=source['identity'][1], lot_no='ALREADY-CREATED', expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=lot, level_order=1, level_name='水平 1')
        config = copy_lot_config(source_lot_config_id=source['lot_config_id'], target_qc_material_lot_id=lot)
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=20)
        app.session_state['show_project_management_page'] = True
        app.session_state['v11_management_tabs'] = '批号使用与追溯'
        app.run()
        assert not app.exception
        index = next(i for i, table in enumerate(app.dataframe) if 'qc_lifecycle_config_id_table_' in table.proto.id)
        names = app.dataframe[index].value['批次'].tolist()
        select_table_row(app, names.index(get_lot_config(source['lot_config_id'])['config_name']), index=index)
        assert app.session_state['qc_lifecycle_config_id'] == source['lot_config_id']
        app.button(key='qc_lifecycle_prepare').click().run()
        prefix = 'qcl_' + app.session_state['qc_lifecycle_dialog']['token'] + '_'
        app.selectbox(key=prefix + 'target').set_value(lot).run()
        app.button(key='qcl_open_existing').click().run()
        app.button(key='qcl_discard').click().run()
        assert not app.exception
        assert app.session_state['v11_management_tabs'] == '批次管理'
        assert app.session_state['v11_selected_lot_config_id'] == config
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_lot_configs WHERE qc_material_lot_id=?', (lot,)).fetchone()[0] == 1


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
