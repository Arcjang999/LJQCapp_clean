from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from database import get_connection
from services.lot_lifecycle_service import (
    source_context, require_writable, result_lot_options, context_dataframe, workbench_systems,
    create_reagent_lot, list_reagent_lots, record_lot_verification, switch_reagent_lots, usage_revision,
    change_qc_lot, set_qc_usage_state, create_target_profile, list_lot_events, correct_reagent_event,
)


def render_result_lot_choice(method,batch_id,test_time,key):
    options=result_lot_options(method,batch_id,test_time)
    revision_key=f'{key}_usage_revision_{batch_id}'
    choice_key=f'{key}_actual_lot_{batch_id}'
    previous=st.session_state.get(revision_key)
    if previous is not None and previous!=options['revision']:
        st.session_state[choice_key]=None
        st.warning('试剂默认批号已变化，请重新确认本次实际使用批号。')
    st.session_state[revision_key]=options['revision']
    choices={row['id']:row for row in options['options']}
    if not choices and not options['revision']:
        st.caption('尚未登记可用试剂批号；本次批号将标记为“未记录”。可在项目/批次的“批号使用与追溯”登记。')
        return {'expected_revision':options['revision']}
    lot_id=st.selectbox('本次实际试剂批号',list(choices),index=None,key=choice_key,
        format_func=lambda i:f"{choices[i]['lot_no']}｜效期 {choices[i]['expiry_date']}",placeholder='请按本次检测实际使用批号选择')
    suggestion=choices.get(options['suggested_lot_id'])
    if suggestion:
        st.caption(f"按检测时间建议：{suggestion['lot_no']}。选择不会改变既往检测记录。")
    return {'reagent_lot_id':lot_id,'expected_revision':options['revision']}


def is_batch_writable(method,batch_id):
    try:
        with get_connection() as c:
            require_writable(c,method,batch_id)
        return True
    except ValueError as exc:
        st.info(str(exc))
        return False


def render_result_provenance(method,batch_id):
    with st.expander('实际批号与历史判定追溯'):
        df=context_dataframe(method,batch_id)
        if df.empty:
            st.caption('暂无逐条快照。旧记录迁移后，缺失的历史批号会明确显示为未记录。')
            return
        names={'result_id':'记录编号','test_time':'检测时间','context_id':'快照编号','actual_reagent_lot':'试剂批号',
               'reagent_lot_no':'试剂批号','reagent_expiry_date':'试剂效期','qc_lot_no':'质控品批号','instrument':'仪器',
               'reagent':'试剂产品','unit_symbol':'单位','method_name':'检测方法','target_profile_id':'靶值版本',
               'provenance':'资料来源','source_context_id':'转入来源快照'}
        display=df.rename(columns=names).copy()
        display['资料来源']=display['资料来源'].map({'recorded':'检测时保存','migration_snapshot':'迁移：已有配置快照',
            'migration_available':'迁移时已有资料','instant_transfer':'即时法转入'})
        st.dataframe(display,hide_index=True,width='stretch')
        st.download_button('导出逐条批号与版本',display.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'{method}-{batch_id}-批号追溯.csv',mime='text/csv',key=f'{method}_context_export_{batch_id}')
        selected=st.selectbox('查看记录的判定版本',df.context_id.tolist(),format_func=lambda i:f"快照 {i}",key=f'{method}_context_audit_{batch_id}')
        with get_connection() as c:
            evaluations=pd.read_sql_query('SELECT id,reason,created_at,evaluation_json FROM qc_result_evaluations WHERE context_id=? ORDER BY id',c,params=(selected,))
        if not evaluations.empty:
            st.caption('检测时保存的记录可查看首次判定及后续回顾。迁移记录仅保留迁移后的回顾，不能恢复未记录的原始判定。')
            which=st.selectbox('判定记录',evaluations.id.tolist(),key=f'{method}_evaluation_{batch_id}')
            st.json(json.loads(evaluations.loc[evaluations.id==which,'evaluation_json'].iloc[0]))


def _systems():
    systems=workbench_systems()
    for item in systems:
        snap=json.loads(item['snapshot_json']);item['snapshot']=snap
        item['label']=f"{snap['test_item_name']}｜{snap['instrument_name']}｜{snap['reagent_name']}｜系统 {item['id']}"
    return {s['id']:s for s in systems}


def _save(action):
    try:
        result=action()
    except (ValueError,TypeError) as exc:
        st.error(str(exc));return None
    st.success('已保存，既往结果保持原来的批号与版本。')
    return result


