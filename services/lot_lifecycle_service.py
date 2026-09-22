"""Shared, append-only lot usage and per-result provenance for the three workbenches."""
from __future__ import annotations

from services.cv_service import calculate_cv_percent

import json
import math
from datetime import datetime

import pandas as pd

from database import atomic_write, get_connection

CONTEXT_COLUMNS = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}
RESULT_TABLES = {'lj': 'results', 'zscore': 'zscore_runs', 'instant': 'instant_results'}


def timestamp(value) -> str:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('请填写有效日期时间。')
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert('Asia/Shanghai').tz_localize(None)
    return stamp.strftime('%Y-%m-%d %H:%M:%S')


def _required(value, label):
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'请填写{label}。')
    return value


def source_context(connection, method, batch_id):
    if method not in CONTEXT_COLUMNS:
        raise ValueError('不支持的质控方法。')
    binding = connection.execute('SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?', (method,batch_id)).fetchone()
    runtime_table = 'instant_batches' if method == 'instant' else 'batches'
    batch = connection.execute(f'SELECT * FROM {runtime_table} WHERE id=?', (batch_id,)).fetchone()
    if batch is None:
        raise ValueError('未找到批次。')
    source = json.loads(binding['source_snapshot_json'] or '{}') if binding else {}
    if not source and method == 'lj':
        source = json.loads(batch['source_config_snapshot_json'] or '{}')
    if not source:
        project_table = 'instant_projects' if method == 'instant' else 'projects'
        project = connection.execute(f'SELECT * FROM {project_table} WHERE id=?',(batch['project_id'],)).fetchone()
        source = {'test_item_name': project['name'], 'input_value_type': project['input_value_type'],
                  'instrument_name': batch['instrument'], 'reagent_name': batch['reagent'],
                  'qc_material_name': batch['qc_material'], 'lot_no': batch['lot_no'], 'provenance':'migration_available'}
    source = dict(source)
    source['runtime_method'],source['runtime_batch_id'] = method,int(batch_id)
    if binding:
        source.setdefault('lot_config_item_id',binding['lot_config_item_id'])
        source.setdefault('project_template_item_id',binding['project_template_item_id'])
    identity = source.get('identity') or []
    if source.get('project_template_item_id') and len(identity) >= 8:
        system_identity = json.dumps([source['project_template_item_id'], *[identity[i] for i in (0,3,4,5,6,7)]])
        connection.execute('INSERT OR IGNORE INTO qc_detection_systems(template_item_id,identity_json,snapshot_json) VALUES(?,?,?)',
            (source['project_template_item_id'],system_identity,json.dumps(source,ensure_ascii=False)))
        source['system_id'] = connection.execute('SELECT id FROM qc_detection_systems WHERE identity_json=?',(system_identity,)).fetchone()[0]
    return source, binding, batch


def require_writable(connection, method, batch_id, test_time=None):
    source,binding,batch = source_context(connection,method,batch_id)
    if batch['is_disabled'] or (method=='instant' and batch['transfer_status']=='transferred'):
        raise ValueError('该批次已停用或已转入 LJ 法，当前为只读。')
    if binding is not None:
        row = connection.execute('''SELECT c.status,c.is_disabled,t.status AS template_status,t.is_disabled AS template_disabled,
            i.is_enabled,i.is_disabled AS item_disabled,l.state,l.effective_at FROM qc_lot_configs c
            JOIN qc_lot_config_items i ON i.lot_config_id=c.id
            JOIN qc_project_templates t ON t.id=c.template_id
            LEFT JOIN qc_config_item_lifecycle l ON l.lot_config_item_id=i.id WHERE i.id=?''',(binding['lot_config_item_id'],)).fetchone()
        if (binding['binding_status']!='active' or row is None or row['status']!='active' or row['is_disabled']
            or row['template_status']!='active' or row['template_disabled'] or not row['is_enabled']
            or row['item_disabled'] or effective_qc_state(connection,binding['lot_config_item_id']) in ('ended','pending')):
            raise ValueError('配置已变更、停用或停止使用，请刷新并重新确认；历史记录仍可查看。')
        if test_time is not None and effective_qc_state(connection,binding['lot_config_item_id'],test_time) in ('ended','pending'):
            raise ValueError('检测时间不在该质控批次的可使用期间，请核对新旧批同时使用及停止使用时间。')
        current=connection.execute("""SELECT c.lab_instrument_id,c.qc_material_id,c.qc_material_lot_id,i.test_item_id,i.input_value_type,i.unit_id,i.method_id,i.reagent_id,c.material_selection_mode
            FROM qc_lot_config_items i JOIN qc_lot_configs c ON c.id=i.lot_config_id WHERE i.id=?""",(binding['lot_config_item_id'],)).fetchone()
        material_mode = bool(current['material_selection_mode'])
        # A material combination's header lot is an internal anchor, not every assay's actual lot.
        identity_indices = (0, 1, 3, 4, 5, 6, 7) if material_mode else range(8)
        if source.get('identity') and any(current[index] != source['identity'][index] for index in identity_indices):
            raise ValueError('检测系统配置已变更，请刷新并重新建立适用配置；旧结果保持原上下文。')
        if material_mode:
            actual_levels = connection.execute('''SELECT a.qc_level_id,l.qc_material_lot_id
                FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
                WHERE a.lot_config_item_id=? AND a.is_disabled=0 ORDER BY a.level_order,a.id''',
                (binding['lot_config_item_id'],)).fetchall()
            frozen_levels = source.get('levels', [])
            if ([row['qc_level_id'] for row in actual_levels] != [row['qc_level_id'] for row in frozen_levels]
                or any(frozen.get('qc_material_lot_id') != current_level['qc_material_lot_id']
                       for frozen, current_level in zip(frozen_levels, actual_levels))):
                raise ValueError('实际质控材料或水平顺序已变更，请建立新批次；旧结果保持原上下文。')
        if source.get('identity'):
            for table,index in [('lab_instruments',0),('md_qc_materials',1),('md_qc_material_lots',2),('md_test_items',3),('md_units',5),('md_methods',6),('md_reagents',7)]:
                if material_mode and index == 2:
                    continue
                identity=source['identity'][index]
                if identity and not connection.execute(f'SELECT 1 FROM {table} WHERE id=? AND is_disabled=0',(identity,)).fetchone():
                    raise ValueError('检测系统引用的基础资料已停用，请刷新确认。')
        for level in source.get('levels',[]):
            if level.get('qc_level_id') and not connection.execute("""SELECT 1 FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
                WHERE l.id=? AND l.is_disabled=0 AND q.is_disabled=0""",(level['qc_level_id'],)).fetchone():
                raise ValueError('当前水平或其质控批号已停用，请刷新确认。')
    return source


