"""Boundary markers stay whole in PNG/PDF, without leaking off-view points."""
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plotting
import zscore_plotting
from services.report_pdf_layout import _decorate_chart_page


def boundary_figure(method):
    figure, axis = plt.subplots(figsize=(6, 4), dpi=120)
    axis.set_position([.12, .25, .8, .5])
    frame = pd.DataFrame({'sequence': [1, 2, 3, 4, 5], 'run_index': [1, 2, 3, 4, 5],
                          'display_value': [0., .5, 1., 1.15, -.15],
                          'status': ['失控' if method == 'lj' else 'reject'] * 5,
                          'rule_hits': [''] * 5})
    if method == 'lj':
        plotting._plot_status_points(axis, frame)
    else:
        zscore_plotting._plot_status_points(axis, frame, '#4e79a7')
    axis.set_xlim(0, 6)
    axis.set_ylim(0, 1)
    return figure, axis


class MarkerBoundaryTests(unittest.TestCase):
    def assert_pixels(self, figure, axis, pixels):
        height, width = pixels.shape[:2]
        scale_x, scale_y = width / figure.bbox.width, height / figure.bbox.height
        red = ((pixels[:, :, 0] > 160) &
               (pixels[:, :, 0] > pixels[:, :, 1].astype(float) * 1.2) &
               (pixels[:, :, 0] > pixels[:, :, 2].astype(float) * 1.1))
        counts = []
        for x, y in ((1, 0), (2, .5), (3, 1)):
            point = axis.transData.transform((x, y))
            column, row = int(round(point[0] * scale_x)), int(round(height - point[1] * scale_y))
            counts.append(int(red[row - 15:row + 16, column - 15:column + 16].sum()))
            if y == 0:
                self.assertGreater(int(red[row + 1:row + 15, column - 15:column + 16].sum()), 5)
            if y == 1:
                self.assertGreater(int(red[row - 15:row, column - 15:column + 16].sum()), 5)
        self.assertGreater(min(counts), 20)
        self.assertLess(max(counts) / min(counts), 1.35, counts)
        # These centers are inside the page but well outside the chart: an
        # unrestricted clip_on=False would draw them in the header/footer.
        for x, y in ((4, 1.15), (5, -.15)):
            point = axis.transData.transform((x, y))
            column, row = int(round(point[0] * scale_x)), int(round(height - point[1] * scale_y))
            self.assertGreater(row - 15, 0)
            self.assertLess(row + 16, height)
            self.assertEqual(int(red[row - 15:row + 16, column - 15:column + 16].sum()), 0)
        self.assertEqual(axis.get_ylim(), (0., 1.))
        self.assertTrue(all(collection.get_clip_on() for collection in axis.collections))

    def check_png(self, method):
        figure, axis = boundary_figure(method)
        try:
            stream = BytesIO()
            figure.savefig(stream, format='png')
            self.assert_pixels(figure, axis, np.array(Image.open(stream).convert('RGB')))
        finally:
            plt.close(figure)

    def test_lj_png_has_complete_upper_and_lower_x_and_clips_far_points(self):
        self.check_png('lj')

    def test_multi_level_png_has_complete_upper_and_lower_x_and_clips_far_points(self):
        self.check_png('zscore')

    def test_pdf_marker_clip_follows_a4_relayout_and_pdf_dpi(self):
        renderer = os.environ.get('LJQC_PDFTOPPM') or shutil.which('pdftoppm')
        if not renderer:
            self.skipTest('Set LJQC_PDFTOPPM to validate the rasterized PDF.')
        with tempfile.TemporaryDirectory(prefix='ljqc-marker-pdf-') as directory:
            root = Path(directory)
            pdf_path = root / 'boundary.pdf'
            figures = []
            try:
                with PdfPages(pdf_path) as pdf:
                    for method in ('lj', 'zscore'):
                        figure, axis = boundary_figure(method)
                        _decorate_chart_page(figure=figure, report_title='状态标记检查',
                                             page_title=method, subtitle_lines=['上下边界'])
                        figures.append((figure, axis))
                        pdf.savefig(figure)
                subprocess.run([renderer, '-r', '120', '-png', str(pdf_path), str(root / 'page')],
                               check=True, capture_output=True)
                for index, (figure, axis) in enumerate(figures, 1):
                    pixels = np.array(Image.open(root / f'page-{index}.png').convert('RGB'))
                    self.assert_pixels(figure, axis, pixels)
                output = os.environ.get('LJQC_BOUNDARY_TEST_OUTPUT')
                if output:
                    folder = Path(output)
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pdf_path, folder / 'status-marker-boundary.pdf')
                    for path in root.glob('page-*.png'):
                        shutil.copy2(path, folder / path.name)
            finally:
                for figure, _ in figures:
                    plt.close(figure)

    def test_real_109_upper_boundary_keeps_values_limits_and_control_lines(self):
        frame = pd.DataFrame([
            dict(id=index, sequence=index, value=value, test_time=pd.Timestamp('2026-04-01'),
                 phase='正式数据', status='失控', rule_hits='', target_profile_id=1,
                 target_mean_used=100., target_sd_used=2.)
            for index, value in enumerate((91., 100., 109., 109., 109.), 1)])
        original = frame.copy(deep=True)
        figure = plotting.plot_lj_chart(frame, {'target_ready': True, 'mean': 100., 'sd': 2.},
                                        '上下边界状态标记', y_axis_mode='标准视图')
        try:
            axis = figure.axes[0]
            self.assertEqual(axis.get_ylim(), (91., 109.))
            self.assertEqual(list(axis.lines[0].get_ydata()), [91., 100., 109., 109., 109.])
            controls = [line for line in axis.lines[1:] if len(line.get_ydata()) == 2]
            self.assertEqual({94., 96., 98., 100., 102., 104., 106.},
                             {float(line.get_ydata()[0]) for line in controls})
            pd.testing.assert_frame_equal(frame, original)
            output = os.environ.get('LJQC_BOUNDARY_TEST_OUTPUT')
            if output:
                folder = Path(output)
                folder.mkdir(parents=True, exist_ok=True)
                figure.savefig(folder / 'lj-109-boundary.png', bbox_inches='tight')
        finally:
            plt.close(figure)


if __name__ == '__main__':
    unittest.main()
