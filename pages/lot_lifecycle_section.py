from __future__ import annotations

import json
import pandas as pd
import streamlit as st

from database import get_connection
from ui.traceability import evaluation_tables
from services.lot_lifecycle_service import (
    require_writable, result_lot_options, context_dataframe, workbench_systems,
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
            st.caption('暂无可查看的检测记录。缺失的历史批号会显示为“未记录”。')
            return
        names={'result_id':'记录编号','test_time':'检测时间','context_id':'追溯编号','actual_reagent_lot':'试剂批号',
               'reagent_lot_no':'试剂批号','reagent_expiry_date':'试剂效期','qc_lot_no':'质控品批号','instrument':'仪器',
               'reagent':'试剂产品','unit_symbol':'单位','method_name':'检测方法','target_profile_id':'参数版本',
               'provenance':'资料来源','source_context_id':'转入来源编号'}
        display=df.rename(columns=names).copy()
        with get_connection() as c:
            profile_versions={r['id']:f"V{r['version_no']}" for r in c.execute('SELECT id,version_no FROM qc_target_profiles WHERE qc_method=? AND batch_id=?',(method,batch_id))}
        display['参数版本']=display['参数版本'].map(profile_versions).fillna('未关联版本')
        display['资料来源']=display['资料来源'].map({'recorded':'检测时保存','migration_snapshot':'历史配置资料',
            'migration_available':'历史保留资料','instant_transfer':'即时法转入'}).fillna('历史保留资料')
        st.dataframe(display.drop(columns=['追溯编号','转入来源编号']),hide_index=True,width='stretch')
        st.download_button('导出逐条批号与版本',display.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'{method}-{batch_id}-批号追溯.csv',mime='text/csv',key=f'{method}_context_export_{batch_id}')
        record_labels={row['context_id']:f"记录 {row['result_id']}｜{row['test_time']}" for row in df.to_dict('records')}
        selected=st.selectbox('查看检测记录',df.context_id.tolist(),format_func=record_labels.get,key=f'{method}_context_audit_{batch_id}')
        with get_connection() as c:
            evaluations=pd.read_sql_query('SELECT id,reason,created_at,evaluation_json FROM qc_result_evaluations WHERE context_id=? ORDER BY id',c,params=(selected,))
            saved_source=json.loads(c.execute('SELECT config_snapshot_json FROM qc_result_contexts WHERE id=?',(selected,)).fetchone()[0])
        if not evaluations.empty:
            st.caption('可查看首次判定及后续复核记录。历史资料未保存当时判定时，仅展示已有的复核结果。')
            reasons={'recorded':'首次判定','current_review':'数据复核','maintenance_review':'维护后复核','migration_review':'历史数据复核','instant_transfer_review':'转入 LJ 后复核'}
            labels={r['id']:f"{reasons.get(r['reason'],'复核记录')}｜{r['created_at']}｜编号 {r['id']}" for r in evaluations.to_dict('records')}
            which=st.selectbox('判定记录',evaluations.id.tolist(),format_func=labels.get,key=f'{method}_evaluation_{batch_id}')
            summary,levels=evaluation_tables(json.loads(evaluations.loc[evaluations.id==which,'evaluation_json'].iloc[0]),
                saved_source.get('input_value_type','raw'),{f'Level {i}':l['level_name'] for i,l in enumerate(saved_source.get('levels',[]),1)})
            st.dataframe(summary,hide_index=True,width='stretch')
            if not levels.empty:st.dataframe(levels,hide_index=True,width='stretch')
        else:
            st.caption('该记录未保存判定详情；原始检测值和批号资料仍可查询。')


def render_lot_management(*, render_dialogs=True):
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    sync_lj_workbench_bindings();sync_zscore_workbench_bindings();sync_instant_workbench_bindings()
    workbench_systems()
    st.caption('批次设置确认、均值和标准差确认、质控品使用状态分别管理。试剂与质控品换批验证依据按实验室 SOP 填写。')
    labels = ['试剂批号与换批','新旧批号比对','均值和标准差管理','历史记录']
    if 'lot_management_tabs' not in st.session_state and st.session_state.get('lot_management_active_tab') in labels:
        st.session_state['lot_management_tabs'] = st.session_state['lot_management_active_tab']
    reagent_tab,qc_tab,target_tab,history_tab=st.tabs(labels, key='lot_management_tabs', on_change='rerun')
    st.session_state['lot_management_active_tab'] = st.session_state.get('lot_management_tabs', labels[0])
    with reagent_tab:
        from ui.reagent_lifecycle_workspace import render_reagent_lifecycle_workspace
        render_reagent_lifecycle_workspace()
    with qc_tab:
        from ui.qc_lifecycle_workspace import render_qc_lifecycle_workspace
        render_qc_lifecycle_workspace()
    with target_tab:
        st.caption('均值和标准差按设定的有效检测点数计算。请另行核对数据是否来自规定数量的独立分析批，并按实验室规程确认暂定或常用参数。')
        _render_target_versions()
    with history_tab:
        _render_history()
    if render_dialogs:
        render_pending_lifecycle_dialogs()


def _render_target_versions():
    from ui.target_profile_workspace import render_target_profile_workspace
    render_target_profile_workspace()


def render_pending_lifecycle_dialogs():
    from ui.qc_replacement_workspace import MODAL_KEY as REPLACEMENT_MODAL, render_pending_qc_replacement_dialog
    from ui.qc_lifecycle_workspace import MODAL_KEY as QC_MODAL, render_pending_qc_lifecycle_dialog
    from ui.target_profile_workspace import render_pending_target_profile_dialog
    from ui.reagent_lifecycle_workspace import MODAL_KEY as REAGENT_MODAL, render_pending_reagent_lifecycle_dialog
    from ui.reagent_history_workspace import MODAL_KEY as HISTORY_MODAL, render_pending_reagent_history_dialog
    if st.session_state.get(REPLACEMENT_MODAL):
        render_pending_qc_replacement_dialog()
    elif st.session_state.get(QC_MODAL):
        render_pending_qc_lifecycle_dialog()
    elif st.session_state.get(REAGENT_MODAL):
        render_pending_reagent_lifecycle_dialog()
    elif st.session_state.get(HISTORY_MODAL):
        render_pending_reagent_history_dialog()
    else:
        render_pending_target_profile_dialog()


def _render_history():
    from ui.reagent_history_workspace import render_reagent_history_workspace
    render_reagent_history_workspace()
