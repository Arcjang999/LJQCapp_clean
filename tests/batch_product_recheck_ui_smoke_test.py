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


def product_page(items):
    import streamlit as st
    from ui.batch_dialogs import open_batch_item_dialog, render_batch_item_dialog
    for item in items:
        if st.button('核对质控品', key=f'open_product_{item}'):
            open_batch_item_dialog(item, kind='product')
        if st.button('质量目标', key=f'open_quality_{item}'):
            open_batch_item_dialog(item, kind='quality')
    render_batch_item_dialog()


def open_product(fixture):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_function(product_page, args=(list(fixture['items'].values()),), default_timeout=30).run()
    item = fixture['items']['zscore']
    app.button(key=f'open_product_{item}').click().run()
    assert_clean(app)
    prefix = 'batch_product_' + app.session_state['batch_item_dialog']['token']
    return app, item, prefix


class BatchProductRecheckUITests(unittest.TestCase):
    def test_candidate_confirmation_is_one_save_and_does_not_modify_quality_or_levels(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation()
            app, item, prefix = open_product(fixture)
            before = get_batch_item_context(item)
            original_levels = list_lot_item_levels(item).to_json(orient='records')
            original_quality = before['item']['quality_review_json']
            unchanged = database_state()
            self.assertEqual(unchanged, database_state())
            self.assertFalse(any(row.label == '确认所选质控品' for row in app.button))
            self.assertEqual('确认并保存', app.button(key=prefix + '_confirm').label)
            app.button(key=prefix + '_confirm').click().run()
            assert_clean(app)
            self.assertNotIn('batch_item_dialog', app.session_state)
            after = get_batch_item_context(item)
            self.assertEqual(before['revision'] + 1, after['revision'])
            review = json.loads(after['item']['product_choice_review_json'])
            self.assertNotEqual(fixture['original']['revision'], review['revision'])
            self.assertEqual('recommendation', review['basis'])
            self.assertEqual(original_quality, after['item']['quality_review_json'])
            self.assertEqual(original_levels, list_lot_item_levels(item).to_json(orient='records'))
            activate_lot_config(fixture['config_id'])
            self.assertEqual('active', get_lot_config(fixture['config_id'])['status'])

    def test_manual_cancel_continue_preserves_evidence_then_frozen_batch_is_readonly(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation(manual=True)
            app, item, prefix = open_product(fixture)
            original = database_state()
            app.text_input(key=prefix + '_person').set_value('本批次人工选品核对人')
            app.text_area(key=prefix + '_evidence').set_value('本批次待保存的仪器试剂与质控品适用依据。')
            app.run()
            app.button(key='batch_product_cancel').click().run()
            assert_clean(app)
            self.assertTrue(app.session_state['batch_item_dialog']['discard'])
            self.assertEqual(original, database_state())
            app.button(key='batch_product_keep').click().run()
            assert_clean(app)
            self.assertEqual('本批次人工选品核对人', app.text_input(key=prefix + '_person').value)
            self.assertEqual('本批次待保存的仪器试剂与质控品适用依据。', app.text_area(key=prefix + '_evidence').value)
            app.button(key=prefix + '_confirm').click().run()
            assert_clean(app)
            self.assertNotIn('batch_item_dialog', app.session_state)
            saved = get_batch_item_context(item)
            review = json.loads(saved['item']['product_choice_review_json'])
            self.assertEqual('manual', review['basis'])
            self.assertEqual('本批次人工选品核对人', review['confirmed_by'])
            activate_lot_config(fixture['config_id'])
            save_qc_product_relation(relation(fixture['scope']) | dict(
                qc_material_id=fixture['scope']['qc_material_id'], scope_note='另一个明确范围', test_item_id=None))
            frozen_before = database_state()
            app.button(key=f'open_product_{item}').click().run()
            assert_clean(app)
            self.assertFalse(get_batch_item_context(item)['editable'])
            self.assertFalse(any(button.label == '确认并保存' for button in app.button))
            self.assertEqual(frozen_before, database_state())
            self.assertEqual(review, json.loads(get_batch_item_context(item)['item']['product_choice_review_json']))

    def test_owner_changed_during_save_rejects_without_partial_review(self):
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation()
            app, item, prefix = open_product(fixture)
            before = get_batch_item_context(item)
            pending = deepcopy(app.session_state['batch_item_dialog']['product_recheck']['draft'])
            saved_review = before['item']['product_choice_review_json']
            with db.read_snapshot() as connection:
                snapshots = tuple(connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id',
                    (fixture['config_id'],)).fetchall())
            def race_owner(scope, item_id, review, *, expected_revision):
                with db.atomic_write() as connection:
                    connection.execute('UPDATE qc_lot_configs SET is_disabled=1 WHERE id=?', (fixture['config_id'],))
                return save_draft_product_choice(scope, item_id, review, expected_revision=expected_revision)
            with patch('services.qc_product_recommendation_service.save_draft_product_choice', side_effect=race_owner):
                app.button(key=prefix + '_confirm').click().run()
            assert_clean(app)
            self.assertTrue(any('已停用' in error.value and '先恢复' in error.value for error in app.error))
            after = get_batch_item_context(item)
            self.assertFalse(after['editable'])
            self.assertEqual(before['revision'], after['revision'])
            self.assertEqual(saved_review, after['item']['product_choice_review_json'])
            self.assertEqual(before['item']['quality_review_json'], after['item']['quality_review_json'])
            self.assertEqual(pending, app.session_state['batch_item_dialog']['product_recheck']['draft'])
            with db.read_snapshot() as connection:
                self.assertEqual(snapshots, tuple(connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id',
                    (fixture['config_id'],)).fetchall()))
            app.run()
            self.assertFalse(any(button.label == '确认并保存' for button in app.button))
            app.button(key='batch_product_close').click().run()
            self.assertNotIn('batch_item_dialog', app.session_state)

    def test_quality_form_no_longer_embeds_the_product_editor(self):
        from streamlit.testing.v1 import AppTest
        with TemporaryDatabaseContext():
            fixture = draft_with_changed_relation()
            item = fixture['items']['zscore']
            app = AppTest.from_function(product_page, args=([item],), default_timeout=30).run()
            app.button(key=f'open_quality_{item}').click().run()
            assert_clean(app)
            self.assertFalse(any(row.label in ('确认并保存', '确认所选质控品', '保存本批次质控品确认')
                                 for row in app.button))
            self.assertFalse(any(row.label == '质控品确认人' for row in app.text_input))


if __name__ == '__main__':
    unittest.main(verbosity=2)
