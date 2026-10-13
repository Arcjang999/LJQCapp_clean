"""Download, review and confirm a unit's preparation file in one guarded dialog."""
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.lab_preparation_service import (
    build_lab_preparation_reference_xlsx, build_lab_preparation_template,
    prepare_lab_preparation, save_lab_preparation,
)


DIALOG_KEY = 'lab_preparation_dialog'


def _close():
    st.session_state.pop(DIALOG_KEY, None)
    st.rerun(scope='app')


@st.dialog('单位资料准备', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state[DIALOG_KEY]
    if state['discard']:
        st.warning('当前文件尚未保存，是否放弃本次资料准备？')
        keep, discard = st.columns(2)
        if keep.button('继续准备', key='lab_prep_keep', type='primary', width='stretch'):
            state.update(discard=False, widget_had_upload=False)
            st.rerun(scope='app')
        if discard.button('放弃本次准备', key='lab_prep_discard', width='stretch'):
            _close()
        return
    st.caption('下载清单，填写实验室、人员、仪器、检验项目、计量单位、方法学、试剂和质控品批号，再上传检查。')
    st.info('保存资料后，点击“新建项目”，设置质量目标、质控品批号、各水平及均值和标准差，再登记试剂批号验证。')
    template, reference = st.columns(2)
    template.download_button('下载空白单位资料清单', build_lab_preparation_template(),
        file_name='单位资料准备清单.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key='lab_prep_template', width='stretch')
    reference.download_button('下载已登记资料清单', build_lab_preparation_reference_xlsx(),
        file_name='已登记资料清单.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key='lab_prep_reference', width='stretch')
    st.caption('引用已有资料时，把“已登记资料清单”中的“已登记标识”复制到填写清单的同名列。关联本清单新增资料时，填写其“清单编号”。')
    uploaded = st.file_uploader('上传填写后的单位资料清单', type=['xlsx'], max_upload_size=10,
        key='lab_prep_upload_' + state['token'])
    if uploaded is not None:
        data = uploaded.getvalue()
        if data != state['data']:
            state.update(review=None, error='', request_key=uuid4().hex)
        state.update(data=data, filename=uploaded.name, widget_had_upload=True)
    elif state.get('widget_had_upload'):
        state.update(data=b'', filename='', review=None, error='', widget_had_upload=False, request_key=uuid4().hex)
    if state['data']:
        st.caption('当前文件：' + state['filename'])
    if st.button('审查单位资料', key='lab_prep_review', disabled=not state['data'], width='stretch'):
        try:
            state['review'] = prepare_lab_preparation(state['data'])
            state['error'] = ''
        except ValueError as exc:
            state['review'] = None
            state['error'] = str(exc)
    if state.get('error'):
        st.error(state['error'])
    review = state.get('review')
    if review:
        summary = review['summary']
        st.caption(f"资料 {summary['rows']} 行 · 新增 {summary['create']} 行 · 引用已有 {summary['reuse']} 行 · 单位设置变更 {summary['settings']} 项")
        if summary['has_blocking_errors']:
            st.error('请按下表提示修改文件，再上传检查。修改前的文件仍保留。')
        else:
            st.success('文件检查完成，核对下方资料后点击“确认保存单位资料”。')
        if review['issues']:
            st.dataframe(pd.DataFrame([{'工作表': item['sheet'], '行': item['row'] or '-',
                '需修正': '是' if item['blocking'] else '可后续补充', '提示': item['message']}
                for item in review['issues']]), hide_index=True, width='stretch')
        if review['settings']:
            from services.lab_preparation_service import UNIT_FIELDS
            labels = {key: label for label, key in UNIT_FIELDS.items()}
            st.markdown('**单位设置变化**')
            st.dataframe(pd.DataFrame([{'项目': labels[key], '原内容': review['before_settings'][key] or '未填写',
                                       '本次内容': value} for key, value in review['settings'].items()]),
                         hide_index=True, width='stretch')
        if review['preview']:
            st.markdown('**本次要保存的资料**')
            st.dataframe(pd.DataFrame(review['preview'])[['工作表', '清单编号', '处理', '内容']],
                         hide_index=True, width='stretch')
    cancel, save = st.columns(2)
    if cancel.button('取消', key='lab_prep_cancel', width='stretch'):
        if state['data'] or review:
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if save.button('确认保存单位资料', key='lab_prep_save', type='primary', width='stretch',
                   disabled=not review or review['summary']['has_blocking_errors']) and review:
        try:
            result = save_lab_preparation(state['data'], expected_preview_hash=review['preview_hash'],
                                         request_key=state['request_key'])
        except ValueError as exc:
            state['review'] = None
            state['error'] = str(exc)
            st.rerun(scope='app')
        else:
            st.session_state['refresh_settings_form'] = True
            st.session_state['lab_preparation_receipt'] = result
            _close()


def render_lab_preparation_entry():
    receipt = st.session_state.get('lab_preparation_receipt')
    if receipt:
        verb = '此文件已保存' if receipt['reused'] else '单位资料已保存'
        st.success(f"{verb}，记录编号：{receipt['receipt_id']}。可在基础资料中查看，接着新建项目和批次。")
        with st.expander('查看本次保存资料'):
            rows = receipt['saved_rows']
            if rows:
                st.dataframe(pd.DataFrame(rows)[['工作表', '清单编号', '处理', '内容']], hide_index=True, width='stretch')
    busy = any(st.session_state.get(key) for key in
        ('qc_record_dialog', 'qc_transfer_dialog', 'zscore_reference_dialog', 'show_instant_transfer_dialog', DIALOG_KEY))
    if st.button('准备单位资料', key='lab_prep_open', disabled=busy):
        st.session_state[DIALOG_KEY] = dict(token=uuid4().hex, request_key=uuid4().hex, data=b'',
                                          filename='', review=None, error='', discard=False, widget_had_upload=False)
        st.rerun(scope='app')
    if st.session_state.get(DIALOG_KEY):
        _render_dialog()
