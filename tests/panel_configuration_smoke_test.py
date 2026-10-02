"""B2 full-panel drafts, exact exchange, rollback and real dialog checks."""
from pathlib import Path
import sys
import json
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.master_data_service import create_test_item, create_manufacturer
from services.project_config_service import (
    create_project_template, get_project_template, list_template_items, template_item_rows,
    save_template_items, preview_panel_items, preview_panel_defaults, save_panel_items,
    validate_project_template, activate_project_template,
)
from services.project_config_io_service import (
    PROJECT_IMPORT_COLUMNS, build_project_template_xlsx, prepare_project_template_import,
    import_project_template_xlsx, preview_project_template_xlsx,
)
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.product_directory_service import get_product_relationships, save_product_coverage, export_material_catalog_context
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.quality_review_fixtures import confirm_fixture_project


def seed(name='20项组合'):
    d = _seed_v11_configuration_dependencies()
    tid = create_project_template(template_name=name, lab_instrument_id=d['lab_instrument_id'],
        qc_material_id=d['qc_material_id'], default_reagent_id=d['reagent_id'], default_method_id=d['method_id'])
    rows = []
    choices = [('lj', 'raw', 1), ('lj', 'ct', 1), ('zscore', 'raw', 2), ('zscore', 'log', 3), ('instant', 'ct', 1)]
    for index in range(20):
        item_id = create_test_item(chinese_name=f'组合检验{index + 1:02d}', standard_code=f'PANEL-{index + 1:02d}')
        method, scale, levels = choices[index % len(choices)]
        rows.append(dict(test_item_id=item_id, qc_method=method, input_value_type=scale, level_count=levels,
            unit_id=d['unit_id'], method_id=d['method_id'], reagent_id=d['reagent_id'], target_n=20,
            notes=f'原始备注{index + 1}', quality_target_source_text=''))
    return d, tid, rows


def counts():
    with get_connection() as connection:
        return {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in (
            'md_test_items', 'md_units', 'md_methods', 'md_manufacturers', 'md_reagents',
            'qc_project_template_items', 'md_product_coverage', 'md_product_coverage_history')}


def fails(call, text):
    try:
        call()
    except (ValueError, RuntimeError) as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError('Expected rejection: ' + text)


def test_twenty_item_panel_full_set_and_quality_gate():
    with TemporaryDatabaseContext():
        d, tid, rows = seed()
        save_template_items(tid, rows[:2])
        confirm_fixture_project(tid)
        old = template_item_rows(tid)
        before = counts()
        revision = get_project_template(tid)['revision_no']
        preview = preview_panel_items(tid, [dict(rows[0], notes='不可覆盖已有备注'), *rows[2:]], expected_revision=revision)
        assert counts() == before and len(preview['rows']) == 20
        assert preview['added_count'] == 18 and preview['retained_count'] == 2
        assert preview['rows'][0]['notes'] == '原始备注1'
        assert preview['rows'][0]['quality_review_json'] == old[0]['quality_review_json']
        result = save_panel_items(tid, preview['rows'], expected_revision=revision)
        assert result['saved_count'] == 20 and get_project_template(tid)['status'] == 'draft'
        assert len(list_template_items(tid)) == 20 and result['errors']
        assert '第 3 项' in '\n'.join(result['errors'])
        fails(lambda: activate_project_template(tid), '质量')
        fails(lambda: save_panel_items(tid, preview['rows'], expected_revision=revision), '已发生变化')
        repeated = preview_panel_items(tid, rows)
        assert repeated['added_count'] == 0 and len(repeated['rows']) == 20
        # Choosing known assays by name carries only assay identity. A common
        # LJ/raw default must never create 17 additional variants of a mixed panel.
        manual_selection = [dict(row, qc_method='lj', input_value_type='raw', level_count=1) for row in rows]
        manual_preview = preview_panel_items(tid, manual_selection)
        assert manual_preview['added_count'] == 0 and manual_preview['retained_count'] == 20
        assert len(manual_preview['rows']) == 20
        assert [(r['qc_method'], r['input_value_type']) for r in manual_preview['rows']] == [
            (r['qc_method'], r['input_value_type']) for r in rows]
        confirm_fixture_project(tid)
        activate_project_template(tid)
        assert not validate_project_template(tid)
        actual = template_item_rows(tid)
        assert [(r['qc_method'], r['level_count'], r['input_value_type']) for r in actual] == [
            (r['qc_method'], r['level_count'], r['input_value_type']) for r in rows]


