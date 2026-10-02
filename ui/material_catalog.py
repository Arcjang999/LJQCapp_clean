"""List/detail material catalogue with one explicit editing dialog at a time."""
from __future__ import annotations

from datetime import date
import hashlib

import streamlit as st
from services.search_service import fuzzy_match, SEARCH_HELP

from services.master_data_service import create_qc_material, list_manufacturers, list_qc_materials
from services.material_catalog_service import (
    get_control_material, get_material_product, list_control_materials,
    restore_control_material_lot,
    set_control_material_disabled, set_material_product_disabled,
    update_control_material, update_material_product,
)
from services.material_workflow_service import concentration_label, list_material_specs, material_label, register_control_material


def _open_dialog(kind: str, *, product_id=None, level_id=None) -> None:
    previous = st.session_state.get('material_dialog')
    parent = previous if previous and previous['kind'] in ('relationships', 'batches') else None
    if kind == 'batches':
        st.session_state.pop('material_catalog_search', None)
        st.session_state.pop('material_batch_show_disabled', None)
        st.session_state.pop('material_catalog_reveal_id', None)
    nonce = int(st.session_state.get('material_dialog_nonce', 0)) + 1
    st.session_state['material_dialog_nonce'] = nonce
    st.session_state['material_dialog'] = {
        'kind': kind, 'product_id': product_id, 'level_id': level_id, 'nonce': nonce,
        'return_to': parent,
        'record': get_control_material(level_id) if level_id else
                  get_material_product(product_id) if product_id and kind.startswith('product_') else None,
    }

    if parent and kind != 'coverage_picker':
        st.rerun()

def _close_dialog(*, notice='', selected_id=None, product_id=None) -> None:
    state = st.session_state.pop('material_dialog', None) or {}
    parent = state.get('return_to')
    if parent:
        st.session_state['material_dialog'] = parent
    if selected_id is not None:
        st.session_state['material_catalog_selected_id'] = int(selected_id)
        st.session_state['material_catalog_reveal_id'] = int(selected_id)
        st.session_state['material_catalog_reveal_query'] = parent.get('search', '') if parent else st.session_state.get('material_catalog_search', '')
    if product_id is not None and not parent:
        st.session_state['material_catalog_pending_product'] = int(product_id)
    if notice:
        st.session_state['product_catalogue_table_nonce'] = int(st.session_state.get('product_catalogue_table_nonce', 0)) + 1
        st.session_state['material_catalog_notice'] = notice
        st.session_state['material_catalog_table_nonce'] = int(st.session_state.get('material_catalog_table_nonce', 0)) + 1
    st.rerun()


def _cancel_controls(prefix: str, *, dirty: bool) -> bool:
    if st.button('取消', key=f'{prefix}_cancel', width='stretch'):
        if not dirty:
            _close_dialog()
        st.session_state[f'{prefix}_discard'] = True
    if st.session_state.get(f'{prefix}_discard'):
        st.warning('修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', key=f'{prefix}_continue', width='stretch'):
            st.session_state[f'{prefix}_discard'] = False
            st.rerun()
        if right.button('放弃修改', key=f'{prefix}_discard_confirm', width='stretch'):
            _close_dialog()
        return True
    return False


