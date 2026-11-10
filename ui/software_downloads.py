"""Desktop download choices shown from the Help menu."""
from __future__ import annotations

import streamlit as st

from services.software_download_service import get_software_download_catalog
from services.runtime_edition import software_downloads_available
from ui.common import go_to_home_page
from ui.module_styles import render_module_header


def _installation_help(target: str, sha256: str) -> None:
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
            st.markdown(
                "当前安装包尚未完成 "
                "Apple Developer ID 签名和公证，可能提示无法验证开发者或无法检查恶意软件。"
                "确认文件来自本页、SHA-256 与下方一致且你信任本应用后，可在首次尝试打开后，"
                "进入“系统设置 → 隐私与安全”，找到 LJQCAppInstaller 并选择“仍要打开”，再按提示确认。"
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
    st.markdown("**1.01 一体安装包包含程序和自动更新器，安装完成即可使用。**\n\n"
                "以后打开软件时会从服务器检查更新，有新版时按提示确认下载。\n\n"
                "已经安装旧版的电脑，请先保存工作并退出软件，再运行下方同平台的安装包。"
                "原有数据、报告、附件和保存位置继续保留。")
    catalog = get_software_download_catalog()
    if not catalog.readable:
        st.info("安装包信息暂时无法读取，请稍后重试。")
    for column, option in zip(st.columns(2, gap="large"), catalog.downloads, strict=True):
        with column, st.container(border=True):
            st.subheader(option.label)
            st.caption("系统要求：Windows 10/11，64 位（x64）。" if option.target == "windows-x86_64"
                       else "系统要求：macOS 13 或以上，Apple 芯片（M 系列）。")
            release = option.release
            if release is not None and not release.updater_integrated:
                release = None
            st.markdown("**1.01 一体安装包**")
            if release is None:
                st.button("1.01 安装包准备中", key="software_download_pending_" + option.target,
                          disabled=True, width="stretch")
            else:
                st.caption(f"开发版 · {release.published_at.isoformat()} · "
                           f"{_package_size(release.size_bytes)}")
                st.link_button(option.download_label, release.url, width="stretch")
                if option.target == "windows-x86_64":
                    st.markdown("双击下载的安装包，按提示完成安装，再从 LJQCApp 图标打开软件。")
                else:
                    st.markdown("打开下载的 DMG，双击其中的 LJQCAppInstaller 完成安装，"
                                "再从“应用程序”中的 LJQCApp 打开软件。")
                st.caption("自动更新器随程序一起安装，无需单独下载、填写地址或寻找程序位置。")
                _installation_help(option.target, release.sha256)