def test_selected_defaults_are_pure_and_do_not_rewrite_other_rows():
    with TemporaryDatabaseContext():
        _, tid, rows = seed()
        original = preview_panel_items(tid, rows)['rows']
        original[1]['method_id'] = None
        selected = [original[0]['row_key'], original[1]['row_key']]
        proposed = preview_panel_defaults(original, {'method_id': 333, 'target_n': 15},
            selected_row_keys=selected, fields=['method_id', 'target_n'])
        assert len(proposed['changes']) == 1 and original[1]['method_id'] is None
        overwrite = preview_panel_defaults(original, {'target_n': 15}, selected_row_keys=selected,
                                          fields=['target_n'], overwrite=True)
        assert len(overwrite['changes']) == 2
        assert overwrite['rows'][2] == original[2]
        fails(lambda: preview_panel_defaults(original, {'cv_limit': 5}, selected_row_keys=selected,
                                             fields=['cv_limit'], overwrite=True), '质量要求')
        invalid = [dict(rows[0], level_count=1.5)]
        fails(lambda: save_template_items(tid, invalid), '整数')


def import_file(n=20, wrong_manufacturer=None):
    values = []
    for index in range(n):
        values.append([f'导入项目{index+1}', '', f'IMPORT-{index+1}', 'LJ', '真实检测值', '导入单位',
            '导入方法', wrong_manufacturer if index == 18 and wrong_manufacturer else '导入试剂厂家',
            '导入试剂', '', 1, 20, '', '', f'导入备注{index+1}'])
    return dataframes_to_xlsx_bytes({'项目配置': pd.DataFrame(values, columns=PROJECT_IMPORT_COLUMNS)})


def test_import_preview_revision_hash_replace_and_atomic_failure():
    with TemporaryDatabaseContext():
        _, tid, rows = seed()
        save_template_items(tid, rows[:2])
        before = counts()
        payload = import_file()
        prepared = prepare_project_template_import(tid, payload, mode='replace')
        assert counts() == before and not prepared['errors'] and len(prepared['removals']) == 2
        args = dict(mode='replace', expected_revision=prepared['expected_revision'], file_sha256=prepared['file_sha256'])
        fails(lambda: import_project_template_xlsx(tid, payload, **args), '再次确认')
        fails(lambda: import_project_template_xlsx(tid, import_file(19), **args, replace_confirmed=True), '文件已变化')
        import services.project_config_io_service as io
        original, attempts = io.create_test_item, []
        def fail_late(**kwargs):
            attempts.append(kwargs['chinese_name'])
            if len(attempts) == 19:
                raise ValueError('模拟第19行保存失败')
            return original(**kwargs)
        with patch.object(io, 'create_test_item', side_effect=fail_late):
            fails(lambda: import_project_template_xlsx(tid, payload, **args, replace_confirmed=True), '第 20 行')
        assert len(attempts) == 20 and counts() == before
        assert get_project_template(tid)['revision_no'] == prepared['expected_revision']
        save_template_items(tid, rows[:2])
        fails(lambda: import_project_template_xlsx(tid, payload, **args, replace_confirmed=True), '目标项目已发生变化')
        create_manufacturer(display_name='仅仪器厂家', categories=['instrument'])
        bad_before = counts()
        wrong = import_file(wrong_manufacturer='仅仪器厂家')
        bad = prepare_project_template_import(tid, wrong)
        assert any('第 20 行' in e and '试剂' in e for e in bad['errors'])
        fails(lambda: import_project_template_xlsx(tid, wrong), '第 20 行')
        assert counts() == bad_before


