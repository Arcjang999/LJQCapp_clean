"""Unit preparation uses only confirmed file contents and existing registration APIs."""
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import database as db
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.lab_preparation_service import (
    PREPARATION_SHEETS, UNIT_FIELDS, build_lab_preparation_template,
    build_lab_preparation_reference_xlsx, get_lab_preparation_receipt,
    prepare_lab_preparation, save_lab_preparation,
)
from services.settings_service import get_report_settings, save_report_settings_form
from services.master_data_service import create_manufacturer
from migrations.lab_preparation import ensure_lab_preparation_schema
from tests.settings_smoke_test import TemporaryDatabaseContext


def dump():
    with db.read_snapshot() as connection:
        return '\n'.join(connection.iterdump())


def tables():
    sheets = xlsx_bytes_to_dataframes(build_lab_preparation_template())
    sheets['单位信息']['填写内容'] = ['准备实验室', '准备科室', '准备负责人', '准备审核人']
    contents = {
        '计量单位': [['U1', '', 'prep-unit', '准备计量单位', '']],
        '方法学': [['M1', '', '准备方法学', 'PREP-METHOD']],
        '检验项目': [['T1', '', '准备检验项目', 'PREP-ITEM', 'PREP', 'U1']],
        '仪器': [['I1', '', '准备仪器1', '准备厂家', 'MODEL-1', '分析仪', '准备科室', 'ASSET-1', 'SERIAL-1', '准备位置']],
        '试剂产品': [['R1', '', '准备厂家', '准备试剂', '准备商品名', 'R-001']],
        '试剂批号': [['RL1', 'R1', '000123', '2028-12-31', '包装资料']],
        '质控品产品': [['Q1', '', '准备厂家', '准备质控品', '', 'QC-001', '2']],
        '质控材料': [['C1', 'Q1', '000456', '2028-12-31', '低值', 'L1', '低值说明', ''],
                    ['C2', 'Q1', '000456', '2028-12-31', '高值', 'L2', '高值说明', '']],
    }
    for sheet, values in contents.items():
        sheets[sheet] = pd.DataFrame(values, columns=PREPARATION_SHEETS[sheet])
    return sheets


def initialize_receipts():
    # The production init_db owns this migration; keep this fixture independent
    # while parallel implementation is being integrated.
    with db.atomic_write() as connection:
        ensure_lab_preparation_schema(connection)


def save(data, token='request-1'):
    review = prepare_lab_preparation(data)
    if review['summary']['has_blocking_errors']:
        raise AssertionError(review['issues'])
    return save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key=token)


