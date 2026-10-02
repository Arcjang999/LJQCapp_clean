"""Frozen handling reports; no source-result or calculation writes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from typing import Any
from uuid import uuid4

from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from database import atomic_write, get_connection
from services import report_pdf_layout as layout
from services.settings_service import get_report_settings_with_fallbacks
from services.settings_service import DEFAULT_REPORT_STATEMENT
from services.value_type_service import get_input_value_type_label


REPORT_TYPE_EVENT = "out_of_control_report"
EVENT_REPORT_LABEL = "失控处理报告"
STATUS_LABELS = {
    "pending": "待处理", "in_progress": "处理中",
    "pending_confirmation": "待确认", "completed": "已完成",
}
CLASSIFICATION_LABELS = {"reject": "失控", "warning": "警告", "accept": "在控", "pending": "待判读"}
METHOD_LABELS = {"lj": "单水平（LJ法）", "zscore": "多水平（Z-score法）"}
MISSING = "未记录"


def _text(value: Any) -> str:
    if value is None or value == "":
        return MISSING
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, tuple)):
        return "、".join(_text(item) for item in value) or MISSING
    return str(value)


def _mapping(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _section(title: str, paragraphs: list[str]) -> Any:
    # Shared paginator handles paragraph boundaries; bound oversized user text
    # before handing it off so one field can safely span arbitrarily many pages.
    chunks = []
    for paragraph in paragraphs:
        lines = layout._wrap_text(str(paragraph), 46).splitlines() or [MISSING]
        chunks.extend("\n".join(lines[i:i + 16]) for i in range(0, len(lines), 16))
    return layout._TextSectionSpec(title, chunks, width=46)


def _level_paragraphs(snapshot: dict) -> list[str]:
    result = []
    value_type = snapshot.get("input_value_type")
    for index, level in enumerate(snapshot.get("levels") or [], 1):
        value = level.get("log_value") if value_type == "log" else level.get("value")
        label = level.get("level_name") or level.get("level_id") or f"水平{index}"
        result.append(
            f"{label}：检测值 {_text(value)}；单位 {_text(snapshot.get('unit_symbol'))}；"
            f"结论 {CLASSIFICATION_LABELS.get(level.get('classification'), _text(level.get('classification')))}；"
            f"触发规则 {_text(level.get('rule_names'))}；实际质控批号 {_text(level.get('lot_no'))}；"
            f"采用均值 {_text(level.get('target_mean'))}；采用标准差 {_text(level.get('target_sd'))}。"
        )
    return result or ["各水平检测证据：未记录。"]


def _quality_paragraphs(snapshot: dict) -> list[str]:
    quality = _mapping(snapshot.get("quality_snapshot"))
    config = _mapping(snapshot.get("config_snapshot"))
    goal = _mapping(quality.get("goal") or quality.get("quality_goal_json") or config.get("quality_goal_json"))
    review = _mapping(quality.get("review") or quality.get("quality_review_json") or config.get("quality_review_json"))
    paragraphs = []
    spec = _mapping(goal.get("spec"))
    if spec:
        paragraphs.append(
            f"采用要求：{_text(spec.get('name'))}；依据 {_text(spec.get('standard'))}；"
            f"版本 {_text(spec.get('version'))}；条款 {_text(spec.get('source_clause'))}；"
            f"页码 {_text(spec.get('source_page'))}。"
        )
    if goal:
        from services.quality_target_service import requirement_text
        paragraphs.append(f"采用依据：{_text(goal.get('evidence'))}；确认人 {_text(goal.get('confirmed_by'))}；采用时间 {_text(goal.get('adopted_at'))}。")
        for item in goal.get("levels", []) if isinstance(goal.get("levels"), list) else []:
            rule = _mapping(item.get("rule"))
            requirement = requirement_text(rule) if all(k in rule for k in ("kind", "operator", "value", "unit")) else _text(item.get("requirement"))
            paragraphs.append(f"采用水平：{_text(item.get('level_order') or item.get('level_id') or item.get('level'))}；要求 {requirement}；浓度 {_text(item.get('concentration'))} {_text(goal.get('unit'))}；类别 {_text(item.get('category'))}。")
        supplement = _mapping(goal.get("supplement"))
        if supplement:
            paragraphs.append(f"实验室补充要求：CV ≤{_text(supplement.get('cv'))}%；依据 {_text(supplement.get('evidence'))}。")
    for item in review.get("candidates", []):
        if item.get("disposition") == "adopted":
            paragraphs.append(f"采用依据：{_text(item.get('source'))}；页码 {_text(item.get('pages'))}；要求 {_text(item.get('requirements'))}。")
    for title, entries in layout._process_requirement_report_sections(review):
        paragraphs.extend(f"{title}：{entry}" for entry in entries)
    for item in review.get("registered_standards", []):
        paragraphs.append(f"登记依据：{_text(item.get('standard'))}；版本 {_text(item.get('version'))}；条款 {_text(item.get('source_clause'))}；要求 {_text(item.get('requirement_text'))}。")
    recorded = _mapping(review.get("recorded"))
    if recorded:
        paragraphs.append(f"补充依据：{_text(recorded.get('source_name'))}；版本 {_text(recorded.get('source_version'))}；要求 {_text(recorded.get('requirement_text'))}。")
    if review:
        paragraphs.append(f"适用依据：{_text(review.get('evidence'))}；确认人 {_text(review.get('confirmed_by'))}；确认时间 {_text(review.get('reviewed_at'))}。")
    # Some historical snapshots expose a simple already-labelled requirement.
    for key, label in (("requirement_text", "采用要求"), ("source_name", "依据"), ("source_version", "版本")):
        if quality.get(key):
            paragraphs.append(f"{label}：{quality[key]}")
    return paragraphs or ["原检测采用的质量要求及来源：未记录；未用当前目录补填。"]


def build_event_report_package(event_id: int, revision_no: int | None = None) -> dict:
    from services.out_of_control_service import get_event
    from services.out_of_control_attachment_service import list_attachments
    event = deepcopy(get_event(int(event_id), revision_no=revision_no))
    revision = int(event["revision_no"])
    uid = uuid4().hex
    settings = get_report_settings_with_fallbacks()
    return {
        "report_type": REPORT_TYPE_EVENT, "report_uid": uid,
        "report_no": f"OOC-{int(event_id):06d}-V{revision}-{uid[:8].upper()}",
        "event_id": int(event_id), "revision_no": revision,
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "file_name": f"失控处理报告_{int(event_id)}_V{revision}_{uid[:8]}.pdf",
        "event": event,
        "lab_info": asdict(settings),
        "declaration": ("本报告用于室内质控事件处理的归档与复核，应结合原检测记录、复测证据及实验室调查综合评估。"
                        if settings.report_statement == DEFAULT_REPORT_STATEMENT else settings.report_statement),
        "attachments": deepcopy(list_attachments(int(event_id), revision_no=revision)),
    }


def render_event_report_pdf(package: dict) -> bytes:
    from services.report_service import _resolve_pdf_font_name
    event = package["event"]
    origin = event["origin_snapshot"]
    content = event.get("content") or {}
    lab = package.get("lab_info") or {}
    status = STATUS_LABELS.get(event.get("status"), MISSING)
    classification = CLASSIFICATION_LABELS.get(event.get("original_classification"), _text(event.get("original_classification")))
    draft = "；尚未完成处理" if event.get("status") != "completed" else ""
    target = _mapping(origin.get("target_profile"))
    context = _mapping(origin.get("context"))
    sections = [
        _section("报告与原检测", [
            f"报告编号：{package['report_no']}；处理版本：{package['revision_no']}；处理状态：{status}{draft}。",
            f"实验室：{_text(lab.get('lab_name'))}；科室：{_text(lab.get('department_name'))}。",
            f"项目：{_text(origin.get('project_name'))}；检验项目：{_text(origin.get('test_item_name'))}；仪器：{_text(origin.get('instrument_name'))}。",
            f"检测时间：{_text(origin.get('test_time'))}；质控方法：{METHOD_LABELS.get(origin.get('qc_method'), MISSING)}；方法学：{_text(origin.get('method_name'))}。",
            f"输入值类型：{get_input_value_type_label(origin['input_value_type']) if origin.get('input_value_type') else MISSING}；计量单位：{_text(origin.get('unit_symbol'))}。",
            f"原检测结论：{classification}；触发规则：{_text(origin.get('rule_names'))}。处理状态不改变原检测结论。",
            f"实际试剂批号：{_text(context.get('reagent_lot_no') or context.get('actual_reagent_lot_no'))}；参数版本：{_text(target.get('version_no', target.get('version')))}；参数依据：{_text(target.get('evidence'))}。",
            f"原手动备注（已有资料）：{_text(origin.get('manual_note'))}。",
        ]),
        _section("各水平检测证据", _level_paragraphs(origin)),
        _section("原检测采用的质量要求", _quality_paragraphs(origin)),
        _section("原因分析与纠正措施", [
            f"原因分类：{_text(content.get('cause_category'))}",
            f"原因分析：{_text(content.get('cause_analysis'))}",
            f"纠正措施：{_text(content.get('corrective_action'))}",
            f"补充说明：{_text(content.get('supplementary_note'))}",
        ]),
    ]
    retests = []
    for index, ref in enumerate(event.get("retest_refs") or [], 1):
        snapshot = ref.get("snapshot") or {}
        retests.append(f"复测{index}：{_text(snapshot.get('test_time'))}；结论 {CLASSIFICATION_LABELS.get(snapshot.get('classification'), _text(snapshot.get('classification')))}；触发规则 {_text(snapshot.get('rule_names'))}；差异说明 {_text(ref.get('difference_reason'))}。")
        retests.extend(_level_paragraphs(snapshot))
    sections.extend([
        _section("关联复测", retests or ["未记录关联复测；不代表已复测或复测合格。"]),
        _section("处理效果与人员记录", [
            f"效果说明：{_text(content.get('effect_description'))}",
            f"效果依据：{_text(content.get('effect_evidence'))}",
            f"处理人：{_text(content.get('handler_text'))}；处理时间：{_text(content.get('handled_at'))}。",
            f"确认人：{_text(content.get('confirmer_text'))}；确认时间：{_text(content.get('confirmed_at'))}。人员文字为业务记录，不代表电子签名。",
        ]),
        _section("患者结果影响评估", [
            f"评估情况：{_text(content.get('patient_impact_assessment'))}；影响范围：{_text(content.get('patient_impact_scope'))}。",
            f"影响期间：{_text(content.get('patient_impact_start'))} 至 {_text(content.get('patient_impact_end'))}。",
            f"处理措施：{_text(content.get('patient_impact_actions'))}",
            f"评估依据：{_text(content.get('patient_impact_basis'))}",
            "本报告记录质控事件处理，不自动确认患者结果可放行。",
        ]),
        _section("附件清单", [
            f"附件{index}：{_text(item.get('original_name'))}；说明：{_text(item.get('description'))}；"
            f"附件编号：{_text(item.get('attachment_id') or item.get('id'))}；"
            f"文件状态：{'可读取' if item.get('available') else _text(item.get('availability_message') or '文件缺失或无法读取')}。"
            for index, item in enumerate(package.get("attachments") or [], 1)
        ] or ["未记录附件。"]),
        _section("报告声明", [
            _text(package.get("declaration")),
            "报告按所列处理版本及原检测证据保存；以后补充或修订另存报告，原文件不覆盖。缺失历史资料以未记录表示。",
        ]),
    ])
    buffer = BytesIO()
    with plt.rc_context(layout._build_pdf_rc_params(_resolve_pdf_font_name())):
        with PdfPages(buffer) as pdf:
            pdf.infodict().update(Title=EVENT_REPORT_LABEL, Author=layout.PDF_AUTHOR, Creator=layout.PDF_CREATOR)
            figures = layout._build_text_pages(
                report_title=EVENT_REPORT_LABEL, page_title="处理记录",
                subtitle_lines=[f"报告编号：{package['report_no']}", f"处理状态：{status}{draft}"], sections=sections,
            )
            for index, figure in enumerate(figures, 1):
                figure.text(layout.PAGE_LEFT, layout.FOOTER_TEXT_Y,
                            f"处理版本 {package['revision_no']} | 生成时间：{package['generated_at']}",
                            fontsize=8.4, color=layout.FOOTER_COLOR)
                figure.text(layout.PAGE_RIGHT, layout.FOOTER_TEXT_Y, f"第 {index}/{len(figures)} 页",
                            ha="right", fontsize=8.4, color=layout.FOOTER_COLOR)
                pdf.savefig(figure)
                plt.close(figure)
    return buffer.getvalue()


def generate_event_report(event_id: int, revision_no: int | None = None) -> dict:
    from services.out_of_control_service import get_event
    selected = get_event(int(event_id), revision_no=revision_no)
    revision_no = int(selected["revision_no"])
    with get_connection() as connection:
        previous = connection.execute("SELECT * FROM qc_event_reports WHERE event_id=? AND revision_no=?",
                                      (int(event_id), revision_no)).fetchone()
    if previous is not None:
        return _report_result(previous)
    package = build_event_report_package(event_id, revision_no)
    pdf_bytes = render_event_report_pdf(package)
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("报告生成失败，请重试。")
    digest = sha256(pdf_bytes).hexdigest()
    # Render first. Failed rendering/storage must not leave a success history.
    with atomic_write() as connection:
        previous = connection.execute("SELECT * FROM qc_event_reports WHERE event_id=? AND revision_no=?",
                                      (int(event_id), revision_no)).fetchone()
        if previous is not None:
            return _report_result(previous)
        cursor = connection.execute("""INSERT INTO qc_event_reports
            (report_uid,event_id,revision_no,report_no,generated_at,file_name,sha256,package_json,pdf_bytes)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
                package["report_uid"], package["event_id"], package["revision_no"], package["report_no"],
                package["generated_at"], package["file_name"], digest,
                json.dumps(package, ensure_ascii=False, allow_nan=False), pdf_bytes,
            ))
        report_id = int(cursor.lastrowid)
    return {"report_id": report_id, "event_id": package["event_id"], "revision_no": package["revision_no"],
            "report_no": package["report_no"], "file_name": package["file_name"], "pdf_bytes": pdf_bytes,
            "sha256": digest, "package": package}


