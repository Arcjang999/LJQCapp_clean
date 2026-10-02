"""Isolated persistence tests with explicit synthetic saved-evaluation evidence.

These tests do not validate clinical algorithms. Real application-generated LJ/Z
evaluations and browser/report paths belong to the B1 integration acceptance.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services import out_of_control_service as service


def seed_engineering_sources():
    """Caller must bind db.DB_PATH to an isolated initialized test database."""
    from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
    from services.project_config_service import create_project_template, save_template_items, list_template_items
    deps = _seed_v11_configuration_dependencies()
    tid = create_project_template(template_name='失控保存工程样例', lab_instrument_id=deps['lab_instrument_id'], qc_material_id=deps['qc_material_id'])
    save_template_items(tid, [{'test_item_id': deps['lj_item_id'], 'qc_method': 'lj', 'input_value_type': 'raw',
                              'unit_id': deps['unit_id'], 'method_id': deps['method_id'], 'reagent_id': deps['reagent_id'],
                              'level_count': 1, 'target_n': 5, 'cv_limit': 5}])
    item_id = int(list_template_items(tid).iloc[0]['id'])
    project = db.create_project('失控保存工程样例项目', input_value_type='raw')
    batch = db.create_batch(project_id=project, instrument='样例仪器', reagent='样例试剂',
                            qc_material='样例材料', concentration='低值', lot_no='ENGINEERING', target_n=5)
    config = {'project_template_item_id': item_id, 'test_item_name': '免疫球蛋白G',
              'lab_instrument_id': deps['lab_instrument_id'], 'instrument_name': '样例仪器A',
              'test_item_id': deps['lj_item_id'], 'input_value_type': 'raw', 'unit_id': deps['unit_id'],
              'unit_symbol': 'g/L', 'method_id': deps['method_id'], 'method_name': '样例方法', 'reagent_id': deps['reagent_id'],
              'identity': [deps['lab_instrument_id'], deps['qc_material_id'], deps['source_lot_id'], deps['lj_item_id'],
                           'raw', deps['unit_id'], deps['method_id'], deps['reagent_id']],
              'quality_goal_json': json.dumps({'test_only': True, 'limit': 5})}
    with db.get_connection() as c:
        identity = [item_id, *[config['identity'][i] for i in (0, 3, 4, 5, 6, 7)]]
        system = c.execute('INSERT INTO qc_detection_systems(template_item_id,identity_json,snapshot_json) VALUES(?,?,?)',
                           (item_id, json.dumps(identity), json.dumps(config))).lastrowid
        config['system_id'] = system
    sources = {}

    def add(name, source_type, time, classification, level_count=1, *, changed=False, other=False, no_context=False, no_evaluation=False):
        local_config = deepcopy(config)
        with db.get_connection() as c:
            if other:
                local_config['identity'][0] = 9999
                local_config['lab_instrument_id'] = 9999
                local_config['instrument_name'] = '样例仪器B'
                identity = [item_id, *[local_config['identity'][i] for i in (0, 3, 4, 5, 6, 7)]]
                local_config['system_id'] = c.execute('INSERT INTO qc_detection_systems(template_item_id,identity_json,snapshot_json) VALUES(?,?,?)',
                                                    (item_id, json.dumps(identity), json.dumps(local_config))).lastrowid
            if source_type == 'lj_result':
                source_id = c.execute('INSERT INTO results(batch_id,test_time,operator,value,manual_note) VALUES(?,?,?,?,?)',
                                      (batch, time, '工程测试', 14 if classification == 'reject' else 10, '原始备注')).lastrowid
                payload = {'result': {'id': source_id, 'phase': '正式数据', 'status': {'reject': '失控', 'warning': '警告', 'accept': '符合质控'}[classification],
                                      'rule_hits': ['1_3s'] if classification == 'reject' else [], 'value': 14}, 'target_mean': 10, 'target_sd': 1}
                column = 'lj_result_id'
            else:
                source_id = c.execute('''INSERT INTO zscore_runs(batch_id,project_id,test_time,operator,level_count,phase,run_status,rule_template_id,rule_hits_run)
                    VALUES(?,?,?,?,?,'formal_qc',?,'engineering',?)''', (batch, project, time, '工程测试', level_count, classification, '[{"rule_id":"1_3s"}]')).lastrowid
                payload = {'id': source_id, 'phase': 'formal_qc', 'run_status': classification, 'rule_hits_run': [{'rule_id': '1_3s'}], 'level_results': []}
                for order in range(1, level_count + 1):
                    status = classification if order > 1 else 'accept'
                    c.execute('INSERT INTO zscore_level_results(run_id,level_id,raw_value,level_status,rule_hits_local) VALUES(?,?,?,?,?)',
                              (source_id, f'Level {order}', 10 * order, status, '["1_3s"]' if status == 'reject' else '[]'))
                    payload['level_results'].append({'level_id': f'Level {order}', 'status': status, 'target_mean': 10 * order, 'target_sd': 1,
                                                    'rule_hits_local': ['1_3s'] if status == 'reject' else []})
                column = 'zscore_run_id'
            if not no_context:
                context = c.execute(f'INSERT INTO qc_result_contexts({column},config_snapshot_json,provenance,reagent_lot_no) VALUES(?,?,?,?)',
                                    (source_id, json.dumps(local_config), 'recorded', 'REAGENT-A')).lastrowid
                for order in range(1, level_count + 1):
                    c.execute('''INSERT INTO qc_result_context_levels(context_id,level_order,qc_level_id,qc_lot_id,lot_no,level_name,level_code)
                        VALUES(?,?,?,?,?,?,?)''', (context, order, deps['source_levels'][order - 1],
                                                  deps['target_lot_id'] if changed else deps['source_lot_id'],
                                                  'QC-NEW' if changed else 'QC-OLD', f'水平{order}', str(order)))
                if not no_evaluation:
                    c.execute('INSERT INTO qc_result_evaluations(context_id,evaluation_json,reason) VALUES(?,?,?)',
                              (context, json.dumps(payload), 'recorded'))
            sources[name] = (source_type, source_id)
    add('lj', 'lj_result', '2026-09-01 09:00:00', 'reject')
    add('warning', 'lj_result', '2026-09-01 09:10:00', 'warning')
    add('later', 'lj_result', '2026-09-01 10:00:00', 'accept')
    add('later_reject', 'lj_result', '2026-09-01 10:10:00', 'reject')
    add('changed', 'lj_result', '2026-09-01 11:00:00', 'accept', changed=True)
    add('other', 'lj_result', '2026-09-01 11:10:00', 'accept', other=True)
    add('same_time', 'lj_result', '2026-09-01 09:00:00', 'accept')
    add('before', 'lj_result', '2026-09-01 08:00:00', 'accept')
    add('gap', 'lj_result', '2026-09-01 11:20:00', 'reject', no_context=True)
    add('z2', 'zscore_run', '2026-09-01 09:00:00', 'reject', 2)
    add('z3', 'zscore_run', '2026-09-01 09:10:00', 'reject', 3)
    add('z3later', 'zscore_run', '2026-09-01 10:00:00', 'accept', 3)
    add('z_missing', 'zscore_run', '2026-09-01 09:20:00', 'reject', 3, no_context=True)
    return sources


class HandlingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.old = db.DB_PATH, db.LEGACY_DB_CANDIDATES
        db.DB_PATH = Path(self.tmp.name) / 'isolated.db'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        self.sources = seed_engineering_sources()
        self.original = self.source_dump()

    def tearDown(self):
        db.DB_PATH, db.LEGACY_DB_CANDIDATES = self.old
        self.tmp.cleanup()

    def source_dump(self):
        with db.get_connection() as c:
            return {table: [tuple(row) for row in c.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
                                  'qc_result_context_levels', 'qc_result_evaluations', 'qc_detection_systems')}

    def dump(self):
        with db.get_connection() as c:
            return '\n'.join(c.iterdump())

    def deny(self, action, message=None):
        before = self.dump()
        with self.assertRaises(ValueError) as caught:
            action()
        self.assertEqual(before, self.dump())
        if message:
            self.assertIn(message, str(caught.exception))

    def open(self, name='lj', request='open'):
        return service.open_event(*self.sources[name], request, '检测员')

    def content(self, event):
        return {**event['content'], 'cause_category': '其他', 'cause_analysis': '已核对原因',
                'corrective_action': '已执行纠正措施', 'effect_description': '人工核对效果',
                'effect_evidence': '独立检查记录', 'handler_text': '处理员'}

    def save(self, event, action='save_draft', request='save', content=None, reason=''):
        return service.save_handling(event['event_id'], event['revision_no'], request, action,
                                     content if content is not None else self.content(event), '处理员', reason)

    def test_unique_snapshots_and_initialization(self):
        event = self.open()
        repeated = self.open(request='open-2')
        self.assertEqual(event['event_id'], repeated['event_id'])
        self.assertEqual(1, repeated['revision_no'])
        self.assertEqual(event['event_id'], self.open()['event_id'])
        self.deny(lambda: service.open_event(*self.sources['warning'], 'open', '检测员', True), '改变')
        self.deny(lambda: self.open('warning', 'warning'), '警告')
        warning = service.open_event(*self.sources['warning'], 'warning-explicit', '检测员', True)
        self.assertEqual('warning', warning['original_classification'])
        self.assertEqual('pending', warning['status'])
        self.deny(lambda: self.open('later', 'accept'), '正式期')
        z3 = self.open('z3', 'z3')
        self.assertEqual(3, len(z3['origin_snapshot']['levels']))
        self.assertEqual(['accept', 'reject', 'reject'], [r['classification'] for r in z3['origin_snapshot']['levels']])
        self.assertNotEqual(event['event_id'], self.open('z2', 'z2')['event_id'])
        self.assertEqual(self.sources['lj'][1], self.sources['z2'][1])
        self.assertEqual(self.original, self.source_dump())
        db.init_db()
        self.assertEqual(event['origin_snapshot'], service.get_event(event['event_id'])['origin_snapshot'])

    def test_concurrent_open_and_stale_save(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            events = list(pool.map(lambda i: service.open_event(*self.sources['lj'], f'parallel-{i}', '检测员'), range(2)))
        self.assertEqual(events[0]['event_id'], events[1]['event_id'])
        saved = self.save(events[0])
        self.assertEqual(2, saved['revision_no'])
        retry = self.save(events[0])
        self.assertEqual(saved['revision_no'], retry['revision_no'])
        self.deny(lambda: self.save(events[0], request='new-stale'), '更新')
        self.deny(lambda: self.save(events[0], content={**self.content(events[0]), 'cause_analysis': '不同内容'}), '改变')
        self.assertEqual(self.original, self.source_dump())

    def test_state_machine_and_revision_history(self):
        event = self.open()
        self.deny(lambda: self.save(event, action='confirm'), '状态')
        event = self.save(event)
        self.deny(lambda: self.save(event, action='submit', request='missing', content={**self.content(event), 'cause_analysis': ' '}), '原因分析')
        event = self.save(event, action='submit', request='submit')
        self.deny(lambda: self.save(event, request='silent-edit'), '状态')
        confirm = {**event['content'], 'confirmer_text': '确认员', 'confirmed_at': service._now(), 'confirmation_checked': True}
        self.deny(lambda: self.save(event, action='confirm', request='bad-confirmer', content={**confirm, 'confirmer_text': ' '}), '确认人')
        self.deny(lambda: self.save(event, action='confirm', request='bad-time', content={**confirm, 'confirmed_at': '2026-01-01 00:00:00'}), '确认时间')
        completed = self.save(event, action='confirm', request='confirmed', content=confirm)
        self.assertEqual('completed', completed['status'])
        self.assertEqual('reject', completed['original_classification'])
        self.deny(lambda: self.save(completed, action='revise', request='missing-reason'), '原因')
        revised = self.save(completed, action='revise', request='revised', reason='补充调查依据')
        self.assertEqual('in_progress', revised['status'])
        self.assertEqual('completed', service.get_event(completed['event_id'], completed['revision_no'])['status'])
        submitted = self.save(revised, action='submit', request='resubmit')
        returned = self.save(submitted, action='return', request='returned', reason='补充资料')
        self.assertEqual('in_progress', returned['status'])
        self.assertEqual(self.original, self.source_dump())

    def test_retest_exact_identity_time_full_run_and_removal(self):
        event = self.open()
        candidates = service.list_retest_candidates(event['event_id'])
        ids = {row['source_id'] for row in candidates}
        self.assertTrue({self.sources[name][1] for name in ('later', 'later_reject', 'changed')} <= ids)
        self.assertFalse({self.sources[name][1] for name in ('other', 'same_time', 'before', 'lj')} & ids)
        def linked(name, reason=''):
            kind, ident = self.sources[name]
            return {**self.content(event), 'retest_refs': [{'source_type': kind, 'source_id': ident, 'difference_reason': reason}]}
        for name in ('other', 'same_time', 'before', 'lj'):
            self.deny(lambda name=name: self.save(event, request='invalid-'+name, content=linked(name, '核对差异')))
        self.deny(lambda: self.save(event, request='difference-required', content=linked('changed')), '差异')
        accepted = self.save(event, request='with-difference', content=linked('changed', '更换材料后复测'))
        self.assertEqual(1, len(accepted['retest_refs']))
        self.assertEqual('in_progress', accepted['status'])
        self.deny(lambda: self.save(accepted, request='remove', content={**accepted['content'], 'retest_refs': []}), '移除')
        removed = self.save(accepted, request='remove-reason', content={**accepted['content'], 'retest_refs': []}, reason='误选复测')
        self.assertEqual([], removed['retest_refs'])
        self.assertEqual(1, len(service.get_event(accepted['event_id'], accepted['revision_no'])['retest_refs']))
        z = self.open('z3', 'open-z3')
        z_content = self.content(z)
        ref = {'source_type': 'zscore_run', 'source_id': self.sources['z3later'][1], 'difference_reason': '参数和试剂批号旧资料未记录，已人工核对'}
        z_content['retest_refs'] = [ref, ref]
        z = self.save(z, request='z-retest', content=z_content)
        self.assertEqual(1, len(z['retest_refs']))
        self.assertEqual(3, len(z['retest_refs'][0]['snapshot']['levels']))
        self.assertEqual(self.original, self.source_dump())

    def test_query_is_readonly_and_cross_day(self):
        before = self.dump()
        pending = service.list_pending()
        self.assertEqual(5, pending['count'])
        self.assertEqual(1, len(pending['verification_gaps']))
        self.assertEqual(before, self.dump())
        event = self.open('z3')
        event = self.save(event)
        after = service.list_pending(start_date='2026-10-01', end_date='2026-10-31', statuses=['in_progress'])
        self.assertEqual([], after['items'])
        self.assertEqual(1, len(after['cross_day']))
        self.assertEqual(1, service.count_pending(start_date='2026-10-01', statuses=['in_progress']))
        self.assertEqual(5, service.list_pending()['count'])
        self.assertTrue(service.list_pending(search='免球蛋白')['items'])
        self.assertEqual([], service.list_pending(instrument_id=9999)['items'])
        self.assertEqual(1, len(service.lookup_events_for_sources('zscore_run', [self.sources['z3'][1]])))

    def test_missing_and_changed_evidence_remain_explicit(self):
        self.deny(lambda: self.open('gap'), '核对')
        missing = self.open('z_missing', 'missing')
        self.assertIsNone(missing['origin_context_id'])
        self.assertEqual([], service.list_retest_candidates(missing['event_id']))
        self.assertTrue(missing['origin_snapshot']['missing_fields'])
        event = self.open('lj', 'lj')
        with db.get_connection() as c:
            original = event['origin_evaluation_id']
            c.execute('INSERT INTO qc_result_evaluations(context_id,evaluation_json,reason,previous_id) VALUES(?,?,?,?)',
                      (event['origin_context_id'], json.dumps({'result': {'phase': '正式数据', 'status': '符合质控'}}), 'engineering_revision', original))
        updated = service.get_event(event['event_id'])
        self.assertTrue(updated['evaluation_changed'])
        self.assertEqual(original, updated['origin_evaluation_id'])
        self.assertEqual('reject', updated['origin_snapshot']['classification'])
        self.assertEqual(event['event_id'], self.open('lj', 'reopen')['event_id'])

    def test_transaction_failure_and_patient_input(self):
        event = self.open()
        with db.get_connection() as c:
            c.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON qc_ooc_status_history WHEN NEW.revision_no=2 BEGIN SELECT RAISE(ABORT,'injected'); END")
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(event)
        self.assertEqual(before, self.dump())
        with db.get_connection() as c:
            c.execute('DROP TRIGGER injected_failure')
        self.deny(lambda: self.save(event, content={**self.content(event), 'patient_impact_start': 'bad'}), '日期')
        self.deny(lambda: self.save(event, content={**self.content(event), 'patient_impact_start': '2026-09-02', 'patient_impact_end': '2026-09-01'}), '结束')
        self.deny(lambda: self.save(event, content={**self.content(event), 'cause_category': '不存在分类'}), '分类')
        self.assertEqual(2, self.save(event)['revision_no'])
        self.assertEqual(self.original, self.source_dump())

    def test_attachments_are_event_scoped_and_atomic(self):
        from services.out_of_control_attachment_service import stage_attachment, list_attachments
        event = self.open()
        other = self.open('z3', 'z3')
        attachment = stage_attachment(event['event_id'], '核对.csv', '项目,结果\n样例,14'.encode(), uploaded_by='工程测试')
        content = {**self.content(event), 'attachment_ids': [attachment['attachment_id']]}
        self.deny(lambda: self.save(other, request='wrong-attachment', content={**self.content(other), 'attachment_ids': content['attachment_ids']}), '不属于')
        with db.get_connection() as c:
            c.execute("CREATE TRIGGER injected_attachment_failure BEFORE INSERT ON qc_ooc_status_history WHEN NEW.revision_no=2 BEGIN SELECT RAISE(ABORT,'injected'); END")
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(event, content=content)
        self.assertEqual(before, self.dump())
        self.assertEqual([], list_attachments(event['event_id'], 1))
        with db.get_connection() as c:
            c.execute('DROP TRIGGER injected_attachment_failure')
        saved = self.save(event, content=content)
        self.assertEqual(attachment['attachment_id'], list_attachments(event['event_id'], saved['revision_no'])[0]['attachment_id'])

    def test_confirmation_cannot_silently_adopt_new_retest_evidence(self):
        event = self.open()
        ref = {'source_type': 'lj_result', 'source_id': self.sources['later'][1], 'difference_reason': '旧资料缺批号与参数版本，已人工核对'}
        event = self.save(event, content={**self.content(event), 'retest_refs': [ref]})
        event = self.save(event, action='submit', request='submit')
        linked = event['retest_refs'][0]
        with db.get_connection() as c:
            c.execute('INSERT INTO qc_result_evaluations(context_id,evaluation_json,reason,previous_id) VALUES(?,?,?,?)',
                      (linked['origin_context_id'], json.dumps({'result': {'phase': '正式数据', 'status': '失控'}}), 'engineering_revision', linked['origin_evaluation_id']))
        confirmation = {**event['content'], 'confirmer_text': '确认员', 'confirmed_at': service._now(), 'confirmation_checked': True}
        self.deny(lambda: self.save(event, action='confirm', request='confirm-after-change', content=confirmation), '判读已更新')
        self.assertEqual(linked['origin_evaluation_id'], service.get_event(event['event_id'])['retest_refs'][0]['origin_evaluation_id'])

    def test_creation_failure_never_leaves_half_event(self):
        with db.get_connection() as c:
            c.execute("CREATE TRIGGER injected_open_failure BEFORE INSERT ON qc_ooc_status_history BEGIN SELECT RAISE(ABORT,'injected'); END")
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            self.open()
        self.assertEqual(before, self.dump())
        self.assertEqual(self.original, self.source_dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
