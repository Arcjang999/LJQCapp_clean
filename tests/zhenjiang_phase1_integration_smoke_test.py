"""One isolated preparation-to-result-to-backup chain across the phase-one modules."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import database as db
from services.daily_context_service import get_daily_context
from services.daily_entry_service import get_submission, submit_daily, validate_submission
from services.export_utils import dataframes_to_xlsx_bytes, dataframe_to_csv_bytes
from services.lab_preparation_service import (
    prepare_lab_preparation, save_lab_preparation, get_lab_preparation_receipt,
)
from services.lot_lifecycle_service import (
    context_dataframe, record_lot_verification, result_lot_options,
    switch_reagent_lots, usage_revision, workbench_systems,
)
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, save_qc_product_relation, validate_qc_product_choice,
)
from services.quality_review_service import validate_quality_review
from services.quick_project_setup_service import (
    get_quick_setup_context, prepare_quick_project_setup, save_quick_project_setup,
)
from services.settings_service import get_report_settings, save_report_settings_form
from services.storage_service import (
    create_database_backup, restore_database_from_backup_file, validate_sqlite_database,
)
from tests.lab_preparation_smoke_test import tables
from tests.quality_review_fixtures import fixture_conditions
from tests.quick_project_setup_smoke_test import database_contents
from tests.storage_smoke_test import TemporaryStorageContext
from ui.data_transfer import _fresh_import_context, build_data_export


METHODS = [('zscore', 2), ('lj', 1), ('instant', 1)]
WHEN = '2026-10-12 08:00:00'
RAW_VALUES = {'zscore': [110.125, 220.25], 'lj': [101.375], 'instant': [99.625]}


class ZhenjiangPhaseOneIntegration(unittest.TestCase):
    def test_preparation_receipt_drives_all_methods_lot_usage_exports_and_complete_restore(self):
        with TemporaryStorageContext() as storage:
            db.init_db()
            before_preparation = database_contents()
            workbook = dataframes_to_xlsx_bytes(tables())
            review = prepare_lab_preparation(workbook)
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(9, review['summary']['rows'])
            self.assertEqual(before_preparation, database_contents())
            preparation = save_lab_preparation(workbook,
                expected_preview_hash=review['preview_hash'], request_key='phase-one-preparation')
            ids = {row['清单编号']: row['record_id'] for row in preparation['saved_rows']}
            self.assertEqual({'U1', 'M1', 'T1', 'I1', 'R1', 'RL1', 'Q1', 'C1', 'C2'}, set(ids))
            self.assertTrue(all(isinstance(value, int) and value > 0 for value in ids.values()))
            self.assertEqual('准备实验室', get_report_settings().lab_name)
            with db.read_snapshot() as connection:
                for table in ('qc_project_templates', 'qc_project_template_items', 'qc_lot_configs', 'qc_lot_verifications'):
                    self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])
                actual_qc_lot = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                                                  (ids['C1'],)).fetchone()[0]
                self.assertEqual('000456', connection.execute('SELECT lot_no FROM md_qc_material_lots WHERE id=?',
                                                            (actual_qc_lot,)).fetchone()[0])
                self.assertEqual('000123', connection.execute('SELECT lot_no FROM md_reagent_lots WHERE id=?',
                                                            (ids['RL1'],)).fetchone()[0])

            scope = dict(reagent_id=ids['R1'], lab_instrument_id=ids['I1'],
                         test_item_id=ids['T1'], method_id=ids['M1'])
            self.assertEqual([], get_qc_product_recommendations(**scope)['candidates'])
            relation_id = save_qc_product_relation(dict(scope, qc_material_id=ids['Q1'],
                scope_note='本隔离夹具的仪器、试剂、项目及方法', evidence='单位资料核对后的适用材料记录',
                confirmed_by='隔离验收核对人'))
            candidates = get_qc_product_recommendations(**scope)
            self.assertEqual([ids['Q1']], [row['qc_material_id'] for row in candidates['candidates']])
            choice = validate_qc_product_choice(qc_material_id=ids['Q1'], **scope,
                                                expected_revision=candidates['revision'])
            self.assertEqual('recommendation', choice['basis'])
            self.assertEqual([relation_id], choice['relation_ids'])

            setups = {}
            for method, count in METHODS:
                values = dict(scope, template_name='第一批隔离贯通-' + method,
                    qc_material_id=ids['Q1'], unit_id=ids['U1'], qc_method=method,
                    input_value_type='raw', level_count=count, target_n=20, cv_limit=None,
                    quality_target_source_text='', level_ids=[ids['C1'], ids['C2']][:count],
                    config_name='第一批完整实际材料-' + method, project_group='隔离验收', notes='',
                    product_choice_review=deepcopy(choice))
                context = get_quick_setup_context(values)
                values['quality'] = dict(checked=True, mode='recorded', confirmed_by='隔离质量确认人',
                    evidence='已核对测试方案、输入尺度、单位和全部实际水平，非临床用途。',
                    source_name='隔离集成测试SOP', source_version='PHASE1-TEST-2026-01',
                    requirement_text='按合成软件验收数据检验建靶录入及判读，不代表真实临床标准。',
                    process_requirements={'source_ids': [], 'requirement_text': ''},
                    **fixture_conditions(context['item']))
                before_preview = database_contents()
                preview = prepare_quick_project_setup(values)
                self.assertEqual(before_preview, database_contents())
                self.assertEqual(count, len(preview['material_labels']))
                self.assertTrue(all('000456' in label for label in preview['material_labels']))
                receipt = save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'],
                                                    request_key='phase-one-setup-' + method)
                setups[method] = receipt
                self.assertEqual(method, receipt['binding']['qc_method'])
                self.assertEqual('active', receipt['binding']['binding_status'])
                with db.read_snapshot() as connection:
                    self.assertEqual([], validate_quality_review('project', receipt['template_item_id'], connection))
                    self.assertEqual([], validate_quality_review('lot', receipt['lot_config_item_id'], connection))
                    for table, identifier in (('qc_project_templates', receipt['template_id']),
                                              ('qc_lot_configs', receipt['lot_config_id'])):
                        self.assertEqual('active', connection.execute('SELECT status FROM ' + table + ' WHERE id=?',
                                                                     (identifier,)).fetchone()[0])
                after_save = database_contents()
                self.assertEqual(receipt, save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'],
                                                                  request_key='phase-one-setup-' + method))
                self.assertEqual(after_save, database_contents())

            systems = {row['template_item_id']: row for row in workbench_systems()}
            switches = []
            for method, _ in METHODS:
                setup = setups[method]
                system = systems[setup['template_item_id']]
                verification_id = record_lot_verification(template_item_id=setup['template_item_id'],
                    system_id=system['id'], reagent_lot_id=ids['RL1'], conclusion='pass',
                    evidence='本隔离检测系统及实际试剂批号的验收依据', confirmed_by='隔离批号确认人',
                    confirmed_at='2026-10-11 08:00:00')
                with db.read_snapshot() as connection:
                    revision = usage_revision(connection, system['id'])
                switches.append(dict(template_item_id=setup['template_item_id'], system_id=system['id'],
                    reagent_lot_id=ids['RL1'], verification_id=verification_id, expected_revision=revision))
            events = switch_reagent_lots(selections=switches, effective_at='2026-10-11 09:00:00',
                                        operator='隔离批号使用人', reason='各检测系统验证通过后使用')
            self.assertEqual(3, len(events))

            submissions, exports = {}, {}
            for method, count in METHODS:
                setup = setups[method]
                batch_id = setup['binding']['runtime_batch_id']
                options = result_lot_options(method, batch_id, WHEN)
                self.assertEqual(ids['RL1'], options['suggested_lot_id'])
                context = get_daily_context(lab_instrument_id=ids['I1'], qc_material_id=ids['Q1'],
                    qc_material_lot_id=actual_qc_lot, lot_config_id=setup['lot_config_id'], test_time=WHEN)
                self.assertEqual(1, len(context['items']))
                item = context['items'][0]
                self.assertTrue(item['writable'], item['issues'])
                self.assertEqual(method, item['qc_method'])
                self.assertEqual([ids['C1'], ids['C2']][:count], [row['qc_level_id'] for row in item['levels']])
                request = dict(submission_id='phase-one-daily-' + method, selection=context['selection'],
                    context_revision=context['context_revision'], test_time=WHEN, operator='隔离日常检测人',
                    purpose='routine', items=[dict(row_key=item['row_key'], reagent_lot_id=ids['RL1'],
                        levels=[dict(qc_level_id=level['qc_level_id'], value=str(value))
                                for level, value in zip(item['levels'], RAW_VALUES[method])])])
                before_validation = database_contents()
                validation = validate_submission(request)
                self.assertTrue(validation['valid'], validation['errors'])
                self.assertEqual(before_validation, database_contents())
                submission = submit_daily(validation['frozen_request'])
                submissions[method] = submission
                self.assertEqual(1, submission['count'])
                saved = submission['items'][0]
                self.assertEqual(RAW_VALUES[method], [row['raw_value'] for row in saved['levels']])
                self.assertEqual(['000456'] * count, [row['lot_no'] for row in saved['levels']])
                self.assertEqual([actual_qc_lot] * count, [row['qc_material_lot_id'] for row in saved['levels']])
                self.assertTrue(saved['context_id'] and saved['evaluation_id'])
                provenance = context_dataframe(method, batch_id)
                self.assertEqual(['000123'], provenance['reagent_lot_no'].tolist())
                self.assertTrue(all('000456' in value for value in provenance['qc_lot_no']))
                after_detection = database_contents()
                self.assertEqual(submission, submit_daily(validation['frozen_request']))
                self.assertEqual(after_detection, database_contents())
                if method in ('lj', 'zscore'):
                    exported = build_data_export(method, _fresh_import_context(method, batch_id), 'all')
                    self.assertEqual(1, len(exported))
                    self.assertEqual(['000123'], exported['实际试剂批号'].tolist())
                    measured = [column for column in exported if column.endswith('检测值')]
                    self.assertEqual(count, len(measured))
                    self.assertEqual(RAW_VALUES[method], [float(exported.iloc[0][column]) for column in measured])
                    csv = pd.read_csv(BytesIO(dataframe_to_csv_bytes(exported)), dtype={'实际试剂批号': str})
                    self.assertEqual(['000123'], csv['实际试剂批号'].tolist())
                    self.assertEqual(RAW_VALUES[method], [float(csv.iloc[0][column]) for column in measured])
                    exports[method] = exported
                else:
                    result = db.get_instant_results(batch_id)
                    self.assertEqual(RAW_VALUES[method], result['value'].tolist())
                    self.assertEqual(['000123'], result['actual_reagent_lot'].tolist())
                    self.assertEqual('not_transferred', db.get_instant_batch(batch_id)['transfer_status'])

            with db.read_snapshot() as connection:
                counts = {'qc_lab_preparation_receipts': 1, 'qc_quick_setup_receipts': 3,
                    'qc_product_recommendation_history': 1, 'qc_lot_verifications': 3,
                    'qc_reagent_lot_usage': 3, 'qc_daily_submissions': 3,
                    'qc_result_contexts': 3, 'zscore_runs': 1, 'zscore_level_results': 2,
                    'results': 1, 'instant_results': 1}
                for table, count in counts.items():
                    self.assertEqual(count, connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], table)
                self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())

            backup = create_database_backup(storage.root / 'complete-backups')
            self.assertTrue(backup.target_path.exists())
            self.assertFalse(backup.warnings)
            self.assertTrue(validate_sqlite_database(backup.target_path)[0])
            snapshot = database_contents()
            settings = get_report_settings()
            save_report_settings_form(dict(settings.to_payload(), lab_name='备份之后的修改'))
            with db.atomic_write() as connection:
                connection.execute('DELETE FROM qc_lab_preparation_receipts')
                connection.execute('DELETE FROM qc_quick_setup_receipts')
            self.assertNotEqual(snapshot, database_contents())
            restored = restore_database_from_backup_file(backup.target_path)
            self.assertTrue(restored.protection_backup_path.exists())
            self.assertFalse(restored.warnings)
            self.assertEqual(snapshot, database_contents())
            self.assertEqual(settings, get_report_settings())
            self.assertEqual(preparation, get_lab_preparation_receipt(preparation['receipt_id']))
            for method, _ in METHODS:
                self.assertEqual(submissions[method], get_submission('phase-one-daily-' + method))
                if method in exports:
                    actual = build_data_export(method, _fresh_import_context(method,
                        setups[method]['binding']['runtime_batch_id']), 'all')
                    pd.testing.assert_frame_equal(exports[method], actual)
            with db.read_snapshot() as connection:
                self.assertEqual(3, len(connection.execute('SELECT receipt_json FROM qc_quick_setup_receipts').fetchall()))
                self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__':
    unittest.main(verbosity=2)