def render_lot_management():
    from services.master_data_service import list_reagents, list_qc_lots
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    sync_lj_workbench_bindings();sync_zscore_workbench_bindings();sync_instant_workbench_bindings()
    systems=_systems()
    st.caption('试剂换批、质控品换批和靶值修订分别记录。先完成本检测项的适用性验证，再确认启用；验证依据按实验室 SOP 填写。')
    reagent_tab,qc_tab,target_tab,history_tab=st.tabs(['试剂批号与换批','质控品换批与并行','靶值版本','历史记录'])
    with reagent_tab:
        products=list_reagents()
        if not products.empty:
            with st.expander('登记试剂生产批号'):
                labels={int(r['id']):r['generic_name'] for r in products.to_dict('records')}
                with st.form('lot_create_reagent'):
                    product=st.selectbox('试剂产品',list(labels),format_func=labels.get)
                    lot_no=st.text_input('试剂批号')
                    expiry=st.date_input('试剂批号效期')
                    source=st.text_input('资料来源／厂家说明')
                    if st.form_submit_button('登记试剂批号'):
                        _save(lambda:create_reagent_lot(reagent_id=product,lot_no=lot_no,expiry_date=expiry,source_text=source))
        lots=list_reagent_lots()
        st.dataframe(lots.rename(columns={'lot_no':'试剂批号','reagent_name':'试剂产品','expiry_date':'效期'}),hide_index=True,width='stretch')
        if systems and not lots.empty:
            selected=st.selectbox('验证检测系统',list(systems),format_func=lambda i:systems[i]['label'],key='lot_verify_system')
            applicable=lots[lots.reagent_id==systems[selected]['snapshot']['identity'][7]]
            if not applicable.empty:
                lot_labels={int(r['id']):f"{r['lot_no']}｜效期 {r['expiry_date']}" for r in applicable.to_dict('records')}
                with st.form('lot_verify_reagent'):
                    lot=st.selectbox('待验证试剂批号',list(lot_labels),format_func=lot_labels.get)
                    conclusion=st.selectbox('验证结论',['pass','fail'],format_func=lambda x:'通过' if x=='pass' else '未通过')
                    evidence=st.text_area('验证方案、对照批号、接受标准及结果／资料位置')
                    person=st.text_input('验证确认人')
                    when=st.datetime_input('验证完成时间')
                    if st.form_submit_button('保存试剂批号验证'):
                        _save(lambda:record_lot_verification(template_item_id=systems[selected]['template_item_id'],system_id=selected,
                            reagent_lot_id=lot,conclusion=conclusion,evidence=evidence,confirmed_by=person,confirmed_at=when))
            st.divider()
            selected_lot=st.selectbox('切换到试剂批号',lots.id.tolist(),format_func=lambda i:f"{lots.loc[lots.id==i,'manufacturer_name'].iloc[0]}｜{lots.loc[lots.id==i,'reagent_name'].iloc[0]}｜{lots.loc[lots.id==i,'lot_no'].iloc[0]}",key='lot_switch_selected_reagent')
            product_id=int(lots.loc[lots.id==selected_lot,'reagent_id'].iloc[0])
            candidates={i:s for i,s in systems.items() if s['snapshot']['identity'][7]==product_id}
            selected_systems=st.multiselect('明确选择本次受影响的检测项',list(candidates),format_func=lambda i:candidates[i]['label'],key='lot_switch_systems')
            preview=[]
            with get_connection() as c:
                for sid in selected_systems:
                    v=c.execute("SELECT * FROM qc_lot_verifications WHERE system_id=? AND reagent_lot_id=? AND conclusion='pass' ORDER BY id DESC LIMIT 1",(sid,selected_lot)).fetchone()
                    preview.append({'system_id':sid,'template_item_id':systems[sid]['template_item_id'],'reagent_lot_id':selected_lot,
                        'verification_id':v['id'] if v else None,'expected_revision':usage_revision(c,sid),
                        '检测项':systems[sid]['label'],'验证依据':v['evidence'] if v else '尚无通过的验证'})
            preview_key=(int(selected_lot),tuple(selected_systems))
            if st.session_state.get('lot_switch_preview_scope')!=preview_key:
                st.session_state['lot_switch_preview_scope']=preview_key
                st.session_state['lot_switch_preview_rows']=preview
            if st.button('刷新换批预览',key='lot_switch_preview_refresh'):
                st.session_state['lot_switch_preview_rows']=preview
            preview=st.session_state.get('lot_switch_preview_rows',preview)
            st.dataframe(pd.DataFrame(preview)[['检测项','验证依据']] if preview else pd.DataFrame(),hide_index=True)
            with st.form('lot_switch_reagent'):
                when=st.datetime_input('实际启用时间')
                person=st.text_input('换批操作者')
                reason=st.text_input('换批原因与原靶值仍适用的依据')
                confirmed=st.checkbox('已核对所选检测项、各项验证和启用时间；原控制参数继续适用')
                if st.form_submit_button('确认切换所选检测项'):
                    if not confirmed:
                        st.error('请先核对并确认换批预览。')
                    else:
                        _save(lambda:switch_reagent_lots(selections=preview,effective_at=when,operator=person,reason=reason))
    with qc_tab:
        with get_connection() as c:
            configs=pd.read_sql_query('SELECT * FROM qc_lot_configs WHERE is_disabled=0 ORDER BY id',c)
        if not configs.empty:
            cid=st.selectbox('原质控品批号配置',configs.id.tolist(),format_func=lambda i:configs.loc[configs.id==i,'config_name'].iloc[0])
            current=configs[configs.id==cid].iloc[0]
            target_lots=list_qc_lots(qc_material_id=int(current['qc_material_id']))
            target_lots=target_lots[target_lots.id!=int(current['qc_material_lot_id'])]
            with get_connection() as c:
                rows=c.execute('''SELECT i.*,t.chinese_name FROM qc_lot_config_items i JOIN md_test_items t ON t.id=i.test_item_id
                    WHERE i.lot_config_id=? AND i.is_disabled=0''',(cid,)).fetchall()
            items={r['source_template_item_id']:r['chinese_name'] for r in rows}
            if not target_lots.empty:
                with st.form('lot_switch_qc'):
                    target=st.selectbox('新质控品批号',target_lots.id.tolist(),format_func=lambda i:target_lots.loc[target_lots.id==i,'lot_no'].iloc[0])
                    chosen=st.multiselect('本次更换质控批号的检测项',list(items),format_func=items.get)
                    when=st.datetime_input('开始平行使用时间')
                    operator=st.text_input('质控换批操作者')
                    reason=st.text_input('质控品换批原因')
                    st.caption('沿用项目，只新建质控批配置；新批检测值从零开始。旧批仍可使用，完成验证后按检测项结束旧批。')
                    if st.form_submit_button('创建新批并行配置'):
                        result=_save(lambda:change_qc_lot(source_config_id=int(cid),target_qc_lot_id=int(target),template_item_ids=chosen,operator=operator,reason=reason,effective_at=when))
                        if result: st.info(f'新批配置已创建：{result}。复制的人工／厂家参数需在批号配置中重新确认。')
            else:
                st.info('请先在基础资料中登记该质控品的新批号及水平。')
        _render_level_combination(systems)
        _render_qc_state(systems)
    with target_tab:
        _render_target_versions()
    with history_tab:
        _render_history(systems)


