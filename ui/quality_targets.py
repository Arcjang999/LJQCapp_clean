from __future__ import annotations
from datetime import date
import pandas as pd
import streamlit as st
from database import atomic_write, get_connection
from services.quality_target_service import (adopt_requirement, clear_requirement, decode, item_context,
    suggested_requirements, source_label, validate_spec_for_item, batch_quality_summary)


def catalog_table(records):
    return pd.DataFrame([{'检验项目':r['name'],'要求类型':'内置标准' if r['origin']=='builtin' else '实验室自定要求',
        '标准或文件/版本':r['standard']+' / '+r['version'],'实施日期':r['effective_date'],
        '测量单位':r['measurement_unit'] or '标准未规定测量单位（以百分数表示）','检测范围':r['scope'],
        '允许不精密度':r['imprecision_text'],'允许偏倚':r['bias_text'] or '未规定',
        '允许总误差/可比性偏差':r['tea_text'] or '未规定','条款':r['source_clause'],
        'PDF页码':r.get('source_page'),'说明':r['notes']} for r in records])


def render_spec(spec):
    st.caption(source_label(spec)+f"｜实施：{spec['effective_date']}")
    st.write('允许不精密度：'+spec['imprecision_text'])
    st.write('允许偏倚：'+(spec['bias_text'] or '原条款未规定'))
    st.write('允许总误差/可比性偏差：'+(spec['tea_text'] or '原条款未规定'))
    st.caption('标准规定的检测范围：'+spec['scope']+'；单位：'+(spec['measurement_unit'] or '以本项目设置的单位为准'))
    if spec.get('notes'):st.info(spec['notes'])
    if spec.get('source_url','').startswith('https://'):
        st.link_button('查看标准原文',spec['source_url'])
    if spec.get('source_page'):st.caption(f"条款在原文第 {spec['source_page']} 页，页码包含封面。")
    basis = spec.get('laboratory_basis')
    if basis:
        st.caption('实验室依据：'+basis.get('source_name', '')+' / '+basis.get('source_version', ''))
        st.write('实验室实际要求：'+basis.get('requirement_text', ''))


def _current_source(source):
    from datetime import date
    from services.quality_applicability_service import source_status
    if source.get('verification'):
        return source_status(source) == 'current'
    today = date.today().isoformat()
    return source.get('effective_date', '') <= today and not (
        source.get('withdrawn_on') and source['withdrawn_on'] <= today)


def render_review_summary(review, *, historical=False):
    if not review:
        return
    if review.get('status') == 'pending':
        st.info('质量要求还没有确认。请填写检测条件和确认人，然后保存质量目标。')
    recorded = review.get('recorded', {})
    if recorded:
        st.write('实验室自定要求：' + recorded.get('requirement_text', ''))
        st.caption('使用文件：' + recorded.get('source_name', '') + ' / ' + recorded.get('source_version', ''))
        st.caption('请按以上要求检查检测结果，均值和SD使用本批次设置。')
    st.caption('确认人：' + review.get('confirmed_by', '') + '｜设置说明：' + review.get('evidence', ''))
    local_process = review.get('process_requirements')
    if local_process is not None:
        st.write('本实验室对照与质控要求：' + (local_process.get('requirement_text') or '未设置'))
    for row in review.get('candidates', []):
        if not historical and not _current_source(row.get('standard', {})):
            continue
        state = {'adopted': '已采用', 'referenced': '作为参考', 'not_selected': '未选用',
                 'pending': '请补充检测条件', 'not_applicable': '本次检测不采用'}.get(row.get('disposition'), '请检查检测条件')
        st.caption(f"{row['source']}｜{state}：{row.get('reason', '')}")
        if row.get('disposition') == 'adopted' and row.get('kind') not in (None, 'numeric'):
            st.caption('；'.join(row.get('requirements', [])) + '｜请按条款检查对照结果')
    for source in review.get('registered_standards', []):
        if not historical and not _current_source(source):
            continue
        st.caption(f"补充文件：{source['standard']} / {source['version']}；{source['source_clause']}；{source['requirement_text']}（请按条款检查检测结果）")
    search=review.get('search_record')
    if search:
        label = '本软件已收录标准的检查记录' if search.get('scope') == 'local_catalog' else '标准查找记录'
        st.caption(label+'：'+search.get('checked_on','')+'｜'+search.get('rationale',''))


