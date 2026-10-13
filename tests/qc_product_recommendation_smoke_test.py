"""Applicability is scoped evidence; changes invalidate drafts without changing algorithms."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, validate_qc_product_choice, validate_choice_review,
    save_qc_product_relation, set_qc_product_relation_disabled, list_qc_product_relations,
    save_draft_product_choice,
)
from tests.zscore_v12_integration_smoke_test import IsolatedDatabase
from tests.zscore_v12_fixtures import seed_zscore_configuration


def scope_for(fixture):
    with db.read_snapshot() as connection:
        row = connection.execute('''SELECT t.lab_instrument_id,t.qc_material_id,i.reagent_id,i.method_id,i.test_item_id,i.unit_id
            FROM qc_project_templates t JOIN qc_project_template_items i ON i.template_id=t.id
            WHERE t.id=?''', (fixture['template_id'],)).fetchone()
    return dict(row)


def selection(scope):
    return {key: scope[key] for key in ('lab_instrument_id', 'reagent_id', 'test_item_id', 'method_id')}


def relation(scope, **updates):
    return dict(selection(scope), qc_material_id=scope['qc_material_id'], scope_note='本仪器、试剂及检验项目',
                evidence='厂家适用说明和本实验室核对记录', confirmed_by='适用资料核对人', **updates)


def reviewed_project(scope, *, name, manual=False):
    from services.project_workspace_service import save_project_details, save_single_test_item
    from services.project_config_service import get_project_template
    review = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope),
        manual_review={'confirmed_by': '实验室核对人', 'evidence': '已人工核对本检测系统的适用性'} if manual else None)
    values = dict(template_name=name, lab_instrument_id=scope['lab_instrument_id'],
        qc_material_id=scope['qc_material_id'], default_reagent_id=scope['reagent_id'],
        default_method_id=scope['method_id'], default_qc_method='zscore', default_level_count=2,
        test_item_id=scope['test_item_id'], product_choice_review=review)
    tid = save_project_details(values)
    item = dict(test_item_id=scope['test_item_id'], reagent_id=scope['reagent_id'], method_id=scope['method_id'],
        unit_id=scope['unit_id'], qc_method='zscore', input_value_type='raw', level_count=2, target_n=20,
        product_choice_review=review)
    iid = save_single_test_item(tid, item, expected_revision=get_project_template(tid)['revision_no'])
    return tid, iid, review


def database_state():
    with db.read_snapshot() as connection:
        return tuple(connection.iterdump())


class ProductRecommendationTests(unittest.TestCase):
    def assert_stale_rejection_preserves_database(self, action):
        before = database_state()
        with self.assertRaisesRegex(ValueError, '可用的质控品资料已修改'):
            action()
        self.assertEqual(before, database_state())

    def test_narrow_scope_manual_choice_and_no_generated_materials(self):
        with IsolatedDatabase():
            f = seed_zscore_configuration()
            scope = scope_for(f)
            with db.read_snapshot() as connection:
                before = tuple(connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                               for table in ('md_qc_material_lots', 'md_qc_levels', 'batches'))
            with self.assertRaises(ValueError):
                validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope))
            manual = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope),
                manual_review={'confirmed_by': '实验室人员', 'evidence': '已核对本项目适用性'})
            self.assertEqual('manual', manual['basis'])
            save_qc_product_relation(relation(scope))
            self.assertEqual(1, len(get_qc_product_recommendations(**selection(scope))['candidates']))
            broad = dict(selection(scope), test_item_id=None)
            self.assertEqual([], get_qc_product_recommendations(**broad)['candidates'])
            with db.read_snapshot() as connection:
                after = tuple(connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                              for table in ('md_qc_material_lots', 'md_qc_levels', 'batches'))
            self.assertEqual(before, after)

    def test_relation_edit_disable_restore_and_stale_selection(self):
        with IsolatedDatabase():
            scope = scope_for(seed_zscore_configuration())
            rid = save_qc_product_relation(relation(scope))
            review = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope))
            values = relation(scope); values['evidence'] = '补充的平台适用核对依据'
            save_qc_product_relation(values, relation_id=rid, expected_revision=1)
            with self.assertRaisesRegex(ValueError, '质控品资料已修改'):
                validate_choice_review(review, qc_material_id=scope['qc_material_id'], **selection(scope))
            set_qc_product_relation_disabled(rid, disabled=True, reason='重新核对适用资料', confirmed_by='核对人员', expected_revision=2)
            self.assertEqual([], get_qc_product_recommendations(**selection(scope))['candidates'])
            set_qc_product_relation_disabled(rid, disabled=False, reason='已补充核对', confirmed_by='核对人员', expected_revision=3)
            self.assertEqual(1, len(list_qc_product_relations()))
            with db.read_snapshot() as connection:
                self.assertEqual(['create', 'edit', 'disable', 'restore'], [row[0] for row in connection.execute(
                    'SELECT action FROM qc_product_recommendation_history ORDER BY id')])

    def test_project_and_item_require_the_declared_complete_scope(self):
        from services.project_workspace_service import save_project_details, save_single_test_item
        from services.project_config_service import get_project_template
        with IsolatedDatabase():
            scope = scope_for(seed_zscore_configuration())
            review = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope),
                manual_review={'confirmed_by': '实验室人员', 'evidence': '本项目适用性已核对'})
            values = dict(template_name='完整范围快捷建项', lab_instrument_id=scope['lab_instrument_id'],
                qc_material_id=scope['qc_material_id'], default_reagent_id=scope['reagent_id'],
                default_method_id=scope['method_id'], default_qc_method='zscore', default_level_count=2,
                product_choice_review=review)
            with self.assertRaisesRegex(ValueError, '不一致'):
                save_project_details(values)
            values['test_item_id'] = scope['test_item_id']
            tid = save_project_details(values)
            item = dict(test_item_id=scope['test_item_id'], reagent_id=scope['reagent_id'], method_id=scope['method_id'],
                unit_id=scope['unit_id'], qc_method='zscore', input_value_type='raw', level_count=2, target_n=20,
                product_choice_review=review)
            iid = save_single_test_item(tid, item, expected_revision=get_project_template(tid)['revision_no'])
            with db.read_snapshot() as connection:
                header = json.loads(connection.execute('SELECT product_choice_review_json FROM qc_project_templates WHERE id=?', (tid,)).fetchone()[0])
                child = json.loads(connection.execute('SELECT product_choice_review_json FROM qc_project_template_items WHERE id=?', (iid,)).fetchone()[0])
            self.assertEqual(header, child)
            self.assertEqual(selection(scope), child['context'])

    def test_panel_preview_rejects_changed_relations_without_partial_items(self):
        from services.master_data_service import create_test_item
        from services.project_config_service import preview_panel_items, save_panel_items, list_template_items
        with IsolatedDatabase():
            f = seed_zscore_configuration(); scope = scope_for(f)
            values = relation(scope); values['test_item_id'] = None
            rid = save_qc_product_relation(values)
            test = create_test_item(chinese_name='整组新增检验项目', default_unit_id=scope['unit_id'])
            row = dict(test_item_id=test, reagent_id=scope['reagent_id'], method_id=scope['method_id'], unit_id=scope['unit_id'],
                       qc_method='lj', input_value_type='raw', level_count=1, target_n=20)
            preview = preview_panel_items(f['template_id'], [row])
            values['evidence'] = '整组预览后修改的依据'
            save_qc_product_relation(values, relation_id=rid, expected_revision=1)
            with self.assertRaisesRegex(ValueError, '重新选择质控品'):
                save_panel_items(f['template_id'], preview['rows'], expected_revision=preview['expected_revision'])
            self.assertEqual(1, len(list_template_items(f['template_id'])))

    def test_parent_status_and_model_changes_affect_new_candidates_and_drafts(self):
        with IsolatedDatabase():
            f = seed_zscore_configuration(); scope = scope_for(f)
            save_qc_product_relation(relation(scope))
            review = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope))
            with db.get_connection() as connection:
                model = connection.execute('SELECT instrument_model_id FROM lab_instruments WHERE id=?', (scope['lab_instrument_id'],)).fetchone()[0]
                manufacturer = connection.execute('SELECT manufacturer_id FROM md_instrument_models WHERE id=?', (model,)).fetchone()[0]
                connection.execute("UPDATE md_instrument_models SET model='核对后的型号' WHERE id=?", (model,))
            with self.assertRaisesRegex(ValueError, '质控品资料已修改'):
                validate_choice_review(review, qc_material_id=scope['qc_material_id'], **selection(scope))
            with db.get_connection() as connection:
                connection.execute('UPDATE md_instrument_models SET is_disabled=1 WHERE id=?', (model,))
            self.assertEqual([], get_qc_product_recommendations(**selection(scope))['candidates'])
            with self.assertRaisesRegex(ValueError, '型号已停用'):
                validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope),
                    manual_review={'confirmed_by': '人员', 'evidence': '不能绕过停用资料'})
            with db.get_connection() as connection:
                connection.execute('UPDATE md_instrument_models SET is_disabled=0 WHERE id=?', (model,))
                connection.execute('UPDATE md_manufacturers SET is_disabled=1 WHERE id=?', (manufacturer,))
            self.assertEqual([], get_qc_product_recommendations(**selection(scope))['candidates'])
            with db.read_snapshot() as connection:
                self.assertEqual(0, connection.execute('SELECT is_disabled FROM batches WHERE id=?', (f['batch_id'],)).fetchone()[0])

    def test_fresh_explicit_review_replaces_a_stale_panel_source_token(self):
        from services.master_data_service import create_test_item
        from services.project_config_service import preview_panel_items, save_panel_items, list_template_items
        with IsolatedDatabase():
            f = seed_zscore_configuration(); scope = scope_for(f)
            values = relation(scope); values['test_item_id'] = None
            rid = save_qc_product_relation(values)
            test = create_test_item(chinese_name='已重新核对的整组新增项', default_unit_id=scope['unit_id'])
            row = dict(test_item_id=test, reagent_id=scope['reagent_id'], method_id=scope['method_id'], unit_id=scope['unit_id'],
                       qc_method='lj', input_value_type='raw', level_count=1, target_n=20)
            preview = preview_panel_items(f['template_id'], [row])
            values['evidence'] = '预览后补充的适用依据'
            save_qc_product_relation(values, relation_id=rid, expected_revision=1)
            for item in preview['rows']:
                item['product_choice_review'] = validate_qc_product_choice(qc_material_id=scope['qc_material_id'],
                    **dict(selection(scope), test_item_id=item['test_item_id']))
            save_panel_items(f['template_id'], preview['rows'], expected_revision=preview['expected_revision'])
            self.assertEqual(2, len(list_template_items(f['template_id'])))

    def test_configuration_import_preview_freezes_recommendation_version(self):
        from services.project_config_io_service import build_project_template_xlsx, prepare_project_template_import, import_project_template_xlsx
        with IsolatedDatabase():
            f = seed_zscore_configuration(); scope = scope_for(f)
            data = build_project_template_xlsx(f['template_id'])
            preview = prepare_project_template_import(f['template_id'], data)
            self.assertFalse(preview['errors'], preview['errors'])
            save_qc_product_relation(relation(scope))
            with self.assertRaisesRegex(ValueError, '适用.*变化'):
                import_project_template_xlsx(f['template_id'], data, expected_revision=preview['expected_revision'],
                    file_sha256=preview['file_sha256'], product_version=preview['product_version'])

    def test_daily_and_single_entry_freeze_same_relationship_revision(self):
        from tests.daily_entry_smoke_test import seed_twenty
        from services.daily_context_service import get_daily_context
        from services.daily_entry_service import validate_submission, submit_daily, DailyEntryError
        from services.lot_lifecycle_service import result_lot_options
        with IsolatedDatabase():
            f = seed_twenty(); d = f['data']; when = '2026-09-28 08:00:00'
            args = dict(lab_instrument_id=d['lab_instrument_id'], qc_material_id=d['qc_material_id'],
                        qc_material_lot_id=f['main_lot'], lot_config_id=f['config_id'], test_time=when)
            context = get_daily_context(**args); item = context['items'][0]
            request = dict(submission_id='relationship-change', selection=context['selection'], context_revision=context['context_revision'],
                test_time=when, operator='本次检测人', purpose='routine', items=[dict(row_key=item['row_key'],
                    reagent_lot_id=f['reagent'], levels=[dict(qc_level_id=level['qc_level_id'], value='100') for level in item['levels']])])
            validation = validate_submission(request)
            self.assertTrue(validation['valid'], validation['errors'])
            previous = result_lot_options('lj', item['runtime_batch_id'], when)['revision']
            save_qc_product_relation(dict(reagent_id=d['reagent_id'], qc_material_id=d['qc_material_id'], lab_instrument_id=d['lab_instrument_id'],
                test_item_id=item['test_item_id'], method_id=d['method_id'], scope_note='所选仪器和检测项', evidence='经实验室核对的资料', confirmed_by='核对人员'))
            with self.assertRaises(DailyEntryError):
                submit_daily(validation['frozen_request'])
            with self.assertRaisesRegex(ValueError, '适用资料已变化'):
                db.add_result(item['runtime_batch_id'], when, 100, operator='本次检测人',
                    lot_selection={'reagent_lot_id': f['reagent'], 'expected_revision': previous})
            current = get_daily_context(**args)
            self.assertNotEqual(context['context_revision'], current['context_revision'])
            with db.read_snapshot() as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM results').fetchone()[0])

    def test_saved_project_draft_cannot_activate_after_relationship_revision_changes(self):
        from services.project_config_service import activate_project_template, get_project_template
        from tests.quality_review_fixtures import confirm_fixture_project
        with IsolatedDatabase():
            scope = scope_for(seed_zscore_configuration())
            rid = save_qc_product_relation(relation(scope))
            tid, iid, old_review = reviewed_project(scope, name='关系变化待确认项目')
            confirm_fixture_project(tid)
            changed = relation(scope); changed['evidence'] = '保存草稿后重新核对的适用依据'
            save_qc_product_relation(changed, relation_id=rid, expected_revision=1)
            self.assert_stale_rejection_preserves_database(lambda: activate_project_template(tid))
            self.assertEqual('draft', get_project_template(tid)['status'])
            fresh = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope))
            self.assertNotEqual(old_review['revision'], fresh['revision'])
            revision = save_draft_product_choice('project', iid, fresh,
                expected_revision=get_project_template(tid)['revision_no'])
            activate_project_template(tid, expected_revision=revision)
            self.assertEqual('active', get_project_template(tid)['status'])

    def test_pending_project_and_lot_quality_confirmation_reject_stale_manual_choices(self):
        from services.project_config_service import activate_project_template, get_project_template, list_lot_config_items
        from services.material_workflow_service import create_material_config
        from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_quality
        with IsolatedDatabase():
            fixture = seed_zscore_configuration(); scope = scope_for(fixture)
            tid, iid, old_review = reviewed_project(scope, name='质量确认前重核人工选品', manual=True)
            confirm_fixture_project(tid)
            activate_project_template(tid)
            cid = create_material_config(template_id=tid, selections={iid: fixture['level_ids']})
            ciid = int(list_lot_config_items(cid).iloc[0]['id'])
            # The project is deliberately pending again, whereas its new lot has
            # never been enabled. Adding a relation invalidates both old reviews.
            with db.atomic_write() as connection:
                connection.execute("UPDATE qc_project_templates SET status='draft' WHERE id=?", (tid,))
            save_qc_product_relation(relation(scope))
            for owner, item_id in (('project', iid), ('lot', ciid)):
                with self.subTest(scope=owner):
                    self.assert_stale_rejection_preserves_database(lambda: confirm_fixture_quality(owner, item_id))
            self.assertEqual('draft', get_project_template(tid)['status'])
            with db.read_snapshot() as connection:
                for table, identifier in (('qc_project_template_items', iid), ('qc_lot_config_items', ciid)):
                    self.assertEqual(old_review, json.loads(connection.execute(
                        'SELECT product_choice_review_json FROM ' + table + ' WHERE id=?', (identifier,)).fetchone()[0]))

    def test_never_enabled_lot_requires_fresh_scope_then_freezes_confirmed_evidence(self):
        from services.project_config_service import (activate_project_template, activate_lot_config,
            get_lot_config, list_lot_config_items, validate_lot_config)
        from services.material_workflow_service import create_material_config
        from services.zscore_workbench_service import sync_zscore_workbench_bindings
        from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_lot
        with IsolatedDatabase():
            fixture = seed_zscore_configuration(); scope = scope_for(fixture)
            rid = save_qc_product_relation(relation(scope))
            tid, iid, _ = reviewed_project(scope, name='待启用批次适用核对')
            confirm_fixture_project(tid); activate_project_template(tid)
            cid = create_material_config(template_id=tid, selections={iid: fixture['level_ids']})
            ciid = int(list_lot_config_items(cid).iloc[0]['id'])
            confirm_fixture_lot(cid)
            changed = relation(scope); changed['evidence'] = '批次待启用期间更新的适用依据'
            save_qc_product_relation(changed, relation_id=rid, expected_revision=1)
            self.assert_stale_rejection_preserves_database(lambda: activate_lot_config(cid))
            fresh = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope))
            wrong = dict(fresh, context=dict(fresh['context'], test_item_id=None))
            before = database_state()
            with self.assertRaisesRegex(ValueError, '不一致'):
                save_draft_product_choice('lot', ciid, wrong, expected_revision=get_lot_config(cid)['revision_no'])
            self.assertEqual(before, database_state())
            save_draft_product_choice('lot', ciid, fresh, expected_revision=get_lot_config(cid)['revision_no'])
            activate_lot_config(cid); sync_zscore_workbench_bindings()
            with db.read_snapshot() as connection:
                confirmed = connection.execute('SELECT product_choice_review_json FROM qc_lot_config_items WHERE id=?', (ciid,)).fetchone()[0]
                snapshots = tuple(row[0] for row in connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY revision_no', (cid,)))
                binding = connection.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE lot_config_item_id=?', (ciid,)).fetchone()[0]
            changed['evidence'] = '启用以后新增的推荐资料'
            save_qc_product_relation(changed, relation_id=rid, expected_revision=2)
            self.assertEqual([], validate_lot_config(cid))
            with self.assertRaisesRegex(ValueError, '本批次已确认'):
                save_draft_product_choice('lot', ciid, fresh, expected_revision=get_lot_config(cid)['revision_no'])
            with db.read_snapshot() as connection:
                self.assertEqual(confirmed, connection.execute('SELECT product_choice_review_json FROM qc_lot_config_items WHERE id=?', (ciid,)).fetchone()[0])
                self.assertEqual(snapshots, tuple(row[0] for row in connection.execute(
                    'SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY revision_no', (cid,))))
                self.assertEqual(binding, connection.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE lot_config_item_id=?', (ciid,)).fetchone()[0])

    def test_unreviewed_new_configuration_import_stays_pending_until_explicit_choice(self):
        from services.project_config_service import (create_project_template, activate_project_template,
            get_project_template, list_template_items)
        from services.project_config_io_service import build_project_template_xlsx, import_project_template_xlsx
        from services.quality_review_service import save_recorded_requirement
        from services.quality_target_service import item_context
        from tests.quality_review_fixtures import fixture_conditions
        with IsolatedDatabase():
            fixture = seed_zscore_configuration(); scope = scope_for(fixture)
            tid = create_project_template(template_name='导入后需明确选品的新草稿',
                lab_instrument_id=scope['lab_instrument_id'], qc_material_id=scope['qc_material_id'])
            import_project_template_xlsx(tid, build_project_template_xlsx(fixture['template_id']))
            iid = int(list_template_items(tid).iloc[0]['id'])
            item = item_context('project', iid)
            def confirm_quality():
                save_recorded_requirement('project', iid, source_name='隔离验收要求', source_version='TEST-1',
                    requirement_text='合成验收数据的质量要求', confirmed_by='隔离确认人', evidence='已核对完整质量适用条件',
                    **fixture_conditions(item))
            before = database_state()
            with self.assertRaisesRegex(ValueError, '不一致'):
                confirm_quality()
            self.assertEqual(before, database_state())
            with self.assertRaisesRegex(ValueError, '适用依据'):
                activate_project_template(tid)
            self.assertEqual(before, database_state())
            fresh = validate_qc_product_choice(qc_material_id=scope['qc_material_id'], **selection(scope),
                manual_review={'confirmed_by': '导入资料核对人', 'evidence': '已逐项人工核对所导入检测系统适用性'})
            save_draft_product_choice('project', iid, fresh, expected_revision=get_project_template(tid)['revision_no'])
            confirm_quality(); activate_project_template(tid)
            self.assertEqual('active', get_project_template(tid)['status'])

    def test_saving_other_item_fields_does_not_refresh_a_stale_manual_choice(self):
        from services.project_workspace_service import save_single_test_item
        from services.project_config_service import get_project_template, list_template_items
        with IsolatedDatabase():
            scope = scope_for(seed_zscore_configuration())
            tid, iid, _ = reviewed_project(scope, name='编辑备注不可替代选品重核', manual=True)
            save_qc_product_relation(relation(scope))
            values = list_template_items(tid).astype(object).where(lambda frame: frame.notna(), None).iloc[0].to_dict()
            values['notes'] = '只修改项目备注'
            self.assert_stale_rejection_preserves_database(lambda: save_single_test_item(tid, values,
                expected_revision=get_project_template(tid)['revision_no'], item_id=iid))


if __name__ == '__main__':
    unittest.main(verbosity=2)