def _report_result(row: Any) -> dict:
    data = bytes(row["pdf_bytes"] or b"")
    if not data or sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("无法读取这份已归档报告，请从完整备份恢复；未生成替代内容。")
    return {"report_id": int(row["id"]), "event_id": int(row["event_id"]), "revision_no": int(row["revision_no"]),
            "report_no": row["report_no"], "file_name": row["file_name"], "pdf_bytes": data,
            "sha256": row["sha256"], "package": json.loads(row["package_json"])}


def list_event_reports(event_id: int | None = None) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute("""SELECT id,event_id,revision_no,report_no,generated_at,file_name,
            sha256,package_json FROM qc_event_reports""" + (" WHERE event_id=?" if event_id is not None else "")
            + " ORDER BY id DESC", (int(event_id),) if event_id is not None else ()).fetchall()
    return [{**dict(row), "report_id": int(row["id"]), "package": json.loads(row["package_json"])} for row in rows]


def read_event_report(report_id: int) -> bytes:
    with get_connection() as connection:
        row = connection.execute("SELECT pdf_bytes,sha256 FROM qc_event_reports WHERE id=?", (int(report_id),)).fetchone()
    if row is None or not row["pdf_bytes"]:
        raise ValueError("无法读取这份已归档报告，请从完整备份恢复；未生成替代内容。")
    data = bytes(row["pdf_bytes"])
    if sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("归档报告校验不一致，请从完整备份恢复；未生成替代内容。")
    return data


