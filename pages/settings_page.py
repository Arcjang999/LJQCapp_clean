from __future__ import annotations

import streamlit as st

from services.settings_service import (
    REPORT_SETTINGS_FALLBACKS,
    ReportSettings,
    get_report_settings,
    save_report_settings_form,
)
from services.storage_service import (
    choose_backup_file_via_dialog,
    choose_directory_via_dialog,
    create_database_backup,
    get_database_location_status,
    migrate_database_to_directory,
    open_folder_in_system,
    restore_database_from_backup_file,
    validate_directory_writable,
    validate_sqlite_database,
)
from ui.common import render_section_intro, render_workbench_context_bar


SETTINGS_FORM_FIELD_MAP = {
    "lab_name": "settings_lab_name",
    "department_name": "settings_department_name",
    "qc_owner_name": "settings_qc_owner_name",
    "reviewer_name": "settings_reviewer_name",
    "report_statement": "settings_report_statement",
}
STORAGE_SESSION_KEYS = {
    "migration_dir": "settings_storage_selected_migration_dir",
    "restore_file": "settings_storage_selected_restore_file",
    "restore_confirmed": "settings_storage_restore_confirmed",
}


def render_settings_page() -> None:
    current_settings = get_report_settings()
    if bool(st.session_state.get("refresh_settings_form", False)):
        _hydrate_settings_form_state(current_settings, force=True)
        st.session_state["refresh_settings_form"] = False
    else:
        _hydrate_settings_form_state(current_settings, force=False)

    action_column, _ = st.columns([0.22, 0.78], gap="small")
    with action_column:
        if st.button("返回当前页面", key="close_settings_page", use_container_width=True):
            st.session_state["show_settings_page"] = False
            st.rerun()

    render_section_intro(
        title="系统设置",
        caption="本页统一维护报告默认信息，以及数据库存储位置迁移、手动备份与恢复。",
        eyebrow="全局入口",
        badges=["报告默认信息", "数据存储与备份"],
        tone="accent",
    )
    render_workbench_context_bar(
        title="当前配置摘要",
        caption="LJ 月报与 Z-score 月报都会优先读取这里保存的默认信息；空值会在报告侧自动回退到安全默认值。",
        items=[
            ("实验室名称", current_settings.lab_name or "未填写"),
            ("科室名称", current_settings.department_name or "未填写"),
            ("质控负责人", current_settings.qc_owner_name or "未填写"),
            ("审核人", current_settings.reviewer_name or "未填写"),
            ("声明默认值", "已配置" if current_settings.report_statement else "将回退到系统默认声明"),
        ],
        badges=["不进入主导航", "影响 LJ / Z-score 月报"],
    )

    saved_settings: ReportSettings | None = None
    st.markdown("**实验室与科室信息**")
    info_left, info_right = st.columns(2, gap="large")
    with info_left:
        st.text_input("实验室名称", key=SETTINGS_FORM_FIELD_MAP["lab_name"])
        st.text_input("质控负责人", key=SETTINGS_FORM_FIELD_MAP["qc_owner_name"])
    with info_right:
        st.text_input("科室名称", key=SETTINGS_FORM_FIELD_MAP["department_name"])
        st.text_input("审核人", key=SETTINGS_FORM_FIELD_MAP["reviewer_name"])

    st.markdown("**报告固定声明**")
    st.text_area(
        "报告声明正文",
        key=SETTINGS_FORM_FIELD_MAP["report_statement"],
        height=160,
    )
    if st.button("保存设置", key="save_system_settings", type="primary", use_container_width=True):
        saved_settings = save_report_settings_form(
            {
                field_name: st.session_state[widget_key]
                for field_name, widget_key in SETTINGS_FORM_FIELD_MAP.items()
            }
        )
        st.session_state["refresh_settings_form"] = True
        st.session_state["settings_saved_notice"] = (
            "系统设置已保存，后续生成的 LJ / Z-score 月报会优先使用这些默认信息。"
        )
        st.rerun()

    if saved_settings is not None:
        current_settings = saved_settings

    saved_notice = str(st.session_state.pop("settings_saved_notice", "") or "").strip()
    if saved_notice:
        st.success(saved_notice)

    st.caption(
        "空值策略：实验室名称、科室名称、质控负责人、审核人为空时，报告会显示“未填写”；"
        "声明为空时，报告会自动回退到系统默认声明。"
    )
    with st.expander("当前报告回退默认值", expanded=False):
        st.write(f"实验室名称：{REPORT_SETTINGS_FALLBACKS['lab_name']}")
        st.write(f"科室名称：{REPORT_SETTINGS_FALLBACKS['department_name']}")
        st.write(f"质控负责人：{REPORT_SETTINGS_FALLBACKS['qc_owner_name']}")
        st.write(f"审核人：{REPORT_SETTINGS_FALLBACKS['reviewer_name']}")
        st.write(f"报告声明：{REPORT_SETTINGS_FALLBACKS['report_statement']}")

    st.divider()
    _render_storage_section()


