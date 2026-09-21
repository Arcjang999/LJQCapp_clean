"""CV setup, inheritance, immutable used batches, and live parameter preview."""
import math
import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import add_result, get_batch, get_connection, get_results
from pages.lj_sections import build_lj_workbench_context
from qc_logic import calculate_qc_results
from services.cv_service import calculate_cv_percent, normalize_cv_limit
from services.lot_lifecycle_service import create_target_profile
from services.project_config_io_service import build_lot_config_xlsx
from services.export_utils import xlsx_bytes_to_dataframes
from services.project_config_service import (
    create_project_template, activate_project_template, list_template_items,
    list_lot_configs, list_lot_config_items, save_lot_item_cv_requirement,
    activate_lot_config, copy_lot_config, get_lot_config,
)
from services.workbench_config_service import sync_lj_workbench_bindings
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.lot_lifecycle_smoke_test import rejected
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project
from ui.traceability import target_history_table
from zscore_logic import _safe_cv
from tests.project_workspace_smoke_test import select_table_row


def add_items_page(template_id, reagent_id):
    from pages.project_management_page import _render_add_template_items
    _render_add_template_items(template_id, reagent_id)


def create_batch_page():
    from pages.project_management_page import _render_lot_configs_tab
    _render_lot_configs_tab()


def target_page():
    from pages.lot_lifecycle_section import _render_target_versions
    _render_target_versions()
    from ui.target_profile_workspace import render_pending_target_profile_dialog
    render_pending_target_profile_dialog()


def batch_parameters_page(config_id, item_id):
    from pages.project_management_page import _render_item_level_form
    from services.project_config_service import list_lot_config_items
    items = list_lot_config_items(config_id)
    _render_item_level_form(config_id, items[items.id == item_id].iloc[0])


def test_cv_calculation_and_invalid_limits():
    assert calculate_cv_percent(100, 2) == 2
    assert calculate_cv_percent(200, 2) == 1
    assert calculate_cv_percent(100, 0) == 0
    for mean, sd in [(0, 2), (-100, 2), (math.inf, 2), (100, math.nan), (100, -2), (None, 2)]:
        assert calculate_cv_percent(mean, sd) is None
        assert _safe_cv(mean, sd) is None
    for value in [0, -1, math.inf, math.nan, "invalid"]:
        rejected(lambda: normalize_cv_limit(value), "大于 0")
    assert normalize_cv_limit(None) is None


