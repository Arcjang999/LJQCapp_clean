from __future__ import annotations

from io import BytesIO
import math

import matplotlib
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator
import pandas as pd


matplotlib.use("Agg")
import matplotlib.pyplot as plt


CJK_FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "Noto Sans CJK TC",
    "Noto Sans CJK JP",
    "Noto Serif CJK SC",
    "Noto Serif CJK TC",
    "Noto Serif CJK JP",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimHei",
    "PingFang SC",
    "Arial Unicode MS",
]


def _get_available_font_fallbacks() -> list[str]:
    available_fonts: dict[str, str] = {}
    for font in font_manager.fontManager.ttflist:
        normalized_name = font.name.strip().lower()
        if normalized_name not in available_fonts:
            available_fonts[normalized_name] = font.name

    configured_fonts: list[str] = []
    for candidate in CJK_FONT_CANDIDATES:
        matched_font = available_fonts.get(candidate.lower())
        if matched_font and matched_font not in configured_fonts:
            configured_fonts.append(matched_font)

    if "DejaVu Sans" not in configured_fonts:
        configured_fonts.append("DejaVu Sans")
    return configured_fonts


def configure_matplotlib_fonts() -> list[str]:
    font_fallbacks = _get_available_font_fallbacks()
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = font_fallbacks
    plt.rcParams["axes.unicode_minus"] = False
    return font_fallbacks


CONFIGURED_FONT_FALLBACKS = configure_matplotlib_fonts()
print(f"[plotting] CONFIGURED_FONT_FALLBACKS={CONFIGURED_FONT_FALLBACKS}")

MANUAL_NOTE_EDGE_COLOR = "#2f4858"


