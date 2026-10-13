"""Real configured 20-point building, formal QC, failure and Instant source chains."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import add_result, get_batch, get_connection, get_instant_batch, get_instant_results, get_results
from import_review import build_lj_building_template_dataframe, build_zscore_building_template_dataframe
from qc_logic import disable_lj_building_result
from services.export_utils import dataframe_to_csv_bytes, dataframe_to_xlsx_bytes
from services.instant_service import (
    build_instant_workbench_context, confirm_instant_transfer_to_lj,
    disable_instant_result, keep_instant_result, save_instant_result,
)
from services.lot_lifecycle_service import (
    create_reagent_lot, record_lot_verification, source_context, target_profile,
)
from services.master_data_service import (
    create_manufacturer, create_instrument_model, create_lab_instrument, create_reagent,
    create_qc_material, create_qc_lot, create_qc_level, create_method, create_test_item, list_units,
)
from services.project_config_service import (
    create_project_template, save_template_items, activate_project_template,
    create_lot_config_from_template, list_lot_config_items, save_lot_item_levels, activate_lot_config,
)
from services.value_type_service import build_level_measurement_label, get_measurement_label
from services.workbench_config_service import sync_lj_workbench_bindings
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project
from tests.zscore_v12_fixtures import seed_zscore_configuration
from ui.data_transfer import (
    _fresh_import_context, _import_source_signature, _ready, build_chart_png,
    build_data_export, commit_data_import, review_data_import,
)
from zscore_logic import (
    PHASE_FORMAL_QC, PHASE_TARGET_BUILDING, create_zscore_run,
    build_zscore_plot_dataframe, disable_zscore_building_run, get_zscore_level_targets, get_zscore_runs,
)


CASES = [('lj', 1), ('zscore', 2), ('zscore', 3)]


def configured_lj():
    manufacturer = create_manufacturer(categories=['instrument', 'reagent', 'qc_material'], display_name='20点厂商')
    model = create_instrument_model(manufacturer_id=manufacturer, generic_name='分析仪', model='20点型号')
    instrument = create_lab_instrument(instrument_model_id=model, display_name='20点仪器')
    reagent = create_reagent(manufacturer_id=manufacturer, generic_name='20点试剂')
    material = create_qc_material(manufacturer_id=manufacturer, generic_name='20点质控品', nominal_level_count=1)
    lot = create_qc_lot(qc_material_id=material, lot_no='QC-20', expiry_date='2028-12-31')
    level = create_qc_level(qc_material_lot_id=lot, level_name='水平1', level_order=1)
    unit = int(list_units().loc[lambda frame: frame.symbol == 'mg/L', 'id'].iloc[0])
    method = create_method(method_name='20点检测法')
    item = create_test_item(chinese_name='20点检测项', default_unit_id=unit)
    template = create_project_template(template_name='20点单水平', lab_instrument_id=instrument, qc_material_id=material)
    save_template_items(template, [{'test_item_id': item, 'qc_method': 'lj', 'input_value_type': 'raw',
        'unit_id': unit, 'method_id': method, 'reagent_id': reagent, 'level_count': 1, 'target_n': 20, 'cv_limit': 5.0}])
    confirm_fixture_project(template)
    activate_project_template(template)
    config = create_lot_config_from_template(template_id=template, qc_material_lot_id=lot, config_name='20点批号配置')
    config_item = int(list_lot_config_items(config).iloc[0]['id'])
    save_lot_item_levels(config_item, [{'qc_level_id': level, 'target_source': 'building'}])
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings()
    with get_connection() as connection:
        return int(connection.execute('SELECT runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                                      (config_item,)).fetchone()[0])


def configured(method, levels):
    batch = configured_lj() if method == 'lj' else seed_zscore_configuration(level_count=levels, target_n=20)['batch_id']
    return batch, verified_lot(method, batch)


def verified_lot(method, batch):
    with get_connection() as connection:
        source, _, _ = source_context(connection, method, batch)
    lot = create_reagent_lot(reagent_id=source['identity'][7], lot_no='R-20', expiry_date='2028-12-31')
    record_lot_verification(template_item_id=source['project_template_item_id'], system_id=source['system_id'],
        reagent_lot_id=lot, conclusion='pass', evidence='实验室批号验证通过', confirmed_by='核对人', confirmed_at='2026-08-01')
    return lot


def data_file(method, levels, count, *, start=0, constant=False, values=None):
    template = (build_lj_building_template_dataframe('raw') if method == 'lj' else
                build_zscore_building_template_dataframe(levels, 'raw'))
    columns = [get_measurement_label('raw')] if method == 'lj' else [
        build_level_measurement_label(f'Level {level}', 'raw') for level in range(1, levels + 1)]
    rows = []
    for index in range(count):
        row = {column: '' for column in template.columns}
        time = datetime(2026, 9, 1, 8) + timedelta(days=start + index)
        row.update({'检测时间': time.strftime('%Y-%m-%d %H:%M:%S'), '检测人': '检验员',
                    '备注': '核对原始检测记录', '实际试剂批号': 'R-20'})
        for level, column in enumerate(columns):
            row[column] = values[level] if values is not None else 100 + 50 * level + (0 if constant else (-.2 if index % 2 == 0 else .2))
        rows.append(row)
    return pd.DataFrame(rows, columns=template.columns)


def preview(method, batch, phase, frame, *, excel=False):
    context = _fresh_import_context(method, batch)
    data = dataframe_to_xlsx_bytes(frame) if excel else dataframe_to_csv_bytes(frame)
    return context, review_data_import(method, context, batch, phase, data)


def import_frame(method, batch, phase, frame, *, excel=False):
    context, review = preview(method, batch, phase, frame, excel=excel)
    assert not review['summary']['has_blocking_errors'], review['issues']
    return commit_data_import(method, batch, phase, review, _import_source_signature(method, context))


def records(method, batch):
    return get_results(batch) if method == 'lj' else get_zscore_runs(batch)


def related_counts():
    tables = ('results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
              'qc_result_context_levels', 'qc_result_evaluations', 'qc_target_profiles', 'zscore_level_targets')
    with get_connection() as connection:
        return {table: connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}


class PhaseTransitionTests(unittest.TestCase):
    def test_twenty_effective_points_then_twenty_first_normal_or_reject(self):
        for method, levels in CASES:
            for reject in (False, True):
                with self.subTest(method=method, levels=levels, reject=reject), IsolatedDatabase():
                    batch, lot = configured(method, levels)
                    self.assertEqual(20, import_frame(method, batch, 'building', data_file(method, levels, 20), excel=levels == 3))
                    context = _fresh_import_context(method, batch)
                    self.assertTrue(_ready(method, context))
                    self.assertIsNone(target_profile(method, batch))
                    if method == 'lj':
                        self.assertEqual({'建靶数据'}, set(context['qc_df'].phase))
                        means, sds = [context['stats']['mean']], [context['stats']['sd']]
                        self.assertEqual(20, context['stats']['effective_building_count'])
                    else:
                        self.assertEqual({PHASE_TARGET_BUILDING}, {run['phase'] for run in context['history_runs']})
                        targets = get_zscore_level_targets(batch, context['template_id'], required_n=20)
                        self.assertTrue(all(target['collected_n'] == 20 for target in targets.values()))
                        means = [targets[f'Level {index}']['final_target_mean'] for index in range(1, levels + 1)]
                        sds = [targets[f'Level {index}']['final_target_sd'] for index in range(1, levels + 1)]
                    values = list(means)
                    if reject:
                        values[0] += 4 * sds[0]
                    selection = {'reagent_lot_id': lot}
                    if method == 'lj':
                        add_result(batch, '2026-09-21 08:00:00', values[0], operator='正式检验员', lot_selection=selection)
                    else:
                        create_zscore_run(batch_id=batch, test_time='2026-09-21 08:00:00', operator='正式检验员',
                            level_results=[{'level_id': f'Level {index + 1}', 'raw_value': value} for index, value in enumerate(values)],
                            template_id=context['template_id'], required_n=20, lot_selection=selection)
                    context = _fresh_import_context(method, batch)
                    frozen = target_profile(method, batch)
                    self.assertEqual('building', frozen['source'])
                    self.assertEqual(20, len(json.loads(frozen['source_result_ids_json'])))
                    for index, level in enumerate(frozen['levels']):
                        self.assertAlmostEqual(means[index], level['mean'])
                        self.assertAlmostEqual(sds[index], level['sd'])
                    if method == 'lj':
                        row = context['qc_df'].iloc[-1]
                        self.assertEqual('正式数据', row['phase'])
                        self.assertEqual('失控' if reject else '符合质控', row['status'])
                        if reject:
                            self.assertIn('1_3s', row['rule_hits'])
                        context['input_value_type_label'] = get_measurement_label('raw')
                        chart_state = {'view_mode': '全图', 'y_axis_mode': '标准视图'}
                    else:
                        row = context['history_runs'][-1]
                        self.assertEqual(PHASE_FORMAL_QC, row['phase'])
                        self.assertEqual('reject' if reject else 'accept', row['run_status'])
                        self.assertEqual(levels, len(row['level_results']))
                        if reject:
                            self.assertIn('1_3s', [hit['rule_id'] for hit in row['rule_hits_run']])
                            self.assertTrue(all(level['status'] == 'accept' for level in row['level_results'][1:]))
                        context['plot_df'] = build_zscore_plot_dataframe(context['history_runs'])
                        context['input_value_type_label'] = get_measurement_label('raw')
                        chart_state = {'phase_scope': 'all', 'view_mode': '合并视图', 'y_axis_mode': '标准视图'}
                    exported = build_data_export(method, context, 'all')
                    self.assertEqual(21, len(exported))
                    self.assertEqual(2, exported['阶段'].nunique())
                    self.assertEqual({'R-20'}, set(exported['实际试剂批号']))
                    self.assertEqual(1, len(build_data_export(method, context, 'formal')))
                    before = related_counts()
                    _, wrong_use = preview(method, batch, 'formal', exported)
                    self.assertTrue(wrong_use['summary']['has_blocking_errors'])
                    self.assertEqual(before, related_counts())
                    self.assertTrue(build_chart_png(method, context, chart_state).startswith(b'\x89PNG'))
                    with get_connection() as connection:
                        column = 'lj_result_id' if method == 'lj' else 'zscore_run_id'
                        self.assertEqual(21, connection.execute(f'SELECT COUNT(*) FROM qc_result_contexts WHERE {column} IS NOT NULL AND reagent_lot_id=?', (lot,)).fetchone()[0])
                        self.assertEqual(21 * levels, connection.execute('SELECT COUNT(*) FROM qc_result_context_levels').fetchone()[0])
                        self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_disabled_point_does_not_count_and_replacement_can_complete_building(self):
        for method, levels in CASES:
            with self.subTest(method=method, levels=levels), IsolatedDatabase():
                batch, _ = configured(method, levels)
                import_frame(method, batch, 'building', data_file(method, levels, 19))
                first = int(get_results(batch).iloc[0]['id']) if method == 'lj' else get_zscore_runs(batch)[0]['id']
                (disable_lj_building_result if method == 'lj' else disable_zscore_building_run)(first)
                import_frame(method, batch, 'building', data_file(method, levels, 1, start=19))
                context = _fresh_import_context(method, batch)
                self.assertEqual(20, len(records(method, batch)))
                self.assertFalse(_ready(method, context))
                _, blocked = preview(method, batch, 'formal', data_file(method, levels, 1, start=20))
                self.assertTrue(blocked['summary']['has_blocking_errors'])
                import_frame(method, batch, 'building', data_file(method, levels, 1, start=20))
                self.assertTrue(_ready(method, _fresh_import_context(method, batch)))
                self.assertEqual(21, len(records(method, batch)))

    def test_zero_sd_does_not_produce_false_in_control(self):
        for method, levels in CASES:
            for zero_levels in sorted({1, levels}):
                with self.subTest(method=method, levels=levels, zero_levels=zero_levels), IsolatedDatabase():
                    batch, _ = configured(method, levels)
                    building = data_file(method, levels, 20)
                    for index in range(zero_levels):
                        column = get_measurement_label('raw') if method == 'lj' else build_level_measurement_label(f'Level {index + 1}', 'raw')
                        building[column] = 100 + 50 * index
                    import_frame(method, batch, 'building', building)
                    import_frame(method, batch, 'formal', data_file(method, levels, 1, start=20, constant=True))
                    context = _fresh_import_context(method, batch)
                    self.assertIsNone(target_profile(method, batch))
                    if method == 'lj':
                        self.assertEqual('无法判定（SD=0）', context['qc_df'].iloc[-1]['status'])
                        self.assertTrue(pd.isna(context['qc_df'].iloc[-1]['z']))
                    else:
                        row = context['history_runs'][-1]
                        self.assertEqual('pending', row['run_status'])
                        self.assertIn('均值或标准差无法用于判读', row['analysis_prompt'])
                        self.assertEqual([], row['rule_hits_run'])
                        for index, level in enumerate(row['level_results']):
                            if index < zero_levels:
                                self.assertIsNone(level['zscore'])
                                self.assertEqual('pending', level['status'])
                            else:
                                self.assertIsNotNone(level['zscore'])

    def test_missing_level_invalid_lot_and_bad_row_block_the_whole_file(self):
        for method, levels in CASES:
            with self.subTest(method=method, levels=levels), IsolatedDatabase():
                batch, _ = configured(method, levels)
                invalid = data_file(method, levels, 20)
                invalid.loc[10, '实际试剂批号'] = '无效批号'
                context, review = preview(method, batch, 'building', invalid)
                self.assertTrue(review['summary']['has_blocking_errors'])
                before = related_counts()
                with self.assertRaises(ValueError):
                    commit_data_import(method, batch, 'building', review, _import_source_signature(method, context))
                self.assertEqual(before, related_counts())
                if method == 'zscore':
                    missing = data_file(method, levels, 20).drop(columns=build_level_measurement_label(f'Level {levels}', 'raw'))
                    _, review = preview(method, batch, 'building', missing)
                    self.assertTrue(review['summary']['has_blocking_errors'])
                bad_value = data_file(method, levels, 20)
                column = get_measurement_label('raw') if method == 'lj' else build_level_measurement_label('Level 1', 'raw')
                bad_value[column] = bad_value[column].astype(object)
                bad_value.loc[10, column] = '未测'
                _, review = preview(method, batch, 'building', bad_value)
                self.assertTrue(review['summary']['has_blocking_errors'])
                self.assertEqual(before, related_counts())

    def test_overflow_policy_and_repeat_confirmation_remain_explicit(self):
        for method, levels in CASES:
            with self.subTest(method=method, levels=levels), IsolatedDatabase():
                batch, _ = configured(method, levels)
                context, review = preview(method, batch, 'building', data_file(method, levels, 21))
                self.assertEqual(method == 'zscore', review['summary']['has_blocking_errors'])
                self.assertTrue(any('超过' in issue['message'] for issue in review['issues']))
                self.assertFalse(any('本次版本不支持正式期导入' in issue['message'] for issue in review['issues']))
                if method == 'lj':
                    self.assertEqual(21, commit_data_import(method, batch, 'building', review, _import_source_signature(method, context)))
                    context = _fresh_import_context(method, batch)
                    self.assertEqual(1, len(context['qc_df'].loc[context['qc_df'].phase == '正式数据']))
                else:
                    import_frame(method, batch, 'building', data_file(method, levels, 20))
                    import_frame(method, batch, 'formal', data_file(method, levels, 1, start=20))
                context, repeated = preview(method, batch, 'formal', data_file(method, levels, 1, start=20))
                self.assertFalse(repeated['summary']['has_blocking_errors'])
                self.assertTrue(any(issue.get('summary_label') == '检测时间重复' for issue in repeated['issues']))
                signature = _import_source_signature(method, context)
                self.assertEqual(1, commit_data_import(method, batch, 'formal', repeated, signature))
                count = len(records(method, batch))
                with self.assertRaisesRegex(ValueError, '变化'):
                    commit_data_import(method, batch, 'formal', repeated, signature)
                self.assertEqual(count, len(records(method, batch)))

    def test_storage_failure_after_partial_loop_rolls_back_records_evidence_and_targets(self):
        for method, levels in CASES:
            with self.subTest(method=method, levels=levels), IsolatedDatabase():
                batch, _ = configured(method, levels)
                data = data_file(method, levels, 20)
                data.loc[10, '检测人'] = '失败点'
                context, review = preview(method, batch, 'building', data)
                signature = _import_source_signature(method, context)
                before = related_counts()
                table = 'results' if method == 'lj' else 'zscore_runs'
                with get_connection() as connection:
                    connection.execute(f"CREATE TRIGGER reject_partial BEFORE INSERT ON {table} WHEN NEW.operator='失败点' BEGIN SELECT RAISE(ABORT,'storage unavailable'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    commit_data_import(method, batch, 'building', review, signature)
                self.assertEqual(before, related_counts())
                with get_connection() as connection:
                    connection.execute('DROP TRIGGER reject_partial')
                self.assertEqual(20, commit_data_import(method, batch, 'building', review, signature))

    def test_instant_three_points_twenty_points_review_then_manual_lj_transfer(self):
        with IsolatedDatabase():
            fixture = seed_instant_configuration(name='20点即时法')
            batch = fixture['batch_id']
            lot = verified_lot('instant', batch)
            def entry(index, value):
                return save_instant_result(batch_id=batch, test_time=(datetime(2026, 9, 1, 8) + timedelta(minutes=index)).strftime('%Y-%m-%d %H:%M:%S'),
                    value=value, log_value=None, operator='检验员', lot_selection={'reagent_lot_id': lot})
            entry(0, 100)
            entry(1, 100.05)
            self.assertFalse(build_instant_workbench_context(batch)['summary']['si_ready'])
            entry(2, 100.1)
            self.assertTrue(build_instant_workbench_context(batch)['summary']['si_ready'])
            with self.assertRaisesRegex(ValueError, '有效点不足'):
                confirm_instant_transfer_to_lj(batch)
            for index in range(3, 19):
                entry(index, 100 + .05 * index)
            entry(19, 130)
            context = build_instant_workbench_context(batch)
            self.assertEqual(20, context['summary']['effective_count'])
            self.assertEqual(1, context['summary']['pending_outlier_review_count'])
            self.assertEqual('not_transferred', get_instant_batch(batch)['transfer_status'])
            with self.assertRaisesRegex(ValueError, '待处理疑似离群点'):
                confirm_instant_transfer_to_lj(batch)
            suspect = int(context['analysis_df'].loc[context['analysis_df'].is_outlier_suspect == 1].iloc[0]['id'])
            keep_instant_result(suspect)
            self.assertTrue(build_instant_workbench_context(batch)['transfer_state']['eligible'])
            disable_instant_result(suspect)
            self.assertEqual(19, build_instant_workbench_context(batch)['summary']['effective_count'])
            with self.assertRaisesRegex(ValueError, '有效点不足'):
                confirm_instant_transfer_to_lj(batch)
            entry(20, 100.5)
            context = build_instant_workbench_context(batch)
            self.assertTrue(context['transfer_state']['eligible'])
            before = get_instant_results(batch).to_dict('records')
            transfer = confirm_instant_transfer_to_lj(batch)
            self.assertEqual(20, transfer['building_count'])
            self.assertEqual(0, transfer['formal_count'])
            target = get_batch(transfer['target_batch_id'])
            self.assertEqual('instant', target['source_method'])
            self.assertEqual(batch, target['source_instant_batch_id'])
            effective = context['analysis_df'].loc[context['analysis_df'].is_effective == 1]
            self.assertEqual(effective.value.tolist(), get_results(target['id']).value.tolist())
            self.assertEqual(21, len(get_instant_results(batch)))
            with get_connection() as connection:
                copies = connection.execute("SELECT * FROM qc_result_contexts WHERE provenance='instant_transfer'").fetchall()
                self.assertEqual(20, len(copies))
                self.assertTrue(all(row['source_context_id'] and row['reagent_lot_id'] == lot for row in copies))
            with self.assertRaisesRegex(ValueError, '已转入 LJ'):
                entry(21, 100)
            with self.assertRaisesRegex(ValueError, '已转入'):
                confirm_instant_transfer_to_lj(batch)
            self.assertEqual([row['value'] for row in before], get_instant_results(batch).value.tolist())


if __name__ == '__main__':
    unittest.main()
