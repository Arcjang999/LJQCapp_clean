"""Date reports keep full-sequence decisions, historical parameters and immutable archives."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
import unittest

import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.report_period_service import resolve_report_period, filter_frame_to_period
from services.report_service import (
    build_lj_period_report_package, build_zscore_period_report_package,
    build_lj_period_report_pdf, build_zscore_period_report_pdf,
    save_lj_period_report_snapshot, save_zscore_period_report_snapshot,
    get_report_history_record, list_report_history_records, filter_report_history_records,
    read_report_history_pdf, regenerate_report_from_history,
)
from services.report_archive_service import read_report_archive
from services.report_pdf_layout import split_report_chart_frames
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data
from tests.zscore_v12_fixtures import seed_zscore_configuration
from zscore_logic import get_zscore_runs, create_zscore_run
from qc_logic import calculate_qc_results


def contents():
    with db.read_snapshot() as connection:
        return tuple(connection.iterdump())


class ReportPeriodTests(unittest.TestCase):
    def test_pending_formal_zscore_is_reported_without_an_in_control_conclusion(self):
        with TemporaryDatabaseContext():
            fixture = seed_zscore_configuration(name='正式参数不可判读报告', level_count=2, target_n=5)
            for index in range(6):
                create_zscore_run(fixture['batch_id'], f'2026-04-0{index+1} 08:00:00', '检测人员',
                    [dict(level_id='Level 1', raw_value=100),dict(level_id='Level 2', raw_value=150)],
                    template_id='2_level_classic',required_n=5)
            package = build_zscore_period_report_package(fixture['batch_id'], '2026-04-06', '2026-04-06')
            self.assertEqual(1, package.report.statistics.formal_count)
            self.assertEqual(1, package.report.statistics.pending_count)
            self.assertEqual(0, package.report.statistics.in_control_count)
            self.assertIn('待判读', package.report.overview_text)
            self.assertNotIn('整体运行稳定', package.report.overview_text)
            self.assertIn('尚不能确认全部检测在控', package.report.conclusion)
            pdf = build_zscore_period_report_pdf(package)
            self.assertIn('待判读检测记录数', ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages))

    def test_range_filters_after_complete_lj_and_zscore_decisions(self):
        with TemporaryDatabaseContext():
            for method, seed, builder in (
                ('lj', seed_lj_batch_with_formal_monthly_data, build_lj_period_report_package),
                ('zscore', seed_zscore_batch_with_formal_monthly_data, build_zscore_period_report_package)):
                with self.subTest(method=method):
                    _, batch = seed()
                    before = contents()
                    report = builder(batch, '2026-04-03', '2026-04-03')
                    self.assertEqual(before, contents())
                    self.assertEqual(1, report.report.statistics.formal_count)
                    self.assertEqual('', report.report.report_month)
                    self.assertEqual('2026-04-03', report.report.period_start)
                    self.assertIn('20260403_20260403', report.report.file_name)
                    if method == 'lj':
                        evaluated, _ = calculate_qc_results(db.get_results(batch), 5)
                        original = evaluated[evaluated.id == int(report.formal_df.iloc[0].id)].iloc[0]
                        self.assertEqual(original.rule_hits, report.formal_df.iloc[0].rule_hits)
                        self.assertEqual(original.status, report.formal_df.iloc[0].status)
                        self.assertIn('2_2s', original.rule_hits)
                    else:
                        run = report.formal_runs[0]
                        original = next(row for row in get_zscore_runs(batch) if row['run_id'] == run['run_id'])
                        self.assertEqual(original['rule_hits_run'], run['rule_hits_run'])
                        self.assertEqual(2, len(report.monthly_plot_df))
                    self.assertEqual(8, len(report.report.source_version['result_ids']))

    def test_calendar_boundaries_include_fractional_last_second_and_validate_month(self):
        period = resolve_report_period(start_date='2026-01-31', end_date='2026-02-01')
        frame = pd.DataFrame({'id': range(4), 'test_time': ['2026-01-30 23:59:59.999',
            '2026-01-31 00:00:00', '2026-02-01 23:59:59.999999', '2026-02-02 00:00:00']})
        self.assertEqual([1, 2], filter_frame_to_period(frame, period).id.tolist())
        with self.assertRaises(ValueError):
            resolve_report_period('2026-02', start_date='2026-02-02', end_date='2026-02-28')
        with self.assertRaises(ValueError):
            resolve_report_period(start_date='2026-03-02', end_date='2026-03-01')

    def test_cross_parameter_versions_are_separate_and_old_page_axis_uses_old_profile(self):
        from services.lot_lifecycle_service import create_target_profile
        from plotting import plot_lj_chart, close_figure
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            profile = create_target_profile(method='lj', batch_id=batch,
                levels=[dict(level_id='Level 1', mean=140, sd=2)], source='manual',
                evidence='核对后的另一参数版本', confirmed_by='隔离参数确认人', effective_at='2026-05-01')
            db.add_result(batch, '2026-05-02 08:00:00', 140, operator='隔离检测人',
                lot_selection={'target_profile_id': profile})
            all_period = build_lj_period_report_package(batch, '2026-04-01', '2026-05-02')
            self.assertEqual(4, all_period.report.statistics.formal_count)
            self.assertIsNone(all_period.report.statistics.monthly_mean)
            version_ids = {row['profile_id'] for row in all_period.report.lot_trace['statistics_by_target_version']}
            self.assertEqual(2, len(version_ids)); self.assertIn(profile, version_ids)
            old = build_lj_period_report_package(batch, '2026-04-01', '2026-04-01')
            figure = plot_lj_chart(old.formal_df, old.stats, title='历史范围', view_mode='正式质控图')
            lower, upper = figure.axes[0].get_ylim()
            self.assertLess(lower, 100); self.assertGreater(upper, 100); self.assertLess(upper, 110)
            close_figure(figure)
            for index in range(67):
                when = pd.Timestamp('2026-05-03 08:00:00') + pd.Timedelta(days=index)
                db.add_result(batch, str(when), 140 + (0.2 if index%2 else -0.2), operator='隔离检测人',
                    lot_selection={'target_profile_id': profile})
            long_package = build_lj_period_report_package(batch, '2026-04-01', '2026-07-31')
            frames = split_report_chart_frames('lj', long_package.formal_df)
            self.assertEqual([3, 60, 8], [len(frame) for frame in frames])
            pdf = build_lj_period_report_pdf(long_package)
            reader = PdfReader(BytesIO(pdf))
            self.assertEqual(3, sum('报告期质控图（' in page.extract_text() for page in reader.pages))
            out = ROOT / 'output' / 'zhenjiang-phase2-report-2026-10-12'
            out.mkdir(parents=True, exist_ok=True)
            (out / 'long-lj-report.pdf').write_bytes(pdf)

    def test_long_three_level_pdf_keeps_every_complete_detection_in_paged_charts(self):
        with TemporaryDatabaseContext():
            fixture = seed_zscore_configuration(name='长期间三水平', level_count=3, target_n=5)
            batch = fixture['batch_id']
            for index, value in enumerate([100, 100.2, 99.8, 100.1, 99.9]):
                create_zscore_run(batch, f'2026-03-0{index+1} 08:00:00', '建靶人员',
                    [dict(level_id=f'Level {level}', raw_value=value+50*(level-1)) for level in range(1, 4)],
                    template_id='3_level_threes',required_n=5)
            for index in range(121):
                when = pd.Timestamp('2026-04-01 08:00:00') + pd.Timedelta(days=index)
                create_zscore_run(batch, str(when), '日常检测人员',
                    [dict(level_id=f'Level {level}', raw_value=100+50*(level-1)+(0.02 if index%2 else -0.02)) for level in range(1, 4)],
                    template_id='3_level_threes',required_n=5)
            package = build_zscore_period_report_package(batch, '2026-04-01', '2026-07-31')
            frames = split_report_chart_frames('zscore', package.monthly_plot_df)
            self.assertEqual([60, 60, 1], [frame.run_id.nunique() for frame in frames])
            self.assertEqual(121, sum(frame.run_id.nunique() for frame in frames))
            self.assertEqual(set(package.monthly_plot_df.run_id), {value for frame in frames for value in frame.run_id})
            self.assertTrue(all(len(group) == 3 for frame in frames for _, group in frame.groupby('run_id')))
            self.assertEqual(121, package.report.statistics.formal_count)
            pdf = build_zscore_period_report_pdf(package)
            reader = PdfReader(BytesIO(pdf))
            chart_pages = [page for page in reader.pages if '报告期图（' in page.extract_text()]
            self.assertEqual(9, len(chart_pages))
            text = '\n'.join(page.extract_text() for page in reader.pages)
            self.assertIn('检验员（签名）', text); self.assertIn('复核员（签名）', text)
            self.assertTrue(package.complete_archive['zip_bytes'].startswith(b'PK'))
            self.assertEqual(package.report.report_type, reader.metadata.get('/Subject'))
            out = ROOT / 'output' / 'zhenjiang-phase2-report-2026-10-12'
            out.mkdir(parents=True, exist_ok=True)
            (out / 'long-three-level-report.pdf').write_bytes(pdf)

    def test_record_outside_selected_period_invalidates_stale_report_without_any_archive_write(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            package = build_lj_period_report_package(batch, '2026-04-03', '2026-04-03')
            pdf = build_lj_period_report_pdf(package)
            db.add_result(batch, '2026-04-02 09:00:00', 100, operator='补录检测人')
            before = contents()
            with self.assertRaisesRegex(ValueError, '已变化'):
                save_lj_period_report_snapshot(package, pdf, request_key='stale-period')
            self.assertEqual(before, contents())

    def test_pdf_zip_save_is_atomic_and_same_request_returns_original_export(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            package = build_lj_period_report_package(batch, '2026-04-01', '2026-04-03')
            pdf = build_lj_period_report_pdf(package)
            before = contents()
            with patch('services.report_archive_service.persist_complete_report_archive', side_effect=ValueError('归档末步失败')):
                with self.assertRaisesRegex(ValueError, '末步失败'):
                    save_lj_period_report_snapshot(package, pdf, request_key='period-atomic')
            self.assertEqual(before, contents())
            export = save_lj_period_report_snapshot(package, pdf, request_key='period-atomic')
            original = contents()
            self.assertEqual(export, save_lj_period_report_snapshot(package, pdf, request_key='period-atomic'))
            self.assertEqual(original, contents())
            fresh = build_lj_period_report_package(batch, '2026-04-01', '2026-04-03')
            self.assertEqual(export, save_lj_period_report_snapshot(fresh, request_key='period-atomic'))
            self.assertEqual(original, contents())

    def test_history_filter_and_regeneration_keep_period_and_original_pdf_zip(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            package = build_zscore_period_report_package(batch, '2026-04-02', '2026-04-03')
            pdf = build_zscore_period_report_pdf(package)
            export = save_zscore_period_report_snapshot(package, pdf, request_key='old-range')
            old_zip = read_report_archive(export)
            record = get_report_history_record(export)
            self.assertTrue(record.archive_available)
            self.assertEqual('2026-04-02', record.period_start)
            self.assertEqual([export], [row.export_id for row in filter_report_history_records(
                list_report_history_records(), start_date='2026-04-03', end_date='2026-04-04')])
            self.assertEqual([], filter_report_history_records(list_report_history_records(),
                start_date='2026-04-04', end_date='2026-04-05'))
            generated = regenerate_report_from_history(record, request_key='regenerated-range')
            new = get_report_history_record(generated.snapshot_id)
            self.assertEqual((record.period_start, record.period_end), (new.period_start, new.period_end))
            self.assertEqual(pdf, read_report_history_pdf(export))
            self.assertEqual(old_zip, read_report_archive(export))
            self.assertEqual(generated.snapshot_id, regenerate_report_from_history(record,
                request_key='regenerated-range').snapshot_id)


if __name__ == '__main__':
    unittest.main(verbosity=2)
