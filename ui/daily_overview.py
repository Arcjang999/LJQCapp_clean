from __future__ import annotations
import logging
from datetime import date
import pandas as pd
import streamlit as st
from services.daily_overview_service import get_daily_overview,list_overview_bindings,METHOD_LABELS
from services.search_service import SEARCH_HELP
from ui.common import open_global_page


def _value(key):
    st.session_state.setdefault('daily_overview_values',{})[key]=st.session_state[key]


def _restore(key):
    if key not in st.session_state and key in st.session_state.get('daily_overview_values',{}):
        st.session_state[key]=st.session_state['daily_overview_values'][key]


def _pending_events(result):
    key='daily_pending_expanded';_restore(key)
    with st.expander('未完成异常处理',expanded=bool(result['pending']['cross_day']),key=key,on_change=_value,args=(key,)):
        for row in result['pending']['cross_day']+result['pending']['items']:
            from ui.out_of_control import navigate_event,STATUS_LABELS
            label=f"{row['test_time']} · {row['project_name']} · {STATUS_LABELS[row['status']]}"
            if st.button(label,key=f"daily_pending_{row['source_type']}_{row['source_id']}"):
                navigate_event(row['source_type'],row['source_id'],event_id=row.get('event_id'))


def render_daily_overview():
    from ui.common import render_module_header
    render_module_header('今日质控总览','按项目查看当日结果，跟进尚未完成的异常处理。',tone='daily',eyebrow='日常质控')
    if st.button('返回项目工作台',key='daily_home'):
        st.session_state['show_daily_overview_page']=False
        st.session_state['pending_top_level_method']='主页';st.rerun()
    try:bindings=list_overview_bindings(include_disabled=True)
    except Exception:
        logging.exception('Daily overview choices failed');st.error('资料读取失败，请重试。');return
    _restore('daily_day')
    columns=st.columns([1,1,1])
    day=columns[0].date_input('检测日期',value=date.today(),key='daily_day',on_change=_value,args=('daily_day',))
    def select(column,label,field,name,key):
        options={r[field]:r[name] for r in bindings}
        _restore(key)
        if st.session_state.get(key) not in [None,*options]:st.session_state[key]=None
        return column.selectbox(label,[None,*options],key=key,format_func=lambda k:'全部' if k is None else options[k],on_change=_value,args=(key,))
    instrument=select(columns[1],'仪器','lab_instrument_id','instrument_name','daily_instrument')
    template=select(columns[2],'项目','template_id','template_name','daily_template')
    columns=st.columns([1,1,2])
    product=select(columns[0],'质控品','qc_material_id','material_name','daily_material')
    _restore('daily_method')
    method=columns[1].selectbox('按质控方法筛选',[None,*METHOD_LABELS],key='daily_method',format_func=lambda k:METHOD_LABELS.get(k,'全部'),on_change=_value,args=('daily_method',))
    _restore('daily_search')
    search=columns[2].text_input('查找检验项目',key='daily_search',help=SEARCH_HELP,on_change=_value,args=('daily_search',))
    entry_action, report_action=st.columns(2)
    if entry_action.button('整组录入',type='primary',key='overview_entry',disabled=template is None,
            help='先在上方选择项目，再录入该项目的实际批次。'):
        from ui.daily_navigation import open_project_daily_entry
        open_project_daily_entry(template)
    if template is None:st.caption('需要录入时，先选择具体项目。')
    if report_action.button('批量月报',key='overview_batch_monthly'):
        open_global_page('show_batch_monthly_reports_page')
    try:
        result=get_daily_overview(day,lab_instrument_id=instrument,template_id=template,qc_material_id=product,qc_method=method,search=search)
    except Exception:
        logging.exception('Daily overview failed');st.error('总览读取失败，请重试。筛选条件已保留。');return
    _pending_events(result)
    if not bindings:
        st.info('尚无可用的项目批次，请先建立项目及批次资料。')
        if st.button('建立项目与批次'):open_global_page('show_project_management_page')
        return
    items=result['items']; metrics=st.columns(3)
    metrics[0].metric('当日检测次数',result['count']);metrics[1].metric('当日曾失控项目',sum(i['ever_reject'] for i in items))
    metrics[2].metric('未完成处理',len(result['pending']['items'])+len(result['pending']['cross_day']))
    st.caption('多水平的全部水平合计为一次检测。“今日无记录”表示所选日期没有保存结果，请结合实验室检测安排核对；本页不计算漏做率。')
    frame=pd.DataFrame([{'项目':i['template_name'],'检验项目':i['test_item_name'],'仪器':i['instrument_name'],
        '质控品':i['material_name'],'批次':i['config_name'],'质控方法':METHOD_LABELS[i['qc_method']],
        '阶段':i['phase'],'当日次数':i['count'],'最新结论':(i['latest'] or {}).get('conclusion','今日无记录'),
        '最新检测时间':(i['latest'] or {}).get('test_time',''),'今日曾失控':'是' if i['ever_reject'] else '否',
        '未完成处理':len(i['pending'])} for i in items])
    if frame.empty:st.info('没有符合筛选条件的项目。');return
    st.dataframe(frame,hide_index=True,width='stretch')
    choices={i['overview_key']:i for i in items};_restore('daily_selected')
    if st.session_state.get('daily_selected') not in [None,*choices]:st.session_state['daily_selected']=None
    selected=st.selectbox('查看项目详情',[None,*choices],key='daily_selected',format_func=lambda k:'请选择' if k is None else
        f"{choices[k]['test_item_name']} · {choices[k]['config_name']} · {METHOD_LABELS[choices[k]['qc_method']]}",on_change=_value,args=('daily_selected',))
    if selected is not None:
        item=choices[selected]
        buttons=st.columns(3)
        if buttons[0].button('查看质控图与单份月报',disabled=item['runtime_batch_id'] is None):
            from ui.daily_navigation import remember_daily_return
            from ui.project_navigation import open_configured_batch
            remember_daily_return();open_configured_batch(item)
        if buttons[1].button('查看批次设置'):
            from ui.project_navigation import open_project_setup
            open_project_setup(item['template_id'],config_id=item['lot_config_id'])
        if buttons[2].button('查看报告历史'):
            open_global_page('show_report_history_page')
        for record in item['records']:
            expand_key=f"daily_record_{record['source_type']}_{record['source_id']}";_restore(expand_key)
            with st.expander(f"{record['test_time']} · {record['conclusion']} · 检测 {record['source_id']}",expanded=True,
                    key=expand_key,on_change=_value,args=(expand_key,)):
                st.dataframe(pd.DataFrame([{'水平':v['level_name'] or f"水平{v['level_order']}",'检测值':v['value'],'实际质控品批号':v['lot_no']} for v in record['values']]),hide_index=True,width='stretch')
                if record['phase']=='formal' and record['classification'] in ('reject','warning'):
                    from ui.out_of_control import render_abnormal_entry
                    render_abnormal_entry(record['source_type'],record['source_id'],warning=record['classification']=='warning',key='daily')
        if item.get('stage_note'):st.info(item['stage_note'])
        quality=item['quality'];st.caption('质量评价期间：'+quality['period'])
        if quality.get('error'):st.error(quality['error'])
        elif quality.get('evaluation_reason'):st.info(quality['evaluation_reason'])
        elif quality['rows']:
            st.dataframe(pd.DataFrame([{'水平':r['level'],'有效点数':r['count'],'检测日数':r.get('days'),
                'CV%':r['cv'],'采用要求':r['requirement'],'评价':r['decision']} for r in quality['rows']]),hide_index=True,width='stretch')
            st.caption(quality.get('statistics_scope',''))
        else:st.info('暂不能判断 CV 是否满足要求。请查看批次已采用的质量要求，并核对所选期间是否有足够的有效检测结果。')
