"""The management catalogue unifies products without exposing directory audits."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import master_data_service as master
from services import product_directory_service as directory
from services.material_catalog_service import (
    get_material_product, set_material_product_disabled, update_material_product,
)
from tests import product_directory_smoke_test as directory_fixture


class ManagedMaterialProductsTest(unittest.TestCase):
    setUp = directory_fixture.DirectoryTest.setUp
    tearDown = directory_fixture.DirectoryTest.tearDown
    dump = directory_fixture.DirectoryTest.dump
    publish = directory_fixture.DirectoryTest.publish

    def compact_package(self):
        package = deepcopy(directory.load_bondson_directory_package())
        package.update(records=package['records'][:3], excluded=package['excluded'][:1])
        return package

    def status(self, product_id, disabled):
        product = get_material_product(product_id)
        set_material_product_disabled(product_id, is_disabled=disabled,
            reason='隔离测试停用' if disabled else '', expected_version=product['edit_version'])

    def test_full_directory_and_local_product_share_stable_ids_without_writes(self):
        package, release = self.publish()
        local = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='实验室补充质控品', catalog_no='LOCAL-9001')
        before = self.dump()
        managed = directory.list_managed_material_products()
        self.assertEqual(list(managed.columns),
            [*directory.PRODUCT_FIELDS, 'product_id', 'manufacturer_name', 'is_disabled'])
        self.assertEqual(len(managed), 1055)
        self.assertEqual(managed.product_id.nunique(), 1055)
        current = directory.list_directory_products(release['release_id'])
        imported = managed.loc[managed.product_id != local].sort_values('product_code')
        expected = current.sort_values('product_code')
        self.assertEqual(imported[[*directory.PRODUCT_FIELDS, 'product_id']].to_dict('records'),
            expected[[*directory.PRODUCT_FIELDS, 'product_id']].to_dict('records'))
        self.assertEqual(set(managed.manufacturer_name), {'邦德盛'})
        excluded = {row['product_code'] for row in package['excluded']}
        self.assertTrue(excluded.isdisjoint(managed.product_code))
        choices = directory.list_catalog_product_choices()
        self.assertEqual(len(choices), 1054)
        self.assertNotIn(local, choices.product_id.tolist())
        self.assertEqual(before, self.dump())

    def test_manual_edits_are_visible_and_searchable_without_changing_identity(self):
        product_id = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='初始名称甲', catalog_no='OLD-9100')
        row = get_material_product(product_id)
        update_material_product(product_id, generic_name='肝炎质控品', catalog_no='NEW-7300',
            expected_version=row['edit_version'])
        before = self.dump()
        managed = directory.list_managed_material_products('邦德盛 肝炎 NEW7300')
        self.assertEqual(managed.product_id.tolist(), [product_id])
        self.assertEqual(managed.product_name.tolist(), ['肝炎质控品'])
        self.assertEqual(managed.product_code.tolist(), ['NEW-7300'])
        self.assertEqual(managed.concentration.tolist(), [''])
        self.assertEqual(managed.concentration_code.tolist(), [''])
        self.assertTrue(directory.list_managed_material_products('OLD9100').empty)
        self.assertEqual(before, self.dump())

    def test_current_products_can_be_shown_when_disabled_and_restored(self):
        self.publish(self.compact_package())
        catalog_id = int(directory.list_managed_material_products().iloc[0].product_id)
        local_id = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='可恢复产品', catalog_no='LOCAL-9300')
        for product_id in (catalog_id, local_id):
            self.status(product_id, True)
        before = self.dump()
        enabled = directory.list_managed_material_products()
        self.assertTrue({catalog_id, local_id}.isdisjoint(enabled.product_id))
        all_products = directory.list_managed_material_products(include_disabled=True).set_index('product_id')
        self.assertEqual(all_products.loc[[catalog_id, local_id], 'is_disabled'].tolist(), [1, 1])
        self.assertEqual(before, self.dump())
        for product_id in (catalog_id, local_id):
            self.status(product_id, False)
        self.assertTrue({catalog_id, local_id}.issubset(directory.list_managed_material_products().product_id))

    def test_retired_excluded_and_test_directory_rows_stay_out_of_management(self):
        package, _ = self.publish(self.compact_package())
        original = directory.list_managed_material_products().set_index('product_code')
        kept, excluded, retired = package['records']
        next_package = deepcopy(package)
        next_package.update(version_label='management-formal-2', records=[kept],
            excluded=[dict(excluded, excluded_reason='不纳入日常名录')])
        self.publish(next_package)
        # Even restoring an old underlying record must not expose excluded or
        # retired source rows through the management list's local-product branch.
        for record in (excluded, retired):
            self.status(int(original.loc[record['product_code'], 'product_id']), False)
        test_package = deepcopy(next_package)
        test_package.update(version_label='management-test-3', is_test=True,
            records=[dict(kept, product_code='TEST-9900')], excluded=[])
        self.publish(test_package)
        local_id = master.create_qc_material(manufacturer_id=self.manufacturer,
            generic_name='有效本地产品', catalog_no='LOCAL-9400')
        before = self.dump()
        for include_disabled in (False, True):
            managed = directory.list_managed_material_products(include_disabled=include_disabled)
            self.assertEqual(set(managed.product_code), {kept['product_code'], 'LOCAL-9400'})
            self.assertEqual(set(managed.product_id),
                {int(original.loc[kept['product_code'], 'product_id']), local_id})
        self.assertEqual(directory.list_catalog_product_choices().product_code.tolist(), [kept['product_code']])
        self.assertEqual(before, self.dump())

    def test_empty_management_list_keeps_expected_columns(self):
        before = self.dump()
        frame = directory.list_managed_material_products('没有任何产品', include_disabled=True)
        self.assertTrue(frame.empty)
        self.assertEqual(list(frame.columns),
            [*directory.PRODUCT_FIELDS, 'product_id', 'manufacturer_name', 'is_disabled'])
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