def test_xlsx_cross_database_identity_coverage_and_repeat():
    with TemporaryDatabaseContext():
        d, tid, rows = seed()
        save_template_items(tid, rows)
        product_id = d['qc_material_id']
        save_product_coverage(product_id, [{'test_item_id': r['test_item_id'], 'method_id': r['method_id']} for r in rows],
            expected_version=get_product_relationships(product_id)['edit_version'], confirmed_by='检验人员', evidence='已核对20项说明')
        context = export_material_catalog_context(product_id)
        payload = build_project_template_xlsx(tid)
        expected = [(r['qc_method'], r['input_value_type'], r['level_count'], r['notes']) for r in template_item_rows(tid)]
        source_ids = [r['test_item_id'] for r in rows]
    with TemporaryDatabaseContext():
        d = _seed_v11_configuration_dependencies()
        for i in range(4):
            create_test_item(chinese_name=f'目标库独立项目{i}')
        # The chosen local product was explicitly identified by its retained UID;
        # real catalog products use the fixed source/product external key.
        with get_connection() as connection:
            connection.execute('UPDATE md_qc_materials SET uid=? WHERE id=?', (context['product']['uid'], d['qc_material_id']))
            connection.execute('UPDATE md_manufacturers SET uid=? WHERE id=?', (context['manufacturer']['uid'], connection.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?', (d['qc_material_id'],)).fetchone()[0]))
        tid = create_project_template(template_name='目标20项', lab_instrument_id=d['lab_instrument_id'], qc_material_id=d['qc_material_id'])
        before = counts()
        prepared = prepare_project_template_import(tid, payload)
        assert not prepared['errors'], prepared['errors']
        assert counts() == before
        result = import_project_template_xlsx(tid, payload, expected_revision=prepared['expected_revision'],
            file_sha256=prepared['file_sha256'], product_version=prepared['product_version'])
        assert result['saved_count'] == 20
        actual = template_item_rows(tid)
        assert [(r['qc_method'], r['input_value_type'], r['level_count'], r['notes']) for r in actual] == expected
        assert [r['test_item_id'] for r in actual] != source_ids
        relation = get_product_relationships(d['qc_material_id'])
        assert len(relation['coverage']) == 20
        assert all(r['test_item_id'] in [i['test_item_id'] for i in actual] for r in relation['coverage'])
        assert all(r['method_id'] == d['method_id'] for r in relation['coverage'])
        assert validate_project_template(tid) and get_project_template(tid)['status'] == 'draft'
        count_after = counts()
        import_project_template_xlsx(tid, payload)
        assert counts() == count_after
        sheets = xlsx_bytes_to_dataframes(payload)
        sheets['项目配置'].loc[0, '方法学*'] = '篡改方法'
        bad = prepare_project_template_import(tid, dataframes_to_xlsx_bytes(sheets))
        assert any('方法学标识' in e for e in bad['errors'])


def test_duplicate_identity_and_blank_row_numbers():
    payload = import_file(2)
    sheets = xlsx_bytes_to_dataframes(payload)
    row = sheets['项目配置'].iloc[0].copy()
    same_test = row.copy()
    same_test['质控方法*'], same_test['水平数*'] = 'Z-score', 2
    sheets['项目配置'] = pd.DataFrame([row.to_dict(), {key:'' for key in PROJECT_IMPORT_COLUMNS}, same_test.to_dict(), row.to_dict()])
    preview, errors = preview_project_template_xlsx(dataframes_to_xlsx_bytes(sheets))
    assert len(preview) == 2 and preview['文件行号'].tolist() == [2, 4]
    assert len(errors) == 1 and '第 5 行' in errors[0]


def test_merge_keeps_same_item_other_explicit_method_and_scale():
    with TemporaryDatabaseContext():
        _, tid, rows = seed()
        alternate = dict(rows[0], qc_method='zscore', level_count=2, input_value_type='log', notes='必须保留')
        save_template_items(tid, [rows[0], alternate])
        sheets = xlsx_bytes_to_dataframes(build_project_template_xlsx(tid))
        sheets['项目配置'] = sheets['项目配置'].iloc[:1].copy()
        sheets['项目配置'].loc[0, '备注'] = '仅更新LJ'
        import_project_template_xlsx(tid, dataframes_to_xlsx_bytes(sheets))
        actual = template_item_rows(tid)
        assert len(actual) == 2 and actual[0]['notes'] == '仅更新LJ' and actual[1]['notes'] == '必须保留'


def test_twenty_item_lot_export_keeps_actual_mixed_lots():
    from tests.daily_entry_smoke_test import seed_twenty
    from services.project_config_io_service import build_lot_config_xlsx
    from services.project_config_service import list_lot_config_items, list_lot_item_levels
    with TemporaryDatabaseContext():
        data = seed_twenty()
        sheets = xlsx_bytes_to_dataframes(build_lot_config_xlsx(data['config_id']))
        actual = sheets['水平均值和标准差']
        expected = []
        for item in list_lot_config_items(data['config_id']).to_dict('records'):
            expected.extend(list_lot_item_levels(item['id']).to_dict('records'))
        assert len(actual) == len(expected) and len(set(actual['实际批号'])) == 3
        assert actual['实际批号'].tolist() == [row['lot_no'] for row in expected]
        assert actual['材料批次标识'].notna().all() and actual['材料水平标识'].notna().all()
        assert '产品来源' in sheets and '产品覆盖' in sheets


def panel_page(template_id):
    import streamlit as st
    from ui.project_dialogs import open_project_dialog, render_pending_project_dialog
    if st.button('开始整组', key='start_panel'):
        open_project_dialog('panel', template_id)
    render_pending_project_dialog()


def test_real_panel_dialog_preview_cancel_and_save():
    with TemporaryDatabaseContext():
        d, source, rows = seed()
        save_template_items(source, rows)
        target = create_project_template(template_name='目标草稿', lab_instrument_id=d['lab_instrument_id'], qc_material_id=d['qc_material_id'])
        app = AppTest.from_function(panel_page, args=(target,), default_timeout=20).run()
        app.button(key='start_panel').click().run()
        assert not app.exception
        token = app.session_state['project_workspace_dialog']['token']
        app.selectbox(key='panel_source_template_' + token).select(source).run()
        app.button(key='panel_source_preview').click().run()
        assert not app.exception and list_template_items(target).empty
        assert len(app.session_state['project_workspace_dialog']['draft']['rows']) == 20
        app.button(key='panel_edit_cancel').click().run()
        app.button(key='project_continue_edit').click().run()
        assert not app.exception and list_template_items(target).empty
        editor = next(element for element in app.dataframe if element.proto.id)
        state = app._tree.get_widget_states()
        edit = state.widgets.add()
        edit.id = editor.proto.id
        edit.string_value = json.dumps({'edited_rows': {'0': {'备注': '逐项编辑保留', '水平数': 2, '质控方法': '多水平法'}},
                                       'added_rows': [], 'deleted_rows': []})
        app._run(state)
        assert not app.exception
        assert app.session_state['project_workspace_dialog']['draft']['rows'][0]['notes'] == '逐项编辑保留'
        app.button(key='panel_save').click().run()
        assert not app.exception and len(list_template_items(target)) == 20
        assert get_project_template(target)['status'] == 'draft'
        assert template_item_rows(target)[0]['qc_method'] == 'zscore'
        assert template_item_rows(target)[0]['notes'] == '逐项编辑保留'


if __name__ == '__main__':
    tests = [value for key, value in list(globals().items()) if key.startswith('test_') and callable(value)]
    for test in tests:
        test()
        print('PASS', test.__name__)
    print(f'All {len(tests)} panel configuration tests passed.')