def create_reagent_lot(*, reagent_id, lot_no, expiry_date, source_text=''):
    lot_no=_required(lot_no,'试剂批号')
    expiry_date=pd.Timestamp(expiry_date).strftime('%Y-%m-%d')
    with atomic_write() as c:
        if not c.execute('SELECT 1 FROM md_reagents WHERE id=? AND is_disabled=0',(reagent_id,)).fetchone():
            raise ValueError('请选择启用的试剂产品。')
        if c.execute('SELECT 1 FROM md_reagent_lots WHERE reagent_id=? AND lot_no=?',(reagent_id,lot_no)).fetchone():
            raise ValueError('该试剂产品已存在此批号。')
        return c.execute('INSERT INTO md_reagent_lots(reagent_id,lot_no,expiry_date,source_text) VALUES(?,?,?,?)',
            (reagent_id,lot_no,expiry_date,str(source_text or '').strip())).lastrowid


def list_reagent_lots(reagent_id=None, include_disabled=False):
    with get_connection() as c:
        return pd.read_sql_query('''SELECT l.*,r.generic_name AS reagent_name,m.display_name AS manufacturer_name FROM md_reagent_lots l
            JOIN md_reagents r ON r.id=l.reagent_id LEFT JOIN md_manufacturers m ON m.id=r.manufacturer_id WHERE (? IS NULL OR l.reagent_id=?)
            AND (? OR (l.is_disabled=0 AND r.is_disabled=0)) ORDER BY l.id DESC''',c,params=(reagent_id,reagent_id,int(include_disabled)))


def workbench_systems():
    with atomic_write() as c:
        bindings=c.execute("SELECT qc_method,runtime_batch_id FROM qc_workbench_bindings WHERE binding_status='active'").fetchall()
        for b in bindings:
            source_context(c,b['qc_method'],b['runtime_batch_id'])
        return [dict(r) for r in c.execute('SELECT * FROM qc_detection_systems ORDER BY id')]


def _resolve_system(c,template_item_id,system_id=None):
    rows=c.execute('SELECT * FROM qc_detection_systems WHERE template_item_id=?',(template_item_id,)).fetchall()
    if system_id is not None:
        rows=[r for r in rows if r['id']==int(system_id)]
    if len(rows)!=1:
        raise ValueError('请选择明确的检测系统（检验项目、仪器、方法、单位和试剂产品）。')
    return rows[0]


def record_lot_verification(*, template_item_id, conclusion, evidence, confirmed_by, confirmed_at,
                            reagent_lot_id=None, qc_lot_id=None, system_id=None):
    if conclusion not in ('pass','fail') or (reagent_lot_id is None)==(qc_lot_id is None):
        raise ValueError('请选择一种批号和明确的验证结论。')
    evidence=_required(evidence,'验证依据及结论说明');confirmed_by=_required(confirmed_by,'确认人')
    workbench_systems()
    with atomic_write() as c:
        system=_resolve_system(c,template_item_id,system_id)
        item=c.execute('SELECT * FROM qc_project_template_items WHERE id=?',(template_item_id,)).fetchone()
        if item is None:
            raise ValueError('检验项目不存在。')
        if reagent_lot_id is not None:
            lot=c.execute('SELECT * FROM md_reagent_lots WHERE id=?',(reagent_lot_id,)).fetchone()
            if lot is None or lot['reagent_id']!=json.loads(system['snapshot_json'])['identity'][7] or lot['is_disabled']:
                raise ValueError('验证试剂批号必须属于该检测项的试剂产品。')
        if qc_lot_id is not None:
            lot=c.execute('SELECT * FROM md_qc_material_lots WHERE id=? AND is_disabled=0',(qc_lot_id,)).fetchone()
            if lot is None or lot['qc_material_id']!=json.loads(system['snapshot_json'])['identity'][1]:
                raise ValueError('质控验证批号必须属于该检测配置的质控品。')
        return c.execute('''INSERT INTO qc_lot_verifications(template_item_id,reagent_lot_id,qc_lot_id,conclusion,evidence,confirmed_by,confirmed_at,system_id)
            VALUES(?,?,?,?,?,?,?,?)''',(template_item_id,reagent_lot_id,qc_lot_id,conclusion,evidence,confirmed_by,timestamp(confirmed_at),system['id'])).lastrowid


def usage_revision(connection, system_id):
    return int(connection.execute('SELECT COALESCE(MAX(id),0) FROM qc_reagent_lot_usage WHERE system_id=?',(system_id,)).fetchone()[0])


def qc_lots_for_source(connection, source):
    """Resolve every actual lot in a combination, preserving the displayed level order."""
    lots={}
    for level in source.get('levels',[]):
        row=connection.execute('''SELECT q.*,l.is_disabled AS level_disabled
            FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            WHERE l.id=?''',(level.get('qc_level_id'),)).fetchone()
        if row is None:
            raise ValueError('质控水平或实际批号不存在，请核对配置。')
        record=lots.setdefault(row['id'],{**dict(row),'level_names':[]})
        record['level_disabled']=record['level_disabled'] or row['level_disabled']
        record['level_names'].append(level.get('level_name',''))
    if not lots:
        lot_id=source.get('qc_material_lot_id') or source['identity'][2]
        row=connection.execute('SELECT * FROM md_qc_material_lots WHERE id=?',(lot_id,)).fetchone()
        if row is None:raise ValueError('实际质控批号不存在，请核对配置。')
        lots[row['id']]={**dict(row),'level_disabled':False,'level_names':[]}
    return lots


def _require_qc_verification(connection, source, lot_id, verification_id, effective_at):
    when=timestamp(effective_at)
    lot=connection.execute('SELECT * FROM md_qc_material_lots WHERE id=?',(lot_id,)).fetchone()
    if lot is None or lot['qc_material_id']!=source['identity'][1]:
        raise ValueError('验证质控批号必须属于当前检测配置的质控品。')
    if lot['is_disabled']:
        raise ValueError('实际质控批号已停用，不能正式使用或建立换批组合。')
    if not lot['expiry_date'] or lot['expiry_date']<when[:10]:
        raise ValueError('生效时间不得晚于实际质控批号效期，请核对效期。')
    verification=connection.execute('SELECT * FROM qc_lot_verifications WHERE id=?',(verification_id,)).fetchone()
    if (verification is None or verification['system_id']!=source['system_id']
        or verification['template_item_id']!=source['project_template_item_id']
        or verification['qc_lot_id']!=lot_id or verification['conclusion']!='pass'
        or verification['confirmed_at']>when):
        raise ValueError('需关联本检测项、本实际质控批号已通过的验证记录，生效时间不得早于验证。')
    latest=connection.execute('''SELECT id,conclusion FROM qc_lot_verifications
        WHERE system_id=? AND qc_lot_id=? AND confirmed_at<=?
        ORDER BY confirmed_at DESC,id DESC LIMIT 1''',(source['system_id'],lot_id,when)).fetchone()
    if latest is None or latest['id']!=verification['id'] or latest['conclusion']!='pass':
        raise ValueError('所选质控验证已被后续结论替代，请核对生效时间对应的最新验证结论。')
    return verification['id']


