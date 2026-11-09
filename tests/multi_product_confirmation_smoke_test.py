"""Each selected product needs scoped evidence; lot numbers alone do not invalidate it."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from database import get_connection
from services.batch_edit_service import get_batch_item_context
from services.master_data_service import create_manufacturer, create_qc_material, create_qc_lot, create_qc_level
from services.qc_product_recommendation_service import (
    choice_review_map, pack_choice_reviews, validate_choice_review, validate_choice_reviews,
    validate_qc_product_choice, require_current_draft_choice, save_draft_product_choice,
)
from tests.batch_dialogs_smoke_test import seed_draft, assert_clean
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext


def fixture():
    data = seed_draft()
    item_id = data['items']['zscore']
    context = get_batch_item_context(item_id)
    scope = dict(reagent_id=context['item']['reagent_id'],
        lab_instrument_id=context['config']['lab_instrument_id'],
        test_item_id=context['item']['test_item_id'], method_id=context['item']['method_id'])
    original = context['config']['qc_material_id']
    maker = create_manufacturer(display_name='第二厂家', categories=['qc_material'])
    product = create_qc_material(manufacturer_id=maker, generic_name='第二水平产品',
        catalog_no='P002', concentration_code='L2', concentration='200 IU/mL')
    lot = create_qc_lot(qc_material_id=product, lot_no='FACTORY-2026', expiry_date='2029-12-31')
    level = create_qc_level(qc_material_lot_id=lot, level_name='200 IU/mL', level_order=1, level_code='L2')
    # Assemble a service fixture directly to isolate the confirmation gate from the selector.
    with get_connection() as connection:
        connection.execute('UPDATE qc_lot_config_item_levels SET qc_level_id=? WHERE lot_config_item_id=? AND level_order=2',
                           (level, item_id))
    def review(pid):
        return validate_qc_product_choice(qc_material_id=pid, **scope,
            manual_review=dict(confirmed_by='核对人', evidence='厂家说明书与本检测系统使用记录'))
    return dict(data=data, item_id=item_id, scope=scope, original=original, product=product,
                lot=lot, level=level, reviews={str(pid): review(pid) for pid in (original, product)})


def page(item_id):
    import streamlit as st
    from ui.batch_dialogs import open_batch_item_dialog, render_batch_item_dialog
    if st.button('核对质控品', key='open_product'):
        open_batch_item_dialog(item_id, kind='product')
    render_batch_item_dialog()


class MultiProductConfirmationTests(unittest.TestCase):
    def test_collection_requires_every_actual_product_and_rejects_wrong_scope(self):
        with TemporaryDatabaseContext():
            f = fixture()
            with self.assertRaisesRegex(ValueError, '尚未完成'):
                validate_choice_reviews({str(f['original']): f['reviews'][str(f['original'])]},
                    qc_material_ids=[f['original'], f['product']], **f['scope'])
            confirmed = validate_choice_reviews(f['reviews'],
                qc_material_ids=[f['original'], f['product']], **f['scope'])
            self.assertEqual(2, confirmed['version'])
            self.assertEqual(set(map(str, (f['original'], f['product']))), set(choice_review_map(confirmed)))
            wrong = dict(f['scope'], test_item_id=None)
            with self.assertRaisesRegex(ValueError, '尚未完成'):
                validate_choice_review(confirmed, qc_material_id=f['product'], **wrong)

    def test_lot_gate_uses_actual_levels_and_saves_collection_in_one_revision(self):
        with TemporaryDatabaseContext():
            f = fixture()
            context = get_batch_item_context(f['item_id'])
            with get_connection() as connection:
                with self.assertRaisesRegex(ValueError, '尚未完成'):
                    require_current_draft_choice('lot', f['item_id'], connection)
            revision = save_draft_product_choice('lot', f['item_id'], f['reviews'],
                expected_revision=context['revision'])
            self.assertEqual(context['revision'] + 1, revision)
            with get_connection() as connection:
                require_current_draft_choice('lot', f['item_id'], connection)
            saved = json.loads(get_batch_item_context(f['item_id'])['item']['product_choice_review_json'])
            self.assertEqual(2, saved['version'])

    def test_manually_selected_product_changes_invalidate_but_new_lot_does_not(self):
        with TemporaryDatabaseContext():
            f = fixture()
            before = f['reviews'][str(f['product'])]
            create_qc_lot(qc_material_id=f['product'], lot_no='NEXT-LOT', expiry_date='2029-12-31')
            validate_choice_review(before, qc_material_id=f['product'], **f['scope'])
            with get_connection() as connection:
                connection.execute("UPDATE md_qc_materials SET concentration='250 IU/mL' WHERE id=?", (f['product'],))
            with self.assertRaisesRegex(ValueError, '产品资料|质控品资料已修改'):
                validate_choice_review(before, qc_material_id=f['product'], **f['scope'])

    def test_frozen_batch_rejects_collection_update(self):
        with TemporaryDatabaseContext():
            f = fixture()
            context = get_batch_item_context(f['item_id'])
            with get_connection() as connection:
                connection.execute("UPDATE qc_lot_configs SET activated_at='2026-09-01 00:00:00' WHERE id=?",
                                   (context['config']['id'],))
            with self.assertRaisesRegex(ValueError, '已确认'):
                save_draft_product_choice('lot', f['item_id'], f['reviews'], expected_revision=context['revision'])

    def test_manual_confirmation_without_selected_product_fingerprint_is_not_reused(self):
        with TemporaryDatabaseContext():
            f = fixture()
            incomplete = dict(f['reviews'][str(f['product'])])
            incomplete.pop('product_revision')
            with self.assertRaisesRegex(ValueError, '尚未核对'):
                validate_choice_review(incomplete, qc_material_id=f['product'], **f['scope'])

    def test_independent_dialog_confirms_all_products_with_one_save(self):
        with TemporaryDatabaseContext():
            f = fixture()
            app = AppTest.from_function(page, args=(f['item_id'],), default_timeout=30).run()
            app.button(key='open_product').click().run()
            assert_clean(app)
            state = app.session_state['batch_item_dialog']
            prefix = 'batch_product_' + state['token']
            self.assertFalse(any(row.label == '质量要求确认人' for row in app.text_input))
            self.assertTrue(any(row.label == '保存质控品适用依据' for row in app.button))
            # The first product's existing confirmation is reused; only the new product asks for evidence.
            product_prefix = prefix + '_' + str(f['product'])
            app.text_input(key=product_prefix + '_person').set_value('第二产品核对人')
            app.text_area(key=product_prefix + '_evidence').set_value('第二厂家P002说明书与本仪器试剂适用记录')
            app.run()
            before = get_batch_item_context(f['item_id'])['revision']
            app.button(key=prefix + '_confirm').click().run()
            assert_clean(app)
            self.assertNotIn('batch_item_dialog', app.session_state)
            after = get_batch_item_context(f['item_id'])
            self.assertEqual(before + 1, after['revision'])
            self.assertEqual('第二产品核对人',
                choice_review_map(after['item']['product_choice_review_json'])[str(f['product'])]['confirmed_by'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
