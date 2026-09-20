"""Isolated applicability policy gates, text-only scales and frozen-history checks."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection, init_db, get_instant_results
from tests.instant_v12_integration_smoke_test import IsolatedDatabase, entry, rejected
from tests.instant_v12_fixtures import seed_instant_configuration
from services.master_data_service import create_qc_lot, create_qc_level
from services.project_config_service import (activate_project_template, activate_lot_config,
    copy_lot_config, list_lot_config_items, list_template_items)
from services.quality_target_service import (adopt_requirement, clear_requirement, decode,
    item_context, validate_lot_goal)
from services.quality_review_service import (save_recorded_requirement, standard_candidates,
    validate_project_quality, runtime_review)


def draft_fixture(mode='raw', candidate=False):
    fixture = seed_instant_configuration(name='质量核对隔离项目', input_value_type=mode, cv_limit=None)
    project_item = int(list_template_items(fixture['template_id']).iloc[0]['id'])
    with get_connection() as connection:
        if candidate:
            connection.execute("UPDATE md_test_items SET chinese_name='CRP' WHERE id=?", (fixture['test_item_id'],))
        connection.execute("UPDATE qc_project_templates SET status='draft' WHERE id=?", (fixture['template_id'],))
        connection.execute("""UPDATE qc_project_template_items SET quality_review_json='{}',
            quality_goal_json='{}' WHERE id=?""", (project_item,))
    return fixture, project_item


def record(scope, item_id, **overrides):
    kwargs = dict(source_name='本实验室 SOP', source_version='QC-SOP-2026-01',
                  requirement_text='按本检测系统的质控材料说明书建立并确认控制参数。',
                  confirmed_by='验收确认人', evidence='已逐项核对检测对象、方法学、输入尺度和适用范围。')
    kwargs.update(overrides)
    return save_recorded_requirement(scope, item_id, **kwargs)


def copied_lot(fixture):
    lot = create_qc_lot(qc_material_id=fixture['material_id'], lot_no='QUALITY-REVIEW-NEW', expiry_date='2028-12-31')
    create_qc_level(qc_material_lot_id=lot, level_order=1, level_name='常规水平')
    config = copy_lot_config(source_lot_config_id=fixture['config_id'], target_qc_material_lot_id=lot)
    return config, int(list_lot_config_items(config).iloc[0]['id'])


def test_new_project_and_lot_require_sources_but_allow_drafts():
    with IsolatedDatabase():
        fixture, project_item = draft_fixture()
        rejected(lambda: activate_project_template(fixture['template_id']), '质量目标待确认')
        rejected(lambda: record('project', project_item, source_version=''), '依据版本或编号')
        assert not decode(item_context('project', project_item)['quality_review_json'])
        record('project', project_item)
        activate_project_template(fixture['template_id'])
        config, lot_item = copied_lot(fixture)
        rejected(lambda: activate_lot_config(config), '质量目标待确认')
        reviewed = record('lot', lot_item)
        assert reviewed['levels'][0]['qc_level_id'] != fixture['level_id']
        activate_lot_config(config)


def test_name_candidate_requires_adoption_or_specific_exclusion_and_cannot_clear_to_bypass():
    with IsolatedDatabase():
        fixture, project_item = draft_fixture(candidate=True)
        candidates = standard_candidates(item_context('project', project_item))
        assert [row['id'] for row in candidates] == ['wst403-2024-047']
        rejected(lambda: record('project', project_item), '不适用原因')
        goal = adopt_requirement('project', project_item, 'wst403-2024-047',
                                confirmed_by='验收', evidence='CRP，免疫比浊，mg/L；按适用范围采用。')
        assert goal['spec']['standard'] == 'WS/T 403—2024'
        review = decode(item_context('project', project_item)['quality_review_json'])
        assert review['candidates'][0]['disposition'] == 'adopted'
        assert not validate_project_quality(project_item)
        clear_requirement('project', project_item)
        rejected(lambda: activate_project_template(fixture['template_id']), '质量目标待确认')
        # A same-name candidate is not automatically an applicable standard.
        record('project', project_item, exclusions={'wst403-2024-047':
            '当前为研究用非血液基质材料；标准的临床样本和分析质量范围不适用，采用所列验证方案。'})
        assert not validate_project_quality(project_item)


def test_ct_and_log_record_sources_without_inventing_cv_or_using_concentration_rules():
    for mode in ('ct', 'log'):
        with IsolatedDatabase():
            fixture, project_item = draft_fixture(mode, candidate=True)
            rejected(lambda: adopt_requirement('project', project_item, 'wst403-2024-047',
                     confirmed_by='验收', evidence='同名'), 'Ct 或 log')
            rejected(lambda: record('project', project_item), '不适用原因')
            reviewed = record('project', project_item, exclusions={'wst403-2024-047':
                f'本配置使用 {mode} 输入尺度，不能套用目录的浓度尺度允许不精密度。'})
            assert reviewed['decision'] == 'record_only'
            item = item_context('project', project_item)
            assert not decode(item['quality_goal_json']) and item['cv_limit'] is None
            activate_project_template(fixture['template_id'])


def test_method_change_or_goal_tampering_invalidates_review():
    with IsolatedDatabase():
        fixture, project_item = draft_fixture(candidate=True)
        adopt_requirement('project', project_item, 'wst403-2024-047',
                          confirmed_by='验收', evidence='已核对项目和测量系统适用性。')
        with get_connection() as connection:
            connection.execute("UPDATE qc_project_template_items SET quality_goal_json='{}' WHERE id=?", (project_item,))
        assert any('质量目标已修改' in error for error in validate_project_quality(project_item))
        record('project', project_item, exclusions={'wst403-2024-047':'研究基质不属于条款范围，采用研究方案。'})
        with get_connection() as connection:
            connection.execute("UPDATE qc_project_template_items SET input_value_type='ct' WHERE id=?", (project_item,))
        assert any('已修改' in error for error in validate_project_quality(project_item))


def test_frozen_legacy_lot_stays_writable_and_migration_is_idempotent():
    with IsolatedDatabase():
        fixture = seed_instant_configuration()
        entry(fixture, 0)
        with get_connection() as connection:
            connection.execute("""UPDATE qc_lot_config_items SET quality_review_json='{}',
                quality_goal_json='{}' WHERE id=?""", (fixture['item_id'],))
            before = [tuple(row) for row in connection.execute('SELECT * FROM instant_results')]
        init_db(); init_db()
        assert validate_lot_goal(fixture['item_id']) == []
        rejected(lambda: record('lot', fixture['item_id']), '已有批次')
        entry(fixture, 1)
        assert len(get_instant_results(fixture['batch_id'])) == 2
        with get_connection() as connection:
            after = [tuple(row) for row in connection.execute('SELECT * FROM instant_results ORDER BY id')]
            assert after[:1] == before
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_recorded_source_follows_runtime_and_transfer_snapshot():
    from services.instant_service import confirm_instant_transfer_to_lj
    with IsolatedDatabase():
        fixture = seed_instant_configuration(input_value_type='ct')
        original = runtime_review('instant', fixture['batch_id'])
        assert original['recorded']['source_version'] == 'TEST-QC-001'
        for index in range(21):
            entry(fixture, index, 25 + index / 100)
        result = confirm_instant_transfer_to_lj(fixture['batch_id'])
        assert runtime_review('lj', result['target_batch_id']) == original


def test_record_only_form_retains_invalid_submission_then_saves():
    with IsolatedDatabase():
        fixture, project_item = draft_fixture('ct')
        app = AppTest.from_string(f"from ui.quality_targets import render_adoption\nrender_adoption('project', {project_item})", default_timeout=30).run()
        prefix = f'quality_project_{project_item}'
        app.text_input(key=prefix+'_source_name').set_value('页面验收 SOP')
        app.text_input(key=prefix+'_source_version').set_value('UI-001')
        app.text_area(key=prefix+'_recorded_text').set_value('Ct尺度试剂说明书要求')
        app.text_input(key=prefix+'_person').set_value('页面验收人')
        app.checkbox(key=prefix+'_confirmed').check().run()
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and app.error
        assert app.text_input(key=prefix+'_source_name').value == '页面验收 SOP'
        app.text_area(key=prefix+'_evidence').set_value('目录浓度尺度不适用于本Ct配置；已核对说明书。')
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and not validate_project_quality(project_item)


def test_quality_page_uses_dialogs_and_cancel_does_not_write():
    from services.quality_target_service import list_catalog
    with IsolatedDatabase():
        fixture, project_item = draft_fixture()
        before = len(list_catalog())
        app = AppTest.from_string('from pages.quality_targets_page import render_quality_targets_page\nrender_quality_targets_page()', default_timeout=30).run()
        app.button(key='quality_custom_open').click().run()
        assert not app.exception
        next(widget for widget in app.text_input if widget.label == '检验项目').set_value('取消的草稿')
        app.button(key='quality_custom_cancel').click().run()
        assert not app.exception and len(list_catalog()) == before
        app.selectbox(key='quality_project_selector').set_value(project_item).run()
        app.button(key='quality_project_open').click().run()
        assert not app.exception
        app.button(key=f'quality_dialog_close_project_{project_item}').click().run()
        assert not app.exception and not decode(item_context('project', project_item)['quality_review_json'])


def test_old_new_lot_entry_opens_one_pending_draft_for_review():
    with IsolatedDatabase():
        fixture = seed_instant_configuration()
        template_item = int(list_template_items(fixture['template_id']).iloc[0]['id'])
        lot = create_qc_lot(qc_material_id=fixture['material_id'], lot_no='UI-REVIEW-LOT', expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=lot, level_order=1, level_name='常规水平')
        app = AppTest.from_string(f"from pages.lot_lifecycle_section import _render_new_parallel_form\n_render_new_parallel_form({fixture['config_id']}, {lot}, {{{template_item}: '验收检验项目'}})", default_timeout=30).run()
        app.multiselect[0].set_value([template_item])
        next(widget for widget in app.text_input if widget.label == '质控换批操作者').set_value('页面验收人')
        next(widget for widget in app.text_input if widget.label == '质控品换批原因').set_value('新批次到货，准备平行质控')
        next(button for button in app.button if button.label == '准备新批次并核对').click().run()
        assert not app.exception
        config = app.session_state['v11_pending_existing_config_id']
        assert app.session_state['show_project_management_page']
        with get_connection() as connection:
            assert connection.execute('SELECT status FROM qc_lot_configs WHERE id=?', (config,)).fetchone()[0] == 'draft'
            assert connection.execute('SELECT count(*) FROM qc_lot_configs WHERE qc_material_lot_id=?', (lot,)).fetchone()[0] == 1
        item = int(list_lot_config_items(config).iloc[0]['id'])
        rejected(lambda: activate_lot_config(config), '质量目标待确认')
        record('lot', item)
        activate_lot_config(config)
        assert not validate_lot_goal(item)


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