def switch_reagent_lots(*, selections, effective_at, operator, reason):
    """Selections explicitly name every assay, verification and optimistic revision."""
    operator=_required(operator,'操作者');reason=_required(reason,'换批原因');when=timestamp(effective_at)
    if not selections or len({(s.get('system_id'),s['template_item_id']) for s in selections})!=len(selections):
        raise ValueError('请选择不重复的检测项。')
    events=[]
    with atomic_write() as c:
        for s in selections:
            item_id=int(s['template_item_id']);lot_id=int(s['reagent_lot_id'])
            system=_resolve_system(c,item_id,s.get('system_id'));system_id=system['id']
            if usage_revision(c,system_id)!=int(s['expected_revision']):
                raise ValueError('批号使用状态已被其他窗口修改，请刷新预览后重新确认。')
            row=c.execute('''SELECT v.*,l.expiry_date,l.is_disabled,i.reagent_id,l.reagent_id AS lot_reagent
                FROM qc_lot_verifications v JOIN md_reagent_lots l ON l.id=v.reagent_lot_id
                JOIN qc_project_template_items i ON i.id=v.template_item_id WHERE v.id=?''',(s['verification_id'],)).fetchone()
            if (row is None or row['template_item_id']!=item_id or row['system_id']!=system_id or row['reagent_lot_id']!=lot_id or row['conclusion']!='pass'
                or row['is_disabled'] or row['lot_reagent']!=json.loads(system['snapshot_json'])['identity'][7] or row['confirmed_at']>when
                or row['expiry_date']<when[:10]):
                raise ValueError('每个检测项都需关联该试剂批号已通过的验证，且生效时间不得早于验证或晚于效期。')
            latest_verification=c.execute('SELECT id,conclusion FROM qc_lot_verifications WHERE system_id=? AND reagent_lot_id=? AND confirmed_at<=? ORDER BY confirmed_at DESC,id DESC LIMIT 1',(system_id,lot_id,when)).fetchone()
            if not latest_verification or latest_verification['id']!=row['id'] or latest_verification['conclusion']!='pass':
                raise ValueError('所选验证已被后续结论替代，请重新核对该批号的最新适用性结论。')
            previous=c.execute('SELECT reagent_lot_id FROM qc_reagent_lot_usage WHERE system_id=? AND effective_at<=? ORDER BY effective_at DESC,id DESC LIMIT 1',(system_id,when)).fetchone()
            event=c.execute('''INSERT INTO qc_lot_change_events(template_item_id,event_type,previous_id,next_id,effective_at,reason,operator,verification_id,system_id)
                VALUES(?,'reagent',?,?,?,?,?,?,?)''',(item_id,previous[0] if previous else None,lot_id,when,reason,operator,row['id'],system_id)).lastrowid
            c.execute('INSERT INTO qc_reagent_lot_usage(template_item_id,reagent_lot_id,effective_at,event_id,verification_id,system_id) VALUES(?,?,?,?,?,?)',
                (item_id,lot_id,when,event,row['id'],system_id))
            events.append(event)
    return events


def result_lot_options(method,batch_id,test_time):
    when=timestamp(test_time)
    with get_connection() as c:
        source,_,_=source_context(c,method,batch_id)
        item=source.get('project_template_item_id');system_id=source.get('system_id')
        if not system_id:
            return {'template_item_id':None,'revision':0,'options':[],'suggested_lot_id':None}
        choices=c.execute('''SELECT l.*,MAX(v.id) AS verification_id FROM md_reagent_lots l
            JOIN qc_lot_verifications v ON v.reagent_lot_id=l.id JOIN md_reagents r ON r.id=l.reagent_id
            WHERE r.is_disabled=0 AND v.system_id=? AND v.conclusion='pass' AND v.confirmed_at<=? AND l.is_disabled=0
            AND l.expiry_date>=? AND v.id=(SELECT v2.id FROM qc_lot_verifications v2 WHERE v2.system_id=v.system_id AND v2.reagent_lot_id=l.id AND v2.confirmed_at<=? ORDER BY v2.confirmed_at DESC,v2.id DESC LIMIT 1) GROUP BY l.id ORDER BY l.id''',(system_id,when,when[:10],when)).fetchall()
        default=c.execute('SELECT reagent_lot_id FROM qc_reagent_lot_usage WHERE system_id=? AND effective_at<=? ORDER BY effective_at DESC,id DESC LIMIT 1',(system_id,when)).fetchone()
        return {'template_item_id':item,'system_id':system_id,'revision':usage_revision(c,system_id),'options':[dict(r) for r in choices],
                'suggested_lot_id':default[0] if default else None}


def record_result_context(connection, method, result_id, batch_id, test_time, selection=None, *, provenance='recorded', source_context_id=None):
    source,_,_=source_context(connection,method,batch_id)
    item=source.get('project_template_item_id');system_id=source.get('system_id');lot=None;usage=None
    selection=selection or {}
    if provenance=='recorded':
        require_writable(connection,method,batch_id,test_time)
        revision=usage_revision(connection,system_id) if system_id else 0
        # Legacy imports without explicit lot must remain unknown, never inherit today's default.
        if 'expected_revision' in selection and int(selection['expected_revision'])!=revision:
            raise ValueError('试剂批号已切换，请刷新并重新确认本次实际使用批号。')
        if selection.get('reagent_lot_id'):
            choices=result_lot_options(method,batch_id,test_time)
            lot=next((r for r in choices['options'] if r['id']==int(selection['reagent_lot_id'])),None)
            if lot is None:
                raise ValueError('所选批号不适用于该项目或检测时间，需先完成验证并核对效期。')
            usage=connection.execute('''SELECT id FROM qc_reagent_lot_usage WHERE system_id=? AND reagent_lot_id=?
                AND effective_at<=? ORDER BY effective_at DESC,id DESC LIMIT 1''',(system_id,lot['id'],timestamp(test_time))).fetchone()
        elif revision and not selection.get('allow_unknown'):
            raise ValueError('请明确选择本次实际使用的试剂批号；旧资料导入可明确标记未记录。')
    target=selection.get('target_profile_id')
    if provenance=='recorded' and method in ('lj','zscore') and target is None and any(level.get('target_source','building')!='building' for level in source.get('levels',[])):
        raise ValueError('请先在项目管理的均值和标准差管理中确认全部水平参数、依据、确认人及生效时间。')
    if target is not None and not connection.execute('SELECT 1 FROM qc_target_profiles WHERE id=? AND qc_method=? AND batch_id=?',(target,method,batch_id)).fetchone():
        raise ValueError('参数版本不属于当前批次。')
    context_id=connection.execute(f'''INSERT INTO qc_result_contexts({CONTEXT_COLUMNS[method]},config_snapshot_json,reagent_lot_id,
        reagent_lot_no,reagent_expiry_date,usage_id,target_profile_id,provenance,source_context_id) VALUES(?,?,?,?,?,?,?,?,?)''',
        (result_id,json.dumps(source,ensure_ascii=False),lot['id'] if lot else None,lot['lot_no'] if lot else '',
         lot['expiry_date'] if lot else '',usage[0] if usage else None,target,provenance,source_context_id)).lastrowid
    for i,level in enumerate(source.get('levels') or [{'level_name':source.get('concentration_label','')}],1):
        qc_lot_id=level.get('qc_material_lot_id') or source.get('qc_material_lot_id')
        if qc_lot_id is None and level.get('qc_level_id'):
            row=connection.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?',(level['qc_level_id'],)).fetchone()
            qc_lot_id=row[0] if row else None
        connection.execute('''INSERT INTO qc_result_context_levels(context_id,level_order,qc_level_id,qc_lot_id,lot_no,level_name,level_code,expiry_date)
            VALUES(?,?,?,?,?,?,?,?)''',(context_id,i,level.get('qc_level_id'),qc_lot_id,level.get('lot_no') or source.get('lot_no',''),level.get('level_name',''),level.get('level_code',''),level.get('expiry_date','')))
    return context_id


