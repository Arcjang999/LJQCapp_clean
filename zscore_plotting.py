from __future__ import annotations

import math

import matplotlib
from matplotlib import font_manager
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

LEVEL_COLORS = {
    "Level 1": "#4e79a7",
    "Level 2": "#76b7b2",
    "Level 3": "#9c755f",
}

STATUS_EDGE_COLORS = {
    "accept": "#ffffff",
    "warning": "#f28e2b",
    "reject": "#e15759",
    "pending": "#7a8ca5",
    "target_building": "#4e79a7",
    "formal_qc": "#59a14f",
}


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


def configure_matplotlib_fonts() -> None:
    font_fallbacks = _get_available_font_fallbacks()
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = font_fallbacks
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_fonts()


def plot_zscore_single_level(
    plot_df: pd.DataFrame,
    level_id: str,
    title: str,
):
    if not _can_plot_zscore_frame(plot_df):
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入本次多水平结果")

    figure, axis = plt.subplots(figsize=(9.4, 5.9), dpi=150)
    level_df = plot_df[plot_df["level_id"] == level_id].sort_values("run_index").copy()
    if level_df.empty:
        plt.close(figure)
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入本次多水平结果")

    _draw_zscore_reference_lines(axis)
    level_color = LEVEL_COLORS.get(level_id, "#4e79a7")
    axis.plot(
        level_df["run_index"],
        level_df["zscore"],
        color=level_color,
        linewidth=1.3,
        alpha=0.85,
        label=level_id,
    )
    _plot_status_points(axis, level_df, level_color)
    _configure_x_axis(axis, level_df)
    axis.set_title(title, pad=10)
    axis.set_xlabel("Run 序号")
    axis.set_ylabel("Z-score / SDI")
    axis.set_ylim(-3.6, 3.6)
    axis.grid(True, linestyle=":", alpha=0.3)
    axis.legend(loc="best")
    figure.tight_layout(pad=0.7)
    return figure


def plot_zscore_overlay(
    plot_df: pd.DataFrame,
    title: str,
    active_levels: list[str] | None = None,
):
    if not _can_plot_zscore_frame(plot_df):
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入本次多水平结果")

    figure, axis = plt.subplots(figsize=(9.4, 5.9), dpi=150)

    overlay_df = plot_df.copy()
    if active_levels:
        overlay_df = overlay_df[overlay_df["level_id"].isin(active_levels)].copy()
    if overlay_df.empty:
        plt.close(figure)
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入本次多水平结果")

    _draw_zscore_reference_lines(axis)
    for level_id in overlay_df["level_id"].drop_duplicates().tolist():
        level_df = overlay_df[overlay_df["level_id"] == level_id].sort_values("run_index").copy()
        level_color = LEVEL_COLORS.get(level_id, "#4e79a7")
        axis.plot(
            level_df["run_index"],
            level_df["zscore"],
            color=level_color,
            linewidth=1.3,
            alpha=0.85,
            label=level_id,
        )
        _plot_status_points(axis, level_df, level_color)

    _configure_x_axis(axis, overlay_df)
    axis.set_title(title, pad=10)
    axis.set_xlabel("Run 序号")
    axis.set_ylabel("Z-score / SDI")
    axis.set_ylim(-3.6, 3.6)
    axis.grid(True, linestyle=":", alpha=0.3)
    axis.legend(loc="best")
    figure.tight_layout(pad=0.7)
    return figure


def _draw_zscore_reference_lines(axis) -> None:
    reference_colors = {1: "#76b7b2", 2: "#edc948", 3: "#ff9da7"}
    axis.axhline(0, color="#222222", linewidth=1.2, linestyle="-")
    axis.text(1.01, 0, "0", transform=axis.get_yaxis_transform(), fontsize=8, va="center")
    for multiplier in (1, 2, 3):
        upper = float(multiplier)
        lower = float(-multiplier)
        axis.axhline(upper, color=reference_colors[multiplier], linewidth=1, linestyle="--")
        axis.axhline(lower, color=reference_colors[multiplier], linewidth=1, linestyle="--")
        axis.text(1.01, upper, f"+{multiplier}", transform=axis.get_yaxis_transform(), fontsize=8, va="center")
        axis.text(1.01, lower, f"-{multiplier}", transform=axis.get_yaxis_transform(), fontsize=8, va="center")


def _plot_status_points(axis, plot_df: pd.DataFrame, level_color: str) -> None:
    for _, point in plot_df.iterrows():
        status = str(point.get("status", "accept"))
        is_preview = bool(point.get("is_preview", False))
        edge_color = STATUS_EDGE_COLORS.get(status, "#7a8ca5")
        marker = "D" if is_preview else "o"
        size = 74 if status == "reject" else 66 if status == "warning" else 52
        axis.scatter(
            [point["run_index"]],
            [point["zscore"]],
            s=size,
            marker=marker,
            facecolors=level_color,
            edgecolors=edge_color,
            linewidths=2.0 if status in {"warning", "reject"} else 1.0,
            zorder=4,
            alpha=0.98 if is_preview else 0.92,
        )


def _configure_x_axis(axis, plot_df: pd.DataFrame) -> None:
    if "run_index" not in plot_df.columns:
        return

    run_axis_columns = ["run_index"]
    if "test_time" in plot_df.columns:
        run_axis_columns.append("test_time")

    run_axis_df = plot_df[run_axis_columns].drop_duplicates().sort_values("run_index").reset_index(drop=True)
    run_indices = run_axis_df["run_index"].astype(int).tolist()
    if not run_indices:
        return

    max_labels = 8
    step = max(1, math.ceil(len(run_indices) / max_labels))
    tick_positions = run_indices[::step]
    if run_indices[-1] not in tick_positions:
        tick_positions.append(run_indices[-1])

    axis.xaxis.set_major_locator(FixedLocator(tick_positions))
    tick_labels: list[str] = []
    for run_index in tick_positions:
        row = run_axis_df.loc[run_axis_df["run_index"] == run_index].iloc[0]
        label = str(run_index)
        if "test_time" in run_axis_df.columns and pd.notna(row["test_time"]):
            timestamp = pd.Timestamp(row["test_time"])
            label = f"{run_index}\n{timestamp.strftime('%m-%d')}"
        tick_labels.append(label)
    axis.set_xticklabels(tick_labels)


def _can_plot_zscore_frame(plot_df: pd.DataFrame | None) -> bool:
    required_columns = {"level_id", "run_index", "zscore"}
    return plot_df is not None and not plot_df.empty and required_columns.issubset(plot_df.columns)


def _build_empty_zscore_figure(title: str, message: str, subtitle: str = ""):
    figure, axis = plt.subplots(figsize=(9.4, 5.9), dpi=150)
    axis.set_title(title, pad=10)
    axis.text(0.5, 0.54, message, ha="center", va="center", transform=axis.transAxes, fontsize=12)
    if subtitle:
        axis.text(
            0.5,
            0.46,
            subtitle,
            ha="center",
            va="center",
            transform=axis.transAxes,
            fontsize=10,
            color="#66768a",
        )
    axis.set_axis_off()
    figure.tight_layout(pad=0.7)
    return figure
