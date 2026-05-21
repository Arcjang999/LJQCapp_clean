from __future__ import annotations

import math
from pathlib import Path

import matplotlib
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator
import pandas as pd


matplotlib.use("Agg")
import matplotlib.pyplot as plt

from services.profiling import profile_timer
from zscore_logic import format_level_id_display


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

LINUX_CJK_FONT_FILE_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

LEVEL_COLORS = {
    "Level 1": "#4e79a7",
    "Level 2": "#76b7b2",
    "Level 3": "#9c755f",
}

PHASE_TARGET_BUILDING = "target_building"
PHASE_FORMAL_QC = "formal_qc"

STATUS_POINT_STYLES = {
    "accept": {"color": "#59a14f", "marker": "o", "size": 46},
    "warning": {"color": "#f28e2b", "marker": "^", "size": 72},
    "reject": {"color": "#e15759", "marker": "x", "size": 82},
    "pending": {"color": "#9c9c9c", "marker": "s", "size": 46},
    PHASE_TARGET_BUILDING: {"color": "#4e79a7", "marker": "s", "size": 48},
}

REFERENCE_LINE_COLORS = {
    0: "#222222",
    1: "#76b7b2",
    2: "#edc948",
    3: "#ff9da7",
}

MANUAL_NOTE_EDGE_COLOR = "#2f4858"
ZSCORE_SINGLE_FIGSIZE = (12.8, 6.4)
ZSCORE_OVERLAY_FIGSIZE = (13.8, 6.6)
ZSCORE_DPI = 140


def _apply_zscore_chart_layout(
    figure,
    *,
    right: float = 0.94,
    top: float = 0.88,
) -> None:
    figure.subplots_adjust(left=0.07, right=right, top=top, bottom=0.14)


def _append_unique_font_name(font_names: list[str], font_name: str | None) -> None:
    resolved_name = str(font_name or "").strip()
    if resolved_name and resolved_name not in font_names:
        font_names.append(resolved_name)


def _register_linux_cjk_fonts() -> list[str]:
    registered_fonts: list[str] = []
    for font_file in LINUX_CJK_FONT_FILE_CANDIDATES:
        if not Path(font_file).is_file():
            continue
        try:
            font_manager.fontManager.addfont(font_file)
            registered_name = font_manager.FontProperties(fname=font_file).get_name()
        except Exception:
            continue
        _append_unique_font_name(registered_fonts, registered_name)
    return registered_fonts


def _get_available_font_name_map() -> dict[str, str]:
    available_fonts: dict[str, str] = {}
    for font in font_manager.fontManager.ttflist:
        normalized_name = font.name.strip().lower()
        if normalized_name and normalized_name not in available_fonts:
            available_fonts[normalized_name] = font.name.strip()
    return available_fonts


def _get_available_font_fallbacks() -> list[str]:
    registered_fonts = _register_linux_cjk_fonts()
    available_fonts = _get_available_font_name_map()

    configured_fonts: list[str] = []
    for registered_name in registered_fonts:
        _append_unique_font_name(
            configured_fonts,
            available_fonts.get(registered_name.lower(), registered_name),
        )

    for candidate in CJK_FONT_CANDIDATES:
        matched_font = available_fonts.get(candidate.lower())
        _append_unique_font_name(configured_fonts, matched_font)

    configured_fonts = [name for name in configured_fonts if name != "DejaVu Sans"]
    configured_fonts.append("DejaVu Sans")
    return configured_fonts


def configure_matplotlib_fonts() -> list[str]:
    font_fallbacks = _get_available_font_fallbacks()
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = font_fallbacks
    plt.rcParams["axes.unicode_minus"] = False
    return font_fallbacks


CONFIGURED_FONT_FALLBACKS = configure_matplotlib_fonts()


