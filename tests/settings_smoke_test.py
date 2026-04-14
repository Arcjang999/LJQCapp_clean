from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import init_db
from pages.main_page import INSTANT_ENTRY_LABEL, LJ_ENTRY_LABEL, MAIN_ENTRY_LABEL, ZSCORE_ENTRY_LABEL
from services.settings_service import get_report_settings


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
    style_markup = " ".join(str(item.value) for item in at.markdown)
    assert '[data-testid="stSidebarNav"]' in style_markup
    assert "WATERMARK_TEXT: 本软件由邦德盛开发，该版本仅供演示或试用。" in style_markup
    assert "data:image/svg+xml;base64" in style_markup
    assert "LJQCApp" not in style_markup
    assert "Quality Control" not in style_markup


def test_global_navigation_and_watermark_apply_to_top_level_pages() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH)
        at.run()

        assert not list(at.exception)
        _assert_global_navigation_and_watermark(at)

        for entry_label in [MAIN_ENTRY_LABEL, LJ_ENTRY_LABEL, ZSCORE_ENTRY_LABEL, INSTANT_ENTRY_LABEL]:
            at.radio(key="top_level_method_selector").set_value(entry_label).run()
            assert not list(at.exception)
            _assert_global_navigation_and_watermark(at)


def test_global_settings_entry_save_and_reopen() -> None:
    with TemporaryDatabaseContext():
        at = AppTest.from_file(APP_FILE_PATH)
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

        reopened_at = AppTest.from_file(APP_FILE_PATH)
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


if __name__ == "__main__":
    test_global_navigation_and_watermark_apply_to_top_level_pages()
    test_global_settings_entry_save_and_reopen()
    print("settings_smoke_test passed")
