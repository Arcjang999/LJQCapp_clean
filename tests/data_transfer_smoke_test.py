"""Unified transfer keeps phase, complete runs, frozen lots and atomic imports."""
from datetime import date
from pathlib import Path
import sys
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import add_result, create_batch, create_project, get_results, read_snapshot
from services.export_utils import dataframe_to_csv_bytes, dataframe_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.value_type_service import build_level_measurement_label, get_measurement_label
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.zscore_v12_fixtures import create_configured_zscore_batch
from tests.lj_v12_integration_smoke_test import _seed_active_lj_configuration
from services.project_config_service import set_lot_config_disabled
from services.workbench_config_service import sync_lj_workbench_bindings
from ui.data_transfer import (
    PHASE_LABELS, _fresh_import_context, _import_source_signature,
    build_chart_png, build_data_export, commit_data_import, review_data_import,
)
from import_review import build_lj_building_template_dataframe, build_zscore_building_template_dataframe


def batch_for(method):
    if method == "zscore":
        return create_configured_zscore_batch(name="数据传输多水平", target_n=5)[1]
    project = create_project("数据传输单水平", input_value_type="raw")
    return create_batch(project_id=project, instrument="仪器", reagent="试剂", qc_material="质控品",
                        concentration="L1", lot_no="QC-01", target_n=5)


def file_for(method, count=5, *, start=1, lot="", level_count=2, input_value_type="raw"):
    template = (build_lj_building_template_dataframe(input_value_type) if method == "lj" else
                build_zscore_building_template_dataframe(level_count, input_value_type))
    rows = []
    for index in range(count):
        row = {column: "" for column in template.columns}
        row.update({"检测时间": f"2026-09-{start+index:02d} 08:00:00", "检测人": "检验员",
                    "备注": "已核对", "实际试剂批号": lot})
        measurements = ([get_measurement_label(input_value_type)] if method == "lj" else
                        [build_level_measurement_label(f"Level {level}", input_value_type) for level in range(1, level_count+1)])
        for level, column in enumerate(measurements):
            row[column] = 100 + 50 * level + (index % 3 - 1) * 0.2
        rows.append(row)
    return pd.DataFrame(rows, columns=template.columns)


def toolbar_page(batch_id):
    from ui.data_transfer import render_transfer_toolbar, _fresh_import_context
    context = _fresh_import_context("lj", batch_id)
    context["input_value_type_label"] = "真实检测值"
    render_transfer_toolbar("lj", context, batch_id,
                            {"view_mode": "全图", "y_axis_mode": "标准视图", "standard_sd_limit": 4.0})


