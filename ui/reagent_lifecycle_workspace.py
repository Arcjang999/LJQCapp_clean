"""Reagent lot registration, verification and explicit batch switching dialogs."""
from copy import deepcopy
from datetime import datetime
from functools import partial
from hashlib import sha1
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.reagent_lifecycle_edit_service import (
    build_reagent_switch_preview, get_reagent_workspace_context,
    save_reagent_registration, save_reagent_switch, save_reagent_verification,
)

MODAL_KEY = 'reagent_lifecycle_dialog'
_TITLES = {'register': '登记试剂批号', 'verify': '登记试剂批号验证', 'switch': '切换试剂批号'}


def _text(value):
    return '' if value is None or pd.isna(value) else str(value)


def _lot_label(lot):
    return '｜'.join(filter(None, [_text(lot.get('manufacturer_name')), _text(lot.get('reagent_name')),
                                 _text(lot.get('lot_no')), '效期 ' + _text(lot.get('expiry_date'))]))


def _systems(context, lot_id):
    lot = next((row for row in context['all_lots'] if row['id'] == lot_id), None)
    if lot is None:
        return []
    return [row for row in context['systems'] if row['snapshot']['identity'][7] == lot['reagent_id']]


def _key(state, field, initial):
    key = 'rgl_' + state['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = initial
    return key


def _field(state, field, label, widget='text_input', **kwargs):
    key = _key(state, field, state['draft'][field])
    state['draft'][field] = getattr(st, widget)(label, key=key, **kwargs)
    return state['draft'][field]


def open_reagent_lifecycle_dialog(kind, lot_id=None):
    if kind not in _TITLES:
        raise ValueError('请选择试剂批号操作。')
    context = get_reagent_workspace_context()
    selected = next((row for row in context['lots'] if row['id'] == lot_id), None)
    if kind != 'register' and selected is None:
        st.error('请选择启用中的试剂批号。')
        return
    if kind == 'register':
        product = selected['reagent_id'] if selected else st.session_state.get('reagent_product_filter')
        if product not in {row['id'] for row in context['products']}:
            product = None
        draft = dict(product=product, lot_no='', expiry=None, source='')
    elif kind == 'verify':
        draft = dict(system=None, conclusion=None, evidence='', person='', when=datetime.now().replace(microsecond=0))
    else:
        draft = dict(systems=[], when=datetime.now().replace(microsecond=0), person='', reason='', confirmed=False)
    st.session_state[MODAL_KEY] = dict(kind=kind, lot_id=lot_id, context=context,
        fingerprint=context['fingerprint'], token=uuid4().hex, draft=deepcopy(draft), initial=draft, discard=False)
    st.rerun(scope='app')


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault('reagent_dialog_cleanup', []).append('rgl_' + state['token'] + '_')
    st.rerun(scope='app')


def _register_fields(state):
    products = {row['id']: row for row in state['context']['products']}
    product = _field(state, 'product', '试剂产品 *', 'selectbox', options=[None, *products],
        placeholder='请选择试剂产品', format_func=lambda value: '请选择试剂产品' if value is None else
        '｜'.join(filter(None, [_text(products[value].get('manufacturer_name')), products[value]['generic_name']])))
    lot_no = _field(state, 'lot_no', '试剂批号 *')
    expiry = _field(state, 'expiry', '效期 *', 'date_input')
    source = _field(state, 'source', '资料来源／厂家说明', 'text_area')
    st.caption('同一试剂产品的批号不能重复登记；登记后按检验项目完成验证，再切换使用批号。')
    return dict(reagent_id=product, lot_no=lot_no, expiry_date=expiry, source_text=source)


def _verify_fields(state):
    systems = {row['id']: row for row in _systems(state['context'], state['lot_id']) if row['available']}
    system_id = _field(state, 'system', '检验项目及仪器 *', 'selectbox', options=[None, *systems],
        placeholder='请选择检验项目及仪器', format_func=lambda value: '请选择检验项目及仪器' if value is None else systems[value]['label'])
    conclusion = _field(state, 'conclusion', '验证结论 *', 'selectbox', options=[None, 'pass', 'fail'],
        placeholder='请选择验证结论', format_func=lambda value: {None:'请选择验证结论','pass':'通过','fail':'未通过'}[value])
    evidence = _field(state, 'evidence', '验证方案、对照批号、接受标准及结果／资料位置 *', 'text_area')
    when = _field(state, 'when', '验证完成时间 *', 'datetime_input', value=None)
    person = _field(state, 'person', '确认人 *')
    if not systems:
        st.info('暂无使用此试剂的可用检验项目，请先核对项目和批次设置。')
    return dict(system_id=system_id, template_item_id=systems[system_id]['template_item_id'] if system_id in systems else None,
        reagent_lot_id=state['lot_id'], conclusion=conclusion, evidence=evidence, confirmed_by=person, confirmed_at=when)


def _switch_fields(state):
    systems = {row['id']: row for row in _systems(state['context'], state['lot_id']) if row['available']}
    selected = _field(state, 'systems', '本次切换的检验项目 *', 'multiselect', options=list(systems),
        format_func=lambda value: systems[value]['label'], placeholder='请选择本次需要切换的检验项目')
    when = _field(state, 'when', '实际启用时间 *', 'datetime_input', value=None)
    preview = []
    if when is None:
        st.info('请填写实际启用时间，以核对对应的验证记录。')
    else:
        preview = build_reagent_switch_preview(state['context'], reagent_lot_id=state['lot_id'], system_ids=selected, effective_at=when)
    if preview:
        st.dataframe(pd.DataFrame([{'检验项目及仪器': row['system_label'],
            '原试剂批号': row['previous_lot_no'] or '未登记', '新试剂批号': row['next_lot_no'],
            '验证结论': {'pass':'通过','fail':'未通过'}.get(row['conclusion'], '尚无验证'),
            '验证完成时间': row['confirmed_at'] or '', '验证依据': row['evidence'] or ''} for row in preview]),
            hide_index=True, width='stretch')
    elif when is not None:
        st.info('请选择本次切换的检验项目，查看新旧批号及验证记录。')
    st.caption('按实际启用时间核对最新验证。只切换所选检验项目，原均值和标准差继续使用，既往检测保留实际使用批号。')
    person = _field(state, 'person', '操作者 *')
    reason = _field(state, 'reason', '换批原因与原均值和标准差仍适用的依据 *', 'text_area')
    confirmed = _field(state, 'confirmed', '已核对所选检验项目、验证结论和启用时间，并确认原均值和标准差仍适用', 'checkbox')
    return dict(selections=preview, effective_at=when, operator=person, reason=reason, confirmed=confirmed)


@st.dialog('试剂批号', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state[MODAL_KEY]
    st.subheader(_TITLES[state['kind']])
    if state['discard']:
        st.warning('本次填写尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续填写', key='reagent_continue', type='primary', width='stretch'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃填写', key='reagent_discard', width='stretch'):
            _close()
        return
    current = get_reagent_workspace_context()
    if current['fingerprint'] != state['fingerprint']:
        st.warning('试剂资料、验证或使用记录已修改。当前输入仍保留，请关闭后重新打开，核对最新资料。')
    if state['kind'] != 'register':
        lot = next(row for row in state['context']['lots'] if row['id'] == state['lot_id'])
        st.markdown('**' + _lot_label(lot) + '**')
    values = {'register':_register_fields,'verify':_verify_fields,'switch':_switch_fields}[state['kind']](state)
    left, right = st.columns(2)
    if left.button('取消', key='reagent_cancel', width='stretch'):
        if state['draft'] != state['initial']:
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    label = {'register':'保存试剂批号','verify':'保存验证记录','switch':'确认切换所选检验项目'}[state['kind']]
    if right.button(label, key='reagent_save', type='primary', width='stretch'):
        try:
            saved = {'register':save_reagent_registration,'verify':save_reagent_verification,
                     'switch':save_reagent_switch}[state['kind']](values, expected_fingerprint=state['fingerprint'])
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            if state['kind'] == 'register':
                st.session_state['reagent_pending_registered'] = (saved, values['reagent_id'])
            st.session_state['reagent_table_version'] = st.session_state.get('reagent_table_version', 0) + 1
            st.session_state['reagent_notice'] = {'register':'试剂批号已登记，请选择检验项目登记验证。',
                'verify':'验证记录已保存。', 'switch':'已保存试剂批号使用安排，请核对实际启用时间。既往检测记录保留。'}[state['kind']]
            _close()


def render_pending_reagent_lifecycle_dialog():
    for prefix in st.session_state.pop('reagent_dialog_cleanup', []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()


def _select(key, ids):
    rows = st.session_state.get(key, {}).get('selection', {}).get('rows', [])
    st.session_state['reagent_selected_lot'] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _render_detail(context, lot):
    st.subheader('试剂批号：' + lot['lot_no'])
    st.caption(_lot_label(lot))
    st.write('资料来源／厂家说明：' + (_text(lot['source_text']) or '未填写'))
    available = lot['id'] in {row['id'] for row in context['lots']}
    systems = _systems(context, lot['id'])
    active = [row for row in systems if row['available']]
    left, right = st.columns(2)
    if left.button('登记批号验证', key='reagent_verify', disabled=not available or not active, width='stretch'):
        open_reagent_lifecycle_dialog('verify', lot['id'])
    if right.button('切换到此试剂批号', key='reagent_switch', disabled=not available or not active, type='primary', width='stretch'):
        open_reagent_lifecycle_dialog('switch', lot['id'])
    if not available:
        st.info('此试剂产品或批号已停用，仅供查询。')
    elif not active:
        st.info('暂无使用此试剂的可用检验项目，请先核对项目和批次设置。')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lots = {row['id']: row for row in context['all_lots']}
    usage_rows = []
    for system in systems:
        records = [row for row in context['usages'] if row['system_id'] == system['id']]
        effective = [row for row in records if row['effective_at'] <= now]
        current = max(effective, key=lambda row:(row['effective_at'],row['id'])) if effective else None
        # At a shared effective time, only the last recorded assignment applies.
        future_by_time = {row['effective_at']:row for row in sorted(records,key=lambda row:row['id']) if row['effective_at'] > now}
        future = [future_by_time[when] for when in sorted(future_by_time)]
        usage_rows.append({'检验项目及仪器':system['label'],
            '当前默认批号':lots.get(current['reagent_lot_id'],{}).get('lot_no','未记录') if current else '未登记',
            '生效时间':current['effective_at'] if current else '',
            '尚未生效的安排':'；'.join(lots.get(row['reagent_lot_id'],{}).get('lot_no','未记录')+'（'+row['effective_at']+'）' for row in future),
            '可调整':'是' if system['available'] else '仅供查询'})
    st.markdown('**检验项目的试剂批号使用情况**')
    if usage_rows:
        st.dataframe(pd.DataFrame(usage_rows), hide_index=True, width='stretch')
    else:
        st.caption('暂无检验项目使用此试剂。')
    names = {row['id']:row['label'] for row in systems}
    history = [row for row in context['verifications'] if row['reagent_lot_id'] == lot['id']]
    st.markdown('**所选批号的验证记录**')
    if history:
        st.dataframe(pd.DataFrame([{'检验项目及仪器':names.get(row['system_id'],'历史检验项目'),
            '验证结论':'通过' if row['conclusion']=='pass' else '未通过', '完成时间':row['confirmed_at'],
            '确认人':row['confirmed_by'], '验证依据':row['evidence']} for row in
            sorted(history,key=lambda row:(row['confirmed_at'],row['id']),reverse=True)]),hide_index=True,width='stretch')
    else:
        st.caption('暂无验证记录。')


def render_reagent_lifecycle_workspace():
    context = get_reagent_workspace_context()
    registered = st.session_state.pop('reagent_pending_registered', None)
    if registered:
        st.session_state['reagent_selected_lot'], st.session_state['reagent_product_filter'] = registered
        st.session_state['reagent_search'] = ''
    notice = st.session_state.pop('reagent_notice', '')
    if notice:
        st.success(notice)
    st.caption('登记试剂批号，按检验项目完成验证后再切换使用批号。原检测记录和均值、标准差保留。')
    settings = st.session_state.setdefault('reagent_filters', {})
    products = {row['id']:row for row in context['all_products']}
    for key, value in [('reagent_search',''), ('reagent_product_filter',None), ('reagent_show_disabled',False)]:
        if key not in st.session_state:
            st.session_state[key] = settings.get(key, value)
    if st.session_state['reagent_product_filter'] not in products:
        st.session_state['reagent_product_filter'] = None
    left, right = st.columns(2)
    query = left.text_input('搜索试剂、厂家或批号', key='reagent_search')
    product = right.selectbox('试剂产品', [None, *products], placeholder='全部试剂产品', key='reagent_product_filter',
        format_func=lambda value: '全部试剂产品' if value is None else
        '｜'.join(filter(None,[_text(products[value].get('manufacturer_name')), products[value]['generic_name']])))
    show_disabled = st.checkbox('显示已停用的试剂产品和批号', key='reagent_show_disabled')
    settings.update(reagent_search=query,reagent_product_filter=product,reagent_show_disabled=show_disabled)
    if st.button('登记试剂批号', key='reagent_register', type='primary', disabled=not context['products']):
        open_reagent_lifecycle_dialog('register', st.session_state.get('reagent_selected_lot'))
    rows = [row for row in context['all_lots'] if (show_disabled or row['id'] in {x['id'] for x in context['lots']})
            and (product is None or row['reagent_id'] == product)]
    if query.strip():
        rows = [row for row in rows if query.strip().casefold() in ' '.join(_text(row.get(field)) for field in
                ('reagent_name','manufacturer_name','lot_no')).casefold()]
    ids = [row['id'] for row in rows]
    if st.session_state.get('reagent_selected_lot') not in ids:
        st.session_state['reagent_selected_lot'] = None
    selected = st.session_state.get('reagent_selected_lot')
    if not rows:
        st.info('没有符合条件的试剂批号，请登记批号或调整筛选。')
        return
    enabled = {row['id'] for row in context['lots']}
    frame = pd.DataFrame([{'厂家':_text(row.get('manufacturer_name')),'试剂产品':row['reagent_name'],
        '试剂批号':row['lot_no'],'效期':row['expiry_date'],'资料状态':'可用' if row['id'] in enabled else '已停用',
        '资料来源':row['source_text']} for row in rows])
    key = 'reagent_lots_' + sha1(str((ids,st.session_state.get('reagent_table_version',0))).encode()).hexdigest()[:12]
    st.dataframe(frame,hide_index=True,width='stretch',height=min(320,max(115,36*len(ids)+38)),key=key,
        selection_mode='single-row',selection_default={'selection':{'rows':[ids.index(selected)] if selected in ids else []}},
        on_select=partial(_select,key,ids))
    if selected is not None:
        _render_detail(context,next(row for row in rows if row['id']==selected))
    else:
        st.caption('请选择一行，查看验证记录和使用情况。')
