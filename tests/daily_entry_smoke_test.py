"""Fresh 20-assay engineering fixtures; original methods, complete runs and real rollback."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services import daily_context_service as contexts
from services import daily_entry_service as entry


def seed_twenty():
    from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
    from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_lot
    from services.master_data_service import create_test_item
    from services.material_workflow_service import register_control_material, create_material_config
    from services.project_config_service import (create_project_template, save_template_items, list_template_items,
        activate_project_template, activate_lot_config)
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    from services.lot_lifecycle_service import (workbench_systems, create_reagent_lot,
        record_lot_verification, switch_reagent_lots)
    d = _seed_v11_configuration_dependencies()
    levels = [register_control_material(material_id=d['qc_material_id'], level_name=name, level_code=str(n),
        lot_no=f'DAILY-MATERIAL-{n}', expiry_date='2100-12-31') for n, name in enumerate(('低值', '中值', '高值'), 1)]
    tid = create_project_template(template_name='日常20项工程验收', lab_instrument_id=d['lab_instrument_id'],
        qc_material_id=d['qc_material_id'], default_reagent_id=d['reagent_id'])
    items = []
    for index in range(20):
        method = ('lj', 'zscore', 'instant')[index % 3]
        value_type = ('raw', 'ct', 'log')[index // 3 % 3]
        test = create_test_item(chinese_name=f'日常工程测试项目{index+1:02d}', default_unit_id=d['unit_id'])
        items.append(dict(test_item_id=test, qc_method=method, input_value_type=value_type, unit_id=d['unit_id'],
            method_id=d['method_id'], reagent_id=d['reagent_id'], level_count=(2 + index % 2) if method == 'zscore' else 1,
            target_n=20 if method == 'instant' else 5, cv_limit=None, sort_order=index))
    save_template_items(tid, items)
    confirm_fixture_project(tid)
    activate_project_template(tid)
    template_items = list_template_items(tid).to_dict('records')
    config = create_material_config(template_id=tid,
        selections={r['id']: levels[:r['level_count']] for r in template_items})
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings(); sync_zscore_workbench_bindings(); sync_instant_workbench_bindings()
    systems = workbench_systems()
    reagent = create_reagent_lot(reagent_id=d['reagent_id'], lot_no='DAILY-REAGENT-01', expiry_date='2100-12-31')
    selections = []
    for system in systems:
        vid = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, conclusion='pass', evidence='合成工程样例的资料核对', confirmed_by='工程验收', confirmed_at='2026-09-01')
        selections.append(dict(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, verification_id=vid, expected_revision=0))
    switch_reagent_lots(selections=selections, effective_at='2026-09-01', operator='工程验收', reason='合成工程样例')
    with db.get_connection() as c:
        main_lot = c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (levels[0],)).fetchone()[0]
    return dict(data=d, template_id=tid, config_id=config, levels=levels, main_lot=main_lot, reagent=reagent)


class DailyEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = db.DB_PATH, db.LEGACY_DB_CANDIDATES
        cls.base = TemporaryDirectory()
        db.DB_PATH = Path(cls.base.name) / 'base.db'; db.LEGACY_DB_CANDIDATES = []
        db.init_db(); cls.fixture = seed_twenty(); cls.base_path = db.DB_PATH

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH, db.LEGACY_DB_CANDIDATES = cls.old
        cls.base.cleanup()

    def setUp(self):
        self.tmp = TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / 'test.db'
        shutil.copyfile(self.base_path, db.DB_PATH)

    def tearDown(self):
        self.tmp.cleanup()

    def dump(self):
        with db.read_snapshot() as c:
            return '\n'.join(c.iterdump())

    def context(self, when='2026-09-28 08:00:00', **changes):
        f = self.fixture
        selection = dict(lab_instrument_id=f['data']['lab_instrument_id'], qc_material_id=f['data']['qc_material_id'],
            qc_material_lot_id=f['main_lot'], test_time=when, lot_config_id=f['config_id'])
        selection.update(changes)
        return contexts.get_daily_context(**selection)

    def request(self, identifier='group', when='2026-09-28 08:00:00', delta=0):
        ctx = self.context(when)
        return dict(submission_id=identifier, selection=ctx['selection'], context_revision=ctx['context_revision'],
            test_time=when, operator='整组工程验收', purpose='routine', items=[dict(row_key=item['row_key'],
                lot_config_item_id=item['lot_config_item_id'], reagent_lot_id=self.fixture['reagent'],
                levels=[dict(qc_level_id=level['qc_level_id'], value=str(100 + level['level_order'] * 10 + delta)) for level in item['levels']])
                for item in ctx['items']])

    def validated(self, request=None):
        result = entry.validate_submission(request or self.request())
        self.assertTrue(result['valid'], result['errors'])
        return result['frozen_request']

    def denied(self, action, message=None):
        before = self.dump()
        with self.assertRaises(entry.DailyEntryError) as error:
            action()
        self.assertEqual(before, self.dump())
        if message:
            self.assertIn(message, str(error.exception))
        return error.exception

    def test_read_only_exact_materials_revisions_and_missing_binding(self):
        before = self.dump()
        with (patch('services.lot_lifecycle_service.source_context', side_effect=AssertionError('query wrote source')),
              patch('services.project_workspace_service.resolve_batch_binding', side_effect=AssertionError('query created binding'))):
            first = self.context(); second = self.context(); self.validated()
        self.assertEqual(first, second)
        self.assertEqual(20, len(first['items']))
        self.assertEqual(before, self.dump())
        z = next(r for r in first['items'] if r['qc_method'] == 'zscore' and r['level_count'] == 3)
        self.assertEqual(3, len({r['qc_material_lot_id'] for r in z['levels']}))
        self.assertEqual([], self.context(lab_instrument_id=999)['items'])
        with db.get_connection() as c:
            c.execute('DELETE FROM qc_workbench_bindings WHERE lot_config_item_id=?', (z['lot_config_item_id'],))
        before = self.dump()
        missing = self.context()
        row = next(r for r in missing['items'] if r['row_key'] == z['row_key'])
        self.assertFalse(row['writable']); self.assertIsNone(row['runtime_batch_id'])
        self.assertEqual(before, self.dump())

    def test_explicit_combination_and_exact_primary_lot(self):
        from services.material_workflow_service import copy_material_config
        with db.get_connection() as c:
            item_ids = [r[0] for r in c.execute('SELECT id FROM qc_lot_config_items WHERE lot_config_id=?', (self.fixture['config_id'],))]
            different_level = self.fixture['data']['target_levels'][0]
        # Change only one LJ material: same main lot still matches other items,
        # so two real complete configurations require explicit selection.
        copied = copy_material_config(source_config_id=self.fixture['config_id'], selections={item_ids[0]: [different_level]})
        choices = self.context(lot_config_id=None)
        # The copied config intentionally contains only the replaced item, so it
        # does not match the old primary lot at all.
        self.assertFalse(choices['requires_combination'])
        self.assertEqual(20, len(choices['items']))
        with db.get_connection() as c:
            c.execute('''UPDATE qc_lot_config_item_levels SET qc_level_id=? WHERE lot_config_item_id=
                (SELECT id FROM qc_lot_config_items WHERE lot_config_id=? AND is_enabled=1)''', (self.fixture['levels'][0], copied))
        choices = self.context(lot_config_id=None)
        self.assertTrue(choices['requires_combination']); self.assertEqual([], choices['items'])
        self.assertEqual(20, len(self.context()['items']))

    def test_collective_errors_no_writes_and_value_types(self):
        request = self.request()
        request['items'][2]['levels'][0]['value'] = ''
        request['items'][19]['levels'].pop()
        with db.get_connection() as c:
            c.execute("UPDATE md_qc_material_lots SET expiry_date='2020-01-01' WHERE id=(SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?)", (self.fixture['levels'][1],))
        before = self.dump()
        result = entry.validate_submission(request)
        self.assertFalse(result['valid']); self.assertEqual(before, self.dump())
        errors = {(r['row_key'], r['field']) for r in result['errors']}
        self.assertIn((request['items'][2]['row_key'], 'value'), errors)
        self.assertIn((request['items'][7]['row_key'], 'expiry_date'), errors)
        self.assertIn((request['items'][19]['row_key'], 'value'), errors)
        for bad in ('nan', 'inf', '-inf'):
            request['items'][0]['levels'][0]['value'] = bad
            self.assertTrue(any(r['field'] == 'value' for r in entry.validate_submission(request)['errors']))

    def test_twenty_atomic_success_retry_concurrency_and_new_measurement(self):
        frozen = self.validated()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: entry.submit_daily(frozen), range(2)))
        self.assertEqual(receipts[0], receipts[1]); self.assertEqual(20, receipts[0]['count'])
        before = self.dump(); self.assertEqual(receipts[0], entry.submit_daily(frozen)); self.assertEqual(before, self.dump())
        with db.get_connection() as c:
            self.assertEqual(20, c.execute('SELECT COUNT(*) FROM qc_daily_submission_items').fetchone()[0])
            self.assertEqual(20, c.execute('SELECT COUNT(*) FROM qc_result_contexts').fetchone()[0])
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM qc_ooc_events').fetchone()[0])
            for row in receipts[0]['items']:
                if row['qc_method'] == 'zscore':
                    self.assertEqual(row['level_count'], c.execute('SELECT COUNT(*) FROM zscore_level_results WHERE run_id=?', (row['source_id'],)).fetchone()[0])
        changed = deepcopy(frozen); changed['items'][0]['levels'][0]['value'] = '999'
        changed['validation_hash'] = contexts._digest({k:v for k,v in changed.items() if k != 'validation_hash'})
        self.denied(lambda: entry.submit_daily(changed), '不同内容')
        same = self.request('same-time')
        self.assertFalse(entry.validate_submission(same)['valid'])
        for row in same['items']: row['allow_same_time'] = True
        self.assertEqual(20, entry.submit_daily(self.validated(same))['count'])
        self.assertEqual(20, entry.submit_daily(self.validated(self.request('next', '2026-09-28 09:00:00')))['count'])

    def test_frozen_input_and_changed_usage_are_rejected(self):
        frozen = self.validated()
        changed = deepcopy(frozen); changed['items'][0]['levels'][0]['value'] = '201'
        self.denied(lambda: entry.submit_daily(changed), '重新核对')
        with db.get_connection() as c:
            c.execute("UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?", (int(frozen['items'][0]['row_key']),))
        self.denied(lambda: entry.submit_daily(frozen), '变化')

    def test_real_reagent_revision_and_sql_readonly_guard(self):
        from services.lot_lifecycle_service import create_reagent_lot, record_lot_verification, switch_reagent_lots
        frozen = self.validated()
        first = self.context()['items'][0]
        reagent = create_reagent_lot(reagent_id=self.fixture['data']['reagent_id'], lot_no='SECOND-R', expiry_date='2100-12-31')
        verification = record_lot_verification(template_item_id=first['template_item_id'], system_id=first['system_id'],
            reagent_lot_id=reagent, conclusion='pass', evidence='明确新试剂批号核对', confirmed_by='工程测试', confirmed_at='2026-09-02')
        switch_reagent_lots(selections=[dict(template_item_id=first['template_item_id'], system_id=first['system_id'],
            reagent_lot_id=reagent, verification_id=verification, expected_revision=first['usage_revision'])],
            effective_at='2026-09-03', operator='工程测试', reason='明确新试剂批号')
        self.denied(lambda: entry.submit_daily(frozen), '变化')
        with db.read_snapshot() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE qc_project_templates SET template_name='不应写入'")

    def test_scales_identity_notes_and_frozen_receipt_levels(self):
        request = self.request()
        context = self.context()
        kinds = {r['row_key']:r['input_value_type'] for r in context['items']}
        for row in request['items']:
            row['manual_note'] = '三种方法都保留本次原备注'
            if kinds[row['row_key']] in ('ct', 'log'):
                for level in row['levels']: level['value'] = '0'
        corrupt = deepcopy(request); corrupt['items'][0]['unit_id'] = 999
        self.assertTrue(any(r['field'] == 'unit_id' for r in entry.validate_submission(corrupt)['errors']))
        numeric_nan = deepcopy(request); numeric_nan['items'][0]['levels'][0]['value'] = float('nan')
        self.assertFalse(entry.validate_submission(numeric_nan)['valid'])
        receipt = entry.submit_daily(self.validated(request))
        with db.get_connection() as c:
            for row in receipt['items']:
                table = entry.METHOD_TABLES[row['qc_method']][0]
                self.assertEqual('三种方法都保留本次原备注', c.execute(f'SELECT manual_note FROM {table} WHERE id=?', (row['source_id'],)).fetchone()[0])
                self.assertFalse(row['conclusion'].isascii())
                if kinds[row['row_key']] in ('ct', 'log'):
                    self.assertTrue(all(level['value'] == 0 for level in row['levels']))
        stored = entry.get_submission(receipt['submission_id'])
        entry.submit_daily(self.validated(self.request('different', '2026-09-28 10:00:00', delta=2)))
        self.assertEqual(stored, entry.get_submission(receipt['submission_id']))

    def test_original_three_method_saves_match_group_values_context_and_evaluation(self):
        from services.instant_service import save_instant_result
        from services.value_type_service import parse_project_input_value
        from zscore_logic import create_zscore_run, get_template_id_for_level_count
        frozen = self.validated()
        entry.submit_daily(frozen)
        tables = ('results', 'instant_results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
                  'qc_result_context_levels', 'qc_result_evaluations', 'qc_target_profiles', 'zscore_level_targets')
        def clean(value):
            if isinstance(value, list): return [clean(row) for row in value]
            if isinstance(value, dict): return {k:clean(v) for k,v in value.items() if k not in ('created_at','updated_at')}
            if isinstance(value, str) and value[:1] in ('{','['):
                try: return clean(json.loads(value))
                except ValueError: pass
            return value
        def scientific_rows():
            with db.read_snapshot() as c:
                return clean({table:[dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY id')] for table in tables})
        grouped = scientific_rows()
        comparison_path = Path(self.tmp.name) / 'original-methods.db'
        shutil.copyfile(self.base_path, comparison_path); db.DB_PATH = comparison_path
        context = self.context(); by_key = {r['row_key']:r for r in context['items']}
        for requested in frozen['items']:
            item = by_key[requested['row_key']]
            values = [parse_project_input_value(level['value'], item['input_value_type']) for level in requested['levels']]
            common = dict(batch_id=item['runtime_batch_id'], test_time=frozen['test_time'], operator=frozen['operator'],
                lot_selection=dict(reagent_lot_id=requested['reagent_lot_id'], expected_revision=item['usage_revision']))
            if item['qc_method'] == 'lj':
                db.add_result(**common, value=values[0][0], log_value=values[0][1])
            elif item['qc_method'] == 'instant':
                save_instant_result(**common, value=values[0][0], log_value=values[0][1])
            else:
                create_zscore_run(**common, level_results=[dict(level_id=f'Level {i+1}',raw_value=v[0],log_value=v[1]) for i,v in enumerate(values)],
                    template_id=get_template_id_for_level_count(item['level_count']), required_n=item['target_n'])
        self.assertEqual(grouped, scientific_rows())

    def test_backup_restore_preserves_twenty_contexts_and_exact_lots(self):
        from services.storage_service import create_database_backup, restore_database_from_backup_file
        entry.submit_daily(self.validated())
        expected = self.context('2026-09-28 10:00:00')
        backup = create_database_backup(Path(self.tmp.name) / 'backups')
        restored = restore_database_from_backup_file(backup.target_path)
        self.assertTrue(restored.protection_backup_path.exists())
        self.assertEqual(expected, self.context('2026-09-28 10:00:00'))
        self.assertEqual(20, entry.get_submission('group')['count'])

    def test_mid_group_and_last_group_faults_rollback_every_table(self):
        original = entry._save_item
        for failure_index in (2, 20):
            frozen = self.validated(self.request(f'failure-{failure_index}'))
            calls = []
            def fail(payload, row):
                result = original(payload, row); calls.append(result)
                if len(calls) == failure_index: raise RuntimeError('injected after actual write')
                return result
            with patch.object(entry, '_save_item', fail):
                self.denied(lambda: entry.submit_daily(frozen))
            self.assertEqual(failure_index, len(calls))
        self.assertEqual(20, entry.submit_daily(frozen)['count'])

    def test_sql_faults_level_context_evaluation_and_receipt(self):
        cases = [('zscore_level_results', "NEW.level_id='Level 2'"), ('qc_result_contexts', '1'),
                 ('qc_result_evaluations', '1'), ('qc_daily_submissions', '1'), ('qc_daily_submission_items', '1')]
        for table, condition in cases:
            frozen = self.validated(self.request('fail-' + table))
            with db.get_connection() as c:
                c.execute(f"CREATE TRIGGER injected_failure BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'injected failure'); END")
            self.denied(lambda: entry.submit_daily(frozen))
            with db.get_connection() as c: c.execute('DROP TRIGGER injected_failure')
        self.assertEqual(20, entry.submit_daily(frozen)['count'])

    def test_parameter_preparation_is_inside_group_transaction(self):
        for index in range(5):
            entry.submit_daily(self.validated(self.request('warm-' + str(index), f'2026-09-28 08:{index:02d}:00', delta=index)))
        with db.get_connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM qc_target_profiles').fetchone()[0])
        frozen = self.validated(self.request('ready', '2026-09-28 09:00:00', delta=2))
        original = entry._save_item; calls = []
        def fail(payload, row):
            result = original(payload, row); calls.append(result)
            if len(calls) == 20: raise RuntimeError('fail after target preparation')
            return result
        with patch.object(entry, '_save_item', fail):
            self.denied(lambda: entry.submit_daily(frozen))
        receipt = entry.submit_daily(frozen)
        self.assertTrue(all(r['target_profile_id'] for r in receipt['items'] if r['qc_method'] != 'instant'))

    def test_write_lock_failure_can_retry_same_request_without_residue(self):
        frozen = self.validated()
        real_connect = sqlite3.connect
        held = real_connect(db.DB_PATH)
        try:
            held.execute('BEGIN IMMEDIATE')
            def short_timeout(*args, **kwargs):
                return real_connect(*args, **{**kwargs, 'timeout': 0.02})
            with patch.object(db.sqlite3, 'connect', short_timeout):
                error = self.denied(lambda: entry.submit_daily(frozen))
                self.assertTrue(error.retryable)
        finally:
            held.rollback(); held.close()
        self.assertEqual(20, entry.submit_daily(frozen)['count'])

    def test_readonly_usage_time_boundary_disabled_material_and_transferred_instant(self):
        from services.lot_lifecycle_service import set_qc_usage_state
        first = self.context()['items'][0]
        set_qc_usage_state(lot_config_item_id=first['lot_config_item_id'], state='ended',
            effective_at='2099-09-29 00:00:00', operator='工程验收', reason='明确将来的使用截止边界')
        before = self.dump()
        left = self.context('2099-09-28 23:59:59')['items'][0]
        right = self.context('2099-09-29 00:00:00')['items'][0]
        self.assertTrue(left['writable'], left['issues'])
        self.assertFalse(right['writable']); self.assertEqual('ended', right['usage_state'])
        self.assertEqual(before, self.dump())
        frozen = self.validated()
        instant = next(r for r in self.context()['items'] if r['qc_method'] == 'instant')
        with db.get_connection() as c:
            c.execute('UPDATE md_qc_material_lots SET is_disabled=1 WHERE id=?', (self.fixture['main_lot'],))
            c.execute("UPDATE instant_batches SET transfer_status='transferred' WHERE id=?", (instant['runtime_batch_id'],))
        before = self.dump()
        current = self.context()
        self.assertTrue(all(not r['writable'] for r in current['items']))
        changed = next(r for r in current['items'] if r['row_key'] == instant['row_key'])
        self.assertTrue(any('转入' in r['message'] for r in changed['issues']))
        self.assertEqual(before, self.dump())
        self.denied(lambda: entry.submit_daily(frozen), '变化')

    def test_errors_keep_item_level_and_exact_settings_destination(self):
        for field,target in {'quality':'quality','target_profile':'parameters','reagent_lot_id':'reagent',
            'usage':'usage','levels':'materials','material':'materials','expiry_date':'materials','binding':'project'}.items():
            error = contexts.issue('待核对', row_key='8', qc_level_id=3, field=field)
            self.assertEqual(dict(row_key='8',qc_level_id=3,field=field,message='待核对',settings_target=target),error)
        self.assertEqual('custom',contexts.issue('明确覆盖',field='quality',settings_target='custom')['settings_target'])

    def test_sd_source_stays_sd_and_log_item_keeps_explicit_scale(self):
        from services.master_data_service import create_test_item, create_method
        from services.project_config_service import (create_project_template, save_template_items,
            list_template_items, list_lot_config_items, activate_project_template, activate_lot_config)
        from services.material_workflow_service import create_material_config
        from services.quality_target_service import get_requirement, adopt_requirement, item_context
        from services.quality_review_service import standard_candidates
        from tests.quality_review_fixtures import fixture_conditions
        from services.workbench_config_service import sync_lj_workbench_bindings
        from services.lot_lifecycle_service import workbench_systems, record_lot_verification, switch_reagent_lots
        spec = get_requirement('wst403-2024-082')
        d = self.fixture['data']
        with db.read_snapshot() as c:
            unit = c.execute("SELECT id FROM md_units WHERE symbol='mmol/L'").fetchone()[0]
        test = create_test_item(chinese_name=spec['name'], default_unit_id=unit, specimen_type='全血')
        method = create_method(method_name='便携式血糖仪法')
        tid = create_project_template(template_name='便携式血糖仪低浓度 SD 工程验收',
            lab_instrument_id=d['lab_instrument_id'], qc_material_id=d['qc_material_id'])
        save_template_items(tid, [dict(test_item_id=test, qc_method='lj', input_value_type='raw',
            unit_id=unit, method_id=method, reagent_id=d['reagent_id'], level_count=1, target_n=5)])
        project_item = int(list_template_items(tid).iloc[0]['id'])
        def adopt(scope, identifier):
            item = item_context(scope, identifier)
            return adopt_requirement(scope, identifier, spec['id'], confirmed_by='工程验收',
                evidence='工程场景明确为便携式血糖仪、全血及5 mmol/L原始检测值；不代表临床验证。',
                levels=[dict(level_order=1, concentration=5)],
                exclusions={r['id']:'本工程场景为便携式血糖仪，采用对应专门条款。'
                    for r in standard_candidates(item) if r['id'] != spec['id']},
                **fixture_conditions(item, clinical=True))
        adopt('project', project_item); activate_project_template(tid)
        config = create_material_config(template_id=tid, selections={project_item:[self.fixture['levels'][0]]})
        lot_item = int(list_lot_config_items(config).iloc[0]['id'])
        goal = adopt('lot', lot_item); activate_lot_config(config); sync_lj_workbench_bindings()
        system = next(r for r in workbench_systems() if r['template_item_id'] == project_item)
        verification = record_lot_verification(template_item_id=project_item, system_id=system['id'],
            reagent_lot_id=self.fixture['reagent'], conclusion='pass', evidence='工程样例试剂验证',
            confirmed_by='工程验收', confirmed_at='2026-09-01')
        switch_reagent_lots(selections=[dict(template_item_id=project_item, system_id=system['id'],
            reagent_lot_id=self.fixture['reagent'], verification_id=verification, expected_revision=0)],
            effective_at='2026-09-01', operator='工程验收', reason='工程样例显式使用')
        before = self.dump()
        context = self.context(lot_config_id=config)
        item = context['items'][0]
        self.assertTrue(item['writable'], item['issues'])
        self.assertEqual(goal, item['quality_goal'])
        self.assertEqual('sd', item['quality_goal']['levels'][0]['rule']['kind'])
        self.assertIsNone(item['source_snapshot']['cv_limit'])
        self.assertEqual(before, self.dump())
        req = dict(submission_id='sd', selection=context['selection'], context_revision=context['context_revision'],
            test_time=context['test_time'], operator='工程验收', items=[dict(row_key=item['row_key'],
            reagent_lot_id=self.fixture['reagent'], input_value_type='log',
            levels=[dict(qc_level_id=self.fixture['levels'][0], value='5')])])
        self.assertFalse(entry.validate_submission(req)['valid'])
        req['items'][0]['input_value_type'] = 'raw'
        frozen = entry.validate_submission(req)
        self.assertTrue(frozen['valid'], frozen['errors'])
        entry.submit_daily(frozen['frozen_request'])
        self.assertEqual(goal, self.context(lot_config_id=config)['items'][0]['quality_goal'])
        logs = [r for r in self.context()['items'] if r['input_value_type'] == 'log']
        self.assertTrue(logs)
        self.assertTrue(all(r['quality_review']['context']['result_scale'] == 'log' and not r['quality_goal'] for r in logs))


if __name__ == '__main__':
    unittest.main(verbosity=2)