def _material_editor(state: dict) -> None:
    record = state['record'] or {}
    edit = bool(state['level_id'])
    prefix = f'material_edit_{state["nonce"]}'
    product_id = state['product_id']
    locked = bool(record.get('identity_locked'))
    product = get_material_product(product_id)
    st.caption(f'质控品：{product["generic_name"]}')
    if locked:
        st.info('此批号已用于批次、验证或检测，当前只能补充备注。需要更换浓度水平、浓度编号、批号或效期时，请新增质控品批号，并核对新批次设置。')
    spec_id = None
    defaults = dict(level_name=record.get('level_name', ''), level_code=record.get('level_code', ''),
        catalog_no=record.get('specification_catalog_no', ''), lot_no=record.get('lot_no', ''),
        expiry_date=date.fromisoformat(record['expiry_date']) if record.get('expiry_date') else None,
        concentration_note=record.get('concentration_label', ''))
    if not edit:
        specs = {int(r['id']): r for r in list_material_specs(product_id).to_dict('records')}
        spec_id = st.selectbox('已登记的浓度水平', [None] + list(specs),
            format_func=lambda value: '新增浓度水平' if value is None else concentration_label(specs[value]),
            key=f'{prefix}_spec')
        if spec_id:
            defaults.update(level_name=specs[spec_id]['level_name'], level_code=specs[spec_id]['level_code'], catalog_no=specs[spec_id]['catalog_no'])
    # Each specification keeps its own inputs; changing selection never writes catalogue data.
    identity_prefix = f'{prefix}_{spec_id or "new"}'
    left, right = st.columns(2)
    name = left.text_input('浓度水平 *', value=defaults['level_name'], max_chars=100,
        disabled=locked or bool(spec_id), key=f'{identity_prefix}_name', placeholder='例如：低值、高值或厂家 Level 2')
    code = right.text_input('浓度编号', value=defaults['level_code'], max_chars=100,
        disabled=locked or bool(spec_id), key=f'{identity_prefix}_code', placeholder='例如：001、L02；保留原编号')
    catalog = st.text_input('货号', value=defaults['catalog_no'], max_chars=100,
        disabled=locked or bool(spec_id), key=f'{identity_prefix}_catalog')
    left, right = st.columns(2)
    lot = left.text_input('批号 *', value=defaults['lot_no'], max_chars=100, disabled=locked, key=f'{prefix}_lot')
    expiry = right.date_input('效期 *', value=defaults['expiry_date'], format='YYYY-MM-DD', disabled=locked, key=f'{prefix}_expiry')
    note = st.text_area('浓度说明 / 备注', value=defaults['concentration_note'], key=f'{prefix}_note',
        help='各检验项目的均值和标准差请在批次设置中填写并确认。')
    draft = dict(level_name=name, level_code=code, catalog_no=catalog, lot_no=lot,
                 expiry_date=expiry, concentration_note=note)
    dirty = draft != defaults or bool(spec_id)
    if _cancel_controls(prefix, dirty=dirty):
        return
    if st.button('保存修改' if edit else '保存', key=f'{prefix}_save', type='primary', width='stretch'):
        try:
            if edit:
                update_control_material(state['level_id'], **draft, expected_version=record['edit_version'])
                level_id = state['level_id']
            else:
                level_id = register_control_material(material_id=product_id, specification_id=spec_id, **draft)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _close_dialog(notice='质控品批号已保存。', selected_id=level_id)


def _product_editor(state: dict) -> None:
    record = state['record'] or {}
    edit = bool(state['product_id'])
    locked = bool(record.get('identity_locked'))
    prefix = f'product_edit_{state["nonce"]}'
    all_manufacturers = list_manufacturers(include_disabled=edit).to_dict('records')
    manufacturers = {int(r['id']): r['display_name'] for r in all_manufacturers
                     if 'qc_material' in r['categories'] or r['id'] == record.get('manufacturer_id')}
    options = [None] + list(manufacturers)
    manufacturer = st.selectbox('厂家 *', options,
        index=options.index(record.get('manufacturer_id')) if record.get('manufacturer_id') in options else 0,
        format_func=lambda value: '请选择厂家' if value is None else manufacturers[value],
        disabled=edit, key=f'{prefix}_manufacturer')
    if locked:
        st.info('此质控品已登记批号或用于项目，名称、货号等资料不能直接修改，可补充备注。')
    labels = {'generic_name': '质控品名称 *', 'trade_name': '商品名称', 'matrix': '基质',
              'physical_form': '物理形态', 'catalog_no': '产品货号', 'registration_no': '注册证 / 备案编号'}
    values = {}
    columns = st.columns(2)
    for position, (key, label) in enumerate(labels.items()):
        values[key] = columns[position % 2].text_input(label, value=record.get(key, ''),
            max_chars=200, disabled=locked, key=f'{prefix}_{key}')
    values['notes'] = st.text_area('备注', value=record.get('notes', ''), key=f'{prefix}_notes')
    dirty = any(values[k] != record.get(k, '') for k in values) or (not edit and manufacturer is not None)
    if _cancel_controls(prefix, dirty=dirty):
        return
    if st.button('保存修改' if edit else '保存', key=f'{prefix}_save', type='primary', width='stretch'):
        try:
            if edit:
                update_material_product(state['product_id'], expected_version=record['edit_version'], **values)
                product_id = state['product_id']
            else:
                if manufacturer is None:
                    raise ValueError('请选择厂家；未登记的厂家可先在基础资料中新增。')
                product_id = create_qc_material(manufacturer_id=manufacturer, **values)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _close_dialog(notice='质控品资料已保存。', product_id=product_id)


