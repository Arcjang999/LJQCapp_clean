"""Atomic shortcut setup, real quality gates, stale previews and request receipts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import get_batch, get_connection, get_instant_batch, read_snapshot
from services.master_data_service import (
    create_manufacturer, create_instrument_model, create_lab_instrument, create_reagent,
    create_qc_material, create_qc_lot, create_qc_level, create_method, create_test_item, list_units,
)
from services.qc_product_recommendation_service import validate_qc_product_choice, save_qc_product_relation
from services.quick_project_setup_service import (
    get_quick_setup_context, prepare_quick_project_setup, save_quick_project_setup,
)
from services.quality_target_service import get_requirement
from services.quality_review_service import validate_quality_review
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quality_review_fixtures import fixture_conditions


METHODS = [('lj', 1), ('zscore', 2), ('zscore', 3), ('instant', 1)]


def metadata_values(method='lj', levels=1, *, numeric=False):
    """Only dictionaries and physical materials exist before the shortcut."""
    maker = create_manufacturer(categories=['instrument', 'reagent', 'qc_material'], display_name='快捷配置厂商')
    model = create_instrument_model(manufacturer_id=maker, generic_name='分析仪', model='快捷配置型号')
    instrument = create_lab_instrument(instrument_model_id=model, display_name='快捷配置仪器')
    reagent = create_reagent(manufacturer_id=maker, generic_name='快捷配置试剂')
    material = create_qc_material(manufacturer_id=maker, generic_name='快捷配置质控品', nominal_level_count=levels)
    lot = create_qc_lot(qc_material_id=material, lot_no='QUICK-QC-01', expiry_date='2028-12-31')
    physical_levels = [create_qc_level(qc_material_lot_id=lot, level_name=f'水平{index}', level_order=index)
                       for index in range(1, levels + 1)]
    unit = int(list_units().loc[lambda frame: frame.symbol == 'mg/L', 'id'].iloc[0])
    principle = create_method(method_name='免疫比浊法（快捷回归）' if numeric else '快捷配置检测法')
    spec = get_requirement('wst403-2024-047') if numeric else None
    item = create_test_item(chinese_name=spec['name'] if spec else '快捷配置合成项目', default_unit_id=unit)
    values = dict(template_name=f'快捷完整项目-{method}-{levels}', lab_instrument_id=instrument,
        qc_material_id=material, reagent_id=reagent, method_id=principle, test_item_id=item,
        input_value_type='raw', unit_id=unit, qc_method=method, level_count=levels,
        target_n=20, cv_limit=None, quality_target_source_text='', level_ids=physical_levels,
        config_name='快捷实际批次', notes='核对单位基础资料', item_notes='核对方法及实际材料')
    values['product_choice_review'] = validate_qc_product_choice(qc_material_id=material,
        reagent_id=reagent, lab_instrument_id=instrument, test_item_id=item, method_id=principle,
        manual_review={'confirmed_by': '物料核对人', 'evidence': '已核对本夹具仪器、试剂、项目、方法及质控品适用性。'})
    context = get_quick_setup_context(values)
    conditions = fixture_conditions(context['item'], clinical=numeric)
    quality = dict(checked=True, confirmed_by='质量核对人', evidence='已核对项目、单位、结果尺度及全部实际浓度水平。',
                   process_requirements={'source_ids': [], 'requirement_text': ''}, **conditions)
    if numeric:
        quality.update(mode='source', requirement_id=spec['id'],
            levels=[dict(level_order=index, concentration=100.0 * index, category='') for index in range(1, levels + 1)])
    else:
        quality.update(mode='recorded', source_name='隔离测试实验室SOP', source_version='QUICK-TEST-2026-01',
                       requirement_text='本合成项目按测试方案核对建靶及判读，仅作软件回归验证。')
    values['quality'] = quality
    return values


def database_contents():
    """Compare all tables, including frozen settings, quality and receipt rows."""
    with read_snapshot() as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: (tuple(tuple(row) for row in connection.execute('PRAGMA table_info(' + table + ')')),
                       tuple(sorted((tuple(row) for row in connection.execute('SELECT * FROM ' + table)), key=repr)))
                for table in tables}


def save(values, preview, request='quick-request-1'):
    return save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key=request)


def raise_after(original, only_scope=None):
    def fail(*args, **kwargs):
        result = original(*args, **kwargs)
        if only_scope is None or args[0] == only_scope:
            raise sqlite3.OperationalError('Injected shortcut stage failure')
        return result
    return fail


class QuickProjectSetupTests(unittest.TestCase):
    def assert_no_change_on_rejection(self, action):
        before = database_contents()
        with self.assertRaises(ValueError):
            action()
        self.assertEqual(before, database_contents())

    def assert_complete(self, values, receipt):
        with get_connection() as connection:
            template = dict(connection.execute('SELECT * FROM qc_project_templates WHERE id=?', (receipt['template_id'],)).fetchone())
            item = dict(connection.execute('SELECT * FROM qc_project_template_items WHERE id=?', (receipt['template_item_id'],)).fetchone())
            config = dict(connection.execute('SELECT * FROM qc_lot_configs WHERE id=?', (receipt['lot_config_id'],)).fetchone())
            config_item = dict(connection.execute('SELECT * FROM qc_lot_config_items WHERE id=?', (receipt['lot_config_item_id'],)).fetchone())
            assignments = [dict(row) for row in connection.execute('SELECT * FROM qc_lot_config_item_levels WHERE lot_config_item_id=? ORDER BY level_order',
                                                                  (receipt['lot_config_item_id'],))]
            binding = dict(connection.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?', (receipt['lot_config_item_id'],)).fetchone())
            self.assertEqual('active', template['status'])
            self.assertEqual('active', config['status'])
            self.assertEqual(1, config['material_selection_mode'])
            self.assertEqual(values['template_name'], template['template_name'])
            self.assertEqual(values['test_item_id'], item['test_item_id'])
            self.assertEqual(values['qc_method'], item['qc_method'])
            self.assertEqual(values['qc_method'], config_item['qc_method'])
            self.assertEqual(values['level_count'], config_item['level_count'])
            self.assertEqual(values['level_ids'], [row['qc_level_id'] for row in assignments])
            self.assertTrue(all(row['target_source'] == 'building' for row in assignments))
            self.assertEqual('active', binding['binding_status'])
            self.assertEqual(values['qc_method'], binding['qc_method'])
            self.assertEqual(binding, receipt['binding'])
            self.assertEqual([], validate_quality_review('project', item['id'], connection))
            self.assertEqual([], validate_quality_review('lot', config_item['id'], connection))
            self.assertEqual('confirmed', json.loads(item['quality_review_json'])['status'])
            self.assertEqual('confirmed', json.loads(config_item['quality_review_json'])['status'])
            runtime = (get_instant_batch if values['qc_method'] == 'instant' else get_batch)(binding['runtime_batch_id'])
            self.assertEqual(20, runtime['target_n'])
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_quick_setup_receipts').fetchone()[0])
            self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_four_methods_prepare_without_writes_then_create_one_complete_setup(self):
        for method, levels in METHODS:
            with self.subTest(method=method, levels=levels), IsolatedDatabase():
                values = metadata_values(method, levels)
                before = database_contents()
                preview = prepare_quick_project_setup(values)
                self.assertEqual(before, database_contents())
                self.assertEqual(levels, len(preview['material_labels']))
                self.assertTrue(all('QUICK-QC-01' in label for label in preview['material_labels']))
                self.assertTrue(preview['source_fingerprint'])
                receipt = save(values, preview)
                self.assert_complete(values, receipt)
                after = database_contents()
                self.assertEqual(receipt, save(values, preview))
                self.assertEqual(after, database_contents())
                changed = deepcopy(values)
                changed['notes'] = '相同请求号下变更内容'
                self.assert_no_change_on_rejection(lambda: save(changed, preview))
                self.assert_no_change_on_rejection(lambda: save(values, preview, request='different-request'))

    def test_real_numeric_standard_is_adopted_for_project_and_all_levels(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3, numeric=True)
            before = database_contents()
            preview = prepare_quick_project_setup(values)
            self.assertEqual(before, database_contents())
            receipt = save(values, preview)
            self.assert_complete(values, receipt)
            with get_connection() as connection:
                project = connection.execute('SELECT quality_goal_json FROM qc_project_template_items WHERE id=?', (receipt['template_item_id'],)).fetchone()
                lot = connection.execute('SELECT quality_goal_json FROM qc_lot_config_items WHERE id=?', (receipt['lot_config_item_id'],)).fetchone()
            for payload in (project[0], lot[0]):
                self.assertEqual('wst403-2024-047', json.loads(payload)['spec']['id'])
            self.assertEqual(3, len(json.loads(lot[0])['levels']))
            for level in json.loads(lot[0])['levels']:
                self.assertEqual(7.5, level['rule']['value'])

    def test_incomplete_materials_invalid_metadata_unconfirmed_quality_and_wrong_scope_reject(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3)
            valid_preview = prepare_quick_project_setup(values)
            variants = []
            missing = deepcopy(values)
            missing['level_ids'] = missing['level_ids'][:2]
            variants.append(missing)
            repeated = deepcopy(values)
            repeated['level_ids'] = [values['level_ids'][0]] * 3
            variants.append(repeated)
            for field in ('lab_instrument_id', 'qc_material_id', 'test_item_id', 'unit_id', 'method_id', 'reagent_id'):
                invalid = deepcopy(values)
                invalid[field] = -1
                variants.append(invalid)
            unconfirmed = deepcopy(values)
            unconfirmed['quality']['checked'] = False
            variants.append(unconfirmed)
            missing_source = deepcopy(values)
            missing_source['quality']['source_version'] = ''
            variants.append(missing_source)
            wrong_scope = deepcopy(values)
            wrong_scope['product_choice_review']['context']['test_item_id'] = -1
            variants.append(wrong_scope)
            missing_choice = deepcopy(values)
            missing_choice.pop('product_choice_review')
            variants.append(missing_choice)
            for index, invalid in enumerate(variants):
                with self.subTest(invalid=index):
                    self.assert_no_change_on_rejection(lambda: prepare_quick_project_setup(invalid))
                    self.assert_no_change_on_rejection(lambda: save(invalid, valid_preview, request=f'invalid-{index}'))

    def test_numeric_standard_cannot_be_replaced_by_recorded_requirement_or_wrong_source(self):
        with IsolatedDatabase():
            values = metadata_values('lj', 1, numeric=True)
            replaced = deepcopy(values)
            replaced['quality'].update(mode='recorded', source_name='实验室SOP', source_version='TEST-1', requirement_text='不能替代适用标准。')
            self.assert_no_change_on_rejection(lambda: prepare_quick_project_setup(replaced))
            wrong = deepcopy(values)
            wrong['quality']['requirement_id'] = 'wst403-2024-001'
            self.assert_no_change_on_rejection(lambda: prepare_quick_project_setup(wrong))
            missing_level = deepcopy(values)
            missing_level['quality']['levels'] = []
            self.assert_no_change_on_rejection(lambda: prepare_quick_project_setup(missing_level))

    def test_changed_relationship_and_physical_material_reject_stale_preview(self):
        for change in ('relation', 'lot', 'level', 'instrument'):
            with self.subTest(change=change), IsolatedDatabase():
                values = metadata_values()
                preview = prepare_quick_project_setup(values)
                if change == 'relation':
                    save_qc_product_relation({key: values[key] for key in ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id', 'qc_material_id')} |
                        dict(scope_note='本仪器和检测项目', evidence='重新核对适用关系', confirmed_by='关系核对人'))
                else:
                    with get_connection() as connection:
                        if change == 'lot':
                            connection.execute("UPDATE md_qc_material_lots SET expiry_date='2029-12-31' WHERE id=(SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?)", (values['level_ids'][0],))
                        elif change == 'level':
                            connection.execute("UPDATE md_qc_levels SET level_name='变更后的浓度名称' WHERE id=?", (values['level_ids'][0],))
                        else:
                            connection.execute('UPDATE lab_instruments SET is_disabled=1 WHERE id=?', (values['lab_instrument_id'],))
                self.assert_no_change_on_rejection(lambda: save(values, preview))

    def test_every_save_stage_failure_rolls_back_the_whole_setup_and_receipt(self):
        stages = [
            ('services.project_workspace_service', 'save_project_details', None),
            ('services.project_workspace_service', 'save_single_test_item', None),
            ('services.quick_project_setup_service', '_confirm_quality', 'project'),
            ('services.project_config_service', 'activate_project_template', None),
            ('services.material_workflow_service', 'create_material_config', None),
            ('services.quick_project_setup_service', '_confirm_quality', 'lot'),
            ('services.project_config_service', 'activate_lot_config', None),
            ('services.project_workspace_service', 'resolve_batch_binding', None),
        ]
        for module_name, function_name, scope in stages:
            with self.subTest(stage=function_name, scope=scope), IsolatedDatabase():
                values = metadata_values('zscore', 3)
                preview = prepare_quick_project_setup(values)
                before = database_contents()
                module = __import__(module_name, fromlist=[function_name])
                original = getattr(module, function_name)
                with patch(module_name + '.' + function_name, side_effect=raise_after(original, scope)):
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'Injected'):
                        save(values, preview)
                self.assertEqual(before, database_contents())
                receipt = save(values, preview)
                self.assert_complete(values, receipt)
        with IsolatedDatabase():
            values = metadata_values('instant', 1)
            preview = prepare_quick_project_setup(values)
            with get_connection() as connection:
                connection.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON qc_quick_setup_receipts BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END")
            before = database_contents()
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
                save(values, preview)
            self.assertEqual(before, database_contents())
            with get_connection() as connection:
                connection.execute('DROP TRIGGER fail_receipt')
            self.assert_complete(values, save(values, preview))


if __name__ == '__main__':
    unittest.main()
