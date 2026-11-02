"""Daily groups select real product/lot identities and retain every SKU level."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services import daily_context_service as contexts
from services import daily_entry_service as entry
from services.lot_lifecycle_service import (create_reagent_lot, record_lot_verification,
    switch_reagent_lots, workbench_systems)
from services.quick_project_setup_service import prepare_quick_project_setup, save_quick_project_setup
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.multi_product_control_workflow_smoke_test import multi_values
from tests.quick_project_setup_smoke_test import database_contents


WHEN = '2026-10-20 08:00:00'


def seed(count=3, *, unused_header=False):
    values = multi_values(3 if unused_header else count)
    prepared = prepare_quick_project_setup(values)
    saved = save_quick_project_setup(values, expected_preview_hash=prepared['preview_hash'], request_key='daily-multi')
    if unused_header:
        from services.master_data_service import create_qc_level
        from services.material_workflow_service import create_material_config
        from services.project_config_service import activate_lot_config, list_lot_config_items
        from services.zscore_workbench_service import sync_zscore_workbench_bindings
        from tests.quality_review_fixtures import confirm_fixture_lot
        with db.read_snapshot() as c:
            second_lot = c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                                  (values['level_ids'][1],)).fetchone()[0]
        third_level = create_qc_level(qc_material_lot_id=second_lot, level_name='另一实际浓度',
                                     level_code='004', level_order=2)
        values['level_ids'] = [*values['level_ids'][1:], third_level]
        values['level_product_ids'] = [*values['level_product_ids'][1:], values['level_product_ids'][1]]
        config = create_material_config(template_id=saved['template_id'],
            selections={saved['template_item_id']: values['level_ids']})
        confirm_fixture_lot(config)
        activate_lot_config(config)
        sync_zscore_workbench_bindings()
        saved['lot_config_id'] = config
        saved['lot_config_item_id'] = int(list_lot_config_items(config).iloc[0]['id'])
    reagent = create_reagent_lot(reagent_id=values['reagent_id'], lot_no='DAILY-MULTI-R', expiry_date='2028-12-31')
    selections = []
    for system in workbench_systems():
        verification = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, conclusion='pass', evidence='逐产品整组录入测试',
            confirmed_by='隔离核对人', confirmed_at='2026-10-01')
        selections.append(dict(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, verification_id=verification, expected_revision=0))
    switch_reagent_lots(selections=selections, effective_at='2026-10-01', operator='隔离核对人', reason='整组回归')
    with db.read_snapshot() as c:
        lots = [c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (level,)).fetchone()[0]
                for level in values['level_ids']]
    return dict(values=values, saved=saved, reagent=reagent, lots=lots)


def context(fixture, slot=0, **changes):
    values, saved = fixture['values'], fixture['saved']
    selection = dict(lab_instrument_id=values['lab_instrument_id'],
        qc_material_id=values['level_product_ids'][slot], qc_material_lot_id=fixture['lots'][slot],
        template_id=saved['template_id'], lot_config_id=saved['lot_config_id'], test_time=WHEN)
    selection.update(changes)
    return contexts.get_daily_context(**selection)


def request(fixture, slot=0):
    ctx = context(fixture, slot)
    return dict(submission_id='daily-multi-save', selection=ctx['selection'],
        context_revision=ctx['context_revision'], test_time=WHEN, operator='整组检测人', purpose='routine',
        items=[dict(row_key=item['row_key'], lot_config_item_id=item['lot_config_item_id'],
            reagent_lot_id=fixture['reagent'], levels=[dict(qc_level_id=level['qc_level_id'],
                value=str(100 * level['level_order'])) for level in item['levels']]) for item in ctx['items']])


class MultiProductDailyEntryTests(unittest.TestCase):
    def test_every_actual_product_selects_the_complete_two_or_three_level_run_readonly(self):
        for count in (2, 3):
            with self.subTest(count=count), IsolatedDatabase():
                fixture = seed(count)
                before = database_contents()
                choices = contexts.list_daily_choices(fixture['saved']['template_id'])
                self.assertEqual(set(fixture['values']['level_product_ids']), {r['id'] for r in choices['materials']})
                self.assertEqual(set(fixture['lots']), {r['id'] for r in choices['lots']})
                for slot in range(count):
                    ctx = context(fixture, slot)
                    self.assertEqual([], ctx['issues'])
                    self.assertEqual(1, len(ctx['items']))
                    item = ctx['items'][0]
                    self.assertTrue(item['writable'], item['issues'])
                    self.assertEqual(fixture['values']['level_ids'], [r['qc_level_id'] for r in item['levels']])
                    self.assertEqual(fixture['values']['level_product_ids'], [r['qc_material_id'] for r in item['levels']])
                    self.assertEqual([f'SKU-{n:03d}' for n in range(1, count + 1)], [r['catalog_no'] for r in item['levels']])
                self.assertEqual(before, database_contents())

    def test_nonheader_product_saves_one_complete_run_and_missing_slot_writes_nothing(self):
        with IsolatedDatabase():
            fixture = seed()
            payload = request(fixture, 2)
            incomplete = deepcopy(payload)
            incomplete['items'][0]['levels'].pop(1)
            before = database_contents()
            denied = entry.validate_submission(incomplete)
            self.assertFalse(denied['valid'])
            self.assertTrue(any(r['field'] == 'value' for r in denied['errors']))
            self.assertEqual(before, database_contents())
            checked = entry.validate_submission(payload)
            self.assertTrue(checked['valid'], checked['errors'])
            receipt = entry.submit_daily(checked['frozen_request'])
            self.assertEqual(1, receipt['count'])
            with db.read_snapshot() as c:
                self.assertEqual(3, c.execute('SELECT COUNT(*) FROM zscore_level_results').fetchone()[0])
                snapshot = json.loads(c.execute('SELECT config_snapshot_json FROM qc_result_contexts').fetchone()[0])
            self.assertEqual(fixture['values']['level_product_ids'], [r['qc_material_id'] for r in snapshot['levels']])
            self.assertEqual(['SKU-001', 'SKU-002', 'SKU-003'], [r['catalog_no'] for r in snapshot['levels']])
            self.assertEqual(receipt, entry.submit_daily(checked['frozen_request']))

    def test_product_lot_mismatch_foreign_configuration_and_foreign_item_are_rejected(self):
        with IsolatedDatabase():
            fixture = seed(2)
            mismatch = context(fixture, 1, qc_material_lot_id=fixture['lots'][0])
            self.assertEqual([], mismatch['items'])
            self.assertTrue(mismatch['issues'])
            foreign_values = deepcopy(fixture['values'])
            foreign_values['template_name'] += '-另一项目'
            preview = prepare_quick_project_setup(foreign_values)
            foreign = save_quick_project_setup(foreign_values,
                expected_preview_hash=preview['preview_hash'], request_key='foreign-daily-multi')
            wrong_config = context(fixture, 1, lot_config_id=foreign['lot_config_id'])
            self.assertEqual([], wrong_config['items'])
            self.assertTrue(wrong_config['issues'])
            payload = request(fixture, 1)
            payload['items'][0]['row_key'] = str(foreign['lot_config_item_id'])
            payload['items'][0]['lot_config_item_id'] = foreign['lot_config_item_id']
            before = database_contents()
            rejected = entry.validate_submission(payload)
            self.assertFalse(rejected['valid'])
            self.assertTrue(any(r['field'] == 'row_key' for r in rejected['errors']))
            self.assertEqual(before, database_contents())

    def test_disabled_actual_secondary_product_blocks_fresh_and_prevalidated_submission(self):
        with IsolatedDatabase():
            fixture = seed()
            checked = entry.validate_submission(request(fixture))
            self.assertTrue(checked['valid'], checked['errors'])
            with db.get_connection() as c:
                c.execute('UPDATE md_qc_materials SET is_disabled=1 WHERE id=?',
                          (fixture['values']['level_product_ids'][1],))
            before = database_contents()
            ctx = context(fixture)
            self.assertFalse(ctx['items'][0]['writable'])
            self.assertTrue(any(r['field'] == 'material' and r['qc_level_id'] == fixture['values']['level_ids'][1]
                                for r in ctx['items'][0]['issues']))
            self.assertFalse(entry.validate_submission(request(fixture))['valid'])
            with self.assertRaises(entry.DailyEntryError):
                entry.submit_daily(checked['frozen_request'])
            self.assertEqual(before, database_contents())

    def test_unused_header_product_does_not_replace_actual_product_status(self):
        with IsolatedDatabase():
            fixture = seed(unused_header=True)
            with db.get_connection() as c:
                c.execute('UPDATE md_qc_materials SET is_disabled=1 WHERE id=?', (fixture['values']['qc_material_id'],))
            ctx = context(fixture)
            self.assertEqual(1, len(ctx['items']))
            self.assertTrue(ctx['items'][0]['writable'], ctx['items'][0]['issues'])
            checked = entry.validate_submission(request(fixture))
            self.assertTrue(checked['valid'], checked['errors'])
            self.assertEqual(1, entry.submit_daily(checked['frozen_request'])['count'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
