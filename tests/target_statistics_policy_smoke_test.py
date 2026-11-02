"""Statistics choices survive project/batch/Excel workflows without enabling parameters."""
from pathlib import Path
import sys
import json
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from database import get_connection
from migrations.target_statistics import ensure_target_statistics_schema
from services.target_statistics_policy_service import get_target_statistics_policy,save_target_statistics_policy
from services.quick_project_setup_service import prepare_quick_project_setup,save_quick_project_setup
from services.project_config_service import (list_template_items,list_lot_config_items,save_template_items,
                                           get_project_template)
from services.material_workflow_service import register_control_material,copy_material_config
from services.project_config_io_service import (build_project_template_xlsx,build_lot_config_xlsx,
    preview_project_template_xlsx,import_project_template_xlsx)
from services.export_utils import xlsx_bytes_to_dataframes,dataframes_to_xlsx_bytes
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values,database_contents


def setup(mode='cumulative'):
    values=metadata_values('lj',1)
    values['target_statistics_mode']=mode
    preview=prepare_quick_project_setup(values)
    receipt=save_quick_project_setup(values,expected_preview_hash=preview['preview_hash'],request_key='policy-fixture')
    return values,receipt


class StatisticsPolicyTests(unittest.TestCase):
    def test_new_setup_requires_an_explicit_valid_choice_without_partial_writes(self):
        with IsolatedDatabase():
            values=metadata_values()
            before=database_contents()
            for value in (None,'','automatic','current'):
                with self.subTest(mode=value):
                    values['target_statistics_mode']=value
                    with self.assertRaisesRegex(ValueError,'月度统计或累计统计'):
                        prepare_quick_project_setup(values)
                    self.assertEqual(before,database_contents())

    def test_choice_is_saved_and_copied_but_never_creates_a_parameter_version(self):
        for mode in ('monthly','cumulative'):
            with self.subTest(mode=mode),IsolatedDatabase():
                values,receipt=setup(mode)
                project,lot=receipt['template_item_id'],receipt['lot_config_item_id']
                self.assertEqual(mode,get_target_statistics_policy('project',project)['mode'])
                self.assertEqual(mode,get_target_statistics_policy('lot',lot)['mode'])
                self.assertEqual(mode,list_template_items(receipt['template_id']).iloc[0]['target_statistics_mode'])
                self.assertEqual(mode,list_lot_config_items(receipt['lot_config_id']).iloc[0]['target_statistics_mode'])
                changed='monthly' if mode=='cumulative' else 'cumulative'
                with get_connection() as c:
                    frozen=c.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE id=?',
                                     (receipt['binding']['id'],)).fetchone()[0]
                save_target_statistics_policy('lot',lot,changed,expected_revision=1,confirmed_by='参数核对人')
                next_level=register_control_material(material_id=values['qc_material_id'],lot_no='NEXT-POLICY-LOT',
                    expiry_date='2029-12-31',level_name='水平1')
                copied=copy_material_config(source_config_id=receipt['lot_config_id'],selections={lot:[next_level]})
                new_item=int(list_lot_config_items(copied).iloc[0]['id'])
                self.assertEqual(changed,get_target_statistics_policy('lot',new_item)['mode'])
                self.assertEqual(mode,get_target_statistics_policy('project',project)['mode'])
                with get_connection() as c:
                    self.assertEqual(0,c.execute('SELECT COUNT(*) FROM qc_target_profiles').fetchone()[0])
                    self.assertEqual(frozen,c.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE id=?',
                        (receipt['binding']['id'],)).fetchone()[0])
                    self.assertEqual([],c.execute('PRAGMA foreign_key_check').fetchall())

    def test_unrelated_project_edit_keeps_existing_choice_and_no_empty_choice_is_invented(self):
        with IsolatedDatabase():
            _,receipt=setup('monthly')
            rows=list_template_items(receipt['template_id']).to_dict('records')
            rows[0].pop('target_statistics_mode')
            rows[0]['notes']='仅修改项目备注'
            save_template_items(receipt['template_id'],rows)
            self.assertEqual('monthly',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            with get_connection() as c:
                c.execute('DELETE FROM qc_target_statistics_policies')
            before=database_contents()
            self.assertEqual('',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            self.assertEqual('',get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
            self.assertEqual(before,database_contents())

    def test_policy_revision_and_idempotence_reject_stale_updates_without_side_effects(self):
        with IsolatedDatabase():
            _,receipt=setup()
            identifier=receipt['lot_config_item_id']
            before=database_contents()
            save_target_statistics_policy('lot',identifier,'cumulative',expected_revision=1)
            self.assertEqual(before,database_contents())
            save_target_statistics_policy('lot',identifier,'monthly',expected_revision=1)
            self.assertEqual(2,get_target_statistics_policy('lot',identifier)['revision'])
            after=database_contents()
            with self.assertRaisesRegex(ValueError,'统计方式已修改'):
                save_target_statistics_policy('lot',identifier,'cumulative',expected_revision=1)
            self.assertEqual(after,database_contents())
            with self.assertRaises(ValueError):save_target_statistics_policy('lot',identifier,'current')
            self.assertEqual(after,database_contents())

    def test_excel_round_trip_keeps_choice_and_invalid_mode_is_atomic(self):
        with IsolatedDatabase():
            _,receipt=setup('monthly')
            sheets=xlsx_bytes_to_dataframes(build_project_template_xlsx(receipt['template_id']))
            self.assertEqual('月度统计',sheets['项目配置'].iloc[0]['均值和SD统计方式'])
            lot_sheets=xlsx_bytes_to_dataframes(build_lot_config_xlsx(receipt['lot_config_id']))
            self.assertEqual('月度统计',lot_sheets['项目配置'].iloc[0]['均值和SD统计方式'])
            sheets['项目配置']['均值和SD统计方式']='累计统计'
            content=dataframes_to_xlsx_bytes(sheets)
            preview,errors=preview_project_template_xlsx(content)
            self.assertEqual([],errors)
            self.assertEqual('累计统计',preview.iloc[0]['均值和SD统计方式'])
            import_project_template_xlsx(receipt['template_id'],content)
            self.assertEqual('cumulative',get_target_statistics_policy('project',receipt['template_item_id'])['mode'])
            self.assertEqual('monthly',get_target_statistics_policy('lot',receipt['lot_config_item_id'])['mode'])
            sheets['项目配置']['均值和SD统计方式']='自动切换'
            invalid=dataframes_to_xlsx_bytes(sheets)
            before=database_contents()
            with self.assertRaises(ValueError):import_project_template_xlsx(receipt['template_id'],invalid)
            self.assertEqual(before,database_contents())

    def test_schema_is_repeatable_and_existing_rows_are_not_filled_or_rewritten(self):
        with IsolatedDatabase():
            _,receipt=setup()
            with get_connection() as c:c.execute('DELETE FROM qc_target_statistics_policies')
            before=database_contents()
            with get_connection() as c:
                ensure_target_statistics_schema(c)
                ensure_target_statistics_schema(c)
            self.assertEqual(before,database_contents())
            self.assertEqual(0,get_target_statistics_policy('lot',receipt['lot_config_item_id'])['revision'])


if __name__=='__main__':unittest.main(verbosity=2)
