"""Reviewed built-in products appear once without changing laboratory records."""
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from services import master_data_service as master
from services import product_directory_service as directory


class BuiltinDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        names = ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')
        self.original = {name: getattr(database, name) for name in names}
        database.DB_PATH = database.DEFAULT_DB_PATH = self.root / 'isolated.db'
        database.STORAGE_CONFIG_PATH = self.root / 'storage.json'
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()

    def tearDown(self):
        for name, value in self.original.items():
            setattr(database, name, value)
        self.temporary.cleanup()

    def dump(self):
        with database.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def rows(self, statement, parameters=()):
        with database.read_snapshot() as connection:
            return [dict(row) for row in connection.execute(statement, parameters)]

    def publish(self, package, manufacturer_id):
        preview = directory.preview_product_directory(package, manufacturer_id)
        self.assertEqual(preview['issues'], [])
        return directory.publish_product_directory(package, manufacturer_id,
            expected_preview_hash=preview['preview_hash'], published_by='隔离测试核对员')

    def operational_rows(self):
        tables = ('md_methods', 'md_qc_material_lots', 'md_qc_levels', 'qc_project_templates',
                  'qc_project_template_items', 'qc_lot_configs', 'qc_lot_config_items',
                  'qc_lot_config_item_levels', 'qc_config_snapshots', 'projects', 'batches',
                  'results', 'zscore_batch_config', 'zscore_runs', 'instant_batches', 'instant_results')
        return {table: self.rows('SELECT * FROM ' + table) for table in tables}

    def test_first_install_keeps_reviewed_text_and_creates_no_methods_lots_or_results(self):
        before = self.operational_rows()
        result = directory.ensure_builtin_bondson_directory()
        self.assertFalse(result['reused'])
        self.assertEqual((result['product_count'], result['excluded_count']), (1054, 252))
        self.assertEqual(master.get_manufacturer_categories(result['manufacturer_id']), ['qc_material'])
        release = self.rows('SELECT * FROM md_product_directory_releases')[0]
        self.assertEqual(release['published_by'], '系统预置（按已确认目录）')
        self.assertEqual(release['is_test'], 0)
        choices = directory.list_catalog_product_choices()
        self.assertEqual(list(choices.columns), [*directory.PRODUCT_FIELDS, 'product_id', 'manufacturer_name'])
        self.assertEqual(len(choices), 1054)
        actual = choices[list(directory.PRODUCT_FIELDS)].sort_values('product_code').to_dict('records')
        expected = [{key: row[key] for key in directory.PRODUCT_FIELDS}
                    for row in directory.load_bondson_directory_package()['records']]
        self.assertEqual(actual, sorted(expected, key=lambda row: row['product_code']))
        excluded = self.rows("SELECT * FROM md_product_directory_items WHERE excluded_reason<>''")
        self.assertEqual(len(excluded), 252)
        self.assertTrue(all(row['product_id'] is None and row['specification_id'] is None for row in excluded))
        self.assertEqual(before, self.operational_rows())

    def test_existing_release_returns_without_loading_resource_or_writing(self):
        first = directory.ensure_builtin_bondson_directory()
        before = self.dump()
        with patch.object(directory, 'atomic_write', side_effect=AssertionError('must remain read-only')), \
             patch.object(directory, 'load_bondson_directory_package', side_effect=AssertionError('must not reload')):
            repeated = directory.ensure_builtin_bondson_directory()
        self.assertTrue(repeated['reused'])
        self.assertEqual(repeated['release_id'], first['release_id'])
        self.assertEqual(before, self.dump())

    def test_later_formal_release_is_never_replaced_by_bundled_version(self):
        manufacturer = master.create_manufacturer(display_name='邦德盛', categories=['qc_material'])
        package = deepcopy(directory.load_bondson_directory_package())
        package.update(version_label='isolated-later-formal-release', records=package['records'][:2], excluded=[])
        published = self.publish(package, manufacturer)
        before = self.dump()
        result = directory.ensure_builtin_bondson_directory()
        self.assertTrue(result['reused'])
        self.assertEqual(result['release_id'], published['release_id'])
        self.assertEqual(result['version_label'], package['version_label'])
        self.assertEqual(len(directory.list_catalog_product_choices()), 2)
        self.assertEqual(before, self.dump())

    def test_catalog_choices_filter_current_enabled_products_and_keep_local_additions(self):
        result = directory.ensure_builtin_bondson_directory()
        local_id = master.create_qc_material(manufacturer_id=result['manufacturer_id'],
            generic_name='本地补充质控品', catalog_no='LOCAL-ONLY')
        local = self.rows('SELECT * FROM md_qc_materials WHERE id=?', (local_id,))
        package = deepcopy(directory.load_bondson_directory_package())
        old_choices = directory.list_catalog_product_choices()
        retired_id = int(old_choices.iloc[-1]['product_id'])
        package.update(version_label='isolated-revised-release', records=package['records'][:2], excluded=[])
        self.publish(package, result['manufacturer_id'])
        with database.atomic_write() as connection:
            connection.execute('UPDATE md_qc_materials SET is_disabled=0 WHERE id=?', (retired_id,))
        choices = directory.list_catalog_product_choices()
        self.assertEqual(len(choices), 2)
        self.assertNotIn(local_id, choices.product_id.tolist())
        self.assertNotIn(retired_id, choices.product_id.tolist())
        self.assertEqual(self.rows('SELECT * FROM md_qc_materials WHERE id=?', (local_id,)), local)
        code = choices.iloc[0]['product_code']
        self.assertIn(code, directory.list_catalog_product_choices(code).product_code.tolist())
        self.assertEqual(len(directory.list_catalog_product_choices('邦德盛')), 2)
        with database.atomic_write() as connection:
            connection.execute('UPDATE md_qc_materials SET is_disabled=1 WHERE id=?', (int(choices.iloc[0]['product_id']),))
        self.assertEqual(len(directory.list_catalog_product_choices()), 1)
        with database.atomic_write() as connection:
            connection.execute('UPDATE md_manufacturers SET is_disabled=1 WHERE id=?', (result['manufacturer_id'],))
        self.assertTrue(directory.list_catalog_product_choices().empty)

    def test_active_existing_manufacturer_keeps_other_roles(self):
        manufacturer = master.create_manufacturer(display_name='邦德盛', categories=['instrument'])
        result = directory.ensure_builtin_bondson_directory()
        self.assertEqual(result['manufacturer_id'], manufacturer)
        self.assertEqual(master.get_manufacturer_categories(manufacturer), ['instrument', 'qc_material'])
        self.assertEqual(len(self.rows('SELECT * FROM md_manufacturers')), 1)

    def test_disabled_manufacturer_is_not_restored_silently(self):
        manufacturer = master.create_manufacturer(display_name='邦德盛', categories=['qc_material'])
        with database.atomic_write() as connection:
            connection.execute('UPDATE md_manufacturers SET is_disabled=1 WHERE id=?', (manufacturer,))
        before = self.dump()
        with self.assertRaisesRegex(ValueError, '厂家已停用'):
            directory.ensure_builtin_bondson_directory()
        self.assertEqual(before, self.dump())

    def test_local_product_number_collision_preserves_all_existing_data(self):
        manufacturer = master.create_manufacturer(display_name='邦德盛', categories=['qc_material'])
        code = directory.load_bondson_directory_package()['records'][0]['product_code']
        master.create_qc_material(manufacturer_id=manufacturer, generic_name='已登记本地产品', catalog_no=code)
        before = self.dump()
        with self.assertRaisesRegex(ValueError, '同厂家同编号'):
            directory.ensure_builtin_bondson_directory()
        self.assertEqual(before, self.dump())

    def test_late_import_failure_rolls_back_manufacturer_products_and_release(self):
        code = directory.load_bondson_directory_package()['excluded'][-1]['product_code']
        with database.atomic_write() as connection:
            connection.execute("CREATE TRIGGER fail_last_directory_item BEFORE INSERT ON md_product_directory_items "
                "WHEN NEW.product_code='" + code.replace("'", "''") + "' BEGIN SELECT RAISE(ABORT,'injected'); END")
        before = self.dump()
        with self.assertRaises(ValueError):
            directory.ensure_builtin_bondson_directory()
        self.assertEqual(before, self.dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
