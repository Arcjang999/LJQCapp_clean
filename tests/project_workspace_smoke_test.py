"""Project-first dialogs, real-ID row selection and default isolation on temporary data."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.master_data_service import create_method, create_qc_material
from services.project_config_service import get_project_template, list_project_templates, list_template_items, save_template_items
from services.project_workspace_service import save_project_details, matching_project_ids
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies


def workspace_page():
    from ui.project_navigation import render_project_navigation
    render_project_navigation()


def legacy_editor_page(template_id):
    from pages.project_management_page import _render_template_editor
    _render_template_editor(template_id)


def values(data, name):
    return dict(template_name=name, lab_instrument_id=data['lab_instrument_id'], qc_material_id=data['qc_material_id'],
        default_reagent_id=data['reagent_id'], default_method_id=data['method_id'], default_qc_method='lj',
        default_level_count=1, project_group='血筛', notes='')


def assert_clean(app):
    assert not list(app.exception), [str(e) for e in app.exception]


def draft_key(app, name):
    return 'project_draft_' + app.session_state['project_workspace_dialog']['token'] + '_' + name


def disable_key(app, name):
    token = app.session_state['project_workspace_dialog']['status_confirmation']['token']
    return ('project_disable_reason_' if name == 'reason' else 'project_disable_final_confirm_') + token


def select_table_row(app, row, index=0):
    # Streamlit 1.63 AppTest has no public dataframe selection setter. Feed the
    # same selection payload as the browser so the registered callback runs.
    table = app.dataframe[index]
    state = app._tree.get_widget_states()
    selected = state.widgets.add()
    selected.id = table.proto.id
    selected.string_value = json.dumps({'selection': {'rows': [row], 'columns': [], 'cells': []}})
    app._run(state)
    assert_clean(app)


def test_defaults_do_not_rewrite_items_and_filters_share_item():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        other_method = create_method(method_name='第二种方法学')
        tid = save_project_details(values(data, '混合血筛项目'))
        second = save_project_details(values(data, '独立项目'))
        common = dict(input_value_type='raw', unit_id=data['unit_id'], reagent_id=data['reagent_id'], target_n=20)
        save_template_items(tid, [dict(common, test_item_id=data['lj_item_id'], qc_method='lj', method_id=data['method_id'], level_count=1),
            dict(common, test_item_id=data['zscore_item_id'], qc_method='zscore', method_id=other_method, level_count=2)])
        save_template_items(second, [dict(common, test_item_id=data['lj_item_id'], qc_method='lj', method_id=other_method, level_count=1)])
        assert matching_project_ids(qc_method='lj', method_name='第二种方法学') == {second}
        assert matching_project_ids(qc_method='zscore', method_name='第二种方法学') == {tid}
        before = list_template_items(tid).to_json(orient='records')
        current = dict(get_project_template(tid))
        edit = values(data, '混合血筛项目更名')
        edit.update(default_qc_method='instant', default_method_id=other_method, project_group='分子与免疫')
        save_project_details(edit, template_id=tid, expected_revision=current['revision_no'])
        assert list_template_items(tid).to_json(orient='records') == before
        assert get_project_template(tid)['default_qc_method'] == 'instant'
        different_product = create_qc_material(generic_name='另一个产品')
        edit['qc_material_id'] = different_product
        try:
            save_project_details(edit, template_id=tid, expected_revision=get_project_template(tid)['revision_no'])
        except ValueError as exc:
            assert '不能更换' in str(exc)
        else:
            raise AssertionError('Referenced identity must stay locked')
        assert get_project_template(tid)['qc_material_id'] == data['qc_material_id']


def test_project_dialog_create_edit_cancel_disable_restore():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        app = AppTest.from_function(workspace_page, default_timeout=15).run()
        assert_clean(app)
        app.button(key='home_create_project').click().run()
        app.text_input(key=draft_key(app, 'template_name')).set_value('弹窗项目')
        app.button(key='project_dialog_save').click().run()
        assert_clean(app)
        assert list_project_templates().empty
        assert app.text_input(key=draft_key(app, 'template_name')).value == '弹窗项目'
        for key, item in [('lab_instrument_id', 'lab_instrument_id'), ('qc_material_id', 'qc_material_id'), ('default_reagent_id', 'reagent_id')]:
            app.selectbox(key=draft_key(app, key)).set_value(data[item])
        app.button(key='project_dialog_save').click().run()
        assert_clean(app)
        tid = int(list_project_templates().iloc[0]['id'])
        assert app.session_state['workspace_project_id'] == tid
        app.button(key='workspace_edit_project').click().run()
        app.text_input(key=draft_key(app, 'template_name')).set_value('不保存的项目名称')
        app.button(key='project_dialog_cancel').click().run()
        assert_clean(app)
        assert get_project_template(tid)['template_name'] == '弹窗项目'
        app.button(key='project_continue_edit').click().run()
        assert_clean(app)
        assert app.text_input(key=draft_key(app, 'template_name')).value == '不保存的项目名称'
        app.button(key='project_dialog_cancel').click().run()
        app.button(key='project_discard_changes').click().run()
        assert_clean(app)
        app.button(key='workspace_edit_project').click().run()
        assert app.text_input(key=draft_key(app, 'template_name')).value == '弹窗项目'
        app.text_input(key=draft_key(app, 'template_name')).set_value('保存的项目名称')
        app.button(key='project_dialog_save').click().run()
        assert_clean(app)
        assert get_project_template(tid)['template_name'] == '保存的项目名称'
        assert not any(button.key == 'workspace_disable_project' for button in app.button)
        app.button(key='workspace_edit_project').click().run()
        app.button(key='project_editor_disable').click().run()
        assert not get_project_template(tid)['is_disabled']
        app.button(key='project_disable_first_confirm').click().run()
        assert_clean(app)
        assert not get_project_template(tid)['is_disabled']
        app.text_input(key=disable_key(app, 'reason')).set_value('暂不使用')
        app.button(key='project_disable_first_confirm').click().run()
        assert not get_project_template(tid)['is_disabled']
        app.button(key=disable_key(app, 'final')).click().run()
        assert_clean(app)
        assert get_project_template(tid)['is_disabled']
        app.checkbox(key='home_show_disabled_projects').check().run()
        select_table_row(app, 0)
        assert app.session_state['workspace_project_id'] == tid
        app.button(key='workspace_edit_project').click().run()
        assert not any(button.key == 'project_dialog_save' for button in app.button)
        app.button(key='project_editor_restore').click().run()
        app.button(key='project_status_cancel').click().run()
        assert get_project_template(tid)['is_disabled']
        app.button(key='project_editor_restore').click().run()
        app.button(key='project_restore_confirm').click().run()
        assert_clean(app)
        assert not get_project_template(tid)['is_disabled']
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_project_templates').fetchone()[0] == 1


def test_project_selection_uses_id_and_filter_clears_hidden_selection():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        first = save_project_details(values(data, 'Alpha项目'))
        second = save_project_details(values(data, 'Beta项目'))
        app = AppTest.from_function(workspace_page, default_timeout=15).run()
        names = app.dataframe[0].value['项目名称'].tolist()
        select_table_row(app, names.index('Beta项目'))
        assert app.session_state['workspace_project_id'] == second
        app.button(key='workspace_back').click().run()
        assert app.session_state['home_selected_project_id'] == second
        app.button(key='home_open_project').click().run()
        assert app.session_state['workspace_project_id'] == second
        app.button(key='workspace_back').click().run()
        app.text_input(key='home_project_filter_search').set_value('Alpha').run()
        assert_clean(app)
        assert app.button(key='home_edit_project').disabled
        assert not any(button.key == 'home_disable_project' for button in app.button)
        select_table_row(app, 0)
        assert app.session_state['workspace_project_id'] == first
        assert app.session_state['home_selected_project_id'] == first


def test_disable_two_confirmations_preserve_unsaved_edits_and_revision():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid = save_project_details(values(data, '已保存的项目'))
        initial = dict(get_project_template(tid))
        app = AppTest.from_function(workspace_page, default_timeout=15).run()
        select_table_row(app, 0)
        app.button(key='workspace_edit_project').click().run()
        app.text_input(key=draft_key(app, 'template_name')).set_value('不能偷偷保存的新名称')
        app.text_area(key=draft_key(app, 'notes')).set_value('取消后保留的填写内容')
        app.button(key='project_editor_disable').click().run()
        assert not any(button.key.startswith('project_disable_final_confirm_') for button in app.button)
        assert dict(get_project_template(tid)) == initial
        app.button(key='project_status_cancel').click().run()
        assert_clean(app)
        assert app.text_input(key=draft_key(app, 'template_name')).value == '不能偷偷保存的新名称'
        assert app.text_area(key=draft_key(app, 'notes')).value == '取消后保留的填写内容'
        app.button(key='project_editor_disable').click().run()
        app.text_input(key=disable_key(app, 'reason')).set_value('双重确认测试')
        app.button(key='project_disable_first_confirm').click().run()
        assert dict(get_project_template(tid)) == initial
        assert not any(button.key == 'project_disable_first_confirm' for button in app.button)
        app.button(key='project_status_cancel').click().run()
        assert_clean(app)
        assert app.text_input(key=draft_key(app, 'template_name')).value == '不能偷偷保存的新名称'
        assert dict(get_project_template(tid)) == initial
        app.button(key='project_editor_disable').click().run()
        # A stale or malformed stage cannot expose the final action without the first confirmation.
        context = app.session_state['project_workspace_dialog']
        context['status_confirmation']['step'] = 'final'
        context['status_confirmation']['first_confirmed'] = False
        app.run()
        assert_clean(app)
        assert not any(button.key.startswith('project_disable_final_confirm_') for button in app.button)
        assert dict(get_project_template(tid)) == initial
        app.text_input(key=disable_key(app, 'reason')).set_value('双重确认测试')
        app.button(key='project_disable_first_confirm').click().run()
        updated = values(data, '另一处保存的新名称')
        save_project_details(updated, template_id=tid, expected_revision=initial['revision_no'])
        changed = dict(get_project_template(tid))
        app.button(key=disable_key(app, 'final')).click().run()
        assert_clean(app)
        assert list(app.error)
        assert dict(get_project_template(tid)) == changed
        app.button(key='project_status_cancel').click().run()
        assert app.text_input(key=draft_key(app, 'template_name')).value == '不能偷偷保存的新名称'
        app.button(key='project_dialog_cancel').click().run()
        app.button(key='project_discard_changes').click().run()
        app.button(key='workspace_edit_project').click().run()
        app.text_area(key=draft_key(app, 'notes')).set_value('最终停用也不能保存这条备注')
        app.button(key='project_editor_disable').click().run()
        app.text_input(key=disable_key(app, 'reason')).set_value('已重新核对')
        app.button(key='project_disable_first_confirm').click().run()
        app.button(key=disable_key(app, 'final')).click().run()
        assert_clean(app)
        stopped = get_project_template(tid)
        assert stopped['is_disabled'] == 1
        assert stopped['template_name'] == changed['template_name']
        assert stopped['notes'] == changed['notes']
        assert stopped['disabled_reason'] == '已重新核对'


def test_legacy_project_editor_opens_same_edit_dialog():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        tid = save_project_details(values(data, '旧入口仍需确认'))
        initial = dict(get_project_template(tid))
        app = AppTest.from_function(legacy_editor_page, args=(tid,), default_timeout=15).run()
        assert_clean(app)
        app.button(key=f'v11_disable_template_{tid}').click().run()
        assert_clean(app)
        assert dict(get_project_template(tid)) == initial
        assert app.session_state['project_workspace_dialog']['kind'] == 'project'
        app.button(key='project_editor_disable').click().run()
        assert not any(button.key.startswith('project_disable_final_confirm_') for button in app.button)
        app.text_input(key=disable_key(app, 'reason')).set_value('旧入口确认')
        app.button(key='project_disable_first_confirm').click().run()
        assert dict(get_project_template(tid)) == initial
        app.button(key='project_status_cancel').click().run()
        assert dict(get_project_template(tid)) == initial


if __name__ == '__main__':
    test_defaults_do_not_rewrite_items_and_filters_share_item()
    test_project_dialog_create_edit_cancel_disable_restore()
    test_project_selection_uses_id_and_filter_clears_hidden_selection()
    test_disable_two_confirmations_preserve_unsaved_edits_and_revision()
    test_legacy_project_editor_opens_same_edit_dialog()
    print('project_workspace_smoke_test passed')
