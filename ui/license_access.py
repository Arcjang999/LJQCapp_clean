"""First-launch license activation without opening business records."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import streamlit as st

from desktop_licensing.client import AccessResult
from desktop_licensing.runtime import authorization_required, bind_client, create_client


def _check(client, code=None):
    try:
        return client.check(code=code)
    except (OSError, ValueError, RuntimeError):
        logging.exception('Desktop license verification failed')
        return AccessResult(False, 'denied', '授权验证未完成，请重试或联系软件维护人员。',
                            reason='verification_failed')


def render_license_gate(*, client=None, required=None) -> bool:
    """Return before init_db/navigation unless this desktop is authorized."""
    if required is None:
        required = authorization_required()
    if not required:
        return True
    if client is None:
        key = '_desktop_license_client'
        if key not in st.session_state:
            st.session_state[key] = create_client()
        client = st.session_state[key]
    bind_client(client)
    result = _check(client)
    if result.allowed:
        if result.status == 'offline':
            until = datetime.fromtimestamp(result.offline_until,
                timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
            st.warning('暂时无法联网验证，可继续使用至 ' + until + '。')
        return True

    st.title('软件激活')
    st.caption('首次使用请输入验证码并联网激活，后续打开时自动验证。')
    if result.status == 'unconfigured':
        st.error('授权服务尚未配置，请联系软件维护人员。')
        return False
    if result.status != 'needs_code':
        st.warning(result.message)
    with st.form('desktop_license_activation', clear_on_submit=True, enter_to_submit=False):
        code = st.text_input('验证码', type='password', max_chars=160)
        activate = st.form_submit_button('验证并进入软件', type='primary')
    if activate:
        if not code.strip():
            st.error('请输入验证码。')
        else:
            checked = _check(client, code.strip())
            if checked.allowed:
                st.rerun(scope='app')
            st.error(checked.message)
    if st.button('重新联网验证', key='desktop_license_retry'):
        st.session_state.pop('_desktop_license_client', None)
        st.rerun(scope='app')
    st.caption('需要延期或更换电脑时，请联系软件维护人员。已有资料保留在本机。')
    return False
