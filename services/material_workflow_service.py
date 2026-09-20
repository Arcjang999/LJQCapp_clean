"""Register physical controls and compose initial/replacement batches atomically."""
from uuid import uuid4
import json
import pandas as pd
from database import atomic_write, get_connection


def concentration_label(level):
    row = dict(level)
    name = str(row.get('level_name') or '')
    code = str(row.get('level_code') or '')
    return name + (f'（编号 {code}）' if code else '')


def material_label(level):
    row = dict(level)
    return f"{concentration_label(row)}｜批号 {row.get('lot_no') or '未记录'}｜效期 {row.get('expiry_date') or '未记录'}"


def config_material_summary(config_id):
    with get_connection() as c:
        rows=c.execute('''SELECT DISTINCT l.level_name,l.level_code,q.lot_no,q.expiry_date
            FROM qc_lot_config_items i JOIN qc_lot_config_item_levels a ON a.lot_config_item_id=i.id
            JOIN md_qc_levels l ON l.id=a.qc_level_id JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            WHERE i.lot_config_id=? AND i.is_disabled=0 AND i.is_enabled=1 AND a.is_disabled=0 ORDER BY a.level_order''',(config_id,)).fetchall()
        return '；'.join(material_label(r) for r in rows) or '未选择质控品'


def save_project_defaults(template_id,*,qc_method,method_id,level_count,project_group):
    from services.project_config_service import QC_METHOD_LABELS
    if qc_method not in QC_METHOD_LABELS: raise ValueError('请选择质控方式。')
    count=int(level_count) if qc_method=='zscore' else 1
    if qc_method=='zscore' and count not in (2,3): raise ValueError('多水平质控使用 2 或 3 个水平。')
    with atomic_write() as c:
        if not c.execute('SELECT id FROM qc_project_templates WHERE id=? AND is_disabled=0',(template_id,)).fetchone(): raise ValueError('项目不存在或已停用。')
        if method_id and not c.execute('SELECT id FROM md_methods WHERE id=? AND is_disabled=0',(method_id,)).fetchone(): raise ValueError('检测方法学已停用。')
        c.execute('UPDATE qc_project_templates SET default_qc_method=?,default_method_id=?,default_level_count=?,project_group=?,revision_no=revision_no+1 WHERE id=?',
            (qc_method,method_id,count,str(project_group or '').strip(),template_id))


def list_material_specs(material_id):
    with get_connection() as c:
        return pd.read_sql_query('SELECT * FROM md_qc_material_specs WHERE qc_material_id=? ORDER BY id',c,params=(int(material_id),))


def create_material_spec(material_id, level_name, level_code='', catalog_no=''):
    name,code,catalog = (str(x or '').strip() for x in (level_name,level_code,catalog_no))
    if not name: raise ValueError('请填写浓度水平。')
    if any(len(x)>100 for x in (name,code,catalog)): raise ValueError('浓度水平、编号和货号各不能超过 100 字。')
    with atomic_write() as c:
        if not c.execute('SELECT id FROM md_qc_materials WHERE id=? AND is_disabled=0',(material_id,)).fetchone():
            raise ValueError('请选择未停用的质控品。')
        existing=c.execute('SELECT * FROM md_qc_material_specs WHERE qc_material_id=? AND level_name=? AND level_code=?',(material_id,name,code)).fetchone()
        if existing:
            if catalog and catalog!=existing['catalog_no']: raise ValueError('此浓度水平和浓度编号已填写不同货号，请核对货号，或选择已有的浓度水平。')
            return existing['id']
        return c.execute('INSERT INTO md_qc_material_specs(uid,qc_material_id,level_name,level_code,catalog_no) VALUES (?,?,?,?,?)',
                         (str(uuid4()),material_id,name,code,catalog)).lastrowid