def build_monthly_handling_summaries(source_type: str, source_ids: list[int]) -> list[dict]:
    from services.out_of_control_service import lookup_events_for_sources
    events = lookup_events_for_sources(source_type, source_ids)
    summaries = []
    for event in events:
        origin = event["origin_snapshot"]
        content = event.get("content") or {}
        reports = [r for r in list_event_reports(event["event_id"]) if r["revision_no"] == event["revision_no"]]
        summaries.append({
            "event_id": event["event_id"], "revision_no": event["revision_no"],
            "source_type": source_type, "source_id": event["source_id"],
            "test_time": origin.get("test_time"), "status": event["status"],
            "original_classification": event["original_classification"],
            "cause_analysis": content.get("cause_analysis"), "corrective_action": content.get("corrective_action"),
            "effect_description": content.get("effect_description"),
            "reports": [{k: row[k] for k in ("id", "report_no", "generated_at", "revision_no")} for row in reports],
        })
    return summaries


def handling_summary_paragraphs(summaries: list[dict]) -> list[str]:
    result = []
    for item in summaries:
        refs = "；".join(f"{r['report_no']}（{r['generated_at']}）" for r in item.get("reports", [])) or "本处理版本尚无独立报告"
        result.extend([
            f"处理记录 {item['event_id']} / 版本 {item['revision_no']}；原检测 {_text(item.get('test_time'))}；"
            f"原结论 {CLASSIFICATION_LABELS.get(item.get('original_classification'), MISSING)}；"
            f"处理状态 {STATUS_LABELS.get(item.get('status'), MISSING)}。",
            f"原因：{_text(item.get('cause_analysis'))}；纠正措施：{_text(item.get('corrective_action'))}；处理效果：{_text(item.get('effect_description'))}。",
            f"独立报告：{refs}。",
        ])
    return result