def _status_confirmation(state: dict) -> None:
    record = state['record']
    product_mode = state['kind'].startswith('product_')
    lot_mode = state['kind'] == 'lot_status'
    disabled = False if lot_mode else not bool(record['is_disabled'])
    verb = '停用' if disabled else '恢复'
    prefix = f'material_status_{state["nonce"]}'
    label = record['generic_name'] if product_mode else material_label(record)
    st.markdown(f'**{label}**')
    if disabled:
        st.warning('停用后不能再选择使用，已保存的检测记录、均值和标准差及报告保留。')
    else:
        st.info('恢复后可重新选择使用，已有检测记录不变。')
    if lot_mode:
        st.caption('恢复批号后，可以重新选择该批号下未单独停用的浓度水平。')
    if not product_mode:
        st.caption(f'用于批次设置：{record["config_count"]} 项；检测记录：{record["result_count"]} 条。')
    reason = st.text_area('停用原因 *', key=f'{prefix}_reason') if disabled else ''
    if st.button('取消', key=f'{prefix}_cancel', width='stretch'):
        _close_dialog()
    if st.button(f'确认{verb}', key=f'{prefix}_confirm', width='stretch'):
        try:
            if lot_mode:
                restore_control_material_lot(state['level_id'], expected_version=record['edit_version'])
            elif product_mode:
                set_material_product_disabled(state['product_id'], is_disabled=disabled, reason=reason, expected_version=record['edit_version'])
            else:
                set_control_material_disabled(state['level_id'], is_disabled=disabled, reason=reason, expected_version=record['edit_version'])
        except ValueError as exc:
            st.error(str(exc))
        else:
            if disabled:
                st.session_state['material_catalog_pending_show_disabled'] = True
            _close_dialog(notice=f'已{verb}，历史记录保留。', selected_id=state['level_id'],
                          product_id=state['product_id'] if product_mode else None)


def _render_material_dialog() -> None:
    state = st.session_state.get('material_dialog')
    if not state:
        return
    kind = state['kind']
    if kind in ('relationships', 'batches'):
        _render_product_manager(state)
    elif kind == 'coverage_picker':
        from ui.product_directory import render_coverage_picker
        render_coverage_picker(state)
    elif kind.endswith('_status'):
        st.subheader('恢复确认' if kind == 'lot_status' or state['record']['is_disabled'] else '停用确认')
        _status_confirmation(state)
    elif kind.startswith('product_'):
        st.subheader('编辑质控品' if state['product_id'] else '新增质控品')
        _product_editor(state)
    else:
        st.subheader('编辑质控品批号' if state['level_id'] else '新增质控品批号')
        _material_editor(state)


