"""Shared condition and provenance form for project and batch dialogs."""
from datetime import date
import hashlib

import streamlit as st

from services.quality_applicability_service import (
    CONTEXT_LABELS, CONTEXT_OPTIONS, assess, default_context, infer_technique, context_errors, source_status,
)


def render_draft_standard_preview(draft):
    """Show actual method-dependent candidates before saving the item draft."""
    from database import get_connection
    from services.quality_catalog_service import KIND_LABELS
    # Existing-item drafts come from pandas Series and may hold numpy integers;
    # SQLite binds those as blobs unless converted to native integer IDs.
    test_item_id = int(draft['test_item_id'])
    method_id = int(draft['method_id']) if draft.get('method_id') is not None else None
    unit_id = int(draft['unit_id']) if draft.get('unit_id') is not None else None
    with get_connection() as connection:
        row = connection.execute('''SELECT t.chinese_name AS test_item_name, t.abbreviation, t.standard_code,
            t.specimen_type, m.method_name, m.method_code, m.principle AS method_principle,
            u.symbol AS unit_symbol FROM md_test_items t
            LEFT JOIN md_methods m ON m.id=? LEFT JOIN md_units u ON u.id=? WHERE t.id=?''',
            (method_id, unit_id, test_item_id)).fetchone()
        if not row:
            return
        item = {**draft, **dict(row)}
        item['test_aliases'] = [r[0] for r in connection.execute(
            "SELECT alias_text FROM md_aliases WHERE entity_type='test_item' AND entity_id=? AND is_disabled=0",
            (test_item_id,))]
    context = default_context(item)
    candidates = assess(item, context)
    with st.expander('质量标准与待核对条件', expanded=True):
        if not item.get('method_name'):
            st.info('请先选择方法学，随后按检测技术、结果性质和用途核对适用标准。')
        for row in candidates:
            if source_status(row['source']) != 'current':
                continue
            state = {'applicable':'适用，须采用', 'pending':'待补充条件', 'not_applicable':'当前条件不适用'}[row['status']]
            st.caption(row['source']['standard']+' · '+row['spec']['name']+'｜'+KIND_LABELS[row['kind']]+'｜'+state)
        if not candidates:
            st.info('目录尚未收录此检测条件的质量要求，保存后请登记官方查找结果或补充来源。')
        missing = context_errors(item, context)
        if missing:
            st.caption('保存后在质量目标中完成核对：'+' '.join(missing))
        st.caption('保存检验项目后会进入质量目标设置；完成适用依据确认后才能启用项目。')


