"""Saved control parameters stay readable on screen and on each report page."""
from io import BytesIO
import math
import os
from pathlib import Path
import sys
import unittest

import matplotlib.pyplot as plt
from matplotlib.text import Text
import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chart_layout import CONTROL_LINE_COLORS
from plotting import plot_lj_chart
from zscore_plotting import plot_zscore_single_level, plot_zscore_overlay


LABELS = {'均值', '+1SD', '+2SD', '+3SD', '-1SD', '-2SD', '-3SD'}


def lj_frame(profiles=((11, 100., 2.),) * 6):
    return pd.DataFrame([
        dict(id=index, sequence=index, test_time=pd.Timestamp('2026-04-01') + pd.Timedelta(hours=index),
             phase='正式数据', status='符合质控', rule_hits='',
             value=mean + (.2 if index % 2 else -.2),
             target_profile_id=profile, target_mean_used=mean, target_sd_used=sd)
        for index, (profile, mean, sd) in enumerate(profiles, 1)
    ])


def labels(axis):
    return [text for text in axis.texts if text.get_gid() == 'qc-control-label']


def horizontal_controls(axis):
    return [line for line in axis.lines if len(line.get_ydata()) == 2
            and float(line.get_ydata()[0]) == float(line.get_ydata()[1])
            and line.get_color() in CONTROL_LINE_COLORS.values()]