def _render_usage_navigation(product_id: int, *, include_batches: bool = True) -> None:
    from services.product_directory_service import get_product_relationships
    from services.project_config_service import list_lot_configs
    from services.material_workflow_service import config_material_summary

    usage = get_product_relationships(product_id)['usage']
    st.subheader('关联项目与批次' if include_batches else '使用此质控品的项目')
    if not usage:
        st.info('尚无项目使用此质控品。建立项目时选择此产品，再添加检验项目和实际批次。')
        return
    projects = {int(row['template_id']): row for row in usage}
    project_key = f'material_usage_project_{product_id}'
    if st.session_state.get(project_key) not in projects:
        st.session_state[project_key] = next(iter(projects))
    template_id = st.selectbox('使用此质控品的项目', list(projects), key=project_key,
        format_func=lambda value: '｜'.join([projects[value]['instrument_name'] or '未填写仪器',
            projects[value]['template_name']]) + ('（已停用）' if projects[value]['template_disabled'] else ''))
    if st.button('打开关联项目', key=f'material_usage_open_project_{product_id}'):
        from ui.daily_navigation import return_to_project_workspace
        st.session_state.pop('material_dialog', None)
        return_to_project_workspace(template_id)
    if not include_batches:
        return
    configs = {int(row['id']): row for row in list_lot_configs(template_id, include_disabled=True).to_dict('records')}
    if not configs:
        st.caption('此项目尚未建立实际批次。可打开项目，进入批次管理登记。')
        return
    config_key = f'material_usage_config_{product_id}_{template_id}'
    if st.session_state.get(config_key) not in configs:
        st.session_state[config_key] = next(iter(configs))
    summaries = {identifier: config_material_summary(identifier) for identifier in configs}
    config_id = st.selectbox('项目中的实际批次', list(configs), key=config_key,
        format_func=lambda value: configs[value]['config_name'] + '｜' + summaries[value] +
            ('（已停用）' if configs[value]['is_disabled'] else ''))
    view, replace = st.columns(2)
    if view.button('查看批次设置', key=f'material_usage_open_config_{product_id}', width='stretch'):
        from ui.project_navigation import open_project_setup
        st.session_state.pop('material_dialog', None)
        open_project_setup(template_id, config_id=config_id)
    if replace.button('更换此质控品批次', key=f'material_usage_replace_{product_id}', width='stretch',
            disabled=bool(projects[template_id]['template_disabled'] or configs[config_id]['is_disabled'])):
        from ui.common import open_global_page
        from ui.qc_replacement_workspace import open_qc_replacement_dialog
        st.session_state.pop('v11_pending_existing_config_id', None)
        st.session_state.pop('v11_pending_copied_config_id', None)
        st.session_state['v11_management_tabs'] = '更换质控品批次'
        st.session_state['qc_replace_project'] = int(template_id)
        st.session_state['qc_replace_search'] = ''
        st.session_state['qc_replace_selected_config'] = int(config_id)
        st.session_state.pop('material_dialog', None)
        open_global_page('show_project_management_page', rerun=False)
        open_qc_replacement_dialog(config_id)


def render_material_catalog(*, render_dialog: bool = True, include_directory: bool = False) -> None:
    from ui.product_directory import render_directory_import, render_product_relationships
    notice = st.session_state.pop('material_catalog_notice', '')
    if notice:
        st.success(notice)
    if include_directory:
        render_directory_import()
    pending = st.session_state.pop('material_catalog_pending_product', None)
    if pending is not None and get_material_product(pending)['is_disabled']:
        st.session_state['material_catalog_pending_show_disabled'] = True
    if st.session_state.pop('material_catalog_pending_show_disabled', False):
        st.session_state['md_show_disabled_qc'] = True
    show_disabled = st.checkbox('显示已停用记录', key='md_show_disabled_qc')
    products = {int(r['id']): r for r in list_qc_materials(include_disabled=show_disabled).to_dict('records')}
    if pending in products:
        previous = st.session_state.get('material_catalog_product_id', st.session_state.get('material_catalog_saved_product_id'))
        if pending != previous:
            st.session_state['material_catalog_search'] = ''
            st.session_state.pop('material_catalog_reveal_id', None)
            st.session_state.pop('material_catalog_reveal_query', None)
        st.session_state['material_catalog_product_id'] = pending
    if 'material_catalog_product_id' not in st.session_state:
        st.session_state['material_catalog_product_id'] = st.session_state.get('material_catalog_saved_product_id')
    if st.session_state.get('material_catalog_product_id') not in products:
        st.session_state['material_catalog_product_id'] = next(iter(products), None)
    choose, add = st.columns([0.75, 0.25], vertical_alignment='bottom')
    product_id = choose.selectbox('质控品', [None] + list(products),
        format_func=lambda value: '请选择质控品' if value is None else
            f'{products[value]["manufacturer_name"] or "未填写厂家"}｜{products[value]["catalog_no"] or "未填写产品编号"}｜{products[value]["generic_name"]}' +
            ('（已停用）' if products[value]['is_disabled'] else ''), key='material_catalog_product_id')
    st.session_state['material_catalog_saved_product_id'] = product_id
    if add.button('新增质控品', key='material_catalog_add_product', width='stretch'):
        _open_dialog('product_new')
    if product_id is None:
        st.info('请先选择或新增质控品，再登记本次使用的浓度水平、实际批号和效期。')
    else:
        product = get_material_product(product_id)
        product_info, product_edit, product_status = st.columns([0.5, 0.25, 0.25], vertical_alignment='bottom')
        product_info.caption(f'基质：{product["matrix"] or "未填写"}｜产品货号：{product["catalog_no"] or "未填写"}')
        if product_edit.button('编辑质控品', key='material_catalog_edit_product', disabled=bool(product['is_disabled']), width='stretch'):
            _open_dialog('product_edit', product_id=product_id)
        if product_status.button('恢复质控品' if product['is_disabled'] else '停用质控品', key='material_catalog_status_product', width='stretch'):
            _open_dialog('product_status', product_id=product_id)
        render_product_relationships(product_id)
        st.divider()
        _render_product_batches(product_id, product, show_disabled)
        st.divider()
        _render_usage_navigation(product_id)
    if render_dialog:
        render_pending_material_dialog()


