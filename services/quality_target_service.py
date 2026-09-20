"""Versioned analytical specifications, explicit adoption, and non-destructive CV evaluation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import date, datetime

import pandas as pd

from database import atomic_write, get_connection
from services.cv_service import calculate_cv_percent


def normalize_name(value):
    return re.sub(r'[\s_\-（）()]', '', str(value)).casefold()


def normalize_unit(value):
    return str(value or '').strip().replace('μ','u').replace('µ','u').replace('％','%').replace('×','').replace(' ','').replace('⁹','^9').replace('¹²','^12')


def decode(value):
    return json.loads(value or '{}') if isinstance(value, str) else (value or {})


def list_catalog():
    with get_connection() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT payload_json FROM qc_quality_catalog ORDER BY origin,id')]


def get_requirement(identifier):
    with get_connection() as c:
        row = c.execute('SELECT payload_json FROM qc_quality_catalog WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise ValueError('所选质量要求不存在。')
    return json.loads(row[0])


def suggested_requirements(name):
    key = normalize_name(name)
    return [r for r in list_catalog() if key and any(normalize_name(x) == key for x in [r['name'], *r.get('aliases',[])])]


def finite_number(value, label, optional=False):
    if value is None or value == '':
        if optional: return None
        raise ValueError(f'{label}不能为空。')
    try: result = float(value)
    except (ValueError, TypeError): raise ValueError(f'{label}须为有限数值。') from None
    if not math.isfinite(result): raise ValueError(f'{label}须为有限数值。')
    return result


def select_rule(spec, concentration=None, category=''):
    matches=[]
    for r in spec['imprecision']:
        if r.get('category') and r['category'] != category: continue
        if any(k in r for k in ('lower','upper')):
            if concentration is None: continue
            if 'lower' in r and (concentration < r['lower'] or (concentration == r['lower'] and not r.get('lower_inclusive',True))): continue
            if 'upper' in r and (concentration > r['upper'] or (concentration == r['upper'] and not r.get('upper_inclusive',True))): continue
        matches.append(r)
    if len(matches) != 1:
        raise ValueError('请核对浓度值和水平类别，以确定适用的允许不精密度要求。')
    return dict(matches[0])


def source_label(spec):
    return f"{spec['standard']} / {spec['version']}；{spec['source_clause']}；{spec['name']}"


def requirement_text(rule):
    return f"{'<' if rule['operator']=='<' else '≤'} {rule['value']:g}{rule['unit']}（{'CV' if rule['kind']=='cv' else 'SD'}）"


def item_context(scope, item_id, connection=None):
    if scope not in ('project','lot'): raise ValueError('无法确定要设置质量目标的项目或批次，请重新打开设置。')
    table = 'qc_project_template_items' if scope=='project' else 'qc_lot_config_items'
    def read(c):
        row=c.execute(f'''SELECT i.*, t.chinese_name AS test_item_name, u.symbol AS unit_symbol
            FROM {table} i JOIN md_test_items t ON t.id=i.test_item_id
            LEFT JOIN md_units u ON u.id=i.unit_id WHERE i.id=? AND i.is_disabled=0''',(int(item_id),)).fetchone()
        if row is None: raise ValueError('未找到该检验项目，请重新选择。')
        return dict(row)
    if connection is not None:return read(connection)
    with get_connection() as c:return read(c)


def validate_spec_for_item(spec, item):
    if item['input_value_type'] != 'raw':
        raise ValueError('该分析质量要求适用于原始检测值，不能直接用于 Ct 或 log 值。')
    if not item['unit_symbol']: raise ValueError('请先设置测量单位。')
    if spec.get('measurement_unit') and normalize_unit(spec['measurement_unit']) != normalize_unit(item['unit_symbol']):
        raise ValueError(f"单位不匹配：要求使用 {spec['measurement_unit']}，当前为 {item['unit_symbol']}；本版不自动换算。")
    if date.fromisoformat(spec['effective_date']) > date.today():
        raise ValueError('该标准或实验室要求尚未到实施日期，暂不能采用。')


def _require_draft(c, item):
    config=c.execute('SELECT status,is_disabled,activated_at FROM qc_lot_configs WHERE id=?',(item['lot_config_id'],)).fetchone()
    used=c.execute('SELECT 1 FROM qc_workbench_bindings WHERE lot_config_item_id=?',(item['id'],)).fetchone()
    if not config or config['is_disabled'] or config['status']!='draft' or config['activated_at'] or used:
        raise ValueError('已有批次的质量要求保持不变；请在尚未启用的新批次中设置。')


def common_cv(goal):
    # Only a uniform inclusive CV limit can safely use the legacy scalar field.
    rules=[v['rule'] for v in goal.get('levels',[])] if goal.get('levels') else goal['spec']['imprecision']
    if rules and all(r['kind']=='cv' and r['operator']=='<=' for r in rules):
        values={r['value'] for r in rules}
        if len(values)==1:return next(iter(values))
    return None


def adopt_requirement(scope, item_id, requirement_id, *, confirmed_by, evidence, levels=None, exclusions=None):
    if not str(confirmed_by).strip() or not str(evidence).strip():
        raise ValueError('请填写确认人和适用依据。')
    spec=get_requirement(requirement_id)
    with atomic_write() as c:
        item=item_context(scope,item_id,c);validate_spec_for_item(spec,item)
        goal=dict(spec=spec,unit=item['unit_symbol'],test_item_id=item['test_item_id'],
                  confirmed_by=str(confirmed_by).strip(),evidence=str(evidence).strip(),
                  adopted_at=datetime.now().isoformat(timespec='seconds'),levels=[],pending=scope=='project')
        if scope=='lot':
            _require_draft(c,item)
            assigned=c.execute('SELECT qc_level_id,level_order FROM qc_lot_config_item_levels WHERE lot_config_item_id=? AND is_disabled=0 ORDER BY level_order',(item_id,)).fetchall()
            if len(assigned)!=item['level_count']: raise ValueError('请先保存完整的质控水平设置。')
            proposed={int(v['level_order']):v for v in levels or []}
            if set(proposed)!={r['level_order'] for r in assigned}:raise ValueError('请逐一确认全部质控水平的适用要求。')
            for row in assigned:
                value=proposed[row['level_order']]
                concentration=finite_number(value.get('concentration'),'浓度值',optional=True)
                if concentration is not None and concentration<0:raise ValueError('浓度值不能为负数。')
                category=value.get('category','')
                selected=select_rule(spec,concentration,category)
                if spec['id']=='wst406-2024-fib' and category=='异常':
                    if concentration is None or not (concentration>6 or concentration<1.5):raise ValueError('Fib 异常水平须填写浓度，且 >6 g/L 或 <1.5 g/L。')
                goal['levels'].append(dict(level_order=row['level_order'],qc_level_id=row['qc_level_id'],concentration=concentration,category=category,rule=selected))
            goal['pending']=False
        table='qc_project_template_items' if scope=='project' else 'qc_lot_config_items'
        c.execute(f'UPDATE {table} SET quality_goal_json=?,cv_limit=?,quality_target_source_text=? WHERE id=?',
            (json.dumps(goal,ensure_ascii=False,allow_nan=False),common_cv(goal),source_label(spec),item_id))
        from services.quality_review_service import build_review, save_review
        reviewed_item=item_context(scope,item_id,c)
        review=build_review(c,reviewed_item,source_spec=spec,confirmed_by=confirmed_by,
                            evidence=evidence,exclusions=exclusions)
        save_review(c,scope,item_id,review)
        if scope=='lot':
            from services.project_config_service import _save_snapshot
            c.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(item['lot_config_id'],))
            _save_snapshot(c,item['lot_config_id'],action_type='edit',change_summary='确认质量目标及各水平适用范围')
        else:
            c.execute("UPDATE qc_project_templates SET revision_no=revision_no+1,status='draft',updated_at=CURRENT_TIMESTAMP WHERE id=?",(item['template_id'],))
    return goal


def clear_requirement(scope,item_id):
    with atomic_write() as c:
        item=item_context(scope,item_id,c)
        if scope=='lot':_require_draft(c,item)
        table='qc_project_template_items' if scope=='project' else 'qc_lot_config_items'
        c.execute(f"UPDATE {table} SET quality_goal_json='{{}}',quality_review_json='{{}}',cv_limit=NULL,quality_target_source_text='' WHERE id=?",(item_id,))
        if scope=='lot':
            from services.project_config_service import _save_snapshot
            c.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1 WHERE id=?',(item['lot_config_id'],))
            _save_snapshot(c,item['lot_config_id'],action_type='edit',change_summary='清空草稿质量目标，需重新确认来源')
        else:c.execute("UPDATE qc_project_templates SET status='draft',revision_no=revision_no+1 WHERE id=?",(item['template_id'],))


def pending_copy(value):
    goal=decode(value)
    if goal:goal.update(pending=True,levels=[],confirmed_by='',evidence='')
    return json.dumps(goal,ensure_ascii=False)


def validate_lot_goal(item_id):
    item=item_context('lot',item_id);goal=decode(item['quality_goal_json'])
    from services.quality_review_service import validate_quality_review
    review_errors=validate_quality_review('lot',item_id)
    if not goal:return review_errors
    try:
        validate_spec_for_item(goal['spec'],item)
        if goal.get('pending') or not goal.get('levels'):raise ValueError('质量目标待核对，请逐水平确认浓度、水平类别和适用依据。')
        with get_connection() as c:
            current={(r[0],r[1]) for r in c.execute('SELECT level_order,qc_level_id FROM qc_lot_config_item_levels WHERE lot_config_item_id=? AND is_disabled=0',(item_id,))}
        if current!={(r['level_order'],r['qc_level_id']) for r in goal['levels']}:raise ValueError('质控水平已变更，请重新确认质量目标。')
        if goal['test_item_id']!=item['test_item_id'] or normalize_unit(goal['unit'])!=normalize_unit(item['unit_symbol']):raise ValueError('项目或单位已变更，请重新确认质量目标。')
    except ValueError as e:return list(dict.fromkeys([str(e),*review_errors]))
    return review_errors


def runtime_goal(method,batch_id):
    with get_connection() as c:
        binding=c.execute('SELECT source_snapshot_json FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?',(method,batch_id)).fetchone()
        if binding:return decode(decode(binding[0]).get('quality_goal_json'))
        if method=='lj':
            batch=c.execute('SELECT source_config_snapshot_json FROM batches WHERE id=?',(batch_id,)).fetchone()
            if batch:return decode(decode(batch[0]).get('quality_goal_json'))
    return {}


def evaluate_cv(goal,level_order,cv,*,count,days=None):
    entry=next((r for r in goal.get('levels',[]) if r['level_order']==level_order),None)
    if not entry or goal.get('pending'):return '未确认适用要求'
    if entry['rule']['kind']!='cv':return 'SD 要求仅展示，暂不自动评价'
    if count<2 or cv is None or not math.isfinite(float(cv)):return '数据不足，暂不评价'
    if goal['spec']['scope'].startswith('日间') and (days is None or days<2):return '不足两个检测日，暂不评价日间 CV'
    threshold=entry['rule']['value']
    return '满足所选 CV 要求' if (cv<threshold if entry['rule']['operator']=='<' else cv<=threshold) else '超出所选 CV 要求'


def batch_quality_summary(method,batch_id,month=None):
    goal=runtime_goal(method,batch_id)
    if not goal:return {}
    values={r['level_order']:[] for r in goal.get('levels',[])};dates={k:set() for k in values}
    def add(order,value,when):
        if order not in values or (month and not str(when).startswith(month)):return
        v=finite_number(value,'检测值',optional=True)
        if v is not None:values[order].append(v);dates[order].add(str(when)[:10])
    if method=='lj':
        from database import get_results,get_batch
        from qc_logic import calculate_qc_results
        frame,_=calculate_qc_results(get_results(batch_id),get_batch(batch_id)['target_n'])
        for r in frame.to_dict('records'):
            if r.get('phase')=='正式数据' and r.get('status')=='符合质控':add(1,r['value'],r['test_time'])
    elif method=='zscore':
        from zscore_logic import get_zscore_runs,resolve_zscore_batch_context
        context=resolve_zscore_batch_context(batch_id)
        for run in get_zscore_runs(batch_id,context['template_id']):
            if run.get('phase')!='formal_qc' or run.get('run_status') not in ('accept','normal'):continue
            for level in run.get('level_results',[]):
                if level.get('is_in_control_for_realtime_stats'):
                    add(int(level['level_id'].split()[-1]),level.get('raw_value'),run['test_time'])
    rows=[]
    for entry in goal.get('levels',[]):
        order=entry['level_order'];data=pd.Series(values[order],dtype=float)
        cv=calculate_cv_percent(data.mean(),data.std(ddof=1)) if len(data)>1 else None
        decision='即时法过渡期，暂不评价正式期 CV' if method=='instant' else evaluate_cv(goal,order,cv,count=len(data),days=len(dates[order]))
        rows.append(dict(level=f'水平 {order}',requirement=requirement_text(entry['rule']),category=entry['category'],concentration=entry['concentration'],count=len(data),days=len(dates[order]),cv=cv,decision=decision))
    return dict(goal=goal,rows=rows,period=month or '本批次全部正式期',statistics_scope='仅正式期在控结果；Z-score 按整次在控筛选；SD 要求、偏倚和总误差不自动评价')


IMPORT_COLUMNS=['检验项目','来源名称','版本','实施日期','单位','允许不精密度类型','上限','比较符','浓度下限','浓度上限','水平类别','允许偏倚说明','允许总误差说明','来源链接或依据','确认人']


def custom_template_csv():
    out=io.StringIO();w=csv.writer(out);w.writerow(IMPORT_COLUMNS)
    w.writerow(['示例项目','实验室SOP','1','2026-01-01','mg/L','CV',5,'<=','','','','','','SOP编号与条款',''])
    return out.getvalue().encode('utf-8-sig')


def preview_custom_csv(data):
    if len(data)>2_000_000:raise ValueError('导入文件不能超过 2 MB。')
    try:reader=csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    except UnicodeError:raise ValueError('请使用 UTF-8 CSV 文件。') from None
    if reader.fieldnames!=IMPORT_COLUMNS:raise ValueError('请使用提供的 CSV 模板，保留表头及顺序。')
    rows=[]
    for index,row in enumerate(reader,start=2):
        if index>501:raise ValueError('每次最多导入 500 条要求。')
        if None in row or any(v is None for v in row.values()):raise ValueError(f'第 {index} 行列数不正确。')
        row={k:v.strip() for k,v in row.items()}
        if any(len(v)>1000 for v in row.values()):raise ValueError(f'第 {index} 行字段不能超过 1000 字。')
        for key in ('检验项目','来源名称','版本','实施日期','单位','来源链接或依据','确认人'):
            if not row[key]:raise ValueError(f'第 {index} 行：{key}不能为空。')
        try:date.fromisoformat(row['实施日期'])
        except ValueError:raise ValueError(f'第 {index} 行实施日期须为 YYYY-MM-DD。') from None
        if row['允许不精密度类型'] not in ('CV','SD') or row['比较符'] not in ('<','<='):raise ValueError('类型须为 CV/SD，比较符须为 < 或 <=。')
        value=finite_number(row['上限'],'上限')
        if value<=0:raise ValueError('上限必须大于 0。')
        rule=dict(kind=row['允许不精密度类型'].lower(),value=value,operator=row['比较符'],unit='%' if row['允许不精密度类型']=='CV' else row['单位'])
        for key,label in [('lower','浓度下限'),('upper','浓度上限')]:
            v=finite_number(row[label],label,optional=True)
            if v is not None:
                if v<0:raise ValueError('浓度界限不能为负数。')
                rule[key]=v;rule[key+'_inclusive']=True
        if 'lower' in rule and 'upper' in rule and rule['lower']>rule['upper']:raise ValueError('浓度下限不能大于上限。')
        if row['水平类别']:rule['category']=row['水平类别']
        rows.append(dict(origin='custom',name=row['检验项目'],aliases=[],standard=row['来源名称'],version=row['版本'],effective_date=row['实施日期'],measurement_unit=row['单位'],scope='实验室自定义室内质量要求',imprecision=[rule],imprecision_text=requirement_text(rule),bias_text=row['允许偏倚说明'],tea_text=row['允许总误差说明'],source_url='',source_clause=row['来源链接或依据'],source_page=None,notes=f"导入确认人：{row['确认人']}；实验室自定义，非内置标准"))
    if not rows:raise ValueError('文件没有可导入的要求。')
    grouped={}
    for row in rows:
        key=tuple(row[k] for k in ('name','standard','version','effective_date','measurement_unit'))
        if key not in grouped:grouped[key]=row
        else:
            prior=grouped[key]
            prior['imprecision'].extend(row['imprecision'])
            for field in ('imprecision_text','bias_text','tea_text','source_clause','notes'):
                if row[field] and row[field] != prior[field]:prior[field]+='；'+row[field]
    for row in grouped.values():
        rules=row['imprecision']
        for index,left in enumerate(rules):
            for right in rules[index+1:]:
                if left.get('category') and right.get('category') and left['category']!=right['category']:continue
                if max(left.get('lower',-math.inf),right.get('lower',-math.inf))<=min(left.get('upper',math.inf),right.get('upper',math.inf)):
                    raise ValueError(f"{row['name']} 的适用范围重叠，请使用互斥的水平类别或浓度区间（导入区间包含端点）。")
        descriptions=[]
        for rule in rules:
            conditions=[rule['category']] if rule.get('category') else []
            if 'lower' in rule:conditions.append(f"浓度 ≥{rule['lower']:g} {row['measurement_unit']}")
            if 'upper' in rule:conditions.append(f"浓度 ≤{rule['upper']:g} {row['measurement_unit']}")
            descriptions.append(('、'.join(conditions)+'：' if conditions else '')+requirement_text(rule))
        row['imprecision_text']='；'.join(descriptions)
        row['id']='custom-'+hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    return list(grouped.values())


def import_custom_csv(data):
    rows=preview_custom_csv(data)
    count=0
    with atomic_write() as c:
        for r in rows:
            count+=c.execute('INSERT OR IGNORE INTO qc_quality_catalog(id,origin,payload_json) VALUES (?,?,?)',(r['id'],'custom',json.dumps(r,ensure_ascii=False,allow_nan=False))).rowcount
    return count
