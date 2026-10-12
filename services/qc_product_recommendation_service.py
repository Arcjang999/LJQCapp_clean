"""Advisory recommendations share explicit scope and local selection evidence."""
from hashlib import sha256
import json
import sqlite3

from database import atomic_write, read_snapshot

SCOPE_FIELDS = ('reagent_id', 'lab_instrument_id', 'test_item_id', 'method_id')
REFERENCE_TABLES = {'reagent_id': 'md_reagents', 'qc_material_id': 'md_qc_materials',
    'lab_instrument_id': 'lab_instruments', 'test_item_id': 'md_test_items', 'method_id': 'md_methods'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _decode(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        return {}


def _identity(values):
    return {key: int(values[key]) if values.get(key) is not None else None for key in SCOPE_FIELDS}


def _active(connection, values):
    _, invalid = _reference_state(connection, values)
    if invalid:
        raise ValueError('所选资料或其厂家、仪器型号已停用或不存在，请重新核对。')


def _reference_state(connection, values):
    state, invalid = {}, False
    for key, table in REFERENCE_TABLES.items():
        if values.get(key) is None:
            continue
        record = connection.execute('SELECT * FROM ' + table + ' WHERE id=?', (int(values[key]),)).fetchone()
        row = dict(record) if record is not None else None
        details = {'record': row}
        invalid = invalid or row is None or bool(row.get('is_disabled'))
        if row is not None:
            owner = row
            category = {'reagent_id': 'reagent', 'qc_material_id': 'qc_material', 'lab_instrument_id': 'instrument'}.get(key)
            if key == 'lab_instrument_id':
                model = connection.execute('SELECT * FROM md_instrument_models WHERE id=?', (row.get('instrument_model_id'),)).fetchone()
                owner = dict(model) if model is not None else {}
                details['instrument_model'] = owner
                invalid = invalid or not owner or bool(owner.get('is_disabled'))
            if category and owner.get('manufacturer_id') is not None:
                manufacturer = connection.execute('SELECT * FROM md_manufacturers WHERE id=?', (owner['manufacturer_id'],)).fetchone()
                maker = dict(manufacturer) if manufacturer is not None else {}
                categories = [entry[0] for entry in connection.execute('SELECT category FROM md_manufacturer_categories WHERE manufacturer_id=? ORDER BY category', (owner['manufacturer_id'],))]
                details.update(manufacturer=maker, manufacturer_categories=categories)
                invalid = invalid or not maker or bool(maker.get('is_disabled')) or category not in categories
        state[key] = details
    return state, invalid


def _context(connection, identity):
    reference_state, unavailable = _reference_state(connection, identity)
    rows = [dict(row) for row in connection.execute('''SELECT r.*,p.generic_name,p.trade_name,p.catalog_no,
        p.is_disabled AS product_disabled,m.display_name AS manufacturer_name,
        n.is_disabled AS instrument_disabled,t.is_disabled AS test_disabled,x.is_disabled AS method_disabled
        FROM qc_product_recommendations r JOIN md_qc_materials p ON p.id=r.qc_material_id
        LEFT JOIN md_manufacturers m ON m.id=p.manufacturer_id
        LEFT JOIN lab_instruments n ON n.id=r.lab_instrument_id
        LEFT JOIN md_test_items t ON t.id=r.test_item_id LEFT JOIN md_methods x ON x.id=r.method_id
        WHERE r.reagent_id=? AND (r.lab_instrument_id IS NULL OR r.lab_instrument_id=?)
        AND (r.test_item_id IS NULL OR r.test_item_id=?) AND (r.method_id IS NULL OR r.method_id=?)
        ORDER BY r.qc_material_id,r.id''', tuple(identity[key] for key in SCOPE_FIELDS))]
    products = {row['qc_material_id']: _reference_state(connection, {'qc_material_id': row['qc_material_id']}) for row in rows}
    revision = sha256(_json({'context': identity, 'references': reference_state, 'products': products, 'relations': rows}).encode()).hexdigest()
    grouped = {}
    for row in rows:
        if unavailable or products[row['qc_material_id']][1] or row['is_disabled'] or row['product_disabled'] or any(row.get(key) for key in ('instrument_disabled', 'test_disabled', 'method_disabled')):
            continue
        candidate = grouped.setdefault(row['qc_material_id'], {
            'qc_material_id': row['qc_material_id'],
            'label': '｜'.join(str(row.get(key) or '') for key in ('manufacturer_name', 'catalog_no', 'generic_name') if row.get(key)),
            'relation_ids': [], 'evidence': [], 'confirmed_by': [], 'scope_notes': []})
        for field, value in (('relation_ids', row['id']), ('evidence', row['evidence']),
                             ('confirmed_by', row['confirmed_by']), ('scope_notes', row['scope_note'])):
            if value not in candidate[field]:
                candidate[field].append(value)
    return {'context': identity, 'revision': revision, 'candidates': list(grouped.values()), 'relation_count': len(rows)}


def get_qc_product_recommendations(*, reagent_id, lab_instrument_id=None, test_item_id=None, method_id=None):
    identity = _identity(locals())
    with read_snapshot() as connection:
        return _context(connection, identity)


def validate_qc_product_choice(*, qc_material_id, reagent_id, lab_instrument_id,
                               test_item_id=None, method_id=None, expected_revision=None, manual_review=None):
    identity = _identity(locals())
    with read_snapshot() as connection:
        _active(connection, {**identity, 'qc_material_id': qc_material_id})
        context = _context(connection, identity)
        if expected_revision is not None and expected_revision != context['revision']:
            raise ValueError('试剂与质控品适用资料已变化，请保留输入并重新核对候选及选品依据。')
        candidate = next((row for row in context['candidates'] if row['qc_material_id'] == int(qc_material_id)), None)
        manual = _decode(manual_review)
        if manual:
            actor, evidence = str(manual.get('confirmed_by') or '').strip(), str(manual.get('evidence') or '').strip()
            if not actor or not evidence:
                raise ValueError('人工选择须填写核对人及适用依据。')
            return dict(version=1, context=identity, qc_material_id=int(qc_material_id), revision=context['revision'],
                        basis='manual', relation_ids=[], confirmed_by=actor, evidence=evidence)
        if candidate is None:
            raise ValueError('暂无匹配的已核对适用关系。可以人工选择，但须填写核对人及适用依据。')
        return dict(version=1, context=identity, qc_material_id=int(qc_material_id), revision=context['revision'],
                    basis='recommendation', relation_ids=candidate['relation_ids'],
                    confirmed_by='；'.join(candidate['confirmed_by']), evidence='；'.join(candidate['evidence']))


def validate_choice_review(review, *, qc_material_id, reagent_id, lab_instrument_id, test_item_id=None, method_id=None):
    """Recheck a UI draft; a serialized review can never authorize a different scope."""
    review = _decode(review)
    identity = _identity(locals())
    if (review.get('version') != 1 or review.get('basis') not in ('manual', 'recommendation')
            or not review.get('revision') or review.get('context') != identity
            or review.get('qc_material_id') != int(qc_material_id)):
        raise ValueError('选品资料与当前仪器、试剂或检验项目不一致，请重新核对。')
    manual = review if review.get('basis') == 'manual' else None
    return validate_qc_product_choice(qc_material_id=qc_material_id, **identity,
                                     expected_revision=review.get('revision'), manual_review=manual)


def product_scope_revision(*, reagent_id, lab_instrument_id, test_item_id=None, method_id=None):
    return get_qc_product_recommendations(**locals())['revision']


def get_qc_product_relation_revision(context=None):
    with read_snapshot() as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM qc_product_recommendations ORDER BY id')]
        references = {row['id']: _reference_state(connection, row) for row in rows}
        selected = _reference_state(connection, context or {})
    return sha256(_json({'relations': rows, 'references': references, 'selected': selected}).encode()).hexdigest()


def resolve_item_choice_review(template, row, *, existing=None):
    """Store local choices; unreviewed vocabulary imports remain draft data."""
    scope = dict(reagent_id=row.get('reagent_id'), lab_instrument_id=template['lab_instrument_id'],
                 test_item_id=row['test_item_id'], method_id=row.get('method_id'))
    product = int(template['qc_material_id'])
    if scope['reagent_id'] is None:
        return {}
    context = get_qc_product_recommendations(**scope)
    if row.get('product_choice_review') is not None:
        reviewed = validate_choice_review(row['product_choice_review'], qc_material_id=product, **scope)
        row['product_recommendation_revision'] = reviewed['revision']
        return reviewed
    expected = row.get('product_recommendation_revision')
    if expected is not None and expected != context['revision']:
        raise ValueError('试剂与质控品适用资料已变化，请重新预览并核对选品。')
    if row.get('manual_product_review') is not None:
        return validate_qc_product_choice(qc_material_id=product, **scope, manual_review=row['manual_product_review'])
    # A previously saved manual choice is local evidence, never a clinical rule.
    prior = _decode((dict(existing) if existing is not None else {}).get('product_choice_review_json'))
    header = _decode(dict(template).get('product_choice_review_json'))
    if prior.get('context') == scope and prior.get('qc_material_id') == product:
        return validate_choice_review(prior, qc_material_id=product, **scope)
    declared = header.get('context') or {}
    if header.get('basis') == 'manual' and header.get('qc_material_id') == product and all(
            declared.get(key) is None or declared.get(key) == value for key, value in scope.items()):
        validate_choice_review(header, qc_material_id=product, **declared)
        return validate_qc_product_choice(qc_material_id=product, **scope, manual_review=header)
    if any(candidate['qc_material_id'] == product for candidate in context['candidates']):
        return validate_qc_product_choice(qc_material_id=product, **scope)
    return {}


def item_product_choice_errors(template, row):
    """New reviewed projects must not activate an item with an unreviewed choice."""
    header = _decode(dict(template).get('product_choice_review_json'))
    review = _decode(dict(row).get('product_choice_review_json'))
    if not header and not review and template['status'] != 'draft':
        # Existing saved setups retain their own confirmation; do not invent one.
        return []
    scope = dict(reagent_id=row.get('reagent_id'), lab_instrument_id=template['lab_instrument_id'],
                 test_item_id=row['test_item_id'], method_id=row.get('method_id'))
    if review.get('context') != scope or review.get('qc_material_id') != int(template['qc_material_id']):
        return ['请核对本检验项目所用试剂与质控品的适用依据；没有匹配关系时可登记人工核对。']
    if template['status'] == 'draft':
        try:
            validate_choice_review(review, qc_material_id=template['qc_material_id'], **scope)
        except (ValueError, TypeError) as exc:
            return [str(exc)]
    return []


def _draft_choice_context(connection, scope, item_id):
    if scope not in ('project', 'lot'):
        raise ValueError('无法确定选品核对的项目或批次，请重新打开。')
    if scope == 'project':
        query = '''SELECT i.*,t.lab_instrument_id,t.qc_material_id,t.status AS owner_status,
            t.is_disabled AS owner_disabled,t.revision_no AS owner_revision,
            t.product_choice_review_json AS header_review_json
            FROM qc_project_template_items i JOIN qc_project_templates t ON t.id=i.template_id
            WHERE i.id=? AND i.is_disabled=0'''
    else:
        query = '''SELECT i.*,c.lab_instrument_id,c.qc_material_id,c.status AS owner_status,
            c.is_disabled AS owner_disabled,c.revision_no AS owner_revision,c.activated_at,
            t.product_choice_review_json AS header_review_json
            FROM qc_lot_config_items i JOIN qc_lot_configs c ON c.id=i.lot_config_id
            JOIN qc_project_templates t ON t.id=c.template_id WHERE i.id=? AND i.is_disabled=0'''
    saved = connection.execute(query, (int(item_id),)).fetchone()
    if saved is None:
        raise ValueError('未找到要核对的检验项目，请重新打开。')
    item = dict(saved)
    frozen = item['owner_status'] != 'draft'
    if scope == 'lot':
        frozen = frozen or bool(item['activated_at']) or bool(connection.execute(
            'SELECT 1 FROM qc_workbench_bindings WHERE lot_config_item_id=?', (int(item_id),)).fetchone())
    identity = dict(reagent_id=item['reagent_id'], lab_instrument_id=item['lab_instrument_id'],
                    test_item_id=item['test_item_id'], method_id=item['method_id'])
    return item, identity, frozen


def require_current_draft_choice(scope, item_id, connection):
    """Recheck pending choices at confirmation; established lots keep their evidence."""
    item, identity, frozen = _draft_choice_context(connection, scope, item_id)
    if frozen:
        return
    review = _decode(item['product_choice_review_json'])
    validate_choice_review(review, qc_material_id=item['qc_material_id'], **identity)


def save_draft_product_choice(scope, item_id, review, *, expected_revision):
    """Save an explicit fresh review for a pending item, without rewriting frozen lots."""
    with atomic_write() as connection:
        item, identity, frozen = _draft_choice_context(connection, scope, item_id)
        if frozen or item['owner_disabled']:
            raise ValueError('仅待确认草稿可以更新适用核对；已确认批次的依据保持不变。')
        if int(expected_revision) != int(item['owner_revision']):
            raise ValueError('项目或批次草稿已变化，请重新打开核对。')
        confirmed = validate_choice_review(review, qc_material_id=item['qc_material_id'], **identity)
        table = 'qc_project_template_items' if scope == 'project' else 'qc_lot_config_items'
        owner_table = 'qc_project_templates' if scope == 'project' else 'qc_lot_configs'
        owner_id = item['template_id'] if scope == 'project' else item['lot_config_id']
        connection.execute(f'UPDATE {table} SET product_choice_review_json=? WHERE id=?',
                           (_json(confirmed), int(item_id)))
        connection.execute(f'UPDATE {owner_table} SET revision_no=revision_no+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                           (owner_id,))
        if scope == 'lot':
            from services.project_config_service import _save_snapshot
            _save_snapshot(connection, owner_id, action_type='edit', change_summary='重新核对试剂与质控品适用依据')
        return int(item['owner_revision']) + 1


def list_qc_product_relations(*, include_disabled=False):
    with read_snapshot() as connection:
        return [dict(row) for row in connection.execute('''SELECT r.*,p.generic_name AS qc_material_name,
            x.generic_name AS reagent_name,n.display_name AS instrument_name,t.chinese_name AS test_item_name,
            m.method_name FROM qc_product_recommendations r
            JOIN md_qc_materials p ON p.id=r.qc_material_id JOIN md_reagents x ON x.id=r.reagent_id
            LEFT JOIN lab_instruments n ON n.id=r.lab_instrument_id LEFT JOIN md_test_items t ON t.id=r.test_item_id
            LEFT JOIN md_methods m ON m.id=r.method_id''' + ('' if include_disabled else ' WHERE r.is_disabled=0') + ' ORDER BY r.id')]


def _audit(connection, relation_id, action, before, after, actor, reason):
    connection.execute('''INSERT INTO qc_product_recommendation_history
        (relation_id,action,before_json,after_json,changed_by,reason) VALUES(?,?,?,?,?,?)''',
        (relation_id, action, _json(before), _json(after), actor, reason))


def save_qc_product_relation(values, *, relation_id=None, expected_revision=None):
    payload = {**_identity(values), 'qc_material_id': int(values['qc_material_id'])}
    if payload['reagent_id'] is None:
        raise ValueError('请选择试剂产品。')
    for field, label in (('scope_note', '适用范围说明'), ('evidence', '适用依据'), ('confirmed_by', '核对人')):
        payload[field] = str(values.get(field) or '').strip()
        if not payload[field]:
            raise ValueError('请填写' + label + '。')
    with atomic_write() as connection:
        _active(connection, payload)
        before = {}
        if relation_id is not None:
            row = connection.execute('SELECT * FROM qc_product_recommendations WHERE id=?', (int(relation_id),)).fetchone()
            if row is None or expected_revision is None or row['revision_no'] != int(expected_revision):
                raise ValueError('适用关系已变化，请重新打开核对。')
            before = dict(row)
            if row['is_disabled']:
                raise ValueError('请先恢复适用关系，再编辑。')
        try:
            if relation_id is None:
                relation_id = connection.execute('INSERT INTO qc_product_recommendations (' + ','.join(payload) +
                    ') VALUES(' + ','.join('?' for _ in payload) + ')', list(payload.values())).lastrowid
            else:
                connection.execute('UPDATE qc_product_recommendations SET ' + ','.join(key + '=?' for key in payload) +
                    ',revision_no=revision_no+1,updated_at=CURRENT_TIMESTAMP,confirmed_at=CURRENT_TIMESTAMP WHERE id=?',
                    [*payload.values(), int(relation_id)])
        except sqlite3.IntegrityError as exc:
            raise ValueError('此试剂、质控品及适用范围已有关系，请编辑原记录。') from exc
        after = dict(connection.execute('SELECT * FROM qc_product_recommendations WHERE id=?', (relation_id,)).fetchone())
        _audit(connection, relation_id, 'edit' if before else 'create', before, after, payload['confirmed_by'], payload['evidence'])
        return int(relation_id)


def set_qc_product_relation_disabled(relation_id, *, disabled, reason, expected_revision, confirmed_by):
    reason, actor = str(reason or '').strip(), str(confirmed_by or '').strip()
    if not reason or not actor:
        raise ValueError('请填写操作原因及核对人。')
    with atomic_write() as connection:
        row = connection.execute('SELECT * FROM qc_product_recommendations WHERE id=?', (int(relation_id),)).fetchone()
        if row is None or int(expected_revision) != row['revision_no']:
            raise ValueError('适用关系已变化，请重新打开核对。')
        before = dict(row)
        if not disabled:
            _active(connection, before)
        connection.execute('''UPDATE qc_product_recommendations SET is_disabled=?,disabled_reason=?,
            revision_no=revision_no+1,updated_at=CURRENT_TIMESTAMP WHERE id=?''', (int(bool(disabled)), reason, int(relation_id)))
        after = dict(connection.execute('SELECT * FROM qc_product_recommendations WHERE id=?', (int(relation_id),)).fetchone())
        _audit(connection, int(relation_id), 'disable' if disabled else 'restore', before, after, actor, reason)
