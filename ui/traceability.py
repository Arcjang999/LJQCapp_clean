"""User-facing views of saved audit records. Stored payloads are never rewritten."""
from __future__ import annotations

from services.cv_service import calculate_cv_percent

import json

import pandas as pd

from services.value_type_service import get_measurement_label
from services.outlier_service import get_outlier_manual_status_label


STATUS_LABELS = {
    'accept': '在控', 'warning': '警告', 'reject': '失控', 'pending': '待判读',
    'normal': '正常', 'kept': '已保留', 'disabled': '已禁用', 'restored': '已恢复',
    'target_building': '建靶期', 'formal_qc': '正式质控',
    'manual': '实验室确认', 'manufacturer': '厂家赋值（已确认）',
    'building': '本批次建靶', 'revision': '靶值修订',
    'copied_pending': '复制参数（待确认）',
}
EVENT_LABELS = {'reagent': '试剂换批', 'correction': '试剂换批更正',
                'qc': '质控品换批', 'target': '控制参数确认',
                'parallel': '开始平行使用', 'active': '正式启用', 'ended': '结束使用'}


def _display(value):
    if isinstance(value, (list, tuple)):
        return '、'.join(str(item) for item in value) or '无'
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return '未记录'
    if isinstance(value, bool):
        return '是' if value else '否'
    return STATUS_LABELS.get(str(value), str(value)) or '无'


def evaluation_tables(payload, input_value_type='raw', level_names=None):
    """Summarize the saved conclusion and its numerical evidence, without recalculation."""
    result=payload.get('result',payload)
    formal = result.get('phase') in ('formal_qc', '正式数据', '正式期')
    value_label=get_measurement_label(input_value_type)
    fields=[('test_time','检测时间'),('operator','检测人'),('phase','阶段'),
            ('status','记录状态'),('run_status','本次检测结论'),('value',value_label),
            ('rule_hits','触发规则'),('rule_hits_run','本次触发规则'),('z','Z 值'),
            ('is_building_included','参与建靶'),('is_effective','参与有效统计'),
            ('manual_status','人工处理状态'),('grubbs_statistic','格拉布斯统计量'),
            ('grubbs_threshold','格拉布斯临界值'),('si_upper','上侧 SI'),('si_lower','下侧 SI'),
            ('si_n2s','SI 警告界限'),('si_n3s','SI 失控界限'),
            ('analysis_prompt','判读说明'),('manual_note','人工备注')]
    def saved_value(key, value):
        if key == 'manual_status':
            return get_outlier_manual_status_label(value) if value is not None else '未记录'
        if key in ('is_building_included', 'is_effective') and value is not None:
            return _display(bool(value))
        return _display(value)
    summary=[{'项目':label,'记录内容':saved_value(key,result[key])} for key,label in fields
             if key in result and not (formal and key == 'is_building_included')]
    for key,label in [('target_mean','判读靶均值'),('target_sd','判读 SD')]:
        if key in payload:summary.append({'项目':label,'记录内容':_display(payload[key])})
    levels=[]
    for level in result.get('level_results',[]):
        lid=level.get('level_id','')
        levels.append({
            '质控水平':(level_names or {}).get(lid,lid.replace('Level ','水平 ')),
            value_label:level.get('raw_value'), '靶均值':level.get('target_mean'),
            'SD':level.get('target_sd'),'Z-score':level.get('zscore'),
            '状态':_display(level.get('status')),
            '触发规则':_display(level.get('rule_hits_local',[])),
            '参与建靶':'不适用（正式期）' if formal else saved_value('is_building_included',level.get('is_building_included')),
            '人工处理状态':saved_value('manual_status',level.get('manual_status')),
        })
    return pd.DataFrame(summary),pd.DataFrame(levels)


def target_history_table(versions, level_names=None):
    rows=[]
    for version in versions.to_dict('records'):
        for level in json.loads(version['levels_json']):
            lid=level['level_id']
            rows.append({'参数版本':f"V{version['version_no']}",
                '质控水平':(level_names or {}).get(lid,lid.replace('Level ','水平 ')),
                '靶均值':level['mean'],'SD':level['sd'],
                '靶值 CV%':calculate_cv_percent(level['mean'],level['sd']),
                '来源':_display(version['source']), '生效时间':version['effective_at'],
                '确认依据':version['evidence'],'确认人':version['confirmed_by']})
    return pd.DataFrame(rows)


def event_history_table(connection, events):
    def name(table,identifier,column):
        if identifier is None:return '未记录'
        row=connection.execute(f'SELECT {column} FROM {table} WHERE id=?',(identifier,)).fetchone()
        return str(row[0]) if row else '未记录'
    rows=[]
    for event in events.to_dict('records'):
        kind=event['event_type'];details=json.loads(event.get('details_json') or '{}')
        before=after='—'
        if kind in ('reagent','correction','qc'):
            table='md_reagent_lots' if kind!='qc' else 'md_qc_material_lots'
            before=name(table,event.get('previous_id'),'lot_no')
            after=name(table,event.get('next_id'),'lot_no')
            if details.get('new_config_id'):
                after=name('qc_lot_configs',details['new_config_id'],'config_name')
            if details.get('source_config_id'):
                before=name('qc_lot_configs',details['source_config_id'],'config_name')
            elif details.get('source_batch_id'):
                row=connection.execute("SELECT lot_config_id FROM qc_workbench_bindings WHERE qc_method='zscore' AND runtime_batch_id=?",(details['source_batch_id'],)).fetchone()
                if row:before=name('qc_lot_configs',row[0],'config_name')
        elif kind=='target':
            before='V'+name('qc_target_profiles',event.get('previous_id'),'version_no') if pd.notna(event.get('previous_id')) else '未设置'
            after='V'+name('qc_target_profiles',event.get('next_id'),'version_no')
        else:
            item=connection.execute('SELECT lot_config_id FROM qc_lot_config_items WHERE id=?',(event.get('next_id'),)).fetchone()
            after=name('qc_lot_configs',item[0],'config_name') if item else '未记录'
        confirmations=details.get('qc_verification_ids',{})
        evidence='；'.join(f"{name('md_qc_material_lots',lot,'lot_no')}：验证 {vid}" for lot,vid in confirmations.items())
        if not evidence and pd.notna(event.get('verification_id')):
            evidence=f"验证 {int(event['verification_id'])}"
        rows.append({'事件编号':event['id'],'操作':EVENT_LABELS.get(kind,'批次调整'),
            '调整前':before,'调整后':after,'生效时间':event['effective_at'],
            '调整依据':event['reason'],'确认人':event['operator'],'关联验证':evidence or '—',
            '更正原事件':str(int(event['corrects_event_id'])) if pd.notna(event.get('corrects_event_id')) else '—'})
    return pd.DataFrame(rows)