class DataTransferTests(unittest.TestCase):
    def test_both_methods_import_two_phases_and_export_whole_history(self):
        for method in ("lj", "zscore"):
            with self.subTest(method=method), IsolatedDatabase():
                batch = batch_for(method)
                context = _fresh_import_context(method, batch)
                building = review_data_import(method, context, batch, "building", dataframe_to_csv_bytes(file_for(method)))
                self.assertFalse(building["summary"]["has_blocking_errors"], building["issues"])
                self.assertTrue(all(row["lot_selection"]["allow_unknown"] for row in building["normalized_rows"]))
                self.assertEqual(5, commit_data_import(method, batch, "building", building, _import_source_signature(method, context)))
                context = _fresh_import_context(method, batch)
                formal = review_data_import(method, context, batch, "formal", dataframe_to_xlsx_bytes(file_for(method, 1, start=6)))
                self.assertFalse(formal["summary"]["has_blocking_errors"], formal["issues"])
                self.assertEqual(1, commit_data_import(method, batch, "formal", formal, _import_source_signature(method, context)))
                exported = build_data_export(method, _fresh_import_context(method, batch), "all")
                self.assertEqual(6, len(exported))
                self.assertEqual(2, exported["阶段"].nunique())
                self.assertIn("实际试剂批号", exported)
                if method == "zscore":
                    columns = [build_level_measurement_label(f"Level {level}", "raw") for level in (1, 2)]
                    self.assertTrue(all(column in exported for column in columns))
                    self.assertTrue(exported[columns].notna().all().all())
                excel = xlsx_bytes_to_dataframes(dataframe_to_xlsx_bytes(exported))
                self.assertEqual(6, len(next(iter(excel.values()))))

    def test_formal_guard_incomplete_levels_and_invalid_actual_lot_survive(self):
        for method in ("lj", "zscore"):
            with self.subTest(method=method), IsolatedDatabase():
                batch = batch_for(method)
                context = _fresh_import_context(method, batch)
                review = review_data_import(method, context, batch, "formal", dataframe_to_csv_bytes(file_for(method, 1)))
                self.assertTrue(review["summary"]["has_blocking_errors"])
                review = review_data_import(method, context, batch, "building", dataframe_to_csv_bytes(file_for(method, 1, lot="未验证批号")))
                self.assertTrue(review["summary"]["has_blocking_errors"])
                self.assertTrue(any(issue["field_name"] == "实际试剂批号" and issue["is_blocking"] for issue in review["issues"]))
                if method == "zscore":
                    data = file_for(method, 1).drop(columns=build_level_measurement_label("Level 2", "raw"))
                    incomplete = review_data_import(method, context, batch, "building", dataframe_to_csv_bytes(data))
                    self.assertTrue(incomplete["summary"]["has_blocking_errors"])
                mixed = file_for(method, 1).assign(阶段="正式期")
                invalid = review_data_import(method, context, batch, "building", dataframe_to_csv_bytes(mixed))
                self.assertTrue(invalid["summary"]["has_blocking_errors"])

    def test_stale_preview_and_late_lot_failure_do_not_partially_import(self):
        with IsolatedDatabase():
            batch = batch_for("lj")
            current = _fresh_import_context("lj", batch)
            signature = _import_source_signature("lj", current)
            review = review_data_import("lj", current, batch, "building", dataframe_to_csv_bytes(file_for("lj", 2)))
            review["normalized_rows"][1]["lot_selection"]["expected_revision"] = 999
            with self.assertRaises(ValueError):
                commit_data_import("lj", batch, "building", review, signature)
            self.assertEqual(0, len(get_results(batch)))
            review = review_data_import("lj", current, batch, "building", dataframe_to_csv_bytes(file_for("lj", 1)))
            self.assertEqual(1, commit_data_import("lj", batch, "building", review, signature))
            with self.assertRaisesRegex(ValueError, "变化"):
                commit_data_import("lj", batch, "building", review, signature)
            self.assertEqual(1, len(get_results(batch)))

    def test_png_is_redrawn_and_date_selection_requires_formal_data(self):
        with IsolatedDatabase():
            batch = batch_for("lj")
            context = _fresh_import_context("lj", batch)
            review = review_data_import("lj", context, batch, "building", dataframe_to_csv_bytes(file_for("lj")))
            commit_data_import("lj", batch, "building", review, _import_source_signature("lj", context))
            context = _fresh_import_context("lj", batch)
            context["input_value_type_label"] = "真实检测值"
            png = build_chart_png("lj", context, {"view_mode": "全图", "y_axis_mode": "标准视图"})
            self.assertTrue(png.startswith(b"\x89PNG"))
            with self.assertRaisesRegex(ValueError, "没有可导出的质控数据"):
                build_chart_png("lj", context, {}, start_date=date(2026, 9, 1), end_date=date(2026, 10, 1))

    def test_three_level_ct_and_log_exports_and_zscore_png(self):
        from zscore_logic import build_zscore_plot_dataframe
        for value_type in ("ct", "log"):
            with self.subTest(value_type=value_type), IsolatedDatabase():
                batch = create_configured_zscore_batch(name="三水平" + value_type, target_n=5,
                                                       level_count=3, input_value_type=value_type)[1]
                context = _fresh_import_context("zscore", batch)
                for phase, count, start in (("building", 5, 1), ("formal", 1, 6)):
                    data = dataframe_to_csv_bytes(file_for("zscore", count, start=start,
                                                            level_count=3, input_value_type=value_type))
                    review = review_data_import("zscore", context, batch, phase, data)
                    self.assertFalse(review["summary"]["has_blocking_errors"], review["issues"])
                    commit_data_import("zscore", batch, phase, review, _import_source_signature("zscore", context))
                    context = _fresh_import_context("zscore", batch)
                exported = build_data_export("zscore", context, "all")
                columns = [build_level_measurement_label(f"Level {level}", value_type) for level in (1, 2, 3)]
                self.assertEqual(6, len(exported))
                self.assertTrue(exported[columns].notna().all().all())
                context.update(plot_df=build_zscore_plot_dataframe(context["history_runs"], None, display_phase=None),
                               input_value_type_label=get_measurement_label(value_type))
                chart = dict(view_mode="单水平视图", selected_level="Level 3", phase_scope="formal",
                             current_view_label="水平3", y_axis_mode="标准视图", figure=object())
                self.assertTrue(build_chart_png("zscore", context, chart).startswith(b"\x89PNG"))
                self.assertTrue(build_chart_png("zscore", context, chart, start_date=date(2026, 9, 6),
                                                end_date=date(2026, 9, 6)).startswith(b"\x89PNG"))

    def test_export_dialog_has_one_download_and_mutually_exclusive_types(self):
        with IsolatedDatabase():
            batch = batch_for("lj")
            app = AppTest.from_function(toolbar_page, args=(batch,), default_timeout=15).run()
            app.button(key=f"qc_transfer_open_export_lj_{batch}").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertEqual(1, len(app.get("download_button")))
            state = app.session_state["qc_transfer_dialog"]
            app.radio(key=f"qc_transfer_kind_{state['nonce']}").set_value("质控图").run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertEqual(1, len(app.get("download_button")))
            self.assertFalse(any(radio.label == "检测范围" for radio in app.radio))
            app.button(key=f"qc_transfer_close_{state['nonce']}").click().run()
            self.assertNotIn("qc_transfer_dialog", app.session_state.filtered_state)

    def test_toolbar_only_two_buttons_and_stage_switch_clears_preview(self):
        with IsolatedDatabase():
            batch = batch_for("lj")
            app = AppTest.from_function(toolbar_page, args=(batch,), default_timeout=15).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(["导入数据", "导出"], [button.label for button in app.button])
            app.session_state["qc_record_dialog"] = {"id": 1}
            app.run()
            self.assertTrue(all(button.disabled for button in app.button))
            app.session_state["qc_record_dialog"] = None
            app.run()
            app.button(key=f"qc_transfer_open_import_lj_{batch}").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            state = app.session_state["qc_transfer_dialog"]
            self.assertEqual("building", state["phase"])
            self.assertEqual(1, len(app.get("file_uploader")))
            state["upload"] = dataframe_to_csv_bytes(file_for("lj", 1))
            state["upload_name"] = "记录.csv"
            app.run()
            app.button(key=f"qc_transfer_review_{state['nonce']}").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertFalse(app.button(key=f"qc_transfer_confirm_{state['nonce']}").disabled)
            app.button(key=f"qc_transfer_cancel_{state['nonce']}").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(app.session_state["qc_transfer_dialog"]["discard"])
            self.assertEqual(0, len(get_results(batch)))
            app.button(key="qc_transfer_keep").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(app.session_state["qc_transfer_dialog"]["upload"])
            self.assertFalse(app.button(key=f"qc_transfer_confirm_{state['nonce']}").disabled)
            app.radio(key=f"qc_transfer_phase_{state['nonce']}").set_value("formal").run()
            self.assertFalse(list(app.exception), list(app.exception))
            switched = app.session_state["qc_transfer_dialog"]
            self.assertEqual("formal", switched["phase"])
            self.assertFalse(switched["upload"])
            self.assertIsNone(switched["review"])
            self.assertTrue(app.button(key=f"qc_transfer_confirm_{state['nonce']}").disabled)

    def test_external_disable_keeps_import_cancel_and_export_close_reachable(self):
        from zscore_logic import create_zscore_run
        for method in ("lj", "zscore"):
            for mode in ("import", "export"):
                with self.subTest(method=method, mode=mode), IsolatedDatabase():
                    if method == "lj":
                        config_id = _seed_active_lj_configuration()
                        sync_lj_workbench_bindings()
                        with read_snapshot() as connection:
                            binding = dict(connection.execute(
                                "SELECT * FROM qc_workbench_bindings WHERE qc_method='lj' AND lot_config_id=?",
                                (config_id,)).fetchone())
                    else:
                        batch = create_configured_zscore_batch(name="外部停用导入导出", target_n=5)[1]
                        with read_snapshot() as connection:
                            binding = dict(connection.execute(
                                "SELECT * FROM qc_workbench_bindings WHERE qc_method='zscore' AND runtime_batch_id=?",
                                (batch,)).fetchone())
                        config_id = binding["lot_config_id"]
                    batch = int(binding["runtime_batch_id"])
                    if method == "lj":
                        add_result(batch, "2026-09-01 08:00:00", 100.0, operator="检验员")
                    else:
                        create_zscore_run(batch_id=batch, test_time="2026-09-01 08:00:00", operator="检验员",
                            template_id="2_level_classic", required_n=5,
                            level_results=[{"level_id": "Level 1", "raw_value": 100.0},
                                           {"level_id": "Level 2", "raw_value": 150.0}])
                    with read_snapshot() as connection:
                        table = "results" if method == "lj" else "zscore_runs"
                        original_ids = [row[0] for row in connection.execute(f"SELECT id FROM {table} ORDER BY id")]
                    app = AppTest.from_string(
                        f"from pages.{method}_page import render_{method}_page\nrender_{method}_page()",
                        default_timeout=25)
                    prefix = "" if method == "lj" else "zscore_"
                    app.session_state[prefix + "selected_project_id"] = int(binding["runtime_project_id"])
                    app.session_state[prefix + "selected_batch_id"] = batch
                    app.run()
                    app.button(key=f"qc_transfer_open_{mode}_{method}_{batch}").click().run()
                    self.assertFalse(list(app.exception), list(app.exception))
                    state = app.session_state["qc_transfer_dialog"]
                    nonce = state["nonce"]
                    if mode == "import":
                        data = dataframe_to_csv_bytes(file_for(method, 1, start=2))
                        state.update(upload=data, upload_name="待导入.csv")
                        app.run()
                        app.button(key=f"qc_transfer_review_{nonce}").click().run()
                        self.assertFalse(list(app.exception), list(app.exception))
                        self.assertFalse(app.button(key=f"qc_transfer_confirm_{nonce}").disabled)

                    set_lot_config_disabled(config_id, is_disabled=True, reason="另一会话停止使用此批次")
                    app.run()
                    self.assertFalse(list(app.exception), list(app.exception))
                    self.assertIsNone(app.session_state[prefix + "selected_batch_id"])
                    self.assertEqual((method, batch),
                        (app.session_state["qc_transfer_dialog"]["method"], app.session_state["qc_transfer_dialog"]["batch_id"]))
                    if mode == "import":
                        self.assertEqual(data, app.session_state["qc_transfer_dialog"]["upload"])
                        app.button(key=f"qc_transfer_cancel_{nonce}").click().run()
                        self.assertTrue(app.session_state["qc_transfer_dialog"]["discard"])
                        app.button(key="qc_transfer_keep").click().run()
                        self.assertFalse(list(app.exception), list(app.exception))
                        self.assertEqual(data, app.session_state["qc_transfer_dialog"]["upload"])
                        app.button(key=f"qc_transfer_cancel_{nonce}").click().run()
                        app.button(key="qc_transfer_discard").click().run()
                    else:
                        self.assertEqual(1, len(app.get("download_button")))
                        app.button(key=f"qc_transfer_close_{nonce}").click().run()
                    self.assertFalse(list(app.exception), list(app.exception))
                    self.assertNotIn("qc_transfer_dialog", app.session_state.filtered_state)
                    with read_snapshot() as connection:
                        self.assertEqual(original_ids,
                            [row[0] for row in connection.execute(f"SELECT id FROM {table} ORDER BY id")])

    def test_storage_failure_keeps_import_file_and_preview_without_partial_rows(self):
        from database import atomic_write
        with IsolatedDatabase():
            batch = batch_for('lj')
            app = AppTest.from_function(toolbar_page, args=(batch,), default_timeout=15).run()
            app.button(key=f'qc_transfer_open_import_lj_{batch}').click().run()
            state = app.session_state['qc_transfer_dialog']
            current = _fresh_import_context('lj', batch)
            content = dataframe_to_csv_bytes(file_for('lj', 2))
            state['upload'], state['upload_name'] = content, '检测.csv'
            state['review'] = review_data_import('lj', current, batch, 'building', content)
            state['source_signature'] = _import_source_signature('lj', current)
            app.run()
            with atomic_write() as connection:
                connection.execute("CREATE TRIGGER reject_import BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT,'storage unavailable'); END")
            app.button(key=f"qc_transfer_confirm_{state['nonce']}").click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(any('尚未保存' in str(item.value) for item in app.error))
            current_state = app.session_state['qc_transfer_dialog']
            self.assertEqual(content, current_state['upload'])
            self.assertTrue(current_state['review']['normalized_rows'])
            self.assertTrue(get_results(batch).empty)


if __name__ == "__main__":
    unittest.main()
