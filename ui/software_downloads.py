"""Desktop download choices shown from the Help menu."""
from __future__ import annotations

import streamlit as st

from services.software_download_service import get_software_download_catalog
from ui.common import go_to_home_page
from ui.module_styles import render_module_header


def _package_size(size_bytes: int) -> str:
    for divisor, unit in ((1_000_000_000, "GB"), (1_000_000, "MB"), (1_000, "KB")):
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.1f} {unit}"
    return f"{size_bytes} B"


def render_software_downloads_page() -> None:
    render_module_header("软件下载", "选择适合你电脑的安装包。", tone="guide", eyebrow="帮助")
    st.button("返回主页", key="software_downloads_home", on_click=go_to_home_page,
              kwargs={"rerun": False})
    catalog = get_software_download_catalog()
    if not catalog.readable:
        st.info("安装包信息暂时无法读取，请稍后重试。")
    for column, option in zip(st.columns(2, gap="large"), catalog.downloads, strict=True):
        with column, st.container(border=True):
            st.subheader(option.label)
            release = option.release
            if release is None:
                st.button("安装包准备中", key="software_download_pending_" + option.target,
                          disabled=True, width="stretch")
                continue
            st.caption(f"版本 {release.version} · {release.published_at.isoformat()} · "
                       f"{_package_size(release.size_bytes)}")
            st.link_button(option.download_label, release.url, width="stretch")