def _binding_options():
    with get_connection() as c:
        rows=c.execute('SELECT * FROM qc_workbench_bindings ORDER BY id').fetchall()
        result={}
        for r in rows:
            s,_,_=source_context(c,r['qc_method'],r['runtime_batch_id'])
            result[r['id']]={**dict(r),'snapshot':s,'label':f"{s.get('test_item_name','')}｜{s.get('instrument_name','')}｜{s.get('lot_no','')}｜{r['qc_method']}"}
        return result


def _render_qc_state(systems):
    bindings=_binding_options()
    if not bindings:return
    bid=st.selectbox('查看／调整质控批使用状态',list(bindings),format_func=lambda i:bindings[i]['label'])
    b=bindings[bid];s=b['snapshot']
    with get_connection() as c:
        lot=c.execute('SELECT qc_material_lot_id FROM qc_lot_configs WHERE id=?',(b['lot_config_id'],)).fetchone()[0]
        state=c.execute('SELECT state FROM qc_config_item_lifecycle WHERE lot_config_item_id=?',(b['lot_config_item_id'],)).fetchone()
    from services.lot_lifecycle_service import effective_qc_state
    with get_connection() as c:effective=effective_qc_state(c,b['lot_config_item_id'])
    st.write('当前状态：'+{'parallel':'平行使用','active':'已启用','ended':'结束使用','pending':'尚未到开始使用时间'}[effective])
    if state and state[0]!=effective:
        st.caption('另有待生效的状态计划；按所登记的生效时间切换。')
    with get_connection() as c:
        available_qc={r['id']:r['lot_no'] for r in c.execute('SELECT id,lot_no FROM md_qc_material_lots WHERE qc_material_id=? AND is_disabled=0',(s['identity'][1],))}
    verify_lot=st.selectbox('待验证的质控批号',list(available_qc),format_func=available_qc.get,key='qc_verify_actual_lot')
    with st.form('lot_verify_qc'):
        conclusion=st.selectbox('质控新批验证结论',['pass','fail'],format_func=lambda x:'通过' if x=='pass' else '未通过')
        evidence=st.text_area('平行观察、控制参数及适用性验证依据')
        person=st.text_input('质控验证确认人');when=st.datetime_input('质控验证完成时间')
        if st.form_submit_button('保存质控批号验证'):
            _save(lambda:record_lot_verification(template_item_id=s['project_template_item_id'],system_id=s['system_id'],qc_lot_id=verify_lot,
                conclusion=conclusion,evidence=evidence,confirmed_by=person,confirmed_at=when))
    with get_connection() as c:
        verifications=c.execute("SELECT id,evidence FROM qc_lot_verifications WHERE system_id=? AND qc_lot_id=? AND conclusion='pass'",(s['system_id'],lot)).fetchall()
    with st.form('lot_qc_state'):
        state=st.selectbox('目标使用状态',['parallel','active','ended'],format_func=lambda x:{'parallel':'平行使用','active':'已启用','ended':'结束使用（只读）'}[x])
        verification=st.selectbox('关联质控验证',[None]+[r['id'] for r in verifications],format_func=lambda x:'未选择' if x is None else f'验证 {x}')
        when=st.datetime_input('状态生效时间');operator=st.text_input('状态确认人');reason=st.text_input('状态调整依据')
        if st.form_submit_button('确认质控批使用状态'):
            _save(lambda:set_qc_usage_state(lot_config_item_id=b['lot_config_item_id'],state=state,effective_at=when,operator=operator,reason=reason,verification_id=verification))


