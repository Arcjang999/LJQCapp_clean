"""Reference list selections, saved filters and alias ownership on isolated data."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from services.master_data_service import create_alias, create_manufacturer, create_test_item
from services.master_data_edit_service import get_master_record_context
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean, select_table_row
from tests.master_data_dialogs_smoke_test import field_key


def make_app(entity):
    return AppTest.from_string(f'''
from ui.master_data_workspace import render_master_data_workspace
from ui.master_data_dialogs import render_pending_master_data_dialog
render_master_data_workspace({entity!r})
render_pending_master_data_dialog()
''', default_timeout=20).run()


def select(app, entity, row):
    index = next(i for i, table in enumerate(app.dataframe) if 'md_table_' + entity + '_' in table.proto.id)
    select_table_row(app, row, index=index)


def test_new_record_renaming_and_hidden_selection():
    with IsolatedDatabase():
        first = create_manufacturer(display_name='Alpha 厂家')
        second = create_manufacturer(display_name='Beta 厂家')
        app = make_app('manufacturer')
        select(app, 'manufacturer', 1)
        assert app.session_state['md_selected_manufacturer'] == second
        app.text_input(key='md_search_manufacturer').set_value('Alpha').run()
        assert app.session_state['md_selected_manufacturer'] is None
        assert app.button(key='md_edit_manufacturer').disabled
        select(app, 'manufacturer', 0)
        assert app.session_state['md_selected_manufacturer'] == first
        app.button(key='md_edit_manufacturer').click().run()
        app.text_input(key=field_key(app, 'display_name')).set_value('Gamma 厂家')
        app.text_input(key=field_key(app, 'legal_name')).set_value('Gamma 有限公司')
        app.button(key='md_dialog_save').click().run()
        assert_clean(app)
        assert app.session_state['md_selected_manufacturer'] == first
        assert app.text_input(key='md_search_manufacturer').value == ''
        assert get_master_record_context('manufacturer', second)['record']['display_name'] == 'Beta 厂家'
        app.text_input(key='md_search_manufacturer').set_value('Gamma').run()
        app.button(key='md_create_manufacturer').click().run()
        app.text_input(key=field_key(app, 'display_name')).set_value('Delta 厂家')
        app.button(key='md_dialog_save').click().run()
        assert_clean(app)
        new_id = app.session_state['md_selected_manufacturer']
        assert new_id not in (first, second)
        assert get_master_record_context('manufacturer', new_id)['record']['display_name'] == 'Delta 厂家'
        assert app.text_input(key='md_search_manufacturer').value == ''


def test_alias_search_edit_keeps_parent_and_switching_parent_clears_child():
    with IsolatedDatabase():
        first = create_test_item(chinese_name='QA 检验项目甲')
        second = create_test_item(chinese_name='QA 检验项目乙')
        alias = create_alias(entity_type='test_item', entity_id=first, alias_text='待修改唯一别名')
        create_alias(entity_type='test_item', entity_id=second, alias_text='乙的别名')
        app = make_app('test_item')
        app.text_input(key='md_search_test_item').set_value('待修改唯一别名').run()
        assert len(app.dataframe[0].value) == 1
        select(app, 'test_item', 0)
        assert app.session_state['md_selected_test_item'] == first
        select(app, 'alias', 0)
        app.button(key='md_edit_alias').click().run()
        app.text_input(key=field_key(app, 'alias_text')).set_value('修改后的新别名')
        app.button(key='md_dialog_save').click().run()
        assert_clean(app)
        assert app.session_state['md_selected_test_item'] == first
        assert app.session_state['md_selected_alias'] == alias
        assert app.text_input(key='md_search_test_item').value == ''
        assert 'md_saved_entity' not in app.session_state.filtered_state
        app.text_input(key='md_search_test_item').set_value('QA 检验项目乙').run()
        assert app.button(key='md_edit_test_item').disabled
        select(app, 'test_item', 0)
        assert app.session_state['md_selected_test_item'] == second
        assert app.session_state['md_selected_alias'] is None
        assert app.button(key='md_edit_alias').disabled
        table = next(t.value for t in app.dataframe if 'md_table_alias_' in t.proto.id)
        assert table['别名内容'].tolist() == ['乙的别名']
        assert get_master_record_context('alias', alias)['record']['entity_id'] == first


def test_notes_save_retains_search_and_selected_row():
    with IsolatedDatabase():
        record_id = create_manufacturer(display_name='保持筛选厂家')
        app = make_app('manufacturer')
        app.text_input(key='md_search_manufacturer').set_value('保持筛选').run()
        select(app, 'manufacturer', 0)
        app.button(key='md_edit_manufacturer').click().run()
        app.text_area(key=field_key(app, 'notes')).set_value('新增备注')
        app.button(key='md_dialog_save').click().run()
        assert_clean(app)
        assert app.text_input(key='md_search_manufacturer').value == '保持筛选'
        assert app.session_state['md_selected_manufacturer'] == record_id
        assert get_master_record_context('manufacturer', record_id)['record']['notes'] == '新增备注'


def test_material_and_reference_drafts_render_one_dialog_at_a_time():
    with IsolatedDatabase():
        app = AppTest.from_string('''
from pages.master_data_page import render_master_data_page
render_master_data_page()
''', default_timeout=20).run()
        app.button(key='material_catalog_add_product').click().run()
        assert_clean(app)
        material_draft = dict(app.session_state['material_dialog'])
        # Simulate a second pending event. Neither draft should be dropped.
        app.button(key='md_create_manufacturer').click().run()
        assert_clean(app)
        assert app.session_state['material_dialog'] == material_draft
        assert app.session_state['master_data_dialog']['entity_type'] == 'manufacturer'
        app.button(key='md_dialog_cancel').click().run()
        assert_clean(app)
        assert 'master_data_dialog' not in app.session_state.filtered_state
        assert app.session_state['material_dialog'] == material_draft
        assert any(text.value == '新增质控品' for text in app.subheader)


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
