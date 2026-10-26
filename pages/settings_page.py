from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory

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
    validate_backup_file,
)
from ui.common import render_compact_stat_metrics, render_section_intro, render_workbench_context_bar
from ui.user_messages import storage_message


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


def _is_cloud_mode() -> bool:
    return os.environ.get("LJQC_DEPLOYMENT_MODE", "").strip().lower() == "cloud"


def _format_settings_path_summary(path: Path | None, *, tail_parts: int = 2) -> str:
    if path is None:
        return "-"

    path_text = str(path)
    if len(path_text) <= 44:
        return path_text

    anchor = path.anchor.rstrip("\\/")
    remainder_parts = list(path.parts[1:] if anchor else path.parts)
    if len(remainder_parts) <= tail_parts:
        return path_text

    separator = "\\" if "\\" in path_text else "/"
    suffix = separator.join(remainder_parts[-tail_parts:])
    return f"{anchor}{separator}...{separator}{suffix}" if anchor else f"...{separator}{suffix}"



def render_settings_page() -> None:
    current_settings = get_report_settings()
    if bool(st.session_state.get("refresh_settings_form", False)):
        _hydrate_settings_form_state(current_settings, force=True)
        st.session_state["refresh_settings_form"] = False
    else:
        _hydrate_settings_form_state(current_settings, force=False)

    action_column, _ = st.columns([0.28, 0.72], gap="small")
    with action_column:
        if st.button("返回当前页面", key="close_settings_page", use_container_width=True):
            st.session_state["show_settings_page"] = False
            st.rerun()

    from ui.common import render_module_header
    render_module_header(
        title="系统设置",
        caption="填写实验室、科室和人员，保存设置；数据备份和恢复在下方操作。",
        tone="settings",
    )
    render_workbench_context_bar(
        title="当前实验室信息",
        caption="下方填写单位、检验员和复核员，点“保存设置”。之后生成的报告使用这些信息。",
        items=[
            ("实验室名称", current_settings.lab_name or "未填写"),
            ("科室名称", current_settings.department_name or "未填写"),
            ("检验员", current_settings.qc_owner_name or "未填写"),
            ("复核员", current_settings.reviewer_name or "未填写"),
            ("报告声明", "已填写" if current_settings.report_statement else "使用默认声明"),
        ],
    )

    with st.container():
        render_section_intro(
            title="实验室信息",
            caption="填写实验室、科室、检验员、复核员和报告声明，点“保存设置”。",
            tone="accent",
        )
        info_left, info_right = st.columns(2, gap="medium")
        with info_left:
            st.text_input("实验室名称", key=SETTINGS_FORM_FIELD_MAP["lab_name"])
            st.text_input("检验员", key=SETTINGS_FORM_FIELD_MAP["qc_owner_name"])
        with info_right:
            st.text_input("科室名称", key=SETTINGS_FORM_FIELD_MAP["department_name"])
            st.text_input("复核员", key=SETTINGS_FORM_FIELD_MAP["reviewer_name"])

        st.markdown("**报告固定声明**")
        st.text_area(
            "报告声明正文",
            key=SETTINGS_FORM_FIELD_MAP["report_statement"],
            height=180,
        )
        if st.button("保存设置", key="save_system_settings", type="primary", use_container_width=True):
            save_report_settings_form(
                {
                    field_name: st.session_state[widget_key]
                    for field_name, widget_key in SETTINGS_FORM_FIELD_MAP.items()
                }
            )
            st.session_state["refresh_settings_form"] = True
            st.session_state["settings_saved_notice"] = (
                "设置已保存，下次生成报告时使用这些单位和人员信息。"
            )
            st.rerun()

        saved_notice = str(st.session_state.pop("settings_saved_notice", "") or "").strip()
        if saved_notice:
            st.success(saved_notice)

        st.caption(
            "未填写时：实验室名称、科室名称、检验员、复核员为空时，报告会显示“未填写”；"
            "报告声明为空时，报告会使用默认声明。"
        )
        with st.expander("查看未填写时的报告内容", expanded=False):
            st.write(f"实验室名称：{REPORT_SETTINGS_FALLBACKS['lab_name']}")
            st.write(f"科室名称：{REPORT_SETTINGS_FALLBACKS['department_name']}")
            st.write(f"检验员：{REPORT_SETTINGS_FALLBACKS['qc_owner_name']}")
            st.write(f"复核员：{REPORT_SETTINGS_FALLBACKS['reviewer_name']}")
            st.write(f"报告声明：{REPORT_SETTINGS_FALLBACKS['report_statement']}")

    with st.container():
        from ui.lab_preparation import render_lab_preparation_entry
        render_section_intro(
            title='单位资料准备',
            caption='选用：下载填写表，一次填写实验室、仪器、试剂和质控品，再上传保存。也可到各管理页面逐项登记。',
            tone='muted',
        )
        render_lab_preparation_entry()

    with st.container():
        cloud_mode = _is_cloud_mode()
        render_section_intro(
            title="数据保存与备份",
            caption=("下载完整备份，或上传备份恢复数据。" if cloud_mode else
                     "查看或更改数据保存位置，以及备份和恢复数据。"),
            badges=(["下载备份", "上传恢复"] if cloud_mode else
                    ["更改数据保存位置", "备份恢复", "重启后生效"]),
            tone="muted",
        )
        _render_storage_section()


