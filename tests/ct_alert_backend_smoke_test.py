"""Ct reminders retain QC calculations, source confirmation and record history."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import database as db
from services.ct_alert_service import (
    normalize_ct_alert, evaluate_ct_alert, ct_requirement_text, format_ct_number, combined_ct_status,
    annotate_lj_frame, annotate_zscore_frame, annotate_zscore_runs, annotate_runtime_frame, runtime_ct_alert,
)
from services.master_data_service import create_unit, list_units, create_qc_lot, create_qc_level
from services.material_workflow_service import create_material_config
from services.project_config_service import (activate_project_template, activate_lot_config,
    list_lot_config_items, copy_lot_config)
from services.project_workspace_service import resolve_batch_binding
from services.quality_review_service import save_pending_review, validate_quality_review
from services.quality_setup_service import save_simple_quality
from services.quality_target_service import item_context, decode
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quality_simple_setup_smoke_test import simple_values, draft, setup_args
from tests.quick_project_setup_smoke_test import database_contents
from zscore_logic import (create_zscore_run, get_zscore_runs, get_template_id_for_level_count,
                          build_zscore_plot_dataframe)


def policy(operation='<=', threshold=38):
    return dict(enabled=True, operator=operation, threshold=threshold, version=1)


def ct_draft(method='lj', levels=1):
    values = simple_values(method, levels)
    units = list_units()
    present = units.loc[units.symbol == 'Ct']
    values.update(input_value_type='ct', unit_id=(int(present.iloc[0].id) if len(present)
                                                 else create_unit(symbol='Ct')))
    values['quality']['context']['result_scale'] = 'ct'
    template, item = draft(values)
    return values, template, item


def confirm_ct_batch(method='lj', levels=1, config=None):
    values, template, item = ct_draft(method, levels)
    config = policy() if config is None else config
    save_simple_quality('project', item, **setup_args(values), ct_alert=config)
    activate_project_template(template)
    lot = create_material_config(template_id=template, selections={item: values['level_ids']})
    lot_item = int(list_lot_config_items(lot).iloc[0].id)
    save_simple_quality('lot', lot_item, **setup_args(values), ct_alert=config)
    activate_lot_config(lot)
    binding = resolve_batch_binding(lot_item)
    return values, template, item, lot_item, binding['runtime_batch_id']


class CtAlertBackendTests(unittest.TestCase):
    def test_requirement_and_chart_labels_preserve_comparison_precision(self):
        from matplotlib import pyplot as plt
        from plotting import plot_ct_requirement_line, plot_ct_requirement_lines
        configured = policy(threshold=38.00001)
        self.assertEqual('38', format_ct_number(38.0))
        self.assertEqual('38.00001', format_ct_number(38.00001))
        self.assertEqual('Ct ≤ 38.00001', ct_requirement_text(configured))
        exact = evaluate_ct_alert(38.00001, configured)
        above = evaluate_ct_alert(38.00001000000001, configured)
        self.assertFalse(exact['alert'])
        self.assertTrue(above['alert'])
        self.assertIn('实测Ct：38.00001｜Ct要求：≤38.00001', exact['message'])
        self.assertIn('实测Ct：38.00001000000001', above['message'])
        figure, axis = plt.subplots()
        try:
            plot_ct_requirement_line(axis, configured)
            plot_ct_requirement_lines(axis, pd.DataFrame({'sequence': [1, 2],
                'ct_alert_config': [configured, configured]}), None, 'sequence')
            self.assertEqual(['实验室自定 Ct 线（≤38.00001）'] * 2,
                             [line.get_label() for line in axis.lines])
            self.assertEqual(38.00001, float(axis.lines[0].get_ydata()[0]))
        finally:
            plt.close(figure)

    def test_comparison_boundaries_disabled_and_invalid_values(self):
        for operation, passing, failing in [('<', 37.99, 38), ('<=', 38, 38.01),
                                           ('>', 38.01, 38), ('>=', 38, 37.99)]:
            self.assertFalse(evaluate_ct_alert(passing, policy(operation))['alert'])
            self.assertTrue(evaluate_ct_alert(failing, policy(operation))['alert'])
        self.assertEqual('Ct ≤ 38', ct_requirement_text(policy()))
        self.assertEqual(policy('<=', 38.0), normalize_ct_alert(policy('≤', '38')))
        self.assertFalse(evaluate_ct_alert(99, None)['enabled'])
        self.assertFalse(evaluate_ct_alert(None, policy())['evaluated'])
        self.assertFalse(evaluate_ct_alert(float('nan'), policy())['evaluated'])
        for value in (float('nan'), float('inf'), True, 'abc'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '有效数值'):
                normalize_ct_alert(policy(threshold=value))
        with self.assertRaisesRegex(ValueError, 'Ct输入'):
            normalize_ct_alert(policy(), 'raw')
        with self.assertRaisesRegex(ValueError, 'Ct要求'):
            normalize_ct_alert(policy('='))
        with self.assertRaisesRegex(ValueError, 'Ct要求'):
            normalize_ct_alert(policy(['<=']))

    def test_yellow_and_red_presentations_preserve_original_rows_and_runs(self):
        source = pd.DataFrame([{'id': 1, 'value': 39, 'status': '符合质控'},
                               {'id': 2, 'value': 39, 'status': '失控'},
                               {'id': 3, 'value': 39, 'status': '待建靶'},
                               {'id': 4, 'value': 37, 'status': '符合质控'}])
        original = source.copy(deep=True)
        shown = annotate_lj_frame(source, policy())
        pd.testing.assert_frame_equal(source, original)
        self.assertEqual(source.status.tolist(), shown.status.tolist())
        self.assertEqual(['Ct提醒', '失控；Ct提醒', '待建靶；Ct提醒', '符合质控'], shown.display_status.tolist())
        self.assertEqual(['warning', 'danger', 'warning', 'success'], shown.display_tone.tolist())
        self.assertEqual(2, int((shown.status == '符合质控').sum()))
        self.assertEqual(normalize_ct_alert(policy()), shown.attrs['ct_alert'])
        runs = [{'id': 1, 'run_status': 'accept', 'level_results': [
                    {'level_id': 'Level 1', 'raw_value': 37}, {'level_id': 'Level 2', 'raw_value': 39}]},
                {'id': 2, 'run_status': 'reject', 'level_results': [{'level_id': 'Level 1', 'raw_value': 39}]}]
        before = deepcopy(runs)
        shown_runs = annotate_zscore_runs(runs, policy())
        self.assertEqual(before, runs)
        self.assertEqual('accept', shown_runs[0]['run_status'])
        self.assertEqual('Ct提醒', shown_runs[0]['display_status'])
        self.assertEqual('danger', shown_runs[1]['display_tone'])
        self.assertEqual(('待判读', 'neutral'), combined_ct_status('pending'))
        building = pd.DataFrame([{'run_id': float('nan'), 'raw_value': 37, 'status': 'accept',
                                  'phase': 'target_building'}])
        view = annotate_zscore_frame(building, policy())
        self.assertEqual('建靶中', view.iloc[0].display_status)
        self.assertEqual('neutral', view.iloc[0].display_tone)
        building_runs = annotate_zscore_runs([{'run_id': float('nan'), 'phase': 'target_building',
            'run_status': 'accept', 'level_results': [{'raw_value': 37}]}], policy(), policies={})
        self.assertEqual('建靶中', building_runs[0]['display_status'])

    def test_incomplete_drafts_atomic_confirmation_and_tamper_detection(self):
        with IsolatedDatabase():
            values = simple_values()
            _, item = draft(values)
            before = database_contents()
            with self.assertRaisesRegex(ValueError, 'Ct输入'):
                save_simple_quality('project', item, **setup_args(values), ct_alert=policy())
            self.assertEqual(before, database_contents())
        with IsolatedDatabase():
            values, _, item = ct_draft()
            incomplete = dict(enabled=True, operator='', threshold=None)
            pending = save_pending_review('project', item, context=values['quality']['context'], ct_alert=incomplete)
            self.assertIsNone(pending['ct_alert']['threshold'])
            self.assertTrue(validate_quality_review('project', item))
            before = database_contents()
            with self.assertRaisesRegex(ValueError, '请选择Ct要求'):
                save_simple_quality('project', item, **setup_args(values), ct_alert=incomplete)
            self.assertEqual(before, database_contents())
            save_simple_quality('project', item, **setup_args(values), ct_alert=policy())
            self.assertEqual([], validate_quality_review('project', item))
            with db.get_connection() as connection:
                review = decode(item_context('project', item, connection)['quality_review_json'])
                review['ct_alert']['threshold'] = 40
                connection.execute('UPDATE qc_project_template_items SET quality_review_json=? WHERE id=?',
                                   (json.dumps(review), item))
            self.assertIn('Ct要求已修改', ' '.join(validate_quality_review('project', item)))
            save_simple_quality('project', item, **setup_args(values), ct_alert=None)
            self.assertNotIn('ct_alert', decode(item_context('project', item)['quality_review_json']))

    def test_real_batch_sources_freeze_across_methods_and_project_changes(self):
        for method, levels in [('lj', 1), ('zscore', 2), ('instant', 1)]:
            with self.subTest(method=method), IsolatedDatabase():
                values, _, item, lot_item, batch = confirm_ct_batch(method, levels)
                adopted = normalize_ct_alert(policy())
                self.assertEqual(adopted, runtime_ct_alert(method, batch))
                copied = decode(item_context('lot', lot_item)['quality_review_json'])
                self.assertEqual(adopted, copied['ct_alert'])
                new_lot = create_qc_lot(qc_material_id=values['qc_material_id'],
                                       lot_no='CT-NEXT', expiry_date='2028-12-31')
                for order in range(1, levels + 1):
                    create_qc_level(qc_material_lot_id=new_lot, level_order=order, level_name=f'水平{order}')
                replacement = copy_lot_config(source_lot_config_id=item_context('lot', lot_item)['lot_config_id'],
                                              target_qc_material_lot_id=new_lot)
                replacement_item = int(list_lot_config_items(replacement).iloc[0].id)
                copied_review = decode(item_context('lot', replacement_item)['quality_review_json'])
                self.assertEqual('pending', copied_review['status'])
                self.assertEqual(adopted, copied_review['ct_alert'])
                save_simple_quality('project', item, **setup_args(values), ct_alert=policy('>=', 30))
                self.assertEqual(adopted, runtime_ct_alert(method, batch))
                before = database_contents()
                with self.assertRaisesRegex(ValueError, '已有批次'):
                    save_simple_quality('lot', lot_item, **setup_args(values), ct_alert=policy('>=', 30))
                self.assertEqual(before, database_contents())

    def test_record_snapshot_has_priority_over_batch_policy(self):
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch()
            db.add_result(batch, '2026-10-01 08:00:00', 39, operator='Ct验收人')
            with db.get_connection() as connection:
                context = connection.execute('SELECT id,config_snapshot_json,lj_result_id FROM qc_result_contexts '
                                             'WHERE lj_result_id IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
                snapshot = json.loads(context['config_snapshot_json'])
                original = deepcopy(snapshot)
                review = decode(snapshot.get('quality_review_json'))
                review.pop('ct_alert', None)
                review.pop('ct_alert_fingerprint', None)
                snapshot['quality_review_json'] = json.dumps(review)
                connection.execute('UPDATE qc_result_contexts SET config_snapshot_json=? WHERE id=?',
                                   (json.dumps(snapshot), context['id']))
            frame = pd.DataFrame([{'id': context['lj_result_id'], 'value': 39, 'status': '符合质控'}])
            shown = annotate_runtime_frame('lj', batch, frame)
            self.assertFalse(shown.iloc[0].ct_alert)
            self.assertIsNone(shown.attrs['ct_alert'])
            with db.get_connection() as connection:
                connection.execute('UPDATE qc_result_contexts SET config_snapshot_json=? WHERE id=?',
                                   (json.dumps(original), context['id']))
            self.assertTrue(annotate_runtime_frame('lj', batch, frame).iloc[0].ct_alert)

    def test_zscore_plot_uses_whole_run_rejection_without_changing_level_status(self):
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch('zscore', 2)
            template = get_template_id_for_level_count(2)
            for day in range(1, 21):
                offset = .2 if day % 2 else -.2
                create_zscore_run(batch, f'2026-10-{day:02d} 08:00:00', 'Ct验收人',
                    [{'level_id': 'Level 1', 'raw_value': 39.2 + offset},
                     {'level_id': 'Level 2', 'raw_value': 30.2 + offset}], template_id=template)
            last = create_zscore_run(batch, '2026-10-21 08:00:00', 'Ct验收人',
                [{'level_id': 'Level 1', 'raw_value': 39.2},
                 {'level_id': 'Level 2', 'raw_value': 33}], template_id=template)
            self.assertEqual('reject', last['run_status'])
            runs = get_zscore_runs(batch, template)
            plot = build_zscore_plot_dataframe(runs)
            self.assertNotIn('run_status', plot.columns)
            original = plot.copy(deep=True)
            shown = annotate_runtime_frame('zscore', batch, plot)
            pd.testing.assert_frame_equal(plot, original)
            row = shown.loc[(shown.run_id == last['run_id']) & (shown.level_id == 'Level 1')].iloc[0]
            self.assertEqual('accept', row.status)
            self.assertEqual('reject', row.run_status)
            self.assertTrue(row.ct_alert)
            self.assertEqual('danger', row.display_tone)
            self.assertEqual('失控；Ct提醒', row.display_status)
            building = shown.loc[shown.phase == 'target_building']
            self.assertTrue(all('建靶中' in value for value in building.display_status))


if __name__ == '__main__':
    unittest.main(verbosity=2)
