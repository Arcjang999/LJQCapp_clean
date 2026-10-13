"""The short quality form keeps real applicability, drafts and frozen-lot gates."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from datetime import date
import csv
import io
import json
import sys
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import get_connection
from services.quality_target_service import decode, item_context, get_requirement, IMPORT_COLUMNS, import_custom_csv, list_catalog
from services.quality_review_service import validate_project_quality
from services.project_config_service import list_lot_item_levels, save_lot_item_levels, copy_lot_config, list_lot_config_items
from services.master_data_service import create_qc_lot, create_qc_level
from tests.quality_applicability_smoke_test import IsolatedDatabase, draft, context
from tests.quality_review_fixtures import fixture_conditions, confirm_fixture_product_choice
from tests.quality_targets_smoke_test import new_lot, activate
from tests.quick_project_setup_smoke_test import metadata_values, database_contents
from tests.quick_project_setup_ui_smoke_test import open_details, quality_prefix
from tests.batch_dialogs_smoke_test import seed_draft, run_app, assert_clean


def screen(scope, item):
    return AppTest.from_string(
        f"from ui.quality_targets import render_adoption\nrender_adoption('{scope}', {item})",
        default_timeout=30).run()


def set_conditions(app, prefix, values):
    for field, value in values.items():
        if field == 'specimen':
            app.text_input(key=prefix+'_context_specimen').set_value(value)
        else:
            widget = app.selectbox(key=prefix+'_context_'+field)
            if widget.disabled:
                assert widget.value == value
            else:
                widget.set_value(value)
    app.run()
    assert_clean(app)


def confirm(app, prefix):
    app.text_input(key=prefix+'_person').set_value('隔离界面确认人')
    app.checkbox(key=prefix+'_confirmed').check().run()
    app.button(key=prefix+'_adopt').click().run()
    assert_clean(app)


class QualitySimpleUiTests(unittest.TestCase):
    def test_unique_uniform_standard_is_automatic_and_needs_no_level_concentration(self):
        with IsolatedDatabase():
            _, item = draft()
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            set_conditions(app, prefix, context())
            self.assertTrue(any('引用现行数值标准' in row.value for row in app.markdown))
            self.assertFalse(any(row.key == prefix+'_requirement' for row in app.selectbox))
            self.assertFalse(any(row.key == prefix+'_mode' for row in app.radio))
            self.assertFalse(any((row.key or '').endswith('_concentration') for row in app.number_input))
            self.assertEqual('wst403-2024-047', app.session_state[prefix+'_requirement'])
            self.assertEqual({}, decode(item_context('project', item)['quality_review_json']))
            app.number_input(key=prefix+'_supplemental_cv').set_value(3.0)
            confirm(app, prefix)
            self.assertFalse(app.error)
            self.assertEqual([], validate_project_quality(item))
            goal = decode(item_context('project', item)['quality_goal_json'])
            self.assertEqual('wst403-2024-047', goal['spec']['id'])
            self.assertEqual(7.5, goal['spec']['imprecision'][0]['value'])
            self.assertEqual(3.0, goal['supplement']['cv'])
            app = screen('project', item)
            app.number_input(key=prefix+'_supplemental_cv').set_value(None).run()
            self.assertIsNone(app.number_input(key=prefix+'_supplemental_cv').value)
            confirm(app, prefix)
            self.assertFalse(app.error)
            goal = decode(item_context('project', item)['quality_goal_json'])
            self.assertNotIn('supplement', goal)
            self.assertEqual(7.5, item_context('project', item)['cv_limit'])

    def test_required_concentration_and_category_are_still_validated_and_frozen(self):
        cases = [('wst403-2024-082', 'mmol/L', '便携式血糖仪法', 5.5, ''),
                 ('wst406-2024-wbc', '10^9/L', '血细胞分析', None, '低浓度'),
                 ('wst406-2024-fib', 'g/L', '凝血初筛法', 7.0, '异常')]
        for identifier, unit, method, concentration, category in cases:
            with self.subTest(source=identifier), IsolatedDatabase():
                item, config, _ = new_lot()
                spec = get_requirement(identifier)
                with get_connection() as connection:
                    original = item_context('lot', item, connection)
                    uid = connection.execute('SELECT id FROM md_units WHERE symbol=?', (unit,)).fetchone()[0]
                    existing = connection.execute('SELECT id FROM md_test_items WHERE chinese_name=? AND id!=? AND is_disabled=0',
                                                  (spec['name'], original['test_item_id'])).fetchone()
                    if existing:
                        connection.execute('UPDATE qc_lot_config_items SET test_item_id=? WHERE id=?', (existing[0], item))
                    else:
                        connection.execute('UPDATE md_test_items SET chinese_name=?,abbreviation=? WHERE id=?',
                                           (spec['name'], '', original['test_item_id']))
                    connection.execute("UPDATE md_methods SET method_name=?,method_code='',principle='' WHERE id=?",
                                       (method, original['method_id']))
                    connection.execute("UPDATE qc_lot_config_items SET unit_id=?,quality_goal_json='{}',quality_review_json='{}' WHERE id=?",
                                       (uid, item))
                confirm_fixture_product_choice('lot', item)
                prefix = f'quality_lot_{item}'
                app = screen('lot', item)
                conditions = fixture_conditions(item_context('lot', item), clinical=True)['context']
                set_conditions(app, prefix, conditions)
                confirm(app, prefix)
                self.assertTrue(app.error)
                self.assertEqual({}, decode(item_context('lot', item)['quality_goal_json']))
                if category:
                    app.selectbox(key=f'{prefix}_{identifier}_1_category').set_value(category).run()
                if concentration is not None:
                    app.number_input(key=f'{prefix}_{identifier}_1_concentration').set_value(concentration)
                confirm(app, prefix)
                self.assertFalse(app.error)
                goal = decode(item_context('lot', item)['quality_goal_json'])
                self.assertEqual(concentration, goal['levels'][0]['concentration'])
                self.assertEqual(category, goal['levels'][0]['category'])
                activate(item, config, 'lj')
                app = screen('lot', item)
                self.assertFalse(any(row.key == prefix+'_adopt' for row in app.button))
                self.assertFalse(any(row.key == prefix+'_person' for row in app.text_input))

    def test_missing_or_mismatched_conditions_cannot_claim_sd_fallback(self):
        with IsolatedDatabase():
            _, item = draft('TSH', unit='mg/L')
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            set_conditions(app, prefix, context())
            self.assertTrue(any('请先完成检测条件' in row.value for row in app.markdown))
            self.assertFalse(any(row.value == '**SD控制＋实验室CV**' for row in app.markdown))
            app.checkbox(key=prefix+'_confirmed').check().run()
            self.assertTrue(app.button(key=prefix+'_adopt').disabled)
            app.button(key=prefix+'_save_pending').click().run()
            assert_clean(app)
            self.assertEqual('pending', decode(item_context('project', item)['quality_review_json'])['status'])
            self.assertTrue(validate_project_quality(item))

    def test_sd_cv_pending_reopen_confirm_and_removing_cv_keep_structured_intent(self):
        with IsolatedDatabase():
            _, item = draft('未收录定量项目')
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            set_conditions(app, prefix, context())
            self.assertEqual('sd_cv', app.radio(key=prefix+'_mode').value)
            self.assertIsNone(app.number_input(key=prefix+'_cv_limit').value)
            self.assertFalse(app.checkbox(key=prefix+'_advanced_search').value)
            app.number_input(key=prefix+'_cv_limit').set_value(4.5)
            app.text_area(key=prefix+'_recorded_text').set_value('采用本批次SD规则；异常按本实验室流程处理。')
            app.button(key=prefix+'_save_pending').click().run()
            assert_clean(app)
            saved = decode(item_context('project', item)['quality_review_json'])
            self.assertEqual(4.5, saved['draft_recorded']['cv_limit'])
            self.assertEqual('sd_cv', saved['draft_recorded']['mode'])
            self.assertEqual('local_catalog', saved['search_record']['scope'])
            app = screen('project', item)
            self.assertEqual(4.5, app.number_input(key=prefix+'_cv_limit').value)
            self.assertEqual('采用本批次SD规则；异常按本实验室流程处理。', app.text_area(key=prefix+'_recorded_text').value)
            confirm(app, prefix)
            self.assertFalse(app.error)
            current = item_context('project', item)
            self.assertEqual(4.5, current['cv_limit'])
            self.assertEqual('custom', decode(current['quality_goal_json'])['spec']['origin'])
            app = screen('project', item)
            self.assertEqual('sd_cv', app.radio(key=prefix+'_mode').value)
            self.assertEqual(4.5, app.number_input(key=prefix+'_cv_limit').value)
            app.number_input(key=prefix+'_cv_limit').set_value(None).run()
            app.button(key=prefix+'_save_pending').click().run()
            assert_clean(app)
            pending = decode(item_context('project', item)['quality_review_json'])
            self.assertIsNone(pending['draft_recorded']['cv_limit'])
            self.assertEqual(4.5, item_context('project', item)['cv_limit'])
            app = screen('project', item)
            self.assertIsNone(app.number_input(key=prefix+'_cv_limit').value)
            confirm(app, prefix)
            self.assertFalse(app.error)
            current = item_context('project', item)
            self.assertIsNone(current['cv_limit'])
            self.assertEqual({}, decode(current['quality_goal_json']))
            saved = decode(current['quality_review_json'])
            self.assertIn('SD', saved['recorded']['requirement_text'])
            self.assertNotIn('CV上限为', saved['recorded']['requirement_text'])
            self.assertNotIn('official_url', saved['search_record'])
            self.assertEqual([], validate_project_quality(item))

    def test_ct_log_signal_have_no_concentration_cv_and_keep_mandatory_requirements(self):
        cases = [('HBV DNA', 'ct', 'Ct', '实时荧光 PCR', context(technique='realtime_pcr', result_scale='ct')),
                 ('HBV DNA', 'log', 'IU/mL', '实时荧光 PCR', context(technique='realtime_pcr', result_scale='log')),
                 ('HBsAg', 'raw', 'S/CO', '化学发光免疫法', context(result_kind='qualitative', result_scale='s_co'))]
        for name, mode, unit, method, conditions in cases:
            with self.subTest(scale=conditions['result_scale']), IsolatedDatabase():
                _, item = draft(name, mode=mode, unit=unit, method=method)
                prefix = f'quality_project_{item}'
                app = screen('project', item)
                set_conditions(app, prefix, conditions)
                self.assertFalse(any(row.key == prefix+'_cv_limit' for row in app.number_input))
                if conditions['result_scale'] == 's_co':
                    selected = app.multiselect(key=prefix+'_mandatory_sources')
                    self.assertEqual({'wst494-2017-qualitative', 'wst494-2017-signal'}, set(selected.value))
                    selected.set_value([]).run()
                    confirm(app, prefix)
                    self.assertTrue(app.error)
                    app.multiselect(key=prefix+'_mandatory_sources').set_value(['wst494-2017-qualitative', 'wst494-2017-signal']).run()
                confirm(app, prefix)
                self.assertFalse(app.error)
                self.assertIsNone(item_context('project', item)['cv_limit'])
                self.assertEqual([], validate_project_quality(item))

    def test_batch_cv_cancel_continue_and_stale_owner_never_save(self):
        with IsolatedDatabase():
            fixture = seed_draft()
            item = fixture['items']['lj']
            prefix = f'quality_lot_{item}'
            app = run_app(fixture)
            app.button(key=f'open_quality_{item}').click().run()
            app.radio(key=prefix+'_mode').set_value('sd_cv').run()
            before = database_contents()
            app.number_input(key=prefix+'_cv_limit').set_value(3.25)
            app.text_area(key=prefix+'_recorded_text').set_value('SD规则及本实验室书面要求')
            app.button(key='batch_quality_close').click().run()
            app.button(key='batch_quality_continue').click().run()
            assert_clean(app)
            self.assertEqual(3.25, app.number_input(key=prefix+'_cv_limit').value)
            self.assertEqual('SD规则及本实验室书面要求', app.text_area(key=prefix+'_recorded_text').value)
            self.assertEqual(before, database_contents())
            other = fixture['items']['instant']
            save_lot_item_levels(other, list_lot_item_levels(other).to_dict('records'))
            after_external = database_contents()
            app.run()
            assert_clean(app)
            self.assertTrue(app.error)
            self.assertFalse(any(row.key == prefix+'_adopt' for row in app.button))
            app.button(key='batch_quality_close').click().run()
            app.button(key='batch_quality_discard').click().run()
            self.assertEqual(after_external, database_contents())

    def test_advanced_lab_text_uses_honest_local_review_without_forcing_official_search(self):
        with IsolatedDatabase():
            _, item = draft('未收录实验室文字要求项目')
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            set_conditions(app, prefix, context())
            app.radio(key=prefix+'_mode').set_value('记录实验室要求').run()
            self.assertFalse(app.checkbox(key=prefix+'_advanced_search').value)
            app.text_input(key=prefix+'_source_name').set_value('本实验室核对文件')
            app.text_input(key=prefix+'_source_version').set_value('QC-TEST-2026')
            app.text_area(key=prefix+'_recorded_text').set_value('依据本批次确认的均值和标准差；每次检测按确认流程登记。')
            app.text_area(key=prefix+'_evidence').set_value('已核对本隔离项目的方法、标本、原始结果尺度及实际单位。')
            confirm(app, prefix)
            self.assertFalse(app.error)
            review = decode(item_context('project', item)['quality_review_json'])
            self.assertEqual('confirmed', review['status'])
            self.assertEqual('本实验室核对文件', review['recorded']['source_name'])
            self.assertEqual('local_catalog', review['search_record']['scope'])
            self.assertNotIn('official_url', review['search_record'])
            self.assertEqual([], validate_project_quality(item))

    def test_existing_custom_catalog_goal_reopens_and_copies_without_defaulting_to_sd(self):
        with IsolatedDatabase():
            fixture = seed_draft()
            item = fixture['items']['lj']
            current = item_context('lot', item)
            values = dict.fromkeys(IMPORT_COLUMNS, '')
            values.update({'检验项目': current['test_item_name'], '来源名称': '本实验室已登记CV要求',
                '版本': 'UI-QC-2026', '实施日期': date.today().isoformat(), '单位': current['unit_symbol'],
                '允许不精密度类型': 'CV', '上限': '3.5', '比较符': '<=',
                '来源链接或依据': '本隔离夹具已核对并批准的书面要求', '确认人': '目录核对人'})
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=IMPORT_COLUMNS)
            writer.writeheader()
            writer.writerow(values)
            self.assertEqual(1, import_custom_csv(output.getvalue().encode('utf-8')))
            identifier = next(row['id'] for row in list_catalog() if row['standard'] == values['来源名称'])
            prefix = f'quality_lot_{item}'
            app = screen('lot', item)
            app.radio(key=prefix+'_mode').set_value('选择标准或自定义目录').run()
            app.selectbox(key=prefix+'_requirement').set_value(identifier).run()
            confirm(app, prefix)
            self.assertFalse(app.error)
            app = screen('lot', item)
            self.assertEqual('选择标准或自定义目录', app.radio(key=prefix+'_mode').value)
            self.assertEqual(identifier, app.selectbox(key=prefix+'_requirement').value)
            self.assertEqual(3.5, item_context('lot', item)['cv_limit'])
            with get_connection() as connection:
                product = connection.execute('SELECT qc_material_id FROM qc_lot_configs WHERE id=?',
                                             (fixture['config_id'],)).fetchone()[0]
            lot = create_qc_lot(qc_material_id=product, lot_no='CUSTOM-CV-COPY', expiry_date='2028-12-31')
            for order in range(1, 4):
                create_qc_level(qc_material_lot_id=lot, level_name=f'复制水平{order}', level_order=order)
            copied = copy_lot_config(source_lot_config_id=fixture['config_id'], target_qc_material_lot_id=lot)
            copied_item = int(list_lot_config_items(copied).loc[lambda frame: frame.qc_method == 'lj', 'id'].iloc[0])
            confirm_fixture_product_choice('lot', copied_item)
            copied_goal = decode(item_context('lot', copied_item)['quality_goal_json'])
            self.assertEqual(identifier, copied_goal['spec']['id'])
            self.assertTrue(copied_goal['pending'])
            app = screen('lot', copied_item)
            prefix = f'quality_lot_{copied_item}'
            self.assertEqual('选择标准或自定义目录', app.radio(key=prefix+'_mode').value)
            self.assertEqual(identifier, app.selectbox(key=prefix+'_requirement').value)
            confirm(app, prefix)
            self.assertFalse(app.error)
            self.assertEqual(identifier, decode(item_context('lot', copied_item)['quality_goal_json'])['spec']['id'])
            self.assertEqual(3.5, item_context('lot', copied_item)['cv_limit'])

    def test_simple_basis_name_version_and_text_survive_real_save_reopen_and_cv_changes(self):
        for cv in (None, 4.25):
            with self.subTest(cv=cv), IsolatedDatabase():
                _, item = draft('简单方案高级依据保存项目')
                prefix = f'quality_project_{item}'
                app = screen('project', item)
                set_conditions(app, prefix, context())
                basis = dict(source_name='本实验室经核对的质控程序', source_version='LAB-QC-2026-R2',
                             requirement_text='按本批次均值和SD判读；异常复核后登记本实验室处置记录。')
                app.text_input(key=prefix+'_source_name').set_value(basis['source_name'])
                app.text_input(key=prefix+'_source_version').set_value(basis['source_version'])
                app.text_area(key=prefix+'_recorded_text').set_value(basis['requirement_text'])
                app.number_input(key=prefix+'_cv_limit').set_value(cv)
                confirm(app, prefix)
                self.assertFalse(app.error)
                current = item_context('project', item)
                if cv is None:
                    saved = decode(current['quality_review_json'])['recorded']
                    self.assertEqual('sd_cv', saved['setup_mode'])
                else:
                    spec = decode(current['quality_goal_json'])['spec']
                    self.assertEqual('sd_cv', spec['setup_mode'])
                    saved = spec['laboratory_basis']
                self.assertEqual(basis['source_name'], saved['source_name'])
                self.assertEqual(basis['source_version'], saved['source_version'])
                self.assertIn(basis['requirement_text'], saved['requirement_text'])
                app = screen('project', item)
                self.assertEqual('sd_cv', app.radio(key=prefix+'_mode').value)
                self.assertEqual(cv, app.number_input(key=prefix+'_cv_limit').value)
                self.assertEqual(basis['source_name'], app.text_input(key=prefix+'_source_name').value)
                self.assertEqual(basis['source_version'], app.text_input(key=prefix+'_source_version').value)
                self.assertEqual(basis['requirement_text'], app.text_area(key=prefix+'_recorded_text').value)
                app.number_input(key=prefix+'_cv_limit').set_value(3.0)
                confirm(app, prefix)
                self.assertFalse(app.error)
                spec = decode(item_context('project', item)['quality_goal_json'])['spec']
                self.assertEqual(basis['source_name'], spec['laboratory_basis']['source_name'])
                self.assertEqual(basis['source_version'], spec['laboratory_basis']['source_version'])
                self.assertEqual(1, spec['laboratory_basis']['requirement_text'].count('实验室自定义CV上限为'))
                self.assertIn('≤3%', spec['laboratory_basis']['requirement_text'])

    def test_quick_sd_cv_all_methods_preview_zero_writes_and_back_cancel_preserve_cv(self):
        for method, count in [('lj', 1), ('zscore', 2), ('zscore', 3), ('instant', 1)]:
            with self.subTest(method=method, levels=count), IsolatedDatabase():
                values = metadata_values(method, count)
                app = open_details(values)
                before = database_contents()
                app.button(key='quick_details_next').click().run()
                prefix = quality_prefix(app)
                self.assertTrue(app.button(key='quick_quality_next').disabled)
                app.checkbox(key=prefix+'_checked').check().run()
                self.assertTrue(app.button(key='quick_quality_next').disabled)
                set_conditions(app, prefix, values['quality']['context'])
                self.assertEqual('sd_cv', app.radio(key=prefix+'_mode').value)
                app.number_input(key=prefix+'_cv_limit').set_value(4.0)
                app.text_input(key=prefix+'_person').set_value('快捷质量核对人')
                app.checkbox(key=prefix+'_checked').check().run()
                app.button(key='project_dialog_cancel').click().run()
                app.button(key='project_continue_edit').click().run()
                assert_clean(app)
                self.assertEqual(4.0, app.number_input(key=prefix+'_cv_limit').value)
                app.button(key='quick_quality_next').click().run()
                assert_clean(app)
                self.assertEqual('preview', app.session_state['project_workspace_dialog']['quick_step'])
                self.assertEqual(before, database_contents())
                app.button(key='quick_preview_back').click().run()
                self.assertEqual(4.0, app.number_input(key=prefix+'_cv_limit').value)
                app.button(key='quick_quality_next').click().run()
                frozen = deepcopy(app.session_state['project_workspace_dialog']['draft']['quality'])
                app.button(key='quick_save').click().run()
                assert_clean(app)
                self.assertFalse(app.error)
                self.assertNotIn('project_workspace_dialog', app.session_state.filtered_state)
                with get_connection() as connection:
                    self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])
                    item = dict(connection.execute('SELECT * FROM qc_lot_config_items').fetchone())
                    self.assertEqual(method, item['qc_method'])
                    self.assertEqual(count, item['level_count'])
                    self.assertEqual(4.0, item['cv_limit'])
                    self.assertEqual('local_catalog', json.loads(item['quality_review_json'])['search_record']['scope'])
                self.assertEqual('sd_cv', frozen['mode'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
