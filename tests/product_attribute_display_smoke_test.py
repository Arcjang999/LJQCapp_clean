"""Saved product attributes reach selection, management and source exports."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services import master_data_service as master
from services import product_directory_service as directory
from services.material_catalog_service import get_material_product
from services.qc_product_display_service import product_choice_label, product_display_rows
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, list_qc_product_relations, save_qc_product_relation,
)
from tests import product_directory_smoke_test as directory_fixture
from tests.qc_product_choice_state_smoke_test import APP, relation
from tests.quick_project_setup_smoke_test import metadata_values
from ui.product_directory import _product_table


class ProductAttributeDisplayTests(unittest.TestCase):
    setUp = directory_fixture.DirectoryTest.setUp
    tearDown = directory_fixture.DirectoryTest.tearDown
    dump = directory_fixture.DirectoryTest.dump
    publish = directory_fixture.DirectoryTest.publish

    def test_manual_attributes_reach_both_choice_modes_and_relation_list(self):
        values = metadata_values()
        product_id = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='新增产品', catalog_no='LOCAL-001',
            concentration_code='001', concentration='1.00*10^5～1.00*10^6IU/mL')
        values['qc_material_id'] = product_id
        values.pop('product_choice_review', None)
        save_qc_product_relation(relation(values))
        expected = 'LOCAL-001｜新增产品｜001｜1.00*10^5～1.00*10^6IU/mL｜邦德盛'
        before = self.dump()
        selected = next(row for row in product_display_rows(master.list_qc_materials().to_dict('records'))
                        if row['id'] == product_id)
        self.assertEqual(product_choice_label(selected), expected)
        scope = {key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')}
        candidates = get_qc_product_recommendations(**scope)['candidates']
        self.assertEqual(next(row for row in candidates if row['qc_material_id'] == product_id)['label'], expected)
        linked = next(row for row in list_qc_product_relations() if row['qc_material_id'] == product_id)
        self.assertEqual(product_choice_label(linked), expected)
        app = AppTest.from_string(APP, default_timeout=30)
        app.session_state['choice_context'] = dict(token='saved-attributes', draft=values)
        app.run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertIn(expected, app.selectbox(key='choice_product').options)
        app.radio(key='choice_mode').set_value('人工选品').run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertIn(expected, app.selectbox(key='choice_product').options)
        self.assertEqual(app.selectbox(key='choice_product').value, product_id)
        self.assertEqual(before, self.dump())

    def test_original_directory_text_keeps_both_attributes_and_display_order(self):
        package, _ = self.publish()
        before = self.dump()
        managed = directory.list_managed_material_products()
        expected = {row['product_code']: [row[k] for k in directory.PRODUCT_FIELDS] for row in package['records']}
        actual = {row['product_code']: [row[k] for k in directory.PRODUCT_FIELDS] for row in managed.to_dict('records')}
        self.assertEqual(actual, expected)
        product = next(row for row in managed.to_dict('records') if row['product_code'] == 'IQC-CM-02103')
        self.assertEqual(product_choice_label(product),
            'IQC-CM-02103｜EB病毒脱氧核糖核酸(EBV DNA)液体室内质控品｜S3｜1.00*10^5～1.00*10^6IU/mL｜邦德盛')
        self.assertEqual(get_material_product(product['product_id'])['concentration_code'], 'S3')
        self.assertEqual(_product_table(managed).columns.tolist(),
            ['产品编号', '产品名称', '浓度编号', '实际浓度', '厂家'])
        context = directory.export_material_catalog_context(product['product_id'])['product']
        self.assertEqual([context[k] for k in directory.PRODUCT_FIELDS], expected[product['product_code']])
        self.assertEqual(before, self.dump())

    def test_manual_management_search_and_export_keep_leading_zero_and_source_text(self):
        product_id = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='手工定性质控品', catalog_no='MANUAL-002',
            concentration_code='0002', concentration='阴性 / 未检出')
        before = self.dump()
        managed = directory.list_managed_material_products(query='0002 阴性')
        self.assertEqual(managed.product_id.tolist(), [product_id])
        self.assertEqual(managed.iloc[0]['concentration_code'], '0002')
        self.assertEqual(managed.iloc[0]['concentration'], '阴性 / 未检出')
        exported = _product_table(managed).to_csv(index=False)
        self.assertIn('0002', exported)
        self.assertIn('阴性 / 未检出', exported)
        product = directory.export_material_catalog_context(product_id)['product']
        self.assertEqual(product['concentration_code'], '0002')
        self.assertEqual(product['concentration'], '阴性 / 未检出')
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
