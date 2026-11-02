"""Resolve real parameter bindings and strictly owned Instant-to-LJ contexts."""
import json


def _row(connection, table, identifier):
    row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (int(identifier),)).fetchone()
    return dict(row) if row else {}


def _decode(value):
    try:
        result = json.loads(value or '{}')
    except (TypeError, ValueError):
        result = {}
    return result if isinstance(result, dict) else {}


def resolve_target_binding(connection, binding_id):
    identifier = int(binding_id)
    if identifier > 0:
        binding = _row(connection, 'qc_workbench_bindings', identifier)
        if binding and binding['qc_method'] in ('lj', 'zscore'):
            return binding
        raise ValueError('请选择单水平或多水平的质控批次。')
    target = _row(connection, 'batches', -identifier) if identifier < 0 else {}
    origin = _row(connection, 'instant_batches', target.get('source_instant_batch_id') or 0)
    project = _row(connection, 'projects', target.get('project_id') or 0)
    origin_project = _row(connection, 'instant_projects', origin.get('project_id') or 0)
    found = connection.execute("SELECT * FROM qc_workbench_bindings WHERE qc_method='instant' AND runtime_batch_id=?",
        (origin.get('id', 0),)).fetchone()
    binding = dict(found) if found else {}
    source, prior = _decode(target.get('source_config_snapshot_json')), _decode(binding.get('source_snapshot_json'))
    identity = source.get('identity') or []
    material = source.get('levels') or []
    valid = (target.get('source_method') == 'instant' and origin and binding
        and origin.get('transfer_status') == 'transferred'
        and origin.get('transferred_to_lj_batch_id') == target['id']
        and origin.get('transferred_to_lj_project_id') == target['project_id']
        and target.get('source_instant_project_id') == origin['project_id']
        and binding['runtime_project_id'] == origin['project_id']
        and project.get('method_type') == 'lj'
        and project.get('input_value_type') == origin_project.get('input_value_type')
        and len(identity) >= 8 and source == prior
        and source.get('project_template_item_id') == binding['project_template_item_id']
        and source.get('lot_config_id') == binding['lot_config_id']
        and source.get('lot_config_item_id') == binding['lot_config_item_id']
        and len(material) == 1 and material[0].get('qc_level_id'))
    if target and connection.execute("SELECT 1 FROM qc_workbench_bindings WHERE qc_method='lj' AND runtime_batch_id=?", (target['id'],)).fetchone():
        valid = False
    if not valid:
        raise ValueError('此批次未关联完整的即时法转入来源，不能确认统计参数。')
    item = _row(connection, 'qc_lot_config_items', binding['lot_config_item_id'])
    if (item.get('qc_method') != 'instant' or item.get('lot_config_id') != binding['lot_config_id']
            or item.get('source_template_item_id') != binding['project_template_item_id']):
        raise ValueError('即时法转入来源与原项目不一致，请核对批次。')
    transfer = dict(source_method='instant', source_binding_id=binding['id'],
        source_instant_project_id=origin['project_id'], source_instant_batch_id=origin['id'],
        runtime_lj_project_id=target['project_id'], runtime_lj_batch_id=target['id'],
        transferred_at=target.get('source_transfer_time'), source_snapshot=source)
    return {**binding, 'id': identifier, 'qc_method': 'lj', 'runtime_project_id': target['project_id'],
            'runtime_batch_id': target['id'], 'source_snapshot_json': target['source_config_snapshot_json'],
            'transfer_chain': transfer}


def target_binding_for_batch(connection, method, batch_id):
    row = connection.execute('SELECT id FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?',
                             (method, int(batch_id))).fetchone()
    if row is not None:
        return resolve_target_binding(connection, row['id'])
    if method == 'lj':
        return resolve_target_binding(connection, -int(batch_id))
    raise ValueError('未找到本批次的参数设置来源。')


def list_target_binding_ids(connection):
    identifiers = [row['id'] for row in connection.execute(
        "SELECT id FROM qc_workbench_bindings WHERE qc_method IN ('lj','zscore') ORDER BY id DESC")]
    for row in connection.execute("SELECT id FROM batches WHERE source_method='instant' ORDER BY id DESC"):
        # Broken or older unbound transfers remain ordinary read-only history.
        try:
            resolve_target_binding(connection, -row['id'])
        except ValueError:
            continue
        identifiers.append(-row['id'])
    return identifiers