def test_creation_to_workbench_and_live_target_preview():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid = create_project_template(template_name="CV 建立流程", lab_instrument_id=data['lab_instrument_id'],
            qc_material_id=data['qc_material_id'], default_reagent_id=data['reagent_id'])
        app = AppTest.from_function(add_items_page, args=(tid, data['reagent_id']), default_timeout=15).run()
        selected = next(x for x in app.multiselect[0].options if '单水平' in x)
        app.multiselect[0].set_value([selected]).run()
        app.number_input(key=f'v11_bulk_cv_limit_{tid}').set_value(3.5)
        app.text_input(key=f'v11_bulk_cv_source_{tid}').set_value('本实验室精密度要求')
        app.button(key=f'v11_add_items_button_{tid}').click().run()
        assert not list(app.exception)
        project_item = list_template_items(tid).iloc[0]
        assert project_item.cv_limit == 3.5
        confirm_fixture_project(tid)
        activate_project_template(tid)
        app = AppTest.from_function(create_batch_page, default_timeout=15).run()
        app.selectbox(key='batch_project_filter').set_value(tid).run()
        app.button(key='batch_create').click().run()
        assert not list(app.exception)
        material_picker = app.selectbox(key=f'create_material_{tid}_{int(project_item.id)}_0')
        material_picker.set_value(data['source_levels'][0]).run()
        cv_key = f'v12_create_cv_{tid}_{int(project_item.id)}'
        assert app.number_input(key=cv_key).value == 3.5
        app.number_input(key=cv_key).set_value(2.5)
        app.button(key='v11_create_config_button').click().run()
        assert not list(app.exception)
        config_id = int(list_lot_configs(tid).iloc[0].id)
        item_id = int(list_lot_config_items(config_id).iloc[0].id)
        assert list_lot_config_items(config_id).iloc[0].cv_limit == 2.5
        assert list_template_items(tid).iloc[0].cv_limit == 3.5
        app.button(key=f'batch_edit_levels_{item_id}').click().run()
        assert not list(app.exception)
        prefix = 'batch_level_' + app.session_state['batch_item_dialog']['token']
        level_id = data['source_levels'][0]
        app.number_input(key=prefix+'_cv').set_value(2.25)
        app.text_input(key=prefix+'_cv_source').set_value('本批次评估依据')
        app.selectbox(key=f'{prefix}_{level_id}_source').set_value('manual')
        app.number_input(key=f'{prefix}_{level_id}_mean').set_value(100)
        app.number_input(key=f'{prefix}_{level_id}_sd').set_value(2)
        app.checkbox(key=f'{prefix}_{level_id}_confirmed').check()
        app.button(key='batch_levels_save').click().run()
        assert not list(app.exception)
        assert app.session_state['v11_selected_lot_config_id'] == config_id
        assert list_lot_config_items(config_id).iloc[0].cv_limit == 2.25
        copied = copy_lot_config(source_lot_config_id=config_id, target_qc_material_lot_id=data['target_lot_id'])
        assert list_lot_config_items(copied).iloc[0].cv_limit == 2.25
        confirm_fixture_lot(config_id)
        activate_lot_config(config_id)
        sync_lj_workbench_bindings()
        with get_connection() as connection:
            binding = connection.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?', (item_id,)).fetchone()
            batch_id = binding['runtime_batch_id']
            saved_snapshot = binding['source_snapshot_json']
        assert get_batch(batch_id)['cv_limit'] == 2.25
        create_target_profile(method='lj', batch_id=batch_id,
            levels=[{'level_id': 'Level 1', 'mean': 100, 'sd': 2}], source='manual',
            evidence='本批次实验室参数确认', confirmed_by='CV 测试', effective_at='2026-09-01')
        context = build_lj_workbench_context(batch_id)
        assert context['cv_limit'] == 2.25 and context['stats']['cv'] == 2
        revision = get_lot_config(config_id)['revision_no']
        rejected(lambda: save_lot_item_cv_requirement(item_id, 9), '已启用')
        assert get_lot_config(config_id)['revision_no'] == revision
        assert get_batch(batch_id)['cv_limit'] == 2.25
        with get_connection() as connection:
            assert connection.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE lot_config_item_id=?', (item_id,)).fetchone()[0] == saved_snapshot
            history_before = connection.execute('SELECT levels_json FROM qc_target_profiles').fetchall()
            table = target_history_table(pd.read_sql_query('SELECT * FROM qc_target_profiles', connection))
        assert table['设定变异系数（%）'].tolist() == [2.0]
        export = xlsx_bytes_to_dataframes(build_lot_config_xlsx(config_id))['水平均值和标准差']
        assert export['设定变异系数（%）'].tolist() == [2]
        app = AppTest.from_function(target_page, default_timeout=15).run()
        assert not app.number_input
        select_table_row(app, 0)
        app.button(key='target_profile_open').click().run()
        prefix = 'target_profile_' + app.session_state['target_profile_dialog']['token'] + '_'
        app.number_input(key=prefix + '1_mean').set_value(100)
        app.number_input(key=prefix + '1_sd').set_value(3).run()
        assert not list(app.exception)
        assert next(x for x in app.metric if x.label == '设定变异系数（%）').value == '3.00%'
        assert any('超出' in x.value for x in app.warning)
        with get_connection() as connection:
            assert connection.execute('SELECT levels_json FROM qc_target_profiles').fetchall() == history_before
        add_result(batch_id, '2026-09-11 08:00:00', 107, operator='CV 测试')
        qc, stats = calculate_qc_results(get_results(batch_id), 20)
        assert stats['cv'] == 2
        assert '1_3s' in qc.iloc[-1]['rule_hits']
        with get_connection() as connection:
            evidence_before = connection.execute('SELECT evaluation_json FROM qc_result_evaluations').fetchall()
        app.number_input(key=prefix + '1_mean').set_value(110)
        app.text_area(key=prefix + 'evidence').set_value('修订参数')
        app.text_input(key=prefix + 'confirmed_by').set_value('CV 测试')
        app.checkbox(key=prefix + 'confirmed').set_value(True)
        app.button(key='target_profile_save').click().run()
        assert not list(app.exception)
        assert any('重新打开' in error.value for error in app.error)
        assert build_lj_workbench_context(batch_id)['stats']['mean'] == 100
        # A new result arrived while the dialog was open. Refresh the review,
        # then save explicitly; the old result keeps its original evaluation.
        app.button(key='target_profile_cancel').click().run()
        app.button(key='target_profile_discard').click().run()
        app.button(key='target_profile_open').click().run()
        prefix = 'target_profile_' + app.session_state['target_profile_dialog']['token'] + '_'
        app.number_input(key=prefix + '1_mean').set_value(110)
        app.number_input(key=prefix + '1_sd').set_value(3)
        app.text_area(key=prefix + 'evidence').set_value('修订参数')
        app.text_input(key=prefix + 'confirmed_by').set_value('CV 测试')
        app.checkbox(key=prefix + 'confirmed').set_value(True)
        app.button(key='target_profile_save').click().run()
        assert not list(app.exception)
        assert 'target_profile_dialog' not in app.session_state
        context = build_lj_workbench_context(batch_id)
        assert context['stats']['mean'] == 110
        assert math.isclose(context['stats']['cv'], 3 / 110 * 100)
        app = AppTest.from_function(batch_parameters_page, args=(config_id, item_id), default_timeout=15).run()
        assert not list(app.exception)
        parameter_table = next(table.value for table in app.dataframe if '设定均值' in table.value.columns)
        assert parameter_table.iloc[0]['设定均值'] == 110
        assert math.isclose(parameter_table.iloc[0]['设定变异系数（%）'], context['stats']['cv'])
        app = AppTest.from_function(target_page, default_timeout=15).run()
        select_table_row(app, 0)
        assert not app.number_input
        current = next(table.value for table in app.dataframe if {'均值', '标准差'}.issubset(table.value.columns))
        assert current.iloc[0]['均值'] == 110 and current.iloc[0]['标准差'] == 3
        app.button(key='target_profile_open').click().run()
        prefix = 'target_profile_' + app.session_state['target_profile_dialog']['token'] + '_'
        assert app.number_input(key=prefix + '1_mean').value == 110
        assert app.number_input(key=prefix + '1_sd').value == 3
        with get_connection() as connection:
            assert connection.execute('SELECT evaluation_json FROM qc_result_evaluations').fetchall() == evidence_before


if __name__ == '__main__':
    test_cv_calculation_and_invalid_limits()
    test_creation_to_workbench_and_live_target_preview()
    print('cv_setup_smoke_test passed')
