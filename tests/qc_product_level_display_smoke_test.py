"""Reviewed concentration identity remains visible across product choice paths."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from database import atomic_write

from services.master_data_service import list_qc_materials
from services.product_directory_service import ensure_builtin_bondson_directory, list_catalog_product_choices
from services.qc_product_display_service import (
    product_choice_label, product_concentration_summary, product_concentration_caption, product_display_rows,
)
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, save_qc_product_relation, validate_qc_product_choice, validate_choice_review,
)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.qc_product_choice_state_smoke_test import APP, relation
from tests.quick_project_setup_smoke_test import database_contents, metadata_values


class ProductLevelDisplayTests(unittest.TestCase):
    def fixture(self):
        values = metadata_values()
        ensure_builtin_bondson_directory()
        products = list_catalog_product_choices().set_index('product_code')
        selected = products.loc['IQC-CM-02103']
        values['qc_material_id'] = int(selected['product_id'])
        values.pop('product_choice_review', None)
        return values, dict(selected)

    def open(self, values, *, locked=False):
        source = APP.replace("prefix='choice')", "prefix='choice', locked=True)") if locked else APP
        app = AppTest.from_string(source, default_timeout=20)
        app.session_state['choice_context'] = dict(token='levels', draft=deepcopy(values))
        app.run()
        self.assertFalse(list(app.exception), list(app.exception))
        return app

    def test_catalogue_levels_distinguish_products_and_reads_preserve_originals(self):
        with IsolatedDatabase():
            values, selected = self.fixture()
            before = database_contents()
            products = {row['catalog_no']: row for row in product_display_rows(
                list_qc_materials().to_dict('records'))}
            labels = []
            for suffix, code in (('01', 'S1'), ('02', 'S2'), ('03', 'S3')):
                row = products['IQC-CM-021' + suffix]
                label = product_choice_label(row)
                self.assertIn('｜' + code + '｜', label)
                self.assertLess(label.index(code), label.index(row['generic_name']))
                labels.append(label)
            self.assertEqual(len(set(labels)), 3)
            self.assertEqual(selected['concentration'], products['IQC-CM-02103']['concentration'])
            self.assertEqual(before, database_contents())

    def test_all_and_recommended_choices_show_same_source_level_and_keep_draft(self):
        with IsolatedDatabase():
            values, selected = self.fixture()
            save_qc_product_relation(relation(values))
            before = database_contents()
            app = self.open(values)
            product_id = values['qc_material_id']
            candidate = get_qc_product_recommendations(**{key: values[key] for key in
                ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')})['candidates'][0]
            self.assertIn('｜S3｜', candidate['label'])
            self.assertIn(candidate['label'], app.selectbox(key='choice_product').options)
            self.assertIn(product_concentration_caption(selected), [item.value for item in app.caption])
            app.button(key='choice_confirm').click().run()
            self.assertTrue(app.session_state['choice_context']['choice_valid'])
            app.radio(key='choice_mode').set_value('人工选品').run()
            self.assertFalse(list(app.exception))
            self.assertIn(candidate['label'], app.selectbox(key='choice_product').options)
            self.assertEqual(product_id, app.selectbox(key='choice_product').value)
            draft = app.session_state['choice_context']['draft']
            for key in ('qc_method', 'level_count', 'level_ids', 'qc_material_id'):
                self.assertEqual(values[key], draft[key])
            self.assertEqual(before, database_contents())

    def test_locked_batch_product_keeps_s3_distinct_from_project_level_one(self):
        with IsolatedDatabase():
            values, selected = self.fixture()
            before = database_contents()
            app = self.open(values, locked=True)
            captions = '\n'.join(item.value for item in app.caption)
            self.assertIn('｜S3｜', captions)
            self.assertIn(product_concentration_caption(selected), captions)
            draft = app.session_state['choice_context']['draft']
            self.assertEqual(values['level_ids'], draft['level_ids'])
            self.assertEqual(1, draft['level_count'])
            self.assertEqual(values['qc_material_id'], draft['qc_material_id'])
            self.assertEqual(before, database_contents())

    def test_local_products_without_source_are_not_assigned_concentration_codes(self):
        with IsolatedDatabase():
            values = metadata_values()
            before = database_contents()
            row = next(row for row in product_display_rows(list_qc_materials().to_dict('records'))
                       if row['id'] == values['qc_material_id'])
            label = product_choice_label(row)
            self.assertNotIn('S1', label)
            self.assertEqual('', product_concentration_summary(row))
            self.assertEqual(before, database_contents())

    def test_caption_preserves_markdown_punctuation_without_changing_source_or_choice_label(self):
        row = dict(manufacturer_name='厂家', product_code='QC_[03]', product_name='质控品',
                   concentration=r'1.00*10^5～1.00*10^6 IU/mL _低值_ [水平](原始记录) \\ **确认** $10^5$ <S3>',
                   concentration_code='S_3[原编号]')
        original = deepcopy(row)
        source = product_concentration_summary(row)
        app = AppTest.from_string(
            'import streamlit as st\n'
            'from services.qc_product_display_service import product_concentration_caption\n'
            'st.caption(product_concentration_caption(st.session_state["row"]))')
        app.session_state['row'] = row
        app.run()
        self.assertFalse(list(app.exception))
        self.assertEqual(product_concentration_caption(row), app.caption[0].value)
        self.assertIn(r'1\.00\*10\^5～1\.00\*10\^6', app.caption[0].value)
        self.assertIn(r'\_低值\_', app.caption[0].value)
        self.assertIn(r'\[水平\]\(原始记录\)', app.caption[0].value)
        self.assertIn(r'S\_3\[原编号\]', app.caption[0].value)
        self.assertEqual('浓度：' + row['concentration'] + '｜浓度编号：' + row['concentration_code'], source)
        self.assertEqual('厂家｜QC_[03]｜S_3[原编号]｜质控品', product_choice_label(row))
        self.assertEqual(original, row)
        self.assertEqual('', product_concentration_caption({}))

    def test_all_three_caption_entry_points_escape_the_source_summary(self):
        from tests.material_catalog_smoke_test import catalogue_page

        with IsolatedDatabase():
            values, selected = self.fixture()
            before = database_contents()
            raw = '浓度：1.00*10^5～1.00*10^6 IU/mL _低值_ [水平](原始记录)｜浓度编号：S3'
            with patch('services.qc_product_display_service.product_concentration_summary', return_value=raw):
                expected = product_concentration_caption(selected)
                choice = self.open(values)
                catalogue = AppTest.from_string(
                    'import streamlit as st\nfrom ui.project_dialogs import _render_catalogue_picker\n'
                    '_render_catalogue_picker(st.session_state["ctx"])', default_timeout=20)
                catalogue.session_state['ctx'] = dict(token='caption', catalogue_open=True, draft={})
                catalogue.session_state['project_catalogue_product_caption'] = values['qc_material_id']
                catalogue.run()
                material = AppTest.from_function(catalogue_page, default_timeout=20)
                material.session_state['material_catalog_product_id'] = values['qc_material_id']
                material.run()
                for name, app in (('choice', choice), ('catalogue', catalogue), ('material', material)):
                    with self.subTest(entry=name):
                        self.assertFalse(list(app.exception), list(app.exception))
                        self.assertIn(expected, [item.value for item in app.caption])
                        self.assertNotIn(raw, [item.value for item in app.caption])
            self.assertEqual(before, database_contents())

    def test_source_identifiers_and_combination_text_are_preserved(self):
        for code in ('S10', '阴性', '水平1/水平2', '基因型1b'):
            with self.subTest(code=code):
                row = dict(manufacturer_name='厂家', product_code='KNOWN-01', product_name='质控品',
                           concentration='原始浓度文本', concentration_code=code)
                self.assertEqual('厂家｜KNOWN-01｜' + code + '｜质控品', product_choice_label(row))
                self.assertEqual('浓度：原始浓度文本｜浓度编号：' + code, product_concentration_summary(row))
        self.assertEqual('厂家｜KNOWN-01｜阴性｜质控品', product_choice_label(dict(
            manufacturer_name='厂家', product_code='KNOWN-01', product_name='质控品',
            concentration='阴性', concentration_code='/')))

    def test_disabled_source_products_keep_their_identity_when_shown(self):
        with IsolatedDatabase():
            values, selected = self.fixture()
            with atomic_write() as connection:
                connection.execute('UPDATE md_qc_materials SET is_disabled=1 WHERE id=?', (values['qc_material_id'],))
            before = database_contents()
            self.assertNotIn(values['qc_material_id'], list_catalog_product_choices().product_id.tolist())
            row = next(row for row in product_display_rows(list_qc_materials(include_disabled=True).to_dict('records'))
                       if row['id'] == values['qc_material_id'])
            self.assertIn('｜S3｜', product_choice_label(row))
            self.assertIn(selected['concentration'], product_concentration_summary(row))
            self.assertEqual(before, database_contents())

    def test_display_format_does_not_invalidate_existing_product_confirmation(self):
        with IsolatedDatabase():
            values, _ = self.fixture()
            save_qc_product_relation(relation(values))
            scope = {key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')}
            review = validate_qc_product_choice(qc_material_id=values['qc_material_id'], **scope)
            before = database_contents()

            def changed_display(rows, **kwargs):
                return [dict(row, product_name='较短的产品名称', concentration_code='S3') for row in
                        product_display_rows(rows, **kwargs)]

            with patch('services.qc_product_recommendation_service.product_display_rows', changed_display):
                current = get_qc_product_recommendations(**scope)
                self.assertEqual(review['revision'], current['revision'])
                self.assertIn('较短的产品名称', current['candidates'][0]['label'])
                self.assertEqual(review, validate_choice_review(review,
                    qc_material_id=values['qc_material_id'], **scope))
            self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
