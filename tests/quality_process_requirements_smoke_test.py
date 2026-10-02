"""Editable local process requirements preserve numeric gates and source snapshots."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import read_snapshot
from services.instant_workbench_service import sync_instant_workbench_bindings
from services.master_data_service import create_qc_lot, create_qc_level
from services.project_config_service import (
    activate_project_template, activate_lot_config, create_lot_config_from_template,
    copy_lot_config, list_lot_config_items, save_lot_item_levels,
)
from services.quality_applicability_service import assess, catalog_index
from services.quality_review_service import (
    runtime_review, save_pending_review, save_recorded_requirement,
    validate_project_quality, validate_quality_review,
)
from services.quality_target_service import decode, item_context
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quality_applicability_smoke_test import adopt, context, draft, record, search


class QualityProcessRequirementsTest(unittest.TestCase):
    def dump(self):
        with read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def pcr(self, name='HBV DNA', mode='ct'):
        fixture, identifier = draft(name, mode=mode,
            unit='Ct' if mode=='ct' else 'copies/mL', method='实时荧光 PCR')
        return fixture, identifier, context(technique='realtime_pcr',
            result_scale=mode if mode in ('ct', 'log') else 'concentration')

    def test_default_references_copy_current_original_text_without_automatic_evaluation(self):
        with IsolatedDatabase():
            _, identifier, ctx = self.pcr()
            source_before = deepcopy(catalog_index())
            candidates = [row for row in assess(item_context('project', identifier), ctx)
                          if row['kind']=='process' and row['status']=='applicable']
            reviewed = record(identifier, ctx, search_record=search())
            local = reviewed['process_requirements']
            self.assertEqual(set(local['source_ids']), {row['id'] for row in candidates})
            for candidate in candidates:
                for sentence in candidate['spec']['requirements']:
                    self.assertIn(sentence, local['requirement_text'])
                saved = next(row for row in reviewed['candidates'] if row['id']==candidate['id'])
                self.assertEqual(saved['requirements'], candidate['spec']['requirements'])
                self.assertEqual(saved['disposition'], 'referenced')
                self.assertFalse(saved['automatic_evaluation'])
            self.assertEqual(validate_project_quality(identifier), [])
            self.assertEqual(catalog_index(), source_before)

    def test_local_text_can_be_changed_cleared_or_saved_without_any_reference(self):
        with IsolatedDatabase():
            _, identifier, ctx = self.pcr()
            source_before = deepcopy(catalog_index())
            cases = [
                dict(source_ids=['wst230-2024-iqc'], requirement_text='本实验室按当地要求设置对照并记录结果。'),
                dict(source_ids=['wst230-2024-iqc'], requirement_text=''),
                dict(source_ids=[], requirement_text='本地核对要求由当前SOP规定。'),
                dict(source_ids=[], requirement_text=''),
            ]
            for local in cases:
                with self.subTest(local=local):
                    reviewed = record(identifier, ctx, search_record=search(), process_requirements=local)
                    self.assertEqual(reviewed['process_requirements'], local)
                    self.assertEqual(validate_project_quality(identifier), [])
                    for row in reviewed['candidates']:
                        expected = 'referenced' if row['id'] in local['source_ids'] else 'not_selected'
                        self.assertEqual(row['disposition'], expected)
                        self.assertFalse(row['automatic_evaluation'])
                    item = item_context('project', identifier)
                    self.assertFalse(decode(item['quality_goal_json']))
                    self.assertIsNone(item['cv_limit'])
            self.assertEqual(catalog_index(), source_before)

    def test_local_process_choice_cannot_bypass_numeric_or_other_mandatory_requirements(self):
        empty = dict(source_ids=[], requirement_text='')
        with IsolatedDatabase():
            _, identifier = draft()
            before = self.dump()
            with self.assertRaisesRegex(ValueError, '必须采用'):
                record(identifier, context(), search_record=search(), process_requirements=empty)
            self.assertEqual(before, self.dump())
            goal = adopt(identifier, process_requirements=empty)
            self.assertEqual(goal['spec']['id'], 'wst403-2024-047')
            self.assertEqual(goal['spec']['imprecision'][0]['value'], 7.5)
            review = decode(item_context('project', identifier)['quality_review_json'])
            self.assertEqual(review['process_requirements'], empty)
            self.assertEqual(next(row for row in review['candidates'] if row['kind']=='numeric')['disposition'], 'adopted')
            self.assertEqual(validate_project_quality(identifier), [])
        with IsolatedDatabase():
            _, identifier = draft('HBsAg', unit='S/CO', method='化学发光免疫分析')
            ctx = context(result_kind='qualitative', result_scale='s_co')
            before = self.dump()
            with self.assertRaises(ValueError):
                record(identifier, ctx, search_record=search(), adopted_standard_ids=[], process_requirements=empty)
            self.assertEqual(before, self.dump())
            reviewed = record(identifier, ctx, search_record=search(), process_requirements=empty)
            self.assertEqual({row['kind'] for row in reviewed['candidates']}, {'qualitative', 'signal_precision'})
            self.assertTrue(all(row['disposition']=='adopted' for row in reviewed['candidates']))
            self.assertEqual(validate_project_quality(identifier), [])

    def test_pending_process_reference_does_not_block_but_cannot_be_selected(self):
        with IsolatedDatabase():
            _, identifier, ctx = self.pcr('HIV-1 RNA', mode='raw')
            pending_id = 'cdc-hiv-iqc-2024-rna-quantitative'
            assessed = next(row for row in assess(item_context('project', identifier), ctx) if row['id']==pending_id)
            self.assertEqual(assessed['status'], 'pending')
            self.assertIn('Log10', assessed['reason'])
            reviewed = record(identifier, ctx, search_record=search())
            self.assertNotIn(pending_id, reviewed['process_requirements']['source_ids'])
            self.assertEqual(next(row for row in reviewed['candidates'] if row['id']==pending_id)['disposition'], 'pending')
            self.assertEqual(validate_project_quality(identifier), [])
            before = self.dump()
            with self.assertRaises(ValueError):
                record(identifier, ctx, search_record=search(), process_requirements={
                    'source_ids': [pending_id], 'requirement_text': ''})
            self.assertEqual(before, self.dump())

    def test_unknown_inapplicable_future_and_nonprocess_sources_are_rejected_atomically(self):
        with IsolatedDatabase():
            _, identifier, ctx = self.pcr()
            before = self.dump()
            for source_id in ('unknown-source', 'cdc-hcv-2023-rna-quantitative-iqc',
                              'wst641-2026-iqc', 'wst494-2017-signal', 'wst403-2024-047'):
                with self.subTest(source_id=source_id):
                    with self.assertRaises(ValueError):
                        record(identifier, ctx, search_record=search(), process_requirements={
                            'source_ids': [source_id], 'requirement_text': '不能冒充适用的过程参考。'})
                    self.assertEqual(before, self.dump())

    def test_pending_save_retains_local_text_without_confirming_project(self):
        with IsolatedDatabase():
            fixture, identifier, ctx = self.pcr()
            local = dict(source_ids=['wst230-2024-iqc'], requirement_text='尚待核对的本地对照设置。')
            save_pending_review('project', identifier, context=ctx, search_record=search(),
                draft_recorded=dict(source_name='草稿SOP', source_version='DRAFT-1', requirement_text='草稿要求'),
                process_requirements=local)
            reviewed = decode(item_context('project', identifier)['quality_review_json'])
            self.assertEqual(reviewed['process_requirements'], local)
            self.assertEqual(reviewed['status'], 'pending')
            self.assertEqual(reviewed['draft_recorded']['source_version'], 'DRAFT-1')
            with self.assertRaisesRegex(ValueError, '待确认'):
                activate_project_template(fixture['template_id'])

    def test_project_and_lot_copies_keep_text_as_pending_and_frozen_batches_keep_snapshot(self):
        with IsolatedDatabase():
            fixture, identifier, ctx = self.pcr()
            original_runtime = deepcopy(runtime_review('instant', fixture['batch_id']))
            local = dict(source_ids=['wst230-2024-iqc'], requirement_text='项目确认的本地对照要求。')
            record(identifier, ctx, search_record=search(), process_requirements=local)
            activate_project_template(fixture['template_id'])
            lot = create_qc_lot(qc_material_id=fixture['material_id'], lot_no='PROCESS-NEW', expiry_date='2028-12-31')
            level = create_qc_level(qc_material_lot_id=lot, level_name='新批常规水平', level_order=1)
            config = create_lot_config_from_template(template_id=fixture['template_id'], qc_material_lot_id=lot)
            item_id = int(list_lot_config_items(config).iloc[0]['id'])
            inherited = decode(item_context('lot', item_id)['quality_review_json'])
            self.assertEqual(inherited['process_requirements'], local)
            self.assertEqual(inherited['status'], 'pending')
            save_lot_item_levels(item_id, [dict(qc_level_id=level, target_source='building',
                target_mean=100, target_sd=2, target_confirmed=True)])
            with self.assertRaisesRegex(ValueError, '待确认'):
                activate_lot_config(config)
            lot_local = dict(source_ids=[], requirement_text='本批次按当地要求确认的对照设置。')
            save_recorded_requirement('lot', item_id, source_name='批次SOP', source_version='LOT-1',
                requirement_text='Ct值按批次设置进行质控。', confirmed_by='隔离测试确认人',
                evidence='按本批次方法与输入尺度核对。', context=ctx, search_record=search(),
                process_requirements=lot_local)
            self.assertEqual(validate_quality_review('lot', item_id), [])
            activate_lot_config(config)
            sync_instant_workbench_bindings()
            with read_snapshot() as connection:
                batch_id = connection.execute('SELECT runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                    (item_id,)).fetchone()[0]
            runtime = deepcopy(runtime_review('instant', batch_id))
            self.assertEqual(runtime['process_requirements'], lot_local)
            from services.daily_context_service import get_daily_context
            from services.lot_lifecycle_service import (
                create_reagent_lot, record_lot_verification, source_context, workbench_systems,
            )
            workbench_systems()
            with read_snapshot() as connection:
                source, _, _ = source_context(connection, 'instant', batch_id, read_only=True)
            reagent_lot = create_reagent_lot(reagent_id=fixture['reagent_id'],
                lot_no='PROCESS-DAILY-REAGENT', expiry_date='2028-12-31')
            record_lot_verification(template_item_id=identifier, system_id=source['system_id'],
                reagent_lot_id=reagent_lot, conclusion='pass', evidence='本地过程要求的日常录入隔离验收',
                confirmed_by='测试核对人', confirmed_at='2026-09-01')
            before_daily = self.dump()
            daily = get_daily_context(lab_instrument_id=fixture['instrument_id'],
                qc_material_id=fixture['material_id'], qc_material_lot_id=lot,
                test_time='2026-09-28 08:00:00', template_id=fixture['template_id'], lot_config_id=config)
            self.assertEqual(daily['issues'], [])
            self.assertEqual(len(daily['items']), 1)
            self.assertTrue(daily['items'][0]['writable'], daily['items'][0]['issues'])
            self.assertEqual(daily['items'][0]['quality_review']['process_requirements'], lot_local)
            self.assertEqual(before_daily, self.dump())
            next_lot = create_qc_lot(qc_material_id=fixture['material_id'], lot_no='PROCESS-COPY', expiry_date='2028-12-31')
            create_qc_level(qc_material_lot_id=next_lot, level_name='复制新批水平', level_order=1)
            copied = copy_lot_config(source_lot_config_id=config, target_qc_material_lot_id=next_lot)
            copied_item = int(list_lot_config_items(copied).iloc[0]['id'])
            copied_review = decode(item_context('lot', copied_item)['quality_review_json'])
            self.assertEqual(copied_review['process_requirements'], lot_local)
            self.assertEqual(copied_review['status'], 'pending')
            record(identifier, ctx, search_record=search(), process_requirements={
                'source_ids': [], 'requirement_text': '后续项目已更改的本地要求。'})
            self.assertEqual(runtime_review('instant', batch_id), runtime)
            self.assertEqual(runtime_review('instant', fixture['batch_id']), original_runtime)
            with self.assertRaisesRegex(ValueError, '已有批次'):
                save_pending_review('lot', item_id, context=ctx, process_requirements=local)


if __name__ == '__main__':
    unittest.main(verbosity=2)
