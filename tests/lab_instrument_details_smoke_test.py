"""Unified device registration keeps shared models and device identities distinct."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from services import master_data_edit_service as edits
from services import master_data_service as master
from services.project_config_service import create_project_template


class LabInstrumentDetailsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.old_path = database.DB_PATH
        self.old_legacy = database.LEGACY_DB_CANDIDATES
        database.DB_PATH = Path(self.temp.name) / 'instrument-details.db'
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()
        self.manufacturer = master.create_manufacturer(display_name='仪器测试厂家', categories=['instrument'])

    def tearDown(self):
        database.DB_PATH = self.old_path
        database.LEGACY_DB_CANDIDATES = self.old_legacy
        self.temp.cleanup()

    def save(self, name='一号仪器', model='LAB-100', **values):
        return edits.save_lab_instrument_details({
            'manufacturer_id': self.manufacturer, 'model': model, 'display_name': name, **values,
        })

    def row(self, table, identifier):
        with database.read_snapshot() as connection:
            return dict(connection.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone())

    def rows(self, table):
        with database.read_snapshot() as connection:
            return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]

    def dump(self):
        with database.read_snapshot() as connection:
            return '\n'.join(connection.iterdump())

    def reject_unchanged(self, call, message):
        before = self.dump()
        with self.assertRaisesRegex(ValueError, message):
            call()
        self.assertEqual(self.dump(), before)

    def update(self, identifier, **values):
        context = edits.get_master_record_context('lab_instrument', identifier)
        return edits.save_lab_instrument_details(values, entity_id=identifier,
                                                 expected_fingerprint=context['fingerprint'])

    def disable(self, entity_type, identifier):
        context = edits.get_master_record_context(entity_type, identifier)
        edits.change_master_status(entity_type, identifier, disabled=True,
                                   expected_fingerprint=context['fingerprint'], reason='资料停用核对')

    def test_new_model_and_two_devices_reuse_without_changing_shared_details(self):
        first = self.save(model='  LAB   100  ', asset_code='资产 001', serial_number='S001',
                          department_name='检验科', instrument_group='生化组', location='一层', notes='第一行\n第二行')
        first_row = self.row('lab_instruments', first)
        model_id = first_row['instrument_model_id']
        model = self.row('md_instrument_models', model_id)
        self.assertEqual((model['manufacturer_id'], model['model'], model['generic_name']),
                         (self.manufacturer, 'LAB 100', 'LAB 100'))
        self.assertEqual(first_row['notes'], '第一行\n第二行')
        with database.atomic_write() as connection:
            connection.execute("UPDATE md_instrument_models SET generic_name='既有通用名',brand_name='既有品牌',notes='共享资料' WHERE id=?", (model_id,))
        shared_before = self.row('md_instrument_models', model_id)
        second = self.save(name='二号仪器', model='lab 100', serial_number='S002', location='二层')
        second_row = self.row('lab_instruments', second)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_row['uid'], second_row['uid'])
        self.assertEqual(second_row['instrument_model_id'], model_id)
        self.assertEqual(self.row('md_instrument_models', model_id), shared_before)
        self.assertEqual(self.row('lab_instruments', first), first_row)
        other_manufacturer = master.create_manufacturer(display_name='另一仪器厂家', categories=['instrument'])
        third = self.save(name='另一厂家仪器', model='LAB 100', manufacturer_id=other_manufacturer)
        self.assertNotEqual(self.row('lab_instruments', third)['instrument_model_id'], model_id)
        distinct = self.save(name='不同字符型号仪器', model='LAB-100')
        self.assertNotEqual(self.row('lab_instruments', distinct)['instrument_model_id'], model_id)

    def test_invalid_or_duplicate_device_does_not_leave_a_new_model(self):
        self.save()
        self.reject_unchanged(lambda: self.save(model='不能留下的新型号'), '同名')
        self.reject_unchanged(lambda: self.save(name='', model='另一个不能留下的新型号'), '不能为空')
        self.reject_unchanged(lambda: self.save(name='第三台', model='有效型号', instrument_model_id=1), '不能编辑')
        self.reject_unchanged(lambda: self.save(name='第三台', model=''), '型号不能为空')
        reagent_manufacturer = master.create_manufacturer(display_name='仅试剂厂家', categories=['reagent'])
        self.reject_unchanged(lambda: self.save(name='第三台', manufacturer_id=reagent_manufacturer), '仪器厂家')

    def test_failure_after_device_insert_rolls_back_both_tables(self):
        before = self.dump()
        original_creator = edits._CREATORS['lab_instrument']

        def fail_after_insert(**values):
            original_creator(**values)
            raise RuntimeError('故障注入：实际仪器已插入但整体保存未完成')

        with patch.dict(edits._CREATORS, {'lab_instrument': fail_after_insert}):
            with self.assertRaisesRegex(RuntimeError, '故障注入'):
                self.save()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.rows('md_instrument_models'), [])
        self.assertEqual(self.rows('lab_instruments'), [])

    def test_changing_unreferenced_device_model_does_not_edit_shared_model(self):
        first = self.save()
        second = self.save(name='二号仪器')
        old_model_id = self.row('lab_instruments', first)['instrument_model_id']
        original_model = self.row('md_instrument_models', old_model_id)
        second_before = self.row('lab_instruments', second)
        self.update(first, manufacturer_id=self.manufacturer, model='LAB-200', location='三层')
        changed = self.row('lab_instruments', first)
        self.assertNotEqual(changed['instrument_model_id'], old_model_id)
        self.assertEqual(self.row('md_instrument_models', old_model_id), original_model)
        self.assertEqual(self.row('lab_instruments', second), second_before)
        self.update(first, manufacturer_id=self.manufacturer, model='lab-100')
        self.assertEqual(self.row('lab_instruments', first)['instrument_model_id'], old_model_id)
        self.assertEqual(len(self.rows('md_instrument_models')), 2)

    def test_project_references_lock_identity_but_allow_location_and_reject_stale_edits(self):
        instrument = self.save()
        material_manufacturer = master.create_manufacturer(display_name='质控品测试厂家', categories=['qc_material'])
        material = master.create_qc_material(generic_name='仪器登记验收质控品', manufacturer_id=material_manufacturer)
        project = create_project_template(template_name='仪器引用项目', lab_instrument_id=instrument, qc_material_id=material)
        project_before = self.row('qc_project_templates', project)
        context = edits.get_master_record_context('lab_instrument', instrument)
        self.assertIn('instrument_model_id', context['locked_fields'])
        self.reject_unchanged(lambda: self.update(instrument, model='LAB-300'), '已用于项目')
        other = master.create_manufacturer(display_name='其他仪器厂家', categories=['instrument'])
        self.reject_unchanged(lambda: self.update(instrument, manufacturer_id=other), '已用于项目')
        self.reject_unchanged(lambda: self.update(instrument, display_name='不可改名'), '已用于项目')
        self.update(instrument, manufacturer_id=self.manufacturer, model='lab-100', department_name='检验二科',
                    instrument_group='免疫组', location='三层', notes='位置更新\n仪器未变')
        updated = self.row('lab_instruments', instrument)
        self.assertEqual(updated['instrument_model_id'], context['record']['instrument_model_id'])
        self.assertEqual((updated['department_name'], updated['instrument_group'], updated['location']), ('检验二科', '免疫组', '三层'))
        self.assertEqual(self.row('qc_project_templates', project), project_before)
        self.reject_unchanged(lambda: edits.save_lab_instrument_details({'model': '未保存的新型号'}, entity_id=instrument,
                              expected_fingerprint=context['fingerprint']), '资料已修改')
        self.reject_unchanged(lambda: edits.save_lab_instrument_details({'notes': '未核对'}, entity_id=instrument), '资料已修改')

    def test_disabled_relationships_are_not_restored_and_existing_notes_remain_editable(self):
        instrument = self.save()
        old_model_id = self.row('lab_instruments', instrument)['instrument_model_id']
        self.disable('instrument_model', old_model_id)
        old_model = self.row('md_instrument_models', old_model_id)
        self.update(instrument, location='新位置', notes='原型号保留')
        self.assertEqual(self.row('lab_instruments', instrument)['instrument_model_id'], old_model_id)
        second = self.save(name='另一台新登记仪器', model='lab-100')
        self.assertNotEqual(self.row('lab_instruments', second)['instrument_model_id'], old_model_id)
        self.assertEqual(self.row('md_instrument_models', old_model_id), old_model)
        self.disable('manufacturer', self.manufacturer)
        self.reject_unchanged(lambda: self.save(name='停用厂家不能新增'), '启用的仪器厂家')
        self.update(instrument, notes='厂家停用后仍可补充原设备备注')
        self.assertEqual(self.row('md_manufacturers', self.manufacturer)['is_disabled'], 1)
        self.disable('lab_instrument', instrument)
        self.reject_unchanged(lambda: self.update(instrument, notes='不可编辑已停用仪器'), '先恢复')


if __name__ == '__main__':
    unittest.main(verbosity=2)