def render_pending_material_dialog() -> None:
    state = st.session_state.get('material_dialog')
    if state:
        title = {'relationships': '查看／管理关联项目', 'batches': '管理批次',
                 'coverage_picker': '选择检验项目'}.get(state['kind'], '质控品资料')
        st.dialog(title, width='large', dismissible=False)(_render_material_dialog)()


def _render_product_batches(product_id, product, show_disabled):
    st.subheader('实际批号与浓度水平')
    st.caption('产品名录中的浓度文字供核对；请按本次采购资料登记实际批号、效期及各水平，不作为均值或标准差。')
    manager = st.session_state.get('material_dialog', {})
    if manager.get('kind') == 'batches' and 'material_catalog_search' not in st.session_state:
        st.session_state['material_catalog_search'] = manager.get('search', '')
    query, add_material = st.columns([0.75, 0.25], vertical_alignment='bottom')
    search = query.text_input('搜索浓度水平、浓度编号或批号', key='material_catalog_search', help=SEARCH_HELP)
    if manager.get('kind') == 'batches':
        manager['search'] = search
    if add_material.button('新增质控品批号', key='material_catalog_add', type='primary', disabled=bool(product['is_disabled']), width='stretch'):
        _open_dialog('material_new', product_id=product_id)
    materials = list_control_materials(product_id, include_disabled=show_disabled)
    if search != st.session_state.get('material_catalog_reveal_query'):
        st.session_state.pop('material_catalog_reveal_id', None)
    reveal_id = st.session_state.get('material_catalog_reveal_id')
    if search.strip() and not materials.empty:
        matches = materials[['level_name', 'level_code', 'lot_no']].fillna('').apply(lambda row: fuzzy_match(search, *row), axis=1)
        reveal = materials['id'].eq(reveal_id)
        if (reveal & ~matches).any():
            st.caption('下方已显示刚保存的记录，原搜索条件保留。')
        materials = materials[matches | reveal]
    ids = materials['id'].astype(int).tolist()
    selected_id = st.session_state.get('material_catalog_selected_id')
    if selected_id not in ids:
        selected_id = ids[0] if ids else None
        st.session_state['material_catalog_selected_id'] = selected_id
    if materials.empty:
        st.info('未找到符合条件的记录，请添加质控品批号或调整搜索条件。')
    else:
        display = materials[['level_name', 'level_code', 'lot_no', 'expiry_date']].rename(columns={
            'level_name': '浓度水平', 'level_code': '浓度编号', 'lot_no': '批号', 'expiry_date': '效期'})
        display['状态'] = materials.apply(lambda r: '已停用' if r['is_disabled'] else '质控品或批号已停用' if r['product_disabled'] or r['lot_disabled'] else '启用', axis=1)
        fingerprint = hashlib.sha1(str((product_id, ids, search, show_disabled)).encode()).hexdigest()[:12]
        table_key = f'material_catalog_table_{fingerprint}_{st.session_state.get("material_catalog_table_nonce", 0)}'
        event = st.dataframe(display, hide_index=True, width='stretch', key=table_key,
            on_select='rerun', selection_mode='single-row',
            selection_default={'selection': {'rows': [ids.index(selected_id)]}} if selected_id in ids else None)
        rows = event.selection.rows
        if rows and 0 <= rows[0] < len(ids):
            selected_id = ids[rows[0]]
            st.session_state['material_catalog_selected_id'] = selected_id
        if selected_id is not None:
            selected = get_control_material(selected_id)
            detail, edit_col, status_col = st.columns([0.6, 0.2, 0.2], vertical_alignment='bottom')
            detail.markdown(f'**当前选择：{material_label(selected)}**')
            detail.caption(f'货号：{selected["specification_catalog_no"] or "未填写"}｜用于批次设置：{selected["config_count"]} 项')
            if selected['concentration_label']:
                detail.write(selected['concentration_label'])
            blocked = bool(selected['is_disabled'] or selected['lot_disabled'] or selected['product_disabled'])
            if edit_col.button('编辑', key='material_catalog_edit', disabled=blocked, width='stretch'):
                _open_dialog('material_edit', product_id=product_id, level_id=selected_id)
            if status_col.button('恢复' if selected['is_disabled'] else '停用', key='material_catalog_status', width='stretch'):
                _open_dialog('material_status', product_id=product_id, level_id=selected_id)
            if selected['is_disabled']:
                st.caption(f'停用原因：{selected["disabled_reason"] or "未填写"}')
            if selected['lot_disabled']:
                st.warning('此批号已停用。请先恢复批号，再选择需要使用的浓度水平。')
                if st.button('恢复批号', key='material_catalog_restore_lot'):
                    _open_dialog('lot_status', product_id=product_id, level_id=selected_id)
    st.caption('浓度编号按厂家标识填写，没有编号时可留空；不同浓度水平可使用各自的实际批号。')



