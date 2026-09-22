from __future__ import annotations

import ast
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import init_db
from pages.main_page import INSTANT_ENTRY_LABEL, LJ_ENTRY_LABEL, MAIN_ENTRY_LABEL, ZSCORE_ENTRY_LABEL
from services.settings_service import get_report_settings
from ui.common import GLOBAL_PAGE_WATERMARK_TEXT


APP_FILE_PATH = str(PROJECT_ROOT / "app.py")


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "settings_smoke_test.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def _assert_global_navigation_and_watermark(at: AppTest) -> None:
    assert st.get_option("client.showSidebarNavigation") is False
    assert st.get_option("client.showErrorDetails") == "none"
    style_markup = " ".join(str(item.value) for item in at.markdown)
    assert '[data-testid="stSidebarNav"]' in style_markup
    assert "WATERMARK_TEXT:" in style_markup
    assert GLOBAL_PAGE_WATERMARK_TEXT in style_markup
    assert "body::before" in style_markup
    assert ".stApp::before" not in style_markup
    assert ".stApp::after" not in style_markup
    assert '[data-testid="stAppViewContainer"]::before' not in style_markup
    assert '[data-testid="stAppViewContainer"]::after' not in style_markup
    assert "data:image/svg+xml;charset=utf-8," in style_markup
    assert "position: fixed" in style_markup
    assert "inset: 0" in style_markup
    assert "pointer-events: none" in style_markup
    assert "background-color: transparent" in style_markup
    assert "background-repeat: repeat" in style_markup
    assert "background-size:" in style_markup
    assert "visibility: hidden !important" not in style_markup
    assert "opacity: 0 !important" not in style_markup
    assert "opacity:0!important" not in style_markup
    assert "opacity: 1 !important" in style_markup
    assert "visibility: visible !important" in style_markup


def test_global_navigation_and_watermark_apply_to_top_level_pages() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH, default_timeout=15)
        at.run()

        assert not list(at.exception)
        _assert_global_navigation_and_watermark(at)
        assert not at.get("code")

        for entry_label in [MAIN_ENTRY_LABEL, LJ_ENTRY_LABEL, ZSCORE_ENTRY_LABEL, INSTANT_ENTRY_LABEL]:
            at.radio(key="top_level_method_selector").set_value(entry_label).run()
            assert not list(at.exception)
            _assert_global_navigation_and_watermark(at)


def test_global_settings_entry_save_and_reopen() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH, default_timeout=15)
        at.run()

        assert not list(at.exception)
        _assert_global_navigation_and_watermark(at)
        navigation = at.radio(key="top_level_method_selector")
        assert "系统设置" not in list(navigation.options)

        at.button(key="open_system_settings").click().run()
        assert not list(at.exception)
        _assert_global_navigation_and_watermark(at)

        at.text_input(key="settings_lab_name").set_value("星城医学实验室")
        at.text_input(key="settings_department_name").set_value("分子诊断中心")
        at.text_input(key="settings_qc_owner_name").set_value("张质控")
        at.text_input(key="settings_reviewer_name").set_value("李审核")
        at.text_area(key="settings_report_statement").set_value("本报告仅供系统设置联动验证使用。")
        at.button(key="save_system_settings").click().run()

        assert not list(at.exception)
        assert any("系统设置已保存" in str(item.value) for item in at.success)

        saved = get_report_settings()
        assert saved.lab_name == "星城医学实验室"
        assert saved.department_name == "分子诊断中心"
        assert saved.qc_owner_name == "张质控"
        assert saved.reviewer_name == "李审核"
        assert saved.report_statement == "本报告仅供系统设置联动验证使用。"

        reopened_at = AppTest.from_file(APP_FILE_PATH, default_timeout=15)
        reopened_at.run()
        assert not list(reopened_at.exception)
        reopened_at.button(key="open_system_settings").click().run()
        assert not list(reopened_at.exception)
        _assert_global_navigation_and_watermark(reopened_at)
        assert reopened_at.text_input(key="settings_lab_name").value == "星城医学实验室"
        assert reopened_at.text_input(key="settings_department_name").value == "分子诊断中心"
        assert reopened_at.text_input(key="settings_qc_owner_name").value == "张质控"
        assert reopened_at.text_input(key="settings_reviewer_name").value == "李审核"
        assert reopened_at.text_area(key="settings_report_statement").value == "本报告仅供系统设置联动验证使用。"


def test_storage_failure_displays_an_action_without_internal_details() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH, default_timeout=15).run()
        at.button(key="open_system_settings").click().run()
        assert not at.get("code")
        with patch("pages.settings_page.choose_directory_via_dialog",
                   side_effect=RuntimeError("无法加载系统文件选择窗口，请检查本机 tkinter 环境。")):
            at.button(key="pick_storage_migration_dir").click().run()
        assert not list(at.exception)
        errors = [str(item.value) for item in at.error]
        assert errors == ["无法打开文件选择窗口，请重新打开软件后重试。"]
        assert at.button(key="confirm_storage_migration").disabled

        with patch("pages.settings_page.validate_sqlite_database",
                   return_value=(False, "无法打开 SQLite 数据库：/missing/backup.db")):
            at.session_state["settings_storage_selected_restore_file"] = "/missing/backup.db"
            at.run()
        assert not list(at.exception)
        assert any("无法打开数据文件" in str(item.value) for item in at.warning)
        assert not any("SQLite" in str(item.value) for item in at.warning)
        assert at.button(key="confirm_restore_database").disabled


def test_business_pages_do_not_render_tracebacks_or_default_language_code() -> None:
    files = [PROJECT_ROOT / "app.py", *sorted((PROJECT_ROOT / "pages").glob("*.py")),
             *sorted((PROJECT_ROOT / "ui").glob("*.py"))]
    leaks = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "st":
                continue
            if node.func.attr == "exception":
                leaks.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: traceback")
            elif node.func.attr == "code":
                language = next((keyword.value for keyword in node.keywords if keyword.arg == "language"), None)
                if language is None and len(node.args) > 1:
                    language = node.args[1]
                if not isinstance(language, ast.Constant) or language.value not in (None, "text", "plain", "plaintext"):
                    leaks.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: code language")
    assert not leaks, "User-facing technical details: " + "; ".join(leaks)


if __name__ == "__main__":
    test_global_navigation_and_watermark_apply_to_top_level_pages()
    test_global_settings_entry_save_and_reopen()
    test_storage_failure_displays_an_action_without_internal_details()
    test_business_pages_do_not_render_tracebacks_or_default_language_code()
    print("settings_smoke_test passed")
