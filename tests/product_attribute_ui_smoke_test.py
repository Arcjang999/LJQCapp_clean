"""Product attributes survive actual create/edit and nested-manufacturer forms."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from services.material_catalog_service import get_material_product
from services.master_data_service import (
    create_manufacturer, create_qc_lot, create_qc_material, list_manufacturers,
    list_qc_materials,
)
from tests.master_data_dialogs_smoke_test import snapshot
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext


APP = '''
import streamlit as st
from ui.material_catalog import _product_editor
def saved(identifier):
 st.session_state['saved_product'] = identifier
 st.session_state['closed'] = True
 st.rerun()
def cancelled():
 st.session_state['closed'] = True
 st.rerun()
if st.session_state.get('closed'):
 st.write('已返回产品清单。')
else:
 _product_editor(st.session_state['form_state'],on_saved=saved,on_cancel=cancelled)
'''


class ProductAttributeUITests(unittest.TestCase):
    def healthy(self, app):
        self.assertFalse(list(app.exception), [str(error) for error in app.exception])

    def open(self, product_id=None):
        app = AppTest.from_string(APP, default_timeout=30)
        app.session_state['form_state'] = dict(product_id=product_id,
            record=get_material_product(product_id) if product_id else None, nonce=1)
        app.run()
        self.healthy(app)
        return app

    def maker(self):
        return create_manufacturer(display_name='产品属性核对厂家', categories=['qc_material'])

    def fill_attributes(self, app, code='001', concentration='1.00*10^5～1.00*10^6 IU/mL'):
        app.text_input(key='product_edit_1_concentration_code').set_value(code)
        app.text_input(key='product_edit_1_concentration').set_value(concentration)

    def assert_attributes(self, app, code='001', concentration='1.00*10^5～1.00*10^6 IU/mL'):
        self.assertEqual(code, app.text_input(key='product_edit_1_concentration_code').value)
        self.assertEqual(concentration, app.text_input(key='product_edit_1_concentration').value)

    def test_new_product_saves_both_attributes_and_edit_reopens_them(self):
        with TemporaryDatabaseContext():
            maker = self.maker()
            app = self.open()
            app.selectbox(key='product_edit_1_manufacturer').set_value(maker)
            app.text_input(key='product_edit_1_generic_name').set_value('手工登记浓度产品')
            app.text_input(key='product_edit_1_catalog_no').set_value('LOCAL-001')
            self.fill_attributes(app)
            app.button(key='product_edit_1_save').click().run()
            self.healthy(app)
            product_id = app.session_state['saved_product']
            row = get_material_product(product_id)
            self.assertEqual(('001', '1.00*10^5～1.00*10^6 IU/mL'),
                (row['concentration_code'], row['concentration']))
            edit = self.open(product_id)
            self.assert_attributes(edit)
            self.fill_attributes(edit, 'S10', '阴性 / 基因型1b')
            edit.button(key='product_edit_1_save').click().run()
            self.healthy(edit)
            current = get_material_product(product_id)
            self.assertEqual(('S10', '阴性 / 基因型1b'),
                (current['concentration_code'], current['concentration']))
            self.assertEqual(product_id, edit.session_state['saved_product'])

    def test_cancel_keeps_draft_until_explicit_discard_without_writing(self):
        with TemporaryDatabaseContext():
            self.maker()
            before = snapshot()
            app = self.open()
            self.fill_attributes(app)
            app.button(key='product_edit_1_cancel').click().run()
            self.healthy(app)
            self.assert_attributes(app)
            self.assertEqual(before, snapshot())
            app.button(key='product_edit_1_continue').click().run()
            self.assert_attributes(app)
            app.button(key='product_edit_1_cancel').click().run()
            app.button(key='product_edit_1_discard_confirm').click().run()
            self.healthy(app)
            self.assertTrue(app.session_state['closed'])
            self.assertEqual(before, snapshot())

    def test_manufacturer_cancel_and_save_preserve_latest_product_attributes(self):
        with TemporaryDatabaseContext():
            before = snapshot()
            app = self.open()
            app.text_input(key='product_edit_1_generic_name').set_value('厂家往返保留产品')
            self.fill_attributes(app)
            app.button(key='product_edit_1_add_manufacturer').click().run()
            self.healthy(app)
            child = app.session_state['form_state']['manufacturer_editor']
            child_key = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=child_key+'display_name').set_value('未保存厂家').run()
            app.button(key='md_dialog_cancel').click().run()
            app.button(key='md_dialog_discard').click().run()
            self.healthy(app)
            self.assert_attributes(app)
            self.assertEqual(before, snapshot())
            app.button(key='product_edit_1_add_manufacturer').click().run()
            child = app.session_state['form_state']['manufacturer_editor']
            child_key = 'md_dialog_' + child['token'] + '_'
            app.text_input(key=child_key+'display_name').set_value('已登记产品厂家')
            app.button(key='md_dialog_save').click().run()
            self.healthy(app)
            self.assert_attributes(app)
            maker = int(list_manufacturers().iloc[0]['id'])
            self.assertEqual(maker, app.selectbox(key='product_edit_1_manufacturer').value)
            self.assertTrue(list_qc_materials().empty)
            app.button(key='product_edit_1_save').click().run()
            self.healthy(app)
            saved = get_material_product(app.session_state['saved_product'])
            self.assertEqual(('001', '1.00*10^5～1.00*10^6 IU/mL'),
                (saved['concentration_code'], saved['concentration']))

    def test_referenced_product_shows_attributes_read_only_and_can_keep_notes(self):
        with TemporaryDatabaseContext():
            product_id = create_qc_material(manufacturer_id=self.maker(), generic_name='已用浓度产品',
                concentration_code='S3', concentration='1.00*10^6 IU/mL')
            create_qc_lot(qc_material_id=product_id, lot_no='USED-LOT', expiry_date='2030-01-01')
            before = snapshot()
            app = self.open(product_id)
            self.assert_attributes(app, 'S3', '1.00*10^6 IU/mL')
            for field in ('concentration_code', 'concentration'):
                self.assertTrue(app.text_input(key='product_edit_1_'+field).disabled)
            self.assertEqual(before, snapshot())
            app.text_area(key='product_edit_1_notes').set_value('补充说明')
            app.button(key='product_edit_1_save').click().run()
            self.healthy(app)
            current = get_material_product(product_id)
            self.assertEqual(('S3', '1.00*10^6 IU/mL', '补充说明'),
                (current['concentration_code'], current['concentration'], current['notes']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
