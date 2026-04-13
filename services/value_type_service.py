from __future__ import annotations

import math
from typing import Any


DEFAULT_INPUT_VALUE_TYPE = "raw"
INPUT_VALUE_TYPE_LABELS = {
    "raw": "真实检测值",
    "ct": "Ct值",
    "log": "log值",
}
INPUT_VALUE_TYPE_OPTIONS = list(INPUT_VALUE_TYPE_LABELS.keys())
INPUT_VALUE_TYPE_ALIASES = {
    "raw": "raw",
    "真实检测值": "raw",
    "检测值": "raw",
    "ct": "ct",
    "ct值": "ct",
    "log": "log",
    "log值": "log",
}


def normalize_input_value_type(value: Any, *, fallback: str = DEFAULT_INPUT_VALUE_TYPE) -> str:
    normalized = str(value or "").strip().lower()
    return INPUT_VALUE_TYPE_ALIASES.get(normalized, fallback)


def get_input_value_type_label(value: Any) -> str:
    return INPUT_VALUE_TYPE_LABELS[normalize_input_value_type(value)]


def get_measurement_label(value: Any) -> str:
    return get_input_value_type_label(value)


def build_level_measurement_label(level_label: str, value: Any) -> str:
    return f"{str(level_label or '').strip()} {get_measurement_label(value)}".strip()


def should_show_auxiliary_log_column(value: Any) -> bool:
    return normalize_input_value_type(value) == "raw"


def compute_legacy_log_value(numeric_value: float, input_value_type: Any) -> float | None:
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    if not math.isfinite(numeric_value):
        return None
    if normalized_input_value_type == "raw":
        if numeric_value <= 0:
            return None
        return math.log10(numeric_value)
    if normalized_input_value_type == "log":
        return float(numeric_value)
    return None


def validate_project_numeric_value(
    numeric_value: Any,
    input_value_type: Any,
    *,
    field_label: str | None = None,
) -> str | None:
    label = str(field_label or get_measurement_label(input_value_type)).strip()
    try:
        resolved_numeric = float(numeric_value)
    except (TypeError, ValueError):
        resolved_numeric = math.nan

    if not math.isfinite(resolved_numeric):
        if normalize_input_value_type(input_value_type) == "raw":
            return f"{label}必须为有效正数。"
        return f"{label}必须为有效数字。"
    if normalize_input_value_type(input_value_type) == "raw" and resolved_numeric <= 0:
        return f"{label}必须为有效正数。"
    return None


def parse_project_input_value(
    raw_value: str | None,
    input_value_type: Any,
    *,
    field_label: str | None = None,
) -> tuple[float | None, float | None, str | None]:
    label = str(field_label or get_measurement_label(input_value_type)).strip()
    text = str(raw_value or "").strip()
    if not text:
        return None, None, f"{label}不能为空。"

    try:
        numeric_value = float(text)
    except ValueError:
        return None, None, validate_project_numeric_value(math.nan, input_value_type, field_label=label)

    validation_error = validate_project_numeric_value(
        numeric_value,
        input_value_type,
        field_label=label,
    )
    if validation_error is not None:
        return None, None, validation_error
    return (
        float(numeric_value),
        compute_legacy_log_value(float(numeric_value), input_value_type),
        None,
    )
