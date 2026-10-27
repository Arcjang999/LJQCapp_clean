"""Shared layout for screen charts and standalone PNGs.

Keep all chart text inside the intended canvas: a tight PNG export otherwise
expands to fit an unbroken title and shrinks the actual plot in the browser.
"""
from __future__ import annotations

import math

from matplotlib.transforms import BboxBase

CHART_TITLE_SIZE = 13
CHART_LABEL_SIZE = 12
CHART_TICK_SIZE = 11
CHART_ANNOTATION_MIN_SIZE = 9
CHART_LEGEND_SIZE = 10
CONTROL_LINE_COLORS = {
    0: "#222222",
    1: "#76b7b2",
    2: "#edc948",
    3: "#ff9da7",
}


class _MarkerClipBox(BboxBase):
    """Follow the axes at draw time, with only a marker-sized physical margin."""

    def __init__(self, axis, padding_points):
        super().__init__()
        self.axis = axis
        self.padding_points = padding_points
        self.set_children(axis.bbox, axis.figure.dpi_scale_trans)

    def get_points(self):
        points = self.axis.bbox.get_points().copy()
        x_pad, y_pad = (value * self.axis.figure.dpi / 72 for value in self.padding_points)
        points[0] -= (x_pad, y_pad)
        points[1] += (x_pad, y_pad)
        return points


def scatter_chart_points(axis, *args, **kwargs):
    """Keep edge markers whole while still clipping points outside the view."""
    collection = axis.scatter(*args, **kwargs)
    radius = math.sqrt(max(collection.get_sizes(), default=0))
    stroke = max(collection.get_linewidths(), default=0) / 2 + .75
    paths = [path.get_extents() for path in collection.get_paths()]
    x_pad = max((max(abs(box.x0), abs(box.x1)) for box in paths), default=.5) * radius + stroke
    y_pad = max((max(abs(box.y0), abs(box.y1)) for box in paths), default=.5) * radius + stroke
    collection.set_clip_path(None)
    collection.set_clip_box(_MarkerClipBox(axis, (x_pad, y_pad)))
    collection.set_clip_on(True)
    return collection


def usable_control_sd(value) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved) or resolved <= 0 or math.isclose(resolved, 0.0, abs_tol=1e-12):
        return None
    return resolved


def add_control_line_labels(axis, mean: float, sd, *, mean_label: str = "均值") -> None:
    """Label the visible rightmost reference without changing its parameters."""
    labels = [(mean, mean_label, 0)]
    resolved_sd = usable_control_sd(sd)
    if resolved_sd is not None:
        for multiple in (1, 2, 3):
            labels.extend(((mean + multiple * resolved_sd, f"+{multiple}SD", multiple),
                           (mean - multiple * resolved_sd, f"-{multiple}SD", multiple)))
    for value, label, multiple in labels:
        axis.annotate(label, (1, value), xycoords=axis.get_yaxis_transform(),
                      xytext=(4, 0), textcoords="offset points",
                      fontsize=CHART_ANNOTATION_MIN_SIZE, va="center",
                      color=CONTROL_LINE_COLORS[multiple], clip_on=False,
                      arrowprops={"arrowstyle": "-", "linewidth": .6,
                                  "color": CONTROL_LINE_COLORS[multiple]},
                      gid="qc-control-label")


def arrange_control_line_labels(axis) -> None:
    """Keep small-SD labels readable when a chart spans distant parameters."""
    labels = sorted((text for text in axis.texts if text.get_gid() == 'qc-control-label'),
                    key=lambda text: text.xy[1])
    if not labels:
        return
    dpi = axis.figure.dpi
    bounds = axis.get_window_extent()
    gap = CHART_ANNOTATION_MIN_SIZE * dpi / 72 * 1.35
    bottom, top = bounds.y0 + gap / 2, bounds.y1 - gap / 2
    originals = [axis.transData.transform((0, text.xy[1]))[1] for text in labels]
    positions = []
    for value in originals:
        positions.append(max(bottom, value, positions[-1] + gap if positions else bottom))
    if positions[-1] > top:
        positions[-1] = top
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(positions[index], positions[index + 1] - gap)
    for text, original, position in zip(labels, originals, positions):
        text.set_position((4, (position - original) * 72 / dpi))


def control_parameter_segments(frame, x_column: str, mean_column: str, sd_column: str):
    """Keep adjacent parameter ranges separate, including repeated versions."""
    segments = []
    current = None
    for _, row in frame.sort_values(x_column, kind='stable').iterrows():
        try:
            mean = float(row.get(mean_column))
            x = float(row[x_column])
        except (TypeError, ValueError):
            current = None
            continue
        if not math.isfinite(mean) or not math.isfinite(x):
            current = None
            continue
        sd = usable_control_sd(row.get(sd_column))
        profile = row.get("target_profile_id")
        if isinstance(profile, float) and math.isnan(profile):
            profile = None
        key = (None if profile is None else str(profile), mean, sd)
        if current is None or current["key"] != key:
            current = {"key": key, "left": x, "right": x, "mean": mean, "sd": sd}
            segments.append(current)
        else:
            current["right"] = x
    return segments


def mark_control_parameter_change(axis, x: float) -> None:
    axis.axvline(x, color="#7d5ba6", linestyle=":", linewidth=1)
    axis.annotate("参数调整", (x, 1), xycoords=("data", "axes fraction"),
                  xytext=(3, -5), textcoords="offset points", rotation=90,
                  va="top", fontsize=CHART_ANNOTATION_MIN_SIZE, color="#6a4c93")


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
        arrange_control_line_labels(axis)
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
    arrange_control_line_labels(axis)
