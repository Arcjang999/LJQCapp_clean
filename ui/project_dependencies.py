"""Add missing reference records with the original forms inside project setup."""
from uuid import uuid4

import streamlit as st


_LABELS = {'test_item': '检验项目', 'lab_instrument': '仪器', 'reagent': '试剂',
           'method': '方法学', 'unit': '单位', 'qc_material': '质控品产品'}
_FIELDS = {
    'quick': {'test_item': 'test_item_id', 'lab_instrument': 'lab_instrument_id',
              'reagent': 'reagent_id', 'method': 'method_id', 'unit': 'unit_id', 'qc_material': 'qc_material_id'},
    'project': {'lab_instrument': 'lab_instrument_id', 'reagent': 'default_reagent_id',
                'method': 'default_method_id', 'qc_material': 'qc_material_id'},
    'item': {'test_item': 'test_item_id', 'reagent': 'reagent_id', 'method': 'method_id', 'unit': 'unit_id'},
    'panel': {'test_item': None, 'reagent': None, 'method': None, 'unit': None},
}


def project_dependency_pending(ctx):
    return bool(ctx.get('dependency_registration'))


def apply_project_dependency_saved(ctx):
    """Apply before rendering parent widgets, which Streamlit then owns for this run."""
    saved = ctx.pop('dependency_saved', None)
    if not saved:
        return
    field, identifier = saved
    if field is None:
        return
    ctx['draft'][field] = identifier
    st.session_state['project_draft_' + ctx['token'] + '_' + field] = identifier
    if field == 'qc_material_id':
        if ctx.get('kind') == 'quick':
            count = int(ctx['draft']['level_count'])
            for name, value in (('level_product_ids', identifier), ('level_ids', None)):
                selected = list(ctx['draft'].get(name, []))
                selected += [None] * max(0, count - len(selected))
                selected[0] = value
                ctx['draft'][name] = selected
            slot = 'quick_control_' + ctx['token'] + '_0'
            st.session_state[slot + '_product'] = identifier
            st.session_state[slot + '_level'] = None
            st.session_state[slot + '_last_product'] = identifier
        prefix = 'project_product_' + ctx['token']
        ctx[prefix + '_mode'] = '人工选品'
        st.session_state[prefix + '_mode'] = '人工选品'
        st.session_state[prefix + '_product'] = identifier
        ctx[prefix + '_last_product'] = identifier


def render_project_dependencies(ctx, *, kind=None, disabled=False, locked_identity=False):
    from services import master_data_service as master
    from ui.master_data_dialogs import create_master_data_draft, render_master_data_editor
    from ui.qc_product_choice import has_pending_product_relation

    kind = kind or ctx['kind']
    fields = dict(_FIELDS[kind])
    if locked_identity:
        for entity in ('lab_instrument', 'qc_material'):
            fields.pop(entity, None)
    listers = {'test_item': master.list_test_items, 'lab_instrument': master.list_lab_instruments,
               'reagent': master.list_reagents, 'method': master.list_methods,
               'unit': master.list_units, 'qc_material': master.list_qc_materials}
    missing = [_LABELS[entity] for entity in fields if listers[entity]().empty]
    if missing:
        st.info('、'.join(missing) + '尚未登记，请展开“补充基础资料”补充。')
    notice = ctx.pop('dependency_notice', '')
    if notice:
        st.success(notice)
    state = ctx.get('dependency_registration')
    prefix = 'project_dependency_' + ctx['token']
    with st.expander('补充基础资料', expanded=bool(state) or bool(missing)):
        st.caption('缺少资料可在这里登记，项目已填内容会保留。')
        if state:
            st.info('正在登记' + _LABELS[state['entity']] + '，请先保存或取消。')
        elif disabled:
            st.info('请先保存或取消“登记质控品批号”。')
        elif has_pending_product_relation(ctx):
            st.info('请先保存或取消“登记试剂配套的质控品”中的记录。')
        selected = st.selectbox('要补充的资料', list(fields), format_func=_LABELS.get,
            key=prefix + '_type', disabled=bool(state) or disabled)
        if st.button('登记' + _LABELS[selected], key=prefix + '_open',
                     disabled=bool(state) or disabled or has_pending_product_relation(ctx)):
            editor = (dict(product_id=None, record=None, nonce=uuid4().hex) if selected == 'qc_material'
                      else create_master_data_draft(selected))
            ctx['dependency_registration'] = dict(entity=selected, field=fields[selected], editor=editor)
            st.rerun(scope='app')
        if not state:
            return False

        def cancel():
            ctx.pop('dependency_registration', None)
            st.rerun(scope='app')

        def saved(identifier):
            ctx['dependency_saved'] = (state['field'], int(identifier))
            ctx['dependency_notice'] = _LABELS[state['entity']] + ('已保存并选中，请继续设置。'
                if state['field'] else '已保存，请在上方选择后继续设置。')
            if state['field']:
                ctx['draft'].pop('product_choice_review', None)
            cancel()

        st.markdown('**登记' + _LABELS[state['entity']] + '**')
        if state['entity'] == 'qc_material':
            from ui.material_catalog import _product_editor
            _product_editor(state['editor'], on_saved=saved, on_cancel=cancel)
        else:
            render_master_data_editor(state['editor'], on_saved=saved, on_cancel=cancel)
    return True