def _render_target_versions():
    options={i:b for i,b in _binding_options().items() if b['qc_method']!='instant'}
    if not options:
        st.caption('启用 LJ 或 Z-score 配置后可确认靶值。即时法继续按 3/20 点规则累计。');return
    selected=st.selectbox('控制参数所属批次',list(options),format_func=lambda i:options[i]['label'])
    b=options[selected];method=b['qc_method'];batch_id=b['runtime_batch_id'];levels=b['snapshot'].get('levels') or [{}]
    with get_connection() as c:
        existing=pd.read_sql_query('SELECT version_no,effective_at,source,levels_json,evidence,confirmed_by FROM qc_target_profiles WHERE qc_method=? AND batch_id=? ORDER BY version_no',c,params=(method,batch_id))
    st.dataframe(existing,hide_index=True,width='stretch')
    with st.form('lot_target_version'):
        source=st.selectbox('控制参数来源',['manual','manufacturer','revision'],format_func=lambda x:{'manual':'实验室确认','manufacturer':'经实验室确认的厂家赋值','revision':'已有靶值修订'}[x])
        parameters=[]
        for i,original in enumerate(levels,1):
            level=dict(original)
            with get_connection() as c:
                retained=c.execute('SELECT p.levels_json,p.version_no FROM qc_level_combination_members m JOIN qc_target_profiles p ON p.id=m.source_profile_id WHERE m.lot_config_item_id=? AND m.qc_level_id=?',(b['lot_config_item_id'],level.get('qc_level_id'))).fetchone()
            if retained:
                reference=next((r for r in json.loads(retained['levels_json']) if r['level_id']==f'Level {i}'),{})
                level['target_mean']=reference.get('mean');level['target_sd']=reference.get('sd')
                st.caption(f'未更换水平的原参数 V{retained["version_no"]} 已预填，仅作为本次确认依据；保存前仍需核对。')
            st.write(level.get('level_name') or f'水平 {i}')
            a,bc=st.columns(2)
            mean=a.number_input('靶均值',key=f'target_mean_{selected}_{i}',value=float(level.get('target_mean') or 0),format='%.6f')
            sd=bc.number_input('SD',key=f'target_sd_{selected}_{i}',value=float(level.get('target_sd') or 0),min_value=0.0,format='%.6f')
            parameters.append({'level_id':f'Level {i}','mean':mean,'sd':sd})
        when=st.datetime_input('新参数生效时间');evidence=st.text_area('参数依据、评估结果和修订原因');person=st.text_input('参数确认人')
        confirm=st.checkbox('已确认全部水平参数适用；新参数使用独立连续规则窗口，旧结果保留原版本')
        if st.form_submit_button('保存新的控制参数版本'):
            if confirm:_save(lambda:create_target_profile(method=method,batch_id=batch_id,levels=parameters,source=source,evidence=evidence,confirmed_by=person,effective_at=when))
            else:st.error('请先确认全部水平的控制参数。')