def _quality_settings_location(scope, item_id=None):
    """Name the real editor reached from each quality window."""
    project_dialog = st.session_state.get('project_workspace_dialog') or {}
    if scope == 'project':
        if project_dialog.get('kind') == 'quality' and project_dialog.get('item_id') == item_id:
            action = '点击底部“返回项目”，在项目工作台选中本检验项目后点击“编辑检验项目”'
        else:
            action = '关闭质量目标窗口，点击顶部“返回主页”，打开对应项目，选中本检验项目后点击“编辑检验项目”'
        return dict(target='编辑检验项目', action=action, note='')
    batch_dialog = st.session_state.get('batch_item_dialog') or {}
    leave = ('点击底部“取消”，按提示处理未保存内容' if
             batch_dialog.get('kind') == 'quality' and batch_dialog.get('item_id') == item_id else
             '关闭质量目标窗口')
    return dict(target='编辑检验项目后新建批次',
        action=leave + '，点击顶部“返回主页”，打开对应项目，选中本检验项目后点击“编辑检验项目”',
        note='本批次的单位、方法学和输入值类型保持创建时的设置；修改项目并确认后，请新建批次采用更新后的设置。')


def quality_validation_issues(item, result, plan, spec=None, *, quick=False, settings_location=None):
    """Describe the existing validation rules at the fields that can resolve them."""
    from services.quality_applicability_service import (
        CONTEXT_LABELS, context_errors, missing_context_fields,
        validate_registered_sources, validate_search_record,
    )
    from services.quality_target_service import select_rule

    issues = []
    def add(field, target, message):
        if not any(row['message'] == message for row in issues):
            issues.append(dict(field=field, target=target, message=message))
    def settings(field, message):
        location = (dict(target='返回检测设置', action='点击“返回检测设置”', note='') if quick else
                    settings_location or _quality_settings_location('lot' if 'lot_config_id' in item else 'project', item.get('id')))
        add(field, location['target'], message + f' 请{location["action"]}，核对“' +
            {'unit_id': '单位', 'method_id': '方法学', 'input_value_type': '输入值类型'}.get(field, '检测设置') +
            '”。' + location.get('note', ''))

    context = result['context']
    missing = missing_context_fields(context)
    for field in missing:
        add(field, '检测条件', '请在上方“检测条件”中' +
            ('填写' if field == 'specimen' else '选择') + '“' + CONTEXT_LABELS[field] + '”。')
    blank_errors = {'请补充' + CONTEXT_LABELS[field] + '。' for field in missing}
    for message in context_errors(item, context):
        if message in blank_errors:
            continue
        if '输入值类型' in message:
            settings('input_value_type', message)
        elif '方法学' in message:
            settings('method_id', message)
        else:
            add('context', '检测条件', message + ' 请在上方“检测条件”核对结果性质、结果尺度和标本。')
    pending = [row for row in plan['assessments'] if row['kind'] != 'process' and row['status'] == 'pending']
    for row in pending:
        message = row['reason']
        if message == '待补充适用条件' and missing:
            continue
        if '单位' in message:
            settings('unit_id', message)
        elif '方法学' in message:
            settings('method_id', message)
        elif '输入值类型' in message:
            settings('input_value_type', message)
        else:
            add('context', '检测条件', message + ' 请核对上方“检测条件”；条款在“查看标准及填写对照要求”中。')
    if plan['state'] == 'blocked' and not missing and not pending and not context_errors(item, context):
        add('context', '检测条件', plan['reason'] + ' 请核对上方“检测条件”和“查看标准及填写对照要求”。')
    if not str(result.get('confirmed_by') or '').strip():
        label = '质量要求确认人' if quick else '确认人'
        add('confirmed_by', label, f'请填写“{label}”。')
    if not result.get('checked'):
        add('checked', '确认勾选', '请勾选上方“已确认检测方法、单位、各水平和以上质量要求填写正确”。')
    mandatory = {row['id'] for row in plan['assessments']
                 if row['kind'] not in ('numeric', 'process') and row['status'] == 'applicable'}
    if not mandatory.issubset(set(result.get('adopted_standard_ids') or [])):
        add('adopted_standard_ids', '同时采用的定性与信号精密度要求',
            '请在“同时采用的定性与信号精密度要求”中选择本项目适用的全部要求。')
    mode = result['mode']
    if mode == 'source':
        if spec is None:
            add('requirement_id', '选择实验室自定要求', '请在“选择实验室自定要求”中选择一项要求。')
        else:
            try:
                validate_spec_for_item(spec, item)
            except ValueError as error:
                if '单位' in str(error):
                    settings('unit_id', str(error))
                elif 'Ct' in str(error) or 'log' in str(error):
                    settings('input_value_type', str(error))
                else:
                    add('requirement_id', '选择实验室自定要求', str(error) + ' 请在“选择实验室自定要求”中重新选择。')
            rules = []
            for level in result['levels']:
                try:
                    rule = select_rule(spec, level.get('concentration'), level.get('category', ''))
                    if spec['id'] == 'wst406-2024-fib' and level.get('category') == '异常':
                        value = level.get('concentration')
                        if value is None or not (value > 6 or value < 1.5):
                            raise ValueError('异常水平须填写浓度，且 >6 g/L 或 <1.5 g/L。')
                    rules.append(rule)
                except ValueError as error:
                    fields = []
                    if any('lower' in row or 'upper' in row for row in spec['imprecision']):
                        fields.append('质控品浓度（按此浓度选择标准限值）')
                    if any(row.get('category') for row in spec['imprecision']):
                        fields.append('水平类别')
                    if spec['id'] == 'wst406-2024-fib' and level.get('category') == '异常' and len(fields) == 1:
                        fields.append('异常水平浓度（g/L）')
                    labels = '、'.join('“'+field+'”' for field in fields) or '适用要求'
                    add('levels', f'水平 {level["level_order"]}', f'请核对上方水平 {level["level_order"]} 的{labels}：{error}')
            limit = result.get('supplemental_cv')
            if limit is not None and (limit <= 0 or any(row['kind'] != 'cv' or limit >= row['value'] for row in (rules or spec['imprecision']))):
                add('supplemental_cv', '选填：实验室更严CV要求', '请展开“选填：实验室更严CV要求”，填入大于0且小于各水平标准CV上限的数值，或清空此选填项。')
    elif mode == 'sd_cv':
        if result.get('cv_limit') is not None and result['cv_limit'] <= 0:
            add('cv_limit', '实验室CV上限', '“实验室CV上限”须大于0；没有此项要求时请清空，保留为空。')
        if not str(result.get('requirement_text') or '').strip():
            add('requirement_text', '文字要求与依据', '请展开“文字要求与依据”，在“实验室实际要求”中保留SD控制说明或填写本实验室实际要求。')
    elif mode == 'recorded':
        for field, label in (('source_name', '使用的说明书或实验室文件名称'),
                             ('source_version', '设置日期或文件版本'), ('requirement_text', '实验室实际要求')):
            if not str(result.get(field) or '').strip():
                add(field, '填写其他实验室要求', f'请在“填写其他实验室要求”中填写“{label}”。')
    if mode != 'sd_cv' and not str(result.get('evidence') or '').strip():
        add('evidence', '质量要求设置说明', '请展开“质量要求设置说明”，填写“说明此要求如何用于本次检测”。')
    try:
        registered = validate_registered_sources(result.get('registered_standards'), context)
        if plan['state'] != 'blocked' and plan['state'] != 'standard':
            search = validate_search_record(result.get('search_record'), confirmed_by=result.get('confirmed_by', ''),
                has_process=any(row['kind'] != 'numeric' and row['status'] == 'applicable' for row in plan['assessments']),
                item=item, context=context)
            if search['conclusion'] == 'registered' and not registered:
                raise ValueError('已选择“已找到新标准，并在下方登记”，请补充标准全文和核查记录。')
    except ValueError as error:
        add('standards', '高级：查找或补充标准', '请展开“高级：查找或补充标准”：' + str(error))
    return issues


