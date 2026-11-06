"""Display names for system-generated text; never rewrite saved evidence or user notes."""
from __future__ import annotations

from copy import deepcopy
import math
import re

LJ_ENTRY_LABEL = "单水平（L-J）"
LJ_METHOD_LABEL = "单水平（L-J法）"


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


_OLD_SD_REQUIREMENT = (
    "按本批次确认的均值和标准差，使用所选质控方法的现有规则判断结果；"
    "L-J及多水平按SD位置和连续规则判读，即时法使用格拉布斯检验。"
    "长期精密度需按适用标准或实验室要求另行评价。"
)
_SD_REQUIREMENT_DISPLAY = (
    "L-J及多水平法按本批次确认的均值、标准差和所选质控规则判读；"
    "即时法采用格拉布斯检验。"
)
_OLD_TEACHING_REQUIREMENT = (
    "采用完整水平结果、20个有效建靶点和当前质控规则；"
    "Ct/Log用于数值信号稳定性监测，不自动判定病原体阴阳性；"
    "均值和SD按本批次参数管理。"
)
_TEACHING_REQUIREMENT_DISPLAY = (
    "各水平累计20个有效建靶点后确定均值和SD；"
    "正式期按本批次参数及已启用质控规则判读。"
)
_PROCESS_REQUIREMENT_DISPLAY = (
    "每次分析前核对质控品及试剂批号、有效期和仪器状态；"
    "异常时登记原因、纠正措施及复测记录。"
)
_OLD_PROCESS_REQUIREMENT = _PROCESS_REQUIREMENT_DISPLAY + "此文本为本次实验室要求。"
_HIV_PROCESS_REQUIREMENT = (
    "HIV-1 RNA定量先将copies/mL结果换算为Log10后录入；"
    "内外部质控随样本经历提取与扩增全过程，确认失控后先恢复在控再重新检测相应样本。"
)
_QUALITY_TEXT_DISPLAYS = {
    _OLD_SD_REQUIREMENT: _SD_REQUIREMENT_DISPLAY,
    _OLD_TEACHING_REQUIREMENT: _TEACHING_REQUIREMENT_DISPLAY,
    _OLD_PROCESS_REQUIREMENT: _PROCESS_REQUIREMENT_DISPLAY,
    _OLD_PROCESS_REQUIREMENT + _HIV_PROCESS_REQUIREMENT:
        _PROCESS_REQUIREMENT_DISPLAY + _HIV_PROCESS_REQUIREMENT,
    "依据本软件已核查目录匹配项目、单位及尺度；":
        "按所选方法、单位及结果尺度核对质量要求。",
    ("已在软件所附已核查目录中核对；当前输入尺度没有直接适用的自动数值限值，"
     "过程条款按适用情况参考，不宣称完成临床标准检索。"):
        "收录标准中未找到此结果尺度的数值限值；采用实验室质控方案，并核对适用的对照要求。",
    ("已核对当前软件收录的项目、方法、尺度和单位；未匹配适用数值限值。"
     "此记录不表示已查询全部国家标准。"):
        "已核对当前目录中的项目、方法、结果尺度和单位；未匹配适用数值限值。",
}
_OLD_CV_SUFFIX = re.compile(
    r"(?P<base>.+) (?P<sentence>实验室自定义CV上限为≤"
    r"(?P<limit>[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)"
    r"%，另行比较正式期在控结果的精密度，不替代SD失控判读。)"
)
_SYSTEM_REQUIREMENT_DISPLAYS = {
    _OLD_SD_REQUIREMENT: _SD_REQUIREMENT_DISPLAY,
    _SD_REQUIREMENT_DISPLAY: _SD_REQUIREMENT_DISPLAY,
    _OLD_TEACHING_REQUIREMENT: _TEACHING_REQUIREMENT_DISPLAY,
    _TEACHING_REQUIREMENT_DISPLAY: _TEACHING_REQUIREMENT_DISPLAY,
}


def display_quality_text(value):
    """Shorten only complete, known generated quality templates for display.

    User wording, partial matches and saved JSON/fingerprints stay untouched.
    The parameterized CV sentence is recognized only after a known generated
    requirement, never after laboratory-written content.
    """
    if not isinstance(value, str):
        return value
    if value in _QUALITY_TEXT_DISPLAYS:
        return _QUALITY_TEXT_DISPLAYS[value]
    match = _OLD_CV_SUFFIX.fullmatch(value)
    if match and match['base'] in _SYSTEM_REQUIREMENT_DISPLAYS:
        limit = float(match['limit'])
        if math.isfinite(limit) and limit > 0:
            return (_SYSTEM_REQUIREMENT_DISPLAYS[match['base']] +
                    f" 实验室CV上限：≤{match['limit']}%；比较正式期在控结果的实测CV。")
    return value


def display_quality_record(value):
    """Return a presentation copy; alter only the three quality prose fields."""
    if isinstance(value, dict):
        result = {}
        for key, field in value.items():
            copied = display_quality_record(field)
            result[key] = (display_quality_text(copied)
                           if key in {'requirement_text', 'evidence', 'rationale'} else copied)
        return result
    if isinstance(value, list):
        return [display_quality_record(field) for field in value]
    return deepcopy(value)
