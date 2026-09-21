"""Temporary-database checks for reference-record dialogs and explicit saves."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection
from services.master_data_edit_service import get_master_record_context, save_master_record
from services.master_data_service import (
    create_alias, create_instrument_model, create_manufacturer, create_method, create_reagent,
    create_test_item, create_unit, list_test_items, set_master_entity_disabled,
)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


def make_app(actions):
    return AppTest.from_string(f'''
import streamlit as st
from ui.master_data_dialogs import open_master_data_dialog, render_pending_master_data_dialog
actions = {actions!r}
for name, arguments in actions.items():
    if st.button(name, key='open_' + name):
        open_master_data_dialog(**arguments)
render_pending_master_data_dialog()
''', default_timeout=30).run()


def open_dialog(app, action='record'):
    app.button(key='open_' + action).click().run()
    assert not app.exception
    return app


def field_key(app, field):
    return 'md_dialog_' + app.session_state['master_data_dialog']['token'] + '_' + field


def fill(app, values):
    for field, value in values.items():
        key = field_key(app, field)
        if field in ('manufacturer_id', 'instrument_model_id', 'default_unit_id', 'alias_type'):
            app.selectbox(key=key).set_value(value)
        elif field == 'notes':
            app.text_area(key=key).set_value(value)
        else:
            app.text_input(key=key).set_value(value)
    return app


def snapshot():
    with get_connection() as connection:
        return tuple(connection.iterdump())


def assert_closed(app):
    assert not app.exception and 'master_data_dialog' not in app.session_state.filtered_state


def test_create_and_edit_all_eight_entities_preserves_every_field():
    with IsolatedDatabase():
        manufacturer = create_manufacturer(display_name='弹窗依赖厂家')
        unit = create_unit(symbol='UI-test/L', unit_name='弹窗单位')
        model = create_instrument_model(manufacturer_id=manufacturer, generic_name='检验仪', model='DEPEND-001')
        parent = create_test_item(chinese_name='弹窗别名归属项目')
        cases = {
            'manufacturer': dict(display_name='弹窗新增厂家', legal_name='弹窗厂家有限公司', country_or_region='中国',
                registration_holder_name='弹窗登记人', notes='厂家备注'),
            'unit': dict(symbol='UI-dialog/L', unit_name='界面验收单位', ucum_code='UI/L', quantity_kind='浓度', notes='单位备注'),
            'test_item': dict(chinese_name='弹窗检验项目', standard_code='UI-TEST-001', abbreviation='UI-T',
                english_name='UI test', category_name='免疫', specimen_type='血清', default_unit_id=unit, notes='项目备注'),
            'instrument_model': dict(manufacturer_id=manufacturer, generic_name='弹窗检验仪', brand_name='弹窗品牌',
                model='UI-MODEL', registration_no='UI-REG-001', device_category_code='22', catalog_no='UI-CAT', notes='型号备注'),
            'lab_instrument': dict(instrument_model_id=model, display_name='弹窗 1 号仪器', asset_code='ASSET-1',
                serial_number='SN-1', department_name='免疫室', instrument_group='常规组', location='二楼', notes='仪器备注'),
            'reagent': dict(manufacturer_id=manufacturer, generic_name='弹窗试剂', trade_name='示例试剂', specification='100次',
                registration_no='REG-R', catalog_no='CAT-R', applicable_instrument_text='弹窗检验仪', notes='试剂备注'),
            'method': dict(method_name='弹窗方法学', method_code='UI-M', method_category='免疫', principle='免疫反应', notes='方法备注'),
            'alias': dict(alias_text='弹窗项目别名', alias_type='lis_code'),
        }
        for entity, values in cases.items():
            app = open_dialog(make_app({'record': dict(entity_type=entity, parent_id=parent if entity == 'alias' else None)}))
            before = snapshot()
            fill(app, values).run()  # Typing / committing input alone must never submit.
            assert not app.exception and snapshot() == before
            app.button(key='md_dialog_save').click().run()
            assert_closed(app)
            saved_id = app.session_state['md_selected_' + entity]
            record = get_master_record_context(entity, saved_id)['record']
            assert all(record[key] == value for key, value in values.items()), (entity, record)
            assert app.session_state['md_saved_entity'] == (entity, saved_id)
            assert app.session_state['md_table_version'] == 1 and app.session_state['md_notice']
            if entity == 'alias':
                assert (record['entity_type'], record['entity_id']) == ('test_item', parent)
            editable_field = 'alias_text' if entity == 'alias' else 'notes'
            edited = open_dialog(make_app({'record': dict(entity_type=entity, entity_id=saved_id)}))
            if entity != 'alias':
                assert edited.text_area(key=field_key(edited, 'notes')).value == values['notes']
            fill(edited, {editable_field: '编辑后保留的内容'})
            edited.button(key='md_dialog_save').click().run()
            assert_closed(edited)
            after = get_master_record_context(entity, saved_id)['record']
            assert after[editable_field] == '编辑后保留的内容'
            assert all(after[key] == value for key, value in values.items() if key != editable_field)


def test_failed_save_and_cancel_continue_keep_draft_then_reopen_cleanly():
    with IsolatedDatabase():
        app = open_dialog(make_app({'record': dict(entity_type='manufacturer')}))
        before = snapshot()
        fill(app, {'registration_holder_name': '待完善登记人', 'notes': '不能丢的草稿'})
        app.button(key='md_dialog_save').click().run()
        assert not app.exception and app.error and snapshot() == before
        assert app.text_area(key=field_key(app, 'notes')).value == '不能丢的草稿'
        fill(app, {'display_name': '继续编辑厂家'})
        app.button(key='md_dialog_cancel').click().run()
        assert not app.exception and app.warning and snapshot() == before
        app.button(key='md_dialog_continue').click().run()
        assert app.text_input(key=field_key(app, 'display_name')).value == '继续编辑厂家'
        assert app.text_area(key=field_key(app, 'notes')).value == '不能丢的草稿'
        app.button(key='md_dialog_cancel').click().run()
        app.button(key='md_dialog_discard').click().run()
        assert_closed(app)
        assert snapshot() == before
        open_dialog(app)
        assert app.text_input(key=field_key(app, 'display_name')).value == ''
        assert app.text_area(key=field_key(app, 'notes')).value == ''


def test_status_requires_confirmation_keeps_history_and_disabled_edit_is_readonly():
    with IsolatedDatabase():
        identifier = create_manufacturer(display_name='停用恢复厂家', notes='完整备注')
        app = make_app({'status': dict(entity_type='manufacturer', entity_id=identifier, kind='status'),
                        'edit': dict(entity_type='manufacturer', entity_id=identifier)})
        before = snapshot()
        open_dialog(app, 'status')
        assert snapshot() == before
        app.button(key='md_status_confirm').click().run()
        assert not app.exception and app.error and snapshot() == before
        app.text_area(key=field_key(app, 'reason')).set_value('阶段性停用').run()
        assert snapshot() == before
        app.button(key='md_status_cancel').click().run()
        assert_closed(app)
        assert snapshot() == before
        open_dialog(app, 'status')
        app.text_area(key=field_key(app, 'reason')).set_value('阶段性停用')
        app.button(key='md_status_confirm').click().run()
        assert_closed(app)
        record = get_master_record_context('manufacturer', identifier)['record']
        assert record['is_disabled'] and record['disabled_reason'] == '阶段性停用' and record['notes'] == '完整备注'
        open_dialog(app, 'edit')
        assert app.text_input(key=field_key(app, 'display_name')).disabled
        assert app.text_area(key=field_key(app, 'notes')).disabled
        assert not any(button.key == 'md_dialog_save' for button in app.button)
        app.button(key='md_dialog_restore').click().run()
        assert get_master_record_context('manufacturer', identifier)['record']['is_disabled']
        app.button(key='md_status_confirm').click().run()
        assert_closed(app)
        restored = get_master_record_context('manufacturer', identifier)['record']
        assert not restored['is_disabled'] and restored['notes'] == '完整备注'
        assert app.session_state['md_selected_manufacturer'] == identifier


def test_official_and_referenced_names_are_locked_but_notes_remain_editable():
    with IsolatedDatabase():
        manufacturer = create_manufacturer(display_name='已使用厂家', notes='原备注')
        create_reagent(manufacturer_id=manufacturer, generic_name='已登记试剂')
        official = int(list_test_items().loc[lambda rows: rows.origin_type == 'official', 'id'].iloc[0])
        for entity, identifier, field in [('manufacturer', manufacturer, 'display_name'), ('test_item', official, 'chinese_name')]:
            before = get_master_record_context(entity, identifier)['record']
            app = open_dialog(make_app({'record': dict(entity_type=entity, entity_id=identifier)}))
            assert app.text_input(key=field_key(app, field)).disabled
            assert not app.text_area(key=field_key(app, 'notes')).disabled
            fill(app, {'notes': '只补充备注'})
            app.button(key='md_dialog_save').click().run()
            assert_closed(app)
            after = get_master_record_context(entity, identifier)['record']
            assert after[field] == before[field] and after['notes'] == '只补充备注'


def test_dropdowns_preserve_existing_disabled_relation_without_offering_other_disabled_records():
    with IsolatedDatabase():
        original = create_manufacturer(display_name='原停用厂家')
        unrelated = create_manufacturer(display_name='其他停用厂家')
        current = create_manufacturer(display_name='可用厂家')
        reagent = create_reagent(manufacturer_id=original, generic_name='停用关联试剂', notes='原备注')
        set_master_entity_disabled('manufacturer', original, is_disabled=True, reason='关联保留')
        set_master_entity_disabled('manufacturer', unrelated, is_disabled=True, reason='不供新增选择')
        app = open_dialog(make_app({'record': dict(entity_type='reagent', entity_id=reagent)}))
        selection = app.selectbox(key=field_key(app, 'manufacturer_id'))
        assert selection.value == original
        assert any('原停用厂家' in label and '已停用' in label for label in selection.options)
        assert not any('其他停用厂家' in label for label in selection.options)
        fill(app, {'notes': '保留旧厂家继续补充备注'})
        app.button(key='md_dialog_save').click().run()
        assert_closed(app)
        assert get_master_record_context('reagent', reagent)['record']['manufacturer_id'] == original
        added = open_dialog(make_app({'record': dict(entity_type='reagent')}))
        selection = added.selectbox(key=field_key(added, 'manufacturer_id'))
        assert not any('停用厂家' in label for label in selection.options)
        selection.set_value(current)
        fill(added, {'generic_name': '使用启用厂家试剂'})
        added.button(key='md_dialog_save').click().run()
        assert_closed(added)
        assert get_master_record_context('reagent', added.session_state['md_selected_reagent'])['record']['manufacturer_id'] == current


def test_stale_edit_and_status_cannot_overwrite_updated_record():
    with IsolatedDatabase():
        identifier = create_method(method_name='并发编辑方法学', notes='原备注')
        for kind in ('edit', 'status'):
            app = open_dialog(make_app({'record': dict(entity_type='method', entity_id=identifier, kind=kind)}))
            if kind == 'edit':
                fill(app, {'notes': '过时弹窗内容'})
            else:
                app.text_area(key=field_key(app, 'reason')).set_value('过时停用原因')
            current = get_master_record_context('method', identifier)
            save_master_record('method', {'notes': '其他窗口已保存 ' + kind}, entity_id=identifier,
                               expected_fingerprint=current['fingerprint'])
            before = snapshot()
            app.button(key='md_dialog_save' if kind == 'edit' else 'md_status_confirm').click().run()
            assert not app.exception and app.error and snapshot() == before
            assert any('已修改' in error.value for error in app.error)
            if kind == 'edit':
                assert app.text_area(key=field_key(app, 'notes')).value == '过时弹窗内容'
            else:
                assert app.text_area(key=field_key(app, 'reason')).value == '过时停用原因'


def test_consecutive_records_and_alias_parent_never_share_drafts():
    with IsolatedDatabase():
        first = create_manufacturer(display_name='厂家甲', notes='甲备注')
        second = create_manufacturer(display_name='厂家乙', notes='乙备注')
        parent = create_test_item(chinese_name='别名归属甲')
        other = create_test_item(chinese_name='别名归属乙')
        alias = create_alias(entity_type='test_item', entity_id=parent, alias_text='旧别名', alias_type='historical')
        app = make_app({'first': dict(entity_type='manufacturer', entity_id=first),
                        'second': dict(entity_type='manufacturer', entity_id=second),
                        'method': dict(entity_type='method'),
                        'alias': dict(entity_type='alias', entity_id=alias, parent_id=other)})
        open_dialog(app, 'first')
        token = app.session_state['master_data_dialog']['token']
        fill(app, {'notes': '甲窗口未保存草稿'})
        app.button(key='md_dialog_cancel').click().run()
        app.button(key='md_dialog_discard').click().run()
        open_dialog(app, 'second')
        assert app.session_state['master_data_dialog']['token'] != token
        assert app.text_input(key=field_key(app, 'display_name')).value == '厂家乙'
        assert app.text_area(key=field_key(app, 'notes')).value == '乙备注'
        app.button(key='md_dialog_cancel').click().run()
        open_dialog(app, 'method')
        assert app.text_input(key=field_key(app, 'method_name')).value == ''
        assert app.text_area(key=field_key(app, 'notes')).value == ''
        app.button(key='md_dialog_cancel').click().run()
        open_dialog(app, 'alias')
        assert any('别名归属甲' in caption.value for caption in app.caption)
        assert app.selectbox(key=field_key(app, 'alias_type')).value == 'historical'
        fill(app, {'alias_text': '新别名', 'alias_type': 'short_name'})
        app.button(key='md_dialog_save').click().run()
        assert_closed(app)
        record = get_master_record_context('alias', alias)['record']
        assert record['entity_id'] == parent and record['alias_text'] == '新别名'
        assert record['normalized_alias'] == '新别名' and record['alias_type'] == 'short_name'


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
