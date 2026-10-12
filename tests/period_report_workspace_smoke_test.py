"""Calendar report previews, exports, retryable archives and stable history UI."""
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services import batch_monthly_report_service as batch_reports
from services import report_service as reports
from services.report_archive_service import read_report_archive, get_report_archive_metadata
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data
from tests.zscore_monthly_report_smoke_test import (
    seed_zscore_batch_with_formal_monthly_data, seed_three_level_zscore_batch_without_abnormal,
)
from tests.project_workspace_smoke_test import select_table_row


def contents():
    with db.read_snapshot() as connection:
        return tuple(connection.iterdump())


def clean(app):
    assert not app.exception, [str(item) for item in app.exception]


def single_page(method, batch_id):
    if method == 'lj':
        from pages.lj_report_section import render_lj_monthly_report_section
        render_lj_monthly_report_section(batch_id)
    else:
        from pages.zscore_report_section import render_zscore_monthly_report_section
        render_zscore_monthly_report_section(batch_id)


def single_app(method, batch_id, *, start='2026-04-01', end='2026-05-01'):
    app = AppTest.from_function(single_page, args=(method, batch_id), default_timeout=120).run()
    clean(app)
    prefix = f'{method}_monthly_report_{batch_id}'
    app.radio(key=prefix + '_period_mode').set_value('自选日期').run()
    app.date_input(key=prefix + '_start').set_value(date.fromisoformat(start))
    app.date_input(key=prefix + '_end').set_value(date.fromisoformat(end)).run()
    clean(app)
    return app, prefix


def batch_app():
    app = AppTest.from_string('from ui.batch_monthly_reports import render_batch_monthly_reports_page\nrender_batch_monthly_reports_page()', default_timeout=120).run()
    clean(app)
    app.radio(key='monthly_period_mode').set_value('自选日期').run()
    app.date_input(key='monthly_start').set_value(date(2026, 4, 1))
    app.date_input(key='monthly_end').set_value(date(2026, 5, 1)).run()
    clean(app)
    return app