def filter_zscore_plot_df(plot_df: pd.DataFrame, phase_scope: str) -> pd.DataFrame:
    normalized_scope = _normalize_phase_scope(phase_scope)
    if plot_df is None:
        return pd.DataFrame()
    if plot_df.empty or "phase" not in plot_df.columns:
        return plot_df.copy()
    if normalized_scope == "building":
        if "is_building_stat_point" in plot_df.columns:
            scoped_df = plot_df[plot_df["is_building_stat_point"]].copy()
        else:
            scoped_df = plot_df[plot_df["phase"] == PHASE_TARGET_BUILDING].copy()
        if not scoped_df.empty:
            scoped_df["plot_phase"] = PHASE_TARGET_BUILDING
        return scoped_df
    if normalized_scope == "formal":
        scoped_df = plot_df[plot_df["phase"] == PHASE_FORMAL_QC].copy()
        if not scoped_df.empty:
            scoped_df["plot_phase"] = PHASE_FORMAL_QC
        return scoped_df
    scoped_df = plot_df.copy()
    if not scoped_df.empty and "plot_phase" not in scoped_df.columns:
        scoped_df["plot_phase"] = scoped_df["phase"]
    return scoped_df


def _plot_zscore_single_level_impl(
    plot_df: pd.DataFrame,
    level_id: str,
    title: str,
    phase_scope: str = "all",
    y_axis_mode: str = "标准视图",
    standard_sd_limit: float = 4.0,
    y_axis_label: str = "检测值",
):
    normalized_scope = _normalize_phase_scope(phase_scope)
    normalized_y_axis_mode = _normalize_y_axis_mode(y_axis_mode)
    scoped_df = filter_zscore_plot_df(plot_df, normalized_scope)
    if not _can_plot_zscore_frame(scoped_df):
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入当前批次的 Z-score 结果。")

    figure, axis = plt.subplots(figsize=ZSCORE_SINGLE_FIGSIZE, dpi=ZSCORE_DPI)
    level_df = scoped_df[scoped_df["level_id"] == level_id].sort_values("run_index").copy()
    if level_df.empty:
        plt.close(figure)
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "当前视图下还没有该水平的记录。")

    prepared_df = _prepare_plot_dataframe(level_df)
    reference_mode = _resolve_reference_mode(prepared_df, normalized_scope)
    show_reference_lines = reference_mode != "none"
    y_limits = _get_value_y_limits(
        prepared_df,
        normalized_y_axis_mode,
        float(standard_sd_limit),
        reference_mode,
    )
    display_df = _build_display_dataframe(prepared_df, y_limits)
    level_color = LEVEL_COLORS.get(level_id, "#4e79a7")
    display_level = format_level_id_display(level_id)

    if normalized_scope == "all":
        _plot_reference_lines(axis, display_df, reference_mode)
        _plot_continuous_trajectory(axis, display_df, level_color)
        _plot_phase_separator(axis, display_df)
        for phase in [PHASE_TARGET_BUILDING, PHASE_FORMAL_QC]:
            phase_df = display_df[display_df["phase"] == phase].sort_values("run_index").copy()
            if phase_df.empty:
                continue
            _plot_phase_line(axis, phase_df, level_color, f"{display_level} | {_phase_label(phase)}")
            _plot_status_points(axis, phase_df, level_color)
    else:
        _plot_reference_lines(axis, display_df, reference_mode)
        _plot_phase_line(axis, display_df, level_color, display_level)
        _plot_status_points(axis, display_df, level_color)

    if y_limits is not None:
        axis.set_ylim(y_limits)
        _plot_out_of_range_markers(axis, display_df, y_limits)
    _plot_manual_note_highlights(axis, display_df)
    _configure_x_axis(axis, display_df)
    axis.set_title(title, pad=8, fontsize=12)
    axis.set_xlabel("检测序号")
    axis.set_ylabel(y_axis_label)
    axis.grid(True, linestyle=":", alpha=0.3)
    _apply_zscore_chart_layout(figure, right=0.94)
    _add_manual_legends(axis, display_df, show_reference_lines=show_reference_lines)
    return figure


def plot_zscore_single_level(
    plot_df: pd.DataFrame,
    level_id: str,
    title: str,
    phase_scope: str = "all",
    y_axis_mode: str = "\u6807\u51c6\u89c6\u56fe",
    standard_sd_limit: float = 4.0,
    y_axis_label: str = "\u68c0\u6d4b\u503c",
):
    with profile_timer(
        "plot_zscore_single_level",
        rows=0 if plot_df is None else len(plot_df),
        level_id=level_id,
        phase_scope=phase_scope,
        y_axis_mode=y_axis_mode,
    ):
        return _plot_zscore_single_level_impl(
            plot_df=plot_df,
            level_id=level_id,
            title=title,
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=y_axis_label,
        )


