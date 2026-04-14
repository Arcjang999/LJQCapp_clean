from __future__ import annotations

from dataclasses import dataclass

from database import get_app_settings, save_app_settings


DEFAULT_REPORT_STATEMENT = (
    "本报告由软件根据所选月份内的质控数据自动生成，仅作为室内质控归档与复核参考。"
    "报告结果应结合原始记录、异常备注和实验室实际处理情况共同判读。"
)
REPORT_SETTINGS_KEYS = (
    "lab_name",
    "department_name",
    "qc_owner_name",
    "reviewer_name",
    "report_statement",
)
REPORT_SETTINGS_FALLBACKS = {
    "lab_name": "未填写",
    "department_name": "未填写",
    "qc_owner_name": "未填写",
    "reviewer_name": "未填写",
    "report_statement": DEFAULT_REPORT_STATEMENT,
}


@dataclass(frozen=True)
class ReportSettings:
    lab_name: str = ""
    department_name: str = ""
    qc_owner_name: str = ""
    reviewer_name: str = ""
    report_statement: str = ""

    def to_payload(self) -> dict[str, str]:
        return {
            "lab_name": _normalize_setting_text(self.lab_name),
            "department_name": _normalize_setting_text(self.department_name),
            "qc_owner_name": _normalize_setting_text(self.qc_owner_name),
            "reviewer_name": _normalize_setting_text(self.reviewer_name),
            "report_statement": _normalize_setting_text(self.report_statement),
        }


def get_report_settings() -> ReportSettings:
    stored = get_app_settings(REPORT_SETTINGS_KEYS)
    return ReportSettings(
        lab_name=_normalize_setting_text(stored.get("lab_name")),
        department_name=_normalize_setting_text(stored.get("department_name")),
        qc_owner_name=_normalize_setting_text(stored.get("qc_owner_name")),
        reviewer_name=_normalize_setting_text(stored.get("reviewer_name")),
        report_statement=_normalize_setting_text(stored.get("report_statement")),
    )


def get_report_settings_with_fallbacks() -> ReportSettings:
    current = get_report_settings()
    payload = current.to_payload()
    return ReportSettings(
        lab_name=payload["lab_name"] or REPORT_SETTINGS_FALLBACKS["lab_name"],
        department_name=payload["department_name"] or REPORT_SETTINGS_FALLBACKS["department_name"],
        qc_owner_name=payload["qc_owner_name"] or REPORT_SETTINGS_FALLBACKS["qc_owner_name"],
        reviewer_name=payload["reviewer_name"] or REPORT_SETTINGS_FALLBACKS["reviewer_name"],
        report_statement=payload["report_statement"] or REPORT_SETTINGS_FALLBACKS["report_statement"],
    )


def save_report_settings_form(values: dict[str, object]) -> ReportSettings:
    settings = ReportSettings(
        lab_name=str(values.get("lab_name", "") or ""),
        department_name=str(values.get("department_name", "") or ""),
        qc_owner_name=str(values.get("qc_owner_name", "") or ""),
        reviewer_name=str(values.get("reviewer_name", "") or ""),
        report_statement=str(values.get("report_statement", "") or ""),
    )
    save_app_settings(settings.to_payload())
    return get_report_settings()


def _normalize_setting_text(value: object) -> str:
    return str(value or "").strip()
