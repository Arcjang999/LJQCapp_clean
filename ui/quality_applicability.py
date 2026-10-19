"""Shared condition and provenance form for project and batch dialogs."""
from datetime import date
import hashlib

import streamlit as st

from services.quality_applicability_service import (
    CONTEXT_LABELS, CONTEXT_OPTIONS, assess, default_context, infer_technique, context_errors,
    missing_context_fields, source_status,
)
from ui.required_fields import required_field


def _applicability_label(row):
    if row['kind'] == 'process':
        return {'applicable': '可参考并按实验室要求修改', 'pending': '请补充检测条件',
                'not_applicable': '本次检测不采用'}[row['status']]
    return {'applicable': '本次检测须采用', 'pending': '请检查方法和单位',
            'not_applicable': '本次检测不采用'}[row['status']]


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
    with st.expander('查看质量标准，补充检测条件', expanded=True):
        if not item.get('method_name'):
            st.info('请先选择方法学。保存后，在质量目标中填写检测技术、结果性质和用途。')
        for row in candidates:
            if source_status(row['source']) != 'current':
                continue
            state = _applicability_label(row)
            st.caption(row['source']['standard']+' · '+row['spec']['name']+'｜'+KIND_LABELS[row['kind']]+'｜'+state)
        if not candidates:
            st.info('本软件已收录的标准中，没有此项目的数值要求。保存后可设置SD控制；原始定量结果可另填CV上限。')
        missing = context_errors(item, context)
        if missing:
            st.caption('保存后还需填写：'+' '.join(missing))
        st.caption('点击“保存”后进入质量目标设置。设置完成后，再确认项目设置。')


def render_conditions(item, review, prefix):
    saved = review.get('context') or default_context(item)
    inferred = infer_technique(item)
    # Widget values arrive before a rerun, while saved may still be an older
    # review (or the previous quick-setup draft). Guide the current inputs.
    current = {key: st.session_state.get(prefix+'_context_'+key, saved.get(key, ''))
               for key in CONTEXT_LABELS}
    if inferred:
        current['technique'] = inferred
    missing = missing_context_fields(current)
    context = {}
    # Keep these five inputs visible so a missing condition cannot be hidden
    # behind an expander. The keys and original widget labels stay unchanged.
    with st.container(border=True, key=prefix+'_conditions'):
        st.markdown('**检测条件**')
        st.caption('以下5项均为必填。请检查已填选项，并填写本次标本。方法学：' + (item.get('method_name') or '未设置'))
        if missing:
            st.info(f'还有 {len(missing)} 项必填：' + '、'.join(CONTEXT_LABELS[key] for key in missing)
                    + '。请填写下方标出的输入框。')
        columns = st.columns(2)
        for index, (key, options) in enumerate(CONTEXT_OPTIONS.items()):
            values = list(options)
            prior = saved.get(key, '')
            if key == 'technique' and inferred:
                prior = inferred
                widget_key = prefix + '_context_' + key
                if widget_key in st.session_state and st.session_state[widget_key] != prior:
                    st.session_state[widget_key] = prior
            widget_key = prefix+'_context_'+key
            with columns[index % 2], required_field(widget_key, missing=key in missing,
                    message='请选择'+CONTEXT_LABELS[key]+'。'):
                context[key] = st.selectbox(CONTEXT_LABELS[key], values,
                    index=values.index(prior) if prior in values else 0,
                    format_func=options.get, key=widget_key,
                    disabled=key == 'technique' and bool(inferred))
        with required_field(prefix+'_context_specimen', missing='specimen' in missing,
                message='请填写标本或基质。'):
            context['specimen'] = st.text_input('标本或基质', value=saved.get('specimen', ''),
                key=prefix+'_context_specimen', max_chars=200)
    rows = assess(item, context)
    with st.expander('查看标准及填写对照要求', expanded=False):
        for row in rows:
            if source_status(row['source']) != 'current':
                continue
            name = row['spec']['name']
            st.caption(f"{row['source']['standard']} · {name}｜{_applicability_label(row)}：{row['reason']}")
        local_process = render_process_requirements(rows, review, prefix)
    process = [r for r in rows if r['kind'] not in ('numeric', 'process') and r['status'] == 'applicable']
    process_ids = [r['id'] for r in process]
    if process:
        names = {r['id']: r['source']['standard']+' · '+r['spec']['name'] for r in process}
        adopted = st.multiselect('同时采用的定性与信号精密度要求', process_ids,
            default=process_ids, format_func=names.get, key=prefix+'_mandatory_sources')
        for row in process:
            st.caption('；'.join(row['spec']['requirements']))
            st.link_button('查看 '+row['source']['standard']+' 原文', row['source']['source_url'])
        st.info('请按以上条款检查对照设置和结果。浓度CV统计使用质控品检测值，阴性对照按条款单独检查。')
    else:
        adopted = []
    search = None
    registered = []
    if not any(r['kind'] == 'numeric' and r['status'] == 'applicable' for r in rows):
        if any(r['kind'] == 'numeric' and r['status'] == 'pending' for r in rows):
            st.warning('找到了此项目的数值标准，请先检查检测方法、结果尺度和单位。具体缺项见上方提示。')
        prior = review.get('search_record') or {}
        candidate_search = {}
        with st.expander('高级：查找或补充标准', expanded=False):
            active = st.checkbox('记录官方查找或补充标准', value=bool(prior and prior.get('scope') != 'local_catalog'),
                                 key=prefix+'_advanced_search')
            st.caption('需要保存官方查询结果或新增标准时，勾选上方选项，再填写下列资料。')
            candidate_search['query'] = st.text_input('查找词（检测项、方法及用途）',value=prior.get('query',''),key=prefix+'_search_query',max_chars=500)
            candidate_search['official_url'] = st.text_input('官方查询结果或标准链接',value=prior.get('official_url',''),key=prefix+'_search_url',max_chars=2000)
            candidate_search['checked_on'] = str(st.date_input('核查日期',value=date.fromisoformat(prior['checked_on']) if prior.get('checked_on') else date.today(),max_value=date.today(),key=prefix+'_search_date'))
            choices = {'': '请选择查询结果', 'no_applicable': '已查询，没有本次检测的分析质量标准',
                       'no_numeric': '已找到参考标准，但未规定本次结果尺度的数值限值', 'registered': '已找到新标准，并在下方登记'}
            keys=list(choices)
            candidate_search['conclusion'] = st.selectbox('查询结果',keys,index=keys.index(prior.get('conclusion','')) if prior.get('conclusion','') in keys else 0,format_func=choices.get,key=prefix+'_search_conclusion')
            candidate_search['rationale'] = st.text_area('查到了什么，哪些条款用于本次检测',value=prior.get('rationale',''),key=prefix+'_search_rationale',max_chars=2000)
            if active:
                search = candidate_search
                if search['conclusion'] == 'registered':
                    registered = render_registered_sources(review.get('registered_standards', []), prefix)
    return dict(context=context, search_record=search, adopted_standard_ids=adopted,
                registered_standards=registered, process_requirements=local_process), rows


