"""Ct-only controls, independent drafts, preview binding and project round trips."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.master_data_service import list_units
from services.project_config_service import (create_project_template, get_project_template,
    list_template_items, template_item_rows, preview_panel_items, save_panel_items)
from services.project_workspace_service import save_single_test_item
from services.project_config_io_service import (build_project_template_xlsx,
    preview_project_template_xlsx, import_project_template_xlsx, preview_panel_import_rows,
    CT_ALERT_COLUMNS)
from services.quality_review_service import validate_project_quality
from services.quality_target_service import decode, item_context
from services.quick_project_setup_service import prepare_quick_project_setup, save_quick_project_setup
from tests.quality_applicability_smoke_test import IsolatedDatabase, draft, context
from tests.quality_simple_ui_smoke_test import screen, set_conditions, assert_clean
from tests.quality_simple_setup_smoke_test import simple_values
from tests.quick_project_setup_smoke_test import database_contents


POLICY = dict(enabled=True, operator='<=', threshold=38.0, version=1)


def ct_values(method='lj', levels=1):
    values = simple_values(method, levels)
    values.update(input_value_type='ct', unit_id=int(list_units().loc[lambda frame: frame.symbol == 'Ct', 'id'].iloc[0]))
    values['quality']['context']['result_scale'] = 'ct'
    values['quality']['ct_alert'] = deepcopy(POLICY)
    return values


def create_source(values):
    preview = prepare_quick_project_setup(values)
    return save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='ct-settings-source')


def empty_target(values, name):
    return create_project_template(template_name=name,
        lab_instrument_id=values['lab_instrument_id'], qc_material_id=values['qc_material_id'])


class CtAlertSettingsTests(unittest.TestCase):
    def test_quick_ct_edits_clear_confirmation_and_preview_but_keep_written_evidence(self):
        from streamlit.testing.v1 import AppTest
        with IsolatedDatabase():
            _, item = draft('Ct快速填写项目', mode='ct', unit='Ct', method='其他方法')
            app = AppTest.from_string(
                "import streamlit as st\n"
                "from ui.quality_targets import render_quality_setup\n"
                "from services.quality_target_service import item_context\n"
                "st.session_state.setdefault('project_workspace_dialog', {})\n"
                f"render_quality_setup(item_context('project', {item}), {{}}, 'ct_quick', quick=True)",
                default_timeout=30).run()
            app.session_state['project_workspace_dialog']['quick_preview'] = {'old': True}
            app.checkbox(key='ct_quick_ct_alert_enabled').check().run()
            self.assertNotIn('quick_preview', app.session_state['project_workspace_dialog'])
            app.selectbox(key='ct_quick_ct_alert_operator').set_value('<=')
            app.number_input(key='ct_quick_ct_alert_threshold').set_value(38.0)
            app.text_area(key='ct_quick_evidence').set_value('本实验室已核对的依据文字')
            app.text_input(key='ct_quick_person').set_value('独立确认人').run()
            app.checkbox(key='ct_quick_checked').check().run()
            app.session_state['project_workspace_dialog']['quick_preview'] = {'old': True}
            app.selectbox(key='ct_quick_ct_alert_operator').set_value('<').run()
            self.assertFalse(app.checkbox(key='ct_quick_checked').value)
            self.assertNotIn('quick_preview', app.session_state['project_workspace_dialog'])
            self.assertEqual('本实验室已核对的依据文字', app.text_area(key='ct_quick_evidence').value)
            self.assertEqual('独立确认人', app.text_input(key='ct_quick_person').value)
            assert_clean(app)

    def test_normal_project_save_keeps_ct_and_type_change_leaves_frozen_batch_unchanged(self):
        from services.quality_review_service import runtime_review
        with IsolatedDatabase():
            values = ct_values()
            source = create_source(values)
            template = source['template_id']
            row = template_item_rows(template)[0]
            row['notes'] = '仅修改项目备注'
            save_single_test_item(template, row, item_id=row['id'],
                expected_revision=get_project_template(template)['revision_no'])
            self.assertEqual(POLICY, decode(list_template_items(template).iloc[0]['quality_review_json'])['ct_alert'])
            row = template_item_rows(template)[0]
            row.update(input_value_type='raw', unit_id=int(list_units().loc[lambda frame: frame.symbol == 'mg/L', 'id'].iloc[0]))
            replacement = save_single_test_item(template, row, item_id=row['id'],
                expected_revision=get_project_template(template)['revision_no'])
            self.assertNotIn('ct_alert', decode(item_context('project', replacement)['quality_review_json']))
            self.assertEqual(POLICY, runtime_review('lj', source['binding']['runtime_batch_id'])['ct_alert'])

    def test_only_ct_input_exposes_independent_controls_and_reopen_retains_policy(self):
        for mode in ('raw', 'log', 'ct'):
            with self.subTest(mode=mode), IsolatedDatabase():
                _, item = draft('Ct选项隔离项目', mode=mode, unit='Ct' if mode == 'ct' else 'mg/L', method='其他方法')
                prefix = f'quality_project_{item}'
                app = screen('project', item)
                set_conditions(app, prefix, context(technique='other',
                    result_scale=mode if mode in ('ct', 'log') else 'concentration'))
                controls = [widget for widget in app.checkbox if widget.key == prefix + '_ct_alert_enabled']
                self.assertEqual(mode == 'ct', bool(controls))
                if mode != 'ct':
                    continue
                self.assertFalse(controls[0].value)
                original_text = app.text_area(key=prefix + '_recorded_text').value
                controls[0].check().run()
                self.assertIsNone(app.number_input(key=prefix + '_ct_alert_threshold').value)
                self.assertTrue(app.button(key=prefix + '_adopt').disabled)
                app.selectbox(key=prefix + '_ct_alert_operator').set_value('<=')
                app.number_input(key=prefix + '_ct_alert_threshold').set_value(38.0).run()
                self.assertEqual(original_text, app.text_area(key=prefix + '_recorded_text').value)
                app.text_input(key=prefix + '_person').set_value('Ct设置确认人')
                app.checkbox(key=prefix + '_confirmed').check().run()
                app.number_input(key=prefix + '_ct_alert_threshold').set_value(38.5).run()
                self.assertFalse(app.checkbox(key=prefix + '_confirmed').value)
                app.checkbox(key=prefix + '_confirmed').check().run()
                app.button(key=prefix + '_adopt').click().run()
                assert_clean(app)
                saved = decode(item_context('project', item)['quality_review_json'])
                self.assertEqual({**POLICY, 'threshold': 38.5}, saved['ct_alert'])
                self.assertEqual([], validate_project_quality(item))
                app = screen('project', item)
                self.assertTrue(app.checkbox(key=prefix + '_ct_alert_enabled').value)
                self.assertEqual(38.5, app.number_input(key=prefix + '_ct_alert_threshold').value)
                app.checkbox(key=prefix + '_ct_alert_enabled').uncheck().run()
                self.assertFalse(any(widget.key == prefix + '_ct_alert_threshold' for widget in app.number_input))
                app.checkbox(key=prefix + '_ct_alert_enabled').check().run()
                self.assertEqual(38.5, app.number_input(key=prefix + '_ct_alert_threshold').value)

    def test_incomplete_draft_can_be_saved_and_later_completed(self):
        with IsolatedDatabase():
            _, item = draft('Ct草稿项目', mode='ct', unit='Ct', method='其他方法')
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            set_conditions(app, prefix, context(technique='other', result_scale='ct'))
            app.checkbox(key=prefix + '_ct_alert_enabled').check().run()
            app.selectbox(key=prefix + '_ct_alert_operator').set_value('>').run()
            app.button(key=prefix + '_save_pending').click().run()
            assert_clean(app)
            saved = decode(item_context('project', item)['quality_review_json'])
            self.assertEqual('pending', saved['status'])
            self.assertEqual('>', saved['ct_alert']['operator'])
            self.assertIsNone(saved['ct_alert']['threshold'])
            app = screen('project', item)
            self.assertEqual('>', app.selectbox(key=prefix + '_ct_alert_operator').value)
            self.assertIsNone(app.number_input(key=prefix + '_ct_alert_threshold').value)

    def test_quick_preview_binds_ct_requirement_and_all_methods_save_same_policy(self):
        for method, levels in [('lj', 1), ('instant', 1), ('zscore', 2)]:
            with self.subTest(method=method), IsolatedDatabase():
                values = ct_values(method, levels)
                before = database_contents()
                preview = prepare_quick_project_setup(values)
                self.assertIn(('实验室自定 Ct 线', 'Ct ≤ 38'), preview['summary'])
                self.assertEqual(before, database_contents())
                changed = deepcopy(values)
                changed['quality']['ct_alert']['threshold'] = 37.0
                with self.assertRaisesRegex(ValueError, '预览'):
                    save_quick_project_setup(changed, expected_preview_hash=preview['preview_hash'], request_key='ct-stale')
                self.assertEqual(before, database_contents())
                receipt = create_source(values)
                for scope, key in [('project', 'template_item_id'), ('lot', 'lot_config_item_id')]:
                    self.assertEqual(POLICY, decode(item_context(scope, receipt[key])['quality_review_json'])['ct_alert'])

    def test_xlsx_and_panel_import_preserve_ct_as_pending_settings(self):
        with IsolatedDatabase():
            values = ct_values()
            source = create_source(values)
            payload = build_project_template_xlsx(source['template_id'])
            workbook = xlsx_bytes_to_dataframes(payload)
            row = workbook['项目配置'].iloc[0]
            self.assertEqual('是', row['启用实验室自定Ct线'])
            self.assertEqual('≤', row['Ct要求'])
            self.assertEqual(38, row['Ct界值'])
            target = empty_target(values, 'Ct文件导入项目')
            import_project_template_xlsx(target, payload)
            imported = list_template_items(target).iloc[0]
            review = decode(imported['quality_review_json'])
            self.assertEqual(POLICY, review['ct_alert'])
            self.assertEqual('pending', review['status'])
            self.assertTrue(validate_project_quality(int(imported['id'])))
            panel_target = empty_target(values, 'Ct整组导入项目')
            candidates = preview_panel_import_rows(panel_target, payload)
            panel = preview_panel_items(panel_target, candidates)
            self.assertFalse(panel['errors'])
            save_panel_items(panel_target, panel['rows'], expected_revision=panel['expected_revision'])
            panel_review = decode(list_template_items(panel_target).iloc[0]['quality_review_json'])
            self.assertEqual(POLICY, panel_review['ct_alert'])
            self.assertEqual('pending', panel_review['status'])

            workbook['项目配置'] = workbook['项目配置'].drop(columns=CT_ALERT_COLUMNS)
            import_project_template_xlsx(target, dataframes_to_xlsx_bytes(workbook))
            self.assertEqual(POLICY, decode(list_template_items(target).iloc[0]['quality_review_json'])['ct_alert'])
            workbook = xlsx_bytes_to_dataframes(payload)
            workbook['项目配置'] = workbook['项目配置'].astype(object)
            workbook['项目配置'].loc[0, '启用实验室自定Ct线'] = '否'
            workbook['项目配置'].loc[0, ['Ct要求', 'Ct界值']] = ['', '']
            import_project_template_xlsx(target, dataframes_to_xlsx_bytes(workbook))
            self.assertNotIn('ct_alert', decode(list_template_items(target).iloc[0]['quality_review_json']))

    def test_invalid_ct_file_is_rejected_atomically(self):
        with IsolatedDatabase():
            values = ct_values()
            source = create_source(values)
            workbook = xlsx_bytes_to_dataframes(build_project_template_xlsx(source['template_id']))
            workbook['项目配置'].loc[0, '输入值类型*'] = '真实检测值'
            payload = dataframes_to_xlsx_bytes(workbook)
            _, errors = preview_project_template_xlsx(payload)
            self.assertTrue(any('Ct输入' in error for error in errors))
            before = database_contents()
            with self.assertRaisesRegex(ValueError, '校验未通过'):
                import_project_template_xlsx(source['template_id'], payload)
            self.assertEqual(before, database_contents())

    def test_incomplete_ct_draft_round_trips_as_pending_and_bad_input_still_rejects(self):
        from services.quality_review_service import save_pending_review
        with IsolatedDatabase():
            values = ct_values()
            source = create_source(values)
            incomplete = {**POLICY, 'threshold': None}
            save_pending_review('project', source['template_item_id'],
                context=values['quality']['context'], ct_alert=incomplete)
            payload = build_project_template_xlsx(source['template_id'])
            preview, errors = preview_project_template_xlsx(payload)
            self.assertFalse(errors)
            target = empty_target(values, 'Ct未填完草稿导入')
            import_project_template_xlsx(target, payload)
            row = list_template_items(target).iloc[0]
            review = decode(row['quality_review_json'])
            self.assertEqual('pending', review['status'])
            self.assertEqual(incomplete, review['ct_alert'])
            self.assertTrue(validate_project_quality(int(row['id'])))
            for field, value in [('Ct要求', '错误比较符'), ('Ct界值', 'inf')]:
                workbook = xlsx_bytes_to_dataframes(payload)
                workbook['项目配置'] = workbook['项目配置'].astype(object)
                workbook['项目配置'].loc[0, field] = value
                broken = dataframes_to_xlsx_bytes(workbook)
                _, errors = preview_project_template_xlsx(broken)
                self.assertTrue(errors)
                before = database_contents()
                with self.assertRaisesRegex(ValueError, '校验未通过'):
                    import_project_template_xlsx(target, broken)
                self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
