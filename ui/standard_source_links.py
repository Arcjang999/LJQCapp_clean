"""Consistent original-document links in every quality-settings view."""
import streamlit as st

from services.standard_source_links_service import standard_source_links


def render_standard_source_links(source):
    links = standard_source_links(source)
    if not links:
        return
    for column, link in zip(st.columns(len(links)), links):
        column.link_button(link['label'], link['url'], help=link['provider'], width='stretch')
    label = str(source.get('standard') or source.get('name') or '原文')
    with st.expander('复制 ' + label + ' 网址'):
        for link in links:
            st.caption(link['provider'] + ' · ' + link['label'])
            st.code(link['url'], language=None, wrap_lines=True)
