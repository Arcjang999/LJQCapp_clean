"""Explicit source setup for synthetic regression fixtures; never disables gates."""
from services.quality_target_service import adopt_requirement, item_context, validate_spec_for_item
from services.quality_review_service import save_recorded_requirement, standard_candidates


def confirm_fixture_quality(scope, item_id):
    """Use an exact applicable candidate or document a synthetic test SOP.

    Tests of clinical applicability should provide their own evidence instead of
    this helper. This helper supplies only synthetic QC workflow fixture context.
    """
    item = item_context(scope, item_id)
    candidates = standard_candidates(item)
    applicable = []
    exclusions = {}
    for spec in candidates:
        try:
            validate_spec_for_item(spec, item)
        except ValueError as error:
            exclusions[spec['id']] = f'隔离回归数据已核对：{error}'
        else:
            applicable.append(spec)
    if applicable:
        spec = applicable[0]
        for other in applicable[1:]:
            exclusions[other['id']] = ('本隔离夹具使用所选来源的适用材料和方法条件；'
                                        '另一来源范围不属于本夹具。')
        levels = []
        if scope == 'lot':
            from services.project_config_service import list_lot_item_levels
            for row in list_lot_item_levels(item_id).to_dict('records'):
                rule = spec['imprecision'][0]
                if 'lower' in rule and 'upper' in rule:
                    concentration = (rule['lower'] + rule['upper']) / 2
                elif 'lower' in rule:
                    concentration = rule['lower'] + 1
                elif 'upper' in rule:
                    concentration = rule['upper'] / 2
                else:
                    concentration = 100.0
                if spec['id'] == 'wst406-2024-fib' and rule.get('category') == '异常':
                    concentration = 7.0
                levels.append(dict(level_order=row['level_order'], concentration=concentration,
                                   category=rule.get('category', '')))
        return adopt_requirement(scope, item_id, spec['id'], levels=levels,
                                 confirmed_by='隔离回归测试',
                                 evidence='已核对本夹具项目、单位、输入尺度与各水平适用条件。',
                                 exclusions=exclusions)
    return save_recorded_requirement(
        scope, item_id, source_name='隔离测试实验室 SOP', source_version='TEST-QC-001',
        requirement_text='使用测试方案规定的合成数据、建靶参数与判读预期，仅作软件回归验证。',
        confirmed_by='隔离回归测试',
        evidence='已核对当前目录，未找到适用于本合成测试项目及输入尺度的标准；采用测试方案。',
        exclusions=exclusions,
    )


def confirm_fixture_project(template_id):
    from services.project_config_service import list_template_items
    for item in list_template_items(template_id).to_dict('records'):
        confirm_fixture_quality('project', item['id'])


def confirm_fixture_lot(config_id):
    from services.project_config_service import list_lot_config_items
    for item in list_lot_config_items(config_id).to_dict('records'):
        if item['is_enabled']:
            confirm_fixture_quality('lot', item['id'])