def render_monthly_with_handling(package: Any, font_name: str, *, method: str) -> bytes:
    """Reuse every existing monthly page; add frozen handling pages only."""
    report = package.report
    if not report.handling_summaries:
        renderer = layout.render_lj_monthly_report_pdf if method == "lj" else layout.render_zscore_monthly_report_pdf
        return renderer(package, font_name)
    buffer = BytesIO()
    with plt.rc_context(layout._build_pdf_rc_params(font_name)):
        with PdfPages(buffer) as pdf:
            pdf.infodict().update(Title=report.title, Author=layout.PDF_AUTHOR, Creator=layout.PDF_CREATOR)
            if method == "lj":
                pages = [(figure, "摘要页") for figure in layout._build_lj_summary_pages(report)]
                pages.append((layout._build_lj_chart_page(package), "图表页"))
                abnormal = layout._build_lj_abnormal_pages
            else:
                pages = [(figure, "摘要页") for figure in layout._build_zscore_summary_pages(report)]
                pages.extend((layout._build_zscore_level_chart_page(package, level), "水平图页") for level in package.active_levels)
                pages.append((layout._build_zscore_level_summary_page(report), "各水平统计页"))
                abnormal = layout._build_zscore_abnormal_pages
            if report.abnormal_records:
                pages.extend((figure, "异常记录页") for figure in abnormal(report))
            pages.extend((figure, "说明页") for figure in layout._build_action_pages(report))
            pages.extend((figure, "批号与参数追溯") for figure in layout._build_lot_trace_pages(report))
            pages.extend((figure, "分析质量要求") for figure in layout._build_quality_pages(report))
            figures = layout._build_text_pages(
                report_title=report.title, page_title="失控处理摘要",
                subtitle_lines=[f"项目：{report.basic_info.project_name}", f"报告月份：{report.report_month_label}"],
                sections=[_section("结构化处理记录", handling_summary_paragraphs(report.handling_summaries)),
                          _section("统计说明", ["处理状态与原检测结论独立；本页不改变原月度在控、警告、失控计数。原手动备注见原报告相应记录。"])]
            )
            pages.extend((figure, "失控处理摘要") for figure in figures)
            layout._write_pages(pdf, pages, report)
    return buffer.getvalue()
