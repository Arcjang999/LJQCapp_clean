"""Quality-target catalog and explicit application to saved projects / draft lots."""
import csv
import io
import pandas as pd
import streamlit as st
from database import get_connection
from services.quality_target_service import (IMPORT_COLUMNS, list_catalog, custom_template_csv,
    preview_custom_csv, import_custom_csv)
from ui.quality_targets import catalog_table,render_adoption


def render_quality_targets_page():
    st.subheader('质量目标')
    if st.button('返回工作台',key='quality_back'):
        st.session_state['show_quality_targets_page']=False;st.rerun()
    st.caption('查询分析质量要求，选择适用来源并在批次中确认。允许偏倚和总误差保留供查阅，本页不自动完成正确度或总误差评价。')
    browse,project,lot,custom=st.tabs(['标准与要求','项目默认要求','批次采用要求','自定义与导入'])
    with browse:
        records=list_catalog()
        query=st.text_input('搜索检验项目或标准',key='quality_search')
        if query:records=[r for r in records if query.casefold() in ' '.join([r['name'],r['standard'],*r.get('aliases',[])]).casefold()]
        st.caption(f'共 {len(records)} 条。血液/凝血精密度收录日间要求；批内要求不作为日常质控上限。')
        st.dataframe(catalog_table(records),hide_index=True,width='stretch')
        st.download_button('导出当前要求目录（CSV）',catalog_table(records).to_csv(index=False).encode('utf-8-sig'),'质量要求目录.csv','text/csv')
        st.caption('目录导出用于核对；导入请使用“自定义与导入”中的专用模板。标准缺项留空，不按固定比例推导。')
    with project:
        with get_connection() as c:
            rows=c.execute('''SELECT i.id,t.template_name,m.chinese_name,i.qc_method FROM qc_project_template_items i
                JOIN qc_project_templates t ON t.id=i.template_id JOIN md_test_items m ON m.id=i.test_item_id
                WHERE i.is_disabled=0 AND t.is_disabled=0 ORDER BY t.id,i.id''').fetchall()
        _selector(rows,'project')
    with lot:
        with get_connection() as c:
            rows=c.execute('''SELECT i.id,t.config_name AS template_name,m.chinese_name,i.qc_method,q.lot_no,t.status FROM qc_lot_config_items i
                JOIN qc_lot_configs t ON t.id=i.lot_config_id JOIN md_test_items m ON m.id=i.test_item_id
                JOIN md_qc_material_lots q ON q.id=t.qc_material_lot_id
                WHERE i.is_disabled=0 AND t.is_disabled=0 ORDER BY t.id DESC,i.id''').fetchall()
        _selector(rows,'lot')
    with custom:
        st.caption('自定义/导入要求始终标记为实验室自定义。内置标准不可覆盖；新版本另存新条目，不改变已采用副本。')
        with st.expander('新增实验室自定义要求'):
            with st.form('quality_custom_form'):
                values={}
                for label in ['检验项目','来源名称','版本','单位']:
                    values[label]=st.text_input(label,max_chars=100)
                values['实施日期']=str(st.date_input('实施日期'))
                values['允许不精密度类型']=st.selectbox('允许不精密度类型',['CV','SD'])
                values['上限']=str(st.number_input('上限',min_value=0.0,value=None))
                values['比较符']=st.selectbox('比较符',['<=','<'])
                values['浓度下限']=st.text_input('浓度下限（可留空）');values['浓度上限']=st.text_input('浓度上限（可留空）')
                values['水平类别']=st.text_input('水平类别（可留空）',max_chars=40)
                values['允许偏倚说明']=st.text_input('允许偏倚说明（选填）',max_chars=300)
                values['允许总误差说明']=st.text_input('允许总误差说明（选填）',max_chars=300)
                values['来源链接或依据']=st.text_input('来源链接或依据',max_chars=500)
                values['确认人']=st.text_input('确认人',max_chars=80)
                if st.form_submit_button('保存自定义要求'):
                    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=IMPORT_COLUMNS);writer.writeheader();writer.writerow(values)
                    try:import_custom_csv(stream.getvalue().encode('utf-8'))
                    except ValueError as e:st.error(str(e))
                    else:st.success('已保存自定义要求。');st.rerun()
        st.download_button('下载质量要求导入模板',custom_template_csv(),'质量要求导入模板.csv','text/csv')
        uploaded=st.file_uploader('上传质量要求 CSV',type=['csv'],key='quality_import')
        if uploaded:
            try:preview=preview_custom_csv(uploaded.getvalue())
            except ValueError as e:st.error(str(e))
            else:
                st.dataframe(catalog_table(preview),hide_index=True,width='stretch')
                checked=st.checkbox('已核对导入预览、来源和确认人',key='quality_import_confirm')
                if st.button('确认导入质量要求',disabled=not checked):
                    try:n=import_custom_csv(uploaded.getvalue())
                    except ValueError as e:st.error(str(e))
                    else:st.success(f'已导入 {n} 条。')


def _selector(rows,scope):
    if not rows:st.info('请先在项目/批次管理建立相应资料。');return
    labels={r['id']:f"{r['template_name']}｜{r['chinese_name']}｜{r['qc_method']}" for r in rows}
    if scope=='lot':
        labels={r['id']:labels[r['id']]+f"｜批号 {r['lot_no']}｜{'待确认' if r['status']=='draft' else '已确认'}" for r in rows}
    selected=st.selectbox('选择检验项目配置',[None]+list(labels),format_func=lambda i:'请选择' if i is None else labels[i],key=f'quality_{scope}_selector')
    if selected:render_adoption(scope,selected)