def _plot_zscore_overlay_impl(
    plot_df: pd.DataFrame,
    title: str,
    active_levels: list[str] | None = None,
    phase_scope: str = "all",
    y_axis_mode: str = "标准视图",
    standard_sd_limit: float = 4.0,
    y_axis_label: str = "检测值",
):
    normalized_scope = _normalize_phase_scope(phase_scope)
    normalized_y_axis_mode = _normalize_y_axis_mode(y_axis_mode)
    scoped_df = filter_zscore_plot_df(plot_df, normalized_scope)
    if not _can_plot_zscore_frame(scoped_df):
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "请先录入当前批次的 Z-score 结果。")

    figure, axis = plt.subplots(figsize=ZSCORE_OVERLAY_FIGSIZE, dpi=ZSCORE_DPI)
    overlay_df = scoped_df.copy()
    if active_levels:
        overlay_df = overlay_df[overlay_df["level_id"].isin(active_levels)].copy()
    if overlay_df.empty:
        plt.close(figure)
        return _build_empty_zscore_figure(title, "暂无可绘制数据", "当前视图下还没有可叠加的水平数据。")

    prepared_df = _prepare_plot_dataframe(overlay_df)
    reference_mode = _resolve_reference_mode(prepared_df, normalized_scope)
    show_reference_lines = reference_mode != "none"
    y_limits = _get_value_y_limits(
        prepared_df,
        normalized_y_axis_mode,
        float(standard_sd_limit),
        reference_mode,
    )
    display_df = _build_display_dataframe(prepared_df, y_limits)

    for current_level_id in display_df["level_id"].drop_duplicates().tolist():
        level_df = display_df[display_df["level_id"] == current_level_id].sort_values("run_index").copy()
        level_color = LEVEL_COLORS.get(current_level_id, "#4e79a7")
        display_level = format_level_id_display(current_level_id)
        _plot_reference_lines(axis, level_df, reference_mode)
        if normalized_scope == "all":
            _plot_continuous_trajectory(axis, level_df, level_color)
            for phase in [PHASE_TARGET_BUILDING, PHASE_FORMAL_QC]:
                phase_df = level_df[level_df["phase"] == phase].sort_values("run_index").copy()
                if phase_df.empty:
                    continue
                _plot_phase_line(axis, phase_df, level_color, f"{display_level} | {_phase_label(phase)}")
                _plot_status_points(axis, phase_df, level_color)
        else:
            _plot_phase_line(axis, level_df, level_color, display_level)
            _plot_status_points(axis, level_df, level_color)

    if normalized_scope == "all":
        _plot_phase_separator(axis, display_df)

    if y_limits is not None:
        axis.set_ylim(y_limits)
        _plot_out_of_range_markers(axis, display_df, y_limits)
    _plot_manual_note_highlights(axis, display_df)
    _configure_x_axis(axis, display_df)
    axis.set_title(title, pad=8, fontsize=12)
    axis.set_xlabel("检测序号")
    axis.set_ylabel(y_axis_label)
    axis.grid(True, linestyle=":", alpha=0.3)
    _apply_zscore_chart_layout(figure, right=0.80)
    _add_manual_legends(
        axis,
        display_df,
        level_ids=display_df["level_id"].drop_duplicates().tolist(),
        show_reference_lines=show_reference_lines,
        place_outside=True,
    )
    return figure


def plot_zscore_overlay(
    plot_df: pd.DataFrame,
    title: str,
    active_levels: list[str] | None = None,
    phase_scope: str = "all",
    y_axis_mode: str = "\u6807\u51c6\u89c6\u56fe",
    standard_sd_limit: float = 4.0,
    y_axis_label: str = "\u68c0\u6d4b\u503c",
):
    with profile_timer(
        "plot_zscore_overlay",
        rows=0 if plot_df is None else len(plot_df),
        phase_scope=phase_scope,
        y_axis_mode=y_axis_mode,
        level_count=0 if active_levels is None else len(active_levels),
    ):
        return _plot_zscore_overlay_impl(
            plot_df=plot_df,
            title=title,
            active_levels=active_levels,
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=y_axis_label,
        )


