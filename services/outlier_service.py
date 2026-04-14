from __future__ import annotations

import math
from functools import lru_cache
from statistics import NormalDist
from typing import Sequence


DEFAULT_GRUBBS_ALPHA = 0.05
GRUBBS_METHOD_NAME = "Grubbs"
GRUBBS_METHOD_LABEL = "双侧单异常值 Grubbs 检验"
GRUBBS_FORMULA_TEXT = "G = max(|xi - x̄|) / s"
MIN_GRUBBS_SAMPLE_SIZE = 3

OUTLIER_STATUS_NORMAL = "normal"
OUTLIER_STATUS_SUSPECT = "outlier_suspect"
OUTLIER_STATUS_KEPT = "kept"
OUTLIER_STATUS_DISABLED = "disabled"
OUTLIER_STATUS_RESTORED = "restored"

OUTLIER_MANUAL_STATUS_NORMAL = "normal"
OUTLIER_MANUAL_STATUS_PENDING_REVIEW = "pending_review"
OUTLIER_MANUAL_STATUS_KEEP = "keep"
OUTLIER_MANUAL_STATUS_DISABLED = "disabled"
OUTLIER_MANUAL_STATUS_RESTORED = "restored"

OUTLIER_STATUS_LABELS = {
    OUTLIER_STATUS_NORMAL: "姝ｅ父",
    OUTLIER_STATUS_SUSPECT: "鐤戜技绂荤兢",
    OUTLIER_STATUS_KEPT: "宸蹭繚鐣?",
    OUTLIER_STATUS_DISABLED: "宸茬鐢?",
    OUTLIER_STATUS_RESTORED: "宸叉仮澶?",
}

OUTLIER_MANUAL_STATUS_LABELS = {
    OUTLIER_MANUAL_STATUS_NORMAL: "鏈鐞?",
    OUTLIER_MANUAL_STATUS_PENDING_REVIEW: "寰呭鐞?",
    OUTLIER_MANUAL_STATUS_KEEP: "淇濈暀",
    OUTLIER_MANUAL_STATUS_DISABLED: "绂佺敤",
    OUTLIER_MANUAL_STATUS_RESTORED: "鎭㈠",
}


SAFE_OUTLIER_STATUS_LABELS = {
    OUTLIER_STATUS_NORMAL: "正常",
    OUTLIER_STATUS_SUSPECT: "疑似离群",
    OUTLIER_STATUS_KEPT: "已保留",
    OUTLIER_STATUS_DISABLED: "已禁用",
    OUTLIER_STATUS_RESTORED: "已恢复",
}

SAFE_OUTLIER_MANUAL_STATUS_LABELS = {
    OUTLIER_MANUAL_STATUS_NORMAL: "未处理",
    OUTLIER_MANUAL_STATUS_PENDING_REVIEW: "待处理",
    OUTLIER_MANUAL_STATUS_KEEP: "保留",
    OUTLIER_MANUAL_STATUS_DISABLED: "禁用",
    OUTLIER_MANUAL_STATUS_RESTORED: "恢复",
}


def _build_outlier_alias_map(
    safe_labels: dict[str, str],
    legacy_labels: dict[str, str],
) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for code, label in safe_labels.items():
        alias_map[code] = code
        alias_map[str(label).strip().lower()] = code
    for code, label in legacy_labels.items():
        alias_map[str(label).strip().lower()] = code
    return alias_map


OUTLIER_STATUS_ALIASES = _build_outlier_alias_map(SAFE_OUTLIER_STATUS_LABELS, OUTLIER_STATUS_LABELS)
OUTLIER_MANUAL_STATUS_ALIASES = _build_outlier_alias_map(
    SAFE_OUTLIER_MANUAL_STATUS_LABELS,
    OUTLIER_MANUAL_STATUS_LABELS,
)


def _simpson_integral(func, start: float, end: float) -> float:
    midpoint = (start + end) / 2.0
    interval = end - start
    return interval * (func(start) + 4.0 * func(midpoint) + func(end)) / 6.0


def _adaptive_simpson(
    func,
    start: float,
    end: float,
    tolerance: float,
    max_depth: int = 20,
) -> float:
    whole = _simpson_integral(func, start, end)
    return _adaptive_simpson_recursive(func, start, end, tolerance, whole, max_depth)


def _adaptive_simpson_recursive(
    func,
    start: float,
    end: float,
    tolerance: float,
    whole: float,
    depth: int,
) -> float:
    midpoint = (start + end) / 2.0
    left = _simpson_integral(func, start, midpoint)
    right = _simpson_integral(func, midpoint, end)
    correction = left + right - whole
    if depth <= 0 or abs(correction) <= 15.0 * tolerance:
        return left + right + correction / 15.0
    return _adaptive_simpson_recursive(
        func,
        start,
        midpoint,
        tolerance / 2.0,
        left,
        depth - 1,
    ) + _adaptive_simpson_recursive(
        func,
        midpoint,
        end,
        tolerance / 2.0,
        right,
        depth - 1,
    )


def _student_t_pdf(x_value: float, degrees_of_freedom: int) -> float:
    degrees = float(degrees_of_freedom)
    numerator = math.gamma((degrees + 1.0) / 2.0)
    denominator = math.sqrt(degrees * math.pi) * math.gamma(degrees / 2.0)
    return numerator / denominator * (1.0 + (x_value * x_value) / degrees) ** (-(degrees + 1.0) / 2.0)


