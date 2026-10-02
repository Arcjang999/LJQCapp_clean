from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import logging
import pandas as pd
import streamlit as st

from services.daily_draft_service import new_draft,draft_rows,parse_rectangular_paste,apply_paste,build_request
from services.daily_overview_service import METHOD_LABELS,CLASS_LABELS
from services.value_type_service import get_input_value_type_label
from ui.common import open_global_page


def _draft():return st.session_state.get('daily_draft')


def _discard_controls(draft):
    if st.button('放弃本组草稿',key='entry_discard'):draft['confirm_discard']=True
    if not draft.get('confirm_discard'):return
    st.warning('是否放弃本组尚未保存的输入？已保存的检测记录不受影响。')
    left,right=st.columns(2)
    if left.button('取消放弃',key='entry_discard_cancel'):
        draft.pop('confirm_discard',None);st.rerun()
    if right.button('确认放弃草稿',key='entry_discard_confirm'):
        st.session_state.setdefault('daily_saved_drafts',{}).pop(str(draft['context']['selection']),None)
        token=draft['draft_id']
        for key in list(st.session_state):
            if token in key:st.session_state.pop(key,None)
        st.session_state.pop('daily_draft',None);st.session_state.pop('daily_pending_context',None)
        st.rerun()


def _entry_time(value):
    from services.lot_lifecycle_service import timestamp
    try:return timestamp(value)
    except (ValueError,TypeError,OverflowError) as exc:
        raise ValueError('检测时间无效，请按年-月-日 时:分:秒填写，并核对日期。') from exc


def _refresh_context(draft):
    from services.daily_context_service import get_daily_context
    current=get_daily_context(test_time=_entry_time(draft['test_time']),**draft['context']['selection'])
    draft['context']=current
    draft.pop('paste_preview',None);draft.pop('file_preview',None)
    for row in draft_rows(draft):draft['values'].setdefault(row['key'],'')
    draft['selected']=[k for k in draft['selected'] if k in {i['row_key'] for i in current['items']}]
    st.session_state['entry_selected_'+draft['draft_id']]=draft['selected']
    draft['frozen']=None;draft['edit_version']+=1;draft['errors']=[]


def _refresh_button(draft):
    if st.button('保留输入并重新核对资料',key='entry_refresh_context'):
        try:_refresh_context(draft)
        except Exception as exc:st.error(str(exc) if isinstance(exc,ValueError) else '资料核对未完成，请重试。')
        else:st.rerun()


def _field(field,key):
    draft=_draft()
    if draft:
        draft[field]=st.session_state[key];draft['frozen']=None
        draft.pop('paste_preview',None);draft.pop('file_preview',None)


def _map_field(field,item_id,key):
    draft=_draft()
    if draft:
        draft[field][item_id]=st.session_state[key];draft['frozen']=None
        draft.pop('paste_preview',None);draft.pop('file_preview',None)


def _editor_change(key,row_keys):
    draft=_draft()
    for index,changes in st.session_state.get(key,{}).get('edited_rows',{}).items():
        if '检测值' in changes:
            draft['values'][row_keys[int(index)]]=str(changes['检测值'] if changes['检测值'] is not None else '')
    draft['frozen']=None


def _errors(errors):
    for error in errors:
        if isinstance(error,dict):
            label=error.get('test_item_name') or error.get('item_name') or ''
            if not label and _draft():
                item=next((i for i in _draft()['context']['items'] if i['row_key']==str(error.get('row_key'))),None)
                if item:
                    label=item['test_item_name']
                    level=next((l for l in item['levels'] if l['qc_level_id']==error.get('qc_level_id')),None)
                    if level:label+=' · '+level.get('level_name',f"水平{level['level_order']}")
            message=error.get('message') or error.get('reason') or '请核对本项设置。'
            st.error((label+'：' if label else '')+str(message))
        else:st.error(str(error))


def _activate(context):
    old=_draft()
    drafts=st.session_state.setdefault('daily_saved_drafts',{})
    if old:
        selection_key=str(old['context']['selection'])
        drafts.pop(selection_key,None)
        drafts[selection_key]=deepcopy(old)
    existing=drafts.get(str(context['selection']))
    st.session_state['daily_draft']=deepcopy(existing) if existing else new_draft(context,operator=(old or {}).get('operator',''))
    st.session_state.pop('daily_pending_context',None)
    st.rerun()