def output_path(name):
    folder = os.environ.get('LJQC_CHART_TEST_OUTPUT')
    if not folder:
        return None
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class ControlLineLabelsTests(unittest.TestCase):
    def assert_labels(self, figure, mean, sd, *, multiple=False):
        axis = figure.axes[0]
        expected_mean = '均值（末段）' if multiple else '均值'
        texts = {text.get_text(): text for text in labels(axis)}
        self.assertEqual((LABELS - {'均值'}) | {expected_mean}, set(texts))
        self.assertAlmostEqual(mean, texts[expected_mean].xy[1])
        for multiplier in (1, 2, 3):
            for sign in (1, -1):
                text = texts[f'{"+" if sign > 0 else "-"}{multiplier}SD']
                self.assertAlmostEqual(mean + sign * multiplier * sd, text.xy[1])
                self.assertEqual(CONTROL_LINE_COLORS[multiplier], text.get_color())
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        boxes = sorted((Text.get_window_extent(text, renderer) for text in texts.values()), key=lambda box: box.y0)
        for box in boxes:
            self.assertGreaterEqual(box.x0, axis.get_window_extent().x1)
            self.assertLessEqual(box.x1, figure.bbox.width)
            self.assertGreaterEqual(box.y0, 0)
            self.assertLessEqual(box.y1, figure.bbox.height)
        self.assertTrue(all(not left.overlaps(right) for left, right in zip(boxes, boxes[1:])))

    def test_saved_version_has_seven_labels_and_three_sd_colors(self):
        frame = lj_frame()
        original = frame.copy(deep=True)
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 999, 'sd': 50}, '正式质控图')
        try:
            self.assert_labels(figure, 100, 2)
            self.assertEqual({94., 96., 98., 100., 102., 104., 106.},
                             {float(line.get_ydata()[0]) for line in horizontal_controls(figure.axes[0])})
            pd.testing.assert_frame_equal(original, frame)
            path = output_path('lj-confirmed.png')
            if path:
                figure.savefig(path, bbox_inches='tight')
        finally:
            plt.close(figure)

    def test_multiple_versions_keep_original_segments_and_mark_rightmost_labels(self):
        frame = lj_frame(((11, 100., 2.),) * 3 + ((22, 140., 3.),) * 3)
        for current in (frame, frame.iloc[::-1]):
            figure = plot_lj_chart(current, {'target_ready': True, 'mean': 140, 'sd': 3}, '跨参数质控图')
            try:
                self.assert_labels(figure, 140, 3, multiple=True)
                lines = horizontal_controls(figure.axes[0])
                self.assertEqual(14, len(lines))
                first = [line for line in lines if math.isclose(float(line.get_ydata()[0]), 100)]
                self.assertEqual([.6, 3.4], list(first[0].get_xdata()))
                self.assertIn('参数调整', {text.get_text() for text in figure.axes[0].texts})
                path = output_path('lj-two-versions.png')
                if path:
                    figure.savefig(path, bbox_inches='tight')
            finally:
                plt.close(figure)

    def test_historical_slice_uses_saved_parameters_and_repeated_versions_do_not_bridge(self):
        frame = lj_frame(((11, 100., 2.), (22, 140., 3.), (11, 100., 2.)))
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 500, 'sd': 100}, '参数区段')
        try:
            self.assert_labels(figure, 100, 2, multiple=True)
            self.assertEqual(21, len(horizontal_controls(figure.axes[0])))
            for line in horizontal_controls(figure.axes[0]):
                self.assertLess(float(line.get_xdata()[-1]) - float(line.get_xdata()[0]), 1)
        finally:
            plt.close(figure)
        figure = plot_lj_chart(frame.iloc[:1], {'target_ready': True, 'mean': 500, 'sd': 100}, '历史范围')
        try:
            self.assert_labels(figure, 100, 2)
            self.assertLess(figure.axes[0].get_ylim()[1], 120)
        finally:
            plt.close(figure)

    def test_zero_missing_and_invalid_sd_only_label_mean(self):
        for sd in (None, 0, float('nan'), float('inf'), -2):
            for saved in (False, True):
                frame = lj_frame(((11, 100., sd),) * 3)
                if not saved:
                    frame = frame.drop(columns=['target_mean_used', 'target_sd_used', 'target_profile_id'])
                figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 100, 'sd': sd}, '参数核对')
                try:
                    self.assertEqual(['均值'], [text.get_text() for text in labels(figure.axes[0])])
                    self.assertEqual(1, len(horizontal_controls(figure.axes[0])))
                finally:
                    plt.close(figure)

    def test_distant_versions_keep_small_sd_labels_separate(self):
        frame = lj_frame(((11, 100., 2.),) * 3 + ((22, 1000., 1.),) * 3)
        figure = plot_lj_chart(frame, {'target_ready': True, 'mean': 1000, 'sd': 1}, '跨参数质控图')
        try:
            self.assert_labels(figure, 1000, 1, multiple=True)
            path = output_path('lj-distant-versions.png')
            if path:
                figure.savefig(path, bbox_inches='tight')
        finally:
            plt.close(figure)

    def test_multi_level_single_graph_labels_keep_each_levels_scale(self):
        frame = pd.DataFrame([
            dict(run_index=index, level_id=f'Level {level}', raw_value=level * 100 + .1,
                 test_time=pd.Timestamp('2026-04-01') + pd.Timedelta(hours=index),
                 phase='formal_qc', status='accept', target_profile_id=11,
                 formal_reference_mean=level * 100., formal_reference_sd=2.)
            for level in (1, 2) for index in range(1, 5)
        ])
        for level in (1, 2):
            figure = plot_zscore_single_level(frame, f'Level {level}', '单水平图')
            try:
                self.assert_labels(figure, level * 100, 2)
            finally:
                plt.close(figure)
        figure = plot_zscore_overlay(frame, '合并图')
        try:
            self.assertFalse(labels(figure.axes[0]))
        finally:
            plt.close(figure)

    def test_multi_level_reference_scope_keeps_building_formal_all_and_none_semantics(self):
        from zscore_plotting import _plot_reference_lines, _resolve_reference_mode
        frame = pd.DataFrame([
            dict(run_index=index, level_id='Level 1', raw_value=100. + index * .1,
                 test_time=pd.Timestamp('2026-04-01') + pd.Timedelta(hours=index),
                 phase='target_building' if index <= 2 else 'formal_qc', status='accept',
                 building_reference_mean=50., building_reference_sd=20.,
                 formal_reference_mean=100., formal_reference_sd=2.)
            for index in range(1, 5)
        ])
        for scope in ('building', 'formal', 'all'):
            with self.subTest(scope=scope):
                figure = plot_zscore_single_level(frame, 'Level 1', '阶段质控图', phase_scope=scope)
                try:
                    lines = horizontal_controls(figure.axes[0])
                    if scope == 'building':
                        self.assertFalse(lines)
                        self.assertFalse(labels(figure.axes[0]))
                    else:
                        self.assertEqual({94., 96., 98., 100., 102., 104., 106.},
                                         {float(line.get_ydata()[0]) for line in lines})
                        self.assert_labels(figure, 100, 2)
                finally:
                    plt.close(figure)
        building = frame[frame['phase'] == 'target_building']
        self.assertEqual('none', _resolve_reference_mode(building, 'all'))
        figure, axis = plt.subplots()
        try:
            _plot_reference_lines(axis, frame, 'none', show_labels=True)
            self.assertFalse(axis.lines)
            self.assertFalse(labels(axis))
            with self.assertRaisesRegex(ValueError, 'Unsupported reference mode'):
                _plot_reference_lines(axis, frame, 'unsupported', show_labels=True)
        finally:
            plt.close(figure)

    def test_real_instant_transfer_and_paginated_period_pdf_share_labels(self):
        import database as db
        from qc_logic import calculate_qc_results
        from services.instant_service import confirm_instant_transfer_to_lj
        from services.lot_lifecycle_service import create_target_profile
        from services.report_pdf_layout import _build_lj_chart_page, split_report_chart_frames
        from services.report_service import build_lj_period_report_package, build_lj_period_report_pdf
        from tests.instant_v12_fixtures import seed_instant_configuration
        from tests.instant_v12_integration_smoke_test import IsolatedDatabase, entry

        with IsolatedDatabase():
            fixture = seed_instant_configuration()
            for index in range(20):
                entry(fixture, index)
            converted = confirm_instant_transfer_to_lj(fixture['batch_id'])
            batch = converted['target_batch_id']
            db.add_result(batch, '2026-09-12 08:00:00', 101., operator='检测人')
            old_frame, old_stats = calculate_qc_results(db.get_results(batch), target_count=20)
            figure = plot_lj_chart(old_frame, old_stats, '转入后的正式质控图', view_mode='正式质控图')
            try:
                self.assert_labels(figure, old_stats['mean'], old_stats['sd'])
                path = output_path('instant-transferred-formal.png')
                if path:
                    figure.savefig(path, bbox_inches='tight')
            finally:
                plt.close(figure)
            create_target_profile(method='lj', batch_id=batch, levels=[dict(level_id='Level 1', mean=105, sd=.8)],
                                  source='revision', evidence='参数核对', confirmed_by='确认人', effective_at='2026-09-13')
            for index in range(64):
                when = pd.Timestamp('2026-09-13 08:00:00') + pd.Timedelta(hours=index)
                db.add_result(batch, str(when), 105 + (.1 if index % 2 else -.1), operator='检测人')
            with db.read_snapshot() as connection:
                before = tuple(connection.iterdump())
            package = build_lj_period_report_package(batch, '2026-09-12', '2026-09-30')
            frames = split_report_chart_frames('lj', package.formal_df)
            self.assertEqual([1, 60, 4], [len(frame) for frame in frames])
            for index, frame in enumerate(frames, 1):
                figure = _build_lj_chart_page(package, frame=frame, page_label=f'{index}/{len(frames)}')
                try:
                    expected = frame.iloc[-1]
                    self.assert_labels(figure, expected['target_mean_used'], expected['target_sd_used'])
                finally:
                    plt.close(figure)
            pdf = build_lj_period_report_pdf(package)
            reader = PdfReader(BytesIO(pdf))
            chart_pages = [page for page in reader.pages if '报告期质控图' in page.extract_text()]
            self.assertEqual(3, len(chart_pages))
            for page in chart_pages:
                text = page.extract_text()
                for label in LABELS:
                    self.assertIn(label, text)
            with db.read_snapshot() as connection:
                self.assertEqual(before, tuple(connection.iterdump()))
            path = output_path('instant-transfer-period.pdf')
            if path:
                path.write_bytes(pdf)


if __name__ == '__main__':
    unittest.main(verbosity=2)