def _render_history(systems):
    if systems:
        sid=st.selectbox('批号事件所属检测系统',list(systems),format_func=lambda i:systems[i]['label'])
        st.dataframe(list_lot_events(sid),hide_index=True,width='stretch')
        st.caption('生效事件不删除；更正会追加新的记录，原结果继续保留当时保存的实际批号。')
        events=list_lot_events(sid)
        editable=events[events.event_type.isin(['reagent','correction'])] if not events.empty else events
        if not editable.empty:
            with get_connection() as c:
                verifications={r['id']:dict(r) for r in c.execute("SELECT v.*,l.lot_no FROM qc_lot_verifications v JOIN md_reagent_lots l ON l.id=v.reagent_lot_id WHERE v.system_id=? AND v.conclusion='pass'",(sid,))}
                revision=usage_revision(c,sid)
            if st.session_state.get('event_correction_system')!=sid:
                st.session_state['event_correction_system']=sid;st.session_state['event_correction_revision']=revision
            if st.button('刷新事件更正预览'):
                st.session_state['event_correction_revision']=revision
            with st.form('lot_correct_event'):
                event=st.selectbox('更正原事件',editable.id.tolist())
                v=st.selectbox('更正后的实际默认批号与验证',list(verifications),format_func=lambda i:f"{verifications[i]['lot_no']}｜验证 {i}")
                when=st.datetime_input('更正事件实际生效时间');person=st.text_input('事件更正人');reason=st.text_input('更正原因')
                if st.form_submit_button('追加更正事件'):
                    _save(lambda:correct_reagent_event(event_id=event,reagent_lot_id=verifications[v]['reagent_lot_id'],verification_id=v,effective_at=when,operator=person,reason=reason,expected_revision=st.session_state['event_correction_revision']))
    options=_binding_options()
    if options:
        selected=st.selectbox('查看历史批次（含停用／结束使用）',list(options),format_func=lambda i:options[i]['label'])
        b=options[selected];render_result_provenance(b['qc_method'],b['runtime_batch_id'])


def _render_level_combination(systems):
    from services.lot_lifecycle_service import create_level_combination
    bindings={i:b for i,b in _binding_options().items() if b['qc_method']=='zscore' and b['binding_status']=='active'}
    if not bindings:return
    with st.expander('多水平：只更换一个或部分水平的质控批号'):
        selected=st.selectbox('来源水平组合',list(bindings),format_func=lambda i:bindings[i]['label'],key='combo_source')
        b=bindings[selected];s=b['snapshot'];levels=s['levels'];chosen=[];verification_ids={}
        with get_connection() as c:
            candidates=[dict(r) for r in c.execute('''SELECT l.*,q.lot_no FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
                WHERE q.qc_material_id=? AND q.is_disabled=0 AND l.is_disabled=0 ORDER BY q.id,l.level_order''',(s['identity'][1],))]
        for i,level in enumerate(levels,1):
            options={r['id']:r for r in candidates if r['level_order']==i}
            lid=st.selectbox(f'水平 {i} 实际质控批号',list(options),index=list(options).index(level['qc_level_id']) if level['qc_level_id'] in options else None,
                format_func=lambda v:f"{options[v]['level_name']}｜{options[v]['lot_no']}",key=f'combo_{selected}_{i}')
            chosen.append(lid)
            if lid and lid!=level['qc_level_id']:
                with get_connection() as c:
                    verifications=[dict(r) for r in c.execute("SELECT * FROM qc_lot_verifications WHERE system_id=? AND qc_lot_id=? AND conclusion='pass' ORDER BY id DESC",(s['system_id'],options[lid]['qc_material_lot_id']))]
                verification_ids[lid]=st.selectbox(f'水平 {i} 换批验证',[None]+[r['id'] for r in verifications],format_func=lambda v:'请选择通过的验证' if v is None else f'验证 {v}',key=f'combo_verify_{selected}_{i}')
        st.caption('更换水平的新批验证可在下方质控验证区登记。新组合独立收集数据；未变更水平的既有参数保留来源，可在靶值版本中复核使用。所有水平参数确认前不输出正式联合结论。')
        with st.form('lot_level_combination'):
            when=st.datetime_input('新水平组合生效时间');person=st.text_input('水平换批确认人');reason=st.text_input('水平换批依据')
            if st.form_submit_button('建立新的水平组合'):
                _save(lambda:create_level_combination(source_batch_id=b['runtime_batch_id'],level_ids=chosen,verification_ids=verification_ids,operator=person,reason=reason,effective_at=when))
