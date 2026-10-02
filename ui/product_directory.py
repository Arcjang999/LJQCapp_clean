"""Product source preview and explicit coverage inside the existing material page."""
import hashlib
import json
from functools import partial

import pandas as pd
import streamlit as st

from services.master_data_service import create_manufacturer, list_manufacturers, list_test_items
from services.search_service import SEARCH_HELP
from services.product_directory_service import (PRODUCT_LABELS, load_bondson_directory_package,
    preview_product_directory, publish_product_directory, list_product_directory_releases,
    list_directory_products, get_product_relationships, save_product_coverage,
    export_material_catalog_context)


def _directory_source(uploaded, manufacturer_id):
    if uploaded is not None:
        content = uploaded.getvalue()
        source = ('upload', uploaded.name)
    else:
        content = json.dumps(load_bondson_directory_package(), ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode('utf-8')
        source = ('bundled', 'bondson_2026_09_28.json')
    return content, (manufacturer_id, *source, hashlib.sha256(content).hexdigest())


def _product_table(records, *, include_excluded=False):
    columns = (['manufacturer_name'] if 'manufacturer_name' in records else []) + [*PRODUCT_LABELS]
    if include_excluded:
        columns.append('excluded_reason')
    return records.reindex(columns=columns).rename(
        columns={'manufacturer_name': '厂商', **PRODUCT_LABELS, 'excluded_reason': '排除原因'})


def _render_published_directory():
    releases = list_product_directory_releases()
    if releases.empty:
        return
    by_id = {int(row['id']): row for row in releases.to_dict('records')}
    chosen = st.selectbox('已发布目录版本', list(by_id),
        format_func=lambda value: by_id[value]['publisher'] + '｜' + by_id[value]['version_label'],
        key='directory_release')
    query = st.text_input('搜索目录产品', key='directory_search')
    show_excluded = st.checkbox('包含排除记录', key='directory_show_excluded')
    records = list_directory_products(chosen, include_excluded=show_excluded, query=query)
    view = _product_table(records, include_excluded=show_excluded)
    st.dataframe(view, hide_index=True, width='stretch')
    st.download_button('导出当前目录清单', view.to_csv(index=False).encode('utf-8-sig'),
        file_name='产品目录.csv', mime='text/csv', key='directory_export')


def _select_catalogue_product(table_key, product_ids):
    rows = st.session_state.get(table_key, {}).get('selection', {}).get('rows', [])
    st.session_state['product_catalogue_selected_id'] = (
        product_ids[rows[0]] if rows and 0 <= rows[0] < len(product_ids) else None)


def render_product_catalogue():
    from services.product_directory_service import list_managed_material_products
    from services.material_catalog_service import get_material_product
    from ui.material_catalog import _open_dialog

    pending = st.session_state.pop('material_catalog_pending_product', None)
    if pending is not None:
        st.session_state['product_catalogue_selected_id'] = pending
        st.session_state['product_catalogue_reveal_id'] = pending
        st.session_state['product_catalogue_reveal_filters'] = (
            st.session_state.get('product_catalogue_search', ''),
            st.session_state.get('product_catalogue_manufacturer'))
    if (st.session_state.get('material_dialog', {}).get('kind') != 'batches'
            and st.session_state.pop('material_catalog_pending_show_disabled', False)):
        st.session_state['product_catalogue_show_disabled'] = True
    if not st.session_state.get('material_dialog'):
        notice = st.session_state.pop('material_catalog_notice', '')
        if notice:
            st.success(notice)
    search, manufacturer_filter, status_filter = st.columns([2, 1, 1], vertical_alignment='bottom')
    query = search.text_input('查找产品', placeholder='输入厂商、产品编号、名称、浓度或浓度编号',
        key='product_catalogue_search')
    all_products = list_managed_material_products(include_disabled=True)
    manufacturers = sorted(all_products['manufacturer_name'].dropna().unique().tolist())
    manufacturer = manufacturer_filter.selectbox('厂商', [None, *manufacturers],
        format_func=lambda value: '全部厂商' if value is None else value,
        key='product_catalogue_manufacturer')
    show_disabled = status_filter.checkbox('显示已停用质控品', key='product_catalogue_show_disabled')
    products = list_managed_material_products(query=query, include_disabled=show_disabled)
    if manufacturer is not None:
        products = products.loc[products['manufacturer_name'] == manufacturer]
    if (query, manufacturer) != st.session_state.get('product_catalogue_reveal_filters'):
        st.session_state.pop('product_catalogue_reveal_id', None)
    reveal_id = st.session_state.get('product_catalogue_reveal_id')
    if reveal_id is not None and reveal_id not in products['product_id'].tolist():
        reveal = all_products.loc[all_products['product_id'] == reveal_id]
        if not reveal.empty and (show_disabled or not reveal.iloc[0]['is_disabled']):
            products = pd.concat([reveal, products], ignore_index=True)
            st.caption('已显示刚保存的质控品，原查找条件保留。')
    st.caption(f'当前显示 {len(products)} 项。单选一个产品后，使用下方按钮管理。')
    view = _product_table(products)
    view['状态'] = products['is_disabled'].map({0: '启用', 1: '已停用'})
    product_ids = products['product_id'].astype(int).tolist()
    selected_id = st.session_state.get('product_catalogue_selected_id')
    if selected_id not in product_ids:
        selected_id = None
        st.session_state['product_catalogue_selected_id'] = None
    fingerprint = hashlib.sha256(repr((query, manufacturer, show_disabled, product_ids)).encode()).hexdigest()[:12]
    table_key = f'product_catalogue_table_{fingerprint}_{st.session_state.get("product_catalogue_table_nonce", 0)}'
    st.dataframe(view, hide_index=True, width='stretch', key=table_key,
        selection_mode='single-row',
        selection_default={'selection': {'rows': [product_ids.index(selected_id)] if selected_id else []}},
        on_select=partial(_select_catalogue_product, table_key, product_ids))
    if products.empty:
        st.info('没有找到符合条件的质控品，请调整查找条件，或新增质控品。')
    product = get_material_product(selected_id) if selected_id is not None else None
    if product:
        st.markdown('**当前选择：' + product['generic_name'] + '**')
        st.caption(f'产品货号：{product["catalog_no"] or "未填写"}｜基质：{product["matrix"] or "未填写"}')
    else:
        st.caption('请在名录中单选一款质控品。')
    add, edit, status = st.columns(3)
    if add.button('新增质控品', key='material_catalog_add_product', width='stretch'):
        _open_dialog('product_new')
    if edit.button('编辑质控品', key='material_catalog_edit_product',
            disabled=not product or bool(product['is_disabled']), width='stretch'):
        _open_dialog('product_edit', product_id=selected_id)
    if status.button('恢复质控品' if product and product['is_disabled'] else '停用质控品',
            key='material_catalog_status_product', disabled=not product, width='stretch'):
        _open_dialog('product_status', product_id=selected_id)
    projects, batches = st.columns(2)
    if projects.button('查看／管理关联项目', key='product_catalogue_relationships', disabled=not product, width='stretch'):
        _open_dialog('relationships', product_id=selected_id)
    if batches.button('管理批次', key='product_catalogue_batches', disabled=not product, width='stretch'):
        _open_dialog('batches', product_id=selected_id)
    st.download_button('导出产品清单', view.to_csv(index=False).encode('utf-8-sig'),
        file_name='质控品名录.csv', mime='text/csv', key='product_catalogue_export')


def render_directory_import(*, show_published=True, expander_label='产品目录与来源'):
    with st.expander(expander_label):
        st.caption('请核对产品编号、产品名称、浓度和浓度编号；浓度与浓度编号任一项有明确内容即可，阴性、分型或位点按原文保留。选择产品后，再登记实际批号，并逐项选择质控方法；多个水平不自动决定方法。')
        manufacturers = list_manufacturers(category='qc_material')
        rows = {int(row['id']):row['display_name'] for row in manufacturers.to_dict('records')}
        if '邦德盛' not in rows.values() and st.button('登记邦德盛厂家', key='directory_register_bondson'):
            try:
                create_manufacturer(display_name='邦德盛',categories=['qc_material'])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        manufacturer_id = st.selectbox('目录所属质控品厂家',[None,*rows],format_func=lambda value:'请选择厂家' if value is None else rows[value],key='directory_manufacturer')
        uploaded = st.file_uploader('更新目录资料包（可选）',type=['json'],key='directory_upload')
        st.caption('请选择质控品厂家，再点击“预览目录”。未上传更新文件时，预览现有邦德盛目录的1054项产品和252项排除记录。')
        source_content, source_identity = _directory_source(uploaded, manufacturer_id)
        draft = st.session_state.get('directory_preview_draft')
        if draft and draft.get('source_identity') != source_identity:
            draft['source_changed'] = True
        if st.button('预览目录',key='directory_preview'):
            try:
                if manufacturer_id is None:
                    raise ValueError('请选择目录所属的质控品厂家。')
                package = json.loads(source_content)
                preview = preview_product_directory(package,manufacturer_id)
                st.session_state['directory_preview_draft'] = dict(package=package,preview=preview,
                    manufacturer_id=manufacturer_id, source_identity=source_identity, source_changed=False)
            except (ValueError,UnicodeError) as exc:
                st.error(str(exc) if not isinstance(exc,json.JSONDecodeError) else '无法读取目录资料包，请核对文件格式。')
        draft = st.session_state.get('directory_preview_draft')
        if draft:
            package,preview = draft['package'],draft['preview']
            if draft.get('source_changed'):
                st.warning('目录厂家或来源资料已改变，请重新预览后再发布。以下仍为上次预览内容。')
            st.write(f"来源：{package['source_file']}｜版本：{package['version_label']}｜取得日期：{package['obtained_at']}")
            st.caption('所属厂家：' + rows.get(draft['manufacturer_id'], '厂家已不可选'))
            st.caption(f"核查人：{package['verified_by']}｜保留{preview['included_count']}项，排除{preview['excluded_count']}项")
            if preview['is_test']:
                st.warning('这是供试用的目录资料。确认后可查看来源和发布记录，其中产品不能用于新建项目或批次。')
            display = pd.DataFrame(preview['records'])
            st.dataframe(display[['action',*PRODUCT_LABELS,'excluded_reason']].rename(columns={**PRODUCT_LABELS,'action':'本次处理','excluded_reason':'排除原因'}),hide_index=True,width='stretch')
            if preview['issues']:
                st.error('目录有需要核对的资料，尚不能发布。')
                st.dataframe(pd.DataFrame(preview['issues']).rename(columns={'row':'原行号','product_code':'产品编号','field':'资料','message':'需要核对'}),hide_index=True,width='stretch')
            if preview['retired_codes']:
                st.caption(f"本次目录不再收录{len(preview['retired_codes'])}个既有编号，将停止未来选择，已有批号和历史保留。")
            actor = st.text_input('本次目录核对人',key='directory_publish_actor')
            save,cancel = st.columns(2)
            if save.button('确认发布目录',key='directory_publish',type='primary',disabled=bool(preview['issues']) or bool(draft.get('source_changed'))):
                try:
                    result=publish_product_directory(package,draft['manufacturer_id'],expected_preview_hash=preview['preview_hash'],published_by=actor)
                    st.session_state.pop('directory_preview_draft',None)
                    st.session_state['material_catalog_notice']='此来源版本已发布，产品没有重复添加。' if result['reused'] else '产品目录已发布，实际批号和检测方法请分别登记与配置。'
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if cancel.button('取消目录预览',key='directory_cancel'):
                st.session_state.pop('directory_preview_draft',None)
                st.rerun()
        if show_published:
            _render_published_directory()


def _picker_selection(nonce, table_key, visible_ids):
    state = st.session_state.get('material_dialog')
    if not state or state.get('kind') != 'coverage_picker' or state['nonce'] != nonce:
        return
    rows = st.session_state.get(table_key, {}).get('selection', {}).get('rows', [])
    selected = [visible_ids[index] for index in rows if 0 <= index < len(visible_ids)]
    state['selected'] = [identifier for identifier in state['selected'] if identifier not in visible_ids] + selected


def _remove_picker_selection(nonce, identifier=None):
    state = st.session_state.get('material_dialog')
    if not state or state.get('kind') != 'coverage_picker' or state['nonce'] != nonce:
        return
    state['selected'] = [] if identifier is None else [value for value in state['selected'] if value != identifier]
    state['table_generation'] = state.get('table_generation', 0) + 1


def _test_item_label(record):
    return record['chinese_name'] + ('｜' + record['standard_code'] if record.get('standard_code') else '')


def render_coverage_picker(state):
    from ui.material_catalog import _close_dialog
    from services.material_catalog_service import get_material_product

    st.subheader('选择检验项目')
    st.caption('质控品：' + get_material_product(state['product_id'])['generic_name'])
    prefix = f'coverage_picker_{state["nonce"]}'
    query = st.text_input('查找检验项目', placeholder='输入项目名称、编号或别名',
                         help=SEARCH_HELP, key=prefix + '_search')
    candidates = list_test_items(query=query)
    visible_ids = candidates.id.astype(int).tolist()
    identity = (query, tuple(visible_ids))
    if state.get('table_identity') != identity:
        state['table_identity'] = identity
        state['table_generation'] = state.get('table_generation', 0) + 1
    table_key = prefix + '_table_' + str(state['table_generation'])
    st.caption(f'找到 {len(candidates)} 项，已选 {len(state["selected"])} 项。可更换关键词继续勾选，已选项目会保留。')
    if candidates.empty:
        st.info('没有找到符合条件的检验项目，请调整关键词。')
    else:
        view = candidates[['chinese_name', 'standard_code', 'abbreviation']].fillna('').rename(
            columns={'chinese_name': '检验项目', 'standard_code': '编号', 'abbreviation': '常用缩写'})
        st.dataframe(view, hide_index=True, width='stretch', height=min(360, max(115, 36 * len(view) + 38)),
            key=table_key, selection_mode='multi-row',
            selection_default={'selection': {'rows': [index for index, identifier in enumerate(visible_ids)
                                                       if identifier in state['selected']]}},
            on_select=partial(_picker_selection, state['nonce'], table_key, visible_ids))
    records = {int(row['id']): row for row in list_test_items(include_disabled=True).to_dict('records')}
    invalid = [identifier for identifier in state['selected'] if identifier not in records or records[identifier]['is_disabled']]
    if invalid:
        st.warning('已选项目中有已停用或无法查询的项目，请在下方移除后重新选择。')
    if state['selected']:
        with st.expander(f'已选检验项目（{len(state["selected"])}项，可移除）'):
            for identifier in state['selected']:
                row = records.get(identifier)
                label = _test_item_label(row) if row else '无法查询的检验项目'
                if row and row['is_disabled']:
                    label += '（已停用）'
                st.button('移除：' + label, key=prefix + '_remove_' + str(identifier),
                    on_click=_remove_picker_selection, args=(state['nonce'], identifier))
            st.button('清空已选', key=prefix + '_clear', on_click=_remove_picker_selection, args=(state['nonce'],))
    cancel, confirm = st.columns(2)
    if cancel.button('取消', key=prefix + '_cancel', width='stretch'):
        _close_dialog()
    if confirm.button('确认选择', key=prefix + '_confirm', type='primary', width='stretch'):
        draft = st.session_state.get('product_coverage_drafts', {}).get(str(state['product_id']))
        if draft is None or draft['version'] != state['coverage_version']:
            st.error('本页选择已变化，请取消后重新打开选择窗口。')
        elif invalid:
            st.error('请先移除已停用或无法查询的项目。')
        else:
            draft['selected'] = list(state['selected'])
            _close_dialog(product_id=state['product_id'])


def render_product_relationships(product_id):
    relationship=get_product_relationships(product_id)
    catalogue=export_material_catalog_context(product_id)
    if catalogue['product'].get('source_code'):
        st.dataframe(pd.DataFrame([{PRODUCT_LABELS[key]:catalogue['product'][key] for key in PRODUCT_LABELS}]),hide_index=True,width='stretch')
    with st.expander('适用检验项目', expanded=True):
        active=[row for row in relationship['coverage'] if not row['is_disabled']]
        st.caption('按产品说明书选择这款质控品可用于哪些检验项目。保存后，配置项目时可以直接带入。')
        drafts=st.session_state.setdefault('product_coverage_drafts',{})
        key=str(product_id)
        if key not in drafts:
            drafts[key]=dict(version=relationship['edit_version'],selected=[row['test_item_id'] for row in active],initial=[row['test_item_id'] for row in active],discard=False,actor='',evidence='')
        draft=drafts[key]
        generation = st.session_state.get('product_coverage_form_generation', {}).get(key, 0)
        prefix=f'coverage_{product_id}_'+draft['version'][:10]+f'_{generation}'
        selected=list(draft['selected'])
        records={int(row['id']):row for row in list_test_items(include_disabled=True).to_dict('records')}
        previous={row['test_item_id']:row for row in active}
        if selected:
            st.caption(f'已选择 {len(selected)} 项。修改后请点击下方“保存适用项目”。')
            view=[]
            for identifier in selected:
                row=records.get(identifier)
                saved=previous.get(identifier,{})
                view.append({'检验项目':(row['chinese_name']+('（已停用）' if row['is_disabled'] else '')) if row else '无法查询的检验项目',
                    '编号':row['standard_code'] if row else '', '适用方法学':saved.get('method_name') or '不限',
                    '参考资料':saved.get('evidence',''), '核对人':saved.get('confirmed_by','')})
            st.dataframe(pd.DataFrame(view),hide_index=True,width='stretch')
        else:
            st.caption('尚未选择适用检验项目。')
        if st.button('选择检验项目',key=prefix+'_choose'):
            from ui.material_catalog import _open_dialog
            draft['actor'] = st.session_state.get(prefix+'_actor', draft.get('actor', ''))
            draft['evidence'] = st.session_state.get(prefix+'_evidence', draft.get('evidence', ''))
            _open_dialog('coverage_picker',product_id=product_id)
            st.session_state['material_dialog'].update(selected=list(selected),coverage_version=draft['version'])
            if st.session_state['material_dialog'].get('return_to'):
                st.rerun()
        actor=st.text_input('核对人',value=draft.get('actor',''),placeholder='填写姓名',key=prefix+'_actor')
        draft['actor']=actor
        evidence=st.text_area('参考资料',value=draft.get('evidence',''),placeholder='例如：产品说明书名称及页码，或厂商提供的说明',key=prefix+'_evidence')
        draft['evidence']=evidence
        st.caption('请填写核对人和参考资料。质控方法、单位和质量要求在具体项目中设置。')
        save,cancel=st.columns(2)
        if save.button('保存适用项目',key=prefix+'_save'):
            try:
                previous={row['test_item_id']:row for row in active}
                save_product_coverage(product_id,[previous.get(identifier,identifier) for identifier in selected],
                    expected_version=draft['version'],confirmed_by=actor,evidence=evidence)
                drafts.pop(key,None)
                st.session_state.setdefault('product_coverage_form_generation', {})[key] = generation + 1
                st.session_state['material_catalog_notice']='适用检验项目已保存。'
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if cancel.button('取消修改',key=prefix+'_cancel'):
            if selected!=draft['initial'] or actor or evidence:
                draft['discard']=True
            else:
                drafts.pop(key,None)
                st.rerun()
        if draft['discard']:
            st.warning('本次修改尚未保存。')
            keep,discard=st.columns(2)
            if keep.button('继续填写',key=prefix+'_keep'):
                draft['discard']=False
                st.rerun()
            if discard.button('放弃修改',key=prefix+'_discard'):
                for state_key in list(st.session_state):
                    if state_key.startswith(prefix):st.session_state.pop(state_key,None)
                drafts.pop(key,None)
                st.rerun()
