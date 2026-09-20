from __future__ import annotations
import pandas as pd
import streamlit as st
from database import get_connection
from services.quality_target_service import (adopt_requirement, clear_requirement, decode, item_context,
    list_catalog, suggested_requirements, source_label, validate_spec_for_item, batch_quality_summary)


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


def render_review_summary(review):
    if not review:
        return
    if review.get('status') == 'pending':
        st.info('已带入原质量目标，请核对本次检验项目和质控品后重新确认。')
    recorded = review.get('recorded', {})
    if recorded:
        st.write('实验室自定要求：' + recorded.get('requirement_text', ''))
        st.caption('来源：' + recorded.get('source_name', '') + ' / ' + recorded.get('source_version', ''))
        st.caption('此处保存要求和依据，暂不据此自动判断是否合格。均值和标准差按原设置使用。')
    st.caption('确认人：' + review.get('confirmed_by', '') + '｜依据：' + review.get('evidence', ''))
    for row in review.get('candidates', []):
        state = '已采用' if row.get('disposition') == 'adopted' else '不适用'
        st.caption(f"{row['source']}｜{state}：{row.get('reason', '')}")


def render_adoption(scope,item_id,*,embedded=False):
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
    if goal:
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
    render_review_summary(review)
    if frozen:
        st.info('本批次已确认，质量目标不能修改。如需调整，请在新批次设置。' if goal or review or item['cv_limit'] is not None else '本批次未设置质量目标，可继续录入和查看结果。新增批次时请设置并确认质量目标。')
        return
    candidates=standard_candidates(item)
    if candidates:
        st.info('已找到与该检验项目名称相符的标准。请核对方法学、单位和适用范围；不适用时，请填写原因。')
    else:
        st.info('尚未找到与该检验项目名称相符的标准。请查找适用标准；确认无适用标准时，填写实验室自定要求及依据。')
    if item['input_value_type']!='raw':
        st.info('已收录的这些标准不能直接用于 Ct 值或 log 值。请填写相应的实验室自定要求和依据；软件暂不据此自动判断是否合格。')
        mode='记录实验室要求'
    else:
        mode=st.radio('质量目标设置',['选择标准或自定义目录','记录实验室要求'],
            index=1 if review.get('decision')=='record_only' else 0,key=prefix+'_mode',horizontal=True,
            format_func=lambda value: {'选择标准或自定义目录':'选择分析质量要求','记录实验室要求':'填写实验室自定要求'}[value])
    selected=None;spec=None;values=[]
    if mode=='选择标准或自定义目录':
        catalog=list_catalog();suggested={r['id'] for r in candidates}
        options=sorted(catalog,key=lambda r:(r['id'] not in suggested,r['standard'],r['name']))
        by_id={r['id']:r for r in options};identifiers=[None]+list(by_id)
        selected=st.selectbox('选择分析质量要求',identifiers,
            index=identifiers.index(goal['spec']['id']) if goal and goal['spec']['id'] in by_id else 0,
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
    exclusions={}
    prior_candidates={row['id']:row for row in review.get('candidates',[])}
    for candidate in candidates:
        if spec and spec['origin']=='builtin' and selected==candidate['id']:
            st.caption('选用标准：'+source_label(candidate))
        else:
            exclusions[candidate['id']]=st.text_area('不适用原因：'+source_label(candidate),
                value=prior_candidates.get(candidate['id'],{}).get('reason','') if prior_candidates.get(candidate['id'],{}).get('disposition')=='not_applicable' else '',
                key=prefix+'_exclude_'+candidate['id'],max_chars=2000,
                help='请说明不适用的原因，例如方法学、检测值类型、质控品或适用范围不同。')
    recorded=review.get('recorded',{})
    if mode=='记录实验室要求':
        source_name=st.text_input('依据名称（说明书、SOP 等）',value=recorded.get('source_name',''),key=prefix+'_source_name',max_chars=200)
        source_version=st.text_input('依据版本或编号',value=recorded.get('source_version',''),key=prefix+'_source_version',max_chars=200)
        requirement_text=st.text_area('实验室自定要求',value=recorded.get('requirement_text',''),key=prefix+'_recorded_text',max_chars=2000)
        st.caption('请填写说明书、SOP 或其他依据规定的要求。软件会保存这些文字，暂不据此自动判断是否合格。')
    elif not selected:
        return
    person=st.text_input('确认人',value=review.get('confirmed_by',''),key=prefix+'_person',max_chars=80)
    evidence=st.text_area('适用依据（方法学、单位、浓度水平等）',value=review.get('evidence',''),key=prefix+'_evidence',max_chars=2000)
    checked=st.checkbox('已核对检验项目、单位、浓度水平及标准或依据版本',key=prefix+'_confirmed')
    if st.button('确认本批次质量目标' if scope=='lot' else '保存质量目标',key=prefix+'_adopt',disabled=not checked,type='primary'):
        try:
            if mode=='记录实验室要求':
                save_recorded_requirement(scope,item_id,source_name=source_name,source_version=source_version,
                    requirement_text=requirement_text,confirmed_by=person,evidence=evidence,exclusions=exclusions)
            else:
                adopt_requirement(scope,item_id,selected,confirmed_by=person,evidence=evidence,levels=values,exclusions=exclusions)
        except ValueError as error:st.error(str(error))
        else:st.success('质量目标已保存，请继续确认项目或批次设置。');st.rerun()


def render_batch_quality(method,batch_id,month=None):
    from services.quality_review_service import runtime_review
    review=runtime_review(method,batch_id)
    summary=batch_quality_summary(method,batch_id,month)
    if not summary:
        if review:
            with st.expander('本批次质量目标',expanded=False):
                render_review_summary(review)
        return
    with st.expander('本批次质量目标与实测变异系数',expanded=False):
        render_spec(summary['goal']['spec'])
        st.caption(f"统计范围：{summary['period']}；{summary['statistics_scope']}。此处只比较实测变异系数与允许不精密度，不能代替标准要求的完整验证。")
        st.caption(f"确认人：{summary['goal']['confirmed_by']}；依据：{summary['goal']['evidence']}")
        st.dataframe(pd.DataFrame(summary['rows']).rename(columns={'level':'水平','requirement':'采用要求','category':'水平类别','concentration':'采用浓度','count':'在控点数','days':'检测日数','cv':'实测变异系数（%）','decision':'比较结果'}),hide_index=True,width='stretch')
