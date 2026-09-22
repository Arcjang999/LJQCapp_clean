from __future__ import annotations
import pandas as pd
import streamlit as st
from database import atomic_write, get_connection
from services.quality_target_service import (adopt_requirement, clear_requirement, decode, item_context,
    suggested_requirements, source_label, validate_spec_for_item, batch_quality_summary)


def catalog_table(records):
    return pd.DataFrame([{'检验项目':r['name'],'来源类型':'内置标准' if r['origin']=='builtin' else '实验室自定要求',
        '来源/版本':r['standard']+' / '+r['version'],'实施日期':r['effective_date'],
        '测量单位':r['measurement_unit'] or '标准未规定测量单位（以百分数表示）','适用范围':r['scope'],
        '允许不精密度':r['imprecision_text'],'允许偏倚':r['bias_text'] or '未规定',
        '允许总误差/可比性偏差':r['tea_text'] or '未规定','条款':r['source_clause'],
        'PDF页码':r.get('source_page'),'说明':r['notes']} for r in records])


def render_spec(spec):
    st.caption(source_label(spec)+f"｜实施：{spec['effective_date']}")
    st.write('允许不精密度：'+spec['imprecision_text'])
    st.write('允许偏倚：'+(spec['bias_text'] or '原条款未规定'))
    st.write('允许总误差/可比性偏差：'+(spec['tea_text'] or '原条款未规定'))
    st.caption('适用范围：'+spec['scope']+'；测量单位：'+(spec['measurement_unit'] or '标准未规定测量单位，请核对本项目单位'))
    if spec.get('notes'):st.info(spec['notes'])
    if spec.get('source_url','').startswith('https://'):
        st.link_button('查看标准原文',spec['source_url'])
    if spec.get('source_page'):st.caption(f"来源：PDF 第 {spec['source_page']} 页（包括封面），详见所列条款。")


def _current_source(source):
    from datetime import date
    from services.quality_applicability_service import source_status
    if source.get('verification'):
        return source_status(source) == 'current'
    today = date.today().isoformat()
    return source.get('effective_date', '') <= today and not (
        source.get('withdrawn_on') and source['withdrawn_on'] <= today)


def render_review_summary(review, *, historical=False):
    if not review:
        return
    if review.get('status') == 'pending':
        st.info('质量目标资料尚待确认，请补齐本次检验项目和质控品的适用条件及来源核查。')
    recorded = review.get('recorded', {})
    if recorded:
        st.write('实验室自定要求：' + recorded.get('requirement_text', ''))
        st.caption('来源：' + recorded.get('source_name', '') + ' / ' + recorded.get('source_version', ''))
        st.caption('请按这些要求人工核对；均值和标准差按本批次设置使用。')
    st.caption('确认人：' + review.get('confirmed_by', '') + '｜依据：' + review.get('evidence', ''))
    for row in review.get('candidates', []):
        if not historical and not _current_source(row.get('standard', {})):
            continue
        state = '已采用' if row.get('disposition') == 'adopted' else '不适用'
        st.caption(f"{row['source']}｜{state}：{row.get('reason', '')}")
        if row.get('disposition') == 'adopted' and row.get('kind') not in (None, 'numeric'):
            st.caption('；'.join(row.get('requirements', [])) + '｜请人工核对')
    for source in review.get('registered_standards', []):
        if not historical and not _current_source(source):
            continue
        st.caption(f"补充依据：{source['standard']} / {source['version']}；{source['source_clause']}；{source['requirement_text']}（请人工核对）")
    search=review.get('search_record')
    if search:
        st.caption('标准复核：'+search.get('checked_on','')+'｜'+search.get('rationale',''))