def render_quality_setup(item, prior, prefix, *, levels=None, goal=None, quick=False, actor='', settings_location=None):
    """One quality form for quick creation, project settings and editable lots."""
    from services.quality_setup_service import SD_REQUIREMENT, quality_setup_plan, local_catalog_review
    from services.quality_catalog_service import browse_quality_catalog
    from ui.quality_applicability import render_conditions

    condition_args, assessments = render_conditions(item, prior, prefix, quick=quick, settings_location=settings_location)
    plan = quality_setup_plan(item, condition_args['context'])
    if plan['state'] == 'sd_cv' and not condition_args['search_record']:
        condition_args['search_record'] = local_catalog_review(item, condition_args['context'])
    basis = (goal or {}).get('spec', {}).get('laboratory_basis') or {}
    recorded = prior.get('draft_recorded') or prior.get('recorded') or basis or prior
    simple_goal = bool(goal and (goal['spec'].get('setup_mode') == 'sd_cv' or goal['spec']['id'].startswith('custom-simple-')))
    previous_mode = prior.get('mode') or recorded.get('mode') or recorded.get('setup_mode')
    if not previous_mode:
        previous_mode = ('sd_cv' if simple_goal else
                         'source' if goal else
                         'recorded' if prior.get('decision') == 'record_only' else 'sd_cv')
    source_value = 'source' if quick else '选择标准或自定义目录'
    recorded_value = 'recorded' if quick else '记录实验室要求'
    mode = 'source' if plan['state'] == 'standard' else 'sd_cv'
    if plan['state'] == 'standard':
        st.markdown('**引用现行数值标准**')
        spec = plan['numeric_spec']
        selected = spec['id']
        st.session_state[prefix+'_requirement'] = selected
    else:
        if plan['state'] == 'blocked':
            st.markdown('**请先完成检测条件**')
            st.warning(plan['reason'])
            st.caption('请按下方提示完成填写；需要修改方法学、单位或输入值类型时，点击底部“返回检测设置”。'
                       if quick else '可以先点击“保存，稍后继续”。填写完整后，再确认质量要求。')
        else:
            st.markdown('**SD控制＋实验室CV**')
            st.caption('本批次按当前方法的质控规则判读；CV按正式期在控结果比较。')
        with st.expander('高级：其他实验室要求或自定目录', expanded=False):
            choices = ['sd_cv', source_value, recorded_value]
            seed = {'source': source_value, 'recorded': recorded_value}.get(previous_mode, previous_mode)
            if st.session_state.get(prefix+'_mode') not in choices:
                st.session_state[prefix+'_mode'] = seed if seed in choices else 'sd_cv'
            choice = st.radio('质量要求设置', choices, key=prefix+'_mode', horizontal=True,
                format_func=lambda value: {'sd_cv': 'SD控制＋实验室CV', source_value: '自定要求目录',
                                          recorded_value: '记录其他实验室要求'}[value])
        mode = {source_value: 'source', recorded_value: 'recorded'}.get(choice, 'sd_cv')
        spec = None
        selected = None
        if mode == 'source':
            candidates = {row['id'] for row in assessments if row['kind'] == 'numeric' and row['status'] == 'applicable'}
            sources = {row['id']: row['spec'] for row in browse_quality_catalog()
                       if row['kind'] in ('numeric', 'custom') and (row['spec']['origin'] != 'builtin' or row['id'] in candidates)}
            initial = prior.get('requirement_id') or (goal['spec']['id'] if goal else None)
            if prefix+'_requirement' not in st.session_state or st.session_state.get(prefix+'_requirement') not in [None, *sources]:
                st.session_state[prefix+'_requirement'] = initial if initial in sources else None
            selected = st.selectbox('选择实验室自定要求', [None, *sources], key=prefix+'_requirement',
                format_func=lambda value: '请选择' if value is None else sources[value]['name']+'｜'+sources[value]['standard'])
            spec = sources.get(selected)

    result = dict(condition_args, mode=mode, levels=[], requirement_id=selected)
    if spec is not None:
        st.write(spec['standard']+' / '+spec['version']+' · '+spec['name'])
        st.write('允许不精密度：'+spec['imprecision_text'])
        with st.expander('查看标准原文和限值', expanded=False):
            render_spec(spec)
        categories = list(dict.fromkeys(row['category'] for row in spec['imprecision'] if row.get('category')))
        needs_concentration = any('lower' in row or 'upper' in row for row in spec['imprecision'])
        previous_levels = prior.get('levels') or (goal.get('levels', []) if goal and goal['spec']['id'] == selected else [])
        for order, level in enumerate(levels or [], 1):
            order = int(level.get('level_order', order))
            previous = next((row for row in previous_levels if row['level_order'] == order), {})
            concentration, category = previous.get('concentration'), previous.get('category', '')
            if needs_concentration or categories:
                from services.material_workflow_service import material_label
                st.markdown(f'**水平 {order}：{material_label(level)}**')
            if needs_concentration:
                concentration = st.number_input('质控品浓度（按此浓度选择标准限值）', min_value=0.0,
                    value=concentration, format='%.4f', key=f'{prefix}_{selected}_{order}_concentration')
            if categories:
                category = st.selectbox('水平类别', ['', *categories],
                    index=categories.index(category)+1 if category in categories else 0,
                    key=f'{prefix}_{selected}_{order}_category', format_func=lambda value: value or '请选择')
                # Fib's abnormal-category rule also requires the actual concentration.
                if selected == 'wst406-2024-fib' and category == '异常' and not needs_concentration:
                    concentration = st.number_input('异常水平浓度（g/L）', min_value=0.0, value=concentration,
                        key=f'{prefix}_{selected}_{order}_concentration')
            result['levels'].append(dict(level_order=order, concentration=concentration, category=category))
        if not needs_concentration and not categories and levels:
            st.caption(f'全部 {len(levels)} 个水平采用此限值。')
        if spec['origin'] == 'builtin' and all(row['kind'] == 'cv' for row in spec['imprecision']):
            with st.expander('选填：实验室更严CV要求', expanded=False):
                value = prior.get('supplemental_cv', (goal or {}).get('supplement', {}).get('cv'))
                if prefix+'_supplemental_cv' not in st.session_state:
                    st.session_state[prefix+'_supplemental_cv'] = value
                result['supplemental_cv'] = st.number_input('实验室更严 CV 要求（选填，%）', min_value=0.0,
                    value=None, key=prefix+'_supplemental_cv', help='填入值须小于各水平的标准CV上限。标准规定的上限会同时保留。')
    elif mode == 'sd_cv':
        if plan['cv_supported']:
            cv = prior.get('cv_limit', recorded.get('cv_limit'))
            if cv is None and simple_goal and 'cv_limit' not in prior and 'cv_limit' not in recorded:
                cv = item.get('cv_limit')
            if prefix+'_cv_limit' not in st.session_state:
                st.session_state[prefix+'_cv_limit'] = cv
            result['cv_limit'] = st.number_input('实验室CV上限（选填，%）', min_value=0.0, value=None,
                key=prefix+'_cv_limit', help='原始定量尺度（浓度、活性、计数等）可设置；留空表示只使用现有质控规则。CV另行比较正式期在控结果的精密度。')
        else:
            result['cv_limit'] = None
            st.caption('Ct、log、S/CO或定性结果沿用当前方法的质控规则，其他要求可在文字依据中填写。')

    if mode in ('sd_cv', 'recorded'):
        with st.expander('文字要求与依据' if mode == 'sd_cv' else '填写其他实验室要求', expanded=mode == 'recorded'):
            simple = mode == 'sd_cv'
            if simple and plan['state'] == 'sd_cv':
                st.caption('本软件已收录的标准中，没有对应本次检测的数值限值。')
            result['source_name'] = st.text_input('使用的说明书或实验室文件名称',
                value=recorded.get('source_name') or ('本实验室SD质控方案' if simple else ''), key=prefix+'_source_name', max_chars=200)
            result['source_version'] = st.text_input('设置日期或文件版本',
                value=recorded.get('source_version') or (date.today().isoformat() if simple else ''), key=prefix+'_source_version', max_chars=200)
            text = recorded.get('requirement_text') or (SD_REQUIREMENT if simple else '')
            # The service appends the current structured CV; old generated CV text
            # must not survive a change to that limit or become its input source.
            if simple:
                import re
                text = re.sub(r'\s*实验室自定义CV上限为≤[^。]*。', '', text)
            result['requirement_text'] = st.text_area('实验室实际要求', value=text,
                key=prefix+('_recorded' if quick else '_recorded_text'), max_chars=2000)
            if not simple:
                st.caption('填写实验室执行的要求和文件名称。查到新的标准时，可在“高级：查找或补充标准”中登记。')
    person_label = '质量要求确认人' if quick else '确认人'
    from ui.required_fields import required_field
    with required_field(prefix+'_person',
            missing=not str(st.session_state.get(prefix+'_person', prior.get('confirmed_by', actor)) or '').strip(),
            message=f'请填写{person_label}。'):
        result['confirmed_by'] = st.text_input(person_label, value=prior.get('confirmed_by', actor), key=prefix+'_person', max_chars=80)
    with st.expander('补充设置说明（选填）' if mode == 'sd_cv' else '质量要求设置说明', expanded=False):
        evidence = prior.get('evidence', '')
        if not evidence and spec:
            evidence = f"{item.get('test_item_name', '')}使用{item.get('method_name', '')}，单位为{item.get('unit_symbol', '')}，全部水平采用{spec['standard']} / {spec['version']}。"
            old_auto = st.session_state.get(prefix+'_auto_evidence')
            if old_auto != evidence and st.session_state.get(prefix+'_evidence', '') in ('', old_auto):
                st.session_state[prefix+'_evidence'] = evidence
            st.session_state[prefix+'_auto_evidence'] = evidence
        result['evidence'] = st.text_area('说明此要求如何用于本次检测', value=evidence, key=prefix+'_evidence', max_chars=2000)
    checked_key = prefix+('_checked' if quick else '_confirmed')
    with required_field(checked_key,
            missing=not st.session_state.get(checked_key, bool(prior.get('checked')) if quick else False),
            message='请核对本页设置后勾选确认。'):
        result['checked'] = st.checkbox('已确认检测方法、单位、各水平和以上质量要求填写正确',
            value=bool(prior.get('checked')) if quick else False, key=checked_key)
    result['blocked'] = plan['state'] == 'blocked'
    result['validation_issues'] = quality_validation_issues(item, result, plan, spec, quick=quick,
                                                        settings_location=settings_location)
    if result['validation_issues']:
        st.warning(('下一步前' if quick else '保存质量目标前') + '，请完成以下操作：\n\n' +
                   '\n'.join('- ' + row['message'] for row in result['validation_issues']))
    return result


