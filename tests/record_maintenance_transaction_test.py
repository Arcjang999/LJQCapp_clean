"""Record maintenance must retain evidence and roll back every related write."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.instant_service import confirm_instant_transfer_to_lj, save_instant_result
from services.record_maintenance_service import get_record_context, save_record_action
from services.workbench_config_service import sync_lj_workbench_bindings
from tests.instant_smoke_test import TemporaryDatabaseContext
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.lj_v12_integration_smoke_test import _seed_active_lj_configuration
from tests.zscore_v12_fixtures import seed_zscore_configuration
from zscore_logic import create_zscore_run, get_template_id_for_level_count


METHODS = ("lj", "zscore", "instant")


def dump_database():
    """Include originals, derived snapshots, evaluations, events and sequences."""
    with db.read_snapshot() as connection:
        return "\n".join(connection.iterdump())


def seed_batch(method, count=4):
    """Use the existing confirmed upstream fixtures, with only needed records."""
    if method == "lj":
        config_id = _seed_active_lj_configuration()
        sync_lj_workbench_bindings()
        with db.read_snapshot() as connection:
            batch_id = int(connection.execute(
                "SELECT runtime_batch_id FROM qc_workbench_bindings "
                "WHERE qc_method='lj' AND lot_config_id=?", (config_id,)
            ).fetchone()[0])
    elif method == "zscore":
        batch_id = int(seed_zscore_configuration(
            name="记录维护三水平", level_count=3, target_n=5
        )["batch_id"])
    else:
        batch_id = int(seed_instant_configuration(name="记录维护即时法")["batch_id"])

    ids = []
    for index in range(count):
        time = f"2026-09-29 08:{index:02d}:00"
        value = 100.0 + (0.0, 0.1, -0.1, 0.2, -0.2)[index % 5]
        operator = f"检测人{index + 1}"
        if method == "lj":
            ids.append(db.add_result(batch_id, time, value, operator=operator))
        elif method == "zscore":
            run = create_zscore_run(batch_id=batch_id, test_time=time, operator=operator,
                template_id=get_template_id_for_level_count(3), required_n=5,
                level_results=[{"level_id": f"Level {level}", "raw_value": value * level}
                               for level in (1, 2, 3)])
            ids.append(int(run["id"]))
        else:
            ids.append(int(save_instant_result(batch_id=batch_id, test_time=time,
                operator=operator, value=value, log_value=None)))
    return batch_id, ids


def context(method, batch_id, record_id):
    return get_record_context(method, record_id, batch_id)


def input_evidence(record):
    fields = ("id", "batch_id", "test_time", "operator", "value", "log_value",
              "manual_note", "reagent_lot_changed")
    evidence = {key: record[key] for key in fields if key in record}
    if "level_results" in record:
        evidence["levels"] = [{key: level[key] for key in
            ("id", "level_id", "raw_value", "log_value")}
            for level in record["level_results"]]
    return evidence


def edit_changes(method, record):
    changes = {"test_time": record["test_time"], "operator": "维护检测人",
               "manual_note": "核对原始仪器记录后更正"}
    if method == "lj":
        changes.update(value=record["value"] + 0.03, reagent_lot_changed=1)
    else:
        changes["level_values"] = {
            level["level_id"]: level["raw_value"] + 0.03
            for level in record.get("level_results", [])}
    return changes


def audit_rows():
    with db.read_snapshot() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM qc_record_maintenance_events ORDER BY id")]


def result_evaluation_rows(method, record_id):
    column = {"lj": "lj_result_id", "zscore": "zscore_run_id",
              "instant": "instant_result_id"}[method]
    with db.read_snapshot() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT e.* FROM qc_result_evaluations e JOIN qc_result_contexts c "
            f"ON c.id=e.context_id WHERE c.{column}=? ORDER BY e.id", (record_id,))]


class RecordMaintenanceTransactionTests(unittest.TestCase):
    def assert_status(self, method, record, included, manual_status):
        rows = record["level_results"] if method == "zscore" else [record]
        inclusion = "is_effective" if method == "instant" else "is_building_included"
        self.assertEqual(3 if method == "zscore" else 1, len(rows))
        for row in rows:
            self.assertEqual(int(included), row[inclusion])
            self.assertEqual(manual_status, row["manual_status"])
            if "handled_at" in row:
                self.assertTrue(row["handled_at"])

    def test_reason_is_required_before_any_action_write(self):
        for method in METHODS:
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                current = context(method, batch_id, ids[1])
                before = dump_database()
                for action in ("keep", "disable", "restore", "edit"):
                    with self.subTest(action=action), self.assertRaisesRegex(ValueError, "维护原因"):
                        save_record_action(method, batch_id, ids[1], action, reason=" \n\t ",
                            expected_fingerprint=current["fingerprint"], request_key=f"missing-{action}",
                            changes=edit_changes(method, current["record"]))
                    self.assertEqual(before, dump_database())

    def test_status_actions_affect_only_selected_id_and_retain_before_after_evidence(self):
        for method in METHODS:
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                selected_id = ids[1]
                original = context(method, batch_id, selected_id)["record"]
                neighbors = {rid: context(method, batch_id, rid)["fingerprint"]
                             for rid in ids if rid != selected_id}
                events = []
                for action, included, status in (("disable", False, "disabled"),
                                                  ("restore", True, "restored"),
                                                  ("keep", True, "keep")):
                    before_context = context(method, batch_id, selected_id)
                    event_id = save_record_action(method, batch_id, selected_id, action,
                        reason=f"  {action}：已核对原始记录  ",
                        expected_fingerprint=before_context["fingerprint"], request_key=f"{method}-{action}")
                    after_context = context(method, batch_id, selected_id)
                    self.assert_status(method, after_context["record"], included, status)
                    self.assertEqual(input_evidence(original), input_evidence(after_context["record"]))
                    for record_id, fingerprint in neighbors.items():
                        self.assertEqual(fingerprint, context(method, batch_id, record_id)["fingerprint"])
                    event = audit_rows()[-1]
                    self.assertEqual(event_id, event["id"])
                    self.assertEqual((method, batch_id, selected_id, action),
                        (event["qc_method"], event["batch_id"], event["record_id"], event["action"]))
                    self.assertEqual(f"{action}：已核对原始记录", event["reason"])
                    self.assertEqual(before_context["record"], json.loads(event["before_json"]))
                    self.assertEqual(after_context["record"], json.loads(event["after_json"]))
                    self.assertTrue(event["created_at"])
                    events.append(action)
                    self.assertEqual(list(reversed(events)), [row["action"] for row in after_context["history"]])
                self.assertEqual(3, len(audit_rows()))

    def test_replayed_request_returns_existing_event_without_duplicate_writes(self):
        for method in METHODS:
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                selected = context(method, batch_id, ids[1])
                arguments = dict(reason="核对后禁用", expected_fingerprint=selected["fingerprint"],
                                 request_key="one-request")
                first_id = save_record_action(method, batch_id, ids[1], "disable", **arguments)
                after = dump_database()
                # A retry carries the old fingerprint because the first write already succeeded.
                second_id = save_record_action(method, batch_id, ids[1], "disable", **arguments)
                self.assertEqual(first_id, second_id)
                self.assertEqual(after, dump_database())
                self.assertEqual(1, len(audit_rows()))
                with self.assertRaisesRegex(ValueError, "维护对象已变化"):
                    save_record_action(method, batch_id, ids[2], "disable", **arguments)
                self.assertEqual(after, dump_database())

    def test_new_request_with_stale_fingerprint_is_rejected_without_writes(self):
        for method in METHODS:
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                selected = context(method, batch_id, ids[1])
                save_record_action(method, batch_id, ids[1], "disable", reason="核对后禁用",
                    expected_fingerprint=selected["fingerprint"], request_key="first-request")
                after = dump_database()
                with self.assertRaisesRegex(ValueError, "检测记录已被修改"):
                    save_record_action(method, batch_id, ids[1], "restore", reason="未刷新旧窗口",
                        expected_fingerprint=selected["fingerprint"], request_key="fresh-request")
                self.assertEqual(after, dump_database())

    def test_context_and_action_reject_wrong_batch_without_writes(self):
        for method in METHODS:
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                current = context(method, batch_id, ids[1])
                before = dump_database()
                with self.assertRaisesRegex(ValueError, "当前批次不一致"):
                    get_record_context(method, ids[1], batch_id + 999)
                with self.assertRaisesRegex(ValueError, "当前批次不一致"):
                    save_record_action(method, batch_id + 999, ids[1], "disable", reason="批次错配",
                        expected_fingerprint=current["fingerprint"], request_key="wrong-batch")
                self.assertEqual(before, dump_database())

    def test_building_edits_persist_reason_and_all_original_changes(self):
        for method in ("lj", "zscore"):
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method)
                selected = context(method, batch_id, ids[1])
                self.assertTrue(selected["edit_allowed"])
                changes = edit_changes(method, selected["record"])
                neighbors = {rid: input_evidence(context(method, batch_id, rid)["record"])
                             for rid in ids if rid != ids[1]}
                previous_evaluations = result_evaluation_rows(method, ids[1])
                event_id = save_record_action(method, batch_id, ids[1], "edit", reason="仪器记录核对更正",
                    expected_fingerprint=selected["fingerprint"], request_key="edit-request", changes=changes)
                current = context(method, batch_id, ids[1])
                self.assertNotEqual(selected["fingerprint"], current["fingerprint"])
                self.assertEqual(changes["operator"], current["record"]["operator"])
                self.assertEqual(changes["manual_note"], current["record"]["manual_note"])
                if method == "lj":
                    self.assertEqual(changes["value"], current["record"]["value"])
                    self.assertEqual(1, current["record"]["reagent_lot_changed"])
                else:
                    self.assertEqual(changes["level_values"], {
                        level["level_id"]: level["raw_value"] for level in current["record"]["level_results"]})
                for rid, evidence in neighbors.items():
                    self.assertEqual(evidence, input_evidence(context(method, batch_id, rid)["record"]))
                event = audit_rows()[0]
                self.assertEqual(event_id, event["id"])
                self.assertEqual(selected["record"], json.loads(event["before_json"]))
                self.assertEqual(current["record"], json.loads(event["after_json"]))
                self.assertEqual("仪器记录核对更正", event["reason"])
                current_evaluations = result_evaluation_rows(method, ids[1])
                self.assertGreater(len(current_evaluations), len(previous_evaluations))
                self.assertEqual(previous_evaluations, current_evaluations[:len(previous_evaluations)])

    def test_instant_original_edit_is_rejected_without_writes(self):
        with TemporaryDatabaseContext():
            batch_id, ids = seed_batch("instant")
            selected = context("instant", batch_id, ids[1])
            self.assertFalse(selected["edit_allowed"])
            before = dump_database()
            with self.assertRaisesRegex(ValueError, "仅支持记录状态维护"):
                save_record_action("instant", batch_id, ids[1], "edit", reason="不应支持原值覆盖",
                    expected_fingerprint=selected["fingerprint"], request_key="instant-edit", changes={})
            self.assertEqual(before, dump_database())

    def test_zscore_edit_requires_complete_level_set(self):
        with TemporaryDatabaseContext():
            batch_id, ids = seed_batch("zscore")
            selected = context("zscore", batch_id, ids[1])
            changes = edit_changes("zscore", selected["record"])
            changes["level_values"].pop("Level 3")
            before = dump_database()
            with self.assertRaisesRegex(ValueError, "全部水平"):
                save_record_action("zscore", batch_id, ids[1], "edit", reason="遗漏一个水平",
                    expected_fingerprint=selected["fingerprint"], request_key="missing-level", changes=changes)
            self.assertEqual(before, dump_database())

    def test_formal_lj_and_zscore_reject_edit_and_status_for_formal_and_prior_building(self):
        for method in ("lj", "zscore"):
            with self.subTest(method=method), TemporaryDatabaseContext():
                batch_id, ids = seed_batch(method, count=6)
                before = dump_database()
                for record_id in (ids[0], ids[-1]):
                    selected = context(method, batch_id, record_id)
                    self.assertFalse(selected["edit_allowed"])
                    for action in ("edit", "keep", "disable", "restore"):
                        with self.subTest(record_id=record_id, action=action), self.assertRaises(ValueError):
                            save_record_action(method, batch_id, record_id, action, reason="正式期锁定检查",
                                expected_fingerprint=selected["fingerprint"],
                                request_key=f"formal-{record_id}-{action}",
                                changes=edit_changes(method, selected["record"]))
                        self.assertEqual(before, dump_database())
                self.assertEqual([], audit_rows())

    def test_transferred_instant_rejects_every_status_action_without_writes(self):
        with TemporaryDatabaseContext():
            batch_id, ids = seed_batch("instant", count=20)
            confirm_instant_transfer_to_lj(batch_id)
            before = dump_database()
            for record_id in (ids[0], ids[-1]):
                selected = context("instant", batch_id, record_id)
                for action in ("keep", "disable", "restore"):
                    with self.subTest(record_id=record_id, action=action), self.assertRaises(ValueError):
                        save_record_action("instant", batch_id, record_id, action, reason="转入后应冻结",
                            expected_fingerprint=selected["fingerprint"], request_key=f"transferred-{record_id}-{action}")
                    self.assertEqual(before, dump_database())
            self.assertEqual([], audit_rows())

    def test_audit_insert_failure_rolls_back_originals_snapshots_and_evaluations(self):
        for method in METHODS:
            actions = ("disable", "restore", "keep", "edit") if method != "instant" else ("disable", "restore", "keep")
            for action in actions:
                with self.subTest(method=method, action=action), TemporaryDatabaseContext():
                    batch_id, ids = seed_batch(method)
                    if action == "restore":
                        selected = context(method, batch_id, ids[1])
                        save_record_action(method, batch_id, ids[1], "disable", reason="先禁用供恢复验证",
                            expected_fingerprint=selected["fingerprint"], request_key="setup-disable")
                    selected = context(method, batch_id, ids[1])
                    self.assertTrue(result_evaluation_rows(method, ids[1]))
                    with db.atomic_write() as connection:
                        connection.execute("""CREATE TRIGGER fail_maintenance_audit
                            BEFORE INSERT ON qc_record_maintenance_events
                            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END""")
                    before = dump_database()
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "injected audit failure"):
                        save_record_action(method, batch_id, ids[1], action, reason="审计失败必须全部回滚",
                            expected_fingerprint=selected["fingerprint"], request_key="failing-request",
                            changes=edit_changes(method, selected["record"]))
                    self.assertEqual(before, dump_database())
                    self.assertEqual(selected["record"], context(method, batch_id, ids[1])["record"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
