"""Populate an empty application database through public business services.

Generated values are synthetic; provenance is retained in internal audit data.
Existing application data is never overwritten. The output folder holds reports,
entry files, verification evidence and complete backups, not another environment.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "output" / "teaching-demo-2026-09-29"
os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_OUTPUT / "plot-cache"))
SEED = 20260929
NOTICE = ""
ACTOR = "检验员"
SOURCE_URL = "https://www.sansure.com.cn/sj/index.aspx"

import database as db
from services import master_data_service as md
from services import project_config_service as config
from services import material_workflow_service as materials
from services import lot_lifecycle_service as lifecycle
from services import daily_context_service as contexts
from services import daily_entry_service as entry
from services import product_directory_service as directory
from services import quality_target_service as quality
from services import quality_review_service as review
from services import out_of_control_service as handling


def spec(code, name, product, method="lj", scale="ct", scenario="stable", **extra):
    return dict(code=code, name=name, product=product, method=method, scale=scale,
                scenario=scenario, unit="Ct", means=[30.0], sds=[0.25],
                specimen="核酸液体材料", group="分子诊断", **extra)


SPECS = [
    spec("HBV", "乙型肝炎病毒DNA", "IQC-CM-00107", scenario="mixed"),
    spec("HCV", "丙型肝炎病毒RNA", "IQC-CM-00505", scale="log", scenario="building19"),
    spec("HIV1", "人类免疫缺陷病毒1型RNA定量测定", "IQC-CM-04005", scale="log"),
    spec("CMV", "人巨细胞病毒DNA", "IQC-CM-01001", scenario="pending"),
    spec("EBV", "EB病毒DNA", "IQC-CM-02103", method="zscore", scenario="confirmation"),
    spec("HSV1", "单纯疱疹病毒1型DNA", "IQC-CM-01901", scenario="warning"),
    spec("HSV2", "单纯疱疹病毒2型DNA", "IQC-CM-02001", scenario="in_progress"),
    spec("MP", "肺炎支原体DNA", "IQC-CM-11301", method="instant", scenario="ready20"),
    spec("CP", "肺炎衣原体DNA", "IQC-CM-11901", method="instant", scenario="suspect19"),
    spec("CT", "沙眼衣原体DNA", "IQC-CM-11401", method="instant", scenario="instant18"),
    spec("NG", "淋球菌DNA", "IQC-CM-13201", method="instant", scenario="transferred"),
    spec("UU", "解脲脲原体核酸", "IQC-CM-24201", method="instant", scenario="instant2"),
    spec("TB", "结核分枝杆菌DNA", "IQC-CM-13301", scenario="building12"),
    spec("RSVA", "呼吸道合胞病毒核酸（A型质控品）", "IQC-CM-03302", method="zscore"),
    spec("RSVB", "呼吸道合胞病毒核酸（B型质控品）", "IQC-CM-03401", method="instant", scenario="instant19"),
    spec("ADV3", "腺病毒核酸（3型质控品）", "IQC-CM-07401"),
    spec("EV71", "肠道病毒71型RNA", "IQC-CM-02301", method="instant", scenario="instant3"),
    dict(code="ALT", name="丙氨酸氨基转移酶", product="IQC-BI-03401", method="lj", scale="raw", scenario="revision", unit="U/L", means=[40.0], sds=[2.0], specimen="血清", group="临床生化"),
    dict(code="TG", name="甘油三酯", product="IQC-BI-03101", method="lj", scale="raw", scenario="stable", unit="mmol/L", means=[1.8], sds=[0.07], specimen="血清", group="临床生化"),
    dict(code="HBA1C", name="糖化血红蛋白", product="IQC-BI-05903", method="zscore", scale="raw", scenario="stable", unit="%", means=[5.6, 9.2], sds=[0.12, 0.18], specimen="全血", group="糖化血红蛋白"),
]


def get_or_create(table, field, value, create):
    with db.get_connection() as c:
        row = c.execute(f"SELECT id FROM {table} WHERE {field}=? AND is_disabled=0", (value,)).fetchone()
    return int(row[0]) if row else create()


def unit(symbol):
    return get_or_create("md_units", "symbol", symbol, lambda: md.create_unit(symbol=symbol, unit_name=symbol))


def confirm_quality(scope, item_id, plan):
    from services.quality_applicability_service import assess, infer_technique
    item = quality.item_context(scope, item_id)
    assay_code = plan.get("assay_code", plan["code"])
    clinical = assay_code in ("ALT", "TG", "HBA1C", "HIV1")
    context = dict(technique=infer_technique(item) or "clinical_chemistry", specimen=plan["specimen"],
                   result_kind="quantitative", result_scale=item["input_value_type"] if item["input_value_type"] in ("ct", "log") else "concentration",
                   purpose="clinical" if clinical else "research")
    candidates = assess(item, context)
    numerical = [r for r in candidates if r["kind"] == "numeric" and r["status"] == "applicable"]
    process_ids = [r["id"] for r in candidates if r["kind"] == "process" and r["status"] == "applicable"] if assay_code == "HIV1" else []
    local_process = "每次分析前核对质控品及试剂批号、有效期和仪器状态；异常时登记原因、纠正措施及复测记录。此文本为本次实验室要求。"
    if assay_code == "HIV1":
        local_process += "HIV-1 RNA定量先将copies/mL结果换算为Log10后录入；内外部质控随样本经历提取与扩增全过程，确认失控后先恢复在控再重新检测相应样本。"
    common = dict(confirmed_by="资料核对员", evidence="依据本软件已核查目录匹配项目、单位及尺度；" + NOTICE,
                  context=context, process_requirements=dict(source_ids=process_ids, requirement_text=local_process))
    if numerical:
        assert len(numerical) == 1, numerical
        levels = []
        if scope == "lot":
            levels = [dict(level_order=r["level_order"], concentration=plan["means"][n], category="")
                      for n, r in enumerate(config.list_lot_item_levels(item_id).to_dict("records"))]
        quality.adopt_requirement(scope, item_id, numerical[0]["id"], levels=levels, **common)
    else:
        review.save_recorded_requirement(scope, item_id, source_name="实验室质控方案", source_version="QC-2026-09",
            requirement_text="采用完整水平结果、20个有效建靶点和当前质控规则；Ct/Log用于数值信号稳定性监测，不自动判定病原体阴阳性；均值和SD按本批次参数管理。",
            search_record=dict(query=plan["name"] + "；数值监测；" + item["input_value_type"],
                official_url="https://www.nhc.gov.cn/wjw/s9492/wsbz.shtml", checked_on="2026-09-28",
                conclusion="no_numeric" if any(r["status"] == "applicable" for r in candidates) else "no_applicable", rationale="已在软件所附已核查目录中核对；当前输入尺度没有直接适用的自动数值限值，过程条款按适用情况参考，不宣称完成临床标准检索。"), **common)


def equipment():
    makers = {name: md.create_manufacturer(display_name=name, categories=categories, notes=NOTICE)
              for name, categories in [("圣湘生物", ["reagent"]), ("上海宏石", ["instrument"]),
                                       ("罗氏诊断", ["instrument", "reagent"]), ("Bio-Rad", ["instrument", "reagent"])]}
    def device(maker, model, title, number, group):
        mid = md.create_instrument_model(manufacturer_id=makers[maker], generic_name=title, model=model, notes=NOTICE)
        return md.create_lab_instrument(instrument_model_id=mid, display_name=model + " · " + number + "号",
            asset_code="ASSET-" + number, serial_number="SN-" + number, department_name="实验室",
            instrument_group=group, location="检验室", notes=NOTICE)
    instruments = dict(molecular=device("上海宏石", "SLAN-96P", "实时荧光定量PCR仪", "01", "分子诊断"),
                       chemistry=device("罗氏诊断", "cobas c 111", "全自动生化分析仪", "02", "临床生化"),
                       hba1c=device("Bio-Rad", "D-10", "糖化血红蛋白分析仪", "03", "糖化血红蛋白"))
    methods = {}
    for name, code in [("实时荧光PCR法", "REALTIME_PCR"), ("IFCC酶法", "IFCC_ALT"),
                       ("GPO-PAP酶比色法", "GPO_PAP"), ("高效液相色谱法", "HPLC")]:
        methods[code] = get_or_create("md_methods", "method_name", name,
            lambda n=name, k=code: md.create_method(method_name=n, method_code=k, notes=NOTICE))
    # HPLC HbA1c belongs to clinical chemistry although its name is not inferred.
    return makers, instruments, methods


def setup_project(plan, makers, instruments, methods):
    plan = dict(plan)
    code = plan["code"]
    if plan["scale"] == "log":
        plan.update(unit="IU/mL", means=[4.5], sds=[0.08], specimen="血清")
    if code == "HIV1":
        plan.update(unit="copies/mL", means=[3.9], sds=[0.08], specimen="血浆")
    if code in ("EBV", "RSVA"):
        plan.update(means=[32.0, 28.7], sds=[0.25, 0.22])
    molecular = code not in ("ALT", "TG", "HBA1C")
    maker = "圣湘生物" if molecular else ("Bio-Rad" if code == "HBA1C" else "罗氏诊断")
    reagent_names = {
        "HBV": "乙型肝炎病毒核酸定量检测试剂盒（PCR-荧光探针法）",
        "HCV": "丙型肝炎病毒核酸检测试剂盒（高敏HCV RNA）",
        "HIV1": "人类免疫缺陷病毒1型核酸测定试剂盒",
        "CMV": "人巨细胞病毒核酸定量检测试剂盒（PCR-荧光探针法）",
        "EBV": "EB病毒核酸定量检测试剂盒（PCR-荧光探针法）",
        "HSV1": "单纯疱疹病毒1型核酸检测试剂盒（PCR-荧光探针法）",
        "HSV2": "单纯疱疹病毒2型核酸检测试剂盒（PCR-荧光探针法）",
        "MP": "肺炎支原体核酸检测试剂盒（PCR-荧光探针法）",
        "CP": "肺炎衣原体核酸检测试剂盒（PCR-荧光探针法）",
        "CT": "沙眼衣原体核酸检测试剂盒（PCR-荧光探针法）",
        "NG": "淋球菌核酸检测试剂盒（PCR-荧光探针法）",
        "UU": "解脲脲原体核酸检测试剂盒（PCR-荧光探针法）",
        "TB": "结核分枝杆菌核酸检测试剂盒（PCR-荧光探针法）",
        "RSVA": "呼吸道合胞病毒核酸检测试剂盒（PCR-荧光探针法）",
        "RSVB": "呼吸道合胞病毒核酸检测试剂盒（PCR-荧光探针法）",
        "ADV3": "腺病毒核酸检测试剂盒（PCR-荧光探针法）",
        "EV71": "肠道病毒71型核酸检测试剂盒（PCR-荧光探针法）",
        "ALT": "ALTL (IFCC) 丙氨酸氨基转移酶试剂",
        "TG": "TRIGL / TG GPO-PAP 甘油三酯试剂",
        "HBA1C": "D-10 Dual Program Reorder Pack",
    }
    reagent_name = reagent_names[code]
    reagent = get_or_create("md_reagents", "generic_name", reagent_name, lambda: md.create_reagent(
        manufacturer_id=makers[maker], generic_name=reagent_name,
        catalog_no={"ALT": "04718569190", "TG": "04657594190", "HBA1C": "220-0201"}.get(code, ""),
        applicable_instrument_text="SLAN-96P（本次配置）" if molecular else ("D-10" if code == "HBA1C" else "cobas c 111"),
        notes=NOTICE + ("名称来源：" + SOURCE_URL if molecular else "名称来源见说明的官方产品资料。")))
    instrument = instruments["molecular" if molecular else ("hba1c" if code == "HBA1C" else "chemistry")]
    method = methods["REALTIME_PCR" if molecular else {"ALT": "IFCC_ALT", "TG": "GPO_PAP", "HBA1C": "HPLC"}[code]]
    catalog = directory.list_catalog_product_choices()
    product = catalog[catalog.product_code == plan["product"]].iloc[0].to_dict()
    material_id = int(product["product_id"])
    tid = get_or_create("md_test_items", "chinese_name", plan["name"], lambda: md.create_test_item(
        chinese_name=plan["name"], abbreviation=code, category_name=plan["group"], specimen_type=plan["specimen"],
        default_unit_id=unit(plan["unit"]), notes=NOTICE))
    # Reuse a built-in analyte identity, but do not mutate a dictionary record.
    pid = config.create_project_template(template_name=f"{code} · {plan['name']}",
        lab_instrument_id=instrument, qc_material_id=material_id, default_reagent_id=reagent,
        default_qc_method=plan["method"], default_method_id=method, default_level_count=len(plan["means"]),
        project_group=plan["group"], notes="")
    variants = [dict(method=plan["method"], scale=plan["scale"], means=plan["means"], sds=plan["sds"], unit=plan["unit"])]
    if code == "HBV":
        variants = [dict(method="lj", scale="raw", means=[5000.0], sds=[180.0], unit="IU/mL"),
                    dict(method="zscore", scale="log", means=[3.7, 4.7, 5.7], sds=[0.08]*3, unit="IU/mL"),
                    dict(method="instant", scale="ct", means=[31.6], sds=[0.22], unit="Ct")]
    config.save_template_items(pid, [dict(test_item_id=tid, qc_method=v["method"], input_value_type=v["scale"],
        unit_id=unit(v["unit"]), method_id=method, reagent_id=reagent, level_count=len(v["means"]), target_n=20,
        notes=NOTICE + ("同目标三方法对照，不把三种判读作为三个独立临床检测。" if code == "HBV" else "")) for v in variants])
    titems = config.list_template_items(pid).to_dict("records")
    for item in titems:
        confirm_quality("project", item["id"], plan)
    config.activate_project_template(pid)
    max_levels = max(len(v["means"]) for v in variants)
    levels = []
    for n in range(max_levels):
        combined = code == "HBA1C"
        level_name = ("水平" + str(n+1) if combined else ("制备水平" + str(n+1) if max_levels > 1 else product["concentration"] or product["concentration_code"]))
        note = NOTICE
        if max_levels > 1 and not combined:
            note += "本批次采用不同浓度的制备水平。"
        if code == "HBV":
            level_name = ["制备L1（1:100）", "制备L2（1:10）", "制备L3（原浓度）"][n]
            note += ["设置原浓度5×10^5 IU/mL，1:100稀释至5000 IU/mL（Log10约3.7），同一L1分别录入原值、Log10和Ct结果。",
                     "设置原浓度5×10^5 IU/mL，1:10稀释至50000 IU/mL（Log10约4.7）。",
                     "设置原浓度5×10^5 IU/mL，未稀释水平（Log10约5.7）。"][n]
        if code == "HIV1":
            note += "目录保留IU/mL原文；本项目以copies/mL为单位并录入Log10；不同量值单位不作自动换算。"
        levels.append(materials.register_control_material(material_id=material_id,
            lot_no=f"QC-202608-{code}-A-L{n+1}", expiry_date="2027-03-31", level_name=level_name,
            level_code=f"L{n+1}", catalog_no=plan["product"], concentration_note=note))
    config_id = materials.create_material_config(template_id=pid, selections={r["id"]: levels[:r["level_count"]] for r in titems}, config_name=f"{code} · 在用批次A")
    litems = config.list_lot_config_items(config_id).to_dict("records")
    for item in litems:
        confirm_quality("lot", item["id"], plan)
    config.activate_lot_config(config_id)
    relation = directory.get_product_relationships(material_id)
    directory.save_product_coverage(material_id, [dict(test_item_id=tid, method_id=method)], expected_version=relation["edit_version"],
        confirmed_by="资料核对员", evidence="按产品目标、检验项目和检测方法核对关联。" + NOTICE)
    reagent_lots = [lifecycle.create_reagent_lot(reagent_id=reagent, lot_no=f"RG-202608-{code}-{letter}", expiry_date="2027-04-30", source_text=NOTICE) for letter in ("A", "B")]
    with db.get_connection() as c:
        lot_id = c.execute("SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?", (levels[0],)).fetchone()[0]
    plan.update(template_id=pid, config_id=config_id, test_item_id=tid, reagent_id=reagent,
                reagent_lots=reagent_lots, instrument_id=instrument, material_id=material_id, levels=levels,
                main_lot_id=lot_id, product_info=product, variants=variants, config_items=litems,
                reagent_name=reagent_name, receipts=[])
    return plan


def sync_and_verify(plans):
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    sync_lj_workbench_bindings(); sync_zscore_workbench_bindings(); sync_instant_workbench_bindings()
    systems = lifecycle.workbench_systems()
    for plan in plans:
        item_ids = {r["id"] for r in config.list_template_items(plan["template_id"]).to_dict("records")}
        plan["systems"] = [s for s in systems if s["template_item_id"] in item_ids]
        switches = []
        for system in plan["systems"]:
            for n, lot in enumerate(plan["reagent_lots"]):
                vid = lifecycle.record_lot_verification(template_item_id=system["template_item_id"], system_id=system["id"],
                    reagent_lot_id=lot, conclusion="pass", evidence="新批试剂已核对，批号比对通过。",
                    confirmed_by=ACTOR, confirmed_at="2026-07-31" if n == 0 else "2026-09-20")
                if n == 0:
                    switches.append(dict(template_item_id=system["template_item_id"], system_id=system["id"],
                                         reagent_lot_id=lot, verification_id=vid, expected_revision=0))
                else:
                    system["next_verification_id"] = vid
        lifecycle.switch_reagent_lots(selections=switches, effective_at="2026-08-01", operator=ACTOR, reason="启用批号A")
        with db.get_connection() as c:
            plan["bindings"] = [dict(r) for r in c.execute("SELECT b.* FROM qc_workbench_bindings b JOIN qc_lot_config_items i ON i.id=b.lot_config_item_id WHERE i.lot_config_id=? ORDER BY i.sort_order,i.id", (plan["config_id"],))]


def context(plan, when):
    return contexts.get_daily_context(lab_instrument_id=plan["instrument_id"], qc_material_id=plan["material_id"],
        qc_material_lot_id=plan["main_lot_id"], test_time=when, template_id=plan["template_id"], lot_config_id=plan["config_id"])


def submit(plan, when, offsets, *, methods=None, suffix="", note=""):
    ctx = context(plan, when)
    if ctx["issues"]:
        raise AssertionError((plan["code"], ctx["issues"]))
    rows = []
    for item in ctx["items"]:
        if methods and item["qc_method"] not in methods:
            continue
        v = next(v for v in plan["variants"] if v["method"] == item["qc_method"] and v["scale"] == item["input_value_type"])
        values = []
        for n, level in enumerate(item["levels"]):
            factor = offsets[n] if isinstance(offsets, list) else offsets
            value = v["means"][n] + v["sds"][n] * factor
            values.append(dict(qc_level_id=level["qc_level_id"], value=f"{value:.7f}"))
        rows.append(dict(row_key=item["row_key"], lot_config_item_id=item["lot_config_item_id"],
            reagent_lot_id=item["suggested_reagent_lot_id"], levels=values, manual_note=note or NOTICE))
    request = dict(submission_id=f"teaching:{plan['code']}:{when}:{suffix}", selection=ctx["selection"],
        context_revision=ctx["context_revision"], test_time=when, operator=ACTOR, purpose="routine", items=rows)
    check = entry.validate_submission(request)
    if not check["valid"]:
        raise AssertionError((plan["code"], when, check["errors"]))
    receipt = entry.submit_daily(check["frozen_request"])
    assert receipt["count"] == len(rows)
    plan["receipts"].append(receipt)
    return receipt


def set_profiles(plan, when="2026-08-21 07:00:00", revision=False):
    for binding in plan["bindings"]:
        method = binding["qc_method"]
        if method == "instant":
            continue
        item = next(r for r in plan["config_items"] if r["id"] == binding["lot_config_item_id"])
        v = next(v for v in plan["variants"] if v["method"] == method and v["scale"] == item["input_value_type"])
        lifecycle.create_target_profile(method=method, batch_id=binding["runtime_batch_id"],
            levels=[dict(level_id=f"Level {n+1}", mean=mean, sd=v["sds"][n]) for n, mean in enumerate(v["means"])],
            source="revision" if revision else "manual", evidence="经核对后录入本批次均值和标准差，保留参数建立记录，后续按版本管理。",
            confirmed_by="资料核对员", effective_at=when)


def seed_results(plan, rng):
    scenario = plan["scenario"]
    is_instant = plan["method"] == "instant"
    if scenario.startswith("building"):
        count = int(scenario.replace("building", ""))
        for n in range(count):
            off = 5.5 if n == count - 2 else ((-1 if n % 2 else 1) * rng.uniform(.2, 1.3))
            submit(plan, f"2026-09-{n+1:02d} 08:10:00", off)
        return
    if is_instant:
        count = {"ready20": 20, "suspect19": 19, "instant18": 18, "transferred": 20,
                 "instant2": 2, "instant19": 19, "instant3": 3}[scenario]
        for n in range(count):
            off = 8.0 if scenario == "suspect19" and n == count - 1 else ((-1 if n % 2 else 1) * rng.uniform(.3, 1.3))
            day = 28 - (count - 1 - n)
            submit(plan, f"2026-09-{day:02d} 08:10:00", off)
        return
    for n in range(20):
        submit(plan, f"2026-08-{n+1:02d} 08:10:00", (-1 if n % 2 else 1) * rng.uniform(.3, 1.3),
               methods=["lj", "zscore"] if plan["code"] == "HBV" and n >= 19 else None)
    set_profiles(plan)
    start = date(2026, 8, 21)
    for n in range(38):
        day = start + timedelta(days=n)
        if day == date(2026, 9, 20) and scenario == "revision":
            with db.get_connection() as c:
                switches = [dict(template_item_id=s["template_item_id"], system_id=s["id"], reagent_lot_id=plan["reagent_lots"][1],
                    verification_id=s["next_verification_id"], expected_revision=lifecycle.usage_revision(c, s["id"])) for s in plan["systems"]]
            lifecycle.switch_reagent_lots(selections=switches, effective_at="2026-09-20 07:00:00", operator=ACTOR, reason="新批试剂验证通过后换批")
            set_profiles(plan, when="2026-09-20 07:30:00", revision=True)
        submit(plan, day.isoformat() + " 08:10:00", (-1 if n % 2 else 1) * rng.uniform(.2, 1.2), methods=["lj", "zscore"])
    offset = 4.0 if scenario in ("mixed", "pending", "confirmation", "in_progress") else (2.3 if scenario == "warning" else .35)
    receipt = submit(plan, "2026-09-28 08:10:00", offset, methods=["lj", "zscore"])
    plan["scenario_receipt"] = receipt
    if scenario in ("mixed", "confirmation", "in_progress"):
        plan["retest_receipt"] = submit(plan, "2026-09-28 09:20:00", -.2, methods=["lj", "zscore"], note="首次复测：由原规则判断连续偏移是否解除；不覆盖首次结果。")


def seed_rule_gallery(plans):
    """Append chronological observations, then assert actual saved rule evidence."""
    scenarios = []
    for code, method, count in (("ALT", "lj", 1), ("HBA1C", "zscore", 2), ("HBV", "zscore", 3)):
        plan = next(p for p in plans if p["code"] == code)
        time = datetime(2026, 9, 28, 10, 0)
        cases = [("1_2s", [[2.3] + [0.] * (count-1)]),
                 ("1_3s", [[3.4] + [0.] * (count-1)]),
                 ("R_4s", [[2.4], [-2.4]] if count == 1 else [[2.4, -2.4] + [0.] * (count-2)])]
        if count < 3:
            cases += [("2_2s", [[2.3] + [0.] * (count-1)] * 2),
                      ("4_1s", [[1.3] + [0.] * (count-1)] * 4),
                      ("10x" if count == 1 else "10_x", [[.4] + [0.] * (count-1)] * 10)]
        else:
            cases += [("2of3_2s", [[2.3, 2.3, 0.]]), ("3_1s", [[1.3, 1.3, 1.3]]),
                      ("12_x", [[.4, 0., 0.]] * 12)]
        for rule, sequence in cases:
            for reset in range(2):
                submit(plan, time.isoformat(" "), [0.] * count, methods=[method], suffix=f"reset:{rule}:{reset}", note="规则示例前中心点，隔开连续规则序列。")
                time += timedelta(minutes=1)
            evidence = []
            for offsets in sequence:
                receipt = submit(plan, time.isoformat(" "), offsets, methods=[method], suffix="rule:" + rule,
                    note="规则示例：" + rule + "；偏移为设定，判读由原质控规则计算。")
                record = receipt["items"][0]
                snapshot = handling.read_source(record["source_type"], record["source_id"])
                evidence.append(dict(time=time.isoformat(" "), source_type=record["source_type"], source_id=record["source_id"],
                    offsets_sd=offsets, classification=record["classification"], rules=snapshot["rule_names"]))
                time += timedelta(minutes=1)
            scenarios.append(dict(code=code, method=method, level_count=count, intended_rule=rule, evidence=evidence))
            observed = {name.strip() for e in evidence for hit in e["rules"] for name in hit.split(",")}
            assert rule in observed, (code, rule, observed)
        for n in range(3):
            submit(plan, time.isoformat(" "), [0.] * count, methods=[method], suffix="gallery-recovery", note="规则结束，结果恢复中心值。")
            time += timedelta(minutes=1)
    return scenarios


def make_events(plans, out):
    from services.out_of_control_attachment_service import stage_attachment
    from services.out_of_control_report_service import generate_event_report
    result = []
    wanted = {"mixed": "completed", "pending": "pending", "confirmation": "pending_confirmation", "in_progress": "in_progress", "warning": "in_progress"}
    for plan in plans:
        scenario = plan["scenario"]
        if scenario not in wanted:
            continue
        for record in plan["scenario_receipt"]["items"]:
            expected = "warning" if scenario == "warning" else "reject"
            assert record["classification"] == expected, (plan["code"], record)
            event = handling.open_event(record["source_type"], record["source_id"],
                f"open:{plan['code']}:{record['source_type']}", ACTOR, explicit_warning=scenario == "warning")
            if event["status"] == wanted[scenario] and event["status"] != "pending":
                result.append(dict(code=plan["code"], event_id=event["event_id"], status=event["status"], classification=expected,
                    source_type=record["source_type"], source_id=record["source_id"], revision=event["revision_no"]))
                continue
            content = dict(cause_category="操作", cause_analysis="加样前混匀不足，造成单次结果偏移。",
                corrective_action="检查质控品效期和保存条件，重新充分混匀、核对加样操作后复测。",
                effect_description="复测恢复在控，保留原失控点以展示追溯。", effect_evidence="关联本批次独立复测记录。",
                handler_text=ACTOR, patient_impact_assessment="无需评估", patient_impact_scope="本次仅开展质控品检测，未检测患者样本。",
                patient_impact_basis=NOTICE, supplementary_note=NOTICE)
            if plan.get("retest_receipt"):
                ref = next(r for r in plan["retest_receipt"]["items"] if r["qc_method"] == record["qc_method"])
                refs = [dict(source_type=ref["source_type"], source_id=ref["source_id"], difference_reason="同批号同参数的首次复测")]
                if ref["classification"] != "accept":
                    followup = submit(plan, "2026-09-28 12:00:00", .2, methods=[record["qc_method"]], suffix="recovery-retest",
                        note="第二次复测：首次复测仍受连续规则影响，继续观察恢复在控；不覆盖首次或第一次复测。")
                    ref = followup["items"][0]
                    refs.append(dict(source_type=ref["source_type"], source_id=ref["source_id"], difference_reason="同批号同参数第二次复测；第一次仍受连续规则影响"))
                assert ref["classification"] == "accept", ref
                content["retest_refs"] = refs
            if scenario == "warning":
                content.update(cause_analysis="单次超过2SD警告，正在核查操作和材料状态。",
                    effect_description="尚待现场复测，当前仅保存处理中草稿。", effect_evidence="原始警告记录，未宣称复测恢复。")
            if wanted[scenario] != "pending":
                attachment = stage_attachment(event["event_id"], f"{plan['code']}_处理核对表.csv",
                    ("项目,核对内容,结论\n" + plan["name"] + ",混匀/效期/加样核对,完成\n").encode("utf-8-sig"), uploaded_by=ACTOR, description=NOTICE)
                content["attachment_ids"] = [attachment["attachment_id"]]
                event = handling.save_handling(event["event_id"], event["revision_no"], f"draft:{event['event_id']}", "save_draft", content, ACTOR)
            if wanted[scenario] in ("pending_confirmation", "completed"):
                event = handling.save_handling(event["event_id"], event["revision_no"], f"submit:{event['event_id']}", "submit", event["content"], ACTOR)
            if wanted[scenario] == "completed":
                content = dict(event["content"], confirmer_text="复核员", confirmed_at=handling._now(), confirmation_checked=True)
                event = handling.save_handling(event["event_id"], event["revision_no"], f"confirm:{event['event_id']}", "confirm", content, "复核员")
                report = generate_event_report(event["event_id"])
                path = out / "reports" / report["file_name"]
                path.write_bytes(report["pdf_bytes"])
            assert event["status"] == wanted[scenario]
            result.append(dict(code=plan["code"], event_id=event["event_id"], status=event["status"], classification=expected,
                source_type=record["source_type"], source_id=record["source_id"], revision=event["revision_no"]))
    return result


def seed_lifecycle(plans):
    """Leave a verified unused QC replacement and a draft configuration to operate live."""
    plan = next(p for p in plans if p["code"] == "TG")
    new_level = materials.register_control_material(material_id=plan["material_id"], lot_no="QC-202608-TG-B-L1",
        expiry_date="2027-06-30", level_name="水平1", level_code="B-L1", catalog_no=plan["product"], concentration_note=NOTICE)
    with db.get_connection() as c:
        new_lot = c.execute("SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?", (new_level,)).fetchone()[0]
    verifications = []
    for s in plan["systems"]:
        verifications.append(lifecycle.record_lot_verification(template_item_id=s["template_item_id"], system_id=s["id"],
            qc_lot_id=new_lot, conclusion="pass", evidence="设定：新旧批号平行比对通过，可现场确认换批。", confirmed_by=ACTOR, confirmed_at="2026-09-28 10:00:00"))
    copied = materials.copy_material_config(source_config_id=plan["config_id"], selections={plan["config_items"][0]["id"]: [new_level]}, config_name="TG · 待现场确认的新批次B")
    return dict(code="TG", new_level=new_level, new_lot=new_lot, new_config=copied, verification_ids=verifications,
                expected="新组合保持草稿，现场核对质量要求后确认；旧批次仍可查询。")


def seed_z_building(plans):
    """A second actual combination keeps Z-score target maintenance operable live."""
    import copy
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    source = next(p for p in plans if p["code"] == "RSVA")
    plan = copy.deepcopy(source)
    levels = [materials.register_control_material(material_id=plan["material_id"],
        lot_no=f"QC-202608-RSVA-B-L{n+1}", expiry_date="2027-06-30", level_name=f"制备水平{n+1}",
        level_code=f"B-L{n+1}", catalog_no=plan["product"], concentration_note=NOTICE + "新批次B制备，现场两水平参数建立。") for n in range(2)]
    cid = materials.copy_material_config(source_config_id=plan["config_id"],
        selections={plan["config_items"][0]["id"]: levels}, config_name="RSVA · 新批次B双水平建靶（19次）")
    items = config.list_lot_config_items(cid).to_dict("records")
    for item in items:
        confirm_quality("lot", item["id"], plan)
    config.activate_lot_config(cid)
    sync_zscore_workbench_bindings()
    with db.get_connection() as c:
        lot = c.execute("SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?", (levels[0],)).fetchone()[0]
        bindings = [dict(r) for r in c.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?", (cid,))]
    plan.update(config_id=cid, main_lot_id=lot, levels=levels, config_items=items, bindings=bindings, receipts=[])
    for n in range(19):
        offsets = [5.5, .2] if n == 17 else [(-1 if n % 2 else 1) * (.4 + (n % 4) * .15)] * 2
        submit(plan, f"2026-09-{n+10:02d} 14:00:00", offsets, suffix="new-z-building",
            note="新批次B完整两水平建靶；第18次一水平偏离，供现场核查，原始run保留。")
    assert all(r["writable"] for r in context(plan, "2026-09-29 14:00:00")["items"])
    from zscore_logic import get_zscore_runs
    frame = get_zscore_runs(bindings[0]["runtime_batch_id"])
    assert len(frame) == 19
    return dict(code="RSVA", config_id=cid, main_lot_id=lot, binding=bindings[0], level_ids=levels,
                expected="两水平19次完整建靶，包含一个待核查离群run；现场两水平输入32.0、28.7。", next_values=[32.0, 28.7])


def append_instant_scale_projects(plans):
    """Append two same-assay projects while retaining all existing identities/results."""
    import copy
    added = []
    for code in ("ALT", "HIV1"):
        base = next(p for p in plans if p["code"] == code)
        new_code = code + "_INIT"
        if any(p["code"] == new_code for p in plans):
            continue
        title = code + " · 初始监测"
        with db.atomic_write():
            with db.get_connection() as c:
                assert not c.execute("SELECT 1 FROM qc_project_templates WHERE template_name=?", (title,)).fetchone(), title
            p = copy.deepcopy(base)
            variant = dict(method="instant", scale=base["scale"], means=list(base["means"]), sds=list(base["sds"]), unit=base["unit"])
            method_id = int(config.list_template_items(base["template_id"]).iloc[0]["method_id"])
            pid = config.create_project_template(template_name=title, lab_instrument_id=p["instrument_id"],
                qc_material_id=p["material_id"], default_reagent_id=p["reagent_id"], default_qc_method="instant",
                default_method_id=method_id, default_level_count=1, project_group=p["group"], notes="")
            config.save_template_items(pid, [dict(test_item_id=p["test_item_id"], qc_method="instant", input_value_type=p["scale"],
                unit_id=unit(p["unit"]), method_id=method_id, reagent_id=p["reagent_id"], level_count=1, target_n=20, notes="")])
            p.update(code=new_code, assay_code=code, method="instant", scenario="instant3", template_id=pid,
                     variants=[variant], receipts=[], project_name=title)
            for item in config.list_template_items(pid).to_dict("records"):
                confirm_quality("project", item["id"], p)
            config.activate_project_template(pid)
            titems = config.list_template_items(pid).to_dict("records")
            cid = materials.create_material_config(template_id=pid, selections={i["id"]:p["levels"][:1] for i in titems},
                config_name=code + " · 初始监测批次A")
            p.update(config_id=cid, config_items=config.list_lot_config_items(cid).to_dict("records"))
            for item in p["config_items"]:
                confirm_quality("lot", item["id"], p)
            config.activate_lot_config(cid)
            sync_and_verify([p])
            for n, offset in enumerate((-.5, .6, -.1)):
                submit(p, f"2026-09-28 18:{10+n:02d}:00", offset, suffix="initial-scale")
        plans.append(p)
        added.append(p)
    return added


def reports(plans, out, *, request_suffix=""):
    from services import batch_monthly_report_service as monthly
    selected_plans = [p for p in plans if p["code"] in ("HBV", "ALT", "HBA1C")]
    selected_batches = {(b["qc_method"], b["runtime_batch_id"]) for p in selected_plans for b in p["bindings"] if b["qc_method"] != "instant"}
    jobs = []
    for month in ("2026-08", "2026-09"):
        preview = monthly.preview_monthly_reports(month)
        picked = [r["unit_key"] for r in preview["items"] if (r["qc_method"], r["batch_id"]) in selected_batches]
        assert len(picked) == 4, (month, preview)
        job = monthly.create_monthly_report_job(preview, picked, "teaching-monthly-" + month + request_suffix)
        for item in monthly.get_monthly_report_job(job)["items"]:
            monthly.run_monthly_report_item(job, item["id"])
            ready = monthly.read_monthly_report_item(job, item["id"])
            # API returns immutable report bytes; save convenient demonstration copies.
            if isinstance(ready, dict):
                payload = ready.get("pdf_bytes")
                name = ready.get("file_name", str(item["id"]) + ".pdf")
                if payload:
                    (out / "reports" / name).write_bytes(payload)
        ready = monthly.get_monthly_report_job(job)
        assert ready["counts"]["succeeded"] == 4, ready
        zip_result = monthly.build_monthly_report_zip(job)
        if isinstance(zip_result, dict):
            (out / "reports" / zip_result["file_name"]).write_bytes(zip_result.get("zip_bytes") or zip_result.get("data"))
        elif isinstance(zip_result, tuple):
            (out / "reports" / f"{month}_四份月报.zip").write_bytes(next(v for v in zip_result if isinstance(v, bytes)))
        elif isinstance(zip_result, bytes):
            (out / "reports" / f"{month}_四份月报.zip").write_bytes(zip_result)
        jobs.append(dict(month=month, job_id=job, count=4, exclusions=len(preview.get("exclusions", []))))
    return jobs


def prepare_live_files(plans, out):
    import csv
    import copy
    from services.daily_draft_service import new_draft, draft_rows, build_request
    from services.daily_result_io_service import export_daily_workbook, preview_daily_workbook, apply_daily_workbook
    from services.project_config_io_service import build_project_template_xlsx, build_lot_config_xlsx
    folder = out / "live-entry"
    folder.mkdir(exist_ok=True)
    summary = []
    for plan in plans:
        if plan["scenario"] == "transferred":
            continue
        ctx = context(plan, "2026-09-29 08:10:00")
        draft = new_draft(ctx, operator="现场操作员", test_time="2026-09-29 08:10:00")
        for row in draft_rows(draft):
            item, level = row["item"], row["level"]
            variant = next(v for v in plan["variants"] if v["method"] == item["qc_method"] and v["scale"] == item["input_value_type"])
            value = variant["means"][level["level_order"] - 1]
            draft["values"][row["key"]] = str(value)
            draft["notes"][str(item["lot_config_item_id"])] = "现场录入"
            attention = "按当前批次的水平顺序录入"
            if item["qc_method"] == "instant":
                from services.instant_service import build_instant_workbench_context
                if build_instant_workbench_context(item["runtime_batch_id"])["summary"]["effective_count"] >= 20:
                    attention = "即时法已满20点，请先核对转入LJ，或取消此行后继续录入其他方法"
            summary.append(dict(项目=item["project_name"], 质控方法=item["qc_method"], 输入尺度=item["input_value_type"],
                单位=item["unit_symbol"], 水平=level["level_name"], 实际批号=level["lot_no"], 建议输入值=value,
                注意事项=attention))
        if plan["code"] in ("HBV", "ALT", "HBA1C", "ALT_INIT", "HIV1_INIT"):
            data = export_daily_workbook(draft)
            empty = new_draft(ctx, operator="现场操作员", test_time="2026-09-29 08:10:00")
            preview = preview_daily_workbook(data, empty)
            assert preview["valid"], preview
            apply_daily_workbook(empty, preview)
            validated = entry.validate_submission(build_request(empty))
            assert validated["valid"], validated
            (folder / f"{plan['code']}_现场导入示例.xlsx").write_bytes(data)
            (folder / f"{plan['code']}_空白录入模板.xlsx").write_bytes(export_daily_workbook(draft, template=True))
            (folder / f"{plan['code']}_项目配置.xlsx").write_bytes(build_project_template_xlsx(plan["template_id"]))
            (folder / f"{plan['code']}_批次配置.xlsx").write_bytes(build_lot_config_xlsx(plan["config_id"]))
            (folder / f"{plan['code']}_粘贴检测值.tsv").write_text("\n".join(str(row["value"]) for row in draft_rows(draft)) + "\n", encoding="utf-8")
            if plan["code"] == "HBV":
                continuing = copy.deepcopy(draft)
                continuing["selected"] = [str(i["lot_config_item_id"]) for i in ctx["items"] if i["qc_method"] != "instant"]
                current = export_daily_workbook(continuing)
                target = new_draft(ctx, operator="现场操作员", test_time="2026-09-29 08:10:00")
                target["selected"] = list(continuing["selected"])
                check = preview_daily_workbook(current, target)
                assert check["valid"], check
                apply_daily_workbook(target, check)
                assert entry.validate_submission(build_request(target))["valid"]
                (folder / "HBV_LJ与多水平_继续录入.xlsx").write_bytes(current)
                (folder / "HBV_LJ与多水平_粘贴检测值.tsv").write_text("\n".join(str(r["value"]) for r in draft_rows(continuing, selected_only=True)) + "\n", encoding="utf-8")
    with (folder / "现场录入值表.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)
    return dict(folder="live-entry", examples=[p["code"] for p in plans if p["code"] in ("HBV", "ALT", "HBA1C", "ALT_INIT", "HIV1_INIT")], suggested_values=summary,
                import_preview_and_submission_validation="passed_without_saving", planned_time="2026-09-29 08:10:00")


def validate(plans, out, events):
    import re
    from services.daily_overview_service import get_daily_overview
    from services.instant_service import build_instant_workbench_context
    from services.report_service import list_report_history_records, read_report_history_pdf
    from services.out_of_control_attachment_service import list_attachments, read_attachment
    with db.get_connection() as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
            "qc_project_templates", "qc_project_template_items", "qc_lot_configs", "qc_workbench_bindings",
            "results", "zscore_runs", "zscore_level_results", "instant_results", "qc_result_contexts", "qc_result_evaluations",
            "qc_daily_submissions", "qc_ooc_events", "qc_target_profiles", "qc_lot_verifications", "qc_lot_change_events",
            "report_exports", "qc_event_reports", "qc_managed_files", "qc_monthly_report_jobs")}
        assert counts["qc_project_templates"] == len(plans)
        assert counts["qc_project_template_items"] == sum(len(p["variants"]) for p in plans)
        assert c.execute("SELECT COUNT(*) FROM zscore_runs r WHERE r.level_count<>(SELECT COUNT(*) FROM zscore_level_results z WHERE z.run_id=r.id)").fetchone()[0] == 0
        missing = c.execute("SELECT COUNT(*) FROM qc_result_contexts x WHERE NOT EXISTS(SELECT 1 FROM qc_result_evaluations e WHERE e.context_id=x.id)").fetchone()[0]
        assert missing == 0
        assert c.execute("SELECT COUNT(*) FROM md_qc_material_lots WHERE lot_no NOT LIKE 'QC-%'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM md_reagent_lots WHERE lot_no NOT LIKE 'RG-%'").fetchone()[0] == 0
        visible_labels = {
            "qc_project_templates": ["template_name", "notes"], "lab_instruments": ["display_name", "notes"],
            "md_reagents": ["generic_name", "notes"], "md_qc_material_lots": ["lot_no"],
            "md_reagent_lots": ["lot_no", "source_text"], "qc_project_template_items": ["notes", "quality_review_json"],
            "qc_lot_config_items": ["notes", "quality_review_json"], "md_qc_levels": ["level_name", "level_code", "concentration_label"],
            "results": ["operator", "manual_note"], "zscore_runs": ["operator", "manual_note"],
            "instant_results": ["operator", "manual_note"],
        }
        for table, columns in visible_labels.items():
            for row in c.execute("SELECT id," + ",".join(columns) + " FROM " + table):
                assert not any(re.search("演示|教学|模拟|demo", str(row[k] or ""), re.I) for k in columns), (table, row["id"])
    overview = get_daily_overview("2026-09-28")
    assert not any(r.get("read_error") or r.get("quality", {}).get("error") for r in overview["items"])
    for plan in plans:
        if plan["scenario"] != "transferred":
            ctx = context(plan, "2026-09-29 08:10:00")
            assert ctx["items"] and all(r["writable"] for r in ctx["items"]), (plan["code"], ctx)
        rel = directory.get_product_relationships(plan["material_id"])
        assert rel["coverage"], plan["code"]
    instant = []
    for plan in plans:
        for b in plan["bindings"]:
            if b["qc_method"] == "instant":
                state = build_instant_workbench_context(b["runtime_batch_id"])
                instant.append(dict(code=plan["code"], batch_id=b["runtime_batch_id"], summary=state["summary"]))
    history = list_report_history_records()
    for report in history:
        data = read_report_history_pdf(report)
        assert data.startswith(b"%PDF-")
    attachments_verified = 0
    for event in events:
        for attachment in list_attachments(event["event_id"]):
            assert read_attachment(event["event_id"], attachment["attachment_id"])
            attachments_verified += 1
    return dict(counts=counts, overview=overview, instant=instant, report_count=len(history), events=events,
                integrity="ok", complete_zscore_runs=True, no_missing_saved_evaluations=True,
                all_active_contexts_ready_for_2026_09_29=True, ordinary_business_labels=True,
                attachments_verified=attachments_verified)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--finish", action="store_true", help="Finish reports/validation from an existing core checkpoint.")
    args = parser.parse_args()
    out = args.output.resolve()
    if out != DEFAULT_OUTPUT.resolve():
        raise SystemExit("For safety this builder only writes its dedicated teaching-demo folder.")
    if (ROOT / "data" / "qc_lj_app.db").exists() and not args.finish:
        raise SystemExit("正式数据库已存在；本脚本不会覆盖已有业务资料。")
    if out.exists() and any(out.iterdir()) and not args.finish:
        if not args.replace:
            raise SystemExit("Existing teaching output found; use --replace to regenerate this isolated dataset.")
        shutil.rmtree(out)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    db.DB_PATH = db.DEFAULT_DB_PATH = ROOT / "data" / "qc_lj_app.db"
    db.STORAGE_CONFIG_PATH = ROOT / "data" / "storage_config.json"
    db.LEGACY_DB_CANDIDATES = []
    if args.finish:
        checkpoint = json.loads((out / "core-ready.json").read_text(encoding="utf-8"))
        plans, rule_scenarios = checkpoint["plans"], checkpoint["rule_scenarios"]
        finish(plans, rule_scenarios, out)
        return
    db.init_db()
    directory.ensure_builtin_bondson_directory()
    from services.settings_service import save_report_settings_form
    save_report_settings_form(dict(lab_name="检验实验室", department_name="检验科", qc_owner_name=ACTOR,
        reviewer_name="复核员", report_statement="本报告根据已录入的质控数据生成，供室内质控归档与复核使用。"))
    db.save_app_settings(dict(data_generation_provenance=json.dumps(dict(kind="synthetic", seed=SEED, generated_by="build_teaching_demo.py", source="confirmed product catalogue and official reagent references"))))
    makers, instruments, methods = equipment()
    plans = []
    for s in SPECS:
        plans.append(setup_project(s, makers, instruments, methods))
        print("Configured", s["code"], flush=True)
    sync_and_verify(plans)
    rng = random.Random(SEED)
    for plan in plans:
        seed_results(plan, rng)
        print("Recorded", plan["code"], len(plan["receipts"]), flush=True)
    rule_scenarios = seed_rule_gallery(plans)
    append_instant_scale_projects(plans)
    (out / "core-ready.json").write_text(json.dumps(dict(plans=plans, rule_scenarios=rule_scenarios), ensure_ascii=False, indent=2), encoding="utf-8")
    print("CORE READY", db.DB_PATH, flush=True)
    finish(plans, rule_scenarios, out)


def finish(plans, rule_scenarios, out):
    progress_path = out / "finish-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    def stage(name, build):
        if name not in progress:
            progress[name] = build()
            progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return progress[name]
    events = stage("events", lambda: make_events(plans, out))
    lifecycle_demo = stage("lifecycle", lambda: seed_lifecycle(plans))
    z_building = stage("z_building", lambda: seed_z_building(plans))
    from services.instant_service import confirm_instant_transfer_to_lj
    transfer_plan = next(p for p in plans if p["code"] == "NG")
    transfer = stage("transfer", lambda: confirm_instant_transfer_to_lj(transfer_plan["bindings"][0]["runtime_batch_id"]))
    jobs = stage("monthly_jobs", lambda: reports(plans, out))
    live_files = stage("live_entry", lambda: prepare_live_files(plans, out))
    clean_plans = [{k: v for k, v in p.items() if k not in ("receipts", "scenario_receipt", "retest_receipt", "systems")} for p in plans]
    manifest = dict(seed=SEED, data_provenance={"kind": "synthetic", "description": "人工构造的功能展示资料；并非厂家实测或患者检验数据。"}, notice=NOTICE, sample_date="2026-09-28", presentation_date="2026-09-29", plans=clean_plans,
        verification_status="pending_final_checks",
        events=events, monthly_jobs=jobs, lifecycle=lifecycle_demo, z_building=z_building, instant_transfer=transfer, rule_scenarios=rule_scenarios, live_entry=live_files,
        limitations=["项目容器当前限定一个质控产品；20个目标按20个项目及临床分组组织，未伪造20目标复合材料。",
          "同目标不同目录编号仍是不同产品；HBV、EBV及RSVA多水平使用明确标识的制备水平，非厂家新增规格或赋值。",
          "HBV同一L1以5000 IU/mL、Log10约3.7及Ct31.6三方法；原品5×10^5 IU/mL分别按1:100、1:10及原浓度制备。",
          "HbA1c双水平对应目录真实组合编号IQC-BI-05903；批号为本地建立的记录编号。",
          "Ct/Log工作台监测数值信号及质控规则，不自动判定阳性/阴性。",
          "报告覆盖当前已实现的LJ/Z正式期；即时法需转入LJ后方可生成正式期月报。"],
        sources=dict(bondson="data/catalogs/bondson_2026_09_28.json；用户已确认原始目录", sansure=SOURCE_URL,
          instrument="https://www.hongshitech.com/cpzx", alt="https://diagnostics.roche.com/global/en/products/lab/altl-ifcc-with-or-without-pyridoxal-phosphate-activation-cps-000021.html",
          tg="https://diagnostics.roche.com/global/en/products/lab/trigl-cps-000263.html",
          hba1c="https://www.bio-rad.com/webroot/web/pdf/cdg/solutions/Hemoglobin%20A1c/doc_A-210.pdf"))
    manifest["method_scale_matrix"] = [dict(method=m, scale=s, projects=[p.get("project_name", p["code"] + " · " + p["name"])
        for p in plans if any(v["method"] == m and v["scale"] == s for v in p["variants"])])
        for m in ("lj", "zscore", "instant") for s in ("raw", "ct", "log")]
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result = validate(plans, out, events)
    manifest["verification_status"] = "passed"
    for name, payload in [("manifest.json", manifest), ("verification.json", result)]:
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    from services.storage_service import create_database_backup
    backup = create_database_backup(out / "restore-point")
    manifest["backup"] = dict(path=str(backup.target_path), sha256=hashlib.sha256(backup.target_path.read_bytes()).hexdigest())
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(dict(database=str(db.DB_PATH), counts=result["counts"], reports=result["report_count"], backup=str(backup.target_path)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