def transfer_result_context(connection, source_result_id, target_result_id, target_batch_id):
    original=connection.execute('SELECT * FROM qc_result_contexts WHERE instant_result_id=?',(source_result_id,)).fetchone()
    if original is None:
        source_result=connection.execute('SELECT * FROM instant_results WHERE id=?',(source_result_id,)).fetchone()
        record_result_context(connection,'instant',source_result_id,source_result['batch_id'],source_result['test_time'],provenance='migration_available')
        original=connection.execute('SELECT * FROM qc_result_contexts WHERE instant_result_id=?',(source_result_id,)).fetchone()
    cid=connection.execute('''INSERT INTO qc_result_contexts(lj_result_id,config_snapshot_json,reagent_lot_id,reagent_lot_no,
        reagent_expiry_date,usage_id,provenance,source_context_id) VALUES(?,?,?,?,?,?,'instant_transfer',?)''',
        (target_result_id,original['config_snapshot_json'],original['reagent_lot_id'],original['reagent_lot_no'],original['reagent_expiry_date'],original['usage_id'],original['id'])).lastrowid
    connection.execute('''INSERT INTO qc_result_context_levels(context_id,level_order,qc_level_id,qc_lot_id,lot_no,level_name,level_code,expiry_date)
        SELECT ?,level_order,qc_level_id,qc_lot_id,lot_no,level_name,level_code,expiry_date FROM qc_result_context_levels WHERE context_id=?''',(cid,original['id']))
    return cid


def context_dataframe(method,batch_id):
    with get_connection() as c:
        rows=c.execute(f'''SELECT x.*,r.test_time,r.id AS result_id FROM qc_result_contexts x
            JOIN {RESULT_TABLES[method]} r ON r.id=x.{CONTEXT_COLUMNS[method]} WHERE r.batch_id=? ORDER BY r.test_time,r.id''',(batch_id,)).fetchall()
    records=[]
    for r in rows:
        with get_connection() as c:
            levels=c.execute('SELECT level_name,level_code,lot_no,expiry_date FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order',(r['id'],)).fetchall()
        from services.material_workflow_service import concentration_label
        qc_label=' / '.join(f"{concentration_label(level)}: {level['lot_no']}" for level in levels)
        s=json.loads(r['config_snapshot_json']);records.append({'result_id':r['result_id'],'test_time':r['test_time'],
            'context_id':r['id'],'reagent_lot_no':r['reagent_lot_no'] or '未记录','reagent_expiry_date':r['reagent_expiry_date'],
            'qc_lot_no':qc_label or s.get('lot_no','未记录'),'instrument':s.get('instrument_name',''),
            'reagent':s.get('reagent_name',''),'unit_symbol':s.get('unit_symbol',''),
            'method_name':s.get('method_name',''),'target_profile_id':r['target_profile_id'],
            'provenance':r['provenance'],'source_context_id':r['source_context_id']})
    return pd.DataFrame(records)


def append_evaluation(method,result_id,payload,reason='current_review'):
    data=json.dumps(_json_safe(payload),ensure_ascii=False,sort_keys=True,allow_nan=False)
    with get_connection() as c:
        context=c.execute(f'SELECT id,provenance FROM qc_result_contexts WHERE {CONTEXT_COLUMNS[method]}=?',(result_id,)).fetchone()
        if context is None:
            return
        previous=c.execute('SELECT id,evaluation_json FROM qc_result_evaluations WHERE context_id=? ORDER BY id DESC LIMIT 1',(context[0],)).fetchone()
        if previous and previous['evaluation_json']==data:
            return
        if previous is None:
            reason='recorded' if context['provenance']=='recorded' else 'migration_review' if context['provenance'].startswith('migration') else 'instant_transfer_review'
        c.execute('''INSERT INTO qc_result_evaluations(context_id,evaluation_json,reason,previous_id) VALUES(?,?,?,?)''',
            (context[0],data,reason,previous['id'] if previous else None))


def backfill_result_contexts():
    """Unknown historic reagent lots remain unknown; never assign current usage."""
    count=0
    with atomic_write() as c:
        for method,table in RESULT_TABLES.items():
            rows=c.execute(f'''SELECT r.id,r.batch_id,r.test_time FROM {table} r LEFT JOIN qc_result_contexts x
                ON x.{CONTEXT_COLUMNS[method]}=r.id WHERE x.id IS NULL ORDER BY r.id''').fetchall()
            for row in rows:
                source,_,_=source_context(c,method,row['batch_id'])
                provenance='migration_snapshot' if source.get('config_snapshot_id') and source.get('provenance')!='migration_available' else 'migration_available'
                record_result_context(c,method,row['id'],row['batch_id'],row['test_time'],provenance=provenance)
                count+=1
    return count


def list_lot_events(system_id):
    with get_connection() as c:
        return pd.read_sql_query('SELECT * FROM qc_lot_change_events WHERE system_id=? ORDER BY effective_at,id',c,params=(system_id,))


