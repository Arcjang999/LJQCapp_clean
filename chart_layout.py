"""Shared layout for screen charts and standalone PNGs.

Keep all chart text inside the intended canvas: a tight PNG export otherwise
expands to fit an unbroken title and shrinks the actual plot in the browser.
"""
from __future__ import annotations

import math

CHART_TITLE_SIZE = 13
CHART_LABEL_SIZE = 12
CHART_TICK_SIZE = 11
CHART_ANNOTATION_MIN_SIZE = 9
CHART_LEGEND_SIZE = 10


def select_detection_ticks(sequences, max_labels: int = 8) -> list[int]:
    """Keep the first/latest detection labels without crowding the final date.

    Use numeric separation as well as row count so sparse or irregularly
    spaced detections do not produce adjacent labels in a much wider range.
    All detections remain plotted; this only chooses their axis labels.
    """
    ordered = sorted(set(int(sequence) for sequence in sequences))
    if len(ordered) <= 1:
        return ordered
    max_labels = max(2, max_labels)
    step = max(1, math.ceil(len(ordered) / max_labels))
    minimum_gap = (ordered[-1] - ordered[0]) / (max_labels - 1) * 0.6
    ticks = [ordered[0]]
    for candidate in ordered[step::step]:
        if candidate - ticks[-1] >= minimum_gap:
            ticks.append(candidate)
    if ticks[-1] != ordered[-1]:
        while len(ticks) > 1 and (
            len(ticks) >= max_labels or ordered[-1] - ticks[-1] < minimum_gap
        ):
            ticks.pop()
        ticks.append(ordered[-1])
    return ticks


def _wrap_chart_title(axis, title: str, max_width: float) -> str:
    renderer = axis.figure.canvas.get_renderer()
    font = axis.title.get_fontproperties().copy()
    font.set_size(CHART_TITLE_SIZE)
    lines: list[str] = []
    for paragraph in str(title or "").split("\n"):
        line = ""
        for character in paragraph:
            candidate = line + character
            width = renderer.get_text_width_height_descent(candidate, font, False)[0]
            if line and width > max_width:
                # Prefer a word boundary when one is near the end. Mixed
                # Chinese text and long lot numbers can also wrap by glyph.
                boundary = line.rfind(" ")
                if boundary >= len(line) * 0.65:
                    lines.append(line[:boundary].rstrip())
                    line = line[boundary + 1:] + character
                else:
                    lines.append(line.rstrip())
                    line = character.lstrip()
            else:
                line = candidate
        lines.append(line.rstrip())
    return "\n".join(lines)


def finish_chart_layout(figure, axis, title: str, *, bottom_legends=()) -> None:
    """Fit readable text and optional legend groups without reducing plot width.

    PDF reports can still replace the returned axes/legend positions with their
    A4 layout. No data, rule labels, phase keys, or title metadata is removed.
    """
    axis.tick_params(axis="both", labelsize=CHART_TICK_SIZE)
    axis.xaxis.label.set_fontsize(CHART_LABEL_SIZE)
    axis.yaxis.label.set_fontsize(CHART_LABEL_SIZE)
    for text in axis.texts:
        text.set_fontsize(max(CHART_ANNOTATION_MIN_SIZE, text.get_fontsize()))
    wrapped_title = _wrap_chart_title(axis, title, figure.bbox.width * 0.88)
    axis.set_title(wrapped_title, fontsize=CHART_TITLE_SIZE, pad=12)
    extra_title_height = max(0, wrapped_title.count("\n")) * 0.24
    width_inches, height_inches = figure.get_size_inches()
    if extra_title_height:
        figure.set_size_inches(width_inches, height_inches + extra_title_height)

    legends = tuple(bottom_legends)
    if not legends:
        figure.tight_layout(pad=0.8)
        return

    renderer = figure.canvas.get_renderer()
    boxes = [legend.get_window_extent(renderer) for legend in legends]
    legend_height = max(box.height for box in boxes) / figure.dpi
    gap_inches = 0.22
    width_inches, height_inches = figure.get_size_inches()
    figure.set_size_inches(width_inches, height_inches + legend_height + gap_inches)
    # The measured legend row is separate from the plotting area. Exclude it
    # only during tight_layout, then restore tight-export inclusion.
    for legend in legends:
        legend.set_in_layout(False)
    bottom = (legend_height + gap_inches) / figure.get_size_inches()[1]
    figure.tight_layout(rect=(0, bottom, 1, 1), pad=0.8)
    widths = [box.width / figure.dpi for box in boxes]
    row_width = sum(widths) + gap_inches * (len(legends) - 1)
    left = (width_inches - row_width) / 2
    for legend, legend_width in zip(legends, widths):
        legend.set_loc("lower center")
        legend.set_bbox_to_anchor(
            ((left + legend_width / 2) / width_inches, 0.02),
            transform=figure.transFigure,
        )
        legend.set_in_layout(True)
        left += legend_width + gap_inches