@lru_cache(maxsize=256)
def student_t_cdf(x_value: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("自由度必须大于 0。")
    if math.isclose(x_value, 0.0, abs_tol=1e-12):
        return 0.5

    if degrees_of_freedom == 1:
        return 0.5 + math.atan(x_value) / math.pi
    if degrees_of_freedom == 2:
        return 0.5 + x_value / (2.0 * math.sqrt(2.0 + x_value * x_value))

    sign = 1.0 if x_value > 0 else -1.0
    upper = abs(float(x_value))
    integral = _adaptive_simpson(
        lambda current_x: _student_t_pdf(current_x, degrees_of_freedom),
        0.0,
        upper,
        tolerance=1e-8,
        max_depth=24,
    )
    return 0.5 + sign * integral


@lru_cache(maxsize=256)
def student_t_ppf(probability: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("自由度必须大于 0。")
    if not 0.0 < probability < 1.0:
        raise ValueError("概率必须位于 0 和 1 之间。")
    if math.isclose(probability, 0.5, abs_tol=1e-12):
        return 0.0
    if probability < 0.5:
        return -student_t_ppf(1.0 - probability, degrees_of_freedom)
    if degrees_of_freedom == 1:
        return math.tan(math.pi * (probability - 0.5))

    lower = 0.0
    normal_seed = NormalDist().inv_cdf(probability)
    upper = max(1.0, float(normal_seed))
    while student_t_cdf(upper, degrees_of_freedom) < probability:
        upper *= 2.0
        if upper > 1e6:
            break

    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        midpoint_probability = student_t_cdf(midpoint, degrees_of_freedom)
        if midpoint_probability < probability:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


@lru_cache(maxsize=128)
def grubbs_critical_value(
    sample_size: int,
    alpha: float = DEFAULT_GRUBBS_ALPHA,
) -> float | None:
    n = int(sample_size)
    if n < MIN_GRUBBS_SAMPLE_SIZE:
        return None
    t_probability = 1.0 - float(alpha) / (2.0 * n)
    t_value = student_t_ppf(t_probability, n - 2)
    numerator = (n - 1.0) * abs(t_value)
    denominator = math.sqrt(n) * math.sqrt(n - 2.0 + t_value * t_value)
    return numerator / denominator


def calculate_grubbs_test(
    values: Sequence[float],
    *,
    alpha: float = DEFAULT_GRUBBS_ALPHA,
) -> dict[str, float | int | bool | str | None]:
    cleaned_values = [float(value) for value in values if math.isfinite(float(value))]
    sample_size = len(cleaned_values)
    result: dict[str, float | int | bool | str | None] = {
        "sample_size": sample_size,
        "alpha": float(alpha),
        "mean": None,
        "sd": None,
        "statistic": None,
        "threshold": None,
        "suspected_index": None,
        "suspected_value": None,
        "is_suspect": False,
        "evaluation_ready": sample_size >= MIN_GRUBBS_SAMPLE_SIZE,
        "reason": "insufficient_points" if sample_size < MIN_GRUBBS_SAMPLE_SIZE else "ok",
    }
    if sample_size == 0:
        return result

    mean_value = sum(cleaned_values) / sample_size
    result["mean"] = mean_value
    if sample_size < MIN_GRUBBS_SAMPLE_SIZE:
        return result

    variance = sum((value - mean_value) ** 2 for value in cleaned_values) / (sample_size - 1)
    sd_value = math.sqrt(max(variance, 0.0))
    result["sd"] = sd_value
    threshold = grubbs_critical_value(sample_size, alpha)
    result["threshold"] = threshold
    if math.isclose(sd_value, 0.0, abs_tol=1e-12):
        result["statistic"] = 0.0
        result["evaluation_ready"] = False
        result["reason"] = "zero_variation"
        return result

    deviations = [abs(value - mean_value) for value in cleaned_values]
    suspected_index = max(range(sample_size), key=lambda index: deviations[index])
    statistic = deviations[suspected_index] / sd_value
    result["statistic"] = statistic
    result["suspected_index"] = suspected_index
    result["suspected_value"] = cleaned_values[suspected_index]
    result["is_suspect"] = bool(threshold is not None and statistic > threshold)
    return result


def normalize_outlier_status(
    status: object,
    *,
    fallback: str = OUTLIER_STATUS_NORMAL,
) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in OUTLIER_STATUS_ALIASES:
        return OUTLIER_STATUS_ALIASES[normalized_status]
    return fallback


def normalize_outlier_manual_status(
    status: object,
    *,
    fallback: str = OUTLIER_MANUAL_STATUS_NORMAL,
) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in OUTLIER_MANUAL_STATUS_ALIASES:
        return OUTLIER_MANUAL_STATUS_ALIASES[normalized_status]
    return fallback


def get_outlier_status_label(status: object) -> str:
    normalized_status = normalize_outlier_status(status)
    return SAFE_OUTLIER_STATUS_LABELS.get(normalized_status, "正常")


def get_outlier_manual_status_label(status: object) -> str:
    normalized_status = normalize_outlier_manual_status(status)
    return SAFE_OUTLIER_MANUAL_STATUS_LABELS.get(normalized_status, "未处理")


def derive_outlier_status(
    *,
    is_building_included: bool,
    is_suspect: bool,
    manual_status: object,
) -> str:
    normalized_manual_status = normalize_outlier_manual_status(manual_status)
    if not is_building_included or normalized_manual_status == OUTLIER_MANUAL_STATUS_DISABLED:
        return OUTLIER_STATUS_DISABLED
    if normalized_manual_status == OUTLIER_MANUAL_STATUS_RESTORED:
        return OUTLIER_STATUS_RESTORED
    if normalized_manual_status == OUTLIER_MANUAL_STATUS_KEEP:
        return OUTLIER_STATUS_KEPT
    if is_suspect:
        return OUTLIER_STATUS_SUSPECT
    return OUTLIER_STATUS_NORMAL