def change_qc_lot(*, source_config_id, target_qc_lot_id, template_item_ids, operator, reason, effective_at):
    from services.project_config_service import copy_lot_config, activate_lot_config
    operator=_required(operator,'操作者');reason=_required(reason,'换批原因')
    if not template_item_ids:
        raise ValueError('请选择本次换批的检测项。')
    with atomic_write() as c:
        source=c.execute('SELECT * FROM qc_lot_configs WHERE id=?',(source_config_id,)).fetchone()
        if source is None or source['qc_material_lot_id']==int(target_qc_lot_id):
            raise ValueError('请选择不同的质控品批号。')
        available={r[0] for r in c.execute('SELECT source_template_item_id FROM qc_lot_config_items WHERE lot_config_id=? AND is_disabled=0',(source_config_id,))}
        if not set(template_item_ids)<=available:
            raise ValueError('所选检测项不属于来源配置。')
        existing=c.execute("SELECT id,status,activated_at FROM qc_lot_configs WHERE template_id=? AND qc_material_lot_id=? AND combination_key='' AND is_disabled=0",(source['template_id'],target_qc_lot_id)).fetchone()
        new_id=existing[0] if existing else copy_lot_config(source_lot_config_id=source_config_id,target_qc_material_lot_id=target_qc_lot_id)
        items=c.execute('SELECT * FROM qc_lot_config_items WHERE lot_config_id=?',(new_id,)).fetchall()
        for item in items:
            if item['source_template_item_id'] not in template_item_ids:
                if not existing:c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?',(item['id'],))
                continue
            if existing and item['is_enabled']:
                raise ValueError('该检测项已经在目标批次中，请直接使用其已有批次或调整使用状态。')
            if existing and (existing['status'] == 'active' or existing['activated_at']
                or c.execute('SELECT 1 FROM qc_workbench_bindings WHERE lot_config_id=?', (new_id,)).fetchone()):
                raise ValueError('目标批次已经固定，不能追加尚未确认的检测项；请建立新的批次草稿并核对质量目标。')
            c.execute('UPDATE qc_lot_config_items SET is_enabled=1 WHERE id=?',(item['id'],))
            old_binding=c.execute('''SELECT b.* FROM qc_workbench_bindings b WHERE b.lot_config_id=?
                AND b.project_template_item_id=?''',(source_config_id,item['source_template_item_id'])).fetchone()
            if old_binding is None:
                raise ValueError('请先确认来源批次，再为该检验项目换批。')
            old_source,_,_=source_context(c,old_binding['qc_method'],old_binding['runtime_batch_id'])
            event=c.execute('''INSERT INTO qc_lot_change_events(system_id,template_item_id,event_type,previous_id,next_id,effective_at,reason,operator,details_json)
                VALUES(?,?,'qc',?,?,?,?,?,?)''',(old_source['system_id'],item['source_template_item_id'],source['qc_material_lot_id'],target_qc_lot_id,
                timestamp(effective_at),reason,operator,json.dumps({'source_config_id':source_config_id,'new_config_id':new_id}))).lastrowid
            c.execute('INSERT INTO qc_config_item_lifecycle(lot_config_item_id,state,effective_at,event_id) VALUES(?,\'parallel\',?,?)',
                (item['id'],timestamp(effective_at),event))
        # Building configs can immediately collect parallel observations. Copied targets stay draft for explicit confirmation.
        pending=c.execute("SELECT 1 FROM qc_lot_config_item_levels l JOIN qc_lot_config_items i ON i.id=l.lot_config_item_id WHERE i.lot_config_id=? AND i.is_enabled=1 AND l.target_source='copied_pending'",(new_id,)).fetchone()
        goal_pending=c.execute("SELECT 1 FROM qc_lot_config_items WHERE lot_config_id=? AND is_enabled=1 AND json_extract(quality_goal_json,'$.pending')=1",(new_id,)).fetchone()
        from services.quality_review_service import validate_quality_review
        review_pending = any(validate_quality_review('lot', item['id'], c)
            for item in c.execute('SELECT id FROM qc_lot_config_items WHERE lot_config_id=? AND is_enabled=1', (new_id,)))
        if not pending and not goal_pending and not review_pending:
            activate_lot_config(new_id)
        return new_id


def set_qc_usage_state(*, lot_config_item_id, state, effective_at, operator, reason, verification_id=None, verification_ids=None):
    if state not in ('parallel','active','ended'):
        raise ValueError('未知使用状态。')
    operator=_required(operator,'操作者');reason=_required(reason,'状态变更依据')
    with atomic_write() as c:
        binding=c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?',(lot_config_item_id,)).fetchone()
        if binding is None:
            raise ValueError('请先确认本批次设置，再调整使用状态。')
        source,_,_=source_context(c,binding['qc_method'],binding['runtime_batch_id'])
        confirmed_verifications={}
        if state=='active':
            lots=qc_lots_for_source(c,source)
            selected={int(k):v for k,v in (verification_ids or {}).items()}
            if verification_id is not None and len(lots)==1:
                selected.setdefault(next(iter(lots)),verification_id)
            if set(selected)!=set(lots) or any(v is None for v in selected.values()):
                raise ValueError('正式使用需逐一关联全部实际质控批号的验证记录。')
            for lot_id,lot in lots.items():
                if lot['level_disabled']:raise ValueError('实际质控水平已停用，不能正式使用。')
                confirmed_verifications[lot_id]=_require_qc_verification(c,source,lot_id,selected[lot_id],effective_at)
            if binding['qc_method']=='lj':
                from database import get_results,get_batch
                from qc_logic import calculate_qc_results
                if target_profile('lj',binding['runtime_batch_id'],effective_at) is None:
                    _,stats=calculate_qc_results(get_results(binding['runtime_batch_id']),get_batch(binding['runtime_batch_id'])['target_n'])
                    if not stats.get('target_ready'):raise ValueError('该批次控制参数尚未确认，继续新旧批同时使用观察或确认均值和标准差后再正式使用。')
            elif binding['qc_method']=='zscore':
                from zscore_logic import get_zscore_level_targets,resolve_zscore_batch_context,should_enable_formal_rules
                context=resolve_zscore_batch_context(binding['runtime_batch_id'])
                profiles=get_zscore_level_targets(binding['runtime_batch_id'],context['template_id'],at_time=effective_at)
                if not should_enable_formal_rules(profiles,context['template']['level_ids']):raise ValueError('全部水平控制参数确认后才可启用正式联合判定。')
        event=c.execute('''INSERT INTO qc_lot_change_events(system_id,template_item_id,event_type,next_id,effective_at,reason,operator,verification_id,details_json)
            VALUES(?,?,?,?,?,?,?,?,?)''',(source['system_id'],source['project_template_item_id'],state,lot_config_item_id,timestamp(effective_at),reason,operator,
            next(iter(confirmed_verifications.values())) if len(confirmed_verifications)==1 else None,
            json.dumps({'qc_verification_ids':confirmed_verifications} if confirmed_verifications else {}))).lastrowid
        c.execute('''INSERT INTO qc_config_item_lifecycle(lot_config_item_id,state,effective_at,event_id) VALUES(?,?,?,?)
            ON CONFLICT(lot_config_item_id) DO UPDATE SET state=excluded.state,effective_at=excluded.effective_at,event_id=excluded.event_id''',
            (lot_config_item_id,state,timestamp(effective_at),event))
        return event


def target_profile(method,batch_id,test_time=None,profile_id=None):
    with get_connection() as c:
        if profile_id is not None:
            row=c.execute('SELECT * FROM qc_target_profiles WHERE id=? AND qc_method=? AND batch_id=?',(profile_id,method,batch_id)).fetchone()
        else:
            row=c.execute('''SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? AND effective_at<=?
                ORDER BY effective_at DESC,id DESC LIMIT 1''',(method,batch_id,timestamp(test_time or datetime.now()))).fetchone()
    if row is None:
        return None
    result=dict(row);result['levels']=json.loads(row['levels_json']);return result


