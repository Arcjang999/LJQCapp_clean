from __future__ import annotations
import pandas as pd
import streamlit as st
from database import get_connection
from services.quality_target_service import (adopt_requirement, clear_requirement, decode, item_context,
    list_catalog, suggested_requirements, source_label, validate_spec_for_item, batch_quality_summary)


def catalog_table(records):
    return pd.DataFrame([{'检验项目':r['name'],'来源类型':'内置标准' if r['origin']=='builtin' else '实验室自定义',
        '来源/版本':r['standard']+' / '+r['version'],'实施日期':r['effective_date'],
        '测量单位':r['measurement_unit'] or '原表未限定（百分数要求）','适用范围':r['scope'],
        '允许不精密度':r['imprecision_text'],'允许偏倚':r['bias_text'] or '未规定',
        '允许总误差/可比性偏差':r['tea_text'] or '未规定','条款':r['source_clause'],
        'PDF页码':r.get('source_page'),'说明':r['notes']} for r in records])


def render_spec(spec):
    st.caption(source_label(spec)+f"｜实施：{spec['effective_date']}")
    st.write('允许不精密度：'+spec['imprecision_text'])
    st.write('允许偏倚：'+(spec['bias_text'] or '原条款未规定'))
    st.write('允许总误差/可比性偏差：'+(spec['tea_text'] or '原条款未规定'))
    st.caption('适用范围：'+spec['scope']+'；测量单位：'+(spec['measurement_unit'] or '原表未限定，采用时核对本项目单位'))
    if spec.get('notes'):st.info(spec['notes'])
    if spec.get('source_url','').startswith('https://'):
        st.link_button('查看标准原文',spec['source_url'])
    if spec.get('source_page'):st.caption(f"来源：PDF 第 {spec['source_page']} 页（包括封面），详见所列条款。")