def _render_selector():
    from services.daily_context_service import list_daily_choices,get_daily_context
    template_id=st.session_state.get('daily_entry_template_id')
    choices=list_daily_choices(template_id=template_id)
    if template_id is not None:
        from services.project_config_service import get_project_template
        template=dict(get_project_template(template_id))
        st.session_state['entry_instrument']=template['lab_instrument_id']
        st.session_state['entry_product']=template['qc_material_id']
        if 'entry_lot' not in st.session_state and _draft():
            st.session_state['entry_lot']=_draft()['context']['selection']['qc_material_lot_id']
    if not choices['instruments']:
        st.info('请先建立仪器、项目和已确认批次，再录入日常结果。')
        if st.button('打开项目与批次'):open_global_page('show_project_management_page')
        return
    if st.session_state.pop('daily_restore_selection',False) and _draft():
        sel=_draft()['context']['selection']
        for key,field in [('entry_instrument','lab_instrument_id'),('entry_product','qc_material_id'),('entry_lot','qc_material_lot_id')]:
            st.session_state[key]=sel[field]
    with st.expander('选择仪器与实际质控品批次',expanded=_draft() is None):
        c=st.columns(3)
        instruments={i['id']:i for i in choices['instruments']}
        if st.session_state.get('entry_instrument') not in instruments:st.session_state['entry_instrument']=None
        instrument=c[0].selectbox('仪器',[None,*instruments],key='entry_instrument',disabled=template_id is not None,format_func=lambda i:'请选择' if i is None else instruments[i]['name'])
        products={i['id']:i for i in choices['materials'] if instrument in i.get('instrument_ids',[])}
        if st.session_state.get('entry_product') not in products:st.session_state['entry_product']=None
        product=c[1].selectbox('质控品',[None,*products],key='entry_product',disabled=template_id is not None,format_func=lambda i:'请选择' if i is None else products[i]['name'])
        lots={i['id']:i for i in choices['lots'] if i['qc_material_id']==product and instrument in i.get('instrument_ids',[])}
        if st.session_state.get('entry_lot') not in lots:st.session_state['entry_lot']=None
        lot=c[2].selectbox('本次主批号',[None,*lots],key='entry_lot',format_func=lambda i:'请选择明确的实际批号' if i is None else lots[i]['lot_no'])
        if None in (instrument,product,lot):return
        when=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        context=get_daily_context(instrument,product,lot,when,template_id=template_id)
        if context.get('requires_combination'):
            combinations={i['lot_config_id']:i for i in context['combinations']}
            key=f'entry_combination_{template_id}_{instrument}_{product}_{lot}'
            if key not in st.session_state and _draft():
                selection=_draft()['context']['selection']
                if (selection.get('template_id')==template_id and selection['lab_instrument_id']==instrument
                        and selection['qc_material_id']==product and selection['qc_material_lot_id']==lot):
                    st.session_state[key]=selection['lot_config_id']
            if st.session_state.get(key) not in combinations:st.session_state[key]=None
            combination=st.selectbox('本次材料组合',[None,*combinations],key=key,format_func=lambda v:'请选择项目批次与完整水平组合' if v is None else combinations[v]['name'])
            if combination is None:return
            context=get_daily_context(instrument,product,lot,when,template_id=template_id,lot_config_id=combination)
        if context.get('issues'):_errors(context['issues'])
        if not context['items']:
            st.info('此批号还没有可录入的检验项目。请打开“项目与批次”，核对检验项目和各水平批号并确认批次。');return
        st.caption(f"本组共 {len(context['items'])} 个检验项目。开始录入前，请逐水平核对实际使用的批号。")
        if st.button('展开本组录入',type='primary',key='entry_start'):
            if _draft():st.session_state['daily_pending_context']=context
            else:_activate(context)
    pending=st.session_state.get('daily_pending_context')
    if pending:
        st.warning('切换后可返回继续填写当前草稿。关闭应用或刷新浏览器前，请先保存需要保留的结果。是否切换到所选批次？')
        a,b=st.columns(2)
        if a.button('保留草稿并切换'):_activate(pending)
        if b.button('取消切换'):
            st.session_state.pop('daily_pending_context',None);st.session_state['daily_restore_selection']=True;st.rerun()