def _prepare_plot_dataframe(plot_df: pd.DataFrame) -> pd.DataFrame:
    prepared_df = _ensure_plot_columns(plot_df)
    if prepared_df.empty:
        return prepared_df

    building_mask = prepared_df["plot_phase"] == PHASE_TARGET_BUILDING
    prepared_df["reference_mean"] = prepared_df["formal_reference_mean"].combine_first(
        prepared_df["building_reference_mean"]
    )
    prepared_df["reference_sd"] = prepared_df["formal_reference_sd"].combine_first(
        prepared_df["building_reference_sd"]
    )
    prepared_df.loc[building_mask, "reference_mean"] = prepared_df.loc[
        building_mask, "building_reference_mean"
    ].combine_first(prepared_df.loc[building_mask, "formal_reference_mean"])
    prepared_df.loc[building_mask, "reference_sd"] = prepared_df.loc[
        building_mask, "building_reference_sd"
    ].combine_first(prepared_df.loc[building_mask, "formal_reference_sd"])
    prepared_df["plot_status"] = prepared_df["status"]
    prepared_df.loc[building_mask, "plot_status"] = PHASE_TARGET_BUILDING
    return prepared_df


def _ensure_plot_columns(plot_df: pd.DataFrame) -> pd.DataFrame:
    prepared_df = plot_df.copy()
    default_columns = {
        "run_index": None,
        "raw_value": None,
        "phase": PHASE_FORMAL_QC,
        "plot_phase": None,
        "status": "pending",
        "building_reference_mean": None,
        "building_reference_sd": None,
        "formal_reference_mean": None,
        "formal_reference_sd": None,
        "is_preview": False,
        "manual_note": "",
    }
    for column_name, default_value in default_columns.items():
        if column_name not in prepared_df.columns:
            prepared_df[column_name] = default_value
    if "plot_phase" in prepared_df.columns:
        prepared_df["plot_phase"] = prepared_df["plot_phase"].where(
            prepared_df["plot_phase"].notna(),
            prepared_df["phase"],
        )
    return prepared_df


def _plot_reference_lines(axis, plot_df: pd.DataFrame, reference_mode: str) -> None:
    reference_profile = _resolve_reference_profile(plot_df, reference_mode)
    if plot_df.empty or reference_profile is None:
        return
    if "run_index" not in plot_df.columns:
        return

    x_min = float(plot_df["run_index"].min())
    x_max = float(plot_df["run_index"].max())
    mean_value = float(reference_profile["mean"])
    axis.plot(
        [x_min, x_max],
        [mean_value, mean_value],
        color=REFERENCE_LINE_COLORS[0],
        linewidth=1.1,
        linestyle="-",
        alpha=0.8,
        zorder=1,
    )
    sd_value = reference_profile.get("sd")
    if sd_value is None or math.isclose(float(sd_value), 0.0, abs_tol=1e-12):
        return

    for multiplier in (1, 2, 3):
        upper = mean_value + multiplier * float(sd_value)
        lower = mean_value - multiplier * float(sd_value)
        axis.plot(
            [x_min, x_max],
            [upper, upper],
            color=REFERENCE_LINE_COLORS[multiplier],
            linewidth=0.95,
            linestyle="--",
            alpha=0.85,
            zorder=1,
        )
        axis.plot(
            [x_min, x_max],
            [lower, lower],
            color=REFERENCE_LINE_COLORS[multiplier],
            linewidth=0.95,
            linestyle="--",
            alpha=0.85,
            zorder=1,
        )


def _plot_phase_line(axis, phase_df: pd.DataFrame, level_color: str, label: str) -> None:
    phase = _resolve_plot_phase(phase_df)
    axis.plot(
        phase_df["run_index"],
        phase_df["display_value"],
        color=level_color,
        linewidth=1.3,
        alpha=0.58 if phase == PHASE_TARGET_BUILDING else 0.88,
        linestyle=_phase_linestyle(phase),
        label=label,
    )


