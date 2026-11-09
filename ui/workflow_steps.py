"""Readable current-step headings for forms with a defined sequence."""
import streamlit as st


def render_workflow_step(step, total, title, description=''):
    st.subheader(f'{step}/{total} {title}', anchor=False)
    if description:
        st.write(description)