def _receipt(draft):
    receipt=draft.get('receipt')
    if not receipt:return False
    st.success('本组已全部保存。重复确认不会新增记录。')
    source={str(i['lot_config_item_id']):i for i in draft['context']['items']}
    st.dataframe(pd.DataFrame([{'检验项目':source[str(r['row_key'])]['test_item_name'],
        '质控方法':METHOD_LABELS[source[str(r['row_key'])]['qc_method']], '检测时间':receipt['test_time'],
        '本次结论':r.get('conclusion') or CLASS_LABELS.get(r.get('classification'),'参数建立或即时法检验'),
        '检测值':'；'.join(str(l.get('value','')) for l in r.get('levels',[]))} for r in receipt['items']]),hide_index=True,width='stretch')
    rows={str(r['row_key']):r for r in receipt['items']}
    selected=st.selectbox('查看本次保存明细',[None,*rows],format_func=lambda k:'请选择检验项目' if k is None else source[k]['test_item_name'],key='receipt_selected_'+draft['draft_id'])
    if selected is not None:
        row=rows[selected];item=source[selected]
        st.dataframe(pd.DataFrame([{'水平':l['level_name'],'检测值':l['value'],'实际质控品批号':l['lot_no'],'单位':l['unit_symbol']} for l in row.get('levels',[])]),hide_index=True,width='stretch')
        columns=st.columns(2)
        if columns[0].button('查看本次记录与质控图',key='receipt_chart_'+str(row['row_key'])):
            from ui.project_navigation import open_configured_batch
            st.session_state['daily_result_focus']={'source_type':row['source_type'],'source_id':row['source_id']}
            open_configured_batch(item)
        if row.get('classification') in ('reject','warning') and row.get('method',row.get('qc_method'))!='instant':
            with columns[1]:
                from ui.out_of_control import render_abnormal_entry
                render_abnormal_entry(row['source_type'],row['source_id'],warning=row['classification']=='warning',key='receipt')
    from services.daily_result_io_service import export_daily_workbook
    st.download_button('导出本次已保存结果',export_daily_workbook(draft),'常规质控已保存结果.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    if st.button('再次检测，开始新一组',type='primary'):
        from services.daily_context_service import get_daily_context
        sel=draft['context']['selection'];when=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        context=get_daily_context(test_time=when,**sel)
        st.session_state['daily_draft']=new_draft(context,operator=draft['operator'],test_time=when);st.rerun()
    return True


def render_daily_entry():
    from ui.common import render_module_header
    template_id=st.session_state.get('daily_entry_template_id')
    template=None
    if template_id is not None:
        from services.project_config_service import get_project_template
        try:
            template=dict(get_project_template(template_id))
            if _draft() and _draft()['context']['selection'].get('template_id') != template_id:
                from ui.daily_navigation import prepare_project_daily_entry
                prepare_project_daily_entry(template_id)
        except ValueError:
            st.error('所选项目不可用，请返回项目工作台重新选择。')
            if st.button('返回项目工作台',key='entry_project_unavailable'):
                from ui.daily_navigation import return_to_project_workspace
                st.session_state.pop('workspace_project_id',None)
                return_to_project_workspace()
            return
    render_module_header('整组日常录入',
        ('当前项目：'+template['template_name']+'。' if template else '')+'核对实际批号，填写全部水平，再核对并保存。',
        tone='daily',eyebrow='日常质控')
    back,overview=st.columns(2)
    if back.button('返回项目工作台',key='entry_project_home'):
        from ui.daily_navigation import return_to_project_workspace
        return_to_project_workspace(template_id)
    if overview.button('返回今日总览',key='entry_overview'):open_global_page('show_daily_overview_page')
    st.caption('Enter / Tab 只移动输入格；整组核对并确认后才保存。')
    try:_render_selector()
    except Exception as exc:
        logging.exception('Daily context read failed');st.error(str(exc) if isinstance(exc,ValueError) else '资料读取失败，请重试。')
    draft=_draft()
    if not draft:return
    if not draft.get('receipt'):_discard_controls(draft)
    token=draft['draft_id'];items=draft['context']['items'];item_map={str(i['lot_config_item_id']):i for i in items}
    if not items:
        st.warning('此组目前没有可录入的检验项目，已保留之前的输入。请核对项目与批次资料后重新读取，或选择其他批次。')
        _errors(draft['context'].get('issues',[]));_refresh_button(draft)
        return
    st.markdown('**本组：'+str(items[0].get('instrument_name',''))+' · '+str(items[0].get('config_name',items[0].get('project_name','')))+'**')
    if _receipt(draft):return
    cols=st.columns(2)
    for col,label,field in [(cols[0],'检测时间（年-月-日 时:分:秒）','test_time'),(cols[1],'检测人','operator')]:
        key=f'daily_{field}_{token}'
        if key not in st.session_state:st.session_state[key]=draft[field]
        col.text_input(label,key=key,on_change=_field,args=(field,key))
    _refresh_button(draft)
    selection_key='entry_selected_'+token
    if selection_key not in st.session_state:st.session_state[selection_key]=draft['selected']
    st.multiselect('本次录入项目',list(item_map),key=selection_key,format_func=lambda k:item_map[k]['test_item_name']+' · '+METHOD_LABELS[item_map[k]['qc_method']],on_change=_field,args=('selected',selection_key))
    rows=draft_rows(draft,selected_only=True)
    from ui.daily_grid import render_daily_grid
    render_daily_grid(draft)
    if draft.get('grid_paste_text') is not None:
        st.session_state['entry_paste_'+token]=draft.pop('grid_paste_text')
    with st.expander('从表格粘贴检测值',expanded=bool(draft.get('paste_preview'))):
        st.caption('按上表项目和水平顺序粘贴一列检测值，或检测值、备注两列；空白会保留为未填写。先预览，再带入。')
        text=st.text_area('粘贴区域',key='entry_paste_'+token)
        if st.button('预览粘贴内容'):
            draft['paste_preview']=parse_rectangular_paste(text,rows);draft['paste_text']=text
            draft['paste_preview']['context_revision']=draft['context']['context_revision']
            draft['paste_preview']['all_row_keys']=[r['key'] for r in rows]
        preview=draft.get('paste_preview')
        if preview:
            _errors(preview['errors'])
            st.dataframe(pd.DataFrame([{'行号':r['line'],'检测值':r['value'],'备注':r['note'] or ''} for r in preview['rows']]),hide_index=True,width='stretch')
            if st.button('确认带入本组',disabled=not preview['valid'] or text!=draft.get('paste_text')):
                apply_paste(draft,preview);draft.pop('paste_preview',None)
                for key in list(st.session_state):
                    if key.startswith('entry_note_'+token):st.session_state.pop(key,None)
                st.rerun()
            if st.button('取消粘贴'):
                draft.pop('paste_preview',None);st.rerun()
    with st.expander('核对实际试剂、备注和材料资料'):
        for key in draft['selected']:
            item=item_map[key];st.markdown('**'+item['test_item_name']+'**')
            options={r['id']:r for r in item.get('reagent_options',[])}
            rk=f'entry_reagent_{token}_{key}'
            if rk not in st.session_state:st.session_state[rk]=draft['reagents'].get(key)
            if st.session_state[rk] not in options:st.session_state[rk]=None
            st.selectbox('实际试剂批号',[None,*options],key=rk,format_func=lambda v:'请选择' if v is None else options[v]['lot_no'],on_change=_map_field,args=('reagents',key,rk))
            nk=f'entry_note_{token}_{key}'
            if nk not in st.session_state:st.session_state[nk]=draft['notes'].get(key,'')
            st.text_input('本次备注',key=nk,on_change=_map_field,args=('notes',key,nk))
            _errors(item.get('issues',[]))
            st.dataframe(pd.DataFrame([{'水平':l.get('level_name',''),'实际批号':l.get('lot_no',''),'有效期':l.get('expiry_date',''),'浓度编号':l.get('level_code','')} for l in item['levels']]),hide_index=True,width='stretch')
            if st.button('打开本项批次设置',key='entry_setup_'+key):
                from ui.project_navigation import open_project_setup
                open_project_setup(item['template_id'],config_id=item['lot_config_id'],lot_config_item_id=item['lot_config_item_id'])
            targets={issue.get('settings_target','project') for issue in item.get('issues',[])}
            targets.update(issue.get('settings_target','project') for issue in draft.get('errors',[])
                if isinstance(issue,dict) and str(issue.get('row_key'))==key)
            labels={'quality':'核对本项质量目标','parameters':'核对本项均值和标准差',
                    'reagent':'核对本项试剂批号与验证','usage':'核对本项质控品使用期间','materials':'核对本项实际材料与水平'}
            for target in sorted(targets-{'project'}):
                if target in labels and st.button(labels[target],key=f'entry_setup_{key}_{target}'):
                    from ui.project_navigation import open_project_setup
                    open_project_setup(item['template_id'],config_id=item['lot_config_id'],
                        lot_config_item_id=item['lot_config_item_id'],settings_target=target,
                        system_id=item.get('system_id'),reagent_lot_id=draft['reagents'].get(key))
    from ui.daily_result_io import render_daily_result_files
    render_daily_result_files(draft)
    sk='entry_same_time_'+token
    if sk not in st.session_state:st.session_state[sk]=draft['allow_same_time']
    st.checkbox('已核对：相同检测时间的记录属于另一次检测',key=sk,on_change=_field,args=('allow_same_time',sk))
    _errors(draft.get('errors',[]))
    left,right=st.columns(2)
    if left.button('核对并保存',key='entry_validate',type='primary',disabled=not rows):
        from services.daily_entry_service import validate_submission
        try:
            if draft['test_time']!=draft['context'].get('test_time'):
                from services.daily_context_service import get_daily_context
                draft['context']=get_daily_context(test_time=_entry_time(draft['test_time']),**draft['context']['selection'])
            validation=validate_submission(build_request(draft));draft['errors']=validation['errors'];draft['frozen']=validation.get('frozen_request') if validation['valid'] else None
        except Exception as exc:
            logging.exception('Daily validation failed');draft['errors']=[str(exc) if isinstance(exc,ValueError) else '核对未完成，请重试。']
        st.rerun()
    if right.button('清空本组输入',key='entry_clear'):draft['confirm_clear']=True
    if draft.get('confirm_clear'):
        st.warning('是否清空本组检测值和备注？取消会保留全部输入。')
        if st.button('确认清空'):
            draft['before_clear']={'values':deepcopy(draft['values']),'notes':deepcopy(draft['notes'])}
            draft['values']={k:'' for k in draft['values']};draft['notes']={k:'' for k in draft['notes']};draft['edit_version']+=1
            draft.pop('confirm_clear',None);draft['frozen']=None
            for key in list(st.session_state):
                if key.startswith('entry_note_'+token):st.session_state.pop(key,None)
            st.rerun()
        if st.button('取消清空'):draft.pop('confirm_clear',None);st.rerun()
    if draft.get('before_clear') and st.button('恢复清空前输入'):
        draft.update(draft.pop('before_clear'));draft['edit_version']+=1
        for key in list(st.session_state):
            if key.startswith('entry_note_'+token):st.session_state.pop(key,None)
        st.rerun()
    if draft.get('frozen'):
        st.info(f"本组 {len(draft['selected'])} 项已核对，请确认检测时间 {draft['test_time']}、检测人 {draft['operator']} 和全部实际批号。")
        if st.button('确认保存整组',type='primary',key='entry_commit'):
            from services.daily_entry_service import submit_daily
            try:draft['receipt']=submit_daily(draft['frozen']);draft['errors']=[]
            except Exception as exc:
                logging.exception('Daily submit failed');draft['errors']=getattr(exc,'errors',None) or [str(exc) if isinstance(exc,ValueError) else '保存未完成，全部输入已保留，请重试。']
            st.rerun()
        if st.button('返回修改本组'):draft['frozen']=None;st.rerun()
