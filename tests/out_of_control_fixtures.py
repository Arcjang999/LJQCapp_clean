"""T1-01 contract examples, NOT an implemented event service or clinical oracle.

Standard-library only; does not import application code or open any database.
Expected outcomes are fixed specifications for subsequent integration tests.
Run directly to validate fixture integrity and the four card scenarios.
"""
from copy import deepcopy
from datetime import datetime
import json


def source(method, source_id, levels, *, system_id=11, context_id=101,
           evaluation_id=201, classification="reject", instrument="仪器A"):
    return {
        "source_type": "lj_result" if method == "lj" else "zscore_run",
        "qc_method": method, "source_id": source_id, "record_type": "routine",
        "origin_context_id": context_id, "origin_evaluation_id": evaluation_id,
        "system_id": system_id, "project_name": "IgG", "instrument": instrument,
        "phase": "formal", "test_time": "2026-09-22 09:00:00",
        "input_value_type": "raw", "unit": "g/L",
        "classification": classification, "rule_names": ["1_3s"],
        "levels": levels, "manual_note": "", "missing_fields": [],
    }


def level(order, value, status, lot):
    return {"level_id": f"L{order}", "level_order": order, "value": value,
            "classification": status, "actual_qc_lot": lot,
            "rule_names": ["1_3s"] if status == "reject" else []}


LJ = source("lj", 1, [level(1, 14, "reject", "QC-A")])
Z2 = source("zscore", 1, [level(1, 8, "accept", "QC-L"),
                          level(2, 24, "reject", "QC-H")],
            context_id=102, evaluation_id=202)
Z3 = source("zscore", 2, [level(1, 8, "accept", "QC-L"),
                          level(2, 18, "reject", "QC-M"),
                          level(3, 32, "reject", "QC-H")],
            context_id=103, evaluation_id=203)
WARNING = source("lj", 2, [level(1, 12.5, "warning", "QC-A")],
                 context_id=104, evaluation_id=204, classification="warning")
WARNING["rule_names"] = WARNING["levels"][0]["rule_names"] = ["1_2s"]
WARNING["manual_note"] = "已处理，复查正常（仅旧手动备注）"
MISSING = source("lj", 3, [level(1, 14, "reject", None)],
                 system_id=None, context_id=None, evaluation_id=None)
MISSING["missing_fields"] = ["origin_context_id", "origin_evaluation_id", "system_id",
                              "levels.0.actual_qc_lot", "quality_snapshot", "target_profile_id"]
OTHER = source("lj", 4, [level(1, 8, "accept", "QC-B")], system_id=12,
               context_id=105, evaluation_id=205, classification="accept", instrument="仪器B")
OTHER["test_time"] = "2026-09-22 10:00:00"

EVENT_CASES = [
    {"name": "LJ_IGG_A", "source": LJ, "open_requests": 2,
     "expected": {"event_count": 1, "level_count": 1, "before_open_candidates": 1,
                  "before_open_events": 0, "status": "pending"}},
    {"name": "Z2_HIGH", "source": Z2, "open_requests": 1,
     "expected": {"event_count": 1, "level_count": 2, "status": "pending"}},
    {"name": "Z3_TWO_REJECT", "source": Z3, "open_requests": 2,
     "expected": {"event_count": 1, "level_count": 3, "status": "pending"}},
    {"name": "WARNING_NOTE", "source": WARNING, "explicit_warning_action": True,
     "expected": {"event_count": 1, "without_explicit_action": 0, "level_count": 1,
                  "classification": "warning", "status": "pending"}},
    {"name": "MISSING_SOURCE", "source": MISSING,
     "expected": {"event_count": 1, "level_count": 1, "status": "pending",
                  "missing_display": "未记录", "retest_match": "unknown"}},
]

RETEST_CASES = [
    {"name": "SAME_NAME_OTHER_INSTRUMENT", "original": MISSING, "candidate": OTHER,
     "expected": "unknown"},
    {"name": "KNOWN_DIFFERENT_SYSTEM", "original": LJ, "candidate": OTHER,
     "expected": "reject_system"},
]
for name, stamp, expected in [
    ("SAME_SYSTEM_LATER", "2026-09-22 10:00:00", "eligible_with_difference_reason"),
    ("SAME_TIME", "2026-09-22 09:00:00", "reject_time"),
    ("EARLIER_TIME", "2026-09-22 08:00:00", "reject_time"),
]:
    candidate = deepcopy(OTHER)
    candidate.update(system_id=11, instrument="仪器A", test_time=stamp)
    candidate["target_profile_id"] = 302
    original = deepcopy(LJ)
    original["target_profile_id"] = 301
    RETEST_CASES.append({"name": name, "original": original, "candidate": candidate,
                         "differences": ["levels.0.actual_qc_lot", "target_profile_id"],
                         "expected": expected})