class LabPreparationTests(unittest.TestCase):
    def test_blank_template_has_no_customer_data_and_preview_does_not_write(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            data = build_lab_preparation_template()
            workbook = xlsx_bytes_to_dataframes(data)
            self.assertEqual(list(UNIT_FIELDS), workbook['单位信息']['资料项目'].tolist())
            self.assertTrue(all(not str(value or '').strip() for value in workbook['单位信息']['填写内容']))
            self.assertTrue(all(workbook[name].empty for name in PREPARATION_SHEETS))
            before = dump()
            review = prepare_lab_preparation(data)
            self.assertTrue(review['summary']['has_blocking_errors'])
            self.assertTrue(any('尚未准备' in issue['message'] for issue in review['issues']))
            self.assertEqual(before, dump())
            populated = dataframes_to_xlsx_bytes(tables())
            review = prepare_lab_preparation(populated)
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(9, review['summary']['rows'])
            self.assertEqual(before, dump())

    def test_atomic_save_registers_and_references_without_confirming_configs(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            save_report_settings_form({'report_statement': '原报告声明保留'})
            result = save(dataframes_to_xlsx_bytes(tables()))
            self.assertEqual(1, result['format_version'])
            self.assertEqual(9, len(result['saved_rows']))
            self.assertEqual('准备实验室', get_report_settings().lab_name)
            self.assertEqual('原报告声明保留', get_report_settings().report_statement)
            with db.read_snapshot() as connection:
                manufacturer = connection.execute("SELECT id FROM md_manufacturers WHERE display_name='准备厂家'").fetchall()
                self.assertEqual(1, len(manufacturer))
                categories = {row[0] for row in connection.execute(
                    'SELECT category FROM md_manufacturer_categories WHERE manufacturer_id=?', (manufacturer[0][0],))}
                self.assertEqual({'instrument', 'reagent', 'qc_material'}, categories)
                item = connection.execute("SELECT * FROM md_test_items WHERE chinese_name='准备检验项目'").fetchone()
                unit = connection.execute("SELECT id FROM md_units WHERE symbol='prep-unit'").fetchone()
                self.assertEqual(unit[0], item['default_unit_id'])
                self.assertEqual('000123', connection.execute('SELECT lot_no FROM md_reagent_lots').fetchone()[0])
                self.assertEqual('000456', connection.execute('SELECT lot_no FROM md_qc_material_lots').fetchone()[0])
                levels = connection.execute('SELECT * FROM md_qc_levels ORDER BY level_order').fetchall()
                self.assertEqual(['低值', '高值'], [row['level_name'] for row in levels])
                self.assertTrue(all(row['specification_id'] for row in levels))
                for table in ('qc_project_templates', 'qc_lot_configs', 'qc_target_profiles', 'qc_lot_verifications'):
                    self.assertEqual(0, connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
                receipt = connection.execute('SELECT * FROM qc_lab_preparation_receipts').fetchone()
                self.assertTrue(receipt['before_settings_json'] and receipt['after_settings_json'] and receipt['created_at'])
            self.assertEqual(result['saved_rows'], get_lab_preparation_receipt(result['receipt_id'])['saved_rows'])
            reference = xlsx_bytes_to_dataframes(build_lab_preparation_reference_xlsx())['已登记资料']
            self.assertTrue(reference['已登记标识'].str.len().gt(0).all())
            self.assertTrue({'计量单位', '方法学', '检验项目', '仪器', '试剂产品', '质控品产品'}.issubset(set(reference['资料类型'])))

    def test_same_request_and_file_are_idempotent_including_new_request(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            data = dataframes_to_xlsx_bytes(tables())
            review = prepare_lab_preparation(data)
            first = save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key='first')
            after = dump()
            for token in ('first', 'new-request'):
                replay = save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key=token)
                self.assertTrue(replay['reused'])
                self.assertEqual(first['receipt_id'], replay['receipt_id'])
                self.assertEqual(after, dump())
            changed = tables()
            changed['单位信息'].loc[0, '填写内容'] = '另一单位'
            with self.assertRaisesRegex(ValueError, '文件已变化'):
                save_lab_preparation(dataframes_to_xlsx_bytes(changed), expected_preview_hash=review['preview_hash'], request_key='first')
            self.assertEqual(after, dump())

    def test_existing_rows_are_reused_blank_settings_preserve_values_and_relations(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            save(dataframes_to_xlsx_bytes(tables()))
            with db.read_snapshot() as connection:
                before = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('md_manufacturers', 'md_reagents', 'md_qc_materials', 'md_qc_material_lots', 'md_qc_levels')}
            second = tables()
            second['单位信息']['填写内容'] = ''
            data = dataframes_to_xlsx_bytes(second)
            review = prepare_lab_preparation(data)
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(9, review['summary']['reuse'])
            self.assertFalse(review['settings'])
            self.assertFalse(review['plan'])
            result = save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key='references')
            self.assertFalse(result['reused'])
            with db.read_snapshot() as connection:
                for table, rows in before.items():
                    self.assertEqual(rows, [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')])
            self.assertEqual('准备实验室', get_report_settings().lab_name)

    def test_stale_preview_and_changed_file_do_not_partially_save(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            data = dataframes_to_xlsx_bytes(tables())
            review = prepare_lab_preparation(data)
            save_report_settings_form({'lab_name': '另一会话填写'})
            before = dump()
            with self.assertRaisesRegex(ValueError, '已变化'):
                save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key='stale')
            self.assertEqual(before, dump())
            review = prepare_lab_preparation(data)
            changed = tables()
            changed['仪器'].loc[0, '序列号'] = 'CHANGED-SERIAL'
            with self.assertRaisesRegex(ValueError, '已变化'):
                save_lab_preparation(dataframes_to_xlsx_bytes(changed), expected_preview_hash=review['preview_hash'], request_key='changed')
            self.assertEqual(before, dump())

    def test_validation_detects_missing_refs_categories_conflicts_and_partial_levels(self):
        scenarios = ('missing-reference', 'duplicate-key', 'invalid-date', 'missing-level', 'wrong-category', 'unsupported-version')
        for scenario in scenarios:
            with self.subTest(scenario=scenario), TemporaryDatabaseContext():
                initialize_receipts()
                contents = tables()
                if scenario == 'missing-reference':
                    contents['试剂批号'].loc[0, '试剂产品编号或标识'] = '没有此产品'
                elif scenario == 'duplicate-key':
                    contents['质控材料'].loc[1, '清单编号'] = 'C1'
                elif scenario == 'invalid-date':
                    contents['试剂批号'].loc[0, '效期'] = '不是日期'
                elif scenario == 'missing-level':
                    contents['质控材料'].loc[0, '浓度水平'] = ''
                elif scenario == 'wrong-category':
                    create_manufacturer(display_name='准备厂家', categories=['reagent'])
                else:
                    contents['填写说明'].loc[contents['填写说明']['事项'] == '清单版本', '说明'] = '999'
                before = dump()
                data = dataframes_to_xlsx_bytes(contents)
                review = prepare_lab_preparation(data)
                self.assertTrue(review['summary']['has_blocking_errors'], review['issues'])
                with self.assertRaisesRegex(ValueError, '审查未通过'):
                    save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key=scenario)
                self.assertEqual(before, dump())

    def test_known_uid_can_be_referenced_but_different_metadata_is_not_overwritten(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            save(dataframes_to_xlsx_bytes(tables()))
            with db.read_snapshot() as connection:
                uid = connection.execute('SELECT uid FROM md_reagents').fetchone()[0]
            sheets = xlsx_bytes_to_dataframes(build_lab_preparation_template())
            sheets['试剂产品'] = pd.DataFrame([['R2', uid, '', '', '', '']], columns=PREPARATION_SHEETS['试剂产品'])
            before = dump()
            review = prepare_lab_preparation(dataframes_to_xlsx_bytes(sheets))
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(1, review['summary']['reuse'])
            self.assertIn('准备试剂', review['preview'][0]['内容'])
            self.assertIn('准备厂家', review['preview'][0]['内容'])
            sheets['试剂产品'].loc[0, '货号'] = 'OTHER-CATALOG'
            bad = prepare_lab_preparation(dataframes_to_xlsx_bytes(sheets))
            self.assertTrue(bad['summary']['has_blocking_errors'])
            self.assertEqual(before, dump())
            sheets['试剂产品'] = pd.DataFrame(columns=PREPARATION_SHEETS['试剂产品'])
            sheets['质控品产品'] = pd.DataFrame([['Q2', '', '准备厂家', '准备质控品', '', '', '']],
                                              columns=PREPARATION_SHEETS['质控品产品'])
            without_number = prepare_lab_preparation(dataframes_to_xlsx_bytes(sheets))
            self.assertFalse(without_number['summary']['has_blocking_errors'], without_number['issues'])
            self.assertEqual(1, without_number['summary']['reuse'])
            self.assertFalse(without_number['plan'])
            self.assertIn('QC-001', without_number['preview'][0]['内容'])

    def test_late_registration_and_receipt_failure_roll_back_everything(self):
        for failure in ('registration', 'receipt'):
            with self.subTest(failure=failure), TemporaryDatabaseContext():
                initialize_receipts()
                if failure == 'receipt':
                    with db.atomic_write() as connection:
                        connection.execute("""CREATE TRIGGER fail_preparation_receipt BEFORE INSERT ON qc_lab_preparation_receipts
                            BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END""")
                before = dump()
                data = dataframes_to_xlsx_bytes(tables())
                review = prepare_lab_preparation(data)
                if failure == 'registration':
                    with patch('services.material_workflow_service.register_control_material', side_effect=ValueError('末行登记失败')):
                        with self.assertRaisesRegex(ValueError, '末行登记失败'):
                            save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key='fail-registration')
                else:
                    with self.assertRaisesRegex(ValueError, '未保存'):
                        save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key='fail-receipt')
                self.assertEqual(before, dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