def render_process_requirements(rows, review, prefix):
    from services.quality_review_service import default_process_requirements
    process = [r for r in rows if r['kind'] == 'process' and r['status'] == 'applicable']
    names = {r['id']: r['source']['standard'] + ' · ' + r['spec']['name'] for r in process}
    prior = review.get('process_requirements')
    if prior is None:
        prior = default_process_requirements(rows)
    source_key = prefix + '_process_sources'
    text_key = prefix + '_process_text'
    seed_key = prefix + '_process_seed'
    seen_key = prefix + '_process_seen'
    if seen_key not in st.session_state:
        st.session_state[seen_key] = list(names) if 'process_requirements' in review else []
    newly_available = [identifier for identifier in names if identifier not in st.session_state[seen_key]]
    if source_key not in st.session_state:
        st.session_state[source_key] = [identifier for identifier in prior['source_ids'] if identifier in names]
    else:
        st.session_state[source_key] = [identifier for identifier in st.session_state[source_key] if identifier in names]
    st.session_state[source_key] = list(dict.fromkeys(st.session_state[source_key] + newly_available))
    st.session_state[seen_key] = list(dict.fromkeys(st.session_state[seen_key] + list(names)))
    st.markdown('**对照与质控要求**')
    st.caption('下方已填入标准的参考内容。请改成本实验室实际执行的对照和质控要求，也可留空；标准原文可在下方查看。')
    selected = st.multiselect('参考标准（可调整）', list(names), key=source_key, format_func=names.get)
    seed = default_process_requirements(rows, selected)['requirement_text']
    if text_key not in st.session_state:
        st.session_state[text_key] = prior['requirement_text']
    elif seed_key in st.session_state and st.session_state[text_key] == st.session_state[seed_key]:
        st.session_state[text_key] = seed
    st.session_state[seed_key] = seed
    if st.button('重新带入所选参考要求', key=prefix+'_process_reset', disabled=not selected,
                 help='用当前所选标准的参考内容替换下方尚未保存的要求。'):
        st.session_state[text_key] = seed
    requirement = st.text_area('本实验室对照与质控要求（可修改、可留空）',
        key=text_key, max_chars=6000, height=180)
    if selected:
        with st.expander('查看参考标准原文'):
            for row in process:
                if row['id'] in selected:
                    st.caption(names[row['id']] + '｜' + row['clause'])
                    for text in row['spec']['requirements']:
                        st.write(text)
                    st.link_button('查看 '+row['source']['standard']+' 原文', row['source']['source_url'])
    return dict(source_ids=selected, requirement_text=requirement)


def render_registered_sources(prior, prefix):
    result=[]
    count=st.number_input('补充标准数量',min_value=1,max_value=8,value=max(1,len(prior)),step=1,key=prefix+'_registered_count')
    for i in range(count):
        old=prior[i] if i<len(prior) else {}
        row={}
        with st.expander(f'补充标准 {i+1} 的名称、条款和全文',expanded=True):
            labels={'standard':'标准编号','name':'标准名称','version':'版本',
                    'effective_date':'实施日期（YYYY-MM-DD）','withdrawn_on':'废止日期（如有）',
                    'source_url':'官方全文链接',
                    'source_clause':'本次使用的条款','source_page':'PDF 页码','verified_by':'阅读并确认此标准的人员',
                    'checked_on':'确认日期（YYYY-MM-DD）'}
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
                st.caption('已保留上次上传的标准全文。更换标准版本时，请上传新全文。')
            kinds={'process':'对照与质控要求','qualitative':'定性要求','numeric_record':'数值指标（人工检查）'}
            keys=list(kinds)
            row['kind']=st.selectbox('条款类型',keys,index=keys.index(old.get('kind','process')),format_func=kinds.get,key=f'{prefix}_registered_{i}_kind')
            row['requirement_text']=st.text_area('本次检测需执行的条款及附加条件',value=old.get('requirement_text',''),key=f'{prefix}_registered_{i}_requirement',max_chars=2000)
            row['applicability_evidence']=st.text_area('说明条款要求的方法、标本、结果类型、单位和用途与本次检测是否一致',value=old.get('applicability_evidence',''),key=f'{prefix}_registered_{i}_evidence',max_chars=2000)
        result.append(row)
    return result
