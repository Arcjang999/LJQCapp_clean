"""Reference form instructions and specification drafts use the current inputs."""
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.material_catalog_service import get_control_material, get_material_product, list_control_materials
from services.material_workflow_service import list_material_specs, register_control_material
from tests.material_catalog_smoke_test import seed_product
from tests.master_data_dialogs_smoke_test import snapshot
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config,
)


MATERIAL_APP = '''
import streamlit as st
from ui.material_catalog import _material_editor
def saved(identifier):
 st.session_state['saved_material']=identifier
_material_editor(st.session_state['form_state'],on_saved=saved,on_cancel=lambda:None)
'''
PRODUCT_APP = '''
import streamlit as st
from ui.material_catalog import _product_editor
_product_editor(st.session_state['form_state'],on_saved=lambda _:None,on_cancel=lambda:None)
'''


def feedback(app):
    return '\n'.join(item.value for kind in ('info', 'caption', 'warning') for item in getattr(app, kind))


class ReferenceFormFeedbackTests(unittest.TestCase):
    def clean(self, app):
        self.assertFalse(list(app.exception), [str(error) for error in app.exception])

    def material_form(self, product_id, *, level_id=None):
        app = AppTest.from_string(MATERIAL_APP, default_timeout=30)
        app.session_state['form_state'] = dict(product_id=product_id, level_id=level_id,
            record=get_control_material(level_id) if level_id else None, nonce=1)
        app.run()
        self.clean(app)
        return app

    def product_form(self, product_id):
        app = AppTest.from_string(PRODUCT_APP, default_timeout=30)
        app.session_state['form_state'] = dict(product_id=product_id,
            record=get_material_product(product_id), nonce=2)
        app.run()
        self.clean(app)
        return app

    def product_with_spec(self):
        product = seed_product()
        level = register_control_material(material_id=product, level_name='已有低值', level_code='L1',
            catalog_no='KNOWN-CAT', lot_no='KNOWN', expiry_date='2030-01-01')
        spec = int(list_material_specs(product).iloc[0].id)
        return product, spec, level

    def test_new_level_draft_survives_existing_specification_and_saves_current_values(self):
        with TemporaryDatabaseContext():
            product, spec, original_level = self.product_with_spec()
            app = self.material_form(product)
            before = snapshot()
            for field, value in (('name', '本次高值'), ('code', 'HIGH-NEW'), ('catalog', 'HIGH-CAT')):
                app.text_input(key='material_edit_1_new_'+field).set_value(value)
            app.text_input(key='material_edit_1_lot').set_value('NEW-LOT')
            app.date_input(key='material_edit_1_expiry').set_value(date(2031, 1, 2))
            app.text_area(key='material_edit_1_note').set_value('本次填写的说明').run()
            app.selectbox(key='material_edit_1_spec').set_value(spec).run()
            self.clean(app)
            self.assertTrue(app.text_input(key=f'material_edit_1_{spec}_name').disabled)
            self.assertEqual('已有低值', app.text_input(key=f'material_edit_1_{spec}_name').value)
            self.assertIn('需要登记新水平时，在该下拉框选择“新增浓度水平”', feedback(app))
            app.selectbox(key='material_edit_1_spec').set_value(None).run()
            self.clean(app)
            self.assertEqual(['本次高值', 'HIGH-NEW', 'HIGH-CAT'],
                [app.text_input(key='material_edit_1_new_'+field).value for field in ('name', 'code', 'catalog')])
            self.assertEqual('NEW-LOT', app.text_input(key='material_edit_1_lot').value)
            self.assertEqual(date(2031, 1, 2), app.date_input(key='material_edit_1_expiry').value)
            self.assertEqual('本次填写的说明', app.text_area(key='material_edit_1_note').value)
            self.assertEqual(before, snapshot())
            app.button(key='material_edit_1_save').click().run()
            self.clean(app)
            saved = get_control_material(app.session_state['saved_material'])
            self.assertEqual(('本次高值', 'HIGH-NEW', 'HIGH-CAT', 'NEW-LOT', '2031-01-02'),
                tuple(saved[field] for field in ('level_name', 'level_code', 'specification_catalog_no', 'lot_no', 'expiry_date')))
            self.assertEqual('已有低值', get_control_material(original_level)['level_name'])
            self.assertEqual(2, len(list_control_materials(product)))

    def test_immediate_specification_switch_keeps_the_latest_uncommitted_identity(self):
        with TemporaryDatabaseContext():
            product, spec, _ = self.product_with_spec()
            app = self.material_form(product)
            before = snapshot()
            app.text_input(key='material_edit_1_new_name').set_value('直接切换前刚输入')
            app.text_input(key='material_edit_1_new_code').set_value('LATEST')
            app.text_input(key='material_edit_1_new_catalog').set_value('LATEST-CAT')
            app.selectbox(key='material_edit_1_spec').set_value(spec).run()
            app.selectbox(key='material_edit_1_spec').set_value(None).run()
            self.clean(app)
            self.assertEqual(['直接切换前刚输入', 'LATEST', 'LATEST-CAT'],
                [app.text_input(key='material_edit_1_new_'+field).value for field in ('name', 'code', 'catalog')])
            self.assertEqual(before, snapshot())

    def test_locked_material_and_product_instructions_name_the_return_action(self):
        with TemporaryDatabaseContext():
            data = _seed_v11_configuration_dependencies()
            _build_active_source_config(data)
            before = snapshot()
            app = self.material_form(data['qc_material_id'], level_id=data['source_levels'][0])
            self.assertTrue(app.text_input(key='material_edit_1_lot').disabled)
            self.assertIn('先点击“保存修改”或“取消”返回批号列表', feedback(app))
            self.assertTrue(any(button.key=='material_edit_1_cancel' for button in app.button))
            product = self.product_form(data['qc_material_id'])
            self.assertTrue(product.text_input(key='product_edit_2_generic_name').disabled)
            self.assertIn('先点击“保存修改”或“取消”返回质控品列表', feedback(product))
            self.assertTrue(any(button.key=='product_edit_2_cancel' for button in product.button))
            self.assertEqual(before, snapshot())

    def test_unreferenced_product_edit_explains_its_fixed_manufacturer(self):
        with TemporaryDatabaseContext():
            product = seed_product()
            before = snapshot()
            app = self.product_form(product)
            self.assertTrue(app.selectbox(key='product_edit_2_manufacturer').disabled)
            self.assertFalse(app.text_input(key='product_edit_2_generic_name').disabled)
            self.assertIn('厂家沿用当前产品，不能在编辑时更换', feedback(app))
            self.assertIn('点击“取消”返回列表，再点“新增质控品”', feedback(app))
            self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
