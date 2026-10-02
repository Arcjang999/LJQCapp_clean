"""Project and test-item dialogs with isolated drafts and explicit save boundaries."""
from __future__ import annotations

from uuid import uuid4
import pandas as pd
import streamlit as st

from services.project_config_service import get_project_template, list_template_items, INPUT_VALUE_TYPE_LABELS
from services.project_workspace_service import (
    save_project_details, save_single_test_item, change_project_status,
    remove_test_item, project_identity_locked,
)

MODAL_KEY = 'project_workspace_dialog'


def _plain(row):
    return {k: None if v is None or (not isinstance(v, (dict, list)) and pd.isna(v)) else v
            for k, v in dict(row).items()}


def open_project_dialog(kind, template_id=None, item_id=None):
    # Historical callers must also enter the editor; no single-step status path.
    if kind in ('disable', 'restore'):
        kind = 'project'
    template = _plain(get_project_template(template_id)) if template_id is not None else {}
    if kind == 'panel':
        draft = {'rows': [], 'stage': 'source'}
    elif kind in ('item', 'remove_item', 'quality'):
        items = list_template_items(template_id)
        matches = items[items.id == item_id] if item_id is not None else pd.DataFrame()
        draft = _plain(matches.iloc[0]) if not matches.empty else {
            'test_item_id': None, 'qc_method': template['default_qc_method'], 'input_value_type': 'raw',
            'unit_id': None, 'method_id': template['default_method_id'],
            'reagent_id': template['default_reagent_id'], 'level_count': template['default_level_count'],
            'target_n': 20, 'cv_limit': None, 'quality_target_source_text': '', 'notes': '',
        }
    else:
        draft = template or {'template_name': '', 'lab_instrument_id': None, 'qc_material_id': None,
            'default_reagent_id': None, 'default_qc_method': 'lj', 'default_method_id': None,
            'default_level_count': 1, 'project_group': '', 'notes': ''}
    st.session_state[MODAL_KEY] = dict(kind=kind, template_id=template_id, item_id=item_id,
        revision=template.get('revision_no'), draft=dict(draft), initial=dict(draft), token=uuid4().hex,
        discard=False, template_name=template.get('template_name', ''))
    st.rerun()


def _finish(message=''):
    st.session_state.pop(MODAL_KEY, None)
    if message:
        st.session_state['project_workspace_notice'] = message
    st.session_state['project_table_version'] = st.session_state.get('project_table_version', 0) + 1
    st.rerun(scope='app')


