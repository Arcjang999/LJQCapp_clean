"""Quality-target catalog and explicit application to saved projects / draft lots."""
import csv
import io
import streamlit as st
from database import get_connection
from services.quality_target_service import (IMPORT_COLUMNS, list_catalog, custom_template_csv,
    preview_custom_csv, import_custom_csv)
from ui.quality_targets import catalog_table,render_adoption


def render_quality_targets_page():
    st.subheader('质量目标')
    if st.button('返回工作台',key='quality_back'):
        st.session_state['show_quality_targets_page']=False;st.rerun()
    st.caption('为检验项目选择适用标准；无适用标准时，填写实验室自定要求及依据。新批次需逐水平核对质量目标。')
    project,browse,custom=st.tabs(['质量目标设置','分析质量要求','实验室自定要求'])
    with browse:
        records=list_catalog()
        query=st.text_input('搜索检验项目或标准',key='quality_search')
        if query:records=[r for r in records if query.casefold() in ' '.join([r['name'],r['standard'],*r.get('aliases',[])]).casefold()]
        st.caption(f'共 {len(records)} 条。血液和凝血项目列出的是日间不精密度要求，不使用批内不精密度要求作为日常质控上限。')
        st.dataframe(catalog_table(records),hide_index=True,width='stretch')
        st.download_button('导出分析质量要求（CSV）',catalog_table(records).to_csv(index=False).encode('utf-8-sig'),'质量要求目录.csv','text/csv')
        st.caption('导出文件供查阅。导入时请使用“实验室自定要求”中的模板。标准未规定的要求显示为空白。')
    with project:
        with get_connection() as c:
            rows=c.execute('''SELECT i.id,t.template_name,m.chinese_name,i.qc_method FROM qc_project_template_items i
                JOIN qc_project_templates t ON t.id=i.template_id JOIN md_test_items m ON m.id=i.test_item_id
                WHERE i.is_disabled=0 AND t.is_disabled=0 ORDER BY t.id,i.id''').fetchall()
        _selector(rows,'project')
        st.caption('新批次的目标确认和已有批次的目标查看，请在批次管理中展开相应检验项目。')
        if st.button('前往批次管理',key='quality_open_lot_management'):
            from ui.common import open_global_page
            st.session_state['v11_management_tabs']='批次管理'
            open_global_page('show_project_management_page')
            st.rerun()
    with custom:
        st.caption('新增和导入的要求均标为“实验室自定要求”。已收录的标准不能修改；新增版本不会改变已有批次的质量目标。')
        if st.button('新增实验室自定要求',key='quality_custom_open'):
            _custom_requirement_dialog()
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
    methods={'lj':'单水平（LJ）','zscore':'多水平法','instant':'即时法'}
    labels={r['id']:f"{r['chinese_name']}｜{r['template_name']}｜{methods.get(r['qc_method'],r['qc_method'])}" for r in rows}
    selected=st.selectbox('选择检验项目',[None]+list(labels),format_func=lambda i:'请选择' if i is None else labels[i],key=f'quality_{scope}_selector')
    if selected and st.button('设置质量目标',key=f'quality_{scope}_open'):
        _adoption_dialog(scope,selected)


@st.dialog('新增实验室自定要求',width='large',dismissible=False)
def _custom_requirement_dialog():
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
        if st.form_submit_button('保存实验室自定要求'):
            stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=IMPORT_COLUMNS);writer.writeheader();writer.writerow(values)
            try:import_custom_csv(stream.getvalue().encode('utf-8'))
            except ValueError as e:st.error(str(e))
            else:st.success('已保存实验室自定要求。');st.rerun()
    if st.button('取消',key='quality_custom_cancel'):
        st.rerun()


@st.dialog('质量目标设置',width='large',dismissible=False)
def _adoption_dialog(scope,item_id):
    if st.button('关闭',key=f'quality_dialog_close_{scope}_{item_id}'):
        st.rerun()
    render_adoption(scope,item_id,embedded=True)
