"""Product concentration fields survive preparation and project-source exchange."""
from pathlib import Path
import sys
import unittest
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import database as db
from services import master_data_service as master
from services import product_directory_service as directory
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.lab_preparation_service import (
    PREPARATION_SHEETS, build_lab_preparation_template, build_lab_preparation_reference_xlsx,
    prepare_lab_preparation, save_lab_preparation,
)
from services.project_config_io_service import _material_context_sheets, _read_material_context
from tests.settings_smoke_test import TemporaryDatabaseContext


def workbook(product, *, legacy=False):
    sheets = xlsx_bytes_to_dataframes(build_lab_preparation_template())
    columns = PREPARATION_SHEETS['质控品产品'][:-2] if legacy else PREPARATION_SHEETS['质控品产品']
    sheets['质控品产品'] = pd.DataFrame([product], columns=columns).fillna('')
    return dataframes_to_xlsx_bytes(sheets)


def dump():
    with db.read_snapshot() as connection:
        return '\n'.join(connection.iterdump())


def save(data, key):
    review = prepare_lab_preparation(data)
    if review['summary']['has_blocking_errors']:
        raise AssertionError(review['issues'])
    return save_lab_preparation(data, expected_preview_hash=review['preview_hash'], request_key=key)


def workbook_xml(data, name):
    namespace = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(BytesIO(data)) as archive:
        book = ET.fromstring(archive.read('xl/workbook.xml'))
        index = next(index for index, sheet in enumerate(book.findall('s:sheets/s:sheet', namespace), 1)
                     if sheet.attrib['name'] == name)
        return (namespace, ET.fromstring(archive.read('xl/styles.xml')),
                ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml')))


class ProductAttributeIOTests(unittest.TestCase):
    def product_row(self, **changes):
        return dict({'清单编号': 'Q1', '已登记标识': '', '厂家': '产品资料厂家', '质控品名称': '同名质控品',
            '商品名': '', '货号': 'QC-001', '标称水平数': '', '浓度编号': '001',
            '实际浓度': '1.00*10^5～1.00*10^6 IU/mL'}, **changes)

    def manufacturer(self):
        return master.create_manufacturer(display_name='产品资料厂家', categories=['qc_material'])

    def test_empty_preparation_template_formats_concentration_codes_as_text(self):
        data = build_lab_preparation_template()
        for name in ('质控品产品', '质控材料'):
            with self.subTest(sheet=name):
                namespace, styles, sheet = workbook_xml(data, name)
                column_index = PREPARATION_SHEETS[name].index('浓度编号') + 1
                column = next(column for column in sheet.findall('s:cols/s:col', namespace)
                              if int(column.attrib['min']) == column_index)
                style = styles.findall('s:cellXfs/s:xf', namespace)[int(column.attrib['style'])]
                self.assertEqual('49', style.attrib['numFmtId'])
                self.assertEqual(1, len(sheet.findall('s:sheetData/s:row', namespace)))

    def test_text_column_option_preserves_values_and_default_formats(self):
        frame = pd.DataFrame({'浓度编号': ['001', 1], '测量值': [10.5, 20.0]})
        data = dataframes_to_xlsx_bytes({'资料': frame}, text_columns={'资料': ['浓度编号']})
        namespace, styles, sheet = workbook_xml(data, '资料')
        code = sheet.find('s:sheetData/s:row/s:c[@r="A2"]', namespace)
        style = styles.findall('s:cellXfs/s:xf', namespace)[int(code.attrib['s'])]
        self.assertEqual(('49', 's'), (style.attrib['numFmtId'], code.attrib['t']))
        values = xlsx_bytes_to_dataframes(data)['资料']
        self.assertEqual(['001', 1], values['浓度编号'].tolist())
        self.assertEqual([10.5, 20.0], values['测量值'].tolist())
        default = dataframes_to_xlsx_bytes({'资料': frame})
        namespace, styles, sheet = workbook_xml(default, '资料')
        self.assertEqual(2, len(styles.findall('s:cellXfs/s:xf', namespace)))
        self.assertTrue(all('style' not in column.attrib for column in sheet.findall('s:cols/s:col', namespace)))
        self.assertEqual(frame.to_dict('records'), xlsx_bytes_to_dataframes(default)['资料'].to_dict('records'))
        for options in ({'其他表': ['浓度编号']}, {'资料': ['不存在的列']}):
            with self.assertRaises(ValueError):
                dataframes_to_xlsx_bytes({'资料': frame}, text_columns=options)

    def test_new_preparation_round_trip_preserves_original_text_and_no_materials(self):
        with TemporaryDatabaseContext():
            headers = xlsx_bytes_to_dataframes(build_lab_preparation_template())['质控品产品'].columns.tolist()
            self.assertEqual(['浓度编号', '实际浓度'], headers[-2:])
            data = workbook(self.product_row())
            before = dump()
            review = prepare_lab_preparation(data)
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertIn('001', review['preview'][0]['内容'])
            self.assertIn('1.00*10^5～1.00*10^6 IU/mL', review['preview'][0]['内容'])
            self.assertEqual(before, dump())
            result = save(data, 'product-attributes')
            product_id = result['saved_rows'][0]['record_id']
            with db.read_snapshot() as connection:
                product = dict(connection.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
                self.assertEqual('001', product['concentration_code'])
                self.assertEqual('1.00*10^5～1.00*10^6 IU/mL', product['concentration'])
                for table in ('md_qc_material_specs', 'md_qc_material_lots', 'md_qc_levels', 'qc_project_templates'):
                    self.assertEqual(0, connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
            reference_data = build_lab_preparation_reference_xlsx()
            reference = xlsx_bytes_to_dataframes(reference_data)['已登记资料']
            item = reference.loc[reference['已登记标识'] == product['uid']].iloc[0]
            self.assertEqual(('001', product['concentration']), (item['浓度编号'], item['实际浓度']))
            namespace, styles, sheet = workbook_xml(reference_data, '已登记资料')
            column_index = reference.columns.tolist().index('浓度编号') + 1
            column = next(column for column in sheet.findall('s:cols/s:col', namespace)
                          if int(column.attrib['min']) == column_index)
            self.assertEqual('49', styles.findall('s:cellXfs/s:xf', namespace)[int(column.attrib['style'])].attrib['numFmtId'])
            repeated = save(data, 'another-request')
            self.assertTrue(repeated['reused'])

    def test_old_seven_column_template_reuses_without_clearing_product_fields(self):
        with TemporaryDatabaseContext():
            manufacturer = self.manufacturer()
            product_id = master.create_qc_material(manufacturer_id=manufacturer, generic_name='同名质控品',
                catalog_no='QC-001', concentration_code='001', concentration='阴性')
            with db.read_snapshot() as connection:
                original = dict(connection.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
            row = self.product_row(**{'已登记标识': original['uid']})
            data = workbook(row, legacy=True)
            result = save(data, 'legacy-template')
            self.assertEqual(product_id, result['saved_rows'][0]['record_id'])
            self.assertEqual(1, result['summary']['reuse'])
            with db.read_snapshot() as connection:
                after = dict(connection.execute('SELECT * FROM md_qc_materials WHERE id=?', (product_id,)).fetchone())
            self.assertEqual(original, after)
            legacy_new = self.product_row(**{'清单编号': 'Q2', '质控品名称': '旧模板新增产品', '货号': 'QC-002'})
            result = save(workbook(legacy_new, legacy=True), 'legacy-new')
            with db.read_snapshot() as connection:
                row = connection.execute('SELECT concentration_code,concentration FROM md_qc_materials WHERE id=?',
                    (result['saved_rows'][0]['record_id'],)).fetchone()
            self.assertEqual(('', ''), tuple(row))

    def test_same_name_products_are_not_mixed_and_conflicts_do_not_write(self):
        with TemporaryDatabaseContext():
            manufacturer = self.manufacturer()
            first = master.create_qc_material(manufacturer_id=manufacturer, generic_name='同名质控品',
                catalog_no='QC-001', concentration_code='S1', concentration='10 IU/mL')
            second = master.create_qc_material(manufacturer_id=manufacturer, generic_name='同名质控品',
                catalog_no='QC-002', concentration_code='S2', concentration='100 IU/mL')
            exact = self.product_row(**{'货号': 'QC-002', '浓度编号': 'S2', '实际浓度': '100 IU/mL'})
            review = prepare_lab_preparation(workbook(exact))
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(second, review['preview'][0]['reference'])
            before = dump()
            for changes in ({'浓度编号': 'S1'}, {'实际浓度': '10 IU/mL'}, {'货号': ''}):
                with self.subTest(changes=changes):
                    data = workbook(dict(exact, **changes))
                    bad = prepare_lab_preparation(data)
                    self.assertTrue(bad['summary']['has_blocking_errors'])
                    with self.assertRaisesRegex(ValueError, '审查未通过'):
                        save_lab_preparation(data, expected_preview_hash=bad['preview_hash'], request_key=str(changes))
                    self.assertEqual(before, dump())
            self.assertNotEqual(first, second)

    def test_published_directory_attributes_remain_the_reference_source(self):
        with TemporaryDatabaseContext():
            manufacturer = self.manufacturer()
            package = deepcopy(directory.load_bondson_directory_package())
            selected = next(row for row in package['records'] if row['product_code'] == 'IQC-CM-02103')
            package.update(source_code='isolated-product-attributes', records=[selected], excluded=[])
            preview = directory.preview_product_directory(package, manufacturer)
            directory.publish_product_directory(package, manufacturer, expected_preview_hash=preview['preview_hash'],
                published_by='资料核对员')
            with db.read_snapshot() as connection:
                product = dict(connection.execute('SELECT * FROM md_qc_materials').fetchone())
            reference = xlsx_bytes_to_dataframes(build_lab_preparation_reference_xlsx())['已登记资料']
            item = reference.loc[reference['已登记标识'] == product['uid']].iloc[0]
            self.assertEqual((selected['concentration_code'], selected['concentration']),
                (item['浓度编号'], item['实际浓度']))
            data = workbook(self.product_row(**{'已登记标识': product['uid'], '质控品名称': selected['product_name'],
                '货号': selected['product_code'], '浓度编号': selected['concentration_code'],
                '实际浓度': selected['concentration']}))
            before = dump()
            review = prepare_lab_preparation(data)
            self.assertFalse(review['summary']['has_blocking_errors'], review['issues'])
            self.assertEqual(product['id'], review['preview'][0]['reference'])
            self.assertEqual(before, dump())

    def test_project_product_source_round_trip_keeps_local_attributes(self):
        with TemporaryDatabaseContext():
            product_id = master.create_qc_material(manufacturer_id=self.manufacturer(), generic_name='来源产品',
                catalog_no='QC-003', concentration_code='001', concentration='阴性 / 基因型1b')
            before = dump()
            data = dataframes_to_xlsx_bytes(dict(_material_context_sheets(product_id)))
            context = _read_material_context(data)
            self.assertEqual(('001', '阴性 / 基因型1b'),
                (context['product']['concentration_code'], context['product']['concentration']))
            with db.read_snapshot() as connection:
                directory.validate_material_catalog_context(connection, product_id, context)
                for field in ('concentration_code', 'concentration'):
                    changed = deepcopy(context)
                    changed['product'][field] = '改动的属性'
                    with self.assertRaisesRegex(ValueError, '当前所选产品不同'):
                        directory.validate_material_catalog_context(connection, product_id, changed)
            self.assertEqual(before, dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
