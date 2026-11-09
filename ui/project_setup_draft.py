"""Keep incomplete new-project input separate from saved project settings."""
from copy import deepcopy
import logging
import re
import sqlite3
from uuid import uuid4

import streamlit as st

from services.project_setup_draft_service import (
    clear_project_setup_draft, load_project_setup_draft, save_project_setup_draft,
)


def is_new_project_setup(ctx):
    return (ctx.get('kind') in ('quick', 'project')
            and ctx.get('template_id') is None and ctx.get('item_id') is None)


def setup_draft_pending(ctx):
    return (bool(ctx.get('dependency_registration'))
            or any(key.endswith('_editor') and bool(value) for key, value in ctx.items())
            or bool(st.session_state.get('quick_' + ctx['token'] + '_registration')))


def _current_widgets(ctx, values):
    token = ctx['token']
    quality_prefix = 'quick_quality_' + token + '_'
    active_quality = quality_prefix + str(ctx.get('quick_quality_identity') or '') + '_'
    return {key: value for key, value in values.items()
        if token in key and not key.endswith(('_file', '_process_reset'))
        and (not key.startswith(quality_prefix) or key.startswith(active_quality))}


def remember_setup_inputs(ctx):
    """Capture before a discard prompt removes the input widgets."""
    if not is_new_project_setup(ctx):
        return
    token = ctx['token']
    if ctx['kind'] == 'quick':
        from ui.quick_project_setup import _remember_quality_inputs
        _remember_quality_inputs(ctx)
    values = deepcopy(ctx.get('setup_draft_widgets', {}))
    quality = ctx.get('quick_quality_inputs') or {}
    values.update(deepcopy(quality.get('values', {})))
    for key, value in list(st.session_state.items()):
        ordinary = key.startswith('project_draft_' + token + '_')
        control = re.fullmatch('quick_control_' + re.escape(token) + r'_\d+_(product|level|last_product)', key)
        product = re.fullmatch('project_product_' + re.escape(token) + r'(?:_\d+)?_(person|evidence|mode|product)', key)
        other = key in ('project_kind_' + token, 'project_instant_' + token,
            'project_dependency_' + token + '_type',
            'project_catalogue_query_' + token, 'project_catalogue_product_' + token)
        if ordinary or control or product or other:
            values[key] = deepcopy(value)
    # A mode switch changes the token; never restore widgets from the old mode.
    ctx['setup_draft_widgets'] = _current_widgets(ctx, values)


def restore_setup_inputs(ctx):
    if ctx.pop('restore_setup_draft_widgets', False):
        for key, value in _current_widgets(ctx, ctx.get('setup_draft_widgets', {})).items():
            st.session_state[key] = deepcopy(value)


def open_saved_setup():
    saved = load_project_setup_draft()
    if saved is None:
        return None
    ctx = saved['context']
    ctx['discard'] = False
    ctx.pop('quick_preview', None)
    if ctx.get('quick_step') == 'preview':
        ctx['quick_step'] = 'quality'
    ctx['setup_draft_widgets'] = saved['widgets']
    ctx['restore_setup_draft_widgets'] = True
    restore_setup_inputs(ctx)
    return ctx


def clear_saved_setup(ctx):
    if is_new_project_setup(ctx) and ctx.get('creation_draft_id'):
        clear_project_setup_draft(ctx['creation_draft_id'])


def new_project_button_label():
    try:
        saved = load_project_setup_draft()
    except (ValueError, TypeError):
        saved = None
    return '继续新建项目' if saved else '新建项目'


def render_save_setup_draft(ctx, *, disabled=False):
    if not is_new_project_setup(ctx):
        return
    ctx.setdefault('creation_draft_id', uuid4().hex)
    if st.button('保存草稿并退出', key='project_save_draft',
                 disabled=disabled or setup_draft_pending(ctx), width='stretch'):
        remember_setup_inputs(ctx)
        context = deepcopy(ctx)
        context['discard'] = False
        context.pop('quick_preview', None)
        context.pop('setup_draft_widgets', None)
        context.pop('restore_setup_draft_widgets', None)
        if context.get('quick_step') == 'preview':
            context['quick_step'] = 'quality'
        try:
            save_project_setup_draft(context, ctx.get('setup_draft_widgets', {}))
        except (ValueError, TypeError) as error:
            st.error(str(error))
        except (sqlite3.Error, OSError):
            logging.exception('Could not save new-project draft')
            st.error('草稿保存失败，请重试。')
        else:
            from ui.project_dialogs import _finish
            _finish('草稿已保存，点击“继续新建项目”接着填写。')