def _render_product_manager(state):
    from ui.product_directory import render_product_relationships

    product_id = state['product_id']
    product = get_material_product(product_id)
    st.markdown('**质控品：' + product['generic_name'] + '**')
    st.caption('产品货号：' + (product['catalog_no'] or '未填写'))
    notice = st.session_state.pop('material_catalog_notice', '')
    if notice:
        st.success(notice)
    if state['kind'] == 'relationships':
        render_product_relationships(product_id)
        draft = st.session_state.get('product_coverage_drafts', {}).get(str(product_id), {})
        dirty = (draft.get('selected') != draft.get('initial') or
                 bool(draft.get('actor') or draft.get('evidence')))
        if not dirty:
            _render_usage_navigation(product_id, include_batches=False)
        else:
            st.caption('保存或放弃本次修改后，可打开使用此质控品的项目。')
    else:
        dirty = False
        if 'material_batch_show_disabled' not in st.session_state:
            st.session_state['material_batch_show_disabled'] = state.get('show_disabled', False)
        if st.session_state.pop('material_catalog_pending_show_disabled', False):
            st.session_state['material_batch_show_disabled'] = True
        state['show_disabled'] = st.checkbox('显示已停用批号和水平', key='material_batch_show_disabled')
        _render_product_batches(product_id, product, state['show_disabled'])
        st.divider()
        _render_usage_navigation(product_id)
    if not dirty:
        state['confirm_close'] = False
    if st.button('关闭', key='material_manager_close', width='stretch'):
        if dirty:
            state['confirm_close'] = True
        else:
            _close_product_manager(state)
    if state.get('confirm_close'):
        st.warning('修改尚未保存，是否放弃并关闭？')
        keep, discard = st.columns(2)
        if keep.button('继续编辑', key='material_manager_keep', width='stretch'):
            state['confirm_close'] = False
            st.rerun()
        if discard.button('放弃修改并关闭', key='material_manager_discard', width='stretch'):
            _close_product_manager(state)


def _close_product_manager(state):
    st.session_state.get('product_coverage_drafts', {}).pop(str(state['product_id']), None)
    st.session_state.pop('material_dialog', None)
    st.rerun()