def render_conditions(item, review, prefix):
    saved = review.get('context') or default_context(item)
    context = {}
    st.markdown('**标准适用条件**')
    st.caption('方法学：' + (item.get('method_name') or '未设置'))
    for key, options in CONTEXT_OPTIONS.items():
        values = list(options)
        prior = saved.get(key, '')
        if key == 'technique' and infer_technique(item):
            prior = infer_technique(item)
            widget_key = prefix + '_context_' + key
            if widget_key in st.session_state and st.session_state[widget_key] != prior:
                st.session_state[widget_key] = prior
        context[key] = st.selectbox(CONTEXT_LABELS[key], values,
            index=values.index(prior) if prior in values else 0,
            format_func=options.get, key=prefix+'_context_'+key,
            disabled=key == 'technique' and bool(infer_technique(item)))
    context['specimen'] = st.text_input('标本或基质', value=saved.get('specimen', ''),
        key=prefix+'_context_specimen', max_chars=200)
    rows = assess(item, context)
    labels = {'applicable': '适用，须采用', 'pending': '待核查', 'not_applicable': '不适用'}
    for row in rows:
        if source_status(row['source']) != 'current':
            continue
        name = row['spec']['name']
        st.caption(f"{row['source']['standard']} · {name}｜{labels[row['status']]}：{row['reason']}")
    process = [r for r in rows if r['kind'] != 'numeric' and r['status'] == 'applicable']
    process_ids = [r['id'] for r in process]
    if process:
        names = {r['id']: r['source']['standard']+' · '+r['spec']['name'] for r in process}
        adopted = st.multiselect('同时采用的过程及定性要求', process_ids,
            default=process_ids, format_func=names.get, key=prefix+'_process_sources')
        for row in process:
            st.caption('；'.join(row['spec']['requirements']))
            st.link_button('查看 '+row['source']['standard']+' 原文', row['source']['source_url'])
        st.info('请按条款人工核对对照设置和相关要求。阴性对照不参与浓度 CV 计算。')
    else:
        adopted = []
    search = None
    registered = []
    if not any(r['kind'] == 'numeric' and r['status'] == 'applicable' for r in rows):
        if any(r['kind'] == 'numeric' and r['status'] == 'pending' for r in rows):
            st.warning('已有候选数值标准，适用条件尚待核查。请先补齐条件或核对单位；填写其他依据不能代替这一步。')
        else:
            st.info('当前目录没有匹配的适用数值条目。请查找官方标准并记录复核结论；资料不全时可保存待确认。')
        prior = review.get('search_record') or {}
        search = {}
        with st.expander('标准查找与复核', expanded=True):
            search['query'] = st.text_input('查找词（检测项、方法及用途）',value=prior.get('query',''),key=prefix+'_search_query',max_chars=500)
            search['official_url'] = st.text_input('官方查询结果或标准链接',value=prior.get('official_url',''),key=prefix+'_search_url',max_chars=2000)
            search['checked_on'] = str(st.date_input('核查日期',value=date.fromisoformat(prior['checked_on']) if prior.get('checked_on') else date.today(),max_value=date.today(),key=prefix+'_search_date'))
            choices = {'': '待核查', 'no_applicable': '已核查，无适用分析质量标准',
                       'no_numeric': '有关条款已关联，无对应尺度的数值限值', 'registered': '找到未收录标准，已补充登记'}
            keys=list(choices)
            search['conclusion'] = st.selectbox('复核结论',keys,index=keys.index(prior.get('conclusion','')) if prior.get('conclusion','') in keys else 0,format_func=choices.get,key=prefix+'_search_conclusion')
            search['rationale'] = st.text_area('查找结果及适用依据',value=prior.get('rationale',''),key=prefix+'_search_rationale',max_chars=2000)
        if search['conclusion'] == 'registered':
            registered = render_registered_sources(review.get('registered_standards', []), prefix)
    return dict(context=context, search_record=search, adopted_standard_ids=adopted,
                registered_standards=registered), rows


def render_registered_sources(prior, prefix):
    result=[]
    count=st.number_input('补充标准数量',min_value=1,max_value=8,value=max(1,len(prior)),step=1,key=prefix+'_registered_count')
    for i in range(count):
        old=prior[i] if i<len(prior) else {}
        row={}
        with st.expander(f'补充标准 {i+1} 的全文核查记录',expanded=True):
            labels={'standard':'标准编号','name':'标准名称','version':'版本',
                    'effective_date':'实施日期（YYYY-MM-DD）','withdrawn_on':'废止日期（如有）',
                    'source_url':'官方全文链接',
                    'source_clause':'适用条款','source_page':'PDF 页码','verified_by':'全文核查人',
                    'checked_on':'全文核查日期（YYYY-MM-DD）'}
            for key,label in labels.items():
                row[key]=st.text_input(label,value=old.get(key,''),key=f'{prefix}_registered_{i}_{key}',max_chars=2000)
            uploaded = st.file_uploader('标准全文文件（PDF）', type=['pdf'], key=f'{prefix}_registered_{i}_file')
            row['sha256'] = old.get('sha256', '')
            if uploaded is not None:
                contents = uploaded.getvalue()
                if not contents.startswith(b'%PDF-'):
                    st.error('请上传可打开的标准全文 PDF 文件。')
                    row['sha256'] = ''
                else:
                    row['sha256'] = hashlib.sha256(contents).hexdigest()
            elif row['sha256']:
                st.caption('已保留上次核对的全文记录。更换版本时，请重新上传全文。')
            kinds={'process':'过程 / 对照要求','qualitative':'定性要求','numeric_record':'数值指标（人工核对）'}
            keys=list(kinds)
            row['kind']=st.selectbox('条款类型',keys,index=keys.index(old.get('kind','process')),format_func=kinds.get,key=f'{prefix}_registered_{i}_kind')
            row['requirement_text']=st.text_area('完整适用要求及附加条件',value=old.get('requirement_text',''),key=f'{prefix}_registered_{i}_requirement',max_chars=2000)
            row['applicability_evidence']=st.text_area('方法、标本、尺度、单位及用途的核对依据',value=old.get('applicability_evidence',''),key=f'{prefix}_registered_{i}_evidence',max_chars=2000)
        result.append(row)
    return result
