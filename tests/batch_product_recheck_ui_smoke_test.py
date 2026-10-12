"""Pending batch product rechecks preserve quality drafts and frozen evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.batch_edit_service import get_batch_item_context
from services.material_workflow_service import create_material_config
from services.project_config_service import (
    activate_lot_config, activate_project_template, get_lot_config,
    list_lot_config_items, list_lot_item_levels,
)
from services.qc_product_recommendation_service import (
    save_draft_product_choice, save_qc_product_relation,
    set_qc_product_relation_disabled,
)
from tests.batch_dialogs_smoke_test import assert_clean, run_app
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext, _seed_v11_configuration_dependencies,
)
from tests.qc_product_recommendation_smoke_test import (
    database_state, relation, reviewed_project,
)
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project


def draft_with_changed_relation(*, manual=False):
    data = _seed_v11_configuration_dependencies()
    scope = {key: data[key] for key in (
        'lab_instrument_id', 'reagent_id', 'qc_material_id', 'unit_id', 'method_id')}
    scope['test_item_id'] = data['zscore_item_id']
    relation_id = None if manual else save_qc_product_relation(relation(scope))
    template_id, project_item_id, original = reviewed_project(
        scope, name='待启用批次界面重核', manual=manual)
    confirm_fixture_project(template_id)
    activate_project_template(template_id)
    config_id = create_material_config(template_id=template_id,
        selections={project_item_id: data['source_levels'][:2]})
    item_id = int(list_lot_config_items(config_id).iloc[0]['id'])
    confirm_fixture_lot(config_id)
    if manual:
        # A relation added then explicitly withdrawn changes the review revision,
        # while preserving a real no-candidate path for manual rechecking.
        relation_id = save_qc_product_relation(relation(scope))
        set_qc_product_relation_disabled(relation_id, disabled=True,
            expected_revision=1, reason='隔离回归已核对该关系不适用', confirmed_by='关系复核人')
    else:
        changed = relation(scope)
        changed['evidence'] = '待启用期间修订的厂家适用依据'
        save_qc_product_relation(changed, relation_id=relation_id, expected_revision=1)
    return dict(config_id=config_id, items={'zscore': item_id}, levels=data['source_levels'][:2],
                scope=scope, original=original)


def open_quality(fixture):
    app = run_app(fixture)
    item = fixture['items']['zscore']
    app.button(key=f'open_quality_{item}').click().run()
    assert_clean(app)
    prefix = f'quality_lot_{item}_'
    assert app.button(key=prefix + 'product_save').disabled
    return app, item, prefix


def fill_quality_draft(app, prefix):
    app.radio(key=prefix + 'mode').set_value('记录实验室要求').run()
    app.text_input(key=prefix + 'source_name').set_value('批次重核后的实验室 SOP')
    app.text_input(key=prefix + 'source_version').set_value('RECHECK-2026-10')
    app.text_area(key=prefix + 'recorded_text').set_value('已逐项核对当前批号和水平，按实验室要求判读。')
    app.text_input(key=prefix + 'person').set_value('批次质量核对人')
    app.text_area(key=prefix + 'evidence').set_value('保留当前实验室条件和本批次两水平的质量核查。')
    app.checkbox(key=prefix + 'confirmed').check().run()
    assert_clean(app)


def assert_quality_draft(testcase, app, prefix):
    testcase.assertEqual('批次重核后的实验室 SOP', app.text_input(key=prefix + 'source_name').value)
    testcase.assertEqual('RECHECK-2026-10', app.text_input(key=prefix + 'source_version').value)
    testcase.assertEqual('已逐项核对当前批号和水平，按实验室要求判读。',
                         app.text_area(key=prefix + 'recorded_text').value)
    testcase.assertEqual('批次质量核对人', app.text_input(key=prefix + 'person').value)
    testcase.assertTrue(app.checkbox(key=prefix + 'confirmed').value)


class BatchProductRecheckUITests(unittest.TestCase):
    def test_candidate_confirmation_requires_save_then_syncs_revision_and_retains_quality_draft(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation()
            app, item, prefix = open_quality(fixture)
            before = get_batch_item_context(item)
            original_levels = list_lot_item_levels(item).to_json(orient='records')
            original_quality = before['item']['quality_review_json']
            fill_quality_draft(app, prefix)
            unchanged = database_state()
            app.button(key=prefix + 'adopt').click().run()
            assert_clean(app)
            self.assertTrue(any('适用资料已变化' in error.value for error in app.error))
            self.assertEqual(unchanged, database_state(), '过期选品依据不能绕过重核直接确认质量')
            assert_quality_draft(self, app, prefix)
            app.button(key=prefix + 'product_confirm').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key=prefix + 'product_save').disabled)
            self.assertEqual(unchanged, database_state(), '核对按钮只生成草稿，尚未显式保存')
            assert_quality_draft(self, app, prefix)
            app.button(key=prefix + 'product_save').click().run()
            assert_clean(app)
            after = get_batch_item_context(item)
            self.assertEqual(before['revision'] + 1, after['revision'])
            state = app.session_state['batch_item_dialog']
            self.assertEqual(after['revision'], state['revision'])
            self.assertEqual(after['revision'], state['context']['revision'])
            self.assertFalse(any(button.key == prefix + 'product_save' for button in app.button))
            review = json.loads(after['item']['product_choice_review_json'])
            self.assertNotEqual(fixture['original']['revision'], review['revision'])
            self.assertEqual('recommendation', review['basis'])
            self.assertEqual(original_quality, after['item']['quality_review_json'])
            self.assertEqual(original_levels, list_lot_item_levels(item).to_json(orient='records'))
            assert_quality_draft(self, app, prefix)
            app.button(key=prefix + 'adopt').click().run()
            assert_clean(app)
            self.assertNotIn('batch_item_dialog', app.session_state)
            quality = json.loads(get_batch_item_context(item)['item']['quality_review_json'])
            self.assertEqual('confirmed', quality['status'])
            self.assertEqual('批次重核后的实验室 SOP', quality['recorded']['source_name'])
            activate_lot_config(fixture['config_id'])
            self.assertEqual('active', get_lot_config(fixture['config_id'])['status'])

    def test_manual_cancel_continue_preserves_evidence_then_frozen_batch_omits_recheck(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation(manual=True)
            app, item, prefix = open_quality(fixture)
            original = database_state()
            app.text_input(key=prefix + 'product_person').set_value('本批次人工选品核对人')
            app.text_area(key=prefix + 'product_evidence').set_value('本批次待保存的仪器试剂与质控品适用依据。')
            app.run()
            assert_clean(app)
            app.button(key='batch_quality_close').click().run()
            assert_clean(app)
            self.assertTrue(app.session_state['batch_item_dialog']['discard'])
            self.assertEqual(original, database_state())
            app.button(key='batch_quality_continue').click().run()
            assert_clean(app)
            self.assertEqual('本批次人工选品核对人', app.text_input(key=prefix + 'product_person').value)
            self.assertEqual('本批次待保存的仪器试剂与质控品适用依据。',
                             app.text_area(key=prefix + 'product_evidence').value)
            app.button(key=prefix + 'product_confirm').click().run()
            app.button(key='batch_quality_close').click().run()
            app.button(key='batch_quality_continue').click().run()
            assert_clean(app)
            self.assertFalse(app.button(key=prefix + 'product_save').disabled)
            self.assertEqual('本批次人工选品核对人', app.text_input(key=prefix + 'product_person').value)
            self.assertEqual('本批次待保存的仪器试剂与质控品适用依据。',
                             app.text_area(key=prefix + 'product_evidence').value)
            self.assertEqual(original, database_state(), '继续编辑保留已确认草稿，但尚未显式保存')
            app.button(key=prefix + 'product_save').click().run()
            assert_clean(app)
            saved = get_batch_item_context(item)
            review = json.loads(saved['item']['product_choice_review_json'])
            self.assertEqual('manual', review['basis'])
            self.assertEqual('本批次人工选品核对人', review['confirmed_by'])
            self.assertEqual('本批次待保存的仪器试剂与质控品适用依据。', review['evidence'])
            # Quality inputs were not modified; close any confirmation then open
            # the same actual lot after its normal explicit activation.
            app.button(key='batch_quality_close').click().run()
            if 'batch_item_dialog' in app.session_state:
                app.button(key='batch_quality_discard').click().run()
            activate_lot_config(fixture['config_id'])
            save_qc_product_relation(relation(fixture['scope']) | dict(
                qc_material_id=fixture['scope']['qc_material_id'],
                scope_note='另一个明确范围', test_item_id=None))
            frozen_before = database_state()
            app.button(key=f'open_quality_{item}').click().run()
            assert_clean(app)
            self.assertFalse(get_batch_item_context(item)['editable'])
            self.assertFalse(any(button.key.startswith(prefix + 'product') for button in app.button))
            self.assertFalse(any(button.key == prefix + 'adopt' for button in app.button))
            self.assertEqual(frozen_before, database_state())
            self.assertEqual(review, json.loads(get_batch_item_context(item)['item']['product_choice_review_json']))

    def test_owner_changed_during_explicit_save_is_rejected_without_partial_review(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation()
            app, item, prefix = open_quality(fixture)
            fill_quality_draft(app, prefix)
            app.button(key=prefix + 'product_confirm').click().run()
            assert_clean(app)
            before = get_batch_item_context(item)
            pending = deepcopy(app.session_state['batch_item_dialog']['product_recheck']['draft'])
            saved_review = before['item']['product_choice_review_json']
            with db.read_snapshot() as connection:
                snapshots = tuple(connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id',
                    (fixture['config_id'],)).fetchall())
            def race_owner(scope, item_id, review, *, expected_revision):
                # Simulate the owner being disabled after the UI's initial read,
                # but before its atomic suitability save starts.
                with db.atomic_write() as connection:
                    connection.execute('UPDATE qc_lot_configs SET is_disabled=1 WHERE id=?',
                                       (fixture['config_id'],))
                return save_draft_product_choice(scope, item_id, review, expected_revision=expected_revision)
            with patch('services.qc_product_recommendation_service.save_draft_product_choice', side_effect=race_owner):
                app.button(key=prefix + 'product_save').click().run()
            assert_clean(app)
            self.assertTrue(any('仅待确认草稿' in error.value for error in app.error))
            after = get_batch_item_context(item)
            self.assertFalse(after['editable'])
            self.assertEqual(before['revision'], after['revision'])
            self.assertEqual(saved_review, after['item']['product_choice_review_json'])
            self.assertEqual(before['item']['quality_review_json'], after['item']['quality_review_json'])
            self.assertEqual(pending, app.session_state['batch_item_dialog']['product_recheck']['draft'])
            assert_quality_draft(self, app, prefix)
            with db.read_snapshot() as connection:
                self.assertEqual(snapshots, tuple(connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id',
                    (fixture['config_id'],)).fetchall()))
            app.run()
            assert_clean(app)
            self.assertFalse(any(button.key == prefix + 'product_save' for button in app.button))
            app.button(key='batch_quality_close').click().run()
            app.button(key='batch_quality_continue').click().run()
            assert_clean(app)
            self.assertIn('batch_item_dialog', app.session_state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