def render_adoption(scope,item_id,*,embedded=False,expected_revision=None,on_saved=None):
    from services.quality_review_service import (standard_candidates, save_recorded_requirement,
        is_frozen_lot)
    item=item_context(scope,item_id);goal=decode(item['quality_goal_json']);prefix=f'quality_{scope}_{item_id}'
    review=decode(item.get('quality_review_json'))
    st.caption(f"{item['test_item_name']}｜单位 {item['unit_symbol']}｜{item['level_count']} 个水平")
    frozen=False
    if scope=='lot':
        with get_connection() as connection:
            frozen=is_frozen_lot(connection,item)
    if embedded:st.markdown('**本批次质量目标**' if scope=='lot' else '**项目质量目标**')
    from services.quality_catalog_service import browse_quality_catalog
    current_ids = {row['id'] for row in browse_quality_catalog()}
    if goal and (frozen or goal['spec']['id'] in current_ids):
        render_spec(goal['spec'])
        if scope=='lot' and goal.get('pending'):
            st.info('请核对下方各水平的适用条件，再确认本批次质量目标。')
        else:
            st.caption(f"确认人：{goal['confirmed_by']}｜设置时间：{goal['adopted_at']}")
        if goal.get('evidence'):st.caption('适用依据：'+goal['evidence'])
        if goal.get('levels'):
            st.dataframe(pd.DataFrame([{'水平':v['level_order'],'浓度':v['concentration'],'类别':v['category'],
                '要求':f"{v['rule']['operator']} {v['rule']['value']:g}{v['rule']['unit']}"} for v in goal['levels']]),hide_index=True)
    elif item['cv_limit'] is not None:
        st.caption(f"原设定允许不精密度（CV）：≤{item['cv_limit']:g}%｜依据：{item['quality_target_source_text'] or '未填写'}")
    render_review_summary(review, historical=frozen)
    if frozen:
        st.info('本批次已确认，质量目标不能修改。如需调整，请在新批次设置。' if goal or review or item['cv_limit'] is not None else '本批次未设置质量目标，可继续录入和查看结果。新增批次时请设置并确认质量目标。')
        return
    from ui.quality_applicability import render_conditions
    condition_args, assessments=render_conditions(item,review,prefix)
    candidates=standard_candidates(item)
    def check_current():
        if scope=='lot' and expected_revision is not None:
            from services.batch_edit_service import get_batch_item_context
            current=get_batch_item_context(item_id)
            if not current['editable']:raise ValueError(current['read_only_reason'])
            if current['revision']!=expected_revision:raise ValueError('本批次设置已修改，请重新打开后再保存。')
    if st.button('保存待确认资料',key=prefix+'_save_pending'):
        try:
            from services.quality_review_service import save_pending_review
            with atomic_write():
                check_current()
                save_pending_review(scope,item_id,context=condition_args['context'],
                    search_record=condition_args['search_record'],registered_standards=condition_args['registered_standards'],
                    draft_recorded={key: st.session_state.get(prefix+'_'+suffix, '')
                        for key,suffix in [('source_name','source_name'),('source_version','source_version'),
                                           ('requirement_text','recorded_text')]})
        except ValueError as error:st.error(str(error))
        else:
            if on_saved is not None:on_saved()
            st.success('已保存待确认资料；补齐核查后才能确认项目或批次。');st.rerun()
    applicable=[r['id'] for r in assessments if r['kind']=='numeric' and r['status']=='applicable']
    if st.session_state.get(prefix+'_applicable_ids') != applicable:
        st.session_state[prefix+'_applicable_ids'] = applicable
        if len(applicable) == 1:
            st.session_state[prefix+'_requirement'] = applicable[0]
    if condition_args['context'].get('result_scale')!='concentration' or item['input_value_type']!='raw':
        st.info('请按当前结果类型填写质量要求，并人工核对对照及相关条款。浓度 CV 限值不适用于 Ct、log 或信号比值。')
        mode='记录实验室要求'
    else:
        mode=st.radio('质量目标设置',['选择标准或自定义目录','记录实验室要求'],
            index=1 if review.get('decision')=='record_only' else 0,key=prefix+'_mode',horizontal=True,
            format_func=lambda value: {'选择标准或自定义目录':'选择分析质量要求','记录实验室要求':'填写实验室自定要求'}[value])
    selected=None;spec=None;values=[]
    if mode=='选择标准或自定义目录':
        catalog=[row['spec'] for row in browse_quality_catalog() if row['kind'] in ('numeric', 'custom')]
        suggested=set(applicable)
        catalog=[r for r in catalog if r['origin']!='builtin' or r['id'] in {s['id'] for s in candidates}]
        options=sorted(catalog,key=lambda r:(r['id'] not in suggested,r['standard'],r['name']))
        by_id={r['id']:r for r in options};identifiers=[None]+list(by_id)
        if st.session_state.get(prefix+'_requirement') not in identifiers:
            st.session_state[prefix+'_requirement'] = None
        selected=st.selectbox('选择分析质量要求',identifiers,
            index=identifiers.index(goal['spec']['id']) if goal and goal['spec']['id'] in by_id else (identifiers.index(applicable[0]) if len(applicable)==1 else 0),
            format_func=lambda k:'请选择' if k is None else by_id[k]['name']+'｜'+by_id[k]['standard']+'｜'+by_id[k]['version']+'｜'+('标准' if by_id[k]['origin']=='builtin' else '实验室自定要求'),key=prefix+'_requirement')
        if selected:
            spec=by_id[selected];render_spec(spec)
            try:validate_spec_for_item(spec,item)
            except ValueError as error:st.error(str(error));return
            if scope=='lot':
                from services.project_config_service import list_lot_item_levels
                levels=list_lot_item_levels(item_id)
                if len(levels)!=item['level_count']:
                    st.info('请先保存完整的水平设置，再确认质量目标。');return
                categories=list(dict.fromkeys(r['category'] for r in spec['imprecision'] if r.get('category')))
                for level in levels.to_dict('records'):
                    order=level['level_order'];prior=next((r for r in goal.get('levels',[]) if r['level_order']==order),{}) if goal and goal['spec']['id']==selected else {}
                    from services.material_workflow_service import material_label
                    st.markdown(f"**水平 {order}：{material_label(level)}**")
                    concentration=st.number_input('质控品浓度（选填，用于核对适用范围）',min_value=0.0,value=prior.get('concentration'),format='%.4f',key=f'{prefix}_{selected}_{order}_concentration')
                    category=st.selectbox('水平类别',['请选择']+categories,index=categories.index(prior['category'])+1 if prior.get('category') in categories else 0,key=f'{prefix}_{selected}_{order}_category') if categories else ''
                    values.append(dict(level_order=order,concentration=concentration,category=category))
    supplemental_cv=None
    if spec and spec['origin']=='builtin':
        supplemental_cv=st.number_input('实验室更严 CV 要求（选填，%）',min_value=0.0,
            value=goal.get('supplement',{}).get('cv'),key=prefix+'_supplemental_cv',
            help='保留标准基准；补充值必须严于适用的同尺度 CV 要求。SD 不能在此比较。')
    recorded=review.get('draft_recorded',review.get('recorded',{}))
    if mode=='记录实验室要求':
        source_name=st.text_input('依据名称（说明书、SOP 等）',value=recorded.get('source_name',''),key=prefix+'_source_name',max_chars=200)
        source_version=st.text_input('依据版本或编号',value=recorded.get('source_version',''),key=prefix+'_source_version',max_chars=200)
        requirement_text=st.text_area('实验室自定要求',value=recorded.get('requirement_text',''),key=prefix+'_recorded_text',max_chars=2000)
        st.caption('请填写说明书、SOP 或其他依据规定的要求，并按该要求人工核对。')
    elif not selected:
        return
    person=st.text_input('确认人',value=review.get('confirmed_by',''),key=prefix+'_person',max_chars=80)
    evidence=st.text_area('适用依据（方法学、单位、浓度水平等）',value=review.get('evidence',''),key=prefix+'_evidence',max_chars=2000)
    checked=st.checkbox('已核对检验项目、单位、浓度水平及标准或依据版本',key=prefix+'_confirmed')
    if st.button('确认本批次质量目标' if scope=='lot' else '保存质量目标',key=prefix+'_adopt',disabled=not checked,type='primary'):
        try:
            with atomic_write():
                check_current()
                if mode=='记录实验室要求':
                    save_recorded_requirement(scope,item_id,source_name=source_name,source_version=source_version,
                        requirement_text=requirement_text,confirmed_by=person,evidence=evidence,**condition_args)
                else:
                    adopt_requirement(scope,item_id,selected,confirmed_by=person,evidence=evidence,levels=values,
                                      supplemental_cv=supplemental_cv,**condition_args)
        except ValueError as error:st.error(str(error))
        else:
            if on_saved is not None:on_saved()
            st.success('质量目标已保存，请继续确认项目或批次设置。');st.rerun()


def render_batch_quality(method,batch_id,month=None):
    from services.quality_review_service import runtime_review
    review=runtime_review(method,batch_id)
    summary=batch_quality_summary(method,batch_id,month)
    if not summary:
        if review:
            with st.expander('本批次质量目标',expanded=False):
                render_review_summary(review, historical=True)
        return
    with st.expander('本批次质量目标与实测变异系数',expanded=False):
        render_review_summary(review, historical=True)
        if not summary.get('goal'):
            return
        render_spec(summary['goal']['spec'])
        st.caption(f"统计范围：{summary['period']}；{summary['statistics_scope']}。此处只比较实测变异系数与允许不精密度，不能代替标准要求的完整验证。")
        st.caption(f"确认人：{summary['goal']['confirmed_by']}；依据：{summary['goal']['evidence']}")
        st.dataframe(pd.DataFrame(summary['rows']).rename(columns={'level':'水平','requirement':'采用要求','category':'水平类别','concentration':'采用浓度','count':'在控点数','days':'检测日数','cv':'实测变异系数（%）','decision':'比较结果'}),hide_index=True,width='stretch')
