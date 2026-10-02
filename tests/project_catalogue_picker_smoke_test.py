"""Real catalogue IDs in project drafts and the full application entry route.

AppTest drives the actual controls against new isolated databases. It does not
replace application routes, directory services, or QC methods with mocks.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

import database
from services.product_directory_service import (
    ensure_builtin_bondson_directory,
    list_catalog_product_choices,
    list_directory_products,
    list_product_directory_releases,
)
from services.project_config_service import get_project_template, list_project_templates
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext,
    _seed_v11_configuration_dependencies,
)
from tests.project_workspace_smoke_test import draft_key, select_table_row, workspace_page


class ProjectCataloguePickerTest(unittest.TestCase):
    def healthy(self, app):
        self.assertFalse(list(app.exception), [item.message for item in app.exception])

    def dump(self):
        with database.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def catalogue_and_project_state(self):
        tables = ('md_product_directory_releases', 'md_product_directory_items',
            'md_product_directory_keys', 'md_qc_materials', 'md_qc_material_specs',
            'md_qc_material_lots', 'md_qc_levels', 'md_product_coverage',
            'qc_project_templates', 'qc_project_template_items', 'qc_lot_configs')
        with database.read_snapshot() as connection:
            return {table: [tuple(row) for row in connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}

    def new_project(self, data, *, method='lj', level_count=1):
        app = AppTest.from_function(workspace_page, default_timeout=30).run()
        self.healthy(app)
        app.button(key='home_create_project').click().run()
        self.healthy(app)
        app.text_input(key=draft_key(app, 'template_name')).set_value('从名录创建的项目')
        for field, source in (('lab_instrument_id', 'lab_instrument_id'),
                              ('default_reagent_id', 'reagent_id'),
                              ('default_method_id', 'method_id')):
            app.selectbox(key=draft_key(app, field)).set_value(data[source])
        token = app.session_state['project_workspace_dialog']['token']
        app.radio(key='project_kind_' + token).set_value(
            '多水平' if method == 'zscore' else '单水平').run()
        self.healthy(app)
        if method == 'zscore':
            app.selectbox(key=draft_key(app, 'default_level_count')).set_value(level_count).run()
        else:
            app.checkbox(key='project_instant_' + token).set_value(False).run()
        self.healthy(app)
        self.assertEqual(app.session_state['project_workspace_dialog']['draft']['default_qc_method'], method)
        self.assertEqual(app.session_state['project_workspace_dialog']['draft']['default_level_count'], level_count)
        return app

    def choose_catalogue_product(self, app, product_code):
        matches = list_catalog_product_choices()
        product = matches.loc[matches.product_code == product_code].iloc[0]
        product_id = int(product['product_id'])
        app.button(key='project_catalogue_open').click().run()
        self.healthy(app)
        token = app.session_state['project_workspace_dialog']['token']
        app.text_input(key='project_catalogue_query_' + token).set_value(product_code).run()
        self.healthy(app)
        tables = [item.value for item in app.dataframe if '产品编号' in item.value.columns]
        self.assertEqual(len(tables), 1)
        self.assertIn(product_code, tables[0]['产品编号'].tolist())
        self.assertLess(len(tables[0]), 1054)
        self.assertEqual(tables[0].columns.tolist(), ['厂商', '产品编号', '产品名称', '浓度', '浓度编号'])
        app.selectbox(key='project_catalogue_product_' + token).set_value(product_id).run()
        self.healthy(app)
        # Choosing a search result alone does not apply it to the project.
        self.assertIsNone(app.session_state['project_workspace_dialog']['draft']['qc_material_id'])
        self.assertTrue(list_project_templates().empty)
        app.button(key='project_catalogue_use').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['project_workspace_dialog']['draft']['qc_material_id'], product_id)
        self.assertEqual(app.selectbox(key=draft_key(app, 'qc_material_id')).value, product_id)
        return product_id

    def test_search_and_use_actual_product_id_then_save_one_project(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            ensure_builtin_bondson_directory()
            before = self.catalogue_and_project_state()
            app = self.new_project(data)
            product_id = self.choose_catalogue_product(app, 'IQC-BI-05903')
            self.assertNotEqual(product_id, data['qc_material_id'])
            app.button(key='project_dialog_save').click().run()
            self.healthy(app)
            projects = list_project_templates()
            self.assertEqual(len(projects), 1)
            saved = get_project_template(int(projects.iloc[0]['id']))
            self.assertEqual(int(saved['qc_material_id']), product_id)
            self.assertEqual((saved['default_qc_method'], int(saved['default_level_count'])), ('lj', 1))
            self.assertEqual(int(saved['lab_instrument_id']), data['lab_instrument_id'])
            self.assertEqual(int(saved['default_reagent_id']), data['reagent_id'])
            after = self.catalogue_and_project_state()
            for table in before:
                if table != 'qc_project_templates':
                    self.assertEqual(before[table], after[table], table)

    def test_same_target_combination_retains_explicit_method_and_cancel_saves_nothing(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            ensure_builtin_bondson_directory()
            product = list_catalog_product_choices().query("product_code == 'IQC-BI-05903'").iloc[0]
            self.assertEqual(product['product_name'], '糖化血红蛋白质控品')
            self.assertEqual(product['concentration_code'], '水平1/水平2')
            for method, level_count in (('lj', 1), ('zscore', 2), ('zscore', 3)):
                with self.subTest(method=method, level_count=level_count):
                    before = self.dump()
                    app = self.new_project(data, method=method, level_count=level_count)
                    original = dict(app.session_state['project_workspace_dialog']['draft'])
                    product_id = self.choose_catalogue_product(app, 'IQC-BI-05903')
                    actual = dict(app.session_state['project_workspace_dialog']['draft'])
                    self.assertEqual(actual, {**original, 'qc_material_id': product_id})
                    self.assertEqual(self.dump(), before)
                    app.button(key='project_dialog_cancel').click().run()
                    self.healthy(app)
                    self.assertTrue(any('是否放弃修改' in item.value for item in app.warning))
                    self.assertEqual(self.dump(), before)
                    app.button(key='project_discard_changes').click().run()
                    self.healthy(app)
                    self.assertNotIn('project_workspace_dialog', app.session_state)
                    self.assertTrue(list_project_templates().empty)
                    self.assertEqual(self.dump(), before)

    def test_full_app_prepares_empty_catalogue_and_global_entry_opens_it(self):
        with TemporaryDatabaseContext():
            isolated_path = database.DB_PATH
            self.assertTrue(list_catalog_product_choices().empty)
            self.assertTrue(list_product_directory_releases().empty)
            app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
            self.healthy(app)
            self.assertEqual(database.DB_PATH, isolated_path)
            self.assertEqual(len(list_catalog_product_choices()), 1054)
            releases = list_product_directory_releases()
            self.assertEqual(len(releases), 1)
            source = list_directory_products(int(releases.iloc[0]['id']), include_excluded=True)
            excluded_codes = set(source.loc[source.excluded_reason != '', 'product_code'])
            self.assertEqual(len(excluded_codes), 252)
            before = self.catalogue_and_project_state()
            app.button(key='open_qc_materials_page').click().run()
            self.healthy(app)
            self.assertTrue(app.session_state['show_qc_materials_page'])
            self.assertNotIn('qc_materials_tabs', app.session_state)
            self.assertIsNone(app.session_state['product_catalogue_selected_id'])
            table_index = next(index for index, table in enumerate(app.dataframe)
                if 'product_catalogue_table_' in table.proto.id)
            main = app.dataframe[table_index].value
            self.assertEqual(len(main), 1054)
            self.assertEqual(main.columns.tolist(), ['厂商', '产品编号', '产品名称', '浓度', '浓度编号', '状态'])
            self.assertTrue(excluded_codes.isdisjoint(main['产品编号']))
            self.assertFalse(any(widget.key == 'product_catalogue_product_id' for widget in app.selectbox))
            self.assertFalse(any(widget.key == 'material_catalog_product_id' for widget in app.selectbox))
            self.assertFalse(any(widget.key == 'product_catalogue_manage' for widget in app.button))
            self.assertFalse(app.button(key='material_catalog_add_product').disabled)
            for key in ('material_catalog_edit_product', 'material_catalog_status_product',
                        'product_catalogue_relationships', 'product_catalogue_batches'):
                self.assertTrue(app.button(key=key).disabled, key)
            product_code = main.iloc[0]['产品编号']
            expected_id = int(list_catalog_product_choices().query(
                'product_code == @product_code').iloc[0]['product_id'])
            select_table_row(app, 0, index=table_index)
            self.healthy(app)
            self.assertEqual(app.session_state['product_catalogue_selected_id'], expected_id)
            self.assertNotIn('material_dialog', app.session_state)
            self.assertNotIn('qc_materials_tabs', app.session_state)
            self.assertTrue(any('product_catalogue_table_' in table.proto.id for table in app.dataframe))
            for key in ('material_catalog_edit_product', 'material_catalog_status_product',
                        'product_catalogue_relationships', 'product_catalogue_batches'):
                self.assertFalse(app.button(key=key).disabled, key)
            self.assertEqual(self.catalogue_and_project_state(), before)
            app.button(key='qc_materials_home').click().run()
            self.healthy(app)
            self.assertFalse(app.session_state['show_qc_materials_page'])
            self.assertTrue(any(button.key == 'home_create_project' for button in app.button))
            self.assertEqual(self.catalogue_and_project_state(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
