"""Display names for system-generated text; never rewrite saved evidence or user notes."""
from __future__ import annotations


QC_USAGE_LABELS = {
    "parallel": "新旧批同时使用",
    "active": "正式使用",
    "ended": "停止使用",
    "pending": "尚未到开始使用时间",
}


def display_generated_text(value):
    """Translate legacy calculation labels only at a presentation boundary."""
    if not isinstance(value, str):
        return value
    for old, new in (
        ("建靶期", "参数建立期"), ("建靶数据", "参数建立数据"),
        ("建靶点", "参数建立点"), ("建靶统计", "参数建立统计"),
        ("建靶图", "参数建立图"), ("参与建靶", "参与参数建立"),
        ("建靶", "均值和标准差建立"),
    ):
        value = value.replace(old, new)
    return value
