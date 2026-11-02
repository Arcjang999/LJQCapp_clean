"""Independent statistics policy, physical-material and frozen-history associations."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean,stdev
import json
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from database import add_result,get_connection
from services.lot_lifecycle_service import append_evaluation,create_target_profile,target_profile
from services.material_workflow_service import copy_material_config,register_control_material
from services.project_config_service import activate_lot_config,list_lot_config_items,list_template_items
from services.quick_project_setup_service import prepare_quick_project_setup,save_quick_project_setup
from services.target_statistics_policy_service import get_target_statistics_policy,save_target_statistics_policy
from services.target_statistics_service import get_target_statistics_candidate,save_target_statistics_candidate
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.multi_product_control_workflow_smoke_test import multi_values
from tests.quick_project_setup_smoke_test import database_contents,metadata_values
from tests.quality_review_fixtures import confirm_fixture_lot
from zscore_logic import create_zscore_run,disable_zscore_building_run

AS_OF=datetime(2026,11,2,8,0)
HISTORY_TABLES=('results','zscore_runs','zscore_level_results','qc_result_contexts',
    'qc_result_context_levels','qc_result_evaluations','report_exports','report_export_files')


def save_values(values,request='association-source'):
    preview=prepare_quick_project_setup(values)
    return save_quick_project_setup(values,expected_preview_hash=preview['preview_hash'],request_key=request)


def fixture(*,mode='cumulative',method='zscore',target_n=20):
    values=multi_values(2) if method=='zscore' else metadata_values('lj',1)
    values.update(target_statistics_mode=mode,target_n=target_n)
    receipt=save_values(values)
    with get_connection() as connection:
        binding=dict(connection.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                                        (receipt['lot_config_item_id'],)).fetchone())
    return values,receipt,binding


def profile(binding,when,means=(100.0,200.0)):
    return create_target_profile(method=binding['qc_method'],batch_id=binding['runtime_batch_id'],
        levels=[dict(level_id=f'Level {order}',mean=value,sd=10.0*order)
                for order,value in enumerate(means,1)],source='manual',evidence='独立联动检查的已确认参数',
        confirmed_by='参数联动核对人',effective_at=when)


def run(binding,when,pair):
    with get_connection() as connection:
        required=connection.execute('SELECT target_n FROM batches WHERE id=?',
                                    (binding['runtime_batch_id'],)).fetchone()[0]
    saved=create_zscore_run(binding['runtime_batch_id'],when,'统计关联检查',
        [dict(level_id=f'Level {order}',raw_value=value) for order,value in enumerate(pair,1)],
        template_id='2_level_classic',required_n=int(required))
    return int(saved['run_id'])


def formal_history():
    values,receipt,binding=fixture()
    profile(binding,'2026-09-01')
    ids=[run(binding,'2026-09-20 08:00:00',(99.0,198.0)),
         run(binding,'2026-09-21 08:00:00',(101.0,202.0))]
    profile(binding,'2026-10-01 00:00:00',(102.0,204.0))
    for when,pair in [('2026-10-01 00:00:00',(100.0,200.0)),
                      ('2026-10-20 08:00:00',(103.0,206.0)),
                      ('2026-10-31 23:59:59',(101.0,202.0)),
                      ('2026-11-01 00:00:00',(102.0,204.0))]:
        ids.append(run(binding,when,pair))
    return values,receipt,binding,ids


def candidate(binding,strategy='cumulative'):
    return get_target_statistics_candidate(binding['id'],strategy,as_of=AS_OF)


def confirm(binding,proposed,**changes):
    args=dict(expected_fingerprint=proposed['fingerprint'],confirmed_by='统计方式确认人',
        evidence='逐水平核对样本、均值和SD后明确启用。',effective_at='2026-11-02 08:01:00',confirmed=True,
        expected_policy_revision=proposed['policy_revision'])
    args.update(changes)
    with patch('services.target_statistics_service.local_now',return_value=AS_OF):
        return save_target_statistics_candidate(binding['id'],proposed,**args)


def history_rows():
    with get_connection() as connection:
        return {table:{int(row['__row_key']):tuple(row)[1:] for row in connection.execute('SELECT rowid AS __row_key,* FROM '+table)}
                for table in HISTORY_TABLES}


def copied_config(receipt,selected):
    config=copy_material_config(source_config_id=receipt['lot_config_id'],
        selections={receipt['lot_config_item_id']:selected})
    item=int(list_lot_config_items(config).iloc[0]['id'])
    return config,item


def instant_transfer_fixture():
    from services.instant_service import save_instant_result,confirm_instant_transfer_to_lj
    from services.target_profile_edit_service import list_target_profile_batches
    values=metadata_values('instant',1)
    values['target_statistics_mode']='monthly'
    receipt=save_values(values)
    source=receipt['binding']
    for day in range(1,21):
        save_instant_result(batch_id=source['runtime_batch_id'],test_time=f'2026-10-{day:02d} 08:00:00',
            operator='即时转入关联核对',value=100+(day%3-1)*0.2,log_value=None)
    moved=confirm_instant_transfer_to_lj(source['runtime_batch_id'])
    contexts=list_target_profile_batches()
    context=next(row for row in contexts if row['binding']['qc_method']=='lj'
                 and row['binding']['runtime_batch_id']==moved['target_batch_id'])
    return values,receipt,source,moved,context


class TargetStatisticsAssociationTests(unittest.TestCase):
    def assert_rejected_without_write(self,action):
        before=database_contents()
        with self.assertRaises(ValueError):
            action()
        self.assertEqual(before,database_contents())

    def test_creation_requires_a_mode_and_project_batch_and_copy_keep_the_choice(self):
        for mode in ('monthly','cumulative'):
            with self.subTest(mode=mode),IsolatedDatabase():
                values=multi_values(2)
                values.pop('target_statistics_mode',None)
                self.assert_rejected_without_write(lambda:prepare_quick_project_setup(values))
                values['target_statistics_mode']=mode
                receipt=save_values(values)
                self.assertEqual(mode,get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
                self.assertEqual(mode,get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
                self.assertEqual(mode,list_template_items(receipt['template_id']).iloc[0]['target_statistics_mode'])
                replacement=register_control_material(material_id=values['level_product_ids'][1],
                    lot_no='POLICY-NEXT-LOT',expiry_date='2029-12-31',level_name='新批水平',level_code='P2')
                _,item=copied_config(receipt,[values['level_ids'][0],replacement])
                self.assertEqual(mode,get_target_statistics_policy('lot',item)['mode'])

    def test_previous_calendar_month_and_cumulative_include_current_month_across_versions(self):
        with IsolatedDatabase():
            values,_,binding,ids=formal_history()
            before=database_contents()
            monthly=candidate(binding,'monthly');cumulative=candidate(binding)
            self.assertEqual(before,database_contents(),'查看统计不能新建参数、改结果或修改统计方式')
            self.assertTrue(monthly['ready'],monthly['issues'])
            self.assertTrue(cumulative['ready'],cumulative['issues'])
            self.assertEqual('2026-10-01 00:00:00',monthly['period_start'])
            self.assertEqual('2026-11-01 00:00:00',monthly['period_end'])
            self.assertEqual([3,3],[row['n'] for row in monthly['levels']])
            self.assertEqual([6,6],[row['n'] for row in cumulative['levels']])
            self.assertEqual(values['level_ids'],[row['qc_level_id'] for row in cumulative['levels']])
            self.assertEqual(values['level_product_ids'],[row['qc_material_id'] for row in cumulative['levels']])
            self.assertEqual(['SKU-001','SKU-002'],[row['catalog_no'] for row in cumulative['levels']])
            for order,row in enumerate(monthly['levels'],1):
                self.assertEqual(ids[2:5],[source['result_id'] for source in row['included_sources']])
                self.assertAlmostEqual(mean([100,103,101])*order,row['mean'])
                self.assertAlmostEqual(stdev([100,103,101])*order,row['sd'])
            for row in cumulative['levels']:
                self.assertEqual(ids,[source['result_id'] for source in row['included_sources']])
                self.assertTrue(all(source['evaluation_id'] for source in row['included_sources']))

    def test_actual_level_history_survives_reordering_but_new_lots_and_other_projects_do_not_merge(self):
        with IsolatedDatabase():
            values,receipt,binding,ids=formal_history()
            other=deepcopy(values);other['template_name']='相同检测资料另一个项目'
            foreign=save_values(other,'association-other-project')['binding']
            profile(foreign,'2026-09-01')
            foreign_ids=[run(foreign,'2026-10-10 08:00:00',(100.5,201.0)),
                         run(foreign,'2026-10-11 08:00:00',(99.5,199.0))]
            replacement=register_control_material(material_id=values['level_product_ids'][1],
                lot_no='NEXT-SECOND-SKU',expiry_date='2029-12-31',level_name='新批首水平',level_code='N2')
            copied,item=copied_config(receipt,[replacement,values['level_ids'][0]])
            confirm_fixture_lot(copied);activate_lot_config(copied);sync_zscore_workbench_bindings()
            with get_connection() as connection:
                new_binding=dict(connection.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                                                     (item,)).fetchone())
            profile(new_binding,'2026-11-01 01:00:00',(200.0,100.0))
            new_ids=[run(new_binding,'2026-11-01 02:00:00',(201.0,99.0)),
                     run(new_binding,'2026-11-01 03:00:00',(203.0,101.0))]
            proposed=candidate(new_binding)
            self.assertTrue(proposed['ready'],proposed['issues'])
            self.assertEqual([replacement,values['level_ids'][0]],[row['qc_level_id'] for row in proposed['levels']])
            self.assertEqual([2,8],[row['n'] for row in proposed['levels']])
            retained=proposed['levels'][1]['included_sources']
            self.assertEqual(ids+new_ids,[row['result_id'] for row in retained])
            self.assertEqual({'Level 1','Level 2'},{row['source_level_id'] for row in retained})
            self.assertFalse(set(foreign_ids)&{row['result_id'] for row in retained})
            self.assertEqual(set(new_ids),{row['result_id'] for row in proposed['levels'][0]['included_sources']})

    def test_explicit_confirmation_freezes_sample_chain_and_only_later_results_use_new_version(self):
        from services.report_service import (build_zscore_monthly_report_package,
            build_zscore_monthly_report_pdf,save_zscore_monthly_report_snapshot)
        with IsolatedDatabase():
            values,receipt,binding,_=formal_history()
            package=build_zscore_monthly_report_package(binding['runtime_batch_id'],'2026-10')
            save_zscore_monthly_report_snapshot(package,build_zscore_monthly_report_pdf(package))
            before=history_rows()
            proposed=candidate(binding,'monthly')
            self.assert_rejected_without_write(lambda:confirm(binding,proposed,confirmed=False))
            identifier=confirm(binding,proposed)
            self.assertEqual(before,history_rows())
            saved=target_profile('zscore',binding['runtime_batch_id'],profile_id=identifier)
            self.assertEqual('revision',saved['source'])
            self.assertEqual(proposed,json.loads(saved['source_statistics_json']))
            self.assertEqual('monthly',get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
            self.assertEqual('cumulative',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            later=run(binding,'2026-11-02 09:00:00',tuple(row['mean'] for row in proposed['levels']))
            with get_connection() as connection:
                context=connection.execute('SELECT target_profile_id FROM qc_result_contexts WHERE zscore_run_id=?',
                                           (later,)).fetchone()
                self.assertEqual(identifier,context[0])
            after=history_rows()
            for table,rows in before.items():
                for key,value in rows.items():
                    self.assertEqual(value,after[table][key],table)
            replacement=register_control_material(material_id=values['level_product_ids'][1],
                lot_no='AFTER-EXPLICIT-SWITCH',expiry_date='2029-12-31',level_name='另批水平',level_code='S2')
            _,item=copied_config(receipt,[values['level_ids'][0],replacement])
            self.assertEqual('monthly',get_target_statistics_policy('lot',item)['mode'])

    def test_new_result_evaluation_or_policy_revision_invalidates_an_old_candidate_atomically(self):
        for change in ('new_result','evaluation','policy_aba'):
            with self.subTest(change=change),IsolatedDatabase():
                _,receipt,binding,ids=formal_history()
                proposed=candidate(binding)
                if change=='new_result':
                    run(binding,'2026-11-01 01:00:00',(101.0,202.0))
                elif change=='evaluation':
                    append_evaluation('zscore',ids[-1],{'independent':'changed evaluation'},reason='independent-source-change')
                else:
                    for mode in ('monthly','cumulative'):
                        policy=get_target_statistics_policy('lot',receipt['lot_config_item_id'])
                        save_target_statistics_policy('lot',receipt['lot_config_item_id'],mode,
                            expected_revision=policy['revision'],confirmed_by='另一窗口核对人')
                self.assert_rejected_without_write(lambda:confirm(binding,proposed))

    def test_foreign_material_forged_values_and_retroactive_time_cannot_create_a_version(self):
        with IsolatedDatabase():
            _,_,binding,_=formal_history()
            proposed=candidate(binding)
            foreign=deepcopy(proposed)
            foreign['levels'][1]['qc_level_id']=foreign['levels'][0]['qc_level_id']
            foreign['levels'][1]['qc_material_id']=foreign['levels'][0]['qc_material_id']
            self.assert_rejected_without_write(lambda:confirm(binding,foreign))
            forged=deepcopy(proposed);forged['levels'][0]['mean']+=1000
            self.assert_rejected_without_write(lambda:confirm(binding,forged))
            self.assert_rejected_without_write(lambda:confirm(binding,proposed,effective_at='2026-10-02'))
            self.assert_rejected_without_write(lambda:confirm(binding,proposed,confirmed_by=''))

    def test_zscore_disabled_building_and_first_formal_reject_are_excluded_for_all_levels(self):
        with IsolatedDatabase():
            _,_,binding=fixture(target_n=5)
            ids=[]
            for day,value in enumerate((100,101,120,99),1):
                ids.append(run(binding,f'2026-10-{day:02d} 08:00:00',(value,2*value)))
            disable_zscore_building_run(ids[2])
            for day,pair in enumerate(((100.5,201),(99.5,199),(150,200),(100,200)),5):
                ids.append(run(binding,f'2026-10-{day:02d} 08:00:00',pair))
            with get_connection() as connection:
                rejected=connection.execute('SELECT phase,run_status FROM zscore_runs WHERE id=?',(ids[6],)).fetchone()
                self.assertEqual(('formal_qc','reject'),tuple(rejected))
            proposed=candidate(binding)
            for level in proposed['levels']:
                self.assertEqual([ids[index] for index in (0,1,3,4,5,7)],
                                 [row['result_id'] for row in level['included_sources']])
                self.assertEqual(6,level['n'])

    def test_lj_uses_the_same_valid_building_and_formal_in_control_boundary(self):
        from qc_logic import disable_lj_building_result
        with IsolatedDatabase():
            _,_,binding=fixture(method='lj',target_n=5)
            ids=[]
            for day,value in enumerate((100,101,99,120),1):
                ids.append(add_result(binding['runtime_batch_id'],f'2026-10-{day:02d} 08:00:00',value,operator='单水平关联'))
            disable_lj_building_result(ids[3])
            for day,value in enumerate((100.5,99.5,100,150),5):
                ids.append(add_result(binding['runtime_batch_id'],f'2026-10-{day:02d} 08:00:00',value,operator='单水平关联'))
            proposed=candidate(binding)
            self.assertEqual(6,proposed['levels'][0]['n'])
            self.assertEqual([ids[index] for index in (0,1,2,4,5,6)],
                [row['result_id'] for row in proposed['levels'][0]['included_sources']])

    def test_completed_original_target_count_can_confirm_before_any_profile_exists(self):
        for method in ('lj','zscore'):
            with self.subTest(method=method),IsolatedDatabase():
                _,_,binding=fixture(method=method,target_n=20)
                for day in range(1,21):
                    offset=(day%3-1)*0.2
                    if method=='lj':
                        add_result(binding['runtime_batch_id'],f'2026-10-{day:02d} 08:00:00',100+offset,
                                   operator='完成原建靶数量')
                    else:
                        run(binding,f'2026-10-{day:02d} 08:00:00',(100+offset,200+2*offset))
                self.assertIsNone(target_profile(method,binding['runtime_batch_id']))
                before=database_contents()
                proposed=candidate(binding)
                self.assertEqual(before,database_contents())
                self.assertTrue(proposed['ready'],proposed['issues'])
                self.assertTrue(all(level['n']==20 for level in proposed['levels']))
                frozen=history_rows()
                identifier=confirm(binding,proposed)
                self.assertEqual(frozen,history_rows())
                saved=target_profile(method,binding['runtime_batch_id'],profile_id=identifier)
                self.assertEqual(1,saved['version_no'])
                self.assertEqual('revision',saved['source'])

    def test_instant_transfer_statistics_use_only_target_lj_and_preserve_original_records(self):
        from services.instant_service import save_instant_result,confirm_instant_transfer_to_lj
        from services.target_profile_edit_service import list_target_profile_batches
        with IsolatedDatabase():
            values=metadata_values('instant',1);values['target_statistics_mode']='monthly'
            receipt=save_values(values);source=receipt['binding']
            for day in range(1,21):
                save_instant_result(batch_id=source['runtime_batch_id'],test_time=f'2026-10-{day:02d} 08:00:00',
                    operator='即时转入关联核对',value=100+(day%3-1)*0.2,log_value=None)
            def instant_rows(*,without_transfer=False):
                with get_connection() as connection:
                    return [{key:value for key,value in dict(row).items()
                             if not without_transfer or not key.startswith('lj_transfer_')}
                            for row in connection.execute('SELECT * FROM instant_results ORDER BY id')]
            with get_connection() as connection:
                bindings=[tuple(row) for row in connection.execute('SELECT * FROM qc_workbench_bindings ORDER BY id')]
                frozen_source=connection.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE id=?',
                                                (source['id'],)).fetchone()[0]
            raw_before=instant_rows(without_transfer=True)
            recorded_before=history_rows()
            moved=confirm_instant_transfer_to_lj(source['runtime_batch_id'])
            self.assertEqual(raw_before,instant_rows(without_transfer=True))
            after_transfer=history_rows()
            for table,rows in recorded_before.items():
                for key,value in rows.items():
                    self.assertEqual(value,after_transfer[table][key],table)
            with get_connection() as connection:
                self.assertEqual(bindings,[tuple(row) for row in connection.execute('SELECT * FROM qc_workbench_bindings ORDER BY id')])
                self.assertEqual(frozen_source,connection.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE id=?',
                    (source['id'],)).fetchone()[0])
                target_ids=[row[0] for row in connection.execute('SELECT id FROM results WHERE batch_id=? ORDER BY id',
                    (moved['target_batch_id'],))]
            contexts=list_target_profile_batches()
            context=next(row for row in contexts if row['binding']['qc_method']=='lj'
                         and row['binding']['runtime_batch_id']==moved['target_batch_id'])
            binding=context['binding']
            self.assertLess(binding['id'],0)
            self.assertTrue(context['editable'],context['read_only_reason'])
            self.assertEqual('monthly',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            self.assertEqual('monthly',get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
            proposed=candidate(binding)
            self.assertTrue(proposed['ready'],proposed['issues'])
            self.assertEqual('monthly',proposed['current_strategy'])
            self.assertEqual(20,proposed['levels'][0]['n'])
            samples=proposed['levels'][0]['included_sources']
            self.assertEqual(target_ids,[row['result_id'] for row in samples])
            self.assertEqual({'lj'},{row['method'] for row in samples})
            self.assertEqual({moved['target_batch_id']},{row['batch_id'] for row in samples})
            self.assertEqual(source['id'],proposed['transfer_chain']['source_binding_id'])
            self.assertEqual(json.loads(frozen_source),proposed['transfer_chain']['source_snapshot'])
            frozen=history_rows();source_rows=instant_rows()
            identifier=confirm(binding,proposed)
            self.assertEqual(frozen,history_rows())
            self.assertEqual(source_rows,instant_rows())
            self.assertEqual('cumulative',get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
            self.assertEqual('monthly',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            saved=target_profile('lj',moved['target_batch_id'],profile_id=identifier)
            self.assertEqual(proposed,json.loads(saved['source_statistics_json']))
            later=add_result(moved['target_batch_id'],'2026-11-02 09:00:00',proposed['levels'][0]['mean'],
                             operator='转入后采用新参数')
            with get_connection() as connection:
                self.assertEqual(identifier,connection.execute('SELECT target_profile_id FROM qc_result_contexts WHERE lj_result_id=?',
                    (later,)).fetchone()[0])
                self.assertEqual(bindings,[tuple(row) for row in connection.execute('SELECT * FROM qc_workbench_bindings ORDER BY id')])
            self.assertEqual(source_rows,instant_rows())

    def test_forged_converted_binding_and_mismatched_instant_origin_reject_without_writes(self):
        from services.target_profile_edit_service import get_target_profile_context
        with IsolatedDatabase():
            _,_,source,moved,context=instant_transfer_fixture()
            binding=context['binding'];proposed=candidate(binding)
            self.assert_rejected_without_write(lambda:get_target_profile_context(-999999))
            self.assert_rejected_without_write(lambda:get_target_statistics_candidate(-999999,'cumulative',as_of=AS_OF))
            self.assert_rejected_without_write(lambda:save_target_statistics_candidate(source['id'],proposed,
                expected_fingerprint=proposed['fingerprint'],confirmed_by='错配来源',evidence='必须拒绝instant冒用LJ来源',
                effective_at='2026-11-02 08:01:00',confirmed=True))
            with get_connection() as connection:
                connection.execute('UPDATE batches SET source_instant_batch_id=? WHERE id=?',
                                   (source['runtime_batch_id']+999,moved['target_batch_id']))
            self.assert_rejected_without_write(lambda:get_target_profile_context(binding['id']))
            self.assert_rejected_without_write(lambda:candidate(binding))
            self.assert_rejected_without_write(lambda:confirm(binding,proposed))

    def test_complete_backup_restores_statistics_versions_history_and_original_report_files(self):
        import database
        from services.report_service import build_zscore_monthly_report_package,save_zscore_monthly_report_snapshot
        from services.storage_service import create_database_backup,restore_database_from_backup_file,validate_backup_file
        from tests.storage_smoke_test import TemporaryStorageContext
        with TemporaryStorageContext() as storage:
            database.init_db()
            _,receipt,binding,_=formal_history()
            package=build_zscore_monthly_report_package(binding['runtime_batch_id'],'2026-10')
            export=save_zscore_monthly_report_snapshot(package)
            proposed=candidate(binding,'monthly')
            identifier=confirm(binding,proposed)
            run(binding,'2026-11-02 09:00:00',tuple(level['mean'] for level in proposed['levels']))
            def state():
                with get_connection() as connection:
                    rows={table:[tuple(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                          for table in ('qc_target_statistics_policies','qc_target_profiles','qc_report_archive_files')}
                    hashes={}
                    for table,column in (('report_export_files','pdf_bytes'),('qc_report_archive_files','archive_bytes')):
                        for row in connection.execute('SELECT export_id,'+column+',sha256 FROM '+table):
                            digest=sha256(bytes(row[column])).hexdigest()
                            self.assertEqual(row['sha256'],digest)
                            hashes[(table,int(row['export_id']))]=digest
                return dict(rows=rows,history=history_rows(),report_file_hashes=hashes)
            before=state()
            policy=get_target_statistics_policy('lot',receipt['lot_config_item_id'])
            self.assertEqual('monthly',policy['mode'])
            self.assertGreater(policy['revision'],1)
            self.assertEqual('cumulative',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            saved=target_profile('zscore',binding['runtime_batch_id'],profile_id=identifier)
            self.assertEqual(3,saved['version_no'])
            self.assertEqual(proposed,json.loads(saved['source_statistics_json']))
            self.assertIn(('report_export_files',export),before['report_file_hashes'])
            self.assertIn(('qc_report_archive_files',export),before['report_file_hashes'])
            original_path=database.get_db_path()
            backup=create_database_backup(storage.root/'complete_backups')
            self.assertTrue(validate_backup_file(backup.target_path)[0])
            self.assertEqual((),backup.warnings)
            self.assertEqual(before,state())
            destination=storage.root/'restored_elsewhere'/'statistics-restored.db'
            destination.parent.mkdir(parents=True)
            self.assertNotEqual(original_path.resolve(),destination.resolve())
            with patch.object(database,'DB_PATH',destination):
                database.init_db()
                with get_connection() as connection:
                    self.assertEqual(0,connection.execute('SELECT COUNT(*) FROM qc_target_statistics_policies').fetchone()[0])
                    self.assertEqual(0,connection.execute('SELECT COUNT(*) FROM qc_target_profiles').fetchone()[0])
                restored=restore_database_from_backup_file(backup.target_path)
                self.assertEqual(destination,restored.target_path)
                self.assertTrue(restored.protection_backup_path.is_file())
                self.assertEqual((),restored.warnings)
                database.init_db()
                self.assertEqual(before,state())
                self.assertEqual(policy,get_target_statistics_policy('lot',receipt['lot_config_item_id']))
                recovered=target_profile('zscore',binding['runtime_batch_id'],profile_id=identifier)
                self.assertEqual(saved,recovered)
            self.assertEqual(before,state(),'备份与异路径恢复不应改写原隔离源库')
            print('BACKUP_RESTORE_VERIFIED',json.dumps(dict(policy_rows=len(before['rows']['qc_target_statistics_policies']),
                lot_mode=policy['mode'],lot_revision=policy['revision'],profile_version=saved['version_no'],
                source_statistics_sha256=sha256(saved['source_statistics_json'].encode()).hexdigest(),
                pdf_sha256=before['report_file_hashes'][('report_export_files',export)],
                archive_sha256=before['report_file_hashes'][('qc_report_archive_files',export)],
                separate_restore_path=True,all_policy_profile_history_rows_equal=True),ensure_ascii=False),flush=True)


if __name__=='__main__':
    unittest.main(verbosity=2)
