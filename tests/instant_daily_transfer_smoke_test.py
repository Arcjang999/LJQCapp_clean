"""Group entry follows an owned Instant-to-LJ transfer without rewriting history."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft, build_request
from services.daily_entry_service import validate_submission, submit_daily, DailyEntryError
from services.instant_service import save_instant_result, confirm_instant_transfer_to_lj
from services.lot_lifecycle_service import (workbench_systems, create_reagent_lot,
    record_lot_verification, switch_reagent_lots, create_target_profile)
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.storage_smoke_test import TemporaryStorageContext

WHEN = '2026-09-12 08:00:00'


def fixture(name='即时转入整组', *, value_type='raw', reagent=True):
    f = seed_instant_configuration(name=name, input_value_type=value_type,
        instrument=name+'仪器', reagent=name+'试剂', qc_material=name+'质控品')
    f['reagent_lot_id'] = None
    if reagent:
        with db.read_snapshot() as c:
            template_item = c.execute('SELECT source_template_item_id FROM qc_lot_config_items WHERE id=?',
                                     (f['item_id'],)).fetchone()[0]
        system = next(row for row in workbench_systems() if row['template_item_id'] == template_item)
        lot = create_reagent_lot(reagent_id=f['reagent_id'], lot_no=name+'试剂批号', expiry_date='2100-12-31')
        verification = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=lot, conclusion='pass', evidence='隔离复现的已核对试剂',
            confirmed_by='核对人', confirmed_at='2026-09-01')
        switch_reagent_lots(selections=[dict(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=lot, verification_id=verification, expected_revision=0)],
            effective_at='2026-09-01', operator='核对人', reason='隔离复现')
        f['reagent_lot_id'] = lot
    base = {'raw':100, 'ct':25, 'log':-3}[value_type]
    for index in range(20):
        value = base + .1 * index
        save_instant_result(batch_id=f['batch_id'], test_time=f'2026-09-11 08:{index:02d}:00',
            operator='原即时法检测人', value=value, log_value=None,
            lot_selection={'reagent_lot_id': f['reagent_lot_id']} if reagent else {})
    return f


def context(f, when=WHEN):
    return get_daily_context(f['instrument_id'], f['material_id'], f['lot_id'], when,
        template_id=f['template_id'], lot_config_id=f['config_id'])


def request(f, ctx, value='100.9'):
    draft = new_draft(ctx, operator='整组检测人')
    draft['values'][f"{f['item_id']}:{f['level_id']}"] = value
    draft['notes'][str(f['item_id'])] = '已核对原浓度'
    return build_request(draft)


def dump():
    with db.read_snapshot() as c:
        return '\n'.join(c.iterdump())


def instant_history(f):
    with db.read_snapshot() as c:
        return dict(results=[tuple(r) for r in c.execute('SELECT * FROM instant_results WHERE batch_id=? ORDER BY id', (f['batch_id'],))],
            bindings=[tuple(r) for r in c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?', (f['item_id'],))],
            contexts=[tuple(r) for r in c.execute('''SELECT x.* FROM qc_result_contexts x JOIN instant_results r
                ON r.id=x.instant_result_id WHERE r.batch_id=? ORDER BY x.id''', (f['batch_id'],))],
            evaluations=[tuple(r) for r in c.execute('''SELECT e.* FROM qc_result_evaluations e
                JOIN qc_result_contexts x ON x.id=e.context_id JOIN instant_results r ON r.id=x.instant_result_id
                WHERE r.batch_id=? ORDER BY e.id''', (f['batch_id'],))])


class InstantDailyTransferTests(unittest.TestCase):
    def setUp(self):
        self.storage = TemporaryStorageContext()
        self.storage.__enter__()
        db.init_db()

    def tearDown(self):
        self.storage.__exit__(None, None, None)

    def test_transfer_rejects_stale_request_and_saves_the_twenty_first_lj_result(self):
        f = fixture()
        old = context(f)
        frozen = validate_submission(request(f, old))['frozen_request']
        self.assertIsNotNone(frozen)
        moved = confirm_instant_transfer_to_lj(f['batch_id'])
        source = instant_history(f)
        before = dump()
        ctx = context(f)
        item = ctx['items'][0]
        self.assertEqual(('lj', moved['target_project_id'], moved['target_batch_id']),
            (item['qc_method'], item['runtime_project_id'], item['runtime_batch_id']))
        self.assertEqual(f['batch_id'], item['transfer_source_batch_id'])
        self.assertTrue(item['writable'], item['issues'])
        self.assertEqual(old['items'][0]['reagent_options'], item['reagent_options'])
        self.assertEqual(old['items'][0]['levels'], item['levels'])
        self.assertFalse(validate_submission(frozen)['valid'])
        with self.assertRaises(DailyEntryError):
            submit_daily(frozen)
        self.assertEqual(before, dump())
        valid = validate_submission(request(f, ctx))
        self.assertTrue(valid['valid'], valid['errors'])
        self.assertEqual(before, dump())
        receipt = submit_daily(valid['frozen_request'])
        row = receipt['items'][0]
        self.assertEqual(('lj', moved['target_batch_id'], '正式数据', 'accept'),
            (row['qc_method'], row['runtime_batch_id'], row['phase'], row['classification']))
        self.assertIsNotNone(row['target_profile_id'])
        self.assertEqual(source, instant_history(f))
        with db.read_snapshot() as c:
            self.assertEqual(21, c.execute('SELECT count(*) FROM results WHERE batch_id=?', (moved['target_batch_id'],)).fetchone()[0])
            self.assertEqual(1, c.execute('SELECT count(*) FROM qc_workbench_bindings WHERE lot_config_item_id=?', (f['item_id'],)).fetchone()[0])
            self.assertEqual(f['reagent_lot_id'], c.execute('SELECT reagent_lot_id FROM qc_result_contexts WHERE id=?', (row['context_id'],)).fetchone()[0])
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        saved = dump()
        self.assertEqual(receipt, submit_daily(valid['frozen_request']))
        self.assertEqual(saved, dump())

    def test_transferred_ct_log_and_new_parameter_version_use_the_lj_target(self):
        for value_type, value in [('ct', '25.9'), ('log', '-2.1')]:
            with self.subTest(value_type=value_type):
                f = fixture(name='转入'+value_type, value_type=value_type)
                moved = confirm_instant_transfer_to_lj(f['batch_id'])
                profile = create_target_profile(method='lj', batch_id=moved['target_batch_id'],
                    levels=[dict(level_id='Level 1', mean=float(value), sd=.5)], source='manual',
                    evidence='已核对转入后参数', confirmed_by='参数核对人', effective_at='2026-09-12 00:00:00')
                ctx = context(f)
                self.assertEqual(profile, ctx['items'][0]['target_profile']['id'])
                checked = validate_submission(request(f, ctx, value))
                self.assertTrue(checked['valid'], checked['errors'])
                source = instant_history(f)
                row = submit_daily(checked['frozen_request'])['items'][0]
                self.assertEqual(profile, row['target_profile_id'])
                self.assertEqual(float(value), row['levels'][0]['value'])
                self.assertEqual('lj', row['qc_method'])
                self.assertEqual(source, instant_history(f))

    def test_unrelated_or_broken_transfer_target_is_rejected_without_writes(self):
        first, other = fixture('原项目'), fixture('另一个项目')
        confirm_instant_transfer_to_lj(first['batch_id'])
        target = confirm_instant_transfer_to_lj(other['batch_id'])
        with db.get_connection() as c:
            c.execute('UPDATE instant_batches SET transferred_to_lj_batch_id=?,transferred_to_lj_project_id=? WHERE id=?',
                (target['target_batch_id'], target['target_project_id'], first['batch_id']))
        before = dump()
        ctx = context(first)
        self.assertFalse(ctx['items'][0]['writable'])
        self.assertTrue(any('关联不完整' in row['message'] for row in ctx['items'][0]['issues']))
        self.assertFalse(validate_submission(request(first, ctx))['valid'])
        self.assertEqual(before, dump())

    def test_target_disabled_and_missing_reagent_still_block_entry(self):
        f = fixture(reagent=False)
        moved = confirm_instant_transfer_to_lj(f['batch_id'])
        ctx = context(f)
        self.assertEqual('lj', ctx['items'][0]['qc_method'])
        self.assertFalse(ctx['items'][0]['writable'])
        self.assertEqual([], ctx['items'][0]['reagent_options'])
        self.assertFalse(validate_submission(request(f, ctx))['valid'])
        with db.get_connection() as c:
            c.execute('UPDATE batches SET is_disabled=1 WHERE id=?', (moved['target_batch_id'],))
        before = dump()
        self.assertTrue(any(row['field'] == 'binding' for row in context(f)['items'][0]['issues']))
        self.assertEqual(before, dump())
        active = fixture('已停用目标项目')
        target = confirm_instant_transfer_to_lj(active['batch_id'])
        writable = context(active)
        self.assertTrue(writable['items'][0]['writable'], writable['items'][0]['issues'])
        frozen = validate_submission(request(active, writable))['frozen_request']
        with db.get_connection() as c:
            c.execute('UPDATE projects SET is_disabled=1 WHERE id=?', (target['target_project_id'],))
        before = dump()
        self.assertFalse(context(active)['items'][0]['writable'])
        with self.assertRaises(DailyEntryError):
            submit_daily(frozen)
        self.assertEqual(before, dump())

    def test_mixed_group_saves_only_the_transferred_member_as_lj(self):
        from tests.daily_entry_smoke_test import seed_twenty
        f = seed_twenty()
        selection = dict(lab_instrument_id=f['data']['lab_instrument_id'],
            qc_material_id=f['data']['qc_material_id'], qc_material_lot_id=f['main_lot'],
            template_id=f['template_id'], lot_config_id=f['config_id'])
        old = get_daily_context(**selection, test_time=WHEN)
        member = old['items'][2]
        self.assertEqual('instant', member['qc_method'])
        for index in range(20):
            save_instant_result(batch_id=member['runtime_batch_id'], test_time=f'2026-09-11 09:{index:02d}:00',
                operator='混合项目核对', value=100+.1*index, log_value=None,
                lot_selection={'reagent_lot_id': f['reagent']})
        moved = confirm_instant_transfer_to_lj(member['runtime_batch_id'])
        current = get_daily_context(**selection, test_time=WHEN)
        draft = new_draft(current, operator='整组检测人')
        draft['selected'] = [row['row_key'] for row in current['items'][:3]]
        draft['values'] = {key: '100.9' for key in draft['values']}
        checked = validate_submission(build_request(draft))
        self.assertTrue(checked['valid'], checked['errors'])
        receipt = submit_daily(checked['frozen_request'])
        self.assertEqual(['lj', 'zscore', 'lj'], [row['qc_method'] for row in receipt['items']])
        self.assertEqual(moved['target_batch_id'], receipt['items'][2]['runtime_batch_id'])
        with db.read_snapshot() as c:
            self.assertEqual(20, c.execute('SELECT count(*) FROM instant_results WHERE batch_id=?', (member['runtime_batch_id'],)).fetchone()[0])
            self.assertEqual(21, c.execute('SELECT count(*) FROM results WHERE batch_id=?', (moved['target_batch_id'],)).fetchone()[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