def _plot_continuous_trajectory(axis, plot_df: pd.DataFrame, level_color: str) -> None:
    if plot_df.empty:
        return
    ordered_df = plot_df.sort_values("run_index").copy()
    axis.plot(
        ordered_df["run_index"],
        ordered_df["display_value"],
        color=level_color,
        linewidth=1.2,
        alpha=0.34,
        linestyle="-",
        zorder=2,
        label="_nolegend_",
    )


def _get_phase_separator_position(plot_df: pd.DataFrame) -> float | None:
    if plot_df.empty or "plot_phase" not in plot_df.columns or "run_index" not in plot_df.columns:
        return None
    building_runs = plot_df.loc[plot_df["plot_phase"] == PHASE_TARGET_BUILDING, "run_index"].dropna()
    formal_runs = plot_df.loc[plot_df["plot_phase"] == PHASE_FORMAL_QC, "run_index"].dropna()
    if building_runs.empty or formal_runs.empty:
        return None

    building_last = float(building_runs.max())
    formal_first = float(formal_runs.min())
    if formal_first <= building_last:
        return None
    return (building_last + formal_first) / 2.0


def _plot_phase_separator(axis, plot_df: pd.DataFrame) -> None:
    separator_x = _get_phase_separator_position(plot_df)
    if separator_x is None:
        return
    axis.axvline(
        separator_x,
        color="#7a8ca5",
        linewidth=1.0,
        linestyle=":",
        alpha=0.9,
        zorder=1,
    )