def create_target_profile(*, method,batch_id,levels,source,evidence,confirmed_by,effective_at,source_result_ids=None):
    if method not in ('lj','zscore') or source not in ('building','manual','manufacturer','revision'):
        raise ValueError('不支持的控制参数来源。')
    evidence=_required(evidence,'靶值依据');confirmed_by=_required(confirmed_by,'确认人')
    with atomic_write() as c:
        snapshot=require_writable(c,method,batch_id)
        count=1 if method=='lj' else len(snapshot.get('levels') or levels)
        if len(levels)!=count or [x['level_id'] for x in levels]!=[f'Level {i+1}' for i in range(count)]:
            raise ValueError('必须按顺序完整确认全部水平，不能只启用部分水平。')
        for level in levels:
            if not all(math.isfinite(float(level[k])) for k in ('mean','sd')) or float(level['sd'])<=0:
                raise ValueError('所有水平均须填写有限均值及大于 0 的 SD。')
            if snapshot.get('input_value_type')=='raw' and float(level['mean'])<=0:
                raise ValueError('真实检测值的设定均值必须大于 0。')
        when=timestamp(effective_at)
        previous=c.execute('SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? ORDER BY version_no DESC LIMIT 1',(method,batch_id)).fetchone()
        if previous and when<=previous['effective_at']:
            raise ValueError('新参数版本生效时间必须晚于已有版本。')
        latest=c.execute(f'SELECT MAX(test_time) FROM {RESULT_TABLES[method]} WHERE batch_id=?',(batch_id,)).fetchone()[0]
        if latest and when<timestamp(latest):
            raise ValueError('不能追溯修改已有检测的参数；请选择不早于最后一条检测的生效时间。')
        if source=='building' and not source_result_ids:
            raise ValueError('本批次均值和标准差建立版本必须关联实际有效参数建立记录。')
        for result_id in source_result_ids or []:
            if not c.execute(f'SELECT 1 FROM {RESULT_TABLES[method]} WHERE id=? AND batch_id=?',(result_id,batch_id)).fetchone():
                raise ValueError('均值和标准差来源记录必须属于当前方法和质控批次。')
        profile_id=c.execute('''INSERT INTO qc_target_profiles(qc_method,batch_id,version_no,effective_at,source,levels_json,evidence,confirmed_by,source_result_ids_json)
            VALUES(?,?,?,?,?,?,?,?,?)''',(method,batch_id,1 if previous is None else previous['version_no']+1,when,source,
            json.dumps(levels,ensure_ascii=False),evidence,confirmed_by,json.dumps(source_result_ids or []))).lastrowid
        if snapshot.get('system_id'):
            c.execute('''INSERT INTO qc_lot_change_events(system_id,template_item_id,event_type,previous_id,next_id,effective_at,reason,operator,details_json)
                VALUES(?,?,'target',?,?,?,?,?,?)''',(snapshot['system_id'],snapshot['project_template_item_id'],previous['id'] if previous else None,profile_id,when,evidence,confirmed_by,json.dumps({'method':method,'batch_id':batch_id})))
        return profile_id


def overlay_zscore_profiles(profiles,version):
    if version is None:
        return profiles
    from copy import deepcopy
    profiles=deepcopy(profiles)
    for level in version['levels']:
        p=profiles[level['level_id']]
        p.update({'target_mean':float(level['mean']),'target_sd':float(level['sd']),
                  'target_cv':calculate_cv_percent(level['mean'], level['sd']),
                  'is_ready':True,'phase':'formal_qc','phase_label':'正式质控','target_source':version['source'],'target_profile_id':version['id'],
                  'final_target_mean':float(level['mean']),'final_target_sd':float(level['sd']),
                  'target_mean_final':float(level['mean']),'target_sd_final':float(level['sd']),
                  'final_target_cv':calculate_cv_percent(level['mean'], level['sd']),
                  'target_cv_final':calculate_cv_percent(level['mean'], level['sd'])})
    return profiles


def result_context_map(connection,method,batch_id):
    return {r['result_id']:dict(r) for r in connection.execute(f'''SELECT x.*,r.id AS result_id FROM qc_result_contexts x
        JOIN {RESULT_TABLES[method]} r ON r.id=x.{CONTEXT_COLUMNS[method]} WHERE r.batch_id=?''',(batch_id,))}


def prepare_lj_target(batch_id,test_time,selection):
    from database import get_batch,get_results
    from qc_logic import calculate_qc_results
    selection=dict(selection or {})
    version=target_profile('lj',batch_id,test_time)
    if version is None and target_profile('lj',batch_id,'9999-12-31') is None:
        batch=get_batch(batch_id);results=get_results(batch_id,include_manual_note=True)
        qc,stats=calculate_qc_results(results,int(batch['target_n']))
        if stats.get('target_ready') and stats.get('sd') and float(stats['sd'])>0:
            ids=qc.loc[(qc.phase=='建靶数据') & (qc.is_building_included==1),'id'].astype(int).tolist()
            pid=create_target_profile(method='lj',batch_id=batch_id,levels=[{'level_id':'Level 1','mean':stats['mean'],'sd':stats['sd']}],
                source='building',evidence='由有效建靶记录固化；原始记录 ID 保存在版本中',confirmed_by='系统：既有建靶规则',effective_at=test_time,source_result_ids=ids)
            version=target_profile('lj',batch_id,profile_id=pid)
    if version:
        selection['target_profile_id']=version['id']
    return selection


def attach_context_columns(dataframe,method,batch_id):
    if dataframe.empty:
        return dataframe
    with get_connection() as c:
        contexts=result_context_map(c,method,batch_id)
    result=dataframe.copy()
    result['target_profile_id']=[contexts.get(int(i),{}).get('target_profile_id') for i in result['id']]
    result['actual_reagent_lot']=[contexts.get(int(i),{}).get('reagent_lot_no') or '未记录' for i in result['id']]
    result['context_id']=[contexts.get(int(i),{}).get('id') for i in result['id']]
    result.attrs['qc_batch_id']=batch_id
    # Mark actual transitions without resetting statistical windows.
    known=result['actual_reagent_lot'].ne('未记录')
    changes=known & known.shift(fill_value=False) & result['actual_reagent_lot'].ne(result['actual_reagent_lot'].shift())
    if 'reagent_lot_changed' in result:
        result['reagent_lot_changed']=result['reagent_lot_changed'].astype(bool) | changes
    return result


def correct_reagent_event(*,event_id,reagent_lot_id,verification_id,effective_at,operator,reason,expected_revision):
    with atomic_write() as c:
        old=c.execute("SELECT * FROM qc_lot_change_events WHERE id=? AND event_type IN ('reagent','correction')",(event_id,)).fetchone()
        if old is None:
            raise ValueError('请选择可更正的试剂使用事件。')
        event=switch_reagent_lots(selections=[{'template_item_id':old['template_item_id'],'system_id':old['system_id'],
            'reagent_lot_id':reagent_lot_id,'verification_id':verification_id,'expected_revision':expected_revision}],
            effective_at=effective_at,operator=operator,reason=reason)[0]
        c.execute("UPDATE qc_lot_change_events SET event_type='correction',corrects_event_id=? WHERE id=?",(event_id,event))
        return event


