"""CV calculations and requirement validation shared by setup and QC views."""
from __future__ import annotations

import math


def calculate_cv_percent(mean, sd):
    """Return SD/mean as percent; nonpositive means have no usable CV."""
    if mean is None or sd is None:
        return None
    try:
        mean, sd = float(mean), float(sd)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mean) or not math.isfinite(sd) or mean <= 1e-12 or sd < 0:
        return None
    value = sd / mean * 100
    return value if math.isfinite(value) else None


def normalize_cv_limit(value):
    if value is None or value == '':
        return None
    try:
        limit = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('CV 要求须为大于 0 的有限数值，或留空。') from exc
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError('CV 要求须为大于 0 的有限数值，或留空。')
    return limit