def _plot_status_points(axis, plot_df: pd.DataFrame, level_color: str) -> None:
    for _, point in plot_df.iterrows():
        status = str(point.get("plot_status", point.get("status", "accept")))
        is_preview = bool(point.get("is_preview", False))
        style = STATUS_POINT_STYLES.get(status, STATUS_POINT_STYLES["pending"])
        marker = "D" if is_preview else style["marker"]
        size = 74 if is_preview else style["size"]
        point_y = point["display_value"]

        if status == PHASE_TARGET_BUILDING:
            axis.scatter(
                [point["run_index"]],
                [point_y],
                s=size,
                marker=marker,
                facecolors=level_color,
                edgecolors="#ffffff",
                linewidths=1.0,
                zorder=4,
                alpha=0.92 if is_preview else 0.78,
            )
            continue

        scatter_kwargs = {
            "s": size,
            "marker": marker,
            "color": style["color"],
            "linewidths": 2.0 if status in {"warning", "reject"} else 0.9,
            "zorder": 4,
            "alpha": 0.98 if is_preview else 0.92,
        }
        if marker != "x":
            scatter_kwargs["edgecolors"] = "#ffffff"
        axis.scatter(
            [point["run_index"]],
            [point_y],
            **scatter_kwargs,
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


def _get_value_y_limits(
    plot_df: pd.DataFrame,
    y_axis_mode: str,
    standard_sd_limit: float,
    reference_mode: str,
) -> tuple[float, float] | None:
    if plot_df.empty or y_axis_mode == "全范围视图":
        return None

    reference_bounds: list[float] = []
    for reference_profile in _collect_reference_profiles(plot_df, reference_mode):
        mean_value = float(reference_profile["mean"])
        sd_value = reference_profile.get("sd")
        if sd_value is None or math.isclose(float(sd_value), 0.0, abs_tol=1e-12):
            reference_bounds.append(mean_value)
        else:
            reference_bounds.extend(
                [
                    mean_value - standard_sd_limit * float(sd_value),
                    mean_value + standard_sd_limit * float(sd_value),
                ]
            )

    if reference_bounds:
        lower = min(reference_bounds)
        upper = max(reference_bounds)
    else:
        raw_values = plot_df["raw_value"].dropna().astype(float).tolist()
        if not raw_values:
            return None
        lower = min(raw_values)
        upper = max(raw_values)

    padding = max((upper - lower) * 0.08, abs((upper + lower) / 2.0) * 0.01, 1e-6)
    return lower - padding, upper + padding


def _build_display_dataframe(plot_df: pd.DataFrame, y_limits: tuple[float, float] | None) -> pd.DataFrame:
    display_df = plot_df.copy()
    if display_df.empty:
        return display_df
    if y_limits is None:
        display_df["display_value"] = display_df["raw_value"]
        display_df["is_above_limit"] = False
        display_df["is_below_limit"] = False
        return display_df

    lower, upper = y_limits
    display_df["is_above_limit"] = display_df["raw_value"] > upper
    display_df["is_below_limit"] = display_df["raw_value"] < lower
    display_df["display_value"] = display_df["raw_value"].clip(lower=lower, upper=upper)
    return display_df


def _plot_out_of_range_markers(axis, display_df: pd.DataFrame, y_limits: tuple[float, float]) -> None:
    if display_df.empty:
        return

    lower, upper = y_limits
    above_df = display_df[display_df["is_above_limit"]]
    below_df = display_df[display_df["is_below_limit"]]

    if not above_df.empty:
        axis.scatter(
            above_df["run_index"],
            [upper] * len(above_df),
            marker="^",
            s=90,
            color="#c23b3d",
            zorder=5,
        )
        for _, point in above_df.iterrows():
            axis.annotate(
                f"{float(point['raw_value']):.2f}",
                xy=(point["run_index"], upper),
                xytext=(0, -18),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=8,
                color="#7a1f26",
            )

    if not below_df.empty:
        axis.scatter(
            below_df["run_index"],
            [lower] * len(below_df),
            marker="v",
            s=90,
            color="#c23b3d",
            zorder=5,
        )
        for _, point in below_df.iterrows():
            axis.annotate(
                f"{float(point['raw_value']):.2f}",
                xy=(point["run_index"], lower),
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
        note_df["run_index"],
        note_df["display_value"],
        s=118,
        marker="o",
        facecolors="none",
        edgecolors=MANUAL_NOTE_EDGE_COLOR,
        linewidths=1.35,
        zorder=6,
    )


def _add_manual_legends(
    axis,
    display_df: pd.DataFrame,
    level_ids: list[str] | None = None,
    show_reference_lines: bool = True,
    place_outside: bool = False,
) -> None:
    visible_phases = _collect_visible_phases(display_df)
    legend_style = {
        "frameon": True,
        "framealpha": 0.94,
        "borderpad": 0.7,
    }
    if place_outside:
        status_legend_loc = {
            "loc": "upper left",
            "bbox_to_anchor": (1.01, 1.00),
            "borderaxespad": 0.0,
            "handlelength": 1.2,
        }
        phase_legend_loc = {
            "loc": "upper left",
            "bbox_to_anchor": (1.01, 0.63),
            "borderaxespad": 0.0,
            "handlelength": 2.0,
        }
        level_legend_loc = {
            "loc": "upper left",
            "bbox_to_anchor": (1.01, 0.30),
            "borderaxespad": 0.0,
            "handlelength": 2.0,
        }
    else:
        status_legend_loc = {
            "loc": "upper left",
            "handlelength": 1.2,
        }
        phase_legend_loc = {
            "loc": "upper right",
            "handlelength": 2.0,
        }
        level_legend_loc = {
            "loc": "lower left",
            "handlelength": 2.0,
        }

    status_handles = _build_status_legend_handles(display_df, _has_out_of_range_points(display_df))
    if status_handles:
        status_legend = axis.legend(
            handles=status_handles,
            title="状态",
            **legend_style,
            **status_legend_loc,
        )
        axis.add_artist(status_legend)

    phase_handles = _build_phase_legend_handles(
        visible_phases=visible_phases,
        show_reference_lines=show_reference_lines,
    )
    if phase_handles:
        phase_legend = axis.legend(
            handles=phase_handles,
            title="阶段 / 样式",
            **legend_style,
            **phase_legend_loc,
        )
        axis.add_artist(phase_legend)

    resolved_level_ids = [level_id for level_id in (level_ids or []) if level_id]
    if len(resolved_level_ids) > 1:
        level_legend = axis.legend(
            handles=_build_level_legend_handles(resolved_level_ids),
            title="水平",
            **legend_style,
            **level_legend_loc,
            ncol=min(3, len(resolved_level_ids)),
        )
        axis.add_artist(level_legend)


def _build_status_legend_handles(display_df: pd.DataFrame, has_out_of_range_points: bool) -> list[Line2D]:
    if display_df.empty or "plot_status" not in display_df.columns:
        return []

    visible_statuses = set(
        str(status)
        for status in display_df["plot_status"].dropna().astype(str).tolist()
        if status in {"accept", "warning", "reject"}
    )
    if not visible_statuses:
        return []

    handles = [
        Line2D(
            [0],
            [0],
            marker=STATUS_POINT_STYLES["accept"]["marker"],
            color="none",
            markerfacecolor=STATUS_POINT_STYLES["accept"]["color"],
            markeredgecolor="#ffffff",
            markeredgewidth=0.9,
            markersize=8,
            linestyle="None",
            label="正常",
        ),
        Line2D(
            [0],
            [0],
            marker=STATUS_POINT_STYLES["warning"]["marker"],
            color="none",
            markerfacecolor=STATUS_POINT_STYLES["warning"]["color"],
            markeredgecolor="#ffffff",
            markeredgewidth=0.9,
            markersize=8,
            linestyle="None",
            label="警告",
        ),
        Line2D(
            [0],
            [0],
            marker=STATUS_POINT_STYLES["reject"]["marker"],
            color=STATUS_POINT_STYLES["reject"]["color"],
            markersize=8,
            linestyle="None",
            markeredgewidth=2.0,
            label="失控",
        ),
    ]

    if has_out_of_range_points:
        handles.append(
            Line2D(
                [0],
                [0],
                marker="^",
                color="#c23b3d",
                markersize=8,
                linestyle="None",
                label="超界裁切点",
            )
        )
    return handles


def _build_phase_legend_handles(
    *,
    visible_phases: list[str],
    show_reference_lines: bool,
) -> list[Line2D]:
    neutral_color = "#404040"
    handles: list[Line2D] = []
    if PHASE_TARGET_BUILDING in visible_phases:
        handles.append(
            Line2D(
                [0],
                [0],
                color=neutral_color,
                linestyle="--",
                linewidth=1.4,
                marker="s",
                markerfacecolor="#4e79a7",
                markeredgecolor="#ffffff",
                markeredgewidth=0.9,
                markersize=7,
                label="建靶期（虚线 / 方形点）",
            )
        )
    if PHASE_FORMAL_QC in visible_phases:
        handles.append(
            Line2D(
                [0],
                [0],
                color=neutral_color,
                linestyle="-",
                linewidth=1.4,
                marker="o",
                markerfacecolor="#808080",
                markeredgecolor="#ffffff",
                markeredgewidth=0.9,
                markersize=7,
                label="正式期（实线 / 圆形点）",
            )
        )
    if show_reference_lines:
        handles.append(
            Line2D(
                [0],
                [0],
                color=REFERENCE_LINE_COLORS[0],
                linestyle="-",
                linewidth=1.2,
                label="均值 / ±SD 控制线",
            )
        )
    handles.append(
        Line2D(
            [0],
            [0],
            marker="o",
            markerfacecolor="none",
            markeredgecolor=MANUAL_NOTE_EDGE_COLOR,
            markeredgewidth=1.35,
            markersize=8,
            linestyle="None",
            label="描边点=含手动备注",
        )
    )
    return handles


def _collect_visible_phases(display_df: pd.DataFrame) -> list[str]:
    if display_df.empty:
        return []
    phase_column = "plot_phase" if "plot_phase" in display_df.columns else "phase"
    if phase_column not in display_df.columns:
        return []
    visible_phases: list[str] = []
    for phase in display_df[phase_column].dropna().astype(str).tolist():
        if phase in {PHASE_TARGET_BUILDING, PHASE_FORMAL_QC} and phase not in visible_phases:
            visible_phases.append(phase)
    return visible_phases


def _build_level_legend_handles(level_ids: list[str]) -> list[Line2D]:
    handles: list[Line2D] = []
    for level_id in level_ids:
        handles.append(
            Line2D(
                [0],
                [0],
                color=LEVEL_COLORS.get(level_id, "#4e79a7"),
                linestyle="-",
                linewidth=1.6,
                marker="o",
                markerfacecolor=LEVEL_COLORS.get(level_id, "#4e79a7"),
                markeredgecolor="#ffffff",
                markeredgewidth=0.8,
                markersize=6,
                label=format_level_id_display(level_id),
            )
        )
    return handles


def _has_out_of_range_points(display_df: pd.DataFrame) -> bool:
    if display_df.empty:
        return False
    if "is_above_limit" not in display_df.columns or "is_below_limit" not in display_df.columns:
        return False
    return bool(display_df["is_above_limit"].any() or display_df["is_below_limit"].any())


def _can_plot_zscore_frame(plot_df: pd.DataFrame | None) -> bool:
    required_columns = {"level_id", "run_index", "raw_value"}
    return plot_df is not None and not plot_df.empty and required_columns.issubset(plot_df.columns)


def _build_empty_zscore_figure(title: str, message: str, subtitle: str = ""):
    figure, axis = plt.subplots(figsize=ZSCORE_SINGLE_FIGSIZE, dpi=ZSCORE_DPI)
    axis.set_title(title, pad=8, fontsize=12)
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
    _apply_zscore_chart_layout(figure, right=0.94)
    return figure


def _normalize_phase_scope(phase_scope: str) -> str:
    if phase_scope in {"building", "formal", "all"}:
        return phase_scope
    return "all"


def _normalize_y_axis_mode(y_axis_mode: str) -> str:
    return "全范围视图" if y_axis_mode == "全范围视图" else "标准视图"


def _resolve_reference_mode(plot_df: pd.DataFrame, phase_scope: str) -> str:
    normalized_scope = _normalize_phase_scope(phase_scope)
    if normalized_scope == "building":
        return "none"
    if normalized_scope == "formal":
        return "formal_full"
    visible_phases = set(
        str(phase)
        for phase in (
            plot_df["plot_phase"] if "plot_phase" in plot_df.columns else plot_df.get("phase", pd.Series(dtype=str))
        ).dropna().astype(str).tolist()
    )
    return "formal_full" if PHASE_FORMAL_QC in visible_phases else "none"


def _resolve_reference_profile(plot_df: pd.DataFrame, reference_mode: str) -> dict[str, float | None] | None:
    if plot_df.empty or reference_mode == "none":
        return None
    if reference_mode == "formal_full":
        mean_series = plot_df["formal_reference_mean"].dropna() if "formal_reference_mean" in plot_df.columns else pd.Series(dtype=float)
        if mean_series.empty:
            return None
        sd_series = plot_df["formal_reference_sd"].dropna() if "formal_reference_sd" in plot_df.columns else pd.Series(dtype=float)
        return {
            "mean": float(mean_series.iloc[-1]),
            "sd": None if sd_series.empty else float(sd_series.iloc[-1]),
        }
    raise ValueError(f"Unsupported reference mode: {reference_mode}")


def _collect_reference_profiles(plot_df: pd.DataFrame, reference_mode: str) -> list[dict[str, float | None]]:
    if plot_df.empty or reference_mode == "none":
        return []
    if "level_id" not in plot_df.columns:
        reference_profile = _resolve_reference_profile(plot_df, reference_mode)
        return [] if reference_profile is None else [reference_profile]

    profiles: list[dict[str, float | None]] = []
    for _, level_df in plot_df.groupby("level_id", sort=False):
        reference_profile = _resolve_reference_profile(level_df, reference_mode)
        if reference_profile is not None:
            profiles.append(reference_profile)
    return profiles


def _resolve_plot_phase(plot_df: pd.DataFrame) -> str:
    if plot_df.empty:
        return PHASE_FORMAL_QC
    return str(plot_df["plot_phase"].iloc[0] if "plot_phase" in plot_df.columns else plot_df["phase"].iloc[0])


def _phase_linestyle(phase: str) -> str:
    return "--" if phase == PHASE_TARGET_BUILDING else "-"


def _phase_label(phase: str) -> str:
    return "建靶期" if phase == PHASE_TARGET_BUILDING else "正式期"
