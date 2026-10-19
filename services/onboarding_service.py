"""Persist first-use navigation independently of laboratory business records."""
from __future__ import annotations

import json
import sqlite3

from database import get_app_settings, read_snapshot, save_app_settings


STEP_IDS = ('unit', 'materials', 'project')
ONBOARDING_SETTINGS_KEY = 'first_use_onboarding'
STATE_VERSION = 1
_STATUSES = ('new', 'active', 'skipped', 'completed')
_PROJECT_TABLES = ('qc_project_templates', 'projects', 'instant_projects', 'zscore_projects')


def _normalize_state(value: object) -> dict:
    if not isinstance(value, dict):
        value = {}
    step = value.get('step')
    if step == 'start':
        step = 'project'
    return {
        'version': STATE_VERSION,
        'step': step if step in STEP_IDS else STEP_IDS[0],
        'status': value.get('status') if value.get('status') in _STATUSES else 'new',
    }


def get_onboarding_state() -> dict:
    stored = get_app_settings((ONBOARDING_SETTINGS_KEY,)).get(ONBOARDING_SETTINGS_KEY, '')
    try:
        value = json.loads(stored)
    except (TypeError, ValueError, RecursionError):
        value = {}
    return _normalize_state(value)


def save_onboarding_progress(step: str, *, status: str = 'active') -> dict:
    if step not in STEP_IDS or status not in _STATUSES:
        raise ValueError('引导进度未保存，请重新打开首次引导后重试。')
    state = {'version': STATE_VERSION, 'step': step, 'status': status}
    save_app_settings({ONBOARDING_SETTINGS_KEY: json.dumps(state, ensure_ascii=False)})
    return state


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _has_projects(connection: sqlite3.Connection, tables: set[str]) -> bool:
    return any(connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None
               for table in _PROJECT_TABLES if table in tables)


def has_existing_projects() -> bool:
    """Draft and disabled projects count, while built-in product lists do not."""
    with read_snapshot() as connection:
        return _has_projects(connection, _tables(connection))


def should_auto_show_onboarding() -> bool:
    return (get_onboarding_state()['status'] not in ('skipped', 'completed')
            and not has_existing_projects())


def get_onboarding_readiness() -> dict[str, bool]:
    """Read registration hints; these never confirm quality or allow detection."""
    with read_snapshot() as connection:
        tables = _tables(connection)
        lab = connection.execute("SELECT value FROM app_settings WHERE key='lab_name'").fetchone()
        instrument = connection.execute('SELECT 1 FROM lab_instruments WHERE is_disabled=0 LIMIT 1').fetchone()
        reagent = connection.execute('''SELECT 1 FROM md_reagent_lots l
            JOIN md_reagents r ON r.id=l.reagent_id
            WHERE l.is_disabled=0 AND r.is_disabled=0 AND trim(l.lot_no)<>'' LIMIT 1''').fetchone()
        qc_lot = connection.execute('''SELECT 1 FROM md_qc_material_lots l
            JOIN md_qc_materials m ON m.id=l.qc_material_id
            JOIN md_qc_levels v ON v.qc_material_lot_id=l.id
            WHERE l.is_disabled=0 AND m.is_disabled=0 AND v.is_disabled=0
              AND trim(l.lot_no)<>'' AND trim(v.level_name)<>'' LIMIT 1''').fetchone()
        active_batch = connection.execute('''SELECT 1 FROM qc_lot_configs c
            JOIN qc_project_templates t ON t.id=c.template_id
            JOIN qc_lot_config_items i ON i.lot_config_id=c.id
            JOIN qc_workbench_bindings b ON b.lot_config_item_id=i.id
            WHERE c.is_disabled=0 AND c.status='active' AND t.is_disabled=0
              AND i.is_disabled=0 AND i.is_enabled=1 AND b.binding_status='active'
            LIMIT 1''').fetchone()
        return {
            'unit': bool(lab and str(lab[0] or '').strip()),
            'instrument': instrument is not None,
            'reagent_lot': reagent is not None,
            'qc_lot': qc_lot is not None,
            'project': _has_projects(connection, tables),
            'active_batch': active_batch is not None,
        }