def render_adoption(scope,item_id):
    item=item_context(scope,item_id);goal=decode(item['quality_goal_json']);prefix=f'quality_{scope}_{item_id}'
    st.caption(f"{item['test_item_name']}｜单位 {item['unit_symbol']}｜{item['level_count']} 个水平")
    frozen=False
    if scope=='lot':
        with get_connection() as c:
            config=c.execute('SELECT status,activated_at FROM qc_lot_configs WHERE id=?',(item['lot_config_id'],)).fetchone()
            frozen=config['status']!='draft' or bool(config['activated_at']) or c.execute('SELECT 1 FROM qc_workbench_bindings WHERE lot_config_item_id=?',(item_id,)).fetchone() is not None
    if goal:
        with st.expander('当前采用/待核对的质量要求',expanded=True):
            render_spec(goal['spec'])
            st.caption('待逐水平确认' if goal.get('pending') else f"确认人：{goal['confirmed_by']}｜采用时间：{goal['adopted_at']}")
            if goal.get('evidence'):st.caption('适用依据：'+goal['evidence'])
            if goal.get('levels'):
                st.dataframe(pd.DataFrame([{'水平':v['level_order'],'浓度':v['concentration'],'类别':v['category'],
                    '要求':f"{v['rule']['operator']} {v['rule']['value']:g}{v['rule']['unit']}"} for v in goal['levels']]),hide_index=True)
    elif item['cv_limit'] is not None:
        st.caption(f"原手填 CV 上限：≤{item['cv_limit']:g}%｜依据：{item['quality_target_source_text'] or '未填写'}")
    if frozen:
        st.info('已有批次保留原采用要求。请在新批次设置时选择新要求。')
        return
    if item['input_value_type']!='raw':
        st.info('Ct / log 项目不能直接采用本页的浓度尺度要求。原手填设置保留。');return
    catalog=list_catalog();suggestions=suggested_requirements(item['test_item_name']);suggested={r['id'] for r in suggestions}
    options=sorted(catalog,key=lambda r:(r['id'] not in suggested,r['standard'],r['name']))
    by_id={r['id']:r for r in options}
    identifiers=[None]+list(by_id)
    selected=st.selectbox('选择适用质量要求',identifiers,
        index=identifiers.index(goal['spec']['id']) if goal and goal['spec']['id'] in by_id else 0,
        format_func=lambda k:'请选择（不会自动采用）' if k is None else ('建议匹配｜' if k in suggested else '')+by_id[k]['name']+'｜'+by_id[k]['standard']+'｜'+by_id[k]['version']+'｜'+('内置' if by_id[k]['origin']=='builtin' else '自定义'),key=prefix+'_requirement')
    if selected:
        spec=by_id[selected];render_spec(spec)
        try:validate_spec_for_item(spec,item)
        except ValueError as e:st.error(str(e));return
        st.caption('请核对项目、检测方法、测量单位及适用条件。选择名称相似的条目不会自动证明适用。')
        values=[]
        if scope=='lot':
            from services.project_config_service import list_lot_item_levels
            levels=list_lot_item_levels(item_id)
            if len(levels)!=item['level_count']:
                st.info('请先在批次管理保存完整的水平设置，再确认质量目标。');return
            categories=list(dict.fromkeys(r['category'] for r in spec['imprecision'] if r.get('category')))
            for level in levels.to_dict('records'):
                order=level['level_order'];prior=next((r for r in goal.get('levels',[]) if r['level_order']==order),{}) if goal and goal['spec']['id']==selected else {}
                st.markdown(f"**水平 {order}：{level['level_name']}**")
                c=st.number_input('用于确定适用范围的浓度（本项目单位，选填）',min_value=0.0,value=prior.get('concentration'),format='%.4f',key=f'{prefix}_{selected}_{order}_concentration')
                category=st.selectbox('水平类别',['请选择']+categories,index=categories.index(prior['category'])+1 if prior.get('category') in categories else 0,key=f'{prefix}_{selected}_{order}_category') if categories else ''
                values.append(dict(level_order=order,concentration=c,category=category))
        else:st.caption('此处保存项目默认版本；新批次仍需按各水平确认后才可采用，不影响已有批次。')
        person=st.text_input('确认人',key=prefix+'_person',max_chars=80)
        evidence=st.text_area('适用性依据（项目匹配、单位、水平类别及说明书要求）',key=prefix+'_evidence',max_chars=1000)
        checked=st.checkbox('已核对适用项目、单位、浓度/类别和来源版本',key=prefix+'_confirmed')
        if st.button('确认采用质量要求',key=prefix+'_adopt',disabled=not checked,type='primary'):
            try:adopt_requirement(scope,item_id,selected,confirmed_by=person,evidence=evidence,levels=values)
            except ValueError as e:st.error(str(e))
            else:st.success('已保存；项目或批次设置需重新确认。');st.rerun()
    if goal and st.button('取消待用质量目标，改为手动设置',key=prefix+'_clear'):
        try:clear_requirement(scope,item_id)
        except ValueError as e:st.error(str(e))
        else:st.rerun()


def render_batch_quality(method,batch_id,month=None):
    summary=batch_quality_summary(method,batch_id,month)
    if not summary:return
    with st.expander('分析质量要求与实测 CV',expanded=False):
        render_spec(summary['goal']['spec'])
        st.caption(f"统计范围：{summary['period']}；{summary['statistics_scope']}。这里只比较统计值与所选限值，不代表完成标准规定的全部验证。")
        st.caption(f"采用确认人：{summary['goal']['confirmed_by']}；依据：{summary['goal']['evidence']}")
        st.dataframe(pd.DataFrame(summary['rows']).rename(columns={'level':'水平','requirement':'采用要求','category':'水平类别','concentration':'采用浓度','count':'在控点数','days':'检测日数','cv':'实测 CV（%）','decision':'比较结果'}),hide_index=True,width='stretch')
