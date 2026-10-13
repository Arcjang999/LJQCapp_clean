"""The short quality setup path still uses real activation and frozen sources."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.daily_context_service import get_daily_context
from services.material_workflow_service import create_material_config
from services.project_config_service import (activate_project_template, activate_lot_config,
    get_project_template, list_lot_config_items)
from services.project_workspace_service import (save_project_details, save_single_test_item,
    resolve_batch_binding)
from services.quality_review_service import (runtime_review, validate_quality_review, build_review,
    save_recorded_requirement)
from services.quality_setup_service import (quality_setup_plan, simple_quality_payload,
    save_simple_quality)
from services.quality_target_service import (item_context, runtime_goal, batch_quality_summary,
    get_requirement, adopt_requirement)
from services.quick_project_setup_service import get_quick_setup_context, prepare_quick_project_setup
from services.report_service import (build_lj_period_report_package, build_zscore_period_report_package,
    build_lj_period_report_pdf)
from services.report_pdf_layout import _build_quality_pages
from services.instant_service import save_instant_result
from services.master_data_service import create_unit, list_units
from services.qc_product_recommendation_service import validate_qc_product_choice
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values, database_contents, save
from zscore_logic import create_zscore_run, get_zscore_runs, get_template_id_for_level_count


METHODS = [('lj', 1), ('zscore', 2), ('zscore', 3), ('instant', 1)]


def simple_values(method='lj', levels=1, *, limit=None):
    values = metadata_values(method, levels)
    values['quality'] = dict(mode='sd_cv', checked=True, cv_limit=limit,
        confirmed_by='简化配置验收人', evidence='',
        context=values['quality']['context'], process_requirements={'source_ids': [], 'requirement_text': ''})
    return values


def draft(values):
    fields = ('test_item_id', 'qc_method', 'input_value_type', 'unit_id', 'method_id',
              'reagent_id', 'level_count', 'target_n', 'cv_limit', 'quality_target_source_text')
    project = dict(template_name=values['template_name'], lab_instrument_id=values['lab_instrument_id'],
        qc_material_id=values['qc_material_id'], default_reagent_id=values['reagent_id'],
        default_method_id=values['method_id'], default_qc_method=values['qc_method'],
        default_level_count=values['level_count'], test_item_id=values['test_item_id'],
        product_choice_review=deepcopy(values['product_choice_review']))
    template_id = save_project_details(project)
    item_id = save_single_test_item(template_id,
        dict({key: values.get(key) for key in fields}, product_choice_review=deepcopy(values['product_choice_review'])),
        expected_revision=get_project_template(template_id)['revision_no'])
    return template_id, item_id


def setup_args(values, limit=None):
    return dict(context=deepcopy(values['quality']['context']), cv_limit=limit,
                confirmed_by='简化配置验收人', process_requirements={'source_ids': [], 'requirement_text': ''})


def insert_detection(method, batch, levels, index, variation):
    when = f'2026-10-{index + 1:02d} 08:00:00'
    if method == 'zscore':
        return create_zscore_run(batch, when, '检测验收人',
            [dict(level_id=f'Level {order}', raw_value=100 + 50*(order-1) + variation)
             for order in range(1, levels+1)],
            template_id=get_template_id_for_level_count(levels), required_n=20)
    if method == 'lj':
        return db.add_result(batch, when, 100+variation, operator='检测验收人')
    return save_instant_result(batch_id=batch, test_time=when, operator='检测验收人',
                               value=100+variation, log_value=None)


def change_builtin_catalog():
    """A real local source edit must invalidate pending confirmation, not frozen use."""
    with db.get_connection() as connection:
        row = connection.execute("SELECT id,payload_json FROM qc_quality_catalog WHERE origin='builtin' ORDER BY id LIMIT 1").fetchone()
        spec = json.loads(row['payload_json'])
        spec['notes'] += ' 隔离目录修订'
        connection.execute('UPDATE qc_quality_catalog SET payload_json=? WHERE id=?',
                           (json.dumps(spec, ensure_ascii=False), row['id']))


class SimpleQualitySetupTests(unittest.TestCase):
    def assert_rejected_without_writes(self, action, message=None):
        before = database_contents()
        with self.assertRaises(ValueError) as raised:
            action()
        if message:
            self.assertIn(message, str(raised.exception))
        self.assertEqual(before, database_contents())

    def test_short_setup_activates_four_methods_and_freezes_sd_only_or_uniform_cv(self):
        for method, levels in METHODS:
            for limit in (None, 3.0):
                with self.subTest(method=method, levels=levels, cv=limit), IsolatedDatabase():
                    values = simple_values(method, levels, limit=limit)
                    values['quality'].update(source_name='本实验室现场质控要求', source_version='LOCAL-QC-2026-03',
                        requirement_text='按本实验室实际检测系统已确认的控制参数执行，检验员核对质控品批号。')
                    before = database_contents()
                    plan = quality_setup_plan(get_quick_setup_context(values)['item'], values['quality']['context'])
                    self.assertEqual('sd_cv', plan['state'])
                    self.assertTrue(plan['cv_supported'])
                    preview = prepare_quick_project_setup(values)
                    self.assertEqual(before, database_contents(), 'preparation must not register a private CV source')
                    receipt = save(values, preview)
                    batch = receipt['binding']['runtime_batch_id']
                    with db.read_snapshot() as connection:
                        for scope, identifier in (('project', receipt['template_item_id']), ('lot', receipt['lot_config_item_id'])):
                            self.assertEqual([], validate_quality_review(scope, identifier, connection))
                        self.assertEqual('active', connection.execute('SELECT status FROM qc_project_templates WHERE id=?',
                                                                      (receipt['template_id'],)).fetchone()[0])
                        self.assertEqual('active', connection.execute('SELECT status FROM qc_lot_configs WHERE id=?',
                                                                      (receipt['lot_config_id'],)).fetchone()[0])
                        self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())
                    review, goal = runtime_review(method, batch), runtime_goal(method, batch)
                    self.assertEqual('confirmed', review['status'])
                    search = review['search_record']
                    self.assertEqual('local_catalog', search['scope'])
                    self.assertEqual('no_numeric', search['conclusion'])
                    self.assertNotIn('official_url', search)
                    self.assertIn('不表示已查询全部', search['rationale'])
                    if limit is None:
                        self.assertEqual({}, goal)
                        self.assertEqual('record_only', review['decision'])
                        self.assertEqual('sd_cv', review['recorded']['setup_mode'])
                        self.assertEqual(values['quality']['source_name'], review['recorded']['source_name'])
                        self.assertEqual(values['quality']['source_version'], review['recorded']['source_version'])
                        self.assertEqual(values['quality']['requirement_text'], review['recorded']['requirement_text'])
                        self.assertEqual([], batch_quality_summary(method, batch)['rows'])
                        self.assertIn('未设置CV上限', batch_quality_summary(method, batch)['statistics_scope'])
                    else:
                        self.assertEqual('custom', goal['spec']['origin'])
                        self.assertIn('非国家数值标准', goal['spec']['notes'])
                        self.assertEqual('sd_cv', goal['spec']['setup_mode'])
                        basis = goal['spec']['laboratory_basis']
                        self.assertEqual(values['quality']['source_name'], basis['source_name'])
                        self.assertEqual(values['quality']['source_version'], basis['source_version'])
                        self.assertIn(values['quality']['requirement_text'], basis['requirement_text'])
                        self.assertEqual(1, basis['requirement_text'].count('实验室自定义CV上限为'))
                        self.assertEqual(list(range(1, levels+1)), [row['level_order'] for row in goal['levels']])
                        self.assertEqual(values['level_ids'], [row['qc_level_id'] for row in goal['levels']])
                        self.assertTrue(all(row['rule'] == dict(kind='cv', value=3.0, operator='<=', unit='%') for row in goal['levels']))
                    for index, variation in enumerate(([-.8, .8]*10)+[-.1, .1]):
                        insert_detection(method, batch, levels, index, variation)
                    summary = batch_quality_summary(method, batch)
                    if limit is not None:
                        self.assertEqual(levels, len(summary['rows']))
                        for order, row in enumerate(summary['rows'], 1):
                            if method == 'instant':
                                self.assertEqual(0, row['count'])
                                self.assertIn('过渡期', row['decision'])
                            else:
                                self.assertEqual(2, row['count'])
                                self.assertAlmostEqual((.02**.5)/(100+50*(order-1))*100, row['cv'], places=8)
                                self.assertEqual('满足所选 CV 要求', row['decision'])
                    if method == 'zscore':
                        runs = get_zscore_runs(batch, get_template_id_for_level_count(levels))
                        self.assertEqual('formal_qc', runs[20]['phase'])
                        self.assertEqual('accept', runs[20]['run_status'])
                    if method in ('lj', 'zscore'):
                        builder = build_lj_period_report_package if method == 'lj' else build_zscore_period_report_package
                        package = builder(batch, '2026-10-21', '2026-10-22')
                        self.assertEqual(2, package.report.statistics.formal_count)
                        self.assertEqual(goal, package.report.quality_summary['goal'])
                        if limit is not None:
                            self.assertEqual([2]*levels, [row['count'] for row in package.report.quality_summary['rows']])
                        if method == 'lj':
                            output = ROOT / 'output' / 'pdf' / 'quality-setup-2026-10-13'
                            output.mkdir(parents=True, exist_ok=True)
                            name = 'sd-control-report.pdf' if limit is None else 'custom-cv-report.pdf'
                            (output / name).write_bytes(build_lj_period_report_pdf(package))
                        figures = _build_quality_pages(package.report)
                        page_text = '\n'.join(t.get_text() for figure in figures for ax in figure.axes for t in ax.texts)
                        self.assertIn('本实验室', page_text)
                        self.assertIn(values['quality']['source_name'], page_text.replace('\n', ''))
                        self.assertIn(values['quality']['source_version'], page_text.replace('\n', ''))
                        if limit is None:
                            self.assertIn('未设置CV上限', page_text)
                        else:
                            self.assertIn('实验室自定义CV', page_text)
                        from matplotlib import pyplot as plt
                        for figure in figures:
                            plt.close(figure)
                    frozen = database_contents()
                    self.assertEqual(receipt, save(values, preview))
                    self.assertEqual(frozen, database_contents())
                    self.assert_rejected_without_writes(lambda: save_simple_quality('lot', receipt['lot_config_item_id'],
                                                         **setup_args(values, 2.0)), '已有批次')

    def test_normal_draft_path_clears_previous_cv_and_activates_sd_only_without_catalog_delete(self):
        with IsolatedDatabase():
            values = simple_values('zscore', 3)
            template, project_item = draft(values)
            save_simple_quality('project', project_item, **setup_args(values, 4.0))
            self.assertEqual(4.0, item_context('project', project_item)['cv_limit'])
            old_text = json.loads(item_context('project', project_item)['quality_goal_json'])['spec']['source_clause']
            save_simple_quality('project', project_item, **setup_args(values, 3.0), requirement_text=old_text)
            current_text = json.loads(item_context('project', project_item)['quality_goal_json'])['spec']['source_clause']
            self.assertEqual(1, current_text.count('实验室自定义CV上限为'))
            self.assertIn('≤3%', current_text)
            self.assertNotIn('≤4%', current_text)
            with db.read_snapshot() as connection:
                count = connection.execute("SELECT count(*) FROM qc_quality_catalog WHERE origin='custom'").fetchone()[0]
            save_simple_quality('project', project_item, **setup_args(values, None), requirement_text=current_text)
            self.assertIsNone(item_context('project', project_item)['cv_limit'])
            self.assertEqual('{}', item_context('project', project_item)['quality_goal_json'])
            recorded_text = json.loads(item_context('project', project_item)['quality_review_json'])['recorded']['requirement_text']
            self.assertNotIn('实验室自定义CV上限为', recorded_text)
            with db.read_snapshot() as connection:
                current = item_context('project', project_item, connection)
                review = json.loads(current['quality_review_json'])
                rebuilt = build_review(connection, current, recorded=review['recorded'],
                    confirmed_by=review['confirmed_by'], evidence=review['evidence'], context=review['context'],
                    search_record=review['search_record'], process_requirements=review['process_requirements'])
                self.assertEqual(review['recorded'], rebuilt['recorded'])
                self.assertEqual('sd_cv', rebuilt['recorded']['setup_mode'])
            record_fields = {key: review['recorded'][key] for key in ('source_name', 'source_version', 'requirement_text')}
            record_args = dict(confirmed_by=review['confirmed_by'], evidence=review['evidence'],
                context=review['context'], search_record=review['search_record'], process_requirements=review['process_requirements'])
            self.assert_rejected_without_writes(lambda: save_recorded_requirement('project', project_item,
                **record_fields, **record_args, setup_mode='unrecognized'), '设置方式')
            # Older recorded requirements keep their actual type even if their
            # title happens to match the shortcut's default wording.
            legacy = save_recorded_requirement('project', project_item, **record_fields, **record_args)
            self.assertNotIn('setup_mode', legacy['recorded'])
            save_simple_quality('project', project_item, **setup_args(values, None), requirement_text=recorded_text)
            activate_project_template(template)
            config = create_material_config(template_id=template, selections={project_item: values['level_ids']}, config_name='普通SD批次')
            lot_item = int(list_lot_config_items(config).iloc[0]['id'])
            save_simple_quality('lot', lot_item, **setup_args(values, 4.0))
            self.assertEqual(3, len(json.loads(item_context('lot', lot_item)['quality_goal_json'])['levels']))
            save_simple_quality('lot', lot_item, **setup_args(values, None))
            self.assertIsNone(item_context('lot', lot_item)['cv_limit'])
            self.assertEqual('{}', item_context('lot', lot_item)['quality_goal_json'])
            activate_lot_config(config)
            binding = resolve_batch_binding(lot_item)
            self.assertEqual({}, runtime_goal('zscore', binding['runtime_batch_id']))
            with db.read_snapshot() as connection:
                self.assertEqual(count, connection.execute("SELECT count(*) FROM qc_quality_catalog WHERE origin='custom'").fetchone()[0])

    def test_scale_conditions_invalid_cv_and_mandatory_numeric_never_fall_back(self):
        with IsolatedDatabase():
            values = simple_values()
            item = get_quick_setup_context(values)['item']
            context = values['quality']['context']
            before = database_contents()
            for invalid in (0, -1, float('nan'), float('inf'), 'not-a-number'):
                with self.subTest(invalid=invalid):
                    self.assert_rejected_without_writes(lambda: simple_quality_payload(item, dict(context=context),
                                                                 cv_limit=invalid, confirmed_by='核对人'))
            for mode, scale in [('raw', 'ct'), ('ct', 'ct'), ('log', 'log'), ('raw', 's_co')]:
                with self.subTest(mode=mode, scale=scale):
                    scaled = {**item, 'input_value_type': mode, 'unit_symbol': 'S/CO' if scale == 's_co' else item['unit_symbol']}
                    ctx = {**context, 'result_scale': scale,
                           'result_kind': 'qualitative' if scale == 's_co' else 'quantitative'}
                    self.assert_rejected_without_writes(lambda: simple_quality_payload(scaled, dict(context=ctx),
                                                                 cv_limit=3, confirmed_by='核对人'))
                    if mode in ('ct', 'log'):
                        self.assertIsNone(simple_quality_payload(scaled, dict(context=ctx), confirmed_by='核对人')['cv_limit'])
            missing = {**context, 'purpose': ''}
            self.assertEqual('blocked', quality_setup_plan(item, missing)['state'])
            self.assert_rejected_without_writes(lambda: simple_quality_payload(item, dict(context=missing), confirmed_by='核对人'))
            self.assertEqual(before, database_contents())
        with IsolatedDatabase():
            numeric = metadata_values(numeric=True)
            numeric_item = get_quick_setup_context(numeric)['item']
            self.assertEqual('standard', quality_setup_plan(numeric_item, numeric['quality']['context'])['state'])
            self.assert_rejected_without_writes(lambda: simple_quality_payload(numeric_item,
                dict(context=numeric['quality']['context']), cv_limit=100, confirmed_by='核对人'), '直接引用')
            pending_item = {**numeric_item, 'test_item_name': 'TSH', 'abbreviation': '',
                            'test_aliases': [], 'standard_code': '', 'unit_symbol': 'mg/L'}
            self.assertEqual('blocked', quality_setup_plan(pending_item, numeric['quality']['context'])['state'])
            self.assert_rejected_without_writes(lambda: simple_quality_payload(pending_item,
                dict(context=numeric['quality']['context']), confirmed_by='核对人'), '单位')

    def test_local_reviews_capture_catalog_changes_and_frozen_batches_keep_original_source(self):
        for limit in (None, 3.0):
            with self.subTest(cv=limit), IsolatedDatabase():
                values = simple_values(limit=limit)
                template, item = draft(values)
                save_simple_quality('project', item, **setup_args(values, limit))
                self.assertEqual([], validate_quality_review('project', item))
                change_builtin_catalog()
                self.assertTrue(any('更新' in error for error in validate_quality_review('project', item)))
                self.assert_rejected_without_writes(lambda: activate_project_template(template), '更新')
                save_simple_quality('project', item, **setup_args(values, limit))
                activate_project_template(template)
                config = create_material_config(template_id=template, selections={item: values['level_ids']}, config_name='本地目录核对')
                lot_item = int(list_lot_config_items(config).iloc[0]['id'])
                save_simple_quality('lot', lot_item, **setup_args(values, limit))
                activate_lot_config(config)
                batch = resolve_batch_binding(lot_item)['runtime_batch_id']
                frozen = deepcopy(runtime_review('lj', batch)), deepcopy(runtime_goal('lj', batch))
                change_builtin_catalog()
                self.assertEqual([], validate_quality_review('lot', lot_item))
                self.assertEqual(frozen, (runtime_review('lj', batch), runtime_goal('lj', batch)))
                insert_detection('lj', batch, 1, 0, -.8)
                with db.read_snapshot() as connection:
                    qc_lot = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (values['level_ids'][0],)).fetchone()[0]
                daily = get_daily_context(values['lab_instrument_id'], values['qc_material_id'], qc_lot,
                                          '2026-10-02 08:00:00', lot_config_id=config)
                self.assertFalse(any('质量' in issue['message'] for issue in daily['items'][0]['issues']), daily['items'][0]['issues'])

    def test_signal_clauses_remain_mandatory_and_never_become_a_concentration_cv(self):
        with IsolatedDatabase():
            values = simple_values()
            units = list_units()
            found = units.loc[units.symbol == 'S/CO']
            values['unit_id'] = int(found.iloc[0]['id']) if len(found) else create_unit(symbol='S/CO')
            with db.get_connection() as connection:
                connection.execute("UPDATE md_test_items SET chinese_name='HBsAg',abbreviation='' WHERE id=?", (values['test_item_id'],))
                connection.execute("UPDATE md_methods SET method_name='化学发光免疫测定',principle='',method_code='' WHERE id=?", (values['method_id'],))
            values['product_choice_review'] = validate_qc_product_choice(**{key: values[key] for key in (
                'qc_material_id', 'reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')},
                manual_review={'confirmed_by': '材料核对人', 'evidence': '已核对该隔离信号测定系统的适用质控品。'})
            values['quality']['context'] = dict(technique='immunoassay', result_kind='qualitative',
                result_scale='s_co', purpose='clinical', specimen='血清')
            item = get_quick_setup_context(values)['item']
            plan = quality_setup_plan(item, values['quality']['context'])
            self.assertEqual('sd_cv', plan['state'])
            self.assertFalse(plan['cv_supported'])
            self.assertEqual({'qualitative', 'signal_precision'}, {row['kind'] for row in plan['assessments']})
            omitted = deepcopy(values)
            omitted['quality']['adopted_standard_ids'] = []
            self.assert_rejected_without_writes(lambda: prepare_quick_project_setup(omitted), '适用的全部')
            with_cv = deepcopy(values)
            with_cv['quality']['cv_limit'] = 10
            self.assert_rejected_without_writes(lambda: prepare_quick_project_setup(with_cv), '结果类型')
            preview = prepare_quick_project_setup(values)
            receipt = save(values, preview)
            review = runtime_review('lj', receipt['binding']['runtime_batch_id'])
            self.assertTrue(all(row['disposition'] == 'adopted' for row in review['candidates']))
            self.assertTrue(all(not row['automatic_evaluation'] for row in review['candidates']))
            self.assertEqual({}, runtime_goal('lj', receipt['binding']['runtime_batch_id']))

    def test_changed_catalog_requires_new_quick_preview_before_save(self):
        for limit in (None, 3.0):
            with self.subTest(cv=limit), IsolatedDatabase():
                values = simple_values(limit=limit)
                preview = prepare_quick_project_setup(values)
                change_builtin_catalog()
                self.assert_rejected_without_writes(lambda: save(values, preview), '预览')
                fresh = prepare_quick_project_setup(values)
                self.assertNotEqual(preview['preview_hash'], fresh['preview_hash'])
                receipt = save(values, fresh)
                self.assertTrue(receipt['binding']['runtime_batch_id'])

    def test_frozen_runtime_rejects_changed_review_levels_and_goal(self):
        for limit in (None, 3.0):
            with self.subTest(cv=limit), IsolatedDatabase():
                values = simple_values(limit=limit)
                receipt = save(values, prepare_quick_project_setup(values))
                lot_item = receipt['lot_config_item_id']
                with db.read_snapshot() as connection:
                    original = dict(connection.execute('SELECT * FROM qc_lot_config_items WHERE id=?', (lot_item,)).fetchone())
                    level = dict(connection.execute('SELECT * FROM qc_lot_config_item_levels WHERE lot_config_item_id=?', (lot_item,)).fetchone())
                    qc_lot = connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (values['level_ids'][0],)).fetchone()[0]
                def current_issues():
                    return get_daily_context(values['lab_instrument_id'], values['qc_material_id'], qc_lot,
                        '2026-10-02 08:00:00', lot_config_id=receipt['lot_config_id'])['items'][0]['issues']
                self.assertFalse(any(issue['field'] == 'quality' for issue in current_issues()))
                for change in ('review', 'goal', 'levels'):
                    with self.subTest(change=change):
                        with db.get_connection() as connection:
                            if change == 'review':
                                altered = json.loads(original['quality_review_json'])
                                altered['confirmed_by'] = '篡改后的确认人'
                                connection.execute('UPDATE qc_lot_config_items SET quality_review_json=? WHERE id=?',
                                    (json.dumps(altered, ensure_ascii=False), lot_item))
                            elif change == 'goal':
                                altered = json.loads(original['quality_goal_json'])
                                altered['unconfirmed_change'] = True
                                connection.execute('UPDATE qc_lot_config_items SET quality_goal_json=? WHERE id=?',
                                    (json.dumps(altered, ensure_ascii=False), lot_item))
                            else:
                                connection.execute('UPDATE qc_lot_config_item_levels SET level_order=2 WHERE id=?', (level['id'],))
                        self.assertTrue(any(issue['field'] == 'quality' for issue in current_issues()), current_issues())
                        with db.get_connection() as connection:
                            connection.execute('UPDATE qc_lot_config_items SET quality_review_json=?,quality_goal_json=? WHERE id=?',
                                (original['quality_review_json'], original['quality_goal_json'], lot_item))
                            connection.execute('UPDATE qc_lot_config_item_levels SET level_order=? WHERE id=?', (level['level_order'], level['id']))
                        self.assertFalse(any(issue['field'] == 'quality' for issue in current_issues()), current_issues())

    def test_private_cv_catalog_and_whole_setup_roll_back_after_failures(self):
        with IsolatedDatabase():
            values = simple_values(limit=3.0)
            _, item = draft(values)
            before = database_contents()
            original = adopt_requirement
            def fail_after_source(*args, **kwargs):
                original(*args, **kwargs)
                raise sqlite3.OperationalError('私有CV保存末步失败')
            with patch('services.quality_target_service.adopt_requirement', side_effect=fail_after_source):
                with self.assertRaisesRegex(sqlite3.OperationalError, '末步失败'):
                    save_simple_quality('project', item, **setup_args(values, 3.0))
            self.assertEqual(before, database_contents())
            save_simple_quality('project', item, **setup_args(values, 3.0))
            self.assertEqual([], validate_quality_review('project', item))
        for stage in ('project', 'lot', 'receipt'):
            with self.subTest(stage=stage), IsolatedDatabase():
                values = simple_values('zscore', 3, limit=3.0)
                preview = prepare_quick_project_setup(values)
                if stage == 'receipt':
                    with db.get_connection() as connection:
                        connection.execute("CREATE TRIGGER fail_short_receipt BEFORE INSERT ON qc_quick_setup_receipts BEGIN SELECT RAISE(ABORT,'末步回执失败'); END")
                before = database_contents()
                if stage == 'receipt':
                    with self.assertRaisesRegex(sqlite3.IntegrityError, '回执失败'):
                        save(values, preview)
                else:
                    from services.quick_project_setup_service import _confirm_quality
                    def fail_after_confirm(scope, *args, **kwargs):
                        _confirm_quality(scope, *args, **kwargs)
                        if scope == stage:
                            raise sqlite3.OperationalError('简化确认失败')
                    with patch('services.quick_project_setup_service._confirm_quality', side_effect=fail_after_confirm):
                        with self.assertRaisesRegex(sqlite3.OperationalError, '简化确认失败'):
                            save(values, preview)
                self.assertEqual(before, database_contents())
                if stage == 'receipt':
                    with db.get_connection() as connection:
                        connection.execute('DROP TRIGGER fail_short_receipt')
                receipt = save(values, preview)
                complete = database_contents()
                self.assertEqual(receipt, save(values, preview))
                self.assertEqual(complete, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
