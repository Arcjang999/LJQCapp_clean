"""Local, non-colour-only guidance for required inputs; never changes their state."""
from contextlib import contextmanager
import hashlib

import streamlit as st


def required_field_key(widget_key):
    """Keep the field wrapper stable when its validation state changes."""
    return 'required_field_' + hashlib.sha256(widget_key.encode()).hexdigest()[:16]


@contextmanager
def required_field(widget_key, *, missing, message):
    key = required_field_key(widget_key)
    if missing:
        st.markdown(
            f'<style>.st-key-{key} {{border:2px solid #BD7411;'
            'background:#FFF8E8;border-radius:8px;padding:10px 12px;}'
            f'.st-key-{key} [data-testid="stCaptionContainer"] '
            '{color:#754507;font-weight:600;}</style>',
            unsafe_allow_html=True,
        )
    with st.container(key=key):
        yield
        if missing:
            st.caption('必填 · ' + message)