def _hydrate_settings_form_state(settings: ReportSettings, *, force: bool) -> None:
    for field_name, widget_key in SETTINGS_FORM_FIELD_MAP.items():
        if force or widget_key not in st.session_state:
            st.session_state[widget_key] = getattr(settings, field_name)


def _render_storage_section() -> None:
    status = get_database_location_status()
    render_section_intro(
        title="数据存储与备份",
        caption="数据库位置配置保存在业务数据库之外；迁移与恢复成功后都需要重启应用后生效。",
        eyebrow="数据安全",
        badges=["原生目录/文件选择窗口", "不做在线热切换"],
        tone="default",
    )
    render_workbench_context_bar(
        title="当前数据库位置",
        caption=status.status_text,
        items=[
            ("当前数据库文件", str(status.db_path)),
            ("当前数据库目录", str(status.db_dir)),
            ("路径配置文件", str(status.config_path)),
            ("默认备份目录", str(status.default_backup_dir)),
        ],
        badges=[
            "已使用外部路径配置" if status.configured_db_path is not None else "使用默认数据库路径",
            "SQLite 校验通过" if status.is_valid_sqlite or not status.exists else "数据库需检查",
        ],
    )

    st.markdown("**当前数据库路径**")
    st.code(str(status.db_path))
    st.markdown("**当前数据库所在目录**")
    st.code(str(status.db_dir))
    action_left, action_right = st.columns(2, gap="small")
    with action_left:
        if st.button("打开数据库所在文件夹", key="open_db_folder", use_container_width=True):
            try:
                open_folder_in_system(status.db_dir)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.success("已调用系统资源管理器打开数据库所在目录。")
    with action_right:
        if st.button("打开默认备份目录", key="open_default_backup_dir", use_container_width=True):
            try:
                status.default_backup_dir.mkdir(parents=True, exist_ok=True)
                open_folder_in_system(status.default_backup_dir)
            except (RuntimeError, OSError) as exc:
                st.error(str(exc))
            else:
                st.success("已调用系统资源管理器打开默认备份目录。")

    if not status.exists:
        st.warning("当前数据库文件尚未生成；执行迁移、备份或恢复前，系统会先初始化当前数据库。")
    elif not status.is_readable:
        st.warning("当前数据库文件存在但无法读取，请先检查文件权限或目录可用性。")
    elif not status.is_valid_sqlite:
        st.warning(status.status_text)
    else:
        st.caption(f"当前数据库大小：{status.size_bytes} 字节")

    st.markdown("**更换数据库存储目录**")
    st.caption("点击“选择新目录”后会弹出系统原生目录选择窗口；迁移成功后请立即重启应用。")
    if st.button("选择新目录", key="pick_storage_migration_dir", use_container_width=True):
        try:
            selected_dir = choose_directory_via_dialog(
                initial_dir=status.db_dir,
                title="选择新的数据库存储目录",
            )
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            if selected_dir is not None:
                st.session_state[STORAGE_SESSION_KEYS["migration_dir"]] = str(selected_dir)

    selected_migration_dir = _read_optional_path(STORAGE_SESSION_KEYS["migration_dir"])
    migration_ready = False
    if selected_migration_dir is not None:
        st.write("已选择的新目录：")
        st.code(str(selected_migration_dir))
        migration_validation = validate_directory_writable(selected_migration_dir, create_if_missing=False)
        if migration_validation[0]:
            st.success("目标目录校验通过。")
            migration_ready = True
        else:
            st.warning(migration_validation[1])

    if st.button(
        "确认迁移数据库",
        key="confirm_storage_migration",
        type="primary",
        use_container_width=True,
        disabled=not migration_ready,
    ):
        try:
            result = migrate_database_to_directory(selected_migration_dir)
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            st.success(result.message)
            if result.config_path is not None:
                st.caption(f"数据库路径配置已写入：{result.config_path}")

    st.markdown("**立即备份数据库**")
    st.caption(f"默认备份目录：{status.default_backup_dir}")
    backup_left, backup_right = st.columns(2, gap="small")
    with backup_left:
        if st.button("立即备份到默认目录", key="backup_default_dir", use_container_width=True):
            try:
                result = create_database_backup()
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.success(result.message)
                st.code(str(result.target_path))
    with backup_right:
        if st.button("选择备份目录并立即备份", key="backup_custom_dir", use_container_width=True):
            try:
                selected_backup_dir = choose_directory_via_dialog(
                    initial_dir=status.default_backup_dir,
                    title="选择数据库备份保存目录",
                )
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                if selected_backup_dir is None:
                    st.info("已取消备份目录选择。")
                else:
                    try:
                        result = create_database_backup(selected_backup_dir)
                    except RuntimeError as exc:
                        st.error(str(exc))
                    else:
                        st.success(result.message)
                        st.code(str(result.target_path))

    st.markdown("**从备份恢复数据库**")
    st.caption("恢复会覆盖当前数据库内容；系统会在恢复前自动生成一份保护性备份。")
    if st.button("选择备份文件", key="pick_restore_backup_file", use_container_width=True):
        try:
            selected_backup_file = choose_backup_file_via_dialog(
                initial_dir=status.default_backup_dir,
                title="选择要恢复的数据库备份文件",
            )
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            if selected_backup_file is not None:
                st.session_state[STORAGE_SESSION_KEYS["restore_file"]] = str(selected_backup_file)
                st.session_state[STORAGE_SESSION_KEYS["restore_confirmed"]] = False

    selected_restore_file = _read_optional_path(STORAGE_SESSION_KEYS["restore_file"])
    restore_ready = False
    if selected_restore_file is not None:
        st.write("已选择的备份文件：")
        st.code(str(selected_restore_file))
        restore_validation = validate_sqlite_database(selected_restore_file)
        if restore_validation[0]:
            st.success("备份文件校验通过。")
            restore_ready = True
        else:
            st.warning(restore_validation[1])

    st.checkbox(
        "我已知晓恢复会覆盖当前数据库内容，且需要在恢复后重启应用。",
        key=STORAGE_SESSION_KEYS["restore_confirmed"],
        disabled=selected_restore_file is None,
    )
    restore_confirmed = bool(st.session_state.get(STORAGE_SESSION_KEYS["restore_confirmed"], False))
    if st.button(
        "确认恢复数据库",
        key="confirm_restore_database",
        type="primary",
        use_container_width=True,
        disabled=not (restore_ready and restore_confirmed),
    ):
        try:
            result = restore_database_from_backup_file(selected_restore_file)
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            st.success(result.message)
            if result.protection_backup_path is not None:
                st.caption(f"保护性备份：{result.protection_backup_path}")


def _read_optional_path(session_key: str) -> Path | None:
    raw_value = str(st.session_state.get(session_key, "") or "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