def _json_safe(value):
    if isinstance(value,dict):
        return {str(k):_json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value,(str,bool,int)):
        return value
    if isinstance(value,float):
        return value if math.isfinite(value) else None
    try:
        if pd.isna(value):
            return None
    except (ValueError,TypeError):
        pass
    return str(value)


def resolve_import_lot(method,batch_id,test_time,lot_no):
    """Only explicit matching; blanks in legacy files remain unknown."""
    options=result_lot_options(method,batch_id,test_time)
    label=str(lot_no or '').strip()
    selection={'expected_revision':options['revision']}
    if not label or label=='未记录':
        return {**selection,'allow_unknown':True}
    matches=[r for r in options['options'] if r['lot_no'].casefold()==label.casefold()]
    if len(matches)!=1:
        raise ValueError(f'试剂批号 {label} 未匹配到该检测时间已验证且未过期的唯一批号；请先登记并验证，或修正文件。')
    return {**selection,'reagent_lot_id':matches[0]['id']}


def review_import_lots(review,method,batch_id):
    """Freeze matching and optimistic revision at preview, never again at submit."""
    from import_review import _make_issue,_build_review_result
    issues=list(review['issues']);rows=[]
    for index,row in enumerate(review['normalized_rows'],2):
        row=dict(row)
        try:row['lot_selection']=resolve_import_lot(method,batch_id,row['test_time'],row.get('actual_reagent_lot'))
        except ValueError as exc:issues.append(_make_issue(row_label=index,field_name='实际试剂批号',message=str(exc),is_blocking=True))
        rows.append(row)
    if rows and any(r.get('lot_selection',{}).get('allow_unknown') for r in rows):
        issues.append(_make_issue(row_label='文件级',field_name='实际试剂批号',message='未填写批号的行将明确保存为未记录，不使用当前默认批号。',is_blocking=False))
    return _build_review_result(total_rows=review['summary']['total_rows'],normalized_rows=rows,issues=issues)


def import_reviewed_results(method,batch_id,rows,*,template_id=None,required_n=None):
    from database import add_result
    from zscore_logic import create_zscore_run
    with atomic_write():
        for row in rows:
            selection=row.get('lot_selection')
            if selection is None:
                raise ValueError('缺少批号匹配预览，请重新审查文件。')
            if method=='lj':
                add_result(batch_id=batch_id,test_time=row['test_time'],operator=row['operator'],value=row['value'],
                    log_value=row.get('log_value'),reagent_lot_changed=row.get('reagent_lot_changed',0),manual_note=row.get('manual_note',''),lot_selection=selection)
            elif method=='zscore':
                create_zscore_run(batch_id=batch_id,test_time=row['test_time'],operator=row['operator'],level_results=row['level_results'],
                    template_id=template_id,required_n=required_n,manual_note=row.get('manual_note',''),lot_selection=selection)
            else:raise ValueError('不支持的导入方法。')
    return len(rows)


def validate_result_edit(connection,method,result_id,new_time=None):
    row=connection.execute(f'SELECT * FROM {RESULT_TABLES[method]} WHERE id=?',(result_id,)).fetchone()
    if row is None:raise ValueError('未找到检测记录。')
    require_writable(connection,method,row['batch_id'],new_time)
    context=connection.execute(f'SELECT * FROM qc_result_contexts WHERE {CONTEXT_COLUMNS[method]}=?',(result_id,)).fetchone()
    if new_time and context:
        profile=target_profile(method,row['batch_id'],new_time) if method!='instant' else None
        if (profile['id'] if profile else None)!=context['target_profile_id']:
            raise ValueError('修改检测时间会跨越控制参数版本，请保留原记录并通过有说明的新录入更正。')
        if context['reagent_lot_id']:
            options=result_lot_options(method,row['batch_id'],new_time)
            if context['reagent_lot_id'] not in [x['id'] for x in options['options']]:
                raise ValueError('修改后的检测时间不适用原试剂批号。')
    return row


def report_lot_trace(method,batch_id,month,frame):
    """Capture a self-contained report snapshot; no later lookup on history read."""
    df=context_dataframe(method,batch_id)
    if df.empty:return {}
    df=df[df.test_time.astype(str).str.startswith(month)]
    if df.empty:return {}
    groups=[]
    value_col='value' if method=='lj' else 'raw_value'
    keys=['target_profile_id']+(['level_id'] if method=='zscore' else [])
    if not frame.empty and 'target_profile_id' in frame:
        for key,group in frame.groupby(keys,dropna=False,sort=False):
            key=key if isinstance(key,tuple) else (key,)
            values=pd.to_numeric(group[value_col],errors='coerce').dropna()
            profile_id=None if pd.isna(key[0]) else int(key[0]);profile=target_profile(method,batch_id,profile_id=profile_id) if profile_id else None
            level_id=key[1] if len(key)>1 else 'Level 1'
            level=next((l for l in profile['levels'] if l['level_id']==level_id),{}) if profile else {}
            groups.append({'profile_id':profile_id,'version':profile['version_no'] if profile else '旧建靶序列','level':level_id,'count':len(values),
                'monthly_mean':values.mean() if len(values) else None,'monthly_sd':values.std(ddof=1) if len(values)>1 else None,
                'target_mean':level.get('mean'),'target_sd':level.get('sd'),'source':profile['source'] if profile else 'legacy',
                'evidence':profile['evidence'] if profile else '原有建靶记录；历史参数资料未记录时不补造',
                'confirmed_by':profile['confirmed_by'] if profile else '', 'effective_at':profile['effective_at'] if profile else ''})
    with get_connection() as c:
        source,_,_=source_context(c,method,batch_id)
        events=[dict(r) for r in c.execute('SELECT * FROM qc_lot_change_events WHERE system_id=? AND substr(effective_at,1,7)=? ORDER BY effective_at,id',(source.get('system_id'),month))]
    return _json_safe({'actual_lots':df.to_dict('records'),'statistics_by_target_version':groups,'events':events,
        'basis':'按本次结果保存的批号及参数版本回顾；累计判定按各版本自己的窗口，缺失历史批号显示未记录。'})


