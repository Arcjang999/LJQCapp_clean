"""Ct reminders follow actual saved measurements without changing QC statistics."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.ct_alert_service import annotate_runtime_frame, annotate_runtime_runs, runtime_ct_alert
from services.lot_lifecycle_service import (create_target_profile, workbench_systems, create_reagent_lot,
    record_lot_verification, switch_reagent_lots)
from services.quality_setup_service import save_simple_quality
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft, build_request
from services.daily_entry_service import validate_submission, submit_daily
from services.daily_overview_service import get_daily_overview
from services.record_maintenance_service import get_record_context
from services.instant_service import save_instant_result, build_instant_workbench_context, confirm_instant_transfer_to_lj
from tests.ct_alert_backend_smoke_test import confirm_ct_batch, policy
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quality_simple_setup_smoke_test import setup_args


def targets(method, batch, count=1):
    return create_target_profile(method=method, batch_id=batch,
        levels=[dict(level_id=f'Level {i}', mean=38, sd=1) for i in range(1, count+1)],
        source='manual', evidence='隔离参数用于验证Ct独立提醒', confirmed_by='核对人', effective_at='2026-09-01')


def saved(method, record):
    column = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[method]
    with db.read_snapshot() as connection:
        row = connection.execute('''SELECT e.evaluation_json,x.config_snapshot_json FROM qc_result_evaluations e
            JOIN qc_result_contexts x ON x.id=e.context_id WHERE x.''' + column +
            '=? ORDER BY e.id DESC LIMIT 1', (record,)).fetchone()
    body = json.loads(row['evaluation_json'])
    return body.get('result', body), json.loads(row['config_snapshot_json'])


class CtAlertLinkageTests(unittest.TestCase):
    def test_lj_saved_reminder_red_priority_history_and_daily_overview(self):
        with IsolatedDatabase():
            values, template, item, _, batch = confirm_ct_batch()
            targets('lj', batch)
            ids = [db.add_result(batch, f'2026-10-01 08:0{i}:00', value, operator='Ct核对人')
                   for i, value in enumerate((38, 39, 45))]
            from pages.lj_sections import build_lj_workbench_context
            context = build_lj_workbench_context(batch)
            frame = context['qc_df']
            self.assertEqual(['符合质控', '符合质控', '失控'], frame.status.tolist())
            self.assertEqual(['符合质控', 'Ct提醒', '失控；Ct提醒'], frame.display_status.tolist())
            self.assertEqual(['success', 'warning', 'danger'], frame.display_tone.tolist())
            judged, source = saved('lj', ids[1])
            self.assertEqual('符合质控', judged['status'])
            self.assertTrue(judged['ct_evaluation']['alert'])
            self.assertEqual(38, judged['ct_evaluation']['config']['threshold'])
            self.assertEqual(38, json.loads(source['quality_review_json'])['ct_alert']['threshold'])
            save_simple_quality('project', item, **setup_args(values), ct_alert=policy('>=', 50))
            again = build_lj_workbench_context(batch)
            self.assertEqual(frame.display_status.tolist(), again['qc_df'].display_status.tolist())
            detail = get_record_context('lj', ids[1])
            self.assertEqual('Ct提醒', detail['evaluation']['result']['display_status'])
            overview = get_daily_overview('2026-10-01', template_id=template)
            record = next(row for group in overview['items'] for row in group['records'] if row['source_id'] == ids[1])
            self.assertEqual('accept', record['classification'])
            self.assertEqual('Ct提醒', record['conclusion'])

    def test_multilevel_run_reminder_does_not_change_run_acceptance(self):
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch('zscore', 2)
            targets('zscore', batch, 2)
            from zscore_logic import create_zscore_run, get_zscore_runs, get_template_id_for_level_count
            run = create_zscore_run(batch_id=batch, test_time='2026-10-01 08:00:00', operator='Ct核对人',
                level_results=[dict(level_id='Level 1', raw_value=37), dict(level_id='Level 2', raw_value=39)],
                template_id=get_template_id_for_level_count(2), required_n=20)
            judged, _ = saved('zscore', run['id'])
            self.assertEqual('accept', judged['run_status'])
            self.assertEqual([False, True], [level['ct_evaluation']['alert'] for level in judged['level_results']])
            shown = annotate_runtime_runs(batch, get_zscore_runs(batch))
            self.assertEqual('Ct提醒', shown[0]['display_status'])
            self.assertEqual('accept', shown[0]['run_status'])

    def test_instant_to_lj_retains_ct_source_and_original_measurements(self):
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch('instant')
            for i in range(20):
                save_instant_result(batch_id=batch, test_time=f'2026-10-01 08:{i:02d}:00',
                    operator='Ct核对人', value=37.5 + i % 2, log_value=None)
            before = db.get_instant_results(batch).value.tolist()
            context = build_instant_workbench_context(batch)
            self.assertTrue(context['latest_row']['ct_alert'])
            self.assertEqual('warning', context['latest_row']['display_tone'])
            moved = confirm_instant_transfer_to_lj(batch)
            target = moved['target_batch_id']
            self.assertEqual(policy(), runtime_ct_alert('lj', target))
            self.assertEqual(before, db.get_results(target).value.tolist())
            self.assertEqual(before, db.get_instant_results(batch).value.tolist())
            shown = annotate_runtime_frame('lj', target, db.get_results(target))
            self.assertEqual(10, int(shown.ct_alert.sum()))

    def test_group_receipt_uses_ct_reminder_while_classification_stays_accept(self):
        with IsolatedDatabase():
            values, template, template_item, lot_item, batch = confirm_ct_batch()
            targets('lj', batch)
            system = next(row for row in workbench_systems() if row['template_item_id'] == template_item)
            reagent = create_reagent_lot(reagent_id=values['reagent_id'], lot_no='CT-GROUP-REAGENT', expiry_date='2100-12-31')
            verification = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
                reagent_lot_id=reagent, conclusion='pass', evidence='隔离核对', confirmed_by='核对人', confirmed_at='2026-09-01')
            switch_reagent_lots(selections=[dict(template_item_id=system['template_item_id'], system_id=system['id'],
                reagent_lot_id=reagent, verification_id=verification, expected_revision=0)],
                effective_at='2026-09-01', operator='核对人', reason='隔离核对')
            with db.read_snapshot() as connection:
                lot_id = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                    (values['level_ids'][0],)).fetchone()[0]
                config_id = connection.execute('SELECT lot_config_id FROM qc_lot_config_items WHERE id=?', (lot_item,)).fetchone()[0]
            current = get_daily_context(values['lab_instrument_id'], values['qc_material_id'], lot_id,
                '2026-10-01 08:00:00', template_id=template, lot_config_id=config_id)
            draft = new_draft(current, operator='Ct整组核对人')
            for key in draft['values']:
                draft['values'][key] = '39'
            checked = validate_submission(build_request(draft))
            self.assertTrue(checked['valid'], checked['errors'])
            receipt = submit_daily(checked['frozen_request'])
            result = receipt['items'][0]
            self.assertEqual('accept', result['classification'])
            self.assertEqual('Ct提醒', result['conclusion'])
            self.assertEqual('warning', result['display_tone'])

    def test_instant_suspect_stays_yellow_across_receipt_workbench_and_overview(self):
        from services.ct_alert_service import combined_ct_status
        from ui.record_workspace import style_judgment_columns
        import pandas as pd
        with IsolatedDatabase():
            values, template, template_item, lot_item, batch = confirm_ct_batch('instant')
            for minute in range(3):
                save_instant_result(batch_id=batch, test_time=f'2026-10-01 08:0{minute}:00',
                    operator='Ct核对人', value=37, log_value=None)
            system = next(row for row in workbench_systems() if row['template_item_id'] == template_item)
            reagent = create_reagent_lot(reagent_id=values['reagent_id'], lot_no='CT-INSTANT-COLOR', expiry_date='2100-12-31')
            verification = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
                reagent_lot_id=reagent, conclusion='pass', evidence='隔离核对', confirmed_by='核对人', confirmed_at='2026-09-01')
            switch_reagent_lots(selections=[dict(template_item_id=system['template_item_id'], system_id=system['id'],
                reagent_lot_id=reagent, verification_id=verification, expected_revision=0)],
                effective_at='2026-09-01', operator='核对人', reason='隔离核对')
            with db.read_snapshot() as connection:
                lot_id = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                    (values['level_ids'][0],)).fetchone()[0]
                config_id = connection.execute('SELECT lot_config_id FROM qc_lot_config_items WHERE id=?', (lot_item,)).fetchone()[0]
            current = get_daily_context(values['lab_instrument_id'], values['qc_material_id'], lot_id,
                '2026-10-01 08:03:00', template_id=template, lot_config_id=config_id)
            draft = new_draft(current, operator='Ct整组核对人')
            for key in draft['values']:
                draft['values'][key] = '39'
            checked = validate_submission(build_request(draft))
            self.assertTrue(checked['valid'], checked['errors'])
            receipt = submit_daily(checked['frozen_request'])['items'][0]
            self.assertEqual('suspect', receipt['classification'])
            self.assertEqual('warning', receipt['display_tone'])
            self.assertIn('疑似离群', receipt['conclusion'])
            self.assertTrue(receipt['ct_alert'])
            judged, _ = saved('instant', receipt['result_id'])
            self.assertEqual('疑似离群', judged['status'])
            self.assertEqual('warning', judged['display_tone'])
            latest = build_instant_workbench_context(batch)['latest_row']
            self.assertEqual('疑似离群；Ct提醒', latest['display_status'])
            self.assertEqual('warning', latest['display_tone'])
            overview = get_daily_overview('2026-10-01', template_id=template)
            record = next(row for group in overview['items'] for row in group['records']
                          if row['source_id'] == receipt['result_id'])
            self.assertEqual('warning', record['display_tone'])
            self.assertIn('疑似离群', record['conclusion'])
            table = style_judgment_columns(pd.DataFrame({'判读': [latest['display_status'], '失控；Ct提醒']}), ['判读'])
            styles = table._compute().ctx
            self.assertIn(('background-color', '#fff6db'), styles[(0, 0)])
            self.assertIn(('background-color', '#fdeaea'), styles[(1, 0)])
        self.assertEqual(('疑似离群', 'warning'), combined_ct_status('疑似离群'))
        self.assertEqual(('失控；Ct提醒', 'danger'), combined_ct_status('reject', True))

    def test_result_exports_keep_original_qc_and_import_source_signatures_match(self):
        from ui.data_transfer import build_data_export, _fresh_import_context, _import_source_signature
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch()
            targets('lj', batch)
            db.add_result(batch, '2026-10-01 08:00:00', 39, operator='Ct核对人')
            from pages.lj_sections import build_lj_workbench_context
            frame = build_data_export('lj', build_lj_workbench_context(batch), 'formal')
            self.assertEqual('符合质控', frame.iloc[0]['判定结果'])
            self.assertEqual('Ct提醒', frame.iloc[0]['判读'])
            self.assertEqual('触发', frame.iloc[0]['Ct提醒'])
            self.assertFalse(any('ct_' in str(column) for column in frame))
        with IsolatedDatabase():
            _, _, _, _, batch = confirm_ct_batch('zscore', 2)
            targets('zscore', batch, 2)
            from zscore_logic import create_zscore_run, get_template_id_for_level_count
            create_zscore_run(batch_id=batch, test_time='2026-10-01 08:00:00', operator='Ct核对人',
                level_results=[dict(level_id='Level 1', raw_value=37), dict(level_id='Level 2', raw_value=39)],
                template_id=get_template_id_for_level_count(2), required_n=20)
            from pages.zscore_sections import build_zscore_workbench_context
            context = build_zscore_workbench_context(batch)
            current = _fresh_import_context('zscore', batch)
            self.assertEqual(_import_source_signature('zscore', context), _import_source_signature('zscore', current))
            frame = build_data_export('zscore', context, 'formal')
            self.assertEqual('在控', frame.iloc[0]['本次检测判定结果'])
            self.assertEqual('Ct提醒', frame.iloc[0]['判读'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
