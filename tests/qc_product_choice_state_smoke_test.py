"""Product refresh and relation drafts retain their explicit save boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from database import read_snapshot
from services.master_data_service import create_qc_material, create_reagent
from services.qc_product_recommendation_service import (
    save_qc_product_relation, set_qc_product_relation_disabled,
)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values, database_contents


APP = '''import streamlit as st
from ui.qc_product_choice import render_product_choice
ctx = st.session_state['choice_context']
draft = ctx['draft']
if ctx.get('reagents'):
    draft['reagent_id'] = st.selectbox('本次试剂', ctx['reagents'], key='scope_reagent')
ctx['choice_valid'] = render_product_choice(ctx, reagent_id=draft['reagent_id'],
    lab_instrument_id=draft['lab_instrument_id'], test_item_id=draft['test_item_id'],
    method_id=draft['method_id'], prefix='choice')
'''


def relation(values, **changes):
    payload = {key: values[key] for key in (
        'reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id', 'qc_material_id')}
    return dict(payload, scope_note='当前检测条件', evidence='已核对适用材料',
                confirmed_by='关系核对人', **changes)


class ProductChoiceStateTests(unittest.TestCase):
    def open(self, values, **extra):
        app = AppTest.from_string(APP, default_timeout=15)
        draft = deepcopy(values)
        draft.pop('product_choice_review', None)
        app.session_state['choice_context'] = dict(token='choice-test', draft=draft, **extra)
        if extra.get('reagents'):
            app.session_state['scope_reagent'] = values['reagent_id']
        app.run()
        self.clean(app)
        return app

    def clean(self, app):
        self.assertFalse(list(app.exception), list(app.exception))

    def editor(self, app):
        return app.session_state['choice_context'].get('choice_editor')

    def fill_editor(self, app, *, evidence='新填写的适用核对依据'):
        prefix = 'choice_' + self.editor(app)['token']
        for field, value in (('scope_note', '尚未保存的适用范围'),
                             ('evidence', evidence), ('confirmed_by', '实际核对人')):
            app.text_input(key=prefix + '_' + field).set_value(value)
        app.run()
        self.clean(app)
        return prefix

    def test_open_relation_blocks_replacement_and_cancel_continue_preserves_draft(self):
        with IsolatedDatabase():
            app = self.open(metadata_values())
            before = database_contents()
            app.button(key='choice_relation_add').click().run()
            prefix = self.fill_editor(app)
            original = deepcopy(self.editor(app))
            self.assertTrue(app.button(key='choice_relation_add').disabled,
                            'An open relation draft must not be replaced by the toolbar.')
            app.button(key=prefix + '_cancel').click().run()
            self.assertTrue(self.editor(app)['discard'])
            self.assertTrue(app.button(key='choice_relation_add').disabled)
            app.button(key=prefix + '_keep').click().run()
            self.clean(app)
            self.assertEqual(original['values'], self.editor(app)['values'])
            self.assertEqual('尚未保存的适用范围', app.text_input(key=prefix + '_scope_note').value)
            app.button(key=prefix + '_cancel').click().run()
            app.button(key=prefix + '_discard').click().run()
            self.clean(app)
            self.assertIsNone(self.editor(app))
            self.assertFalse(app.button(key='choice_relation_add').disabled)
            self.assertEqual(before, database_contents())

    def test_relation_save_only_registers_relation_and_external_disable_requires_new_review(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = self.open(values)
            app.button(key='choice_relation_add').click().run()
            prefix = self.fill_editor(app)
            app.button(key=prefix + '_save').click().run()
            self.clean(app)
            self.assertIsNone(self.editor(app))
            self.assertFalse(app.session_state['choice_context']['choice_valid'])
            app.radio(key='choice_mode').set_value('已核对的候选').run()
            app.button(key='choice_confirm').click().run()
            self.assertTrue(app.session_state['choice_context']['choice_valid'])
            with read_snapshot() as connection:
                row = connection.execute('SELECT id,revision_no FROM qc_product_recommendations').fetchone()
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_product_recommendation_history').fetchone()[0])
                for table in ('qc_project_templates', 'qc_project_template_items', 'qc_lot_configs', 'batches'):
                    self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])
            set_qc_product_relation_disabled(row['id'], disabled=True, reason='外部重新核对',
                                            confirmed_by='另一位核对人', expected_revision=row['revision_no'])
            before_refresh = database_contents()
            app.run()
            self.clean(app)
            self.assertFalse(app.session_state['choice_context']['choice_valid'])
            self.assertNotIn('product_choice_review', app.session_state['choice_context']['draft'])
            self.assertEqual('人工选品', app.radio(key='choice_mode').value)
            self.assertEqual(before_refresh, database_contents())

    def test_changed_scope_refreshes_candidates_and_clears_old_selection_review(self):
        with IsolatedDatabase():
            values = metadata_values()
            with read_snapshot() as connection:
                maker = connection.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?',
                                           (values['qc_material_id'],)).fetchone()[0]
            other_reagent = create_reagent(manufacturer_id=maker, generic_name='第二种适用试剂')
            other_product = create_qc_material(manufacturer_id=maker, generic_name='第二种适用质控品')
            save_qc_product_relation(relation(values))
            changed = dict(values, reagent_id=other_reagent, qc_material_id=other_product)
            save_qc_product_relation(relation(changed))
            before = database_contents()
            app = self.open(values, reagents=[values['reagent_id'], other_reagent])
            app.button(key='choice_confirm').click().run()
            self.assertTrue(app.session_state['choice_context']['choice_valid'])
            app.selectbox(key='scope_reagent').set_value(other_reagent).run()
            self.clean(app)
            draft = app.session_state['choice_context']['draft']
            self.assertIsNone(app.selectbox(key='choice_product').value)
            self.assertIsNone(draft['qc_material_id'])
            self.assertNotIn('product_choice_review', draft)
            self.assertFalse(app.session_state['choice_context']['choice_valid'])
            app.selectbox(key='choice_product').set_value(other_product).run()
            app.button(key='choice_confirm').click().run()
            self.assertTrue(app.session_state['choice_context']['choice_valid'])
            self.assertEqual(other_reagent, app.session_state['choice_context']['draft']['product_choice_review']['context']['reagent_id'])
            self.assertEqual(before, database_contents())

    def test_external_relation_change_rejects_save_but_preserves_editor_and_cancel(self):
        with IsolatedDatabase():
            values = metadata_values()
            relation_id = save_qc_product_relation(relation(values))
            app = self.open(values)
            app.session_state['choice_relations_selected'] = relation_id
            app.run()
            app.button(key='choice_relation_edit').click().run()
            prefix = self.fill_editor(app)
            set_qc_product_relation_disabled(relation_id, disabled=True, reason='外部停用',
                                            confirmed_by='另一个核对人', expected_revision=1)
            before = database_contents()
            app.button(key=prefix + '_save').click().run()
            self.clean(app)
            self.assertTrue(any('已变化' in error.value for error in app.error))
            self.assertEqual('新填写的适用核对依据', self.editor(app)['values']['evidence'])
            self.assertEqual(before, database_contents())
            app.button(key=prefix + '_cancel').click().run()
            app.button(key=prefix + '_keep').click().run()
            self.clean(app)
            self.assertEqual('新填写的适用核对依据', app.text_input(key=prefix + '_evidence').value)
            app.button(key=prefix + '_cancel').click().run()
            app.button(key=prefix + '_discard').click().run()
            self.assertIsNone(self.editor(app))
            self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