def create_level_combination(*,source_batch_id,level_ids,verification_ids,operator,reason,effective_at):
    """Replace one or more actual QC levels; every run retains its original combination."""
    from services.project_config_service import copy_lot_config,save_lot_item_levels,activate_lot_config
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    operator=_required(operator,'操作者');reason=_required(reason,'水平换批依据')
    with atomic_write() as c:
        source,binding,_=source_context(c,'zscore',source_batch_id)
        if binding is None:raise ValueError('请从项目中选择已确认的多水平批次。')
        old_levels=source['levels']
        if len(level_ids)!=len(old_levels) or len(set(level_ids))!=len(level_ids):raise ValueError('请选择完整且不重复的水平组合。')
        old_ids=[l['qc_level_id'] for l in old_levels]
        if level_ids==old_ids:raise ValueError('新组合必须至少更换一个水平。')
        old_target=target_profile('zscore',source_batch_id,effective_at)
        chosen=[]
        for order,lid in enumerate(level_ids,1):
            level=c.execute('''SELECT l.*,q.qc_material_id,q.lot_no,q.expiry_date,q.is_disabled AS lot_disabled FROM md_qc_levels l
                JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id WHERE l.id=?''',(lid,)).fetchone()
            if (level is None or level['is_disabled'] or level['lot_disabled'] or level['qc_material_id']!=source['identity'][1]
                or level['expiry_date']<timestamp(effective_at)[:10]):
                raise ValueError('各水平必须属于同一质控产品、对应浓度顺序且未过期。')
            old_level=c.execute('SELECT * FROM md_qc_levels WHERE id=?',(old_ids[order-1],)).fetchone()
            if old_level['specification_id']:
                if level['specification_id']!=old_level['specification_id']: raise ValueError('换批须选择对应浓度规格的新材料；改变规格请重新建立批次。')
            elif level['level_code'] or old_level['level_code']:
                if (level['level_code'],level['level_name'])!=(old_level['level_code'],old_level['level_name']): raise ValueError('请选择对应浓度水平和编号的新材料。')
            elif level['level_order']!=old_level['level_order']:
                raise ValueError('请选择与原材料对应的浓度水平。')
            vid=verification_ids.get(lid)
            if lid!=old_ids[order-1]:
                _require_qc_verification(c,source,level['qc_material_lot_id'],vid,effective_at)
            chosen.append((level,vid))
        key=json.dumps(level_ids,separators=(',',':'))
        new=copy_lot_config(source_lot_config_id=binding['lot_config_id'],target_qc_material_lot_id=chosen[0][0]['qc_material_lot_id'],
            config_name=source['config_name']+'｜新水平组合',combination_key=key)
        items=c.execute('SELECT * FROM qc_lot_config_items WHERE lot_config_id=?',(new,)).fetchall()
        selected=None
        for item in items:
            if item['source_template_item_id']!=source['project_template_item_id']:
                c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?',(item['id'],));continue
            selected=item['id']
        if selected is None:raise ValueError('新组合未匹配来源检测项。')
        for order,(level,vid) in enumerate(chosen):
            c.execute('INSERT INTO qc_level_combination_members(lot_config_item_id,qc_level_id,source_profile_id,verification_id) VALUES(?,?,?,?)',
                (selected,level['id'],old_target['id'] if old_target and level['id']==old_ids[order] else None,vid))
        save_lot_item_levels(selected,[{'qc_level_id':lid,'target_source':'building'} for lid in level_ids])
        event=c.execute('''INSERT INTO qc_lot_change_events(system_id,template_item_id,event_type,effective_at,reason,operator,details_json)
            VALUES(?,?,'qc',?,?,?,?)''',(source['system_id'],source['project_template_item_id'],timestamp(effective_at),reason,operator,
            json.dumps({'source_batch_id':source_batch_id,'new_config_id':new,'old_level_ids':old_ids,'new_level_ids':level_ids,'retained_target_profile_id':old_target['id'] if old_target else None}))).lastrowid
        c.execute("INSERT INTO qc_config_item_lifecycle VALUES(?,'parallel',?,?)",(selected,timestamp(effective_at),event))
        goal_pending=c.execute("SELECT 1 FROM qc_lot_config_items WHERE id=? AND json_extract(quality_goal_json,'$.pending')=1",(selected,)).fetchone()
        from services.quality_review_service import validate_quality_review
        if not goal_pending and not validate_quality_review('lot', selected, c):
            activate_lot_config(new);sync_zscore_workbench_bindings()
        return new


def atomic_action(function):
    from functools import wraps
    @wraps(function)
    def operation(*args,**kwargs):
        with atomic_write():return function(*args,**kwargs)
    return operation


def purge_demo_provenance(connection,project_ids=(),instant_project_ids=()):
    """For the existing explicit demo refresh only; reject every non-demo project."""
    batch_ids=[];instant_batch_ids=[]
    for projects,project_table,batch_table,target in ((project_ids,'projects','batches',batch_ids),(instant_project_ids,'instant_projects','instant_batches',instant_batch_ids)):
        for project_id in projects:
            row=connection.execute(f'SELECT name FROM {project_table} WHERE id=?',(project_id,)).fetchone()
            if row is None or not row[0].startswith(('[DEMO]','【演示】')):
                raise ValueError('演示数据清理不能操作正式项目。')
            target.extend(r[0] for r in connection.execute(f'SELECT id FROM {batch_table} WHERE project_id=?',(project_id,)))
    context_ids=set()
    for method,batches in [('lj',batch_ids),('zscore',batch_ids),('instant',instant_batch_ids)]:
        for batch_id in batches:
            context_ids.update(r[0] for r in connection.execute(f'SELECT x.id FROM qc_result_contexts x JOIN {RESULT_TABLES[method]} r ON r.id=x.{CONTEXT_COLUMNS[method]} WHERE r.batch_id=?',(batch_id,)))
    for cid in context_ids:
        if any(r[0] not in context_ids for r in connection.execute('SELECT id FROM qc_result_contexts WHERE source_context_id=?',(cid,))):
            raise ValueError('演示记录被其他项目引用，请保留该演示项目以维持追溯。')
    for cid in sorted(context_ids,reverse=True):
        for evaluation in connection.execute('SELECT id FROM qc_result_evaluations WHERE context_id=? ORDER BY id DESC',(cid,)).fetchall():
            connection.execute('DELETE FROM qc_result_evaluations WHERE id=?',(evaluation[0],))
        connection.execute('DELETE FROM qc_result_context_levels WHERE context_id=?',(cid,))
        connection.execute('DELETE FROM qc_result_contexts WHERE id=?',(cid,))
    for batch_id in batch_ids:
        connection.execute('DELETE FROM qc_target_profiles WHERE batch_id=?',(batch_id,))


def effective_qc_state(connection,item_id,at_time=None):
    when=timestamp(at_time or datetime.now())
    current=connection.execute("SELECT event_type FROM qc_lot_change_events WHERE next_id=? AND event_type IN ('active','parallel','ended') AND effective_at<=? ORDER BY effective_at DESC,id DESC LIMIT 1",(item_id,when)).fetchone()
    if current:return current[0]
    initial=connection.execute("""SELECT e.effective_at FROM qc_lot_change_events e JOIN qc_lot_config_items i
        ON i.source_template_item_id=e.template_item_id AND i.lot_config_id=json_extract(e.details_json,'$.new_config_id')
        WHERE i.id=? AND e.event_type='qc' ORDER BY e.id LIMIT 1""",(item_id,)).fetchone()
    if initial:return 'parallel' if initial[0]<=when else 'pending'
    return 'active'
