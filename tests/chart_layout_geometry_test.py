"""Tight PNGs must keep a readable plot with long mixed Chinese/Latin titles."""
from io import BytesIO
from pathlib import Path
import sys

import matplotlib.pyplot as plt
from matplotlib.legend import Legend
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chart_layout import finish_chart_layout, select_detection_ticks
from plotting import _configure_instant_x_axis, _configure_x_axis as _configure_lj_x_axis, plot_instant_chart, plot_lj_chart
from zscore_plotting import _configure_x_axis as _configure_zscore_x_axis, plot_zscore_overlay, plot_zscore_single_level


LONG_TITLE = (
    "即时法趋势图 - 质控批号 QC-202608-RSVB-A-L1 | SLAN-96P · 01号 | "
    "呼吸道合胞病毒核酸检测试剂盒（PCR-荧光探针法） | "
    "呼吸道合胞病毒B型核糖核酸(RSV-B RNA)液体室内质控品 | "
    "1.00*10^3～1.00*10^4copies/mL（编号 L1）"
)


def _single_frame():
    return pd.DataFrame([
        {
            "id": i, "sequence": i, "value": 30 + (-1) ** i * 0.15,
            "is_effective": 1, "is_outlier_suspect": 0,
            "test_time": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
            "phase": "正式数据", "status": "符合质控", "rule_hits": "",
        }
        for i in range(1, 20)
    ])


def _three_level_frame():
    return pd.DataFrame([
        {
            "run_index": i, "level_id": f"Level {level}",
            "raw_value": level * 100 + (30 if i == 4 else (-1) ** i),
            "test_time": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
            "phase": "target_building" if i <= 2 else "formal_qc",
            "status": {3: "warning", 4: "reject"}.get(i, "accept"),
            "rule_hits": "1_3s" if i == 4 else "",
            "formal_reference_mean": level * 100, "formal_reference_sd": 2,
            "building_reference_mean": level * 100, "building_reference_sd": 2,
            "manual_note": "已核对" if i == 4 else "",
        }
        for level in (1, 2, 3) for i in range(1, 6)
    ])


def _assert_title_and_png_bounds(figure, title, dpi):
    figure.set_dpi(dpi)
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    axis = figure.axes[0]
    title_box = axis.title.get_window_extent(renderer)
    assert "".join(axis.get_title().split()) == "".join(title.split())
    assert -1 <= title_box.x0 < title_box.x1 <= figure.bbox.width + 1
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
    image = Image.open(buffer)
    # The exported canvas may include the normal 0.1-inch savefig padding.
    # An unwrapped title previously made it almost twice as wide.
    assert image.width <= figure.bbox.width + dpi * 0.25
    assert axis.get_window_extent(renderer).width / image.width >= 0.80
    return renderer


def test_long_titles_keep_plot_width_and_all_metadata():
    frame = _single_frame()
    zframe = _three_level_frame()
    figures = [
        plot_instant_chart(frame, {"mean": 30, "sd": .15}, LONG_TITLE),
        plot_lj_chart(frame, {"target_ready": True, "mean": 30, "sd": .15}, LONG_TITLE),
        plot_zscore_single_level(zframe, "Level 1", LONG_TITLE),
        plot_zscore_overlay(zframe, LONG_TITLE),
    ]
    try:
        for figure in figures:
            for dpi in (150, 200):
                _assert_title_and_png_bounds(figure, LONG_TITLE, dpi)
            assert figure.axes[0].get_yticklabels()[0].get_fontsize() >= 11
    finally:
        for figure in figures:
            plt.close(figure)


def test_three_level_legends_are_below_labels_and_inside_export():
    figure = plot_zscore_overlay(_three_level_frame(), "全部数据图", standard_sd_limit=3)
    try:
        renderer = _assert_title_and_png_bounds(figure, "全部数据图", 200)
        axis = figure.axes[0]
        legends = list(dict.fromkeys(artist for artist in axis.artists if isinstance(artist, Legend)))
        legend_text = {legend.get_title().get_text(): [text.get_text() for text in legend.get_texts()] for legend in legends}
        assert legend_text["状态"] == ["正常", "警告", "失控", "超界裁切点"]
        assert legend_text["水平"] == ["水平 1", "水平 2", "水平 3"]
        assert legend_text["阶段 / 样式"] == [
            "参数建立期（虚线 / 方形点）", "正式期（实线 / 圆形点）",
            "均值 / ±SD 控制线", "描边点=含手动备注",
        ]
        boxes = [legend.get_window_extent(renderer) for legend in legends]
        for box in boxes:
            assert 0 <= box.x0 < box.x1 <= figure.bbox.width
            assert 0 <= box.y0 < box.y1 < axis.xaxis.label.get_window_extent(renderer).y0
        for index, box in enumerate(boxes):
            assert all(not box.overlaps(other) for other in boxes[index + 1:])
        assert axis.get_position().width >= .85
    finally:
        plt.close(figure)


def test_empty_charts_also_bound_long_titles():
    figures = [
        plot_instant_chart(pd.DataFrame(), {}, LONG_TITLE),
        plot_lj_chart(pd.DataFrame(), {}, LONG_TITLE),
        plot_zscore_single_level(pd.DataFrame(), "Level 1", LONG_TITLE),
        plot_zscore_overlay(pd.DataFrame(), LONG_TITLE),
    ]
    try:
        for figure in figures:
            _assert_title_and_png_bounds(figure, LONG_TITLE, 200)
    finally:
        for figure in figures:
            plt.close(figure)


def test_final_detection_date_does_not_overlap_previous_tick():
    cases = [[], [7], [7, 8], list(range(1, 9)), list(range(21, 94)),
             [1, 2, 100, 101], [3, 10, 11, 35, 36, 90, 91], [91, 3, 35, 3]]
    for sequences in cases:
        ticks = select_detection_ticks(sequences)
        assert ticks == sorted(set(ticks)) and len(ticks) <= 8
        assert set(ticks).issubset(sequences)
        if sequences:
            assert ticks[0] == min(sequences) and ticks[-1] == max(sequences)
    assert select_detection_ticks(range(21, 94))[-2:] == [81, 93]

    # Exercise each actual axis formatter, including its two-line date text.
    for sequences in (list(range(21, 94)), [1, 2, 100, 101], [7], [7, 8]):
        frame = pd.DataFrame({
            "sequence": sequences, "effective_sequence": sequences, "run_index": sequences,
            "test_time": [pd.Timestamp("2026-09-01") + pd.Timedelta(days=i) for i in sequences],
        })
        for configure in (_configure_lj_x_axis, _configure_instant_x_axis, _configure_zscore_x_axis):
            figure, axis = plt.subplots(figsize=(9.4, 5.9), dpi=150)
            try:
                axis.set_xlim(min(sequences) - .5, max(sequences) + .5)
                configure(axis, frame)
                finish_chart_layout(figure, axis, "质控图")
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                labels = axis.get_xticklabels()
                boxes = [label.get_window_extent(renderer) for label in labels]
                assert all(not left.overlaps(right) for left, right in zip(boxes, boxes[1:]))
                last_date = frame.iloc[-1]["test_time"].strftime("%m-%d")
                assert labels[-1].get_text() == f"{sequences[-1]}\n{last_date}"
            finally:
                plt.close(figure)


if __name__ == "__main__":
    test_long_titles_keep_plot_width_and_all_metadata()
    test_three_level_legends_are_below_labels_and_inside_export()
    test_empty_charts_also_bound_long_titles()
    test_final_detection_date_does_not_overlap_previous_tick()
    print("chart_layout_geometry_test passed")
