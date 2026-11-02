"""Save a user's statistics preference without changing active control settings."""
from database import atomic_write, read_snapshot

STATISTICS_MODE_LABELS = {'monthly': '月度统计', 'cumulative': '累计统计'}


def normalize_statistics_mode(value, *, optional=False):
    mode = str(value or '').strip()
    if not mode and optional:
        return ''
    if mode not in STATISTICS_MODE_LABELS:
        raise ValueError('请选择月度统计或累计统计。')
    return mode


def _scope(scope):
    if scope not in ('project', 'lot'):
        raise ValueError('统计方式所属项目无效。')
    return ('project_item_id', 'qc_project_template_items') if scope == 'project' else ('lot_item_id', 'qc_lot_config_items')


def get_target_statistics_policy(scope, item_id, connection=None):
    if connection is None:
        with read_snapshot() as current:
            return get_target_statistics_policy(scope, item_id, current)
    field, table = _scope(scope)
    if not connection.execute(f'SELECT 1 FROM {table} WHERE id=?', (int(item_id),)).fetchone():
        raise ValueError('未找到统计方式所属的检验项目。')
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qc_target_statistics_policies'").fetchone()
    row = connection.execute(f'SELECT * FROM qc_target_statistics_policies WHERE {field}=?',
                             (int(item_id),)).fetchone() if exists else None
    return dict(row) if row else dict(mode='', revision=0, confirmed_by='', updated_at='')


def save_target_statistics_policy(scope, item_id, mode, *, expected_revision=None,
                                  confirmed_by='', connection=None):
    if connection is None:
        with atomic_write() as current:
            return save_target_statistics_policy(scope, item_id, mode,
                expected_revision=expected_revision, confirmed_by=confirmed_by, connection=current)
    mode = normalize_statistics_mode(mode)
    field, table = _scope(scope)
    if not connection.execute(f'SELECT 1 FROM {table} WHERE id=? AND is_disabled=0', (int(item_id),)).fetchone():
        raise ValueError('检验项目不存在或已停用，请重新打开设置。')
    prior = get_target_statistics_policy(scope, item_id, connection)
    if expected_revision is not None and int(prior['revision']) != int(expected_revision):
        raise ValueError('统计方式已修改，请重新核对后再切换。')
    if prior['mode'] != mode:
        connection.execute(f'''INSERT INTO qc_target_statistics_policies({field},mode,confirmed_by)
            VALUES(?,?,?) ON CONFLICT({field}) DO UPDATE SET mode=excluded.mode,
            revision=qc_target_statistics_policies.revision+1,confirmed_by=excluded.confirmed_by,
            updated_at=CURRENT_TIMESTAMP''', (int(item_id), mode, str(confirmed_by or '').strip()))
    return get_target_statistics_policy(scope, item_id, connection)


def copy_target_statistics_policy(source_scope, source_id, target_scope, target_id, connection):
    policy = get_target_statistics_policy(source_scope, source_id, connection)
    if policy['mode']:
        save_target_statistics_policy(target_scope, target_id, policy['mode'],
            confirmed_by=policy['confirmed_by'], connection=connection)
