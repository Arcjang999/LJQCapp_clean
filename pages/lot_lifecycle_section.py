from __future__ import annotations

import json
from copy import deepcopy
import pandas as pd
import streamlit as st

from database import get_connection
from ui.traceability import evaluation_tables
from services.lot_lifecycle_service import (
    require_writable, result_lot_options, context_dataframe, workbench_systems, source_context,
)


_ENTRY_FIELDS = {
    'lj': ('entry_batch_id', 'entry_operator', 'entry_value', 'entry_reagent_changed', 'entry_test_time'),
    'zscore': ('zscore_entry_batch_id', 'zscore_entry_operator', 'zscore_level1_value',
               'zscore_level2_value', 'zscore_level3_value', 'zscore_entry_test_time'),
    'instant': ('instant_entry_batch_id', 'instant_entry_operator', 'instant_entry_value', 'instant_entry_test_time'),
}
_ENTRY_RETURN_KEY = 'result_lot_preparation_return'


def restore_result_lot_entry_draft(method, batch_id):
    state = st.session_state.get(_ENTRY_RETURN_KEY)
    if state and state['method'] == method and state['batch_id'] == batch_id:
        for field, value in state['draft'].items():
            st.session_state[field] = deepcopy(value)
        st.session_state.pop(_ENTRY_RETURN_KEY, None)


def render_result_lot_preparation(method, batch_id, key, *, in_form=False):
    with get_connection() as connection:
        binding = connection.execute('''SELECT b.*,c.template_id FROM qc_workbench_bindings b
            JOIN qc_lot_configs c ON c.id=b.lot_config_id
            WHERE b.qc_method=? AND b.runtime_batch_id=? AND b.binding_status='active' ''', (method, batch_id)).fetchone()
    if binding is None:
        return
    button = st.form_submit_button if in_form else st.button
    if button('登记或核对试剂批号', key=f'{key}_prepare_reagent_{batch_id}', width='stretch'):
        from ui.project_navigation import open_project_setup
        previous_return = st.session_state.get(_ENTRY_RETURN_KEY)
        try:
            with get_connection() as connection:
                source, _, _ = source_context(connection, method, batch_id, read_only=True)
            fields = (*_ENTRY_FIELDS[method], f'{key}_usage_revision_{batch_id}', f'{key}_actual_lot_{batch_id}')
            st.session_state[_ENTRY_RETURN_KEY] = dict(method=method, batch_id=batch_id, binding=dict(binding),
                draft={field: deepcopy(st.session_state[field]) for field in fields if field in st.session_state})
            open_project_setup(binding['template_id'], config_id=binding['lot_config_id'],
                lot_config_item_id=binding['lot_config_item_id'], settings_target='reagent',
                system_id=source.get('system_id'), reagent_lot_id=st.session_state.get(f'{key}_actual_lot_{batch_id}'))
        except ValueError as error:
            if previous_return is None:
                st.session_state.pop(_ENTRY_RETURN_KEY, None)
            else:
                st.session_state[_ENTRY_RETURN_KEY] = previous_return
            st.error(str(error))


def _render_result_lot_entry_return():
    state = st.session_state.get(_ENTRY_RETURN_KEY)
    if state and st.button('返回本次结果录入', key='result_lot_preparation_back'):
        from ui.project_navigation import open_configured_batch
        open_configured_batch(state['binding'])


def render_result_lot_choice(method,batch_id,test_time,key):
    options=result_lot_options(method,batch_id,test_time)
    revision_key=f'{key}_usage_revision_{batch_id}'
    choice_key=f'{key}_actual_lot_{batch_id}'
    previous=st.session_state.get(revision_key)
    if previous is not None and previous!=options['revision']:
        st.session_state[choice_key]=None
        st.warning('试剂资料或使用安排已更新，请重新选择本次批号。')
    st.session_state[revision_key]=options['revision']
    choices={row['id']:row for row in options['options']}
    if st.session_state.get(choice_key) not in choices:
        st.session_state[choice_key] = None
    if not choices and not options['revision']:
        st.caption('当前没有可选的试剂批号，保存后本次批号为“未记录”。'
                   '如需记录实际批号，请先登记试剂批号和验证，再返回选择。'
                   '后续登记不会补改已保存的检测记录。')
        return {'expected_revision':options['revision']}
    if not choices:
        st.info('所填检测时间没有可选的试剂批号。请先核对批号验证的完成时间、结论和效期，再返回选择。')
    lot_id=st.selectbox('本次试剂批号',list(choices),index=None,key=choice_key,
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
    with st.expander('检测批号与判读记录'):
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
        st.download_button('导出检测批号清单',display.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'{method}-{batch_id}-批号追溯.csv',mime='text/csv',key=f'{method}_context_export_{batch_id}')
        record_labels={row['context_id']:f"记录 {row['result_id']}｜{row['test_time']}" for row in df.to_dict('records')}
        selected=st.selectbox('查看检测记录',df.context_id.tolist(),format_func=record_labels.get,key=f'{method}_context_audit_{batch_id}')
        with get_connection() as c:
            evaluations=pd.read_sql_query('SELECT id,reason,created_at,evaluation_json FROM qc_result_evaluations WHERE context_id=? ORDER BY id',c,params=(selected,))
            saved_source=json.loads(c.execute('SELECT config_snapshot_json FROM qc_result_contexts WHERE id=?',(selected,)).fetchone()[0])
        if not evaluations.empty:
            st.caption('选择判读记录，查看检测时及之后的复核结果；历史记录按已保存的资料显示。')
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
    _render_result_lot_entry_return()
    st.caption('选择下方页签，登记批号验证、换批或均值和标准差；验证依据按实验室记录填写。')
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
        st.caption('核对有效检测次数和独立分析批数，按实验室规程确认均值和标准差。')
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
