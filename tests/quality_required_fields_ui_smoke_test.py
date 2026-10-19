"""Required detection conditions stay visible through edits, reruns and drafts."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import get_connection
from services.quality_applicability_service import CONTEXT_LABELS
from services.quality_target_service import decode, item_context
from tests.quality_applicability_smoke_test import IsolatedDatabase, context, draft
from tests.quality_simple_ui_smoke_test import screen, set_conditions
from tests.quick_project_setup_ui_smoke_test import assert_clean
from ui.required_fields import required_field_key


def fresh_item(*, method='免疫比浊法'):
    """Fix the optional dictionary default so the expected blank fields are explicit."""
    fixture, identifier = draft(method=method)
    with get_connection() as connection:
        connection.execute('UPDATE md_test_items SET specimen_type=? WHERE id=?',
                           ('', fixture['test_item_id']))
    return identifier, f'quality_project_{identifier}'


class QualityRequiredFieldsUiTests(unittest.TestCase):
    def assert_missing(self, app, prefix, fields):
        assert_clean(app)
        fields = tuple(fields)
        captions = [row.value for row in app.caption]
        styles = '\n'.join(row.value for row in app.markdown)
        for field in ('technique', 'result_kind', 'result_scale', 'purpose', 'specimen'):
            action = '填写' if field == 'specimen' else '选择'
            prompt = f'必填 · 请{action}{CONTEXT_LABELS[field]}。'
            selector = '.st-key-' + required_field_key(prefix + '_context_' + field)
            if field in fields:
                self.assertIn(prompt, captions)
                self.assertIn(selector, styles)
            else:
                self.assertNotIn(prompt, captions)
                self.assertNotIn(selector, styles)
        summaries = [row.value for row in app.info if row.value.startswith('还有 ')]
        expected = [] if not fields else [
            f"还有 {len(fields)} 项必填：{'、'.join(CONTEXT_LABELS[field] for field in fields)}。"
            '请填写下方标出的输入框。']
        self.assertEqual(expected, summaries)
        self.assertFalse(any(row.label == '检测条件' for row in app.expander))

    def test_default_blanks_show_local_prompts_and_summary_without_changing_labels(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item()
            app = screen('project', identifier)
            self.assert_missing(app, prefix, ('result_kind', 'result_scale', 'purpose', 'specimen'))
            for field in ('technique', 'result_kind', 'result_scale', 'purpose'):
                self.assertEqual(CONTEXT_LABELS[field],
                                 app.selectbox(key=prefix + '_context_' + field).label)
            self.assertEqual('标本或基质', app.text_input(key=prefix + '_context_specimen').label)
            self.assertEqual('immunoassay', app.selectbox(key=prefix + '_context_technique').value)
            self.assertTrue(app.selectbox(key=prefix + '_context_technique').disabled)
            self.assertTrue(app.button(key=prefix + '_adopt').disabled)
            self.assertEqual({}, decode(item_context('project', identifier)['quality_review_json']))

    def test_screenshot_pair_partial_completion_and_extra_rerun_keep_current_values(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item()
            app = screen('project', identifier)
            set_conditions(app, prefix, {'result_scale': 'concentration', 'specimen': '血清'})
            self.assert_missing(app, prefix, ('result_kind', 'purpose'))
            app.selectbox(key=prefix + '_context_result_kind').set_value('quantitative').run()
            self.assert_missing(app, prefix, ('purpose',))
            app.run()
            self.assert_missing(app, prefix, ('purpose',))
            self.assertEqual('quantitative', app.selectbox(key=prefix + '_context_result_kind').value)
            self.assertEqual('concentration', app.selectbox(key=prefix + '_context_result_scale').value)
            self.assertEqual('血清', app.text_input(key=prefix + '_context_specimen').value)
            app.selectbox(key=prefix + '_context_purpose').set_value('clinical').run()
            self.assert_missing(app, prefix, ())
            app.run()
            self.assert_missing(app, prefix, ())
            self.assertEqual('clinical', app.selectbox(key=prefix + '_context_purpose').value)
            app.checkbox(key=prefix + '_confirmed').check().run()
            self.assertFalse(app.button(key=prefix + '_adopt').disabled)

    def test_clearing_completed_field_restores_only_its_highlight_and_blocks_confirmation(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item()
            app = screen('project', identifier)
            set_conditions(app, prefix, context())
            self.assert_missing(app, prefix, ())
            app.checkbox(key=prefix + '_confirmed').check().run()
            self.assertFalse(app.button(key=prefix + '_adopt').disabled)
            app.selectbox(key=prefix + '_context_result_kind').set_value('').run()
            self.assert_missing(app, prefix, ('result_kind',))
            self.assertTrue(app.button(key=prefix + '_adopt').disabled)
            app.run()
            self.assert_missing(app, prefix, ('result_kind',))
            self.assertEqual('clinical', app.selectbox(key=prefix + '_context_purpose').value)
            self.assertEqual('血清', app.text_input(key=prefix + '_context_specimen').value)

    def test_uninferred_technique_and_all_other_conditions_share_the_same_prompts(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item(method='未明确检测方法')
            app = screen('project', identifier)
            self.assert_missing(app, prefix, ('technique', 'result_kind', 'result_scale', 'purpose', 'specimen'))
            self.assertFalse(app.selectbox(key=prefix + '_context_technique').disabled)
            app.selectbox(key=prefix + '_context_technique').set_value('immunoassay').run()
            self.assert_missing(app, prefix, ('result_kind', 'result_scale', 'purpose', 'specimen'))
            app.run()
            self.assert_missing(app, prefix, ('result_kind', 'result_scale', 'purpose', 'specimen'))
            self.assertEqual('immunoassay', app.selectbox(key=prefix + '_context_technique').value)

    def test_whitespace_specimen_is_missing_and_filling_it_clears_the_prompt(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item()
            app = screen('project', identifier)
            set_conditions(app, prefix, context(specimen='   '))
            self.assert_missing(app, prefix, ('specimen',))
            self.assertEqual('   ', app.text_input(key=prefix + '_context_specimen').value)
            app.text_input(key=prefix + '_context_specimen').set_value('血清').run()
            self.assert_missing(app, prefix, ())
            app.selectbox(key=prefix + '_context_result_scale').set_value('').run()
            self.assert_missing(app, prefix, ('result_scale',))
            self.assertEqual('血清', app.text_input(key=prefix + '_context_specimen').value)

    def test_pending_save_and_reopen_keep_the_two_missing_fields_and_completed_input(self):
        with IsolatedDatabase():
            identifier, prefix = fresh_item()
            app = screen('project', identifier)
            set_conditions(app, prefix, {'result_scale': 'concentration', 'specimen': '本次血清'})
            self.assert_missing(app, prefix, ('result_kind', 'purpose'))
            app.button(key=prefix + '_save_pending').click().run()
            assert_clean(app)
            saved = decode(item_context('project', identifier)['quality_review_json'])
            self.assertEqual('pending', saved['status'])
            self.assertEqual('本次血清', saved['context']['specimen'])
            self.assertEqual('concentration', saved['context']['result_scale'])
            self.assertEqual('', saved['context']['result_kind'])
            self.assertEqual('', saved['context']['purpose'])
            self.assert_missing(app, prefix, ('result_kind', 'purpose'))
            app = screen('project', identifier)
            self.assert_missing(app, prefix, ('result_kind', 'purpose'))
            self.assertEqual('本次血清', app.text_input(key=prefix + '_context_specimen').value)
            self.assertEqual('concentration', app.selectbox(key=prefix + '_context_result_scale').value)
            self.assertTrue(app.button(key=prefix + '_adopt').disabled)
            set_conditions(app, prefix, {'result_kind': 'quantitative', 'purpose': 'clinical'})
            self.assert_missing(app, prefix, ())
            app.run()
            self.assert_missing(app, prefix, ())
            self.assertEqual('本次血清', app.text_input(key=prefix + '_context_specimen').value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
