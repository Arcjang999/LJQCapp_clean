"""Persist an unfinished new-project form separately from business records."""
from __future__ import annotations

from datetime import date, datetime
import json
import math
import re

import numpy as np

from database import atomic_write, read_snapshot


PROJECT_SETUP_DRAFT_KEY = 'new_project_setup_draft'
_VERSION = 1
_TYPE = '__project_setup_draft_type__'
_UNREADABLE = '项目草稿无法读取，请联系维护人员。'


def _encode(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('草稿中有无效数值，请核对后重试。')
        return value
    if isinstance(value, datetime):
        return {_TYPE: 'datetime', 'value': value.isoformat()}
    if isinstance(value, date):
        return {_TYPE: 'date', 'value': value.isoformat()}
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, tuple):
        return {_TYPE: 'tuple', 'items': [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value) or _TYPE in value:
            raise ValueError('草稿内容无法保存，请核对后重试。')
        return {key: _encode(item) for key, item in value.items()}
    raise ValueError('草稿内容无法保存，请继续填写或重试。')


def _decode(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(_UNREADABLE)
        return value
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        if _TYPE not in value:
            return {key: _decode(item) for key, item in value.items()}
        kind = value[_TYPE]
        if kind == 'tuple' and set(value) == {_TYPE, 'items'} and isinstance(value['items'], list):
            return tuple(_decode(item) for item in value['items'])
        if kind in ('date', 'datetime') and set(value) == {_TYPE, 'value'} and isinstance(value['value'], str):
            try:
                return (datetime if kind == 'datetime' else date).fromisoformat(value['value'])
            except ValueError:
                raise ValueError(_UNREADABLE) from None
    raise ValueError(_UNREADABLE)


def _validate_context(context):
    if (not isinstance(context, dict) or context.get('kind') not in ('quick', 'project')
            or context.get('template_id') is not None or context.get('item_id') is not None
            or not isinstance(context.get('draft'), dict)):
        raise ValueError('只能暂存新建项目。')
    draft_id = context.get('creation_draft_id')
    token = context.get('token')
    if (not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{32}', draft_id)
            or not isinstance(token, str) or not token or len(token) > 100):
        raise ValueError('项目草稿标识无效，请重新打开新建项目。')
    if context.get('quick_step', 'details') not in ('details', 'quality', 'preview'):
        raise ValueError('项目草稿的填写步骤无效，请重新打开新建项目。')


def _validate_widgets(widgets):
    if (not isinstance(widgets, dict) or any(not isinstance(key, str) for key in widgets)
            or any(key.endswith('_file') for key in widgets)):
        raise ValueError('草稿内容无法保存，请继续填写或重试。')


def _load_payload(raw):
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        raise ValueError(_UNREADABLE) from None
    if not isinstance(payload, dict) or set(payload) != {'version', 'context', 'widgets'}:
        raise ValueError(_UNREADABLE)
    if type(payload['version']) is not int or payload['version'] != _VERSION:
        raise ValueError('项目草稿版本不受支持，请使用保存草稿的程序版本。')
    try:
        context, widgets = _decode(payload['context']), _decode(payload['widgets'])
        _validate_context(context)
        _validate_widgets(widgets)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(_UNREADABLE) from None
    return {'context': context, 'widgets': widgets}


def _stored(connection):
    return connection.execute('SELECT value FROM app_settings WHERE key=?',
                              (PROJECT_SETUP_DRAFT_KEY,)).fetchone()


def load_project_setup_draft():
    with read_snapshot() as connection:
        row = _stored(connection)
        return _load_payload(row['value']) if row is not None else None


def save_project_setup_draft(context, widgets):
    """Allow incomplete fields; only explicit final save creates a project."""
    _validate_context(context)
    _validate_widgets(widgets)
    try:
        payload = json.dumps({'version': _VERSION, 'context': _encode(context),
                              'widgets': _encode(widgets)}, ensure_ascii=False, allow_nan=False)
    except (TypeError, RecursionError):
        raise ValueError('草稿内容无法保存，请核对后重试。') from None
    with atomic_write() as connection:
        row = _stored(connection)
        if row is not None:
            previous = _load_payload(row['value'])
            if previous['context']['creation_draft_id'] != context['creation_draft_id']:
                raise ValueError('已有未完成的项目草稿，请先继续填写或放弃该草稿。')
        connection.execute('''INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP''',
            (PROJECT_SETUP_DRAFT_KEY, payload))


def clear_project_setup_draft(draft_id):
    if not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{32}', draft_id):
        raise ValueError('项目草稿标识无效，请重新打开新建项目。')
    with atomic_write() as connection:
        row = _stored(connection)
        if row is None:
            return
        previous = _load_payload(row['value'])
        if previous['context']['creation_draft_id'] == draft_id:
            connection.execute('DELETE FROM app_settings WHERE key=?', (PROJECT_SETUP_DRAFT_KEY,))