class PeriodReportWorkspaceTests(unittest.TestCase):
    def test_single_day_31_days_and_year_share_export_boundaries_without_losing_levels(self):
        from ui.data_transfer import build_data_export, build_chart_png, build_chart_export, _fresh_import_context
        from services.report_period_service import resolve_report_period, filter_frame_to_period
        from services.value_type_service import get_input_value_type_label
        from zscore_logic import create_zscore_run, get_template_id_for_level_count, build_zscore_plot_dataframe
        from pages.lj_sections import build_monthly_export_dataframe
        from pages.zscore_sections import build_zscore_monthly_export_plot_dataframe
        with TemporaryDatabaseContext():
            _, lj = seed_lj_batch_with_formal_monthly_data()
            _, z2 = seed_zscore_batch_with_formal_monthly_data()
            _, z3 = seed_three_level_zscore_batch_without_abnormal()
            for when in ('2026-05-01 23:59:59.999999', '2026-05-02 00:00:00', '2026-12-31 23:59:59.999999'):
                db.add_result(lj, when, 100.0, operator='期间核对')
                for batch, count in ((z2, 2), (z3, 3)):
                    create_zscore_run(batch_id=batch, test_time=when, operator='期间核对',
                        template_id=get_template_id_for_level_count(count), required_n=5,
                        level_results=[dict(level_id=f'Level {i}', raw_value=[100, 150, 200][i-1]) for i in range(1, count+1)])
            for method, batch in (('lj', lj), ('zscore', z2), ('zscore', z3)):
                context = _fresh_import_context(method, batch)
                context['input_value_type_label'] = get_input_value_type_label(context['input_value_type'])
                if method == 'zscore':
                    context['plot_df'] = build_zscore_plot_dataframe(context['history_runs'], draft_run=None, display_phase=None)
                all_formal = build_data_export(method, context, 'formal')
                before = contents()
                for start, end in (('2026-05-01', '2026-05-01'), ('2026-04-01', '2026-05-01'), ('2026-01-01', '2026-12-31')):
                    expected = filter_frame_to_period(all_formal, resolve_report_period(start_date=start, end_date=end), '检测时间')
                    exported = build_data_export(method, context, 'formal', start_date=start, end_date=end)
                    package = getattr(reports, f'build_{method}_period_report_package')(batch, start, end)
                    self.assertEqual(len(expected), len(exported))
                    self.assertEqual(len(expected), package.report.statistics.formal_count)
                    if method == 'lj':
                        chart_rows = build_monthly_export_dataframe(context['qc_df'], start, end)
                        self.assertEqual(set(package.formal_df.id), set(chart_rows.id))
                    else:
                        chart_rows = build_zscore_monthly_export_plot_dataframe(context['plot_df'], start, end)
                        self.assertEqual(len(expected), chart_rows.run_id.nunique())
                        self.assertTrue(all(group.level_id.nunique() == len(context['required_level_ids']) for _, group in chart_rows.groupby('run_id')))
                self.assertTrue(build_chart_png(method, context, dict(view_mode='合并视图'),
                    start_date='2026-01-01', end_date='2026-12-31').startswith(b'\x89PNG'))
                self.assertEqual(before, contents(), '范围图、数据和报告预审均只读')
            from services.lot_lifecycle_service import create_target_profile
            for method, batch in (('lj', lj), ('zscore', z2)):
                levels = [dict(level_id='Level 1', mean=100, sd=5)]
                if method == 'zscore':
                    levels.append(dict(level_id='Level 2', mean=150, sd=5))
                create_target_profile(method=method, batch_id=batch, levels=levels, source='revision',
                    evidence='跨年图表验收的新参数依据', confirmed_by='参数核对人', effective_at='2027-01-01 00:00:00')
                for index in range(65):
                    when = f'2027-01-01 09:{index//60:02d}:{index%60:02d}'
                    if method == 'lj':
                        db.add_result(batch, when, 100, operator='密集趋势核对')
                    else:
                        create_zscore_run(batch_id=batch, test_time=when, operator='密集趋势核对',
                            template_id='2_level_classic', required_n=5,
                            level_results=[dict(level_id='Level 1', raw_value=100), dict(level_id='Level 2', raw_value=150)])
                context = _fresh_import_context(method, batch)
                context['input_value_type_label'] = '真实检测值'
                if method == 'zscore':
                    context['plot_df'] = build_zscore_plot_dataframe(context['history_runs'], draft_run=None, display_phase=None)
                before = contents()
                exported = build_chart_export(method, context, {}, start_date='2026-01-01', end_date='2027-01-01')
                self.assertEqual('application/zip', exported['mime'])
                self.assertGreaterEqual(exported['detail_count'], 3, '参数版本先分开，密集版本再分图')
                import csv
                from io import StringIO
                with ZipFile(BytesIO(exported['data'])) as archive:
                    self.assertTrue(archive.read('质控图_总览.png').startswith(b'\x89PNG'))
                    listing = list(csv.DictReader(StringIO(archive.read('图表清单.csv').decode('utf-8-sig'))))
                    self.assertEqual(len(build_data_export(method, context, 'formal')), sum(int(row['检测次数']) for row in listing))
                    self.assertTrue(all(archive.read(row['文件']).startswith(b'\x89PNG') for row in listing))
                self.assertEqual(before, contents(), '密集跨年多参数分图包含全部检测并且只读')

    def test_batch_31_day_partial_failure_restart_retry_and_original_nested_archives(self):
        with TemporaryDatabaseContext():
            seed_lj_batch_with_formal_monthly_data()
            seed_zscore_batch_with_formal_monthly_data()
            before = contents()
            preview = batch_reports.preview_period_reports('2026-04-01', '2026-05-01')
            self.assertEqual(before, contents())
            self.assertEqual(2, len(preview['items']))
            chosen = [row['unit_key'] for row in preview['items']]
            job_id = batch_reports.create_monthly_report_job(preview, chosen, 'period-batch')
            self.assertEqual(job_id, batch_reports.create_monthly_report_job(preview, chosen, 'period-batch'))
            with self.assertRaisesRegex(ValueError, '选择已变化'):
                batch_reports.create_monthly_report_job(preview, chosen[:1], 'period-batch')
            items = batch_reports.get_monthly_report_job(job_id)['items']
            with patch.object(batch_reports, 'build_zscore_monthly_report_pdf', side_effect=RuntimeError('isolated PDF failure')):
                for item in items:
                    batch_reports.run_monthly_report_item(job_id, item['id'])
            partial = batch_reports.get_monthly_report_job(job_id)
            self.assertEqual(1, partial['counts']['succeeded'])
            self.assertEqual(1, partial['counts']['failed'])
            saved = next(row for row in partial['items'] if row['status'] == 'succeeded')
            original = read_report_archive(saved['export_id'])
            db.init_db()
            failed = next(row for row in partial['items'] if row['status'] == 'failed')
            self.assertEqual('succeeded', batch_reports.run_monthly_report_item(job_id, failed['id'])['status'])
            self.assertEqual(saved['export_id'], batch_reports.run_monthly_report_item(job_id, saved['id'])['export_id'])
            archive = ZipFile(BytesIO(batch_reports.build_monthly_report_zip(job_id)))
            self.assertEqual(2, len([name for name in archive.namelist() if name.endswith('.pdf')]))
            nested = [archive.read(name) for name in archive.namelist() if name.endswith('.zip')]
            self.assertEqual(2, len(nested))
            self.assertIn(original, nested)
            self.assertIn('2026-05-01', archive.read('质控报告清单.csv').decode('utf-8-sig'))
            self.assertEqual('2026-04-01 至 2026-05-01', batch_reports.get_monthly_report_job(job_id)['period_label'])

    def test_batch_stale_preview_is_rejected_and_existing_month_jobs_keep_dates(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            preview = batch_reports.preview_period_reports('2026-04-01', '2026-05-01')
            db.add_result(batch, '2026-04-10 10:00:00', 100, operator='后续检测')
            before = contents()
            with self.assertRaisesRegex(ValueError, '已变化'):
                batch_reports.create_monthly_report_job(preview, [row['unit_key'] for row in preview['items']], 'stale-period')
            self.assertEqual(before, contents())
            with db.atomic_write() as connection:
                connection.execute('''INSERT INTO qc_monthly_report_jobs(request_id,request_digest,report_month,scope_json,exclusions_json)
                    VALUES('old-month','old','2026-04','{}','[]')''')
            db.init_db()
            old = batch_reports.list_monthly_report_jobs()[0]
            self.assertEqual(('2026-04-01', '2026-04-30'), (old['period_start'], old['period_end']))

    def test_single_preview_cancel_then_archive_uses_saved_bytes_for_each_method(self):
        from tests.zscore_v12_fixtures import seed_zscore_configuration
        from zscore_logic import create_zscore_run
        with TemporaryDatabaseContext():
            pending = seed_zscore_configuration(name='待判读报告界面', level_count=2, target_n=5)
            for index in range(6):
                create_zscore_run(pending['batch_id'], f'2026-04-0{index+1} 08:00:00', '检测人',
                    [dict(level_id='Level 1', raw_value=100), dict(level_id='Level 2', raw_value=150)],
                    template_id='2_level_classic', required_n=5)
            fixtures = [('lj', seed_lj_batch_with_formal_monthly_data()[1]),
                        ('zscore', seed_zscore_batch_with_formal_monthly_data()[1]),
                        ('zscore', pending['batch_id'])]
            for method, batch in fixtures:
                app, prefix = single_app(method, batch)
                before = contents()
                app.button(key=prefix + '_generate').click().run()
                clean(app)
                self.assertEqual(before, contents())
                if batch == pending['batch_id'] and method == 'zscore':
                    self.assertTrue(any('1 次检测待判读' in item.value for item in app.info))
                app.button(key=prefix + '_cancel').click().run()
                clean(app)
                self.assertNotIn(prefix + '_preview', app.session_state)
                self.assertEqual(before, contents())
                app.button(key=prefix + '_generate').click().run()
                app.button(key=prefix + '_archive').click().run()
                clean(app)
                export_id = app.session_state[prefix + '_preview']['snapshot_id']
                record = reports.get_report_history_record(export_id)
                self.assertEqual('', record.report_month)
                self.assertEqual('2026-04-01 至 2026-05-01', record.period_label)
                self.assertTrue(record.archive_available)
                labels = {item.label for item in app.get('download_button')}
                self.assertIn('下载 PDF', labels)
                self.assertIn('下载完整归档', labels)
                with ZipFile(BytesIO(read_report_archive(export_id))) as archive:
                    manifest = get_report_archive_metadata(export_id)['manifest']
                    self.assertEqual('2026-04-01', manifest['period']['start_date'])
                    self.assertTrue(any(archive.read(name) == reports.read_report_history_pdf(export_id) for name in archive.namelist() if name.endswith('.pdf')))
            preview = batch_reports.preview_period_reports('2026-04-01', '2026-05-01', methods=['zscore'])
            chosen = [row['unit_key'] for row in preview['items'] if row['batch_id'] == pending['batch_id']]
            job_id = batch_reports.create_monthly_report_job(preview, chosen, 'pending-summary-ui')
            item = batch_reports.get_monthly_report_job(job_id)['items'][0]
            batch_reports.run_monthly_report_item(job_id, item['id'])
            summary = batch_reports.monthly_job_summary(job_id)['reports'][0]
            self.assertEqual(1, summary['undetermined_count'])
            self.assertEqual(0, summary['in_control_count'])

    def test_single_stale_and_storage_failure_preserve_preview_for_retry(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            app, prefix = single_app('lj', batch)
            app.button(key=prefix + '_generate').click().run()
            db.add_result(batch, '2026-04-10 10:00:00', 100.0, operator='预审后追加')
            unchanged = contents()
            app.button(key=prefix + '_archive').click().run()
            clean(app)
            self.assertTrue(app.error)
            self.assertEqual(unchanged, contents())
            self.assertNotIn('snapshot_id', app.session_state[prefix + '_preview'])
            app.button(key=prefix + '_generate').click().run()
            request = app.session_state[prefix + '_preview']['request_key']
            with db.atomic_write() as connection:
                connection.execute("CREATE TRIGGER fail_period_pdf BEFORE INSERT ON report_export_files BEGIN SELECT RAISE(ABORT,'isolated unavailable'); END")
            unchanged = contents()
            app.button(key=prefix + '_archive').click().run()
            clean(app)
            self.assertTrue(app.error)
            self.assertEqual(request, app.session_state[prefix + '_preview']['request_key'])
            self.assertEqual(unchanged, contents())
            with db.atomic_write() as connection:
                connection.execute('DROP TRIGGER fail_period_pdf')
            unchanged = contents()
            with patch.object(reports, 'build_lj_period_report_pdf', side_effect=OSError('isolated file unavailable')):
                app.button(key=prefix + '_archive').click().run()
            clean(app)
            self.assertEqual(unchanged, contents())
            self.assertEqual(request, app.session_state[prefix + '_preview']['request_key'])
            self.assertTrue(any(button.key == prefix + '_cancel' for button in app.button))
            self.assertTrue(any(button.key == prefix + '_archive' for button in app.button))
            app.button(key=prefix + '_archive').click().run()
            clean(app)
            self.assertIn('snapshot_id', app.session_state[prefix + '_preview'])
            self.assertEqual(request, app.session_state[prefix + '_preview']['request_key'])

    def test_single_explicit_old_handling_version_requires_updated_preview(self):
        from services.out_of_control_service import open_event, save_handling
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            package = reports.build_lj_period_report_package(batch, '2026-04-01', '2026-05-01')
            source = int(package.formal_df.loc[lambda frame: frame.status == '失控', 'id'].iloc[0])
            event = open_event('lj_result', source, 'period-ui-event', '登记人')
            save_handling(event['event_id'], 1, 'period-ui-draft', 'save_draft',
                dict(cause_analysis='第二版调查内容'), '处理人')
            app, prefix = single_app('lj', batch)
            app.button(key=prefix + '_generate').click().run()
            clean(app)
            key = prefix + '_' + app.session_state[prefix + '_preview']['request_key'] + '_handling_' + str(event['event_id'])
            app.selectbox(key=key).set_value(1).run()
            self.assertTrue(app.button(key=prefix + '_archive').disabled)
            before = contents()
            app.button(key=prefix + '_refresh').click().run()
            clean(app)
            self.assertEqual(before, contents())
            self.assertFalse(app.button(key=prefix + '_archive').disabled)
            app.button(key=prefix + '_archive').click().run()
            clean(app)
            export = app.session_state[prefix + '_preview']['snapshot_id']
            self.assertEqual({str(event['event_id']): 1}, get_report_archive_metadata(export)['manifest']['handling_versions'])

    def test_batch_ui_scope_change_cancel_and_generation_keep_report_period(self):
        with TemporaryDatabaseContext():
            seed_lj_batch_with_formal_monthly_data()
            app = batch_app()
            before = contents()
            app.button(key='monthly_preview').click().run()
            clean(app)
            self.assertEqual(before, contents())
            app.date_input(key='monthly_end').set_value(date(2026, 12, 31)).run()
            self.assertFalse(any(button.key == 'monthly_generate' for button in app.button))
            app.button(key='monthly_preview_cancel').click().run()
            self.assertNotIn('monthly_prepared', app.session_state)
            self.assertEqual(before, contents())
            app.button(key='monthly_preview').click().run()
            app.button(key='monthly_generate').click().run()
            clean(app)
            job = batch_reports.get_monthly_report_job(app.session_state['monthly_current_job'])
            self.assertEqual('2026-12-31', job['period_end'])
            self.assertEqual(1, job['counts']['succeeded'])
            self.assertTrue(any(item.label == '下载此份完整归档' for item in app.get('download_button')))

    def test_history_period_selection_regeneration_preserves_original_and_clears_hidden_id(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            package = reports.build_lj_period_report_package(batch, '2026-04-01', '2026-05-01')
            export = reports.save_lj_period_report_snapshot(package, request_key='history-period')
            old_pdf = reports.read_report_history_pdf(export)
            old_zip = read_report_archive(export)
            app = AppTest.from_string('from pages.report_history_page import render_report_history_page\nrender_report_history_page()', default_timeout=120).run()
            clean(app)
            app.checkbox(key='report_history_use_period').check().run()
            app.radio(key='report_history_period_period_mode').set_value('自选日期').run()
            app.date_input(key='report_history_period_start').set_value(date(2026, 5, 1))
            app.date_input(key='report_history_period_end').set_value(date(2026, 5, 1)).run()
            clean(app)
            self.assertEqual(1, len(app.dataframe[0].value))
            select_table_row(app, 0)
            app.button(key='report_history_view').click().run()
            clean(app)
            self.assertTrue(any(item.label == '下载已保存的完整归档' for item in app.get('download_button')))
            db.add_result(batch, '2026-04-10 10:00:00', 100.0, operator='历史追加')
            before = contents()
            with patch('pages.report_history_page.regenerate_report_from_history', side_effect=OSError('isolated history file unavailable')):
                app.button(key=f'report_history_regenerate_{export}').click().run()
            clean(app)
            self.assertEqual(before, contents())
            request_key = f'report_history_regenerated_{export}_request'
            request = app.session_state[request_key]
            self.assertFalse(app.button(key=f'report_history_regenerate_{export}').disabled)
            app.button(key=f'report_history_regenerate_{export}').click().run()
            clean(app)
            self.assertEqual(request, app.session_state[request_key])
            state = app.session_state[f'report_history_regenerated_{export}']
            new = reports.get_report_history_record(state['snapshot_id'])
            self.assertEqual(('2026-04-01', '2026-05-01'), (new.period_start, new.period_end))
            self.assertEqual(old_pdf, reports.read_report_history_pdf(export))
            self.assertEqual(old_zip, read_report_archive(export))
            app.text_input(key='report_history_project_query').set_value('查无此项目').run()
            clean(app)
            self.assertNotIn('report_history_records_selected', app.session_state)
            self.assertNotIn('report_history_detail_id', app.session_state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