def register_control_material(*,material_id,lot_no,expiry_date,specification_id=None,level_name='',level_code='',catalog_no='',concentration_note=''):
    from services.master_data_service import create_qc_lot,create_qc_level,_date_text
    expiry=_date_text(expiry_date)
    if not expiry: raise ValueError('请填写效期。')
    with atomic_write() as c:
        spec_id=specification_id or create_material_spec(material_id,level_name,level_code,catalog_no)
        spec=c.execute('SELECT * FROM md_qc_material_specs WHERE id=? AND qc_material_id=?',(spec_id,material_id)).fetchone()
        if not spec: raise ValueError('所选浓度水平不属于此质控品，请重新选择浓度水平。')
        if not c.execute('SELECT id FROM md_qc_materials WHERE id=? AND is_disabled=0',(material_id,)).fetchone():
            raise ValueError('质控品已停用，请刷新后重新选择。')
        lot=c.execute('SELECT * FROM md_qc_material_lots WHERE qc_material_id=? AND LOWER(TRIM(lot_no))=LOWER(TRIM(?)) ORDER BY is_disabled,id DESC',(material_id,str(lot_no).strip())).fetchone()
        if lot and lot['is_disabled']: raise ValueError('此批号已停用，请先核对并恢复原批号，勿重复登记。')
        if lot and lot['expiry_date']!=expiry: raise ValueError('同一质控品的此批号已登记不同效期，请核对批号和效期。')
        lot_id=lot['id'] if lot else create_qc_lot(qc_material_id=material_id,lot_no=lot_no,expiry_date=expiry)
        used=c.execute('SELECT level_order,level_name,level_code,is_disabled FROM md_qc_levels WHERE qc_material_lot_id=?',(lot_id,)).fetchall()
        if any((r['level_name'],r['level_code'])==(spec['level_name'],spec['level_code']) for r in used):
            raise ValueError('此批号下已登记相同的浓度水平和浓度编号，请选择已有记录；已停用的记录请先恢复。')
        order=next((i for i in range(1,10) if i not in {r['level_order'] for r in used if not r['is_disabled']}),None)
        if order is None: raise ValueError('此批号已有 9 个浓度水平。')
        level_id=create_qc_level(qc_material_lot_id=lot_id,level_name=spec['level_name'],level_code=spec['level_code'],
            level_order=order,concentration_label=concentration_note)
        c.execute('UPDATE md_qc_levels SET specification_id=? WHERE id=?',(spec_id,level_id))
        return level_id


def available_materials(material_id,include_disabled=False):
    with get_connection() as c:
        return pd.read_sql_query('''SELECT l.*,q.lot_no,q.expiry_date,q.qc_material_id,
            q.is_disabled AS lot_disabled FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            JOIN md_qc_materials m ON m.id=q.qc_material_id WHERE q.qc_material_id=?'''+
            ('' if include_disabled else ' AND l.is_disabled=0 AND q.is_disabled=0 AND m.is_disabled=0')+' ORDER BY l.level_name,l.level_code,q.expiry_date DESC,l.id',c,params=(material_id,))


def validate_material_selection(c,material_id,level_ids,count):
    if len(level_ids)!=count or len(set(level_ids))!=count: raise ValueError(f'请为 {count} 个水平分别选择质控品，不要重复选择同一条记录。')
    rows=[]
    for lid in level_ids:
        row=c.execute('''SELECT l.*,q.qc_material_id,q.expiry_date,q.lot_no FROM md_qc_levels l
            JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id JOIN md_qc_materials m ON m.id=q.qc_material_id
            WHERE l.id=? AND l.is_disabled=0 AND q.is_disabled=0 AND m.is_disabled=0''',(lid,)).fetchone()
        if not row or row['qc_material_id']!=material_id: raise ValueError('所选浓度水平和批号不属于此质控品，或已停用，请重新选择。')
        if not row['expiry_date']: raise ValueError('所选批号未填写效期，请先在基础资料中补充。')
        rows.append(row)
    return rows