def _hydrate_settings_form_state(settings: ReportSettings, *, force: bool) -> None:
    for field_name, widget_key in SETTINGS_FORM_FIELD_MAP.items():
        if force or widget_key not in st.session_state:
            st.session_state[widget_key] = getattr(settings, field_name)


def _render_storage_section() -> None:
    if _is_cloud_mode():
        _render_cloud_storage_section()
        return
    status = get_database_location_status()
    render_workbench_context_bar(
        title="当前数据保存位置",
        caption=storage_message(status.status_text),
        items=[
            ("当前数据文件", _format_settings_path_summary(status.db_path)),
            ("当前数据文件夹", _format_settings_path_summary(status.db_dir)),
            ("默认备份目录", _format_settings_path_summary(status.default_backup_dir)),
        ],
        badges=[
            "自选保存位置" if status.configured_db_path is not None else "默认保存位置",
            "数据文件正常" if status.is_valid_sqlite or not status.exists else "数据文件需检查",
        ],
    )

    location_col, action_col = st.columns([1.1, 0.9], gap="medium")
    with location_col:
        render_compact_stat_metrics(
            [
                ("数据文件", str(status.db_path.name)),
                ("文件读取", "可读取" if status.is_readable or not status.exists else "需检查"),
                ("数据文件状态", "有效" if status.is_valid_sqlite or not status.exists else "异常"),
                ("文件大小", f"{status.size_bytes} 字节" if status.exists else "-"),
            ]
        )
        with st.expander("查看路径详情", expanded=False):
            st.markdown("**当前数据文件路径**")
            st.text(str(status.db_path))
            st.markdown("**当前数据文件夹**")
            st.text(str(status.db_dir))
            st.markdown("**默认备份目录**")
            st.text(str(status.default_backup_dir))

    with action_col:
        if st.button("打开数据文件夹", key="open_db_folder", use_container_width=True):
            try:
                open_folder_in_system(status.db_dir)
            except RuntimeError as exc:
                st.error(storage_message(exc))
            else:
                st.success("已打开数据文件夹。")
        if st.button("打开默认备份目录", key="open_default_backup_dir", use_container_width=True):
            try:
                status.default_backup_dir.mkdir(parents=True, exist_ok=True)
                open_folder_in_system(status.default_backup_dir)
            except (RuntimeError, OSError) as exc:
                st.error(storage_message(exc))
            else:
                st.success("已打开备份文件夹。")

    if not status.exists:
        st.warning("首次保存、更改位置或备份时会建立数据文件。")
    elif not status.is_readable:
        st.warning("请检查数据文件夹是否可访问，或请电脑管理员协助开放读取权限。")
    elif not status.is_valid_sqlite:
        st.warning(storage_message(status.status_text))

    st.markdown("**更改数据保存位置**")
    st.caption("选择新的数据保存文件夹，完成更改后请重新打开软件。")
    migration_left, migration_right = st.columns([1.1, 0.9], gap="medium")
    with migration_left:
        if st.button("选择新文件夹", key="pick_storage_migration_dir", use_container_width=True):
            try:
                selected_dir = choose_directory_via_dialog(
                    initial_dir=status.db_dir,
                    title="选择新的数据保存文件夹",
                )
            except RuntimeError as exc:
                st.error(storage_message(exc))
            else:
                if selected_dir is not None:
                    st.session_state[STORAGE_SESSION_KEYS["migration_dir"]] = str(selected_dir)

        selected_migration_dir = _read_optional_path(STORAGE_SESSION_KEYS["migration_dir"])
        migration_ready = False
        if selected_migration_dir is not None:
            st.write("新的数据文件夹：")
            st.text(str(selected_migration_dir))
            migration_validation = validate_directory_writable(selected_migration_dir, create_if_missing=False)
            if migration_validation[0]:
                st.success("此文件夹可以保存数据。")
                migration_ready = True
            else:
                st.warning(storage_message(migration_validation[1]))
        else:
            st.info('请先点击“选择新文件夹”，选择可写的数据文件夹，再确认更改保存位置。')
        if st.button(
            "确认更改保存位置",
            key="confirm_storage_migration",
            type="primary",
            use_container_width=True,
            disabled=not migration_ready,
        ):
            try:
                result = migrate_database_to_directory(selected_migration_dir)
            except RuntimeError as exc:
                st.error(storage_message(exc))
            else:
                st.success(storage_message(result.message))
    with migration_right:
        st.info("更改保存位置后，现有项目和检测记录会一起保留。")
        st.caption("建议更改保存位置前先备份，完成后按提示重新打开软件。")

    st.markdown("**数据备份**")
    st.caption("项目资料、检测记录、处理附件和已保存报告一同备份。可保存到默认备份文件夹，也可另选文件夹。")
    backup_left, backup_right = st.columns(2, gap="medium")
    with backup_left:
        if st.button("立即备份到默认目录", key="backup_default_dir", use_container_width=True):
            try:
                result = create_database_backup()
            except RuntimeError as exc:
                st.error(storage_message(exc))
            else:
                st.success(storage_message(result.message))
                st.text(str(result.target_path))
    with backup_right:
        if st.button("选择备份目录并立即备份", key="backup_custom_dir", use_container_width=True):
            try:
                selected_backup_dir = choose_directory_via_dialog(
                    initial_dir=status.default_backup_dir,
                    title="选择备份保存文件夹",
                )
            except RuntimeError as exc:
                st.error(storage_message(exc))
            else:
                if selected_backup_dir is None:
                    st.info("已取消文件夹选择。")
                else:
                    try:
                        result = create_database_backup(selected_backup_dir)
                    except RuntimeError as exc:
                        st.error(storage_message(exc))
                    else:
                        st.success(storage_message(result.message))
                        st.text(str(result.target_path))

    st.markdown("**从备份恢复数据**")
    st.warning("恢复会用备份中的资料替换当前数据。恢复前会自动备份当前数据，请妥善保留。")
    if st.button("选择备份文件", key="pick_restore_backup_file", use_container_width=True):
        try:
            selected_backup_file = choose_backup_file_via_dialog(
                initial_dir=status.default_backup_dir,
                title="选择要恢复的备份文件",
            )
        except RuntimeError as exc:
            st.error(storage_message(exc))
        else:
            if selected_backup_file is not None:
                st.session_state[STORAGE_SESSION_KEYS["restore_file"]] = str(selected_backup_file)
                st.session_state[STORAGE_SESSION_KEYS["restore_confirmed"]] = False

    selected_restore_file = _read_optional_path(STORAGE_SESSION_KEYS["restore_file"])
    restore_ready = False
    if selected_restore_file is not None:
        st.write("已选择的备份文件：")
        st.text(str(selected_restore_file))
        restore_validation = validate_backup_file(selected_restore_file)
        if restore_validation[0]:
            st.success(restore_validation[1])
            restore_ready = True
        else:
            st.warning(storage_message(restore_validation[1]))

    st.checkbox(
        "我已知晓恢复会覆盖当前数据，并且需要在恢复后重新打开软件。",
        key=STORAGE_SESSION_KEYS["restore_confirmed"],
        disabled=selected_restore_file is None,
    )
    restore_confirmed = bool(st.session_state.get(STORAGE_SESSION_KEYS["restore_confirmed"], False))
    if selected_restore_file is None:
        st.info('请先点击“选择备份文件”，通过检查后勾选上方确认项，再点击“确认恢复数据”。')
    elif restore_ready and not restore_confirmed:
        st.info('备份文件已通过检查。请勾选上方确认项，再点击“确认恢复数据”。')
    if st.button(
        "确认恢复数据",
        key="confirm_restore_database",
        type="primary",
        use_container_width=True,
        disabled=not (restore_ready and restore_confirmed),
    ):
        try:
            result = restore_database_from_backup_file(selected_restore_file)
        except RuntimeError as exc:
            st.error(storage_message(exc))
        else:
            st.success(storage_message(result.message))
            if result.protection_backup_path is not None:
                st.caption(f"恢复前的数据备份：{result.protection_backup_path}")


