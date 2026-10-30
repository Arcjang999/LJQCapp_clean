"""Product concentration attributes retain text and respect existing identities."""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import atomic_write, read_snapshot
from migrations.product_attributes import ensure_product_attributes_schema
from services import master_data_service as master
from services import product_directory_service as directory
from services.material_catalog_service import get_material_product, update_material_product
from services.qc_product_recommendation_service import (
    save_qc_product_relation, validate_choice_review, validate_qc_product_choice,
)
from tests import product_directory_smoke_test as directory_fixture


def legacy_choice_revision(connection, identity):
    """Frozen pre-attribute payload and SQL from f93e658, read on old columns.

    This deliberately does not call today's revision/reference helpers or strip
    keys from a current payload: the test removes the new columns first.
    """
    tables = {'reagent_id': 'md_reagents', 'qc_material_id': 'md_qc_materials',
              'lab_instrument_id': 'lab_instruments', 'test_item_id': 'md_test_items',
              'method_id': 'md_methods'}

    def reference_state(values):
        state, invalid = {}, False
        for key, table in tables.items():
            if values.get(key) is None:
                continue
            record = connection.execute('SELECT * FROM ' + table + ' WHERE id=?', (int(values[key]),)).fetchone()
            row = dict(record) if record is not None else None
            details = {'record': row}
            invalid = invalid or row is None or bool(row.get('is_disabled'))
            if row is not None:
                owner = row
                category = {'reagent_id': 'reagent', 'qc_material_id': 'qc_material',
                            'lab_instrument_id': 'instrument'}.get(key)
                if key == 'lab_instrument_id':
                    model = connection.execute('SELECT * FROM md_instrument_models WHERE id=?', (row.get('instrument_model_id'),)).fetchone()
                    owner = dict(model) if model is not None else {}
                    details['instrument_model'] = owner
                    invalid = invalid or not owner or bool(owner.get('is_disabled'))
                if category and owner.get('manufacturer_id') is not None:
                    manufacturer = connection.execute('SELECT * FROM md_manufacturers WHERE id=?', (owner['manufacturer_id'],)).fetchone()
                    maker = dict(manufacturer) if manufacturer is not None else {}
                    categories = [entry[0] for entry in connection.execute(
                        'SELECT category FROM md_manufacturer_categories WHERE manufacturer_id=? ORDER BY category',
                        (owner['manufacturer_id'],))]
                    details.update(manufacturer=maker, manufacturer_categories=categories)
                    invalid = invalid or not maker or bool(maker.get('is_disabled')) or category not in categories
            state[key] = details
        return state, invalid

    references, unavailable = reference_state(identity)
    rows = [dict(row) for row in connection.execute('''SELECT r.*,p.generic_name,p.trade_name,p.catalog_no,
        p.is_disabled AS product_disabled,m.display_name AS manufacturer_name,
        n.is_disabled AS instrument_disabled,t.is_disabled AS test_disabled,x.is_disabled AS method_disabled
        FROM qc_product_recommendations r JOIN md_qc_materials p ON p.id=r.qc_material_id
        LEFT JOIN md_manufacturers m ON m.id=p.manufacturer_id
        LEFT JOIN lab_instruments n ON n.id=r.lab_instrument_id
        LEFT JOIN md_test_items t ON t.id=r.test_item_id LEFT JOIN md_methods x ON x.id=r.method_id
        WHERE r.reagent_id=? AND (r.lab_instrument_id IS NULL OR r.lab_instrument_id=?)
        AND (r.test_item_id IS NULL OR r.test_item_id=?) AND (r.method_id IS NULL OR r.method_id=?)
        ORDER BY r.qc_material_id,r.id''', tuple(identity[key] for key in
        ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')))]
    products = {row['qc_material_id']: reference_state({'qc_material_id': row['qc_material_id']}) for row in rows}
    assert not unavailable and all(not pair[1] for pair in products.values())
    payload = {'context': identity, 'references': references, 'products': products, 'relations': rows}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class ProductAttributeStorageTests(unittest.TestCase):
    setUp = directory_fixture.DirectoryTest.setUp
    tearDown = directory_fixture.DirectoryTest.tearDown
    dump = directory_fixture.DirectoryTest.dump
    publish = directory_fixture.DirectoryTest.publish

    def product(self, **changes):
        values = dict(manufacturer_id=self.manufacturer, generic_name='手工浓度产品',
                      catalog_no='MANUAL-001', concentration_code='001',
                      concentration='1.00*10^5～1.00*10^6 IU/mL')
        values.update(changes)
        return master.create_qc_material(**values)

    def test_additive_migration_preserves_existing_cells_and_is_idempotent(self):
        with closing(sqlite3.connect(':memory:')) as c:
            c.execute('CREATE TABLE schema_migrations(migration_key TEXT PRIMARY KEY,app_version TEXT)')
            c.execute('CREATE TABLE md_qc_materials(id INTEGER PRIMARY KEY,uid TEXT,generic_name TEXT,notes TEXT)')
            c.execute("INSERT INTO md_qc_materials VALUES(7,'original-id','原产品','原备注')")
            c.execute('CREATE TABLE original_files(id INTEGER PRIMARY KEY,content BLOB)')
            c.execute('INSERT INTO original_files VALUES(1,?)', (b'original-pdf-attachment\x00\xff',))
            original = c.execute('SELECT id,uid,generic_name,notes FROM md_qc_materials').fetchall()
            original_file = c.execute('SELECT * FROM original_files').fetchall()
            ensure_product_attributes_schema(c)
            self.assertEqual(original, c.execute('SELECT id,uid,generic_name,notes FROM md_qc_materials').fetchall())
            self.assertEqual([('', '')], c.execute('SELECT concentration_code,concentration FROM md_qc_materials').fetchall())
            self.assertEqual(original_file, c.execute('SELECT * FROM original_files').fetchall())
            once = '\n'.join(c.iterdump())
            ensure_product_attributes_schema(c)
            self.assertEqual(once, '\n'.join(c.iterdump()))

    def test_create_and_read_preserve_literal_concentration_and_leading_zero_code(self):
        literal = '1.00*10^5～1.00*10^6 IU/mL  [原文]\n低值'
        product_id = self.product(concentration=literal)
        before = self.dump()
        row = get_material_product(product_id)
        self.assertEqual('001', row['concentration_code'])
        self.assertEqual(literal, row['concentration'])
        listed = master.list_qc_materials().set_index('id').loc[product_id]
        self.assertEqual('001', listed['concentration_code'])
        self.assertEqual(literal, listed['concentration'])
        self.assertEqual(before, self.dump())
        zero = self.product(generic_name='零编号产品', catalog_no='MANUAL-000', concentration_code=0)
        self.assertEqual('0', get_material_product(zero)['concentration_code'])

    def test_attribute_only_edit_checks_fresh_version_and_preserves_unsupplied_fields(self):
        product_id = self.product()
        row = get_material_product(product_id)
        update_material_product(product_id, concentration_code='S_03[原编号]',
                                concentration='阴性 / 基因型1b', expected_version=row['edit_version'])
        current = get_material_product(product_id)
        self.assertEqual(row['id'], current['id'])
        self.assertEqual(row['uid'], current['uid'])
        self.assertEqual(row['catalog_no'], current['catalog_no'])
        self.assertEqual(('S_03[原编号]', '阴性 / 基因型1b'),
                         (current['concentration_code'], current['concentration']))
        before = self.dump()
        with self.assertRaisesRegex(ValueError, '资料已修改'):
            update_material_product(product_id, concentration_code='stale', expected_version=row['edit_version'])
        self.assertEqual(before, self.dump())
        with self.assertRaisesRegex(ValueError, '200'):
            update_material_product(product_id, concentration_code='x' * 201,
                                    expected_version=current['edit_version'])
        self.assertEqual(before, self.dump())

    def test_referenced_product_keeps_both_attributes_locked(self):
        product_id = self.product()
        master.create_qc_lot(qc_material_id=product_id, lot_no='REAL-LOT', expiry_date='2029-01-01')
        row = get_material_product(product_id)
        self.assertTrue(row['identity_locked'])
        for field in ('concentration_code', 'concentration'):
            before = self.dump()
            with self.assertRaisesRegex(ValueError, '不能直接修改'):
                update_material_product(product_id, expected_version=row['edit_version'], **{field: '错误覆盖'})
            self.assertEqual(before, self.dump())
        update_material_product(product_id, notes='补充备注', expected_version=row['edit_version'])
        current = get_material_product(product_id)
        self.assertEqual(('001', row['concentration']), (current['concentration_code'], current['concentration']))
        self.assertEqual('补充备注', current['notes'])

    def test_directory_read_uses_source_and_notes_never_rewrite_local_attributes(self):
        package = directory.load_bondson_directory_package()
        package.update(records=package['records'][:1], excluded=[])
        _, release = self.publish(package)
        source = package['records'][0]
        product_id = int(directory.list_directory_products(release['release_id']).iloc[0].product_id)
        # Model an older imported product whose new local columns are empty.
        with atomic_write() as c:
            c.execute("UPDATE md_qc_materials SET concentration_code='',concentration='' WHERE id=?", (product_id,))
        row = get_material_product(product_id)
        self.assertEqual((source['concentration_code'], source['concentration']),
                         (row['concentration_code'], row['concentration']))
        self.assertTrue(row['identity_locked'])
        with read_snapshot() as c:
            original = dict(c.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
            source_rows = c.execute('SELECT * FROM md_product_directory_items').fetchall()
        update_material_product(product_id, notes='目录备注', expected_version=row['edit_version'])
        with read_snapshot() as c:
            after = dict(c.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
            self.assertEqual(source_rows, c.execute('SELECT * FROM md_product_directory_items').fetchall())
        self.assertEqual({k: v for k, v in original.items() if k not in ('notes', 'updated_at')},
                         {k: v for k, v in after.items() if k not in ('notes', 'updated_at')})
        current = get_material_product(product_id)
        before = self.dump()
        with self.assertRaisesRegex(ValueError, '不能直接修改'):
            update_material_product(product_id, concentration_code='new', expected_version=current['edit_version'])
        self.assertEqual(before, self.dump())
        next_package = deepcopy(package)
        next_package['version_label'] += '-next'
        next_package['records'][0].update(concentration_code='S_NEW', concentration='新的原始浓度')
        self.publish(next_package)
        latest = get_material_product(product_id)
        self.assertEqual(('S_NEW', '新的原始浓度'), (latest['concentration_code'], latest['concentration']))
        before = self.dump()
        with self.assertRaisesRegex(ValueError, '资料已修改'):
            update_material_product(product_id, notes='旧目录版本', expected_version=current['edit_version'])
        self.assertEqual(before, self.dump())

    def test_old_saved_choice_survives_empty_attribute_migration_but_real_changes_require_review(self):
        maker = master.create_manufacturer(display_name='旧确认仪器试剂厂家', categories=['instrument', 'reagent'])
        model = master.create_instrument_model(manufacturer_id=maker, generic_name='确认仪器', model='R1')
        instrument = master.create_lab_instrument(instrument_model_id=model, display_name='确认仪器1')
        reagent = master.create_reagent(manufacturer_id=maker, generic_name='确认试剂')
        item = master.create_test_item(chinese_name='确认检验项目')
        method = master.create_method(method_name='确认方法')
        product_id = self.product(concentration_code='', concentration='')
        scope = dict(reagent_id=reagent, lab_instrument_id=instrument, test_item_id=item, method_id=method)
        save_qc_product_relation(dict(scope, qc_material_id=product_id, scope_note='旧确认范围',
                                     evidence='原说明书核对', confirmed_by='原核对员'))
        with atomic_write() as c:
            c.execute('ALTER TABLE md_qc_materials DROP COLUMN concentration_code')
            c.execute('ALTER TABLE md_qc_materials DROP COLUMN concentration')
            old_product = dict(c.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
            self.assertNotIn('concentration_code', old_product)
            self.assertNotIn('concentration', old_product)
            old_revision = legacy_choice_revision(c, scope)
            relation_id = c.execute('SELECT id FROM qc_product_recommendations WHERE qc_material_id=?',
                                    (product_id,)).fetchone()[0]
            # Exact persisted recommendation-review shape from the old service.
            old_review = dict(version=1, context=scope, qc_material_id=product_id, revision=old_revision,
                              basis='recommendation', relation_ids=[relation_id],
                              confirmed_by='原核对员', evidence='原说明书核对')
            ensure_product_attributes_schema(c)
        before = self.dump()
        self.assertEqual(old_review, validate_choice_review(old_review, qc_material_id=product_id, **scope))
        self.assertEqual(before, self.dump())
        review = old_review
        for changes in ({'concentration_code': '001'}, {'concentration': '1.00*10^5 IU/mL'},
                        {'concentration_code': 'S_03[原编号]'}):
            product = get_material_product(product_id)
            update_material_product(product_id, expected_version=product['edit_version'], **changes)
            with atomic_write() as c:
                # Hold the old timestamp fixed so only the actual attribute
                # value, rather than a new save time, can invalidate this review.
                c.execute('UPDATE md_qc_materials SET updated_at=? WHERE id=?',
                          (old_product['updated_at'], product_id))
            before = self.dump()
            with self.assertRaisesRegex(ValueError, '资料已修改'):
                validate_choice_review(review, qc_material_id=product_id, **scope)
            self.assertEqual(before, self.dump())
            review = validate_qc_product_choice(qc_material_id=product_id, **scope)
            self.assertNotEqual(old_revision, review['revision'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
