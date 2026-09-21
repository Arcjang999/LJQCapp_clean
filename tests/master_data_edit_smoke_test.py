"""Catalogue edits preserve referenced identity and reject stale or invalid writes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection, init_db
from services.master_data_edit_service import (
    change_master_status, get_master_record_context, save_master_record,
)
from services.master_data_service import create_qc_material
from services.project_config_service import (
    activate_project_template, create_lot_config_from_template, create_project_template,
    save_template_items,
)
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies,
)
from tests.quality_review_fixtures import confirm_fixture_project


def rejected(call, message: str):
    before = dump()
    try:
        call()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError('Expected ValueError')
    assert dump() == before, 'Rejected operation must not write any table'


def dump():
    with get_connection() as connection:
        return '\n'.join(connection.iterdump())


def context(kind, identifier):
    return get_master_record_context(kind, identifier)


def record(kind, identifier):
    return context(kind, identifier)['record']


def edit(kind, identifier, **values):
    return save_master_record(kind, values, entity_id=identifier,
                              expected_fingerprint=context(kind, identifier)['fingerprint'])


def status(kind, identifier, disabled, reason='资料核对'):
    change_master_status(kind, identifier, disabled=disabled, reason=reason,
                         expected_fingerprint=context(kind, identifier)['fingerprint'])


def seed_records():
    manufacturer = save_master_record('manufacturer', {'display_name': '目录测试厂家', 'legal_name': '目录测试有限公司'})
    unit = save_master_record('unit', {'symbol': 'test-unit', 'quantity_kind': '质量浓度'})
    method = save_master_record('method', {'method_name': '目录测试方法', 'method_code': 'M001'})
    item = save_master_record('test_item', {'chinese_name': '目录测试项目', 'default_unit_id': unit})
    model = save_master_record('instrument_model', {'manufacturer_id': manufacturer, 'generic_name': '目录分析仪', 'model': 'CAT-1'})
    instrument = save_master_record('lab_instrument', {'instrument_model_id': model, 'display_name': '目录测试仪器'})
    reagent = save_master_record('reagent', {'manufacturer_id': manufacturer, 'generic_name': '目录检测试剂'})
    alias = save_master_record('alias', {'entity_type': 'test_item', 'entity_id': item, 'alias_text': '目录别名'})
    return {'manufacturer': manufacturer, 'unit': unit, 'method': method, 'test_item': item,
            'instrument_model': model, 'lab_instrument': instrument, 'reagent': reagent, 'alias': alias}


def test_local_edit_round_trip_and_duplicates():
    with TemporaryDatabaseContext():
        ids = seed_records()
        for kind, identifier in ids.items():
            before = record(kind, identifier)
            if kind != 'alias':
                assert edit(kind, identifier, notes='补充登记备注\n第二行说明') == identifier
                assert record(kind, identifier)['notes'] == '补充登记备注\n第二行说明'
            after = record(kind, identifier)
            assert (after['id'], after['uid'], after['origin_type'], after['created_at']) == (
                before['id'], before['uid'], before['origin_type'], before['created_at'])
        edit('method', ids['method'], method_name='目录测试方法改名')
        assert record('method', ids['method'])['method_name'] == '目录测试方法改名'
        standalone = save_master_record('unit', {'symbol': 'standalone-unit', 'notes': '第一行\n第二行'})
        assert record('unit', standalone)['notes'] == '第一行\n第二行'
        edit('unit', standalone, symbol='correct-unit')
        rejected(lambda: edit('unit', standalone, symbol='test-unit', notes='不能部分保存'), '已有同名')
        rejected(lambda: save_master_record('unit', {'symbol': ' test-unit '}), '已存在')
        rejected(lambda: edit('method', ids['method'], method_name=''), '不能为空')
        rejected(lambda: edit('method', ids['method'], origin_type='official'), '不能编辑')


def test_official_identity_and_local_notes():
    with TemporaryDatabaseContext():
        with get_connection() as connection:
            targets = [(kind, connection.execute(f'SELECT id FROM {table} WHERE origin_type=\'official\' LIMIT 1').fetchone()[0], field)
                       for kind, table, field in [('test_item', 'md_test_items', 'chinese_name'),
                                                ('method', 'md_methods', 'method_name'), ('unit', 'md_units', 'symbol')]]
        for kind, identifier, field in targets:
            entry = context(kind, identifier)
            assert field in entry['locked_fields'] and 'notes' not in entry['locked_fields']
            rejected(lambda: edit(kind, identifier, **{field: '不应覆盖标准资料'}), '系统收录')
            edit(kind, identifier, notes='本实验室备注')
            assert record(kind, identifier)['notes'] == '本实验室备注'
            assert record(kind, identifier)[field] == entry['record'][field]
        item_id = targets[0][1]
        alias_id = save_master_record('alias', {'entity_type': 'test_item', 'entity_id': item_id, 'alias_text': '本地项目简称'})
        assert record('alias', alias_id)['origin_type'] == 'hospital'


def test_project_references_and_history_unchanged():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        template = create_project_template(template_name='目录编辑历史保护', lab_instrument_id=data['lab_instrument_id'],
            qc_material_id=data['qc_material_id'], default_reagent_id=data['reagent_id'])
        save_template_items(template, [dict(test_item_id=data['lj_item_id'], qc_method='lj', input_value_type='raw',
            unit_id=data['unit_id'], method_id=data['method_id'], reagent_id=data['reagent_id'], level_count=1, target_n=20)])
        confirm_fixture_project(template)
        activate_project_template(template)
        config = create_lot_config_from_template(template_id=template, qc_material_lot_id=data['source_lot_id'])
        with get_connection() as connection:
            snapshots = [tuple(r) for r in connection.execute('SELECT * FROM qc_config_snapshots WHERE lot_config_id=?', (config,))]
            model = connection.execute('SELECT instrument_model_id FROM lab_instruments WHERE id=?', (data['lab_instrument_id'],)).fetchone()[0]
            manufacturer = connection.execute('SELECT manufacturer_id FROM md_instrument_models WHERE id=?', (model,)).fetchone()[0]
        cases = [('test_item', data['lj_item_id'], 'chinese_name'), ('unit', data['unit_id'], 'symbol'),
                 ('method', data['method_id'], 'method_name'), ('reagent', data['reagent_id'], 'generic_name'),
                 ('lab_instrument', data['lab_instrument_id'], 'display_name'), ('instrument_model', model, 'model'),
                 ('manufacturer', manufacturer, 'display_name')]
        for kind, identifier, identity in cases:
            entry = context(kind, identifier)
            assert entry['referenced'] and identity in entry['locked_fields'], (kind, entry)
            rejected(lambda: edit(kind, identifier, **{identity: '历史身份不可更换', 'notes': '失败也不写备注'}), '不能直接修改')
            edit(kind, identifier, notes='资料补充')
            assert record(kind, identifier)[identity] == entry['record'][identity]
        edit('lab_instrument', data['lab_instrument_id'], location='实验室二层', department_name='检验科')
        with get_connection() as connection:
            assert snapshots == [tuple(r) for r in connection.execute('SELECT * FROM qc_config_snapshots WHERE lot_config_id=?', (config,))]
        # Disabled and draft references still lock identities.
        with get_connection() as connection:
            connection.execute('UPDATE qc_project_templates SET is_disabled=1 WHERE id=?', (template,))
        assert context('lab_instrument', data['lab_instrument_id'])['referenced']


def test_default_fields_materials_and_new_references_lock():
    with TemporaryDatabaseContext():
        ids = seed_records()
        stale_context = context('method', ids['method'])
        material = create_qc_material(generic_name='默认资料引用质控品')
        template = create_project_template(template_name='默认资料引用', lab_instrument_id=ids['lab_instrument'], qc_material_id=material,
            default_reagent_id=ids['reagent'])
        with get_connection() as connection:
            connection.execute('UPDATE qc_project_templates SET default_method_id=? WHERE id=?', (ids['method'], template))
        assert context('method', ids['method'])['fingerprint'] == stale_context['fingerprint']
        rejected(lambda: save_master_record('method', {'method_name': '已被新项目采用后不能更名'},
            entity_id=ids['method'], expected_fingerprint=stale_context['fingerprint']), '已用于项目')
        assert context('reagent', ids['reagent'])['referenced']
        assert context('unit', ids['unit'])['referenced'], 'Test default unit also preserves unit identity'
        only_material_manufacturer = save_master_record('manufacturer', {'display_name': '仅供质控品厂家'})
        create_qc_material(generic_name='质控品引用保护', manufacturer_id=only_material_manufacturer)
        assert context('manufacturer', only_material_manufacturer)['referenced']
        reagent_lot_only = save_master_record('reagent', {'generic_name': '已有批号的试剂'})
        with get_connection() as connection:
            connection.execute("INSERT INTO md_reagent_lots(reagent_id,lot_no,expiry_date) VALUES (?,'R-001','2028-12-31')", (reagent_lot_only,))
        assert context('reagent', reagent_lot_only)['referenced']
        sourced = save_master_record('method', {'method_name': '导入来源方法学'})
        with get_connection() as connection:
            source = connection.execute('SELECT id FROM md_sources LIMIT 1').fetchone()[0]
            connection.execute("UPDATE md_methods SET origin_type='import' WHERE id=?", (sourced,))
            connection.execute("""INSERT INTO md_source_records(uid,origin_type,entity_type,entity_id,source_id,external_record_id)
                VALUES ('master-edit-source','import','method',?,?,'master-edit-method')""", (sourced, source))
        assert context('method', sourced)['referenced']
        rejected(lambda: edit('method', sourced, method_name='不可覆盖导入来源'), '不能直接修改')


def test_foreign_keys_missing_disabled_and_retained():
    with TemporaryDatabaseContext():
        ids = seed_records()
        other_manufacturer = save_master_record('manufacturer', {'display_name': '另一个停用厂家'})
        status('manufacturer', ids['manufacturer'], True)
        status('manufacturer', other_manufacturer, True)
        # Existing relationships survive notes edits, but cannot be newly chosen.
        edit('reagent', ids['reagent'], notes='原厂家已停用，资料备注仍可补充')
        assert record('reagent', ids['reagent'])['manufacturer_id'] == ids['manufacturer']
        rejected(lambda: edit('reagent', ids['reagent'], manufacturer_id=other_manufacturer), '已停用')
        rejected(lambda: save_master_record('reagent', {'generic_name': '不能新增', 'manufacturer_id': ids['manufacturer']}), '已停用')
        rejected(lambda: save_master_record('lab_instrument', {'display_name': '无效型号', 'instrument_model_id': 999999}), '不存在')
        rejected(lambda: save_master_record('lab_instrument', {'display_name': '缺少型号'}), '请选择')
        rejected(lambda: save_master_record('test_item', {'chinese_name': '无效单位', 'default_unit_id': 999999}), '不存在')
        rejected(lambda: save_master_record('test_item', {'chinese_name': '错误单位编号', 'default_unit_id': 1.5}), '有效')


def test_alias_parent_normalization_and_status():
    with TemporaryDatabaseContext():
        ids = seed_records()
        edit('alias', ids['alias'], alias_text=' ALT - 1 ', alias_type='lis_code')
        assert record('alias', ids['alias'])['normalized_alias'] == 'alt1'
        rejected(lambda: save_master_record('alias', {'entity_type': 'test_item', 'entity_id': ids['test_item'],
            'alias_text': 'alt_1', 'alias_type': 'lis_code'}), '已存在')
        other_item = save_master_record('test_item', {'chinese_name': '另一检验项目'})
        rejected(lambda: edit('alias', ids['alias'], entity_id=other_item), '不能更换')
        rejected(lambda: save_master_record('alias', {'entity_type': 'test_item', 'entity_id': 999999, 'alias_text': '不存在归属'}), '不存在')
        status('test_item', ids['test_item'], True)
        rejected(lambda: save_master_record('alias', {'entity_type': 'test_item', 'entity_id': ids['test_item'], 'alias_text': '不可新增'}), '已停用')
        edit('alias', ids['alias'], alias_text='已有别名仍能修正')
        status('alias', ids['alias'], True)
        assert record('alias', ids['alias'])['is_disabled'] == 1
        status('alias', ids['alias'], False)
        assert record('alias', ids['alias'])['is_disabled'] == 0


def test_stale_save_status_and_restore_collision():
    with TemporaryDatabaseContext():
        identifier = save_master_record('manufacturer', {'display_name': '并发及恢复测试'})
        opened = context('manufacturer', identifier)
        with get_connection() as connection:
            # Same timestamp deliberately proves field-level fingerprints catch subsecond edits.
            connection.execute('UPDATE md_manufacturers SET notes=? WHERE id=?', ('另一个窗口刚改了备注', identifier))
        assert record('manufacturer', identifier)['updated_at'] == opened['record']['updated_at']
        rejected(lambda: save_master_record('manufacturer', {'notes': '旧窗口不可覆盖'}, entity_id=identifier,
            expected_fingerprint=opened['fingerprint']), '资料已修改')
        rejected(lambda: change_master_status('manufacturer', identifier, disabled=True,
            expected_fingerprint=opened['fingerprint'], reason='旧窗口停用'), '资料已修改')
        rejected(lambda: status('manufacturer', identifier, True, reason=''), '停用原因')
        status('manufacturer', identifier, True)
        assert record('manufacturer', identifier)['disabled_reason'] == '资料核对'
        rejected(lambda: edit('manufacturer', identifier, notes='先恢复才可编辑'), '先恢复')
        duplicate = save_master_record('manufacturer', {'display_name': '并发及恢复测试'})
        rejected(lambda: status('manufacturer', identifier, False), '不能恢复')
        status('manufacturer', duplicate, True)
        status('manufacturer', identifier, False)
        assert record('manufacturer', identifier)['is_disabled'] == 0
        assert record('manufacturer', identifier)['disabled_reason'] == ''


def test_repeated_init_preserves_official_notes_and_accepts_unchanged_open_dialog():
    with TemporaryDatabaseContext():
        with get_connection() as connection:
            identifier = connection.execute("SELECT id FROM md_test_items WHERE origin_type='official' ORDER BY id LIMIT 1").fetchone()[0]
            connection.execute("UPDATE md_test_items SET updated_at='2000-01-01 00:00:00' WHERE id=?", (identifier,))
        opened = context('test_item', identifier)
        assert opened['record']['notes'].startswith('分析物：')
        init_db()
        refreshed = context('test_item', identifier)
        assert refreshed['record']['updated_at'] != opened['record']['updated_at']
        assert {field for field in refreshed['record'] if refreshed['record'][field] != opened['record'][field]} == {'updated_at'}
        assert refreshed['fingerprint'] == opened['fingerprint']
        local_notes = '本实验室补充备注\n保留换行与具体操作依据'
        save_master_record('test_item', {'notes': local_notes}, entity_id=identifier,
            expected_fingerprint=opened['fingerprint'])
        saved = context('test_item', identifier)
        init_db()
        init_db()
        repeated = context('test_item', identifier)
        assert repeated['record']['notes'] == local_notes
        assert repeated['fingerprint'] == saved['fingerprint']
        assert all(repeated['record'][field] == opened['record'][field]
                   for field in ('standard_code', 'chinese_name', 'category_name', 'specimen_type', 'result_type'))
        # Notes remain business data: another edit invalidates both stale save and status.
        edit('test_item', identifier, notes='另一窗口的新备注')
        rejected(lambda: save_master_record('test_item', {'notes': '不能覆盖'}, entity_id=identifier,
            expected_fingerprint=saved['fingerprint']), '资料已修改')
        rejected(lambda: change_master_status('test_item', identifier, disabled=True,
            expected_fingerprint=saved['fingerprint'], reason='旧窗口停用'), '资料已修改')


def test_only_official_test_item_seed_timestamp_is_excluded():
    with TemporaryDatabaseContext():
        identifiers = seed_records()
        for kind in ('test_item', 'manufacturer', 'method', 'unit'):
            identifier = identifiers[kind]
            opened = context(kind, identifier)
            from services.master_data_service import MASTER_ENTITY_TABLES
            with get_connection() as connection:
                connection.execute(f"UPDATE {MASTER_ENTITY_TABLES[kind]} SET updated_at='2000-01-01 00:00:00' WHERE id=?", (identifier,))
            assert context(kind, identifier)['fingerprint'] != opened['fingerprint']


if __name__ == '__main__':
    tests = [test_local_edit_round_trip_and_duplicates, test_official_identity_and_local_notes,
             test_project_references_and_history_unchanged, test_default_fields_materials_and_new_references_lock,
             test_foreign_keys_missing_disabled_and_retained, test_alias_parent_normalization_and_status,
             test_stale_save_status_and_restore_collision,
             test_repeated_init_preserves_official_notes_and_accepts_unchanged_open_dialog,
             test_only_official_test_item_seed_timestamp_is_excluded]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'All {len(tests)} master data edit smoke tests passed.')
