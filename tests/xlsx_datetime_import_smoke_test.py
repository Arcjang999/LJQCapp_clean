"""Excel-native date cells survive the editable daily-result workbook round trip."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.daily_draft_service import new_draft
from services.daily_result_io_service import export_daily_workbook, preview_daily_workbook, apply_daily_workbook

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def native_dates(data, cells, when, *, date1904=False, format_code='yyyy-mm-dd hh:mm:ss', format_id=164):
    """Write the same numeric/date-style representation Excel saves in OOXML."""
    epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    serial = (when - epoch).total_seconds() / 86400
    out = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(out, 'w', ZIP_DEFLATED) as target:
        styles = ET.fromstring(source.read('xl/styles.xml'))
        xfs = styles.find(f'{{{NS}}}cellXfs')
        index = len(xfs)
        ET.SubElement(xfs, f'{{{NS}}}xf', numFmtId=str(format_id), fontId='0', fillId='0', borderId='0', xfId='0')
        xfs.set('count', str(len(xfs)))
        if format_id >= 164:
            formats = styles.find(f'{{{NS}}}numFmts')
            if formats is None:
                formats = ET.SubElement(styles, f'{{{NS}}}numFmts')
            ET.SubElement(formats, f'{{{NS}}}numFmt', numFmtId=str(format_id), formatCode=format_code)
            formats.set('count', str(len(formats)))
        for name in source.namelist():
            content = source.read(name)
            if name == 'xl/styles.xml':
                content = ET.tostring(styles)
            elif name == 'xl/workbook.xml' and date1904:
                root = ET.fromstring(content)
                ET.SubElement(root, f'{{{NS}}}workbookPr', date1904='1')
                content = ET.tostring(root)
            elif name == 'xl/worksheets/sheet1.xml':
                root = ET.fromstring(content)
                for cell in root.iter(f'{{{NS}}}c'):
                    if cell.get('r') in cells:
                        cell.attrib.pop('t', None)
                        cell.set('s', str(index))
                        for child in list(cell):
                            cell.remove(child)
                        ET.SubElement(cell, f'{{{NS}}}v').text = str(serial)
                content = ET.tostring(root)
            target.writestr(name, content)
    return out.getvalue()


def draft():
    item = dict(lot_config_item_id=1, instrument_name='PCR 01', project_name='核酸质控', test_item_name='HBV DNA',
                input_value_type='ct', unit_id=1, reagent_options=[dict(id=1, lot_no='REAGENT-01')],
                suggested_reagent_lot_id=1, levels=[dict(qc_level_id=1, lot_no='QC-01', level_name='阳性')])
    result = new_draft(dict(selection=dict(lab_instrument_id=1, qc_material_id=1, qc_material_lot_id=1),
                            context_revision='unchanged', test_time='2026-09-28 08:00:00', items=[item]), operator='演示操作员')
    result['values']['1:1'] = '27.6'
    return result


class ExcelDateTests(unittest.TestCase):
    def test_edited_excel_datetime_round_trips_into_daily_draft(self):
        original = draft()
        when = datetime(2026, 9, 29, 9, 15, 30)
        for date1904 in (False, True):
            with self.subTest(date1904=date1904):
                workbook = native_dates(export_daily_workbook(original), {'G2'}, when, date1904=date1904)
                preview = preview_daily_workbook(workbook, original)
                self.assertTrue(preview['valid'], preview['errors'])
                self.assertEqual('2026-09-29 09:15:30', preview['test_time'])
                target = draft()
                apply_daily_workbook(target, preview)
                self.assertEqual('2026-09-29 09:15:30', target['test_time'])
                self.assertEqual({'1:1': '27.6'}, target['values'])

    def test_built_in_date_style_is_decoded(self):
        book = dataframes_to_xlsx_bytes({'日期': pd.DataFrame({'效期': ['2026-09-29']})})
        book = native_dates(book, {'A2'}, datetime(2026, 9, 29), format_id=14)
        value = xlsx_bytes_to_dataframes(book)['日期'].iloc[0, 0]
        self.assertEqual('2026-09-29', value.strftime('%Y-%m-%d'))

    def test_plain_numbers_and_literal_units_are_not_dates(self):
        book = dataframes_to_xlsx_bytes({'结果': pd.DataFrame({'值': [46294.5]})})
        book = native_dates(book, {'A2'}, datetime(2026, 9, 29, 12), format_code='0.0 "day"')
        self.assertIsInstance(xlsx_bytes_to_dataframes(book)['结果'].iloc[0, 0], (int, float))


if __name__ == '__main__':
    unittest.main(verbosity=2)
