"""Real shortcut UI steps, product relations, preserved drafts and atomic retry."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import get_connection
from pages.main_page import LJ_ENTRY_LABEL, ZSCORE_ENTRY_LABEL, INSTANT_ENTRY_LABEL
from services.qc_product_recommendation_service import save_qc_product_relation
from services.project_config_service import get_project_template
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import workspace_page, draft_key, assert_clean
from tests.quick_project_setup_smoke_test import metadata_values, database_contents


def product_prefix(app):
    return 'project_product_' + app.session_state['project_workspace_dialog']['token']


def quality_prefix(app):
    ctx = app.session_state['project_workspace_dialog']
    return 'quick_quality_' + ctx['token'] + '_' + ctx['quick_quality_identity']


def open_details(values, *, candidate=False):
    if candidate:
        save_qc_product_relation({key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id', 'qc_material_id')} |
            dict(scope_note='本仪器、本检测项和方法', evidence='已核对产品说明书及实验室适用范围', confirmed_by='关系核对人'))
    app = AppTest.from_function(workspace_page, default_timeout=20).run()
    assert_clean(app)
    app.button(key='home_create_project').click().run()
    assert app.session_state['project_workspace_dialog']['kind'] == 'quick'
    for field in ('test_item_id', 'lab_instrument_id', 'reagent_id', 'method_id', 'unit_id'):
        app.selectbox(key=draft_key(app, field)).set_value(values[field]).run()
        assert_clean(app)
    token = app.session_state['project_workspace_dialog']['token']
    if values['qc_method'] == 'zscore':
        app.radio(key='project_kind_' + token).set_value('多水平').run()
        app.selectbox(key=draft_key(app, 'level_count')).set_value(values['level_count']).run()
    elif values['qc_method'] == 'instant':
        app.checkbox(key='project_instant_' + token).check().run()
    prefix = product_prefix(app)
    if candidate:
        app.radio(key=prefix + '_mode').set_value('已核对的候选').run()
    app.selectbox(key=prefix + '_product').set_value(values['qc_material_id']).run()
    if not candidate:
        app.text_input(key=prefix + '_person').set_value('人工选品核对人')
        app.text_area(key=prefix + '_evidence').set_value('已逐项核对仪器、试剂、项目、方法及质控品适用性。')
    app.button(key=prefix + '_confirm').click().run()
    assert_clean(app)
    assert not app.button(key='quick_details_next').disabled
    for index, level_id in enumerate(values['level_ids'], 1):
        app.selectbox(key=f"quick_material_{token}_{values['qc_material_id']}_{index}").set_value(level_id).run()
    app.text_input(key=draft_key(app, 'template_name')).set_value(values['template_name'])
    app.text_input(key=draft_key(app, 'config_name')).set_value(values['config_name'])
    app.text_area(key=draft_key(app, 'notes')).set_value('完整项目草稿备注')
    app.run()
    assert_clean(app)
    assert app.text_input(key=draft_key(app, 'template_name')).value == values['template_name']
    return app


def fill_quality(app, values):
    prefix = quality_prefix(app)
    quality = values['quality']
    for key in ('technique', 'result_kind', 'result_scale', 'purpose'):
        widget = app.selectbox(key=prefix + '_context_' + key)
        if not widget.disabled:
            widget.set_value(quality['context'][key])
    app.text_input(key=prefix + '_context_specimen').set_value(quality['context']['specimen'])
    app.run()
    assert_clean(app)
    mode_widgets = [widget for widget in app.radio if widget.key == prefix + '_mode']
    if mode_widgets:
        mode_widgets[0].set_value(quality['mode']).run()
    if quality['mode'] == 'source':
        identifier = quality['requirement_id']
        assert app.session_state[prefix + '_requirement'] == identifier
        for level in quality['levels']:
            fields = [widget for widget in app.number_input
                      if widget.key == f"{prefix}_{identifier}_{level['level_order']}_concentration"]
            if fields:
                fields[0].set_value(level['concentration'])
        if quality.get('supplemental_cv') is not None:
            app.number_input(key=prefix + '_supplemental_cv').set_value(quality['supplemental_cv'])
    else:
        for field, suffix in (('source_name', '_source_name'), ('source_version', '_source_version')):
            app.text_input(key=prefix + suffix).set_value(quality[field])
        app.text_area(key=prefix + '_recorded').set_value(quality['requirement_text'])
    app.text_input(key=prefix + '_person').set_value(quality['confirmed_by'])
    app.text_area(key=prefix + '_evidence').set_value(quality['evidence'])
    if quality['mode'] == 'recorded':
        search = quality['search_record']
        app.checkbox(key=prefix + '_advanced_search').check()
        app.text_input(key=prefix + '_search_query').set_value(search['query'])
        app.text_input(key=prefix + '_search_url').set_value(search['official_url'])
        app.text_area(key=prefix + '_search_rationale').set_value(search['rationale'])
        app.selectbox(key=prefix + '_search_conclusion').set_value(search['conclusion'])
    app.checkbox(key=prefix + '_checked').check().run()
    assert_clean(app)
    return prefix


def to_preview(app, values):
    app.button(key='quick_details_next').click().run()
    assert_clean(app)
    fill_quality(app, values)
    app.button(key='quick_quality_next').click().run()
    assert_clean(app)
    assert app.session_state['project_workspace_dialog']['quick_step'] == 'preview'


class QuickProjectSetupUiTests(unittest.TestCase):
    def test_mode_switch_preserves_original_until_confirm_and_keeps_common_draft_without_writes(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3)
            app = open_details(values)
            to_preview(app, values)
            app.button(key='quick_preview_back').click().run()
            app.button(key='quick_quality_back').click().run()
            assert_clean(app)
            context = app.session_state['project_workspace_dialog']
            original_token = context['token']
            original = deepcopy(context['draft'])
            before = database_contents()
            app.button(key='quick_use_group').click().run()
            assert_clean(app)
            self.assertEqual('group', app.session_state['project_workspace_dialog']['setup_mode_switch'])
            self.assertEqual(original, app.session_state['project_workspace_dialog']['draft'])
            self.assertEqual(original_token, app.session_state['project_workspace_dialog']['token'])
            self.assertEqual(before, database_contents())
            app.button(key='setup_mode_keep').click().run()
            assert_clean(app)
            context = app.session_state['project_workspace_dialog']
            self.assertEqual('quick', context['kind'])
            self.assertEqual(original_token, context['token'])
            self.assertEqual(original, context['draft'])
            self.assertEqual(values['template_name'], app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual('人工选品核对人', app.text_input(key=product_prefix(app) + '_person').value)
            self.assertEqual(values['level_ids'], context['draft']['level_ids'])
            self.assertEqual(values['quality']['source_version'], context['draft']['quality']['source_version'])
            app.button(key='quick_use_group').click().run()
            app.button(key='setup_mode_change').click().run()
            assert_clean(app)
            context = app.session_state['project_workspace_dialog']
            self.assertEqual('project', context['kind'])
            self.assertNotEqual(original_token, context['token'])
            for field in ('template_name', 'lab_instrument_id', 'qc_material_id', 'project_group', 'notes'):
                self.assertEqual(original[field], context['draft'][field])
            for field, previous in [('default_reagent_id', 'reagent_id'), ('default_method_id', 'method_id'),
                                    ('default_qc_method', 'qc_method'), ('default_level_count', 'level_count')]:
                self.assertEqual(original[previous], context['draft'][field])
            for identity, cached in original['manual_choice_drafts'].items():
                self.assertEqual(cached, context['draft']['manual_choice_drafts'][identity])
            self.assertNotIn('quality', context['draft'])
            self.assertNotIn('level_ids', context['draft'])
            self.assertEqual(values['template_name'], app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual(before, database_contents())

    def test_complete_manual_and_candidate_paths_save_once_and_route_to_correct_method(self):
        cases = [('lj', 1, False), ('zscore', 2, True), ('zscore', 3, False), ('instant', 1, True)]
        for method, levels, candidate in cases:
            with self.subTest(method=method, levels=levels, candidate=candidate), IsolatedDatabase():
                values = metadata_values(method, levels)
                app = open_details(values, candidate=candidate)
                before = database_contents()
                app.button(key='quick_details_next').click().run()
                assert_clean(app)
                self.assertEqual(before, database_contents())
                fill_quality(app, values)
                app.button(key='quick_quality_next').click().run()
                assert_clean(app)
                self.assertEqual(before, database_contents())
                state = app.session_state['project_workspace_dialog']
                self.assertEqual('recommendation' if candidate else 'manual', state['draft']['product_choice_review']['basis'])
                app.button(key='quick_save').click().run()
                assert_clean(app)
                self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
                self.assertEqual({'lj': LJ_ENTRY_LABEL, 'zscore': ZSCORE_ENTRY_LABEL, 'instant': INSTANT_ENTRY_LABEL}[method],
                                 app.session_state['pending_top_level_method'])
                prefix = {'lj': '', 'zscore': 'zscore_', 'instant': 'instant_'}[method]
                with get_connection() as connection:
                    self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])
                    binding = connection.execute('SELECT * FROM qc_workbench_bindings').fetchone()
                    self.assertEqual('active', binding['binding_status'])
                    self.assertEqual(method, binding['qc_method'])
                    self.assertEqual(binding['runtime_batch_id'], app.session_state[prefix + 'selected_batch_id'])
                    self.assertEqual(binding['runtime_project_id'], app.session_state[prefix + 'selected_project_id'])
                self.assertEqual(values['template_name'], get_project_template(app.session_state['workspace_project_id'])['template_name'])

    def test_cancel_continue_and_back_steps_keep_all_draft_fields_without_writes(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3)
            app = open_details(values)
            before = database_contents()
            app.button(key='quick_details_next').click().run()
            prefix = fill_quality(app, values)
            app.button(key='project_dialog_cancel').click().run()
            assert_clean(app)
            self.assertTrue(app.session_state['project_workspace_dialog']['discard'])
            self.assertEqual(before, database_contents())
            app.button(key='project_continue_edit').click().run()
            assert_clean(app)
            self.assertEqual('recorded', app.radio(key=prefix + '_mode').value)
            self.assertEqual(values['quality']['source_name'], app.text_input(key=prefix + '_source_name').value)
            self.assertEqual(values['quality']['source_version'], app.text_input(key=prefix + '_source_version').value)
            self.assertEqual(values['quality']['requirement_text'], app.text_area(key=prefix + '_recorded').value)
            app.button(key='quick_quality_next').click().run()
            assert_clean(app)
            app.button(key='quick_preview_back').click().run()
            assert_clean(app)
            self.assertEqual('recorded', app.radio(key=prefix + '_mode').value)
            self.assertEqual(values['quality']['source_version'], app.text_input(key=prefix + '_source_version').value)
            app.button(key='quick_quality_back').click().run()
            assert_clean(app)
            self.assertEqual(values['template_name'], app.text_input(key=draft_key(app, 'template_name')).value)
            self.assertEqual('完整项目草稿备注', app.text_area(key=draft_key(app, 'notes')).value)
            product = product_prefix(app)
            self.assertEqual('人工选品核对人', app.text_input(key=product + '_person').value)
            self.assertEqual('已逐项核对仪器、试剂、项目、方法及质控品适用性。', app.text_area(key=product + '_evidence').value)
            self.assertEqual(values['level_ids'], app.session_state['project_workspace_dialog']['draft']['level_ids'])
            self.assertEqual(before, database_contents())
            app.button(key='project_dialog_cancel').click().run()
            app.button(key='project_discard_changes').click().run()
            assert_clean(app)
            self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
            self.assertEqual(before, database_contents())

    def test_stale_material_or_relation_preview_does_not_save_and_keeps_the_draft(self):
        for change in ('lot', 'relation'):
            with self.subTest(change=change), IsolatedDatabase():
                values = metadata_values()
                app = open_details(values)
                to_preview(app, values)
                draft = deepcopy(app.session_state['project_workspace_dialog']['draft'])
                if change == 'lot':
                    with get_connection() as connection:
                        connection.execute("UPDATE md_qc_material_lots SET expiry_date='2029-12-31' WHERE id=(SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?)", (values['level_ids'][0],))
                else:
                    save_qc_product_relation({key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id', 'qc_material_id')} |
                        dict(scope_note='核对后的项目范围', evidence='适用资料更新', confirmed_by='更新核对人'))
                before = database_contents()
                app.button(key='quick_save').click().run()
                assert_clean(app)
                self.assertTrue(list(app.error))
                self.assertEqual('preview', app.session_state['project_workspace_dialog']['quick_step'])
                self.assertEqual(draft, app.session_state['project_workspace_dialog']['draft'])
                self.assertEqual(before, database_contents())
                expected = ('项目资料或可用质控品已修改', '返回上一步') if change == 'lot' else (
                    '该试剂可用的质控品资料已修改', '重新选择质控品')
                self.assertTrue(any(all(text in str(item.value) for text in expected) for item in app.error))

    def test_numeric_quality_back_step_retains_automatic_uniform_standard_and_stricter_cv(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3, numeric=True)
            values['quality']['supplemental_cv'] = 2.0
            app = open_details(values)
            before = database_contents()
            to_preview(app, values)
            app.button(key='quick_preview_back').click().run()
            assert_clean(app)
            prefix = quality_prefix(app)
            identifier = values['quality']['requirement_id']
            self.assertEqual(identifier, app.session_state[prefix + '_requirement'])
            self.assertFalse(any(widget.key == prefix + '_requirement' for widget in app.selectbox))
            for level in values['quality']['levels']:
                self.assertFalse(any(widget.key == f"{prefix}_{identifier}_{level['level_order']}_concentration"
                                     for widget in app.number_input))
            self.assertEqual([1, 2, 3], [row['level_order'] for row in
                             app.session_state['project_workspace_dialog']['draft']['quality']['levels']])
            self.assertEqual(2.0, app.number_input(key=prefix + '_supplemental_cv').value)
            self.assertEqual(before, database_contents())

    def test_storage_failure_retains_every_field_and_retry_saves_one_setup(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            to_preview(app, values)
            draft = deepcopy(app.session_state['project_workspace_dialog']['draft'])
            with get_connection() as connection:
                connection.execute("CREATE TRIGGER reject_shortcut BEFORE INSERT ON qc_quick_setup_receipts BEGIN SELECT RAISE(ABORT,'storage unavailable'); END")
            before = database_contents()
            app.button(key='quick_save').click().run()
            assert_clean(app)
            self.assertTrue(any('全部填写内容仍保留' in str(item.value) for item in app.error))
            self.assertEqual(draft, app.session_state['project_workspace_dialog']['draft'])
            self.assertEqual(before, database_contents())
            with get_connection() as connection:
                connection.execute('DROP TRIGGER reject_shortcut')
            app.button(key='quick_save').click().run()
            assert_clean(app)
            self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
            with get_connection() as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])

    def test_register_relation_immediately_refreshes_candidates_without_creating_a_project(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            prefix = product_prefix(app)
            self.assertEqual(['全部质控品'], app.radio(key=prefix + '_mode').options)
            app.button(key=prefix + '_relation_add').click().run()
            assert_clean(app)
            editor = app.session_state['project_workspace_dialog'][prefix + '_editor']
            ep = prefix + '_' + editor['token']
            for field, value in [('scope_note', '本仪器、检验项目与方法'), ('evidence', '已核对说明书及实验室适用依据'), ('confirmed_by', '关系登记核对人')]:
                app.text_input(key=ep + '_' + field).set_value(value)
            app.button(key=ep + '_save').click().run()
            assert_clean(app)
            self.assertIn('已登记的配套质控品', app.radio(key=prefix + '_mode').options)
            self.assertTrue(any('已核对说明书及实验室适用依据' in str(item.value) for item in app.caption))
            self.assertTrue(app.button(key='quick_details_next').disabled)
            app.radio(key=prefix + '_mode').set_value('已核对的候选').run()
            app.button(key=prefix + '_confirm').click().run()
            assert_clean(app)
            self.assertEqual('recommendation', app.session_state['project_workspace_dialog']['draft']['product_choice_review']['basis'])
            self.assertFalse(app.button(key='quick_details_next').disabled)
            with get_connection() as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_product_recommendations').fetchone()[0])
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0])
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