def render_adoption(scope,item_id,*,embedded=False,expected_revision=None,on_saved=None):
    from services.quality_review_service import (standard_candidates, save_recorded_requirement,
        is_frozen_lot)
    item=item_context(scope,item_id);goal=decode(item['quality_goal_json']);prefix=f'quality_{scope}_{item_id}'
    review=decode(item.get('quality_review_json'))
    st.caption(f"{item['test_item_name']}｜单位 {item['unit_symbol']}｜{item['level_count']} 个水平")
    frozen=False
    if scope=='lot':
        with get_connection() as connection:
            frozen=is_frozen_lot(connection,item)
    if embedded:st.markdown('**本批次质量目标**' if scope=='lot' else '**项目质量目标**')
    def saved_details():
        from services.quality_catalog_service import browse_quality_catalog
        current_ids = {row['id'] for row in browse_quality_catalog()}
        if goal and (frozen or goal['spec']['id'] in current_ids):
            render_spec(goal['spec'])
            if scope=='lot' and goal.get('pending'):
                st.info('请检查各水平使用的质量要求，然后点击“确认本批次质量目标”。')
            else:
                st.caption(f"确认人：{goal['confirmed_by']}｜设置时间：{goal['adopted_at']}")
            if goal.get('evidence'):st.caption('设置说明：'+goal['evidence'])
            if goal.get('levels'):
                st.dataframe(pd.DataFrame([{'水平':v['level_order'],'浓度':v['concentration'],'类别':v['category'],
                    '要求':f"{v['rule']['operator']} {v['rule']['value']:g}{v['rule']['unit']}"} for v in goal['levels']]),hide_index=True)
        elif item['cv_limit'] is not None:
            st.caption(f"原设定允许不精密度（CV）：≤{item['cv_limit']:g}%｜依据：{item['quality_target_source_text'] or '未填写'}")
        render_review_summary(review, historical=frozen)
    if frozen:
        saved_details()
    elif goal or review or item['cv_limit'] is not None:
        with st.expander('查看已保存的质量要求和文件', expanded=False):
            saved_details()
    if frozen:
        st.info('本批次继续使用已保存的质量要求。需要调整时，请在新批次设置。' if goal or review or item['cv_limit'] is not None else '本批次没有已保存的质量要求。请返回批次详情核对；需要补充或调整时，请在新批次设置。')
        return
    levels = []
    if scope == 'lot':
        from services.project_config_service import list_lot_item_levels
        levels = list_lot_item_levels(item_id).to_dict('records')
        if len(levels) != item['level_count']:
            st.info('各水平的质控品还没有选齐。请先返回批次详情，点击当前检验项目的“编辑水平设置”，选择并保存全部水平后，再设置质量要求。')
            return
    result = render_quality_setup(item, review, prefix, levels=levels, goal=goal,
                                  settings_location=_quality_settings_location(scope, item_id))
    condition_args = {key: result[key] for key in ('context', 'search_record', 'adopted_standard_ids',
                                                   'registered_standards', 'process_requirements')}
    def check_current():
        if scope == 'lot' and expected_revision is not None:
            from services.batch_edit_service import get_batch_item_context
            current = get_batch_item_context(item_id)
            if not current['editable']:
                raise ValueError(current['read_only_reason'])
            if current['revision'] != expected_revision:
                raise ValueError('本批次设置已修改，请重新打开后再保存。')
    pending, confirm = st.columns(2)
    if pending.button('保存，稍后继续', key=prefix+'_save_pending'):
        try:
            from services.quality_review_service import save_pending_review
            from services.quality_setup_service import local_catalog_review
            with atomic_write():
                check_current()
                search = condition_args['search_record']
                if result['mode'] == 'sd_cv' and not result['blocked'] and not search:
                    search = local_catalog_review(item, result['context'])
                draft_recorded = {key: result.get(key, '') for key in
                                  ('source_name', 'source_version', 'requirement_text')}
                if result['mode'] == 'sd_cv':
                    draft_recorded.update(mode='sd_cv', cv_limit=result.get('cv_limit'))
                save_pending_review(scope, item_id, context=result['context'], search_record=search,
                    registered_standards=result['registered_standards'],
                    process_requirements=result['process_requirements'], draft_recorded=draft_recorded)
        except ValueError as error:
            st.error(str(error))
        else:
            if on_saved is not None:
                on_saved()
            st.success('填写内容已保存。下次可继续填写，完成后再确认项目或批次设置。')
            st.rerun()
    if confirm.button('确认本批次质量目标' if scope == 'lot' else '保存质量目标', key=prefix+'_adopt',
                      disabled=not result['checked'] or result['blocked'], type='primary'):
        try:
            with atomic_write():
                check_current()
                args = dict(condition_args, confirmed_by=result['confirmed_by'], evidence=result['evidence'])
                if result['mode'] == 'sd_cv':
                    from services.quality_setup_service import save_simple_quality
                    save_simple_quality(scope, item_id, **args, cv_limit=result.get('cv_limit'), levels=result['levels'],
                        **{key: result[key] for key in ('source_name', 'source_version', 'requirement_text')})
                elif result['mode'] == 'recorded':
                    save_recorded_requirement(scope, item_id, **args,
                        **{key: result[key] for key in ('source_name', 'source_version', 'requirement_text')})
                else:
                    adopt_requirement(scope, item_id, result['requirement_id'], **args, levels=result['levels'],
                                      supplemental_cv=result.get('supplemental_cv'))
        except ValueError as error:
            st.error(str(error))
        else:
            if on_saved is not None:
                on_saved()
            st.success('质量目标已保存，请继续确认项目或批次设置。')
            st.rerun()


def render_batch_quality(method,batch_id,month=None):
    from services.quality_review_service import runtime_review
    review=runtime_review(method,batch_id)
    summary=batch_quality_summary(method,batch_id,month)
    if not summary:
        if review:
            with st.expander('本批次质量目标',expanded=False):
                render_review_summary(review, historical=True)
        return
    with st.expander('本批次质量目标与实测变异系数',expanded=False):
        render_review_summary(review, historical=True)
        if not summary.get('goal'):
            return
        render_spec(summary['goal']['spec'])
        st.caption(f"统计范围：{summary['period']}；{summary['statistics_scope']}。下表显示实测CV与CV上限的比较；标准中的其他要求请另行检查。")
        st.caption(f"确认人：{summary['goal']['confirmed_by']}；依据：{summary['goal']['evidence']}")
        st.dataframe(pd.DataFrame(summary['rows']).rename(columns={'level':'水平','requirement':'采用要求','category':'水平类别','concentration':'采用浓度','count':'在控点数','days':'检测日数','cv':'实测变异系数（%）','decision':'比较结果'}),hide_index=True,width='stretch')
