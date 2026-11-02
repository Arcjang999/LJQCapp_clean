"""Different SKUs retain their own lots, evidence and history through one run."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.master_data_service import create_manufacturer, create_qc_material, create_qc_lot, create_qc_level, create_test_item
from services.qc_product_recommendation_service import validate_qc_product_choice, pack_choice_reviews
from services.quick_project_setup_service import prepare_quick_project_setup, save_quick_project_setup
from services.quick_project_setup_service import get_quick_setup_context
from services.material_workflow_service import copy_material_config, register_control_material
from services.project_config_service import list_lot_config_items, activate_lot_config, list_lot_item_levels
from services.quality_review_service import validate_quality_review
from services.lot_lifecycle_service import context_dataframe, record_lot_verification, workbench_systems
from services.project_config_io_service import build_lot_config_xlsx
from services.export_utils import xlsx_bytes_to_dataframes
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values, database_contents
from tests.quality_review_fixtures import confirm_fixture_lot
from zscore_logic import create_zscore_run, get_template_id_for_level_count


def multi_values(count=2):
    values = metadata_values('zscore', count)
    with get_connection() as c:
        c.execute("UPDATE md_qc_materials SET catalog_no='SKU-001',concentration_code='001' WHERE id=?",
                  (values['qc_material_id'],))
        c.execute("UPDATE md_qc_levels SET level_code='001' WHERE id=?", (values['level_ids'][0],))
    maker = create_manufacturer(display_name='另一厂家', categories=['qc_material'])
    products = [values['qc_material_id']]
    selected = [values['level_ids'][0]]
    for order in range(2, count + 1):
        product = create_qc_material(manufacturer_id=maker, generic_name='同名多水平质控品',
            catalog_no=f'SKU-{order:03d}', concentration_code=f'{order:03d}', concentration=str(order * 100))
        # Equal printed lot numbers across manufacturers must never merge products.
        lot = create_qc_lot(qc_material_id=product, lot_no='QUICK-QC-01', expiry_date='2028-12-31')
        level = create_qc_level(qc_material_lot_id=lot, level_name=f'水平{order}',
                               level_code=f'{order:03d}', level_order=1)
        products.append(product)
        selected.append(level)
    reviews = {str(product): validate_qc_product_choice(qc_material_id=product,
        reagent_id=values['reagent_id'], lab_instrument_id=values['lab_instrument_id'],
        test_item_id=values['test_item_id'], method_id=values['method_id'],
        manual_review={'confirmed_by':'逐产品核对人', 'evidence':f'已核对货号SKU-{order:03d}适用本检测系统'})
        for order, product in enumerate(products, 1)}
    values.update(level_ids=selected, level_product_ids=products, config_name='',
                  product_choice_reviews=reviews, product_choice_review=pack_choice_reviews(reviews))
    return values


def save(values):
    preview = prepare_quick_project_setup(values)
    return save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='multi-products')


class MultiProductWorkflowTests(unittest.TestCase):
    def test_two_and_three_products_save_as_one_complete_configuration(self):
        for count in (2, 3):
            with self.subTest(count=count), IsolatedDatabase():
                values = multi_values(count)
                context=get_quick_setup_context(values)
                self.assertEqual(list(range(1,count+1)),[level['level_order'] for level in context['levels']])
                self.assertEqual([1]*count,[level['physical_level_order'] for level in context['levels']])
                result = save(values)
                rows = list_lot_item_levels(result['lot_config_item_id']).to_dict('records')
                self.assertEqual(values['level_product_ids'], [row['qc_material_id'] for row in rows])
                self.assertEqual([f'SKU-{order:03d}' for order in range(1,count+1)],
                                 [row['catalog_no'] for row in rows])
                with get_connection() as c:
                    binding = c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                                        (result['lot_config_item_id'],)).fetchone()
                    snapshot = json.loads(binding['source_snapshot_json'])
                    config = c.execute('SELECT config_name FROM qc_lot_configs WHERE id=?',
                                       (result['lot_config_id'],)).fetchone()
                self.assertEqual(values['level_product_ids'], [row['qc_material_id'] for row in snapshot['levels']])
                self.assertIn('SKU-002', config[0])
                self.assertEqual([], validate_quality_review('lot', result['lot_config_item_id']))

    def test_missing_second_product_review_and_empty_slot_never_save_half_a_project(self):
        with IsolatedDatabase():
            values = multi_values()
            values['product_choice_reviews'].pop(str(values['level_product_ids'][1]))
            before = database_contents()
            with self.assertRaises(ValueError):
                prepare_quick_project_setup(values)
            self.assertEqual(before, database_contents())
            values['level_ids'][1] = None
            with self.assertRaises(ValueError):
                prepare_quick_project_setup(values)
            self.assertEqual(before, database_contents())

    def test_result_snapshot_and_export_keep_product_identity_after_live_names_change(self):
        with IsolatedDatabase():
            values = multi_values()
            result = save(values)
            batch = result['binding']['runtime_batch_id']
            create_zscore_run(batch, '2026-10-01 08:00:00', '材料追溯检查',
                [dict(level_id='Level 1',raw_value=100.0),dict(level_id='Level 2',raw_value=200.0)],
                template_id=get_template_id_for_level_count(2), required_n=20)
            trace = context_dataframe('zscore', batch)
            self.assertIn('SKU-001', trace.iloc[0]['qc_lot_no'])
            self.assertIn('SKU-002', trace.iloc[0]['qc_lot_no'])
            sheets = xlsx_bytes_to_dataframes(build_lot_config_xlsx(result['lot_config_id']))
            exported = sheets['水平均值和标准差']
            self.assertEqual(['SKU-001','SKU-002'], exported['产品货号'].tolist())
            frozen = trace.iloc[0]['qc_lot_no']
            with get_connection() as c:
                c.execute("UPDATE md_qc_materials SET generic_name='后续更名' WHERE id=?",
                          (values['level_product_ids'][1],))
            sync_zscore_workbench_bindings()
            self.assertEqual(frozen, context_dataframe('zscore',batch).iloc[0]['qc_lot_no'])

    def test_same_sku_new_lot_keeps_review_but_requires_new_quality_confirmation(self):
        with IsolatedDatabase():
            values = multi_values()
            result = save(values)
            level = register_control_material(material_id=values['level_product_ids'][1],
                lot_no='NEXT-LOT', expiry_date='2029-01-31', level_name='水平2', level_code='002')
            new = copy_material_config(source_config_id=result['lot_config_id'],
                selections={result['lot_config_item_id']:[values['level_ids'][0],level]})
            item = int(list_lot_config_items(new).iloc[0]['id'])
            from services.qc_product_recommendation_service import require_current_draft_choice
            with get_connection() as c:
                require_current_draft_choice('lot',item,c)
            confirm_fixture_lot(new)
            activate_lot_config(new)
            self.assertEqual(['QUICK-QC-01','NEXT-LOT'],list_lot_item_levels(item)['lot_no'].tolist())

    def test_second_product_lot_can_have_its_own_verification(self):
        with IsolatedDatabase():
            values = multi_values()
            result = save(values)
            systems = workbench_systems()
            with get_connection() as c:
                lot = c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',
                                (values['level_ids'][1],)).fetchone()[0]
            verification = record_lot_verification(template_item_id=result['template_item_id'],
                system_id=systems[0]['id'], qc_lot_id=lot, conclusion='pass', evidence='新批已完成检查',
                confirmed_by='核对人', confirmed_at='2026-10-01 08:00:00')
            self.assertGreater(verification,0)

    def test_panel_single_level_uses_its_actual_product_instead_of_the_header_product(self):
        with IsolatedDatabase():
            from services.project_config_service import create_project_template, save_template_items, list_template_items, activate_project_template
            from services.material_workflow_service import create_material_config
            from services.workbench_config_service import sync_lj_workbench_bindings
            from tests.quality_review_fixtures import confirm_fixture_project
            from database import add_result
            values=multi_values()
            single_test=create_test_item(chinese_name='第二产品单水平项目',default_unit_id=values['unit_id'])
            project=create_project_template(template_name='按水平组合产品的整组项目',
                lab_instrument_id=values['lab_instrument_id'],qc_material_id=values['qc_material_id'],
                default_reagent_id=values['reagent_id'])
            rows=[]
            for test,method,count,products in [(values['test_item_id'],'zscore',2,values['level_product_ids']),
                                             (single_test,'lj',1,[values['level_product_ids'][1]])]:
                review={str(product):validate_qc_product_choice(qc_material_id=product,
                    reagent_id=values['reagent_id'],lab_instrument_id=values['lab_instrument_id'],
                    test_item_id=test,method_id=values['method_id'],
                    manual_review={'confirmed_by':'整组核对人','evidence':'已分别核对本项实际产品'}) for product in products}
                rows.append(dict(test_item_id=test,qc_method=method,level_count=count,target_n=20,
                    input_value_type='raw',unit_id=values['unit_id'],method_id=values['method_id'],
                    reagent_id=values['reagent_id'],product_choice_review=pack_choice_reviews(review)))
            save_template_items(project,rows);confirm_fixture_project(project);activate_project_template(project)
            items={row['qc_method']:row['id'] for row in list_template_items(project).to_dict('records')}
            config=create_material_config(template_id=project,selections={items['zscore']:values['level_ids'],items['lj']:[values['level_ids'][1]]})
            confirm_fixture_lot(config);activate_lot_config(config);sync_lj_workbench_bindings()
            with get_connection() as c:
                binding=c.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_id=? AND qc_method='lj'",(config,)).fetchone()
            source=json.loads(binding['source_snapshot_json'])
            self.assertEqual(values['level_product_ids'][1],source['levels'][0]['qc_material_id'])
            self.assertEqual('另一厂家',source['qc_material_manufacturer_name'])
            add_result(binding['runtime_batch_id'],'2026-10-01 08:00:00',100,operator='整组检测')
            self.assertIn('SKU-002',context_dataframe('lj',binding['runtime_batch_id']).iloc[0]['qc_lot_no'])

    def test_different_specification_sku_cannot_be_hidden_inside_one_product(self):
        with IsolatedDatabase():
            values=multi_values()
            before=database_contents()
            with self.assertRaisesRegex(ValueError,'新增该货号'):
                register_control_material(material_id=values['qc_material_id'],lot_no='OTHER',
                    expiry_date='2029-12-31',level_name='另一货号',catalog_no='DIFFERENT-SKU')
            self.assertEqual(before,database_contents())

    def test_existing_specification_cannot_register_a_new_lot_under_another_sku(self):
        with IsolatedDatabase():
            values=multi_values()
            from services.material_workflow_service import create_material_spec
            spec_id=create_material_spec(values['qc_material_id'],'旧水平','OLD')
            with get_connection() as c:
                c.execute('UPDATE md_qc_material_specs SET catalog_no=? WHERE id=?',
                          ('DIFFERENT-SKU',spec_id))
            before=database_contents()
            with self.assertRaisesRegex(ValueError,'新增该货号'):
                register_control_material(material_id=values['qc_material_id'],lot_no='NEW-WRONG-SPEC',
                    expiry_date='2029-12-31',specification_id=spec_id)
            self.assertEqual(before,database_contents())

    def test_secondary_product_changes_do_not_rewrite_results_or_saved_report(self):
        from services.report_service import (build_zscore_monthly_report_package,
            build_zscore_monthly_report_pdf, save_zscore_monthly_report_snapshot)
        with IsolatedDatabase():
            values=multi_values()
            receipt=save(values)
            batch=receipt['binding']['runtime_batch_id']
            for day in range(1,26):
                create_zscore_run(batch,f'2026-10-{day:02d} 08:00:00','关联复核',
                    [dict(level_id=f'Level {order}',raw_value=100.0*order+(day%3-1)*0.2)
                     for order in (1,2)],template_id=get_template_id_for_level_count(2),required_n=20)
            package=build_zscore_monthly_report_package(batch,'2026-10')
            pdf=build_zscore_monthly_report_pdf(package)
            export=save_zscore_monthly_report_snapshot(package,pdf)
            tables=('zscore_runs','zscore_level_results','qc_result_contexts',
                    'qc_result_context_levels','qc_result_evaluations')
            def records():
                with get_connection() as c:
                    return {table:[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY id')]
                            for table in tables}
            before=records()
            trace=context_dataframe('zscore',batch).to_json(orient='records')
            with get_connection() as c:
                archived=tuple(c.execute('SELECT * FROM report_exports WHERE id=?',(export,)).fetchone())
                c.execute("UPDATE md_qc_materials SET generic_name='更名后的产品',catalog_no='NEW-SKU',is_disabled=1 WHERE id=?",
                          (values['level_product_ids'][1],))
                c.execute("UPDATE md_manufacturers SET display_name='更名后的厂家' WHERE id=(SELECT manufacturer_id FROM md_qc_materials WHERE id=?)",
                          (values['level_product_ids'][1],))
            sync_zscore_workbench_bindings()
            reopened=build_zscore_monthly_report_package(batch,'2026-10')
            self.assertEqual(before,records())
            self.assertEqual(trace,context_dataframe('zscore',batch).to_json(orient='records'))
            self.assertEqual(package.report.basic_info.qc_material,reopened.report.basic_info.qc_material)
            self.assertEqual(package.report.lot_trace['actual_lots'],reopened.report.lot_trace['actual_lots'])
            self.assertIn('SKU-002',json.dumps(reopened.report.lot_trace,ensure_ascii=False))
            self.assertNotIn('NEW-SKU',json.dumps(reopened.report.lot_trace,ensure_ascii=False))
            with get_connection() as c:
                self.assertEqual(archived,tuple(c.execute('SELECT * FROM report_exports WHERE id=?',(export,)).fetchone()))
                self.assertEqual(pdf,bytes(c.execute('SELECT pdf_bytes FROM report_export_files WHERE export_id=?',(export,)).fetchone()[0]))


if __name__=='__main__':
    unittest.main(verbosity=2)
