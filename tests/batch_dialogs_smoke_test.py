"""Batch dialogs preserve unsaved values and save only the selected item's settings."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.batch_edit_service import get_batch_item_context
from services.master_data_service import create_test_item
from services.material_workflow_service import create_material_config
from services.project_config_service import (
    activate_lot_config, activate_project_template, create_project_template,
    get_lot_config, list_lot_config_items, list_lot_item_levels, list_template_items,
    save_lot_item_levels, save_template_items,
)
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project


def seed_draft():
    data = _seed_v11_configuration_dependencies()
    instant_test = create_test_item(chinese_name='批次弹窗即时法测试', default_unit_id=data['unit_id'])
    tid = create_project_template(template_name='批次弹窗测试', lab_instrument_id=data['lab_instrument_id'],
        qc_material_id=data['qc_material_id'], default_reagent_id=data['reagent_id'])
    definitions = [('lj', data['lj_item_id'], 1), ('zscore', data['zscore_item_id'], 3), ('instant', instant_test, 1)]
    save_template_items(tid, [dict(test_item_id=test, qc_method=method, input_value_type='raw',
        unit_id=data['unit_id'], method_id=data['method_id'], reagent_id=data['reagent_id'],
        level_count=count, target_n=20, cv_limit=5.0) for method, test, count in definitions])
    confirm_fixture_project(tid)
    activate_project_template(tid)
    templates = {row['qc_method']: row for row in list_template_items(tid).to_dict('records')}
    levels = data['source_levels']
    config_id = create_material_config(template_id=tid, config_name='待确认的批次', selections={
        templates['lj']['id']: [levels[0]], templates['zscore']['id']: levels,
        templates['instant']['id']: [levels[2]],
    })
    items = {row['qc_method']: int(row['id']) for row in list_lot_config_items(config_id).to_dict('records')}
    save_lot_item_levels(items['lj'], [dict(qc_level_id=levels[0], target_source='manual',
        target_mean=100.0, target_sd=2.0, target_confirmed=True, notes='原单水平说明')])
    save_lot_item_levels(items['zscore'], [dict(qc_level_id=lid, target_source=source,
        target_mean=200.0+index*100, target_sd=3.0+index, target_confirmed=source!='copied_pending',
        notes=f'原说明 {lid}') for index,(lid,source) in enumerate(zip(levels, ['manufacturer','manual','copied_pending']))])
    return dict(config_id=config_id, items=items, levels=levels)


def dialog_page(items):
    import streamlit as st
    from ui.batch_dialogs import open_batch_item_dialog, render_batch_item_dialog
    for item_id in items:
        if st.button('水平设置', key=f'open_levels_{item_id}'):
            open_batch_item_dialog(item_id)
        if st.button('质量目标', key=f'open_quality_{item_id}'):
            open_batch_item_dialog(item_id, kind='quality')
    render_batch_item_dialog()


def run_app(fixture):
    app = AppTest.from_function(dialog_page, args=(list(fixture['items'].values()),), default_timeout=15).run()
    assert_clean(app)
    return app


def assert_clean(app):
    assert not list(app.exception), [str(error) for error in app.exception]


def key(app, field, level_id=None):
    prefix = 'batch_level_' + app.session_state['batch_item_dialog']['token'] + '_'
    return prefix + (f'{level_id}_' if level_id is not None else '') + field


def test_cancel_and_failed_save_preserve_values_and_original_records():
    with TemporaryDatabaseContext():
        fixture = seed_draft()
        item = fixture['items']['lj']; level = fixture['levels'][0]
        before = list_lot_item_levels(item).to_json(orient='records')
        revision = get_lot_config(fixture['config_id'])['revision_no']
        app = run_app(fixture)
        app.button(key=f'open_levels_{item}').click().run()
        assert_clean(app)
        app.number_input(key=key(app, 'mean', level)).set_value(110.0)
        app.text_area(key=key(app, 'notes', level)).set_value('尚未保存的说明')
        app.button(key='batch_levels_cancel').click().run()
        assert_clean(app)
        assert list_lot_item_levels(item).to_json(orient='records') == before
        app.button(key='batch_levels_continue').click().run()
        assert app.number_input(key=key(app, 'mean', level)).value == 110.0
        assert app.text_area(key=key(app, 'notes', level)).value == '尚未保存的说明'
        app.number_input(key=key(app, 'cv')).set_value(0.0)
        app.button(key='batch_levels_save').click().run()
        assert_clean(app)
        assert list(app.error)
        assert list_lot_item_levels(item).to_json(orient='records') == before
        assert get_lot_config(fixture['config_id'])['revision_no'] == revision
        assert app.number_input(key=key(app, 'mean', level)).value == 110.0
        app.number_input(key=key(app, 'cv')).set_value(2.25)
        app.text_input(key=key(app, 'cv_source')).set_value('本批允许不精密度依据')
        app.button(key='batch_levels_save').click().run()
        assert_clean(app)
        row = list_lot_item_levels(item).iloc[0]
        assert row.target_mean == 110 and row.target_sd == 2 and row.target_confirmed
        assert row.notes == '尚未保存的说明'
        assert get_batch_item_context(item)['item']['cv_limit'] == 2.25
        assert 'batch_item_dialog' not in app.session_state


def test_level_selection_order_and_per_material_values_survive_reorder():
    with TemporaryDatabaseContext():
        fixture = seed_draft(); item = fixture['items']['zscore']
        low, middle, high = fixture['levels']
        app = run_app(fixture)
        app.button(key=f'open_levels_{item}').click().run()
        app.number_input(key=key(app, 'mean', low)).set_value(222.0).run()
        app.multiselect(key=key(app, 'selected')).set_value([high, low, middle]).run()
        assert_clean(app)
        assert app.number_input(key=key(app, 'mean', low)).value == 222.0
        assert app.number_input(key=key(app, 'mean', high)).value == 400.0
        assert app.selectbox(key=key(app, 'source', high)).value == 'copied_pending'
        assert not app.checkbox(key=key(app, 'confirmed', high)).value
        app.button(key='batch_levels_save').click().run()
        assert_clean(app)
        rows = list_lot_item_levels(item).to_dict('records')
        assert [row['qc_level_id'] for row in rows] == [high, low, middle]
        assert [row['level_order'] for row in rows] == [1, 2, 3]
        assert [row['target_mean'] for row in rows] == [400, 222, 300]
        assert [row['target_source'] for row in rows] == ['copied_pending', 'manufacturer', 'manual']
        assert [row['notes'] for row in rows] == [f'原说明 {lid}' for lid in [high, low, middle]]


def test_reopen_and_switch_items_do_not_reuse_unsaved_values():
    with TemporaryDatabaseContext():
        fixture = seed_draft(); low = fixture['levels'][0]
        app = run_app(fixture)
        lj = fixture['items']['lj']; instant = fixture['items']['instant']
        app.button(key=f'open_levels_{lj}').click().run()
        old_token = app.session_state['batch_item_dialog']['token']
        app.number_input(key=key(app, 'mean', low)).set_value(999.0)
        app.button(key='batch_levels_cancel').click().run()
        app.button(key='batch_levels_discard').click().run()
        app.button(key=f'open_levels_{instant}').click().run()
        assert_clean(app)
        high = fixture['levels'][2]
        assert app.selectbox(key=key(app, 'source', high)).value == 'building'
        assert app.number_input(key=key(app, 'mean', high)).value is None
        app.text_area(key=key(app, 'notes', high)).set_value('即时法说明')
        app.button(key='batch_levels_save').click().run()
        assert_clean(app)
        assert list_lot_item_levels(instant).iloc[0]['notes'] == '即时法说明'
        app.button(key=f'open_levels_{lj}').click().run()
        assert app.session_state['batch_item_dialog']['token'] != old_token
        assert app.number_input(key=key(app, 'mean', low)).value == 100.0


def test_stale_revision_rejects_and_fixed_batch_is_readonly():
    with TemporaryDatabaseContext():
        fixture = seed_draft(); item = fixture['items']['lj']; low = fixture['levels'][0]
        app = run_app(fixture)
        app.button(key=f'open_levels_{item}').click().run()
        app.number_input(key=key(app, 'mean', low)).set_value(777.0)
        other = fixture['items']['instant']
        other_levels = list_lot_item_levels(other).to_dict('records')
        save_lot_item_levels(other, other_levels)
        app.button(key='batch_levels_save').click().run()
        assert_clean(app)
        assert list(app.error)
        assert list_lot_item_levels(item).iloc[0]['target_mean'] == 100.0
        app.button(key='batch_levels_cancel').click().run()
        app.button(key='batch_levels_discard').click().run()
        # The completed historical batch path is a read-only display, including after later stop/restore.
        zlevels = list_lot_item_levels(fixture['items']['zscore']).to_dict('records')
        for row in zlevels:
            row['target_source'] = 'manufacturer'; row['target_confirmed'] = True
        save_lot_item_levels(fixture['items']['zscore'], zlevels)
        confirm_fixture_lot(fixture['config_id'])
        activate_lot_config(fixture['config_id'])
        app.button(key=f'open_levels_{item}').click().run()
        assert_clean(app)
        assert not any(button.key == 'batch_levels_save' for button in app.button)
        assert not app.number_input
        assert app.dataframe[0].value.iloc[0]['均值'] == 100.0


def test_quality_cancel_save_and_reopen_clear_unsaved_fields():
    with TemporaryDatabaseContext():
        fixture = seed_draft(); item = fixture['items']['lj']; prefix = f'quality_lot_{item}_'
        app = run_app(fixture)
        original = get_batch_item_context(item)['item']['quality_review_json']
        app.button(key=f'open_quality_{item}').click().run()
        assert_clean(app)
        app.radio(key=prefix+'mode').set_value('记录实验室要求').run()
        app.text_input(key=prefix+'source_name').set_value('尚未保存的依据')
        app.button(key='batch_quality_close').click().run()
        assert_clean(app)
        assert get_batch_item_context(item)['item']['quality_review_json'] == original
        app.button(key='batch_quality_continue').click().run()
        assert_clean(app)
        assert app.text_input(key=prefix+'source_name').value == '尚未保存的依据'
        app.button(key='batch_quality_close').click().run()
        app.button(key='batch_quality_discard').click().run()
        assert_clean(app)
        assert get_batch_item_context(item)['item']['quality_review_json'] == original
        assert 'batch_item_dialog' not in app.session_state
        app.button(key=f'open_quality_{item}').click().run()
        app.radio(key=prefix+'mode').set_value('记录实验室要求').run()
        assert app.text_input(key=prefix+'source_name').value != '尚未保存的依据'
        app.text_input(key=prefix+'source_name').set_value('批次弹窗试验 SOP')
        app.text_input(key=prefix+'source_version').set_value('SOP-2026-09')
        app.text_area(key=prefix+'recorded_text').set_value('按说明书确认均值和标准差，仅作软件回归测试。')
        app.text_input(key=prefix+'person').set_value('测试确认人')
        app.text_area(key=prefix+'evidence').set_value('已核对检验项目、单位、浓度水平及依据版本。')
        app.checkbox(key=prefix+'confirmed').check().run()
        app.button(key=prefix+'adopt').click().run()
        assert_clean(app)
        assert 'batch_item_dialog' not in app.session_state
        review = json.loads(get_batch_item_context(item)['item']['quality_review_json'])
        assert review['status'] == 'confirmed'
        assert review['recorded']['source_name'] == '批次弹窗试验 SOP'
        app.button(key=f'open_quality_{item}').click().run()
        assert_clean(app)
        assert not app.checkbox(key=prefix+'confirmed').value
        assert app.text_input(key=prefix+'source_name').value == '批次弹窗试验 SOP'
        other = fixture['items']['instant']
        save_lot_item_levels(other, list_lot_item_levels(other).to_dict('records'))
        app.run()
        assert_clean(app)
        assert list(app.error)
        assert not any(button.key == prefix+'adopt' for button in app.button)
        app.button(key='batch_quality_close').click().run()
        app.button(key=f'open_quality_{item}').click().run()
        with get_connection() as connection:
            connection.execute('UPDATE qc_lot_config_items SET is_disabled=1 WHERE id=?', (item,))
        app.run()
        assert_clean(app)
        assert list(app.error)
        assert not any(button.key == prefix+'adopt' for button in app.button)


if __name__ == '__main__':
    test_cancel_and_failed_save_preserve_values_and_original_records()
    test_level_selection_order_and_per_material_values_survive_reorder()
    test_reopen_and_switch_items_do_not_reuse_unsaved_values()
    test_stale_revision_rejects_and_fixed_batch_is_readonly()
    test_quality_cancel_save_and_reopen_clear_unsaved_fields()
    print('batch_dialogs_smoke_test passed')
