"""Desktop download choices shown from the Help menu."""
from __future__ import annotations

import streamlit as st

from services.software_download_service import get_software_download_catalog
from services.desktop_update_catalog_service import get_desktop_update_catalog
from services.runtime_edition import software_downloads_available
from ui.common import go_to_home_page
from ui.module_styles import render_module_header


def _installation_help(target: str, sha256: str, *, bootstrap: bool = False) -> None:
    with st.expander("安装提示与文件校验"):
        if target == "windows-x86_64":
            st.markdown(
                "安装包尚未进行 Windows 代码签名，下载信誉也在积累，可能出现 "
                "SmartScreen“Windows 已保护你的电脑”提示。确认文件来自本页、"
                "SHA-256 与下方一致且你信任本应用后，可选择“更多信息 → 仍要运行”。"
                "若单位策略未提供此选项，请联系管理员。"
            )
            st.caption("校验方法：在 PowerShell 中输入 Get-FileHash，将下载文件拖入窗口，"
                       "再输入 -Algorithm SHA256 后回车，比对 Hash。")
            st.markdown("[微软 SmartScreen 说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)")
        else:
            application_name = "LJQCAppUpdater" if bootstrap else "LJQCApp"
            instructions = (
                "打开更新接入 DMG，运行其中的 LJQCAppUpdater 更新接入应用，选择已安装的 LJQCApp。"
                "它会保留原程序与资料并接续启动入口，无需手动拖动它替换原应用。"
                if bootstrap else "打开 DMG 后，将 LJQCApp 拖入“应用程序”，再打开应用。"
            )
            st.markdown(
                instructions + "当前安装包尚未完成 "
                "Apple Developer ID 签名和公证，可能提示无法验证开发者或无法检查恶意软件。"
                "确认文件来自本页、SHA-256 与下方一致且你信任本应用后，可在首次尝试打开后，"
                "进入“系统设置 → 隐私与安全”，找到 " + application_name + " 并选择“仍要打开”，再按提示确认。"
            )
            st.caption("校验方法：在“终端”中输入 shasum -a 256，将下载的 DMG 拖入窗口后回车，"
                       "比对输出的校验值。")
            st.markdown("[Apple 安全打开应用说明](https://support.apple.com/zh-cn/102445)")
        st.caption("SHA-256（可复制，用于核对下载文件是否与发布文件一致）")
        st.code(sha256, language=None, wrap_lines=True)
        st.caption("无需关闭系统安全保护。若提示发现恶意软件、文件已损坏，或校验值不一致，请停止安装并联系发布方。")


def _package_size(size_bytes: int) -> str:
    for divisor, unit in ((1_000_000_000, "GB"), (1_000_000, "MB"), (1_000, "KB")):
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.1f} {unit}"
    return f"{size_bytes} B"


def render_software_downloads_page() -> None:
    if not software_downloads_available():
        go_to_home_page(rerun=False)
        return
    render_module_header("软件下载", "选择适合你电脑的安装包。", tone="guide", eyebrow="帮助")
    st.button("返回主页", key="software_downloads_home", on_click=go_to_home_page,
              kwargs={"rerun": False})
    st.markdown("**当前提供开发版，仍在持续更新。** 请从本页下载安装包；"
                "首次安装如遇系统验证提示，可查看下方“安装提示与文件校验”。")
    st.markdown("**1.00 基础安装包固定保留。** 新用户先安装基础包，再运行一次“更新接入补丁”；"
                "已经安装 1.00 的用户直接接入即可。接入后，每次启动检查更新，经确认下载并退出后更新，"
                "继续保留原有数据、报告、附件和保存位置。")
    catalog = get_software_download_catalog()
    updates = get_desktop_update_catalog()
    bootstraps = {entry.target: entry for entry in updates.bootstraps}
    if not catalog.readable:
        st.info("安装包信息暂时无法读取，请稍后重试。")
    for column, option in zip(st.columns(2, gap="large"), catalog.downloads, strict=True):
        with column, st.container(border=True):
            st.subheader(option.label)
            st.caption("系统要求：Windows 10/11，64 位（x64）。" if option.target == "windows-x86_64"
                       else "系统要求：macOS 13 或以上，Apple 芯片（M 系列）。")
            release = option.release
            st.markdown("**1.00 基础安装包**")
            if release is None:
                st.button("安装包准备中", key="software_download_pending_" + option.target,
                          disabled=True, width="stretch")
            else:
                build = f" · 构建 {release.build_number}" if release.build_number is not None else ""
                st.caption(f"开发版 · 版本 {release.version}{build} · {release.published_at.isoformat()} · "
                           f"{_package_size(release.size_bytes)}")
                st.link_button(option.download_label, release.url, width="stretch")
                _installation_help(option.target, release.sha256)
            st.markdown("**一次性更新接入补丁**")
            bootstrap = bootstraps.get(option.target)
            if bootstrap is None:
                st.button("更新接入组件准备中", key="software_update_pending_" + option.target,
                          disabled=True, width="stretch")
            else:
                st.caption(f"更新接入补丁 · 构建 {bootstrap.build_number} · "
                           f"{bootstrap.published_at.date().isoformat()} · {_package_size(bootstrap.size_bytes)}")
                label = "下载 Windows 更新接入补丁" if option.target == "windows-x86_64" else "下载 Mac 更新接入补丁"
                st.link_button(label, bootstrap.url, width="stretch")
                if option.target == "windows-x86_64":
                    st.caption("运行更新接入补丁，按提示选择已经安装的 LJQCApp；完成后继续通过原入口打开。")
                else:
                    st.caption("打开更新接入 DMG，运行其中的更新接入应用；由它接续原程序入口，保留原有资料。")
                _installation_help(option.target, bootstrap.sha256, bootstrap=True)