def _key(ctx, field):
    key = 'project_draft_' + ctx['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = ctx['draft'].get(field)
    return key


def _text(ctx, field, label, *, multiline=False, **kwargs):
    ctx['draft'][field] = (st.text_area if multiline else st.text_input)(label, key=_key(ctx, field), **kwargs)


def _choice(ctx, field, label, frame, label_fields, *, disabled=False, optional=False):
    current = ctx['draft'].get(field)
    rows = {int(r['id']): r for r in frame.to_dict('records')
            if not r.get('is_disabled', 0) or r['id'] == current}
    if current not in rows:
        ctx['draft'][field] = None
    key = _key(ctx, field)
    if st.session_state[key] not in [None, *rows]:
        st.session_state[key] = None
    ctx['draft'][field] = st.selectbox(label, [None, *rows], key=key, disabled=disabled, filter_mode='fuzzy',
        placeholder='逐项设置' if optional else '请选择',
        format_func=lambda value: ('逐项设置' if optional else '请选择') if value is None else
        '｜'.join(str(rows[value].get(f) or '') for f in label_fields if rows[value].get(f)))


def _qc_choices(ctx, *, defaults=False):
    draft = ctx['draft']
    field = 'default_qc_method' if defaults else 'qc_method'
    count_field = 'default_level_count' if defaults else 'level_count'
    kind_key = 'project_kind_' + ctx['token']
    if kind_key not in st.session_state:
        st.session_state[kind_key] = '多水平' if draft[field] == 'zscore' else '单水平'
    kind = st.radio('默认质控方法' if defaults else '质控方法', ['单水平', '多水平'],
        format_func=lambda value: '单水平（LJ）' if value == '单水平' else '多水平法',
        horizontal=True, key=kind_key)
    if kind == '多水平':
        draft[field] = 'zscore'
        key = _key(ctx, count_field)
        if st.session_state[key] not in (2, 3):
            st.session_state[key] = 2
        draft[count_field] = st.selectbox('质控水平数', [2, 3], key=key)
    else:
        instant_key = 'project_instant_' + ctx['token']
        if instant_key not in st.session_state:
            st.session_state[instant_key] = draft[field] == 'instant'
        use_instant = st.checkbox('使用即时法', key=instant_key,
            help='质控结果较少、尚未建立均值和标准差时，可选择即时法。')
        draft[field] = 'instant' if use_instant else 'lj'
        draft[count_field] = 1
        if draft[field] == 'instant':
            st.caption('累计 3 个有效结果后开始即时法判断；达到 20 个后，可确认转入 LJ法。')
        else:
            st.caption('可用本批质控结果建立均值和标准差，也可使用经确认的均值和标准差。')


def _cancel(ctx):
    if ctx['draft'] != ctx['initial']:
        ctx['discard'] = True
        st.rerun()
    _finish()


def _render_discard(ctx):
    st.warning('本次填写尚未保存。是否放弃修改？')
    left, right = st.columns(2)
    if left.button('继续编辑', type='primary', key='project_continue_edit'):
        ctx['discard'] = False
        st.rerun()
    if right.button('放弃修改', key='project_discard_changes'):
        _finish()


def _render_catalogue_picker(ctx):
    from services.product_directory_service import list_catalog_product_choices, PRODUCT_LABELS
    if st.button('从产品名录选择', key='project_catalogue_open'):
        ctx['catalogue_open'] = not ctx.get('catalogue_open', False)
    if not ctx.get('catalogue_open'):
        return
    with st.container(border=True):
        st.caption('按产品编号、名称、浓度或浓度编号查找，核对后带入本项目。实际批号在建立批次时选择。')
        query = st.text_input('搜索名录产品', key='project_catalogue_query_' + ctx['token'])
        matches = list_catalog_product_choices(query=query)
        if matches.empty:
            st.info('没有符合条件的目录产品，请调整搜索内容；也可在质控品管理中新增其他产品。')
            return
        st.dataframe(matches[['manufacturer_name', *PRODUCT_LABELS]].rename(columns={'manufacturer_name':'厂商', **PRODUCT_LABELS}), hide_index=True,
                     width='stretch', height=210)
        choices = {int(row['product_id']): row for row in matches.to_dict('records')}
        key = 'project_catalogue_product_' + ctx['token']
        if st.session_state.get(key) not in choices:
            st.session_state[key] = None
        selected = st.selectbox('选择目录产品', [None, *choices], key=key, filter_mode='fuzzy',
            format_func=lambda value: '请选择' if value is None else '｜'.join(
                str(choices[value].get(field) or '') for field in ['manufacturer_name', *PRODUCT_LABELS]))
        if st.button('使用此目录产品', key='project_catalogue_use', disabled=selected is None):
            ctx['draft']['qc_material_id'] = selected
            st.session_state[_key(ctx, 'qc_material_id')] = selected
            ctx['catalogue_open'] = False
            st.rerun()


def _render_project_form(ctx):
    from services.master_data_service import list_lab_instruments, list_qc_materials, list_reagents, list_methods
    st.subheader('编辑项目' if ctx['template_id'] else '新建项目')
    if ctx['initial'].get('is_disabled'):
        _render_disabled_project(ctx)
        return
    locked = ctx['template_id'] is not None and project_identity_locked(ctx['template_id'])
    _text(ctx, 'template_name', '项目名称 *')
    if not locked:
        _render_catalogue_picker(ctx)
    left, right = st.columns(2)
    with left:
        _choice(ctx, 'lab_instrument_id', '仪器 *', list_lab_instruments(include_disabled=True), ['display_name'], disabled=locked)
        _choice(ctx, 'default_reagent_id', '默认试剂 *', list_reagents(include_disabled=True), ['generic_name', 'trade_name'])
    with right:
        _choice(ctx, 'qc_material_id', '质控品 *', list_qc_materials(include_disabled=True), ['manufacturer_name', 'catalog_no', 'generic_name'], disabled=locked)
        _choice(ctx, 'default_method_id', '默认方法学', list_methods(include_disabled=True), ['method_name'], optional=True)
    if locked:
        st.caption('已添加检验项目或批次，不能更换仪器和质控品。如需更换，请新建项目。')
    _text(ctx, 'project_group', '分组', placeholder='例如：血筛、生化、分子')
    _qc_choices(ctx, defaults=True)
    st.caption('默认设置只带入以后新增的检验项目，各项可分别调整。')
    _text(ctx, 'notes', '项目备注', multiline=True)
    cancel, save = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('保存项目', type='primary', key='project_dialog_save'):
        try:
            tid = save_project_details(ctx['draft'], template_id=ctx['template_id'], expected_revision=ctx['revision'])
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state['workspace_project_id'] = tid
            st.session_state['home_selected_project_id'] = tid
            st.session_state['v11_selected_template_id'] = tid
            _finish('项目已保存，请添加检验项目。' if not ctx['template_id'] else '项目资料已保存。')
    if ctx['template_id'] is not None:
        st.divider()
        if ctx['draft'] != ctx['initial']:
            st.caption('停用按已保存的项目资料执行，本次未保存的修改不会一并保存。')
        if st.button('停用项目', key='project_editor_disable'):
            _begin_project_status(ctx, disabled=True)


def _render_disabled_project(ctx):
    saved = ctx['initial']
    st.info('项目已停用，资料仅供查看。恢复后请重新确认项目设置。')
    for field, label in [('template_name', '项目名称'), ('instrument_name', '仪器'),
                         ('qc_material_name', '质控品'), ('default_reagent_name', '默认试剂'),
                         ('project_group', '分组'), ('notes', '项目备注')]:
        st.write(f'{label}：{saved.get(field) or "未填写"}')
    cancel, restore = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _finish()
    if restore.button('恢复项目', key='project_editor_restore'):
        _begin_project_status(ctx, disabled=False)


def _begin_project_status(ctx, *, disabled):
    ctx['status_confirmation'] = dict(
        template_id=ctx['template_id'], revision=ctx['revision'],
        saved_name=ctx['initial']['template_name'], editor_token=ctx['token'],
        disabled=disabled, step='reason' if disabled else 'restore',
        reason='', first_confirmed=False, token=uuid4().hex,
    )
    st.rerun()


def _render_project_status_confirmation(ctx):
    confirmation = ctx['status_confirmation']
    if (confirmation['template_id'] != ctx['template_id'] or
            confirmation['revision'] != ctx['revision'] or confirmation['editor_token'] != ctx['token']):
        ctx.pop('status_confirmation', None)
        st.rerun()
    disabled = confirmation['disabled']
    st.subheader(('停用项目' if disabled else '恢复项目') + '：' + confirmation['saved_name'])
    st.info('原有批次、检测结果和报告仍可查询。')
    if disabled and ctx['draft'] != ctx['initial']:
        st.caption('仅停用当前已保存的项目；本次未保存的修改不会一并保存。')
    if disabled and confirmation['step'] == 'reason':
        confirmation['reason'] = st.text_input('停用原因 *', value=confirmation['reason'],
            key='project_disable_reason_' + confirmation['token'])
        cancel, first = st.columns(2)
        if cancel.button('取消，返回编辑', type='primary', key='project_status_cancel'):
            ctx.pop('status_confirmation', None)
            st.rerun()
        if first.button('确认停用', key='project_disable_first_confirm'):
            if not confirmation['reason'].strip():
                st.error('请填写停用原因。')
            else:
                confirmation['reason'] = confirmation['reason'].strip()
                confirmation['first_confirmed'] = True
                confirmation['step'] = 'final'
                st.rerun()
        return
    if disabled and not (confirmation['step'] == 'final' and confirmation['first_confirmed'] and confirmation['reason']):
        confirmation.update(step='reason', first_confirmed=False)
        st.rerun()
    if disabled:
        st.warning('请再次确认停用。停用后不能继续在此项目下新增检测。')
        st.write('停用原因：' + confirmation['reason'])
    else:
        st.caption('恢复后请重新确认项目设置。')
    cancel, final = st.columns(2)
    if cancel.button('取消，返回编辑', type='primary', key='project_status_cancel'):
        ctx.pop('status_confirmation', None)
        st.rerun()
    button_key = 'project_disable_final_confirm_' + confirmation['token'] if disabled else 'project_restore_confirm'
    if final.button('再次确认停用' if disabled else '确认恢复', key=button_key):
        try:
            change_project_status(confirmation['template_id'], expected_revision=confirmation['revision'],
                                  disabled=disabled, reason=confirmation['reason'])
        except ValueError as exc:
            st.error(str(exc))
        else:
            if disabled:
                st.session_state.pop('workspace_project_id', None)
            _finish('项目已停用。' if disabled else '项目已恢复，请重新确认项目设置。')


def _render_item_form(ctx):
    from services.master_data_service import list_test_items, list_units, list_methods, list_reagents
    st.subheader('编辑检验项目' if ctx['item_id'] else '添加检验项目')
    st.caption(ctx['template_name'])
    _choice(ctx, 'test_item_id', '检验项目 *', list_test_items(include_disabled=True), ['chinese_name', 'abbreviation', 'aliases'])
    left, right = st.columns(2)
    with left:
        _choice(ctx, 'method_id', '方法学 *', list_methods(include_disabled=True), ['method_name'])
        _choice(ctx, 'unit_id', '单位 *', list_units(include_disabled=True), ['symbol', 'unit_name'])
    with right:
        _choice(ctx, 'reagent_id', '试剂 *', list_reagents(include_disabled=True), ['generic_name', 'trade_name'])
        ctx['draft']['input_value_type'] = st.selectbox('输入值类型', list(INPUT_VALUE_TYPE_LABELS),
            format_func=INPUT_VALUE_TYPE_LABELS.get, key=_key(ctx, 'input_value_type'))
    _qc_choices(ctx)
    if ctx['draft']['qc_method'] == 'instant':
        ctx['draft']['target_n'] = 20
    else:
        ctx['draft']['target_n'] = st.number_input('建立均值和标准差所需数据点数', min_value=5, max_value=20, step=1, key=_key(ctx, 'target_n'))
    if ctx['draft']['test_item_id']:
        from ui.quality_applicability import render_draft_standard_preview
        render_draft_standard_preview(ctx['draft'])
    _text(ctx, 'notes', '备注', multiline=True)
    cancel, save = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('保存', type='primary', key='project_item_save'):
        try:
            iid = save_single_test_item(ctx['template_id'], ctx['draft'], expected_revision=ctx['revision'], item_id=ctx['item_id'])
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            st.session_state[f"workspace_item_{ctx['template_id']}"] = iid
            # The next app run renders one new dialog, never a nested dialog.
            st.session_state['pending_project_quality_item'] = (ctx['template_id'], iid)
            _finish('检验项目已保存，请设置质量目标。')


_PANEL_FIELDS = {'unit_id': '单位', 'method_id': '方法学', 'reagent_id': '试剂', 'qc_method': '质控方法',
                 'input_value_type': '输入值类型', 'level_count': '水平数', 'target_n': '参数建立点数'}


def _panel_reference_options():
    from services.master_data_service import list_units, list_methods, list_reagents
    from services.project_config_service import QC_METHOD_LABELS
    options = {'qc_method': QC_METHOD_LABELS, 'input_value_type': INPUT_VALUE_TYPE_LABELS}
    for field, frame, names in [('unit_id', list_units(), ['symbol']), ('method_id', list_methods(), ['method_name']),
                                ('reagent_id', list_reagents(), ['generic_name', 'trade_name', 'manufacturer_name'])]:
        options[field] = {int(r['id']): '｜'.join(str(r.get(name) or '') for name in names if r.get(name))
                          for r in frame.to_dict('records')}
    return options


def _panel_values(ctx, template, options):
    values = ctx.setdefault('panel_defaults', dict(unit_id=None, method_id=template['default_method_id'],
        reagent_id=template['default_reagent_id'], qc_method=template['default_qc_method'],
        input_value_type='raw', level_count=int(template['default_level_count']), target_n=20))
    for field, label in _PANEL_FIELDS.items():
        key = 'panel_default_' + ctx['token'] + '_' + field
        if field in options:
            choices = options[field]
            current = values.get(field)
            values[field] = st.selectbox(label, [None, *choices], index=[None, *choices].index(current) if current in choices else 0,
                format_func=lambda value, choices=choices: '逐项选择' if value is None else choices[value], key=key)
        else:
            values[field] = st.number_input(label, min_value=1 if field == 'level_count' else 5,
                max_value=3 if field == 'level_count' else 20, value=int(values[field]), step=1, key=key)
    st.caption('请逐项核对质控方法，质控品有多个水平也不会自动改为多水平法。选择即时法后，累计20个有效建立点再人工确认转入LJ。')
    return dict(values)


def _render_panel_source(ctx, template, options):
    from services.project_config_service import list_project_templates, template_item_rows, preview_panel_items
    from services.master_data_service import list_test_items
    source = st.radio('带入来源', ['已有项目', '质控品适用检验项目', '选择检验项目', '导入配置清单'],
                      horizontal=True, key='panel_source_' + ctx['token'])
    candidates = []
    if source == '已有项目':
        templates = list_project_templates()
        names = {int(r['id']): r['template_name'] for r in templates.to_dict('records') if int(r['id']) != ctx['template_id']}
        chosen = st.selectbox('来源项目', [None, *names], format_func=lambda value: '请选择' if value is None else names[value],
                              key='panel_source_template_' + ctx['token'])
        if chosen:
            candidates = template_item_rows(chosen)
            st.caption('带入后请核对各项的方法、单位和输入值类型，再逐项确认当前项目的质量要求。')
    elif source == '导入配置清单':
        file = st.file_uploader('项目配置 XLSX', type=['xlsx'], key='panel_source_file_' + ctx['token'])
        if file:
            from services.project_config_io_service import preview_panel_import_rows
            try:
                candidates = preview_panel_import_rows(ctx['template_id'], file.getvalue())
            except ValueError as exc:
                st.error(str(exc))
    else:
        tests = list_test_items()
        if source == '质控品适用检验项目':
            from services.product_directory_service import get_product_relationships
            coverage = get_product_relationships(int(template['qc_material_id']))['coverage']
            ids = {int(row['test_item_id']) for row in coverage if not row.get('is_disabled')}
            tests = tests[tests.id.isin(ids)]
            if tests.empty:
                st.info('尚未登记此质控品适用的检验项目。请在基础资料中核对并保存适用项目，或在此逐项选择本次检测项目。')
        names = {int(r['id']): str(r['chinese_name']) + ('｜' + str(r['standard_code']) if r.get('standard_code') else '')
                 for r in tests.to_dict('records')}
        selected = st.multiselect('本次检验项目', list(names), format_func=names.get,
            default=list(names) if source == '质控品适用检验项目' else [], key='panel_source_tests_' + source + ctx['token'])
        with st.expander('本次新增项的初始设置', expanded=True):
            defaults = _panel_values(ctx, template, options)
        candidates = [dict(test_item_id=tid, **defaults) for tid in selected]
    if candidates and source in ('已有项目', '导入配置清单'):
        indices = list(range(len(candidates)))
        selected = st.multiselect('本次带入项目', indices, default=indices,
            format_func=lambda i: str(candidates[i].get('test_item_name') or candidates[i]['test_item_id']) + '｜' +
                str(options['qc_method'].get(candidates[i]['qc_method'], '')) + '｜' +
                str(options['input_value_type'].get(candidates[i]['input_value_type'], '')),
            key='panel_candidate_selection_' + ctx['token'] + str(source) + str([r['test_item_id'] for r in candidates]))
        candidates = [candidates[i] for i in selected]
    cancel, preview = st.columns(2)
    if cancel.button('取消', key='panel_source_cancel'):
        _finish()
    if preview.button('预览整组带入', type='primary', disabled=not candidates, key='panel_source_preview'):
        try:
            prepared = preview_panel_items(ctx['template_id'], candidates, expected_revision=ctx['revision'])
            if prepared['errors']:
                raise ValueError('\n'.join(prepared['errors']))
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            ctx['draft'] = dict(rows=prepared['rows'], stage='edit')
            ctx['panel_preview'] = prepared
            ctx['editor_version'] = 0
            st.rerun()


def _panel_frame(rows, options):
    import json
    display = []
    for row in rows:
        try:
            review = json.loads(row.get('quality_review_json') or '{}')
        except (TypeError, ValueError):
            review = {}
        current = {'行标识': row['row_key'], '检验项目': row.get('test_item_name', ''),
                   '质量要求': '已确认，修改后需重核' if review.get('status') == 'confirmed' else '待逐项核对',
                   '备注': row.get('notes', '')}
        for field, label in _PANEL_FIELDS.items():
            current[label] = options[field].get(row.get(field), '') if field in options else row.get(field)
        display.append(current)
    return pd.DataFrame(display)


def _panel_edited_rows(edited, original, options):
    originals = {row['row_key']: row for row in original}
    rows = []
    for shown in edited.to_dict('records'):
        row = dict(originals[shown['行标识']])
        for field, label in _PANEL_FIELDS.items():
            value = shown[label]
            row[field] = next((key for key, text in options[field].items() if text == value), None) if field in options else value
        row['notes'] = shown.get('备注') or ''
        rows.append(row)
    return rows


def _render_panel_form(ctx):
    from services.project_config_service import preview_panel_defaults, save_panel_items
    st.subheader('整组添加检验项目')
    st.caption(ctx['template_name'])
    template, options = _plain(get_project_template(ctx['template_id'])), _panel_reference_options()
    if ctx['draft']['stage'] == 'source':
        _render_panel_source(ctx, template, options)
        return
    prepared = ctx['panel_preview']
    st.info(f"本次新增 {prepared['added_count']} 项，保留已有 {prepared['retained_count']} 项。可逐项修改；保存后仍需核对质量要求。")
    entries = pd.DataFrame(prepared['entries'])
    if not entries.empty:
        with st.expander('带入清单'):
            st.dataframe(entries[['test_item_name', 'message']].rename(columns={'test_item_name': '检验项目', 'message': '处理结果'}),
                         hide_index=True, width='stretch')
    columns = {'行标识': None}
    for field, choices in options.items():
        columns[_PANEL_FIELDS[field]] = st.column_config.SelectboxColumn(_PANEL_FIELDS[field], options=['', *choices.values()])
    columns['水平数'] = st.column_config.NumberColumn('水平数', min_value=1, max_value=3, step=1)
    columns['参数建立点数'] = st.column_config.NumberColumn('参数建立点数', min_value=5, max_value=20, step=1)
    edited = st.data_editor(_panel_frame(ctx['draft']['rows'], options), hide_index=True, width='stretch',
        disabled=['行标识', '检验项目', '质量要求'], column_config=columns,
        key='panel_rows_' + ctx['token'] + '_' + str(ctx['editor_version']))
    ctx['draft']['rows'] = _panel_edited_rows(edited, ctx['draft']['rows'], options)
    with st.expander('为选中项目统一设置'):
        names = {row['row_key']: row.get('test_item_name', '') for row in ctx['draft']['rows']}
        selected = st.multiselect('选择项目', list(names), format_func=names.get, key='panel_bulk_rows_' + ctx['token'])
        fields = st.multiselect('选择需要统一修改的内容', list(_PANEL_FIELDS), format_func=_PANEL_FIELDS.get, key='panel_bulk_fields_' + ctx['token'])
        values = _panel_values(ctx, template, options)
        overwrite = st.checkbox('同时替换所选内容的原填写', key='panel_bulk_overwrite_' + ctx['token'])
        if st.button('预览统一设置', disabled=not selected or not fields, key='panel_bulk_preview'):
            ctx['bulk_preview'] = preview_panel_defaults(ctx['draft']['rows'], values,
                selected_row_keys=selected, fields=fields, overwrite=overwrite)
            ctx['bulk_preview']['original_rows'] = [dict(row) for row in ctx['draft']['rows']]
        proposed = ctx.get('bulk_preview')
        if proposed:
            changes = [{ '检验项目': c['test_item_name'], '修改内容': _PANEL_FIELDS[c['field']],
                         '原填写': str(options.get(c['field'], {}).get(c['before'], c['before']) or '未填写'),
                         '改为': str(options.get(c['field'], {}).get(c['after'], c['after']) or '未填写')}
                       for c in proposed['changes']]
            st.dataframe(pd.DataFrame(changes), hide_index=True, width='stretch')
            yes, no = st.columns(2)
            if yes.button('应用以上设置', key='panel_bulk_apply'):
                if ctx['draft']['rows'] != proposed['original_rows']:
                    ctx.pop('bulk_preview', None)
                    st.error('逐项填写已变化，请重新预览统一设置。')
                else:
                    ctx['draft']['rows'] = proposed['rows']
                    ctx.pop('bulk_preview', None)
                    ctx['editor_version'] += 1
                    st.rerun()
            if no.button('取消统一设置', key='panel_bulk_cancel'):
                ctx.pop('bulk_preview', None)
                st.rerun()
    cancel, save = st.columns(2)
    if cancel.button('取消', key='panel_edit_cancel'):
        _cancel(ctx)
    if save.button('保存整组项目', type='primary', key='panel_save'):
        try:
            result = save_panel_items(ctx['template_id'], ctx['draft']['rows'], expected_revision=ctx['revision'])
        except (TypeError, ValueError) as exc:
            st.error(str(exc))
        else:
            _finish(f"已保存 {result['saved_count']} 项。请逐项核对质量要求后确认项目设置。")


def _render_confirmation(ctx):
    if ctx['kind'] != 'remove_item':
        st.error('请从编辑项目中选择停用或恢复。')
        if st.button('关闭', key='project_unsupported_confirmation_close'):
            _finish()
        return
    label = '移除检验项目'
    name = ctx['draft'].get('test_item_name')
    st.subheader(f'{label}：{name}')
    st.info('原有批次、检测结果和报告仍可查询。')
    reason = st.text_input('原因', key='project_confirm_reason')
    cancel, confirm = st.columns(2)
    if cancel.button('取消', type='primary', key='project_confirm_cancel'):
        _finish()
    if confirm.button('确认' + label, key='project_confirm_apply'):
        try:
            if not reason.strip():
                raise ValueError('请填写移除原因。')
            remove_test_item(ctx['template_id'], ctx['item_id'], expected_revision=ctx['revision'], reason=reason)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _finish(label + '已完成。')


@st.dialog('项目设置', width='large', dismissible=False)
def render_project_dialog():
    ctx = st.session_state.get(MODAL_KEY)
    if not ctx:
        return
    if ctx['discard']:
        _render_discard(ctx)
    elif ctx.get('status_confirmation'):
        _render_project_status_confirmation(ctx)
    elif ctx['kind'] == 'project':
        _render_project_form(ctx)
    elif ctx['kind'] == 'item':
        _render_item_form(ctx)
    elif ctx['kind'] == 'panel':
        _render_panel_form(ctx)
    elif ctx['kind'] == 'quality':
        from ui.quality_targets import render_adoption
        st.subheader('质量目标 · ' + str(ctx['draft'].get('test_item_name', '')))
        render_adoption('project', ctx['item_id'], embedded=True)
        if st.button('返回项目', key='project_quality_close'):
            _finish()
    else:
        _render_confirmation(ctx)


def render_pending_project_dialog():
    pending = st.session_state.pop('pending_project_quality_item', None)
    if pending:
        open_project_dialog('quality', pending[0], pending[1])
    if st.session_state.get(MODAL_KEY):
        render_project_dialog()
