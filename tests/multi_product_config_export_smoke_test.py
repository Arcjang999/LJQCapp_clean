"""All SKU provenance is exported without weakening the existing import contract."""
from collections import OrderedDict
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.export_utils import xlsx_bytes_to_dataframes, dataframes_to_xlsx_bytes
from services.project_config_io_service import (build_lot_config_xlsx, build_project_template_xlsx,
    prepare_project_template_import, import_project_template_xlsx)
from services.project_config_service import create_project_template, list_template_items, validate_project_template
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.multi_product_control_workflow_smoke_test import multi_values, save


class MultiProductConfigExportTests(unittest.TestCase):
    def test_batch_overview_and_reference_sheets_include_every_actual_sku(self):
        with IsolatedDatabase():
            values = multi_values(2)
            receipt = save(values)
            sheets = xlsx_bytes_to_dataframes(build_lot_config_xlsx(receipt['lot_config_id']))
            self.assertEqual(['SKU-001', 'SKU-002'], sheets['水平均值和标准差']['产品货号'].tolist())
            self.assertEqual({'SKU-001', 'SKU-002'}, set(sheets['全部产品来源（供查阅）']['产品编号']))
            self.assertEqual(2, len(set(sheets['全部产品来源（供查阅）']['产品标识'])))
            self.assertEqual(1, len(sheets['产品来源']), '原导入匹配表仍为一条，不能使自己的导出无法预览')
            overview = dict(zip(sheets['批次信息']['字段'], sheets['批次信息']['值']))
            for sku in ('SKU-001', 'SKU-002'):
                self.assertIn(sku, overview['质控品'])
            self.assertIn('另一厂家', overview['质控品'])
            self.assertIn('水平1：QUICK-QC-01', overview['批号'])
            self.assertIn('水平2：QUICK-QC-01', overview['批号'])
            self.assertIn('须重新选择各水平产品和批号', overview['导入说明'])

    def test_template_product_set_export_previews_and_imports_but_stays_unconfirmed(self):
        with IsolatedDatabase():
            values = multi_values(2)
            receipt = save(values)
            data = build_project_template_xlsx(receipt['template_id'])
            sheets = xlsx_bytes_to_dataframes(data)
            self.assertEqual({'SKU-001', 'SKU-002'}, set(sheets['全部产品来源（供查阅）']['产品编号']))
            overview = dict(zip(sheets['项目信息']['字段'], sheets['项目信息']['值']))
            self.assertIn('SKU-002', overview['质控品'])
            self.assertIn('供查阅', overview['导入说明'])
            target = create_project_template(template_name='导入后待核对的多产品项目',
                lab_instrument_id=values['lab_instrument_id'], qc_material_id=values['qc_material_id'])
            prepared = prepare_project_template_import(target, data)
            self.assertEqual([], prepared['errors'])
            result = import_project_template_xlsx(target, data,
                expected_revision=prepared['expected_revision'], file_sha256=prepared['file_sha256'],
                product_version=prepared['product_version'])
            self.assertEqual(1, result['imported_count'])
            self.assertEqual(1, len(list_template_items(target)))
            self.assertTrue(validate_project_template(target), '参考明细不能充当本地适用性及质量确认')

    def test_extra_reference_sheets_do_not_relax_primary_product_identity_check(self):
        with IsolatedDatabase():
            values = multi_values(2)
            receipt = save(values)
            data = build_project_template_xlsx(receipt['template_id'])
            sheets = xlsx_bytes_to_dataframes(data)
            sheets['产品来源'] = sheets['全部产品来源（供查阅）'].copy()
            changed = dataframes_to_xlsx_bytes(OrderedDict(sheets))
            prepared = prepare_project_template_import(receipt['template_id'], changed)
            self.assertTrue(any('恰好包含' in row for row in prepared['errors']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