def create_material_config(*,template_id,selections,config_name='',quality_requirements=None):
    from services.project_config_service import create_lot_config_from_template,save_lot_item_levels
    with atomic_write() as c:
        template=c.execute('SELECT * FROM qc_project_templates WHERE id=? AND is_disabled=0',(template_id,)).fetchone()
        if not template: raise ValueError('未找到可用项目，请重新选择未停用的项目。')
        items=c.execute('SELECT * FROM qc_project_template_items WHERE template_id=? AND is_disabled=0 ORDER BY sort_order,id',(template_id,)).fetchall()
        if not items or set(selections)!={r['id'] for r in items}: raise ValueError('请为每个检验项目选择质控品。')
        resolved={r['id']:validate_material_selection(c,template['qc_material_id'],selections[r['id']],r['level_count']) for r in items}
        first=resolved[items[0]['id']][0]
        key='materials:'+json.dumps(selections,sort_keys=True,separators=(',',':'))
        config=create_lot_config_from_template(template_id=template_id,qc_material_lot_id=first['qc_material_lot_id'],
            config_name=config_name or template['template_name']+'｜批次设置',quality_requirements=quality_requirements,
            material_selection_mode=True,combination_key=key)
        for item in c.execute('SELECT id,source_template_item_id FROM qc_lot_config_items WHERE lot_config_id=?',(config,)).fetchall():
            save_lot_item_levels(item['id'],[dict(qc_level_id=lid,target_source='building') for lid in selections[item['source_template_item_id']]])
        return config


def copy_material_config(*,source_config_id,selections,config_name=''):
    from services.project_config_service import copy_lot_config,save_lot_item_levels,list_lot_item_levels
    with atomic_write() as c:
        source=c.execute('SELECT * FROM qc_lot_configs WHERE id=? AND is_disabled=0',(source_config_id,)).fetchone()
        if not source: raise ValueError('未找到原批次，请重新选择。')
        items=c.execute('SELECT * FROM qc_lot_config_items WHERE lot_config_id=? AND is_disabled=0 ORDER BY sort_order,id',(source_config_id,)).fetchall()
        if not selections or set(selections)-{r['id'] for r in items}: raise ValueError('请选择原批次中需要换批的检验项目。')
        items=[r for r in items if r['id'] in selections]
        resolved={r['id']:validate_material_selection(c,source['qc_material_id'],selections[r['id']],r['level_count']) for r in items}
        if all(list_lot_item_levels(r['id'])['qc_level_id'].tolist()==selections[r['id']] for r in items): raise ValueError('所选浓度水平和批号与原批次相同，请至少更换一个水平的质控品。')
        key='replacement:'+json.dumps(selections,sort_keys=True,separators=(',',':'))
        config=copy_lot_config(source_lot_config_id=source_config_id,target_qc_material_lot_id=resolved[items[0]['id']][0]['qc_material_lot_id'],
            combination_key=key,config_name=config_name or source['config_name']+'｜换批')
        c.execute('UPDATE qc_lot_configs SET material_selection_mode=1 WHERE id=?',(config,))
        for item in items:
            new=c.execute('SELECT id FROM qc_lot_config_items WHERE lot_config_id=? AND test_item_id=? AND qc_method=? AND input_value_type=?',
                (config,item['test_item_id'],item['qc_method'],item['input_value_type'])).fetchone()
            prior={r['qc_level_id']:r for r in list_lot_item_levels(item['id']).to_dict('records')}
            assignments=[]
            for lid in selections[item['id']]:
                old=prior.get(lid,{})
                assignments.append(dict(qc_level_id=lid,target_source='copied_pending' if old.get('target_mean') is not None else 'building',
                    target_mean=old.get('target_mean'),target_sd=old.get('target_sd'),target_confirmed=False))
            save_lot_item_levels(new['id'],assignments)
        wanted={r['test_item_id'] for r in items}
        for row in c.execute('SELECT id,test_item_id FROM qc_lot_config_items WHERE lot_config_id=?',(config,)).fetchall():
            if row['test_item_id'] not in wanted: c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?',(row['id'],))
        from services.project_config_service import _save_snapshot
        # save_lot_item_levels already recorded this revision; replace its draft snapshot before any activation.
        c.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1 WHERE id=?',(config,))
        _save_snapshot(c,config,action_type='edit',change_summary='已选择新批号，均值和标准差及质量目标待确认')
        return config
