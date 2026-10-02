"""Editable procedure defaults survive Streamlit reruns, drafts and reopening."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from tests.quality_applicability_smoke_test import IsolatedDatabase, draft, context
from services.quality_target_service import decode, item_context
from services.quality_review_service import validate_project_quality


def screen(item_id):
    return AppTest.from_string(
        f"from ui.quality_targets import render_adoption\nrender_adoption('project', {item_id})",
        default_timeout=30).run()


def prepare(app, prefix):
    for key, value in context().items():
        if key == 'specimen':
            app.text_input(key=prefix+'_context_'+key).set_value(value)
        else:
            widget = app.selectbox(key=prefix+'_context_'+key)
            if widget.disabled:
                assert widget.value == value
            else:
                widget.set_value(value)
    return app.run()


def test_default_edit_draft_reopen_confirm_and_clear():
    with IsolatedDatabase():
        _, item_id = draft()
        prefix = f'quality_project_{item_id}'
        app = prepare(screen(item_id), prefix)
        assert not app.exception
        assert app.multiselect(key=prefix+'_process_sources').value == ['wst641-2018-iqc']
        reference = app.text_area(key=prefix+'_process_text').value
        assert 'WS/T 641' in reference and '均值' in reference
        local = '按本地区质控方案，每日首批设置本实验室规定的对照。\n异常时依照 SOP 处理并记录。'
        app.text_area(key=prefix+'_process_text').set_value(local).run()
        assert not decode(item_context('project', item_id)['quality_review_json'])
        app.button(key=prefix+'_save_pending').click().run()
        assert not app.exception
        saved = decode(item_context('project', item_id)['quality_review_json'])
        assert saved['process_requirements']['requirement_text'] == local
        app = screen(item_id)
        assert app.text_area(key=prefix+'_process_text').value == local
        app.text_input(key=prefix+'_person').set_value('页面核对人')
        app.text_area(key=prefix+'_evidence').set_value('数值限值按适用标准，对照安排按当地方案。')
        app.checkbox(key=prefix+'_confirmed').check().run()
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and not app.error
        assert validate_project_quality(item_id) == []
        app = screen(item_id)
        assert app.text_area(key=prefix+'_process_text').value == local
        app.multiselect(key=prefix+'_process_sources').set_value([]).run()
        assert app.text_area(key=prefix+'_process_text').value == local
        app.text_area(key=prefix+'_process_text').set_value('').run()
        app.checkbox(key=prefix+'_confirmed').check().run()
        app.button(key=prefix+'_adopt').click().run()
        assert not app.exception and not app.error and validate_project_quality(item_id) == []
        app = screen(item_id)
        assert app.multiselect(key=prefix+'_process_sources').value == []
        assert app.text_area(key=prefix+'_process_text').value == ''
        saved = decode(item_context('project', item_id)['quality_review_json'])
        assert saved['selected_source_id'] == 'wst403-2024-047'
        assert saved['process_requirements'] == {'source_ids': [], 'requirement_text': ''}


def test_reference_reset_and_deselection_update_only_unedited_text():
    with IsolatedDatabase():
        _, item_id = draft()
        prefix = f'quality_project_{item_id}'
        app = prepare(screen(item_id), prefix)
        reference = app.text_area(key=prefix+'_process_text').value
        app.multiselect(key=prefix+'_process_sources').set_value([]).run()
        assert app.text_area(key=prefix+'_process_text').value == ''
        app.multiselect(key=prefix+'_process_sources').set_value(['wst641-2018-iqc']).run()
        assert app.text_area(key=prefix+'_process_text').value == reference
        app.text_area(key=prefix+'_process_text').set_value('本地安排').run()
        app.button(key=prefix+'_process_reset').click().run()
        assert not app.exception and app.text_area(key=prefix+'_process_text').value == reference
        assert not decode(item_context('project', item_id)['quality_review_json'])


def test_signal_requirements_and_process_editor_have_separate_controls():
    with IsolatedDatabase():
        _, item_id = draft('HBsAg', unit='S/CO', method='化学发光免疫法')
        prefix = f'quality_project_{item_id}'
        app = screen(item_id)
        app.selectbox(key=prefix+'_context_purpose').set_value('clinical')
        app.text_input(key=prefix+'_context_specimen').set_value('血清')
        app.run()
        assert not app.exception
        assert app.multiselect(key=prefix+'_process_sources').value == []
        assert set(app.multiselect(key=prefix+'_mandatory_sources').value) == {
            'wst494-2017-qualitative', 'wst494-2017-signal'}
        app.text_area(key=prefix+'_process_text').set_value('本地对照设置').run()
        assert not app.exception
        assert len(app.multiselect(key=prefix+'_mandatory_sources').value) == 2


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name)