VERSION_CASE = {
    "name": "EVALUATION_V2", "source_ref": ["zscore_run", 2], "event_id": "fixture-event-3",
    "origin_evaluation_id": 203,
    "evaluations": [{"id": 203, "previous_id": None, "classification": "reject"},
                    {"id": 206, "previous_id": 203, "classification": "accept"}],
    "handling_history": [{"revision_no": 1, "status": "pending"},
                         {"revision_no": 2, "status": "in_progress"}],
    "expected": {"event_count": 1, "origin_evaluation_id": 203,
                 "latest_evaluation_id": 206, "status": "in_progress",
                 "preserved_handling_versions": [1, 2]},
}
REQUEST_CASES = [
    {"name": "RETRY", "saved_request": ["req-1", "payload-A", 1],
     "incoming": ["req-1", "payload-A", 1], "current_version": 2,
     "expected": "same_receipt_version_2"},
    {"name": "REUSED_KEY_CHANGED_PAYLOAD", "saved_request": ["req-1", "payload-A", 1],
     "incoming": ["req-1", "payload-B", 1], "current_version": 2,
     "expected": "reject_request_payload_conflict"},
    {"name": "STALE_WINDOW", "saved_request": ["req-1", "payload-A", 1],
     "incoming": ["req-2", "payload-B", 1], "current_version": 2,
     "expected": "reject_revision_conflict"},
]
STATE_CASES = [
    {"name": "RETURN_FOR_EDIT", "from": "pending_confirmation", "to": "in_progress",
     "reason": "补充效果依据", "expected": "new_revision"},
    {"name": "RETURN_MISSING_REASON", "from": "pending_confirmation", "to": "in_progress",
     "reason": "", "expected": "reject_missing_reason"},
    {"name": "COMPLETED_SUPPLEMENT", "from": "completed", "to": "in_progress",
     "reason": "补充复测说明", "expected": "new_revision_preserve_completed_history"},
    {"name": "NO_AUTOMATIC_COMPLETION", "from": "in_progress", "later_result": "accept",
     "expected": "in_progress"},
    {"name": "NO_RETEST_YET", "from": "in_progress", "retests": [], "effect_evidence": "",
     "expected": "in_progress"},
]


def validate_fixtures():
    """Check the supplied examples, not the behavior of an unbuilt service."""
    assert len({c["name"] for c in EVENT_CASES}) == 5
    for case in EVENT_CASES:
        src = case["source"]
        assert len(src["levels"]) == case["expected"]["level_count"]
        assert len({l["level_id"] for l in src["levels"]}) == len(src["levels"])
        assert (src["source_type"], src["qc_method"]) in {
            ("lj_result", "lj"), ("zscore_run", "zscore")}
        assert case["expected"]["event_count"] == 1
        assert case["expected"]["status"] == "pending"
    # A1: two rejected levels, but a single run identity with ALL three levels.
    assert sum(l["classification"] == "reject" for l in Z3["levels"]) == 2
    assert EVENT_CASES[2]["expected"] == {"event_count": 1, "level_count": 3, "status": "pending"}
    # Distinct source tables may legitimately share numeric IDs.
    assert LJ["source_id"] == Z2["source_id"]
    assert LJ["source_type"] != Z2["source_type"]
    # A2: names cannot repair missing system identity, known different IDs also fail.
    missing_case, mismatch_case = RETEST_CASES[:2]
    assert missing_case["original"]["project_name"] == missing_case["candidate"]["project_name"]
    assert missing_case["original"]["system_id"] is None and missing_case["expected"] == "unknown"
    assert mismatch_case["original"]["system_id"] != mismatch_case["candidate"]["system_id"]
    assert mismatch_case["expected"] == "reject_system"
    for case in RETEST_CASES[2:]:
        a, b = case["original"], case["candidate"]
        assert a["system_id"] == b["system_id"]
        later = datetime.fromisoformat(b["test_time"]) > datetime.fromisoformat(a["test_time"])
        assert later == (case["expected"] == "eligible_with_difference_reason")
        assert a["levels"][0]["actual_qc_lot"] != b["levels"][0]["actual_qc_lot"]
        assert a["target_profile_id"] != b["target_profile_id"]
    # A3: free text cannot promote warning to rejection or completion.
    assert WARNING["manual_note"] and WARNING["rule_names"] == ["1_2s"]
    assert EVENT_CASES[3]["expected"]["without_explicit_action"] == 0
    assert EVENT_CASES[3]["expected"]["classification"] == "warning"
    assert all(MISSING[k] is None for k in ("system_id", "origin_context_id", "origin_evaluation_id"))
    # A4: separate original and latest references with preserved handling history.
    assert VERSION_CASE["evaluations"][1]["previous_id"] == VERSION_CASE["origin_evaluation_id"]
    assert VERSION_CASE["expected"]["origin_evaluation_id"] != VERSION_CASE["expected"]["latest_evaluation_id"]
    assert [r["revision_no"] for r in VERSION_CASE["handling_history"]] == [1, 2]
    assert VERSION_CASE["expected"]["status"] == "in_progress"
    assert REQUEST_CASES[0]["saved_request"] == REQUEST_CASES[0]["incoming"]
    assert REQUEST_CASES[1]["saved_request"][0] == REQUEST_CASES[1]["incoming"][0]
    assert REQUEST_CASES[1]["saved_request"][1] != REQUEST_CASES[1]["incoming"][1]
    assert REQUEST_CASES[2]["incoming"][2] < REQUEST_CASES[2]["current_version"]
    assert STATE_CASES[0]["reason"] and not STATE_CASES[1]["reason"]
    assert STATE_CASES[2]["reason"] and STATE_CASES[2]["to"] == "in_progress"
    return {"scope": "contract_fixture_integrity_only", "card_scenarios": ["A1", "A2", "A3", "A4"],
            "event_examples": len(EVENT_CASES), "retest_examples": len(RETEST_CASES),
            "version_examples": 1, "request_examples": len(REQUEST_CASES),
            "state_examples": len(STATE_CASES), "database_opened": False,
            "application_started": False, "result": "passed"}


if __name__ == "__main__":
    print(json.dumps(validate_fixtures(), ensure_ascii=False, indent=2))
