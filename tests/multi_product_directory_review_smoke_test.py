"""Directory business text, rather than publication time, versions product reviews."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services import product_directory_service as directory
from services.master_data_service import create_manufacturer
from services.material_catalog_service import get_material_product
from services.material_workflow_service import register_control_material
from services.qc_product_recommendation_service import (pack_choice_reviews, save_qc_product_relation,
    validate_choice_review, validate_qc_product_choice)
from services.quick_project_setup_service import prepare_quick_project_setup, save_quick_project_setup
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.multi_product_control_workflow_smoke_test import multi_values
from tests.quick_project_setup_smoke_test import database_contents
from zscore_logic import create_zscore_run, get_template_id_for_level_count


def publish(package, maker):
    preview = directory.preview_product_directory(package, maker)
    if preview['issues']:
        raise AssertionError(preview['issues'])
    return directory.publish_product_directory(package, maker,
        expected_preview_hash=preview['preview_hash'], published_by='目录隔离核对人')


def seed(*, recommended=False):
    values = multi_values(2)
    maker = create_manufacturer(display_name='非首产品目录厂家', categories=['qc_material'])
    package = directory.load_bondson_directory_package()
    package.update(source_code='isolated-review-directory', version_label='1',
                   records=package['records'][:1], excluded=[])
    release = publish(package, maker)
    product = int(directory.list_directory_products(release['release_id']).iloc[0].product_id)
    level = register_control_material(material_id=product, lot_no='DIRECTORY-LOT',
        expiry_date='2028-12-31', level_name='目录质控水平', level_code='CAT-02')
    values['level_ids'][1] = level
    values['level_product_ids'][1] = product
    scope = {key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')}
    if recommended:
        save_qc_product_relation(dict(scope, qc_material_id=product, scope_note='目录适用范围',
            confirmed_by='配套核对人', evidence='原厂适用资料已核对'))
    reviews = {str(selected): validate_qc_product_choice(qc_material_id=selected, **scope,
        manual_review=None if recommended and selected == product else
            {'confirmed_by': '逐产品核对人', 'evidence': '已核对目录当前浓度及本检测条件'})
        for selected in values['level_product_ids']}
    values.update(product_choice_reviews=reviews, product_choice_review=pack_choice_reviews(reviews))
    return dict(values=values, maker=maker, package=package, product=product, scope=scope)


def next_release(fixture, **changes):
    package = deepcopy(fixture['package'])
    package['version_label'] = '2'
    package['records'][0].update(changes)
    return publish(package, fixture['maker'])


class MultiProductDirectoryReviewTests(unittest.TestCase):
    def test_nonfirst_source_change_rejects_old_review_and_preview_then_fresh_review_saves(self):
        with IsolatedDatabase():
            fixture = seed()
            values, product, scope = (fixture[key] for key in ('values', 'product', 'scope'))
            preview = prepare_quick_project_setup(values)
            with db.read_snapshot() as c:
                old_stamp = c.execute('SELECT updated_at FROM md_qc_materials WHERE id=?', (product,)).fetchone()[0]
            next_release(fixture, concentration_code='NEW-CODE', concentration='新的原厂浓度')
            # Hold the coarse save time constant: source business text alone must invalidate.
            with db.atomic_write() as c:
                c.execute('UPDATE md_qc_materials SET updated_at=? WHERE id=?', (old_stamp, product))
            before = database_contents()
            with self.assertRaisesRegex(ValueError, '资料已修改'):
                validate_choice_review(values['product_choice_reviews'][str(product)], qc_material_id=product, **scope)
            with self.assertRaises(ValueError):
                save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='stale-source')
            self.assertEqual(before, database_contents())
            values['product_choice_reviews'][str(product)] = validate_qc_product_choice(qc_material_id=product,
                **scope, manual_review={'confirmed_by': '重新核对人', 'evidence': '已核对新的原厂浓度和编号'})
            values['product_choice_review'] = pack_choice_reviews(values['product_choice_reviews'])
            renewed = prepare_quick_project_setup(values)
            self.assertNotEqual(preview['preview_hash'], renewed['preview_hash'])
            saved = save_quick_project_setup(values, expected_preview_hash=renewed['preview_hash'], request_key='fresh-source')
            self.assertGreater(saved['lot_config_item_id'], 0)

    def test_identical_source_republication_keeps_manual_and_recommended_reviews_and_preview(self):
        with IsolatedDatabase():
            fixture = seed(recommended=True)
            values = fixture['values']
            preview = prepare_quick_project_setup(values)
            next_release(fixture)
            with db.atomic_write() as c:
                c.execute("UPDATE md_qc_materials SET updated_at='2099-12-31 23:59:59' WHERE id=?", (fixture['product'],))
            for product in values['level_product_ids']:
                review = values['product_choice_reviews'][str(product)]
                self.assertEqual(review, validate_choice_review(review, qc_material_id=product, **fixture['scope']))
            self.assertEqual(preview['preview_hash'], prepare_quick_project_setup(values)['preview_hash'])
            saved = save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='same-business-source')
            self.assertGreater(saved['lot_config_item_id'], 0)

    def test_later_source_change_preserves_frozen_binding_result_and_product_evidence(self):
        with IsolatedDatabase():
            fixture = seed()
            values = fixture['values']
            preview = prepare_quick_project_setup(values)
            saved = save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='frozen-source')
            create_zscore_run(saved['binding']['runtime_batch_id'], '2026-10-20 08:00:00', '目录历史核对',
                [dict(level_id='Level 1', raw_value=100), dict(level_id='Level 2', raw_value=200)],
                template_id=get_template_id_for_level_count(2), required_n=20)
            def frozen():
                with db.read_snapshot() as c:
                    return dict(binding=c.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                        (saved['lot_config_item_id'],)).fetchone()[0],
                        result=c.execute('SELECT config_snapshot_json FROM qc_result_contexts').fetchone()[0],
                        evidence=c.execute('SELECT product_choice_review_json FROM qc_lot_config_items WHERE id=?',
                            (saved['lot_config_item_id'],)).fetchone()[0])
            original = frozen()
            next_release(fixture, product_name='后续目录名称', concentration_code='AFTER', concentration='后续目录浓度')
            self.assertEqual('后续目录浓度', get_material_product(fixture['product'])['concentration'])
            sync_zscore_workbench_bindings()
            self.assertEqual(original, frozen())
            self.assertNotIn('后续目录浓度', json.dumps(frozen(), ensure_ascii=False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
