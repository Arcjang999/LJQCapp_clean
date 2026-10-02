"""T2A-02/03/04/05 on new isolated databases and the reviewed real directory."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import database
from services import master_data_service as master
from services.master_data_edit_service import get_master_record_context,save_master_record
from services import product_directory_service as directory
from services.material_workflow_service import register_control_material


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary=TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.original={name:getattr(database,name) for name in ('DB_PATH','DEFAULT_DB_PATH','STORAGE_CONFIG_PATH','LEGACY_DB_CANDIDATES')}
        database.DB_PATH=database.DEFAULT_DB_PATH=self.root/'isolated.db'
        database.STORAGE_CONFIG_PATH=self.root/'config.json'
        database.LEGACY_DB_CANDIDATES=[]
        database.init_db()
        self.manufacturer=master.create_manufacturer(display_name='邦德盛',categories=['qc_material'])

    def tearDown(self):
        for name,value in self.original.items():setattr(database,name,value)
        self.temporary.cleanup()

    def dump(self):
        with database.read_snapshot() as connection:return '\n'.join(connection.iterdump())

    def publish(self,package=None):
        package=package or directory.load_bondson_directory_package()
        preview=directory.preview_product_directory(package,self.manufacturer)
        self.assertEqual(preview['issues'],[])
        result=directory.publish_product_directory(package,self.manufacturer,expected_preview_hash=preview['preview_hash'],published_by='资料核对员')
        return package,result

    def test_manufacturer_roles_validation_fingerprint_removal_and_rollback(self):
        reagent=master.create_manufacturer(display_name='仅试剂厂家',categories=['reagent'])
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'质控品厂家'):
            master.create_qc_material(manufacturer_id=reagent,generic_name='不能保存')
        self.assertEqual(before,self.dump())
        mixed=master.create_manufacturer(display_name='多类厂家',categories=['qc_material','instrument','instrument'])
        self.assertEqual(master.get_manufacturer_categories(mixed),['instrument','qc_material'])
        master.create_instrument_model(manufacturer_id=mixed,generic_name='仪器',model='A')
        master.create_qc_material(manufacturer_id=mixed,generic_name='材料')
        context=get_master_record_context('manufacturer',mixed)
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'仍在使用'):
            master.set_manufacturer_categories(mixed,['qc_material'],expected_fingerprint=context['fingerprint'])
        self.assertEqual(before,self.dump())
        master.set_manufacturer_categories(mixed,['qc_material','instrument','reagent'],expected_fingerprint=context['fingerprint'])
        with self.assertRaisesRegex(ValueError,'已修改'):
            save_master_record('manufacturer',{'notes':'过期写入'},entity_id=mixed,expected_fingerprint=context['fingerprint'])
        before=self.dump()
        with patch.object(master,'_save_manufacturer_categories',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):master.create_manufacturer(display_name='回滚厂家',categories=['reagent'])
        self.assertEqual(before,self.dump())
        draft=master.create_manufacturer(display_name='未分类草稿')
        self.assertNotIn(draft,master.list_manufacturers(category='qc_material').id.tolist())
        master.set_master_entity_disabled('manufacturer',reagent,is_disabled=True,reason='测试停用')
        self.assertNotIn(reagent,master.list_manufacturers(category='reagent').id.tolist())
        from migrations.product_directory import ensure_product_directory_schema
        before=self.dump()
        with database.atomic_write() as connection:
            ensure_product_directory_schema(connection)
            ensure_product_directory_schema(connection)
        self.assertEqual(before,self.dump())

    def test_real_directory_preview_publish_idempotency_exact_text_and_exclusion(self):
        package=directory.load_bondson_directory_package()
        before=self.dump();preview=directory.preview_product_directory(package,self.manufacturer)
        self.assertEqual(before,self.dump())
        self.assertEqual((preview['included_count'],preview['excluded_count']),(1054,252))
        _,result=self.publish(package)
        frame=directory.list_directory_products(result['release_id'])
        expected={row['product_code']:[row[key] for key in directory.PRODUCT_FIELDS] for row in package['records']}
        actual={row['product_code']:[row[key] for key in directory.PRODUCT_FIELDS] for row in frame.to_dict('records')}
        self.assertEqual(expected,actual)
        all_rows=directory.list_directory_products(result['release_id'],include_excluded=True)
        excluded=all_rows[all_rows.excluded_reason!='']
        self.assertEqual(len(excluded),252)
        self.assertTrue(excluded.product_id.isna().all())
        self.assertEqual({r['product_code']:r['excluded_reason'] for r in excluded.to_dict('records')},
                         {r['product_code']:r['excluded_reason'] for r in package['excluded']})
        with database.read_snapshot() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM md_qc_material_lots').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM md_qc_levels').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM md_product_coverage').fetchone()[0],0)
        before=self.dump();_,again=self.publish(package)
        self.assertTrue(again['reused']);self.assertEqual(result['release_id'],again['release_id']);self.assertEqual(before,self.dump())

    def test_or_text_semantics_missing_duplicate_and_wrong_version_reject(self):
        package=directory.load_bondson_directory_package();package.update(source_code='isolated-text',version_label='1',records=[],excluded=[])
        for index,(concentration,code) in enumerate([('阴性',''),('','基因型A/B'),('/','水平1/水平2'),('c.123A>G','/'),('阳性','')]):
            package['records'].append(dict(product_code=f'T-{index}',product_name='原文产品',concentration=concentration,concentration_code=code,source_rows=[index+2]))
        _,result=self.publish(package)
        self.assertEqual(len(directory.list_directory_products(result['release_id'])),5)
        changed=deepcopy(package);changed['records'][0]['concentration']='篡改同版本'
        self.assertTrue(directory.preview_product_directory(changed,self.manufacturer)['issues'])
        before=self.dump()
        with self.assertRaises(ValueError):directory.publish_product_directory(changed,self.manufacturer,expected_preview_hash='x',published_by='验收')
        self.assertEqual(before,self.dump())
        for concentration,code in [('', ''),('/', ''),('', '/')]:
            bad=deepcopy(package);bad['version_label']='bad';bad['records'][0].update(concentration=concentration,concentration_code=code)
            self.assertTrue(any(row['field']=='浓度 / 浓度编号' for row in directory.preview_product_directory(bad,self.manufacturer)['issues']))
        duplicate=deepcopy(package);duplicate['version_label']='duplicate';duplicate['records'].append(duplicate['records'][0])
        self.assertTrue(any('重复' in row['message'] for row in directory.preview_product_directory(duplicate,self.manufacturer)['issues']))
        missing=deepcopy(package);missing.pop('source_sha256')
        with self.assertRaises(ValueError):directory.preview_product_directory(missing,self.manufacturer)

    def test_failure_is_atomic_local_collision_and_test_package_excluded(self):
        package=directory.load_bondson_directory_package();package['records']=package['records'][:2];package['excluded']=[]
        with database.atomic_write() as connection:
            connection.execute("CREATE TRIGGER fail_directory_item BEFORE INSERT ON md_product_directory_items WHEN NEW.product_code='"+package['records'][-1]['product_code']+"' BEGIN SELECT RAISE(ABORT,'injected'); END")
        preview=directory.preview_product_directory(package,self.manufacturer);before=self.dump()
        with self.assertRaises(ValueError):
            directory.publish_product_directory(package,self.manufacturer,expected_preview_hash=preview['preview_hash'],published_by='验收')
        self.assertEqual(before,self.dump())
        with database.atomic_write() as connection:connection.execute('DROP TRIGGER fail_directory_item')
        master.create_qc_material(manufacturer_id=self.manufacturer,generic_name='本地同号产品',catalog_no=package['records'][0]['product_code'])
        self.assertTrue(directory.preview_product_directory(package,self.manufacturer)['issues'])
        package.update(is_test=True,source_code='engineering',version_label='test-only')
        _,result=self.publish(package)
        self.assertTrue(directory.list_directory_products(result['release_id']).product_id.isna().all())
        self.assertEqual(len(master.list_qc_materials()),1)

    def test_updates_preserve_actual_lot_and_specs_and_freeze_prior_directory(self):
        package=directory.load_bondson_directory_package();package['records']=package['records'][:2];package['excluded']=[]
        _,first=self.publish(package)
        products=directory.list_directory_products(first['release_id']);row=products.iloc[0]
        register_control_material(material_id=int(row.product_id),specification_id=int(row.specification_id),lot_no='ACTUAL-01',expiry_date='2030-01-01')
        with database.read_snapshot() as connection:
            saved={table:[tuple(r) for r in connection.execute('SELECT * FROM '+table)] for table in ('md_qc_material_lots','md_qc_levels','md_qc_material_specs')}
        newer=deepcopy(package);newer['version_label']='2';newer['records'][0]['concentration']='保留新版本文字'
        _,second=self.publish(newer)
        self.assertNotEqual(first['release_id'],second['release_id'])
        old=directory.list_directory_products(first['release_id']).set_index('product_code')
        self.assertEqual(old.loc[package['records'][0]['product_code'],'concentration'],package['records'][0]['concentration'])
        with database.read_snapshot() as connection:
            for table in ('md_qc_material_lots','md_qc_levels'):
                self.assertEqual(saved[table],[tuple(r) for r in connection.execute('SELECT * FROM '+table)])
            self.assertTrue(set(saved['md_qc_material_specs']) <= {tuple(r) for r in connection.execute('SELECT * FROM md_qc_material_specs')})

    def test_explicit_coverage_18_items_usage_methods_exchange_conflict_and_retire(self):
        package,result=self.publish()
        product=int(directory.list_directory_products(result['release_id']).iloc[0].product_id)
        items=[master.create_test_item(chinese_name=f'明确关联项目{i}',standard_code=f'ENGINEER-{i}') for i in range(18)]
        before=directory.get_product_relationships(product)
        saved=directory.save_product_coverage(product,items,expected_version=before['edit_version'],confirmed_by='实验室核对人',evidence='本地逐项目核对记录')
        self.assertEqual(len(saved['coverage']),18);self.assertTrue(all(r['source_kind']=='local' for r in saved['coverage']))
        snapshot=self.dump()
        directory.save_product_coverage(product,items,expected_version=saved['edit_version'],confirmed_by='实验室核对人',evidence='本地逐项目核对记录')
        self.assertEqual(snapshot,self.dump())
        with self.assertRaisesRegex(ValueError,'已修改'):
            directory.save_product_coverage(product,[],expected_version=before['edit_version'],confirmed_by='实验室核对人',evidence='取消')
        from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
        from services.project_config_service import create_project_template,save_template_items,list_template_items
        dependencies=_seed_v11_configuration_dependencies()
        template=create_project_template(template_name='独立方法同产品',lab_instrument_id=dependencies['lab_instrument_id'],qc_material_id=product)
        rows=[dict(test_item_id=item,qc_method=method,input_value_type='raw',unit_id=dependencies['unit_id'],method_id=dependencies['method_id'],reagent_id=dependencies['reagent_id'],level_count=count,target_n=5,cv_limit=5)
              for item,method,count in [(items[0],'lj',1),(items[1],'zscore',3)]]
        save_template_items(template,rows)
        method_before=list_template_items(template)[['test_item_id','qc_method','level_count']].to_dict('records')
        current=directory.get_product_relationships(product)
        self.assertEqual({row['qc_method'] for row in current['usage']},{'lj','zscore'})
        payload=directory.export_material_catalog_context(product)
        with database.read_snapshot() as connection:directory.validate_material_catalog_context(connection,product,payload)
        before=self.dump()
        directory.apply_material_catalog_context(product,payload,{item:item for item in items},expected_version=current['edit_version'])
        self.assertEqual(before,self.dump())
        bad=deepcopy(payload);bad['manufacturer']['categories']=['reagent']
        with database.read_snapshot() as connection:
            with self.assertRaisesRegex(ValueError,'厂家'):directory.validate_material_catalog_context(connection,product,bad)
        directory.save_product_coverage(product,items[1:],expected_version=current['edit_version'],confirmed_by='实验室核对人',evidence='停止未来推荐首项')
        self.assertTrue(next(r for r in directory.get_product_relationships(product)['coverage'] if r['test_item_id']==items[0])['is_disabled'])
        self.assertEqual(method_before,list_template_items(template)[['test_item_id','qc_method','level_count']].to_dict('records'))


if __name__=='__main__':unittest.main(verbosity=2)