def plot_lj_chart(
    qc_df: pd.DataFrame,
    stats: dict,
    title: str,
    view_mode: str = "\u5168\u90e8\u6570\u636e\u56fe",
    y_axis_mode: str = "\u6807\u51c6\u89c6\u56fe",
    standard_sd_limit: float = 4.0,
    y_axis_label: str = "检测值",
):
    figure, axis = plt.subplots(figsize=(9.4, 5.9), dpi=150)
    plot_df = _filter_view_data(qc_df, view_mode)

    if plot_df.empty:
        axis.set_title(title, pad=10)
        axis.text(
            0.5,
            0.5,
            "\u6682\u65e0\u6570\u636e",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
        axis.set_axis_off()
        figure.tight_layout(pad=0.7)
        return figure

    y_limits = _get_y_limits(plot_df, stats, y_axis_mode, standard_sd_limit)
    display_df = _build_display_dataframe(plot_df, y_limits)
    x_values = display_df["sequence"]
    y_values = display_df["display_value"]
    axis.plot(x_values, y_values, color="#4e79a7", linewidth=1.2, alpha=0.8)

    target_df = display_df[display_df["phase"] == "\u5efa\u9776\u6570\u636e"]
    if not target_df.empty:
        axis.scatter(
            target_df["sequence"],
            target_df["display_value"],
            color="#4e79a7",
            s=42,
            label="\u5efa\u9776\u6570\u636e",
            zorder=3,
        )

    formal_df = display_df[display_df["phase"] == "\u6b63\u5f0f\u6570\u636e"]
    _plot_status_points(axis, formal_df)
    _plot_reagent_change_lines(axis, display_df)

    if stats.get("target_ready"):
        _plot_control_lines(axis, stats["mean"], stats["sd"])
    if y_limits is not None:
        axis.set_ylim(y_limits)
        _plot_out_of_range_markers(axis, display_df, y_limits)
    _plot_manual_note_highlights(axis, display_df)

    axis.set_title(title, pad=10)
    axis.set_xlabel("\u68c0\u6d4b\u5e8f\u53f7")
    axis.set_ylabel(y_axis_label)
    _configure_x_axis(axis, display_df)
    axis.grid(True, linestyle=":", alpha=0.35)
    _add_lj_legend(axis)
    figure.tight_layout(pad=0.7)
    return figure


def _plot_status_points(axis, formal_df: pd.DataFrame) -> None:
    if formal_df.empty:
        return

    status_styles = {
        "\u7b26\u5408\u8d28\u63a7": {"color": "#59a14f", "marker": "o", "size": 46},
        "\u8b66\u544a": {"color": "#f28e2b", "marker": "^", "size": 72},
        "\u5931\u63a7": {"color": "#e15759", "marker": "x", "size": 82},
        "\u65e0\u6cd5\u5224\u5b9a\uff08SD=0\uff09": {
            "color": "#9c9c9c",
            "marker": "s",
            "size": 46,
        },
    }

    for status, style in status_styles.items():
        points = formal_df[formal_df["status"] == status]
        if points.empty:
            continue
        axis.scatter(
            points["sequence"],
            points["display_value"],
            color=style["color"],
            marker=style["marker"],
            s=style["size"],
            label=status,
            zorder=4,
        )
        if status in {"\u8b66\u544a", "\u5931\u63a7"}:
            _annotate_rule_hits(axis, points)


def _plot_control_lines(axis, mean: float, sd: float | None) -> None:
    axis.axhline(mean, color="#222222", linewidth=1.2, linestyle="-", label="均值")
    axis.text(1.01, mean, "均值", transform=axis.get_yaxis_transform(), fontsize=8, va="center")
    if sd is None:
        return

    colors = {1: "#76b7b2", 2: "#edc948", 3: "#ff9da7"}
    for multiplier in (1, 2, 3):
        upper = mean + multiplier * sd
        lower = mean - multiplier * sd
        axis.axhline(upper, color=colors[multiplier], linewidth=1, linestyle="--")
        axis.axhline(lower, color=colors[multiplier], linewidth=1, linestyle="--")
        axis.text(1.01, upper, f"+{multiplier}SD", transform=axis.get_yaxis_transform(), fontsize=8, va="center")
        axis.text(1.01, lower, f"-{multiplier}SD", transform=axis.get_yaxis_transform(), fontsize=8, va="center")


def _plot_reagent_change_lines(axis, plot_df: pd.DataFrame) -> None:
    if "reagent_lot_changed" not in plot_df.columns:
        return

    changed_points = plot_df[plot_df["reagent_lot_changed"] == 1]
    for index, (_, point) in enumerate(changed_points.iterrows()):
        label = "\u8bd5\u5242\u6279\u53f7\u53d8\u66f4" if index == 0 else None
        axis.axvline(
            x=float(point["sequence"]) - 0.5,
            color="#808080",
            linestyle="--",
            linewidth=1,
            alpha=0.7,
            label=label,
        )


def _configure_x_axis(axis, plot_df: pd.DataFrame) -> None:
    sequences = plot_df["sequence"].astype(int).tolist()
    if not sequences:
        return

    max_labels = 8
    step = max(1, math.ceil(len(sequences) / max_labels))
    tick_positions = sequences[::step]
    if sequences[-1] not in tick_positions:
        tick_positions.append(sequences[-1])

    axis.xaxis.set_major_locator(FixedLocator(tick_positions))
    if "test_time" in plot_df.columns:
        tick_labels = []
        for sequence in tick_positions:
            test_time = pd.Timestamp(plot_df.loc[plot_df["sequence"] == sequence, "test_time"].iloc[0])
            tick_labels.append(f"{sequence}\n{test_time.strftime('%m-%d')}")
        axis.set_xticklabels(tick_labels)
    else:
        axis.set_xticklabels([str(position) for position in tick_positions])


def _filter_view_data(qc_df: pd.DataFrame, view_mode: str) -> pd.DataFrame:
    if qc_df.empty or "phase" not in qc_df.columns:
        return qc_df.copy()
    filtered_df = qc_df.copy()
    if "is_building_included" in filtered_df.columns:
        disabled_build_mask = (
            (filtered_df["phase"] == "\u5efa\u9776\u6570\u636e")
            & (filtered_df["is_building_included"].fillna(1).astype(int) == 0)
        )
        filtered_df = filtered_df.loc[~disabled_build_mask].copy()
    if view_mode == "\u5efa\u9776\u56fe":
        return filtered_df[filtered_df["phase"] == "\u5efa\u9776\u6570\u636e"].copy()
    if view_mode == "\u6b63\u5f0f\u8d28\u63a7\u56fe":
        return filtered_df[filtered_df["phase"] == "\u6b63\u5f0f\u6570\u636e"].copy()
    return filtered_df.copy()


def _annotate_rule_hits(axis, points: pd.DataFrame) -> None:
    for index, (_, point) in enumerate(points.iterrows()):
        rule_hits = str(point.get("rule_hits", "")).strip()
        if not rule_hits:
            continue
        label = rule_hits.replace(", ", "+")
        x_offset = 6 if index % 2 == 0 else -6
        horizontal_alignment = "left" if x_offset > 0 else "right"
        axis.annotate(
            label,
            xy=(point["sequence"], point["display_value"]),
            xytext=(x_offset, -10),
            textcoords="offset points",
            fontsize=8,
            color="#333333",
            ha=horizontal_alignment,
            va="top",
        )


def _get_y_limits(plot_df: pd.DataFrame, stats: dict, y_axis_mode: str, standard_sd_limit: float) -> tuple[float, float] | None:
    if plot_df.empty or y_axis_mode == "\u5168\u8303\u56f4\u89c6\u56fe":
        return None
    if not stats.get("target_ready") or stats.get("mean") is None or stats.get("sd") is None:
        return None

    mean = float(stats["mean"])
    sd = float(stats["sd"])
    if math.isclose(sd, 0.0, abs_tol=1e-12):
        return None

    padding = max(sd * 0.25, abs(mean) * 0.01, 1e-6)
    lower = mean - standard_sd_limit * sd - padding
    upper = mean + standard_sd_limit * sd + padding
    return lower, upper


def _build_display_dataframe(plot_df: pd.DataFrame, y_limits: tuple[float, float] | None) -> pd.DataFrame:
    display_df = plot_df.copy()
    if y_limits is None or display_df.empty:
        display_df["display_value"] = display_df["value"]
        display_df["is_above_limit"] = False
        display_df["is_below_limit"] = False
        return display_df

    lower, upper = y_limits
    display_df["is_above_limit"] = display_df["value"] > upper
    display_df["is_below_limit"] = display_df["value"] < lower
    display_df["display_value"] = display_df["value"].clip(lower=lower, upper=upper)
    return display_df


def _plot_out_of_range_markers(axis, display_df: pd.DataFrame, y_limits: tuple[float, float]) -> None:
    if display_df.empty:
        return

    lower, upper = y_limits
    above_df = display_df[display_df["is_above_limit"]]
    below_df = display_df[display_df["is_below_limit"]]

    if not above_df.empty:
        axis.scatter(
            above_df["sequence"],
            [upper] * len(above_df),
            marker="^",
            s=90,
            color="#c23b3d",
            label="\u8d85\u51fa\u6807\u51c6\u89c6\u56fe\u4e0a\u9650",
            zorder=5,
        )
        for _, point in above_df.iterrows():
            axis.annotate(
                f"{float(point['value']):.2f}",
                xy=(point["sequence"], upper),
                xytext=(0, -18),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=8,
                color="#7a1f26",
            )

    if not below_df.empty:
        axis.scatter(
            below_df["sequence"],
            [lower] * len(below_df),
            marker="v",
            s=90,
            color="#c23b3d",
            label="\u8d85\u51fa\u6807\u51c6\u89c6\u56fe\u4e0b\u9650",
            zorder=5,
        )
        for _, point in below_df.iterrows():
            axis.annotate(
                f"{float(point['value']):.2f}",
                xy=(point["sequence"], lower),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color="#7a1f26",
            )


def _plot_manual_note_highlights(axis, display_df: pd.DataFrame) -> None:
    if display_df.empty or "manual_note" not in display_df.columns:
        return

    note_mask = display_df["manual_note"].fillna("").astype(str).str.strip() != ""
    note_df = display_df[note_mask]
    if note_df.empty:
        return

    axis.scatter(
        note_df["sequence"],
        note_df["display_value"],
        s=118,
        marker="o",
        facecolors="none",
        edgecolors=MANUAL_NOTE_EDGE_COLOR,
        linewidths=1.35,
        zorder=6,
    )


def _add_lj_legend(axis) -> None:
    handles, labels = axis.get_legend_handles_labels()
    handles.append(
        Line2D(
            [0],
            [0],
            marker="o",
            markerfacecolor="none",
            markeredgecolor=MANUAL_NOTE_EDGE_COLOR,
            markeredgewidth=1.35,
            linestyle="None",
            markersize=8,
            label="描边点=含手动备注",
        )
    )
    labels.append("描边点=含手动备注")

    unique_handles: list[object] = []
    unique_labels: list[str] = []
    for handle, label in zip(handles, labels):
        if not label or label.startswith("_") or label in unique_labels:
            continue
        unique_handles.append(handle)
        unique_labels.append(label)
    axis.legend(unique_handles, unique_labels, loc="best")


def plot_instant_chart(
    analysis_df: pd.DataFrame,
    summary: dict[str, object],
    title: str,
    *,
    y_axis_label: str = "检测值",
):
    figure, axis = plt.subplots(figsize=(9.2, 5.6), dpi=150)
    if analysis_df.empty:
        axis.set_title(title, pad=10)
        axis.text(
            0.5,
            0.5,
            "暂无数据",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
        axis.set_axis_off()
        figure.tight_layout(pad=0.7)
        return figure

    plot_df = analysis_df[analysis_df["is_effective"] == 1].copy()
    plot_df = plot_df.sort_values(["test_time", "id"]).reset_index(drop=True)
    if plot_df.empty:
        axis.set_title(title, pad=10)
        axis.text(
            0.5,
            0.5,
            "当前批次暂无有效点",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
        axis.set_axis_off()
        figure.tight_layout(pad=0.7)
        return figure

    if "effective_sequence" not in plot_df.columns or plot_df["effective_sequence"].isna().all():
        plot_df["effective_sequence"] = range(1, len(plot_df) + 1)
    x_values = plot_df["effective_sequence"].astype(float)
    y_values = plot_df["value"].astype(float)

    axis.plot(
        x_values,
        y_values,
        color="#4e79a7",
        linewidth=1.25,
        alpha=0.85,
        label="有效点趋势",
        zorder=2,
    )

    normal_df = plot_df[plot_df["is_outlier_suspect"] != 1]
    suspect_df = plot_df[plot_df["is_outlier_suspect"] == 1]
    if not normal_df.empty:
        axis.scatter(
            normal_df["effective_sequence"],
            normal_df["value"],
            color="#59a14f",
            marker="o",
            s=48,
            label="有效点",
            zorder=3,
        )
    if not suspect_df.empty:
        axis.scatter(
            suspect_df["effective_sequence"],
            suspect_df["value"],
            color="#e15759",
            marker="D",
            s=68,
            label="疑似离群",
            zorder=4,
        )
        for _, point in suspect_df.iterrows():
            axis.annotate(
                "疑似离群",
                xy=(float(point["effective_sequence"]), float(point["value"])),
                xytext=(0, -12),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color="#8f1f28",
            )

    latest_point = plot_df.iloc[-1]
    axis.scatter(
        [float(latest_point["effective_sequence"])],
        [float(latest_point["value"])],
        s=132,
        facecolors="none",
        edgecolors="#1f2d3d",
        linewidths=1.4,
        label="当前点",
        zorder=5,
    )

    mean_value = summary.get("mean")
    sd_value = summary.get("sd")
    if mean_value is not None:
        resolved_mean = float(mean_value)
        axis.axhline(resolved_mean, color="#222222", linewidth=1.1, linestyle="-", label="均值")
        axis.text(1.01, resolved_mean, "均值", transform=axis.get_yaxis_transform(), fontsize=8, va="center")
        if sd_value is not None and not math.isclose(float(sd_value), 0.0, abs_tol=1e-12):
            resolved_sd = float(sd_value)
            upper = resolved_mean + resolved_sd
            lower = resolved_mean - resolved_sd
            axis.axhline(upper, color="#76b7b2", linewidth=1.0, linestyle="--", label="+1SD")
            axis.axhline(lower, color="#76b7b2", linewidth=1.0, linestyle="--", label="-1SD")

    _configure_instant_x_axis(axis, plot_df)
    axis.set_title(title, pad=10)
    axis.set_xlabel("有效点序号")
    axis.set_ylabel(y_axis_label)
    axis.grid(True, linestyle=":", alpha=0.35)

    handles, labels = axis.get_legend_handles_labels()
    unique_handles: list[object] = []
    unique_labels: list[str] = []
    for handle, label in zip(handles, labels):
        if not label or label.startswith("_") or label in unique_labels:
            continue
        unique_handles.append(handle)
        unique_labels.append(label)
    axis.legend(unique_handles, unique_labels, loc="best")
    figure.tight_layout(pad=0.7)
    return figure


def _configure_instant_x_axis(axis, plot_df: pd.DataFrame) -> None:
    sequences = plot_df["effective_sequence"].dropna().astype(int).tolist()
    if not sequences:
        return
    max_labels = 8
    step = max(1, math.ceil(len(sequences) / max_labels))
    tick_positions = sequences[::step]
    if sequences[-1] not in tick_positions:
        tick_positions.append(sequences[-1])
    axis.xaxis.set_major_locator(FixedLocator(tick_positions))
    tick_labels = []
    for sequence in tick_positions:
        matched_rows = plot_df.loc[plot_df["effective_sequence"] == sequence, "test_time"]
        if matched_rows.empty:
            tick_labels.append(str(sequence))
            continue
        test_time = pd.Timestamp(matched_rows.iloc[0])
        tick_labels.append(f"{sequence}\n{test_time.strftime('%m-%d')}")
    axis.set_xticklabels(tick_labels)


def figure_to_png_bytes(figure) -> bytes:
    buffer = BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    return buffer.getvalue()
