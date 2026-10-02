from hashlib import sha256
import pandas as pd
import streamlit as st
from services.daily_result_io_service import export_daily_workbook,preview_daily_workbook,apply_daily_workbook


def render_daily_result_files(draft):
    with st.expander('常规结果文件：模板、预览与导出'):
        st.download_button('下载本组结果模板',export_daily_workbook(draft,template=True),'常规质控结果模板.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key='daily_template')
        st.download_button('导出本组已填内容',export_daily_workbook(draft),'常规质控本组结果.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key='daily_export')
        st.caption('先下载本组模板填写检测值，再上传并预览。核对项目和水平后，点击“带入结果草稿”，再到整组录入区核对并保存；保存前不会计入当日检测。')
        file=st.file_uploader('上传本组常规结果文件',type=['xlsx'],max_upload_size=10,key='daily_result_file_'+draft['draft_id'])
        if file:
            data=file.getvalue();digest=sha256(data).hexdigest()
            if st.button('预览结果文件'):
                draft['file_preview']=preview_daily_workbook(data,draft)
            preview=draft.get('file_preview')
            if preview and preview['file_hash']==digest:
                for error in preview['errors']:st.error(error)
                st.dataframe(pd.DataFrame([{'文件行号':r['line'],'检验项目':r['test_item_name'],'水平':r['level_name'],'检测值':r['value']} for r in preview['rows']]),hide_index=True,width='stretch')
                if st.button('带入结果草稿',disabled=not preview['valid']):
                    apply_daily_workbook(draft,preview)
                    for field in ('test_time','operator'):
                        st.session_state.pop(f'daily_{field}_{draft["draft_id"]}',None)
                    for key in list(st.session_state):
                        if key.startswith('entry_note_'+draft['draft_id']) or key.startswith('entry_reagent_'+draft['draft_id']):st.session_state.pop(key,None)
                    draft.pop('file_preview',None);st.rerun()
                if st.button('取消文件预览'):draft.pop('file_preview',None);st.rerun()