def _render_cloud_storage_section() -> None:
    notice = st.session_state.pop("settings_cloud_restore_notice", "")
    if notice:
        st.success(notice)

    st.markdown("**数据备份**")
    st.caption("项目资料、检测记录、处理附件和已保存报告一同备份。")
    if st.button("生成完整备份", key="cloud_create_backup", use_container_width=True):
        try:
            result = create_database_backup()
        except RuntimeError as exc:
            st.error(storage_message(exc))
        else:
            st.session_state["settings_cloud_backup_path"] = str(result.target_path)
            st.success("备份已生成，点击下方按钮下载。")
    _render_cloud_backup_download("settings_cloud_backup_path", "下载完整备份", "cloud_download_backup")
    _render_cloud_backup_download(
        "settings_cloud_protection_path", "下载恢复前的数据备份", "cloud_download_protection_backup"
    )

    st.markdown("**从备份恢复数据**")
    st.warning("恢复会替换所有人当前使用的数据。请先让其他人退出，恢复前会自动备份当前数据。")
    uploaded = st.file_uploader("选择备份文件", type=["zip", "db"], key="cloud_restore_upload")
    payload = uploaded.getvalue() if uploaded is not None else b""
    signature = sha256(payload).hexdigest() if uploaded is not None else ""
    if signature != st.session_state.get("settings_cloud_restore_signature", ""):
        st.session_state["settings_cloud_restore_signature"] = signature
        st.session_state["settings_cloud_restore_confirmed"] = False

    if uploaded is None:
        st.info('请先在“选择备份文件”中上传备份，通过检查后勾选确认项，再点击“确认恢复数据”。')
        st.checkbox("我确认用此备份替换当前数据。", key="settings_cloud_restore_confirmed", disabled=True)
        st.button("确认恢复数据", key="cloud_confirm_restore", type="primary",
                  use_container_width=True, disabled=True)
        return

    try:
        with TemporaryDirectory(prefix="ljqc-upload-") as temporary:
            backup_file = Path(temporary) / ("backup.zip" if uploaded.name.lower().endswith(".zip") else "backup.db")
            backup_file.write_bytes(payload)
            restore_ready, message = validate_backup_file(backup_file)
            if restore_ready:
                st.success(message)
            else:
                st.warning(storage_message(message))
            confirmed = st.checkbox("我确认用此备份替换当前数据。", key="settings_cloud_restore_confirmed",
                                    disabled=not restore_ready)
            if restore_ready and not confirmed:
                st.info('备份文件已通过检查。请勾选上方“我确认用此备份替换当前数据”，再点击“确认恢复数据”。')
            if st.button("确认恢复数据", key="cloud_confirm_restore", type="primary",
                         use_container_width=True, disabled=not (restore_ready and confirmed)):
                try:
                    result = restore_database_from_backup_file(backup_file)
                except RuntimeError as exc:
                    st.error(storage_message(exc))
                else:
                    st.session_state.clear()
                    st.session_state["show_settings_page"] = True
                    st.session_state["settings_cloud_restore_notice"] = (
                        "数据已恢复，请其他使用者刷新页面后继续。" + " ".join(result.warnings)
                    )
                    if result.protection_backup_path is not None:
                        st.session_state["settings_cloud_protection_path"] = str(result.protection_backup_path)
                    st.rerun()
    except OSError:
        st.error("无法读取备份文件，请重新选择后重试。")


def _render_cloud_backup_download(session_key: str, label: str, key: str) -> None:
    backup_path = _read_optional_path(session_key)
    if backup_path is None:
        return
    try:
        data = backup_path.read_bytes()
    except OSError:
        st.warning("备份文件暂时无法下载，请重新生成备份。")
        return
    st.download_button(label, data=data, file_name=backup_path.name,
                       mime="application/zip" if backup_path.suffix == ".zip" else "application/octet-stream",
                       key=key, use_container_width=True)


def _read_optional_path(session_key: str) -> Path | None:
    raw_value = str(st.session_state.get(session_key, "") or "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
