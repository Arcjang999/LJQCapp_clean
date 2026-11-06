"""Ct chart/report reminders preserve original QC outcomes and frozen sources."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd
from pypdf import PdfReader
from matplotlib.colors import to_rgba
from matplotlib.legend import Legend

from plotting import plot_lj_chart, plot_instant_chart, CT_ALERT_COLOR, figure_to_png_bytes
from zscore_plotting import plot_zscore_single_level, plot_zscore_overlay
from services.ct_alert_service import annotate_lj_frame, annotate_zscore_frame
from services.report_service import _with_ct_report_summary, _ct_report_conclusion
from services.report_pdf_layout import _build_ct_alert_pages, _build_quality_pages


POLICY = dict(enabled=True, operator='<=', threshold=38.0, version=1)


def lj_frame():
    return pd.DataFrame([
        dict(id=index, sequence=index, test_time=pd.Timestamp('2026-10-21') + pd.Timedelta(hours=index),
             phase='正式数据', status=status, rule_hits='1_3s' if status == '失控' else '',
             value=value, target_mean_used=30., target_sd_used=.2)
        for index, (value, status) in enumerate([(30.1, '符合质控'), (38.1, '符合质控'), (39., '失控')], 1)
    ])


def zscore_frame():
    rows = []
    for index in range(1, 4):
        for level in ('Level 1', 'Level 2'):
            rows.append(dict(run_id=index, run_index=index, level_id=level,
                test_time=pd.Timestamp('2026-10-21') + pd.Timedelta(hours=index),
                phase='formal_qc', plot_phase='formal_qc', status='accept' if index < 3 else 'reject',
                run_status='accept' if index < 3 else 'reject', raw_value=37.9 + index * .1,
                formal_reference_mean=38., formal_reference_sd=.2,
                building_reference_mean=38., building_reference_sd=.2))
    return pd.DataFrame(rows)


def ct_lines(figure):
    return [line for line in figure.axes[0].lines if line.get_label().startswith('实验室自定 Ct 线')]


def chart_text(figure):
    legends = [artist for artist in figure.axes[0].get_children() if isinstance(artist, Legend)]
    return ' '.join(text.get_text() for legend in legends for text in legend.get_texts())


class CtChartReportTests(unittest.TestCase):
    def test_lj_line_is_visible_outside_sd_range_and_red_wins(self):
        frame = lj_frame()
        original = frame.copy(deep=True)
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 30., 'sd': .2}, 'Ct质控图', ct_alert=POLICY)
        try:
            self.assertEqual([38.], [float(line.get_ydata()[0]) for line in ct_lines(figure)])
            lower, upper = figure.axes[0].get_ylim()
            self.assertLess(lower, 38.)
            self.assertGreater(upper, 38.)
            reminders = [points for points in figure.axes[0].collections if points.get_label() == 'Ct提醒']
            self.assertEqual(1, len(reminders))
            self.assertEqual(1, len(reminders[0].get_offsets()))
            self.assertIn('Ct提醒', chart_text(figure))
            self.assertTrue(figure_to_png_bytes(figure).startswith(b'\x89PNG'))
            pd.testing.assert_frame_equal(frame, original)
        finally:
            plt.close(figure)

    def test_each_historical_segment_uses_its_original_line(self):
        frame = annotate_lj_frame(lj_frame(), POLICY)
        second = dict(POLICY, threshold=35.)
        frame['ct_alert_config'] = [POLICY, None, second]
        frame.attrs['ct_alert'] = None
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 30., 'sd': .2}, '历史Ct要求')
        try:
            lines = ct_lines(figure)
            self.assertEqual([38., 35.], [float(line.get_ydata()[0]) for line in lines])
            self.assertLess(max(lines[0].get_xdata()), 2.)
            self.assertGreater(min(lines[1].get_xdata()), 2.)
            self.assertGreater(figure.axes[0].get_ylim()[1], 38.)
        finally:
            plt.close(figure)

    def test_zscore_single_overlay_and_instant_share_ct_legend(self):
        frame = annotate_zscore_frame(zscore_frame(), POLICY)
        for builder in (lambda: plot_zscore_single_level(frame, 'Level 1', 'Ct单图'),
                        lambda: plot_zscore_overlay(frame, 'Ct多水平图')):
            figure = builder()
            try:
                self.assertEqual(1, len(ct_lines(figure)))
                self.assertIn('实验室自定 Ct 线', chart_text(figure))
                self.assertIn('Ct提醒', chart_text(figure))
            finally:
                plt.close(figure)
        instant = pd.DataFrame([dict(id=1, test_time='2026-10-21 08:00:00', value=38.1,
            is_effective=1, effective_sequence=1, is_outlier_suspect=0, status='有效点')])
        figure = plot_instant_chart(instant, {'mean': 38.1, 'sd': None}, 'Ct即时法', ct_alert=POLICY)
        try:
            self.assertEqual(1, len(ct_lines(figure)))
            self.assertIn('Ct提醒', chart_text(figure))
        finally:
            plt.close(figure)

    def test_disabled_ct_does_not_add_lines_or_change_data(self):
        frame = lj_frame()
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 30., 'sd': .2}, '普通质控图')
        try:
            self.assertFalse(ct_lines(figure))
            self.assertNotIn('Ct提醒', chart_text(figure))
        finally:
            plt.close(figure)

    def test_report_counts_runs_once_and_keeps_original_qc_summary(self):
        records = [dict(id=1, test_time='2026-10-21 08:00:00', run_status='accept', level_results=[
            dict(level_id='Level 1', raw_value=38.1), dict(level_id='Level 2', raw_value=39.)]),
            dict(id=2, test_time='2026-10-22 08:00:00', run_status='reject', level_results=[
                dict(level_id='Level 1', raw_value=40.), dict(level_id='Level 2', raw_value=37.)])]
        original = deepcopy(records)
        existing = {'statistics_scope': '原统计', 'rows': [{'count': 1, 'cv': .25}]}
        result = _with_ct_report_summary(existing, 'zscore', POLICY, records)
        self.assertEqual(2, result['ct_alert']['alert_count'])
        self.assertEqual(2, result['ct_alert']['evaluated_count'])
        self.assertEqual(2, len(result['ct_alert']['records'][0]['values']))
        self.assertEqual(existing['rows'], result['rows'])
        self.assertEqual(original, records)
        report = SimpleNamespace(title='Ct核对报告', report_month_label='2026年10月',
            basic_info=SimpleNamespace(project_name='核酸检测'), quality_summary=result)
        figures = _build_quality_pages(report) + _build_ct_alert_pages(report)
        try:
            text = ''.join(t.get_text() for figure in figures for axis in figure.axes for t in axis.texts)
            self.assertIn('Ct提醒次数：2', text)
            table_values = [cell.get_text().get_text() for figure in figures for axis in figure.axes
                            for table in axis.tables for cell in table.get_celld().values()]
            self.assertIn('失控；Ct提醒', table_values)
            colors = [cell.get_facecolor() for figure in figures for axis in figure.axes
                      for table in axis.tables for cell in table.get_celld().values()]
            self.assertIn(to_rgba('#fff3c4'), colors)
            self.assertIn(to_rgba('#fde9e7'), colors)
        finally:
            for figure in figures:
                plt.close(figure)

    def test_ct_reminder_keeps_pending_report_conclusion(self):
        from dataclasses import make_dataclass
        Report = make_dataclass('CtPendingReport', ['quality_summary', 'statistics', 'conclusion',
            'overview_text', 'abnormal_records', 'corrective_actions_empty_text', 'abnormal_summary_text'])
        for pending_key in ('pending_count', 'undetermined_count'):
            original = Report({'ct_alert': {'alert_count': 1}},
                SimpleNamespace(out_of_control_count=0, warning_count=0, **{pending_key: 1}),
                '有检测记录待判读，请核对控制参数。', '本期存在待判读记录。', [], '', '')
            result = _ct_report_conclusion(original)
            self.assertTrue(result.conclusion.startswith(original.conclusion))
            self.assertIn('Ct提醒1次', result.conclusion)

    def test_real_ct_report_uses_frozen_requirement_and_preserves_qc_counts(self):
        from database import add_result
        from services.master_data_service import create_unit, list_units
        from services.quick_project_setup_service import prepare_quick_project_setup
        from services.quality_setup_service import save_simple_quality
        from services.report_service import build_lj_period_report_package, build_lj_period_report_pdf
        from tests.instant_v12_integration_smoke_test import IsolatedDatabase
        from tests.quality_simple_setup_smoke_test import simple_values
        from tests.quick_project_setup_smoke_test import save
        with IsolatedDatabase():
            values = simple_values()
            units = list_units()
            found = units.loc[units.symbol == 'Ct']
            values.update(input_value_type='ct', unit_id=int(found.iloc[0].id) if len(found) else create_unit(symbol='Ct'))
            context = dict(technique='other', result_kind='quantitative', result_scale='ct',
                           purpose='research', specimen='核酸液体材料')
            config = dict(POLICY, threshold=30.)
            values['quality'].update(context=context, ct_alert=config)
            preview = prepare_quick_project_setup(values)
            receipt = save(values, preview, request='ct-chart-report')
            batch = receipt['binding']['runtime_batch_id']
            for index, value in enumerate([29.8, 30.2] * 10 + [29.9, 30.00001, 31.2000001], 1):
                add_result(batch, f'2026-10-{index:02d} 08:00:00', value, operator='Ct核对人')
            package = build_lj_period_report_package(batch, '2026-10-21', '2026-10-23')
            self.assertEqual(3, package.report.statistics.formal_count)
            self.assertEqual(2, package.report.statistics.in_control_count)
            self.assertEqual(1, package.report.statistics.out_of_control_count)
            self.assertEqual(2, package.report.quality_summary['ct_alert']['alert_count'])
            self.assertEqual('Ct ≤ 30', package.report.quality_summary['ct_alert']['requirement'])
            original_source = package.report.source_version['fingerprint']
            save_simple_quality('project', receipt['template_item_id'], confirmed_by='后续要求核对人',
                context=context, process_requirements={'source_ids': [], 'requirement_text': ''},
                ct_alert=dict(POLICY, threshold=40.))
            reopened = build_lj_period_report_package(batch, '2026-10-21', '2026-10-23')
            self.assertEqual(package.report.quality_summary['ct_alert'], reopened.report.quality_summary['ct_alert'])
            self.assertEqual(original_source, reopened.report.source_version['fingerprint'])
            pdf = build_lj_period_report_pdf(reopened)
            reader = PdfReader(BytesIO(pdf))
            text = ''.join(page.extract_text() for page in reader.pages)
            self.assertIn('Ct提醒次数', text)
            self.assertIn('Ct提醒明细', text)
            ct_detail = ''.join(page.extract_text() for page in reader.pages
                                if 'Ct提醒明细' in page.extract_text())
            self.assertIn('30.00001', ct_detail)
            self.assertIn('31.2000001', ct_detail)
            output = ROOT / 'output' / 'pdf' / 'ct-alert-2026-11-06'
            output.mkdir(parents=True, exist_ok=True)
            (output / 'ct-reminder-report.pdf').write_bytes(pdf)
            figure = plot_lj_chart(reopened.formal_df, reopened.stats, 'Ct要求与质控判读')
            try:
                figure.savefig(output / 'ct-lj-chart.png', bbox_inches='tight')
            finally:
                plt.close(figure)

    def test_real_zscore_ct_report_counts_whole_runs_and_colors_red_first(self):
        from services.master_data_service import create_unit, list_units
        from services.quick_project_setup_service import prepare_quick_project_setup
        from services.report_service import build_zscore_period_report_package, build_zscore_period_report_pdf
        from tests.instant_v12_integration_smoke_test import IsolatedDatabase
        from tests.quality_simple_setup_smoke_test import simple_values
        from tests.quick_project_setup_smoke_test import save
        from zscore_logic import create_zscore_run, get_template_id_for_level_count
        with IsolatedDatabase():
            values = simple_values('zscore', 2)
            units = list_units()
            found = units.loc[units.symbol == 'Ct']
            values.update(input_value_type='ct', unit_id=int(found.iloc[0].id) if len(found) else create_unit(symbol='Ct'))
            context = dict(technique='other', result_kind='quantitative', result_scale='ct',
                           purpose='research', specimen='核酸液体材料')
            values['quality'].update(context=context, ct_alert=dict(POLICY, threshold=35.))
            preview = prepare_quick_project_setup(values)
            receipt = save(values, preview, request='ct-z-chart-report')
            batch = receipt['binding']['runtime_batch_id']
            points = [(30.+shift, 35.+shift) for shift in [-.2, .2]*10]
            points += [(29.9, 34.9), (30.1, 35.1), (31.2, 35.2)]
            for index, pair in enumerate(points, 1):
                create_zscore_run(batch, f'2026-10-{index:02d} 08:00:00', 'Ct核对人',
                    [dict(level_id=f'Level {order}', raw_value=value) for order, value in enumerate(pair, 1)],
                    template_id=get_template_id_for_level_count(2), required_n=20)
            package = build_zscore_period_report_package(batch, '2026-10-21', '2026-10-23')
            self.assertEqual(3, package.report.statistics.formal_count)
            self.assertEqual(2, package.report.statistics.in_control_count)
            self.assertEqual(1, package.report.statistics.out_of_control_count)
            self.assertEqual(2, package.report.quality_summary['ct_alert']['alert_count'])
            self.assertEqual(3, package.report.quality_summary['ct_alert']['evaluated_count'])
            self.assertEqual(1, len(package.report.quality_summary['ct_alert']['records'][-1]['values']))
            latest = package.monthly_plot_df.loc[package.monthly_plot_df.run_index == 23]
            self.assertEqual({'danger'}, set(latest.display_tone))
            pdf = build_zscore_period_report_pdf(package)
            self.assertIn('Ct提醒明细', ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages))
            output = ROOT / 'output' / 'pdf' / 'ct-alert-2026-11-06'
            output.mkdir(parents=True, exist_ok=True)
            (output / 'ct-zscore-report.pdf').write_bytes(pdf)
            figure = plot_zscore_overlay(package.monthly_plot_df, 'Ct多水平要求与判读')
            try:
                figure.savefig(output / 'ct-zscore-overlay.png', bbox_inches='tight')
                self.assertIn('Ct提醒', chart_text(figure))
            finally:
                plt.close(figure)


if __name__ == '__main__':
    unittest.main(verbosity=2)
