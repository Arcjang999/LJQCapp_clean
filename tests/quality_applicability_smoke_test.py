"""Task 0 acceptance: mandatory standards, distinct scales and immutable sources."""
from pathlib import Path
import copy
from datetime import date
import json
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import get_connection, init_db
from tests.instant_v12_integration_smoke_test import IsolatedDatabase, rejected
from tests.instant_v12_fixtures import seed_instant_configuration
from services.master_data_service import create_unit, list_units, create_qc_lot, create_qc_level
from services.project_config_service import (list_template_items, list_lot_config_items,
    activate_project_template, activate_lot_config, copy_lot_config)
from services.quality_target_service import (item_context, adopt_requirement, get_requirement,
    decode, clear_requirement, runtime_goal)
from services.quality_review_service import (save_recorded_requirement, save_pending_review,
    standard_candidates, validate_project_quality, runtime_review)
from services.quality_applicability_service import (assess, catalog_index, source_status,
    validate_registered_sources)


def draft(name='CRP', mode='raw', unit='mg/L', method='免疫比浊法'):
    f=seed_instant_configuration(name='适用标准隔离验证', input_value_type=mode, cv_limit=None)
    iid=int(list_template_items(f['template_id']).iloc[0]['id'])
    units=list_units()
    found=units.loc[units.symbol==unit]
    uid=int(found.iloc[0]['id']) if len(found) else create_unit(symbol=unit)
    with get_connection() as c:
        c.execute('UPDATE md_test_items SET chinese_name=?,abbreviation=? WHERE id=?',(name,'',f['test_item_id']))
        c.execute('UPDATE md_methods SET method_name=?,method_code=?,principle=? WHERE id=?',(method+'（隔离案例）','','',f['method_id']))
        c.execute("UPDATE qc_project_template_items SET unit_id=?,quality_goal_json='{}',quality_review_json='{}' WHERE id=?",(uid,iid))
        c.execute("UPDATE qc_project_templates SET status='draft' WHERE id=?",(f['template_id'],))
    return f,iid


def context(**kwargs):
    return dict(dict(technique='immunoassay',result_kind='quantitative',
        result_scale='concentration',purpose='clinical',specimen='血清'),**kwargs)


def search(conclusion='no_numeric'):
    return dict(query='本隔离案例检测对象、技术、标本与用途',
        official_url='https://www.nhc.gov.cn/wjw/s9492/wsbz.shtml',checked_on=date.today().isoformat(),
        conclusion=conclusion,rationale='隔离测试复核记录；不用于真实临床配置。')


def adopt(iid, identifier='wst403-2024-047', **kwargs):
    return adopt_requirement('project',iid,identifier,confirmed_by='测试核查人',
        evidence='已逐项核对官方标准、检测方法、结果尺度及单位。',context=context(),**kwargs)


def record(iid, ctx, **kwargs):
    return save_recorded_requirement('project',iid,source_name='测试 SOP',source_version='TEST-2026',
        requirement_text='测试方案对应的质量要求；过程要求另关联标准。',confirmed_by='测试核查人',
        evidence='依据明确的检测条件和全文核查记录登记。',context=ctx,**kwargs)


def test_immune_aliases_and_exact_units():
    for name,identifier,unit in [('IgG','wst403-2024-042','g/L'),('TSH','wst403-2024-055','mU/L'),('AFP','wst403-2024-072','ng/mL')]:
        with IsolatedDatabase():
            f,iid=draft(name,unit=unit)
            assert identifier in [r['id'] for r in standard_candidates(item_context('project',iid))]
            adopt(iid,identifier)
            assert validate_project_quality(iid)==[]
            activate_project_template(f['template_id'])
    with IsolatedDatabase():
        f,iid=draft('TSH',unit='mg/L')
        assert any(r['status']=='pending' for r in assess(item_context('project',iid),context()))
        rejected(lambda:record(iid,context(),search_record=search(),exclusions={'wst403-2024-055':'不想换单位'}),'待核查')


def test_drafts_and_free_text_cannot_bypass_applicable_standard():
    with IsolatedDatabase():
        f,iid=draft()
        save_pending_review('project',iid,context={'specimen':'血清'})
        rejected(lambda:activate_project_template(f['template_id']),'质量目标待确认')
        rejected(lambda:record(iid,context(),exclusions={'wst403-2024-047':'不采用'}),'必须采用')
        custom=copy.deepcopy(get_requirement('wst403-2024-047'))
        custom.update(id='custom-loose',origin='custom',standard='测试实验室要求')
        custom['imprecision'][0]['value']=99
        with get_connection() as c:
            c.execute('INSERT INTO qc_quality_catalog(id,origin,payload_json) VALUES (?,?,?)',
                      (custom['id'],'custom',json.dumps(custom)))
        rejected(lambda:adopt(iid,'custom-loose'),'必须采用')
        rejected(lambda:adopt(iid,supplemental_cv=99),'严于')
        goal=adopt(iid,supplemental_cv=3)
        assert goal['spec']['imprecision'][0]['value']==7.5 and goal['supplement']['cv']==3
        assert item_context('project',iid)['cv_limit']==3
        clear_requirement('project',iid)
        rejected(lambda:activate_project_template(f['template_id']),'质量目标待确认')


def test_realtime_pcr_ct_log_and_concentration_adopt_process_requirements():
    for mode,scale in [('ct','ct'),('log','log'),('raw','concentration')]:
        with IsolatedDatabase():
            f,iid=draft('HBV DNA',mode=mode,unit='Ct' if mode=='ct' else 'IU/mL',method='实时荧光 PCR')
            ctx=context(technique='realtime_pcr',result_scale=scale)
            rejected(lambda:record(iid,ctx,search_record=search(),adopted_standard_ids=[]),'全部适用')
            rejected(lambda:record(iid,ctx,search_record=search('no_applicable')),'已有适用')
            reviewed=record(iid,ctx,search_record=search())
            assert any(r['id']=='wst230-2024-iqc' and r['disposition']=='adopted' for r in reviewed['candidates'])
            assert not decode(item_context('project',iid)['quality_goal_json'])
            assert item_context('project',iid)['cv_limit'] is None
            assert validate_project_quality(iid)==[]
            activate_project_template(f['template_id'])


def test_sequencing_and_isothermal_do_not_inherit_pcr_or_concentration_cv():
    for technique,method in [('sequencing','基因测序'),('isothermal','核酸恒温扩增')]:
        with IsolatedDatabase():
            f,iid=draft('HBV DNA',mode='ct',method=method)
            ctx=context(technique=technique,result_scale='ct')
            assert not any(r['id']=='wst230-2024-iqc' for r in assess(item_context('project',iid),ctx))
            reviewed=record(iid,ctx,search_record=search())
            assert {r['id'] for r in reviewed['candidates']} == {'wst641-2018-iqc'}
            wrong={**ctx,'technique':'realtime_pcr'}
            rejected(lambda:record(iid,wrong,search_record=search()),'方法学不一致')


def test_hcv_specialized_source_is_mandatory_only_for_reviewed_identity_and_method():
    identifier = 'cdc-hcv-2023-rna-quantitative-iqc'
    with IsolatedDatabase():
        f, iid = draft('HCV RNA', unit='IU/mL', method='实时荧光 PCR')
        with get_connection() as connection:
            official_id = connection.execute("SELECT id FROM md_test_items WHERE standard_code='2600204A'").fetchone()[0]
            connection.execute('UPDATE qc_project_template_items SET test_item_id=? WHERE id=?', (official_id, iid))
        ctx = context(technique='realtime_pcr')
        item = item_context('project', iid)
        rows = assess(item, ctx)
        assert {r['id'] for r in rows if r['status']=='applicable'} == {
            identifier, 'wst230-2024-iqc', 'wst641-2018-iqc'}
        rejected(lambda: record(iid, ctx, search_record=search(),
            adopted_standard_ids=['wst230-2024-iqc','wst641-2018-iqc']), '全部适用')
        review = record(iid, ctx, search_record=search())
        selected = next(r for r in review['candidates'] if r['id']==identifier)
        assert selected['standard']['source_type']=='中国疾病预防控制中心技术规范'
        assert selected['disposition']=='adopted' and not selected['automatic_evaluation']
        assert not decode(item_context('project',iid)['quality_goal_json'])
        assert item_context('project',iid)['cv_limit'] is None

        for name, code in [('HBV DNA', ''), ('丙型肝炎病毒抗体检测', ''), ('HCV RNA', '2600104A')]:
            unrelated = dict(item, test_item_name=name, abbreviation='', standard_code=code, test_aliases=[])
            assert not any(r['id'].startswith('cdc-hcv') for r in assess(unrelated, ctx))
        coded = dict(item, test_item_name='本地简称', standard_code='2600204A')
        assert any(r['id']==identifier for r in assess(coded,ctx))
        missing = assess(dict(item, method_name='核酸恒温扩增'), dict(ctx, technique='isothermal'))
        assert next(r for r in missing if r['id']==identifier)['status']=='pending'
        tma = assess(dict(item, method_name='转录介导扩增（TMA）'), dict(ctx, technique='isothermal'))
        assert next(r for r in tma if r['id']==identifier)['status']=='applicable'
        assert not any(r['id']=='wst230-2024-iqc' for r in tma)
        qualitative = assess(dict(item, method_name='转录介导扩增（TMA）'),
            dict(ctx, technique='isothermal', result_kind='qualitative', result_scale='qualitative'))
        assert [r['id'] for r in qualitative]==['cdc-hcv-2023-rna-qualitative-iqc']
        assert not any(r['id'].startswith('cdc-hcv') for r in assess(item,dict(ctx,purpose='research')))


def test_hiv1_rna_quantitative_requires_log10_and_cannot_be_confused_with_other_targets():
    identifier = 'cdc-hiv-iqc-2024-rna-quantitative'
    for mode,scale in [('raw','concentration'), ('ct','ct'), ('log','log')]:
        with IsolatedDatabase():
            f,iid=draft('HIV-1 RNA',mode=mode,unit='copies/mL',method='实时荧光 PCR')
            ctx=context(technique='realtime_pcr',result_scale=scale)
            item=item_context('project',iid)
            row=next(r for r in assess(item,ctx) if r['id']==identifier)
            if mode!='log':
                assert row['status']=='pending' and 'Log10' in row['reason']
                rejected(lambda:record(iid,ctx,search_record=search()), '待核查')
                continue
            assert row['status']=='applicable'
            review=record(iid,ctx,search_record=search())
            assert next(r for r in review['candidates'] if r['id']==identifier)['disposition']=='adopted'
            assert item_context('project',iid)['cv_limit'] is None
            for name in ['HIV-2 RNA','HIV-1 DNA','HIV抗体','HIV RNA','CD4计数']:
                other=dict(item,test_item_name=name,abbreviation='',test_aliases=[])
                assert not any(r['id'].startswith('cdc-hiv') for r in assess(other,ctx))
            wrong_alias=dict(item,test_item_name='HIV-2 RNA',abbreviation='HIV-1 RNA')
            assert not any(r['id'].startswith('cdc-hiv') for r in assess(wrong_alias,ctx))
            tma=dict(item,method_name='转录介导扩增（TMA）')
            tma_rows=assess(tma,dict(ctx,technique='isothermal'))
            assert next(r for r in tma_rows if r['id']==identifier)['status']=='pending'
            capture=dict(item,method_name='RNA捕获探针等温扩增')
            capture_rows=assess(capture,dict(ctx,technique='isothermal'))
            assert next(r for r in capture_rows if r['id']==identifier)['status']=='applicable'
            assert not any(r['id']=='wst230-2024-iqc' for r in capture_rows)
    with IsolatedDatabase():
        f,iid=draft('HIV-1 RNA',mode='ct',unit='Ct',method='实时荧光 PCR')
        ctx=context(technique='realtime_pcr',result_kind='qualitative',result_scale='ct')
        review=record(iid,ctx,search_record=search())
        assert any(r['id']=='cdc-hiv-iqc-2024-rna-qualitative' for r in review['candidates'])
        assert not any(r['id']==identifier for r in review['candidates'])
        assert item_context('project',iid)['cv_limit'] is None


def test_signal_and_qualitative_requirements_have_no_automatic_concentration_cv():
    with IsolatedDatabase():
        f,iid=draft('HBsAg',unit='S/CO',method='化学发光免疫分析')
        ctx=context(result_kind='qualitative',result_scale='s_co')
        reviewed=record(iid,ctx,search_record=search())
        assert {r['id'] for r in reviewed['candidates']}=={'wst494-2017-qualitative','wst494-2017-signal'}
        assert all(not r['automatic_evaluation'] for r in reviewed['candidates'])
        assert not decode(item_context('project',iid)['quality_goal_json'])
        assert validate_project_quality(iid)==[]
        rejected(lambda:record(iid,context(),search_record=search()),'信号比值')
    with IsolatedDatabase():
        f,iid=draft('抗-HCV',unit='定性',method='酶联免疫测定')
        record(iid,context(result_kind='qualitative',result_scale='qualitative'),search_record=search())
        rejected(lambda:activate_project_template(f['template_id']),'尚不支持阳性')


def test_unknown_catalog_requires_official_review_and_preserves_pending_data():
    with IsolatedDatabase():
        f,iid=draft('未收录检测项',method='未指定技术')
        ctx=context(technique='other')
        rejected(lambda:record(iid,ctx),'目录尚未收录')
        rejected(lambda:record(iid,ctx,search_record={**search(),'official_url':'https://example.org'}),'官方来源')
        rejected(lambda:record(iid,ctx,search_record={**search(),'checked_on':'2099-01-01'}),'晚于今天')
        save_pending_review('project',iid,context=ctx,search_record=search(),
                            draft_recorded={'source_name':'尚待核查的 SOP'})
        assert decode(item_context('project',iid)['quality_review_json'])['context']==ctx
        assert decode(item_context('project',iid)['quality_review_json'])['draft_recorded']['source_name']=='尚待核查的 SOP'
        rejected(lambda:activate_project_template(f['template_id']),'待确认')
        reviewed=record(iid,ctx,search_record=search())
        assert reviewed['search_record']['confirmed_by']=='测试核查人'
        assert validate_project_quality(iid)==[]


def test_effective_dates_withdrawal_and_reference_intervals_are_distinct():
    sources={s['id']:s for s in catalog_index()['sources']}
    assert source_status(sources['wst641-2018'],date(2026,9,22))=='current'
    assert source_status(sources['wst641-2026'],date(2026,9,22))=='future'
    assert source_status(sources['wst641-2018'],date(2026,11,1))=='withdrawn'
    assert source_status(sources['wst641-2026'],date(2026,11,1))=='current'
    assert sources['wst645.1-2026']['kind']=='reference_interval'
    with IsolatedDatabase():
        f,iid=draft()
        item=item_context('project',iid)
        old={r['id'] for r in assess(item,context(),on_date=date(2026,10,31))}
        new={r['id'] for r in assess(item,context(),on_date=date(2026,11,1))}
        assert 'wst641-2018-iqc' in old and 'wst641-2026-iqc' not in old
        assert 'wst641-2018-iqc' not in new and 'wst641-2026-iqc' in new
        adopt(iid)
        changed=catalog_index()
        next(s for s in changed['sources'] if s['id']=='wst403-2024')['withdrawn_on']='2026-01-01'
        with patch('services.quality_applicability_service.catalog_index',return_value=changed):
            assert validate_project_quality(iid)
        assert validate_project_quality(iid)==[]


def test_copy_inherits_conditions_but_never_confirmation():
    with IsolatedDatabase():
        f,iid=draft()
        adopt(iid)
        activate_project_template(f['template_id'])
        before=runtime_review('instant',f['batch_id'])
        lot=create_qc_lot(qc_material_id=f['material_id'],lot_no='TASK00-COPY',expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=lot,level_name='新水平',level_order=1)
        new=copy_lot_config(source_lot_config_id=f['config_id'],target_qc_material_lot_id=lot)
        copied=int(list_lot_config_items(new).iloc[0]['id'])
        assert decode(item_context('lot',copied)['quality_review_json'])['status']=='pending'
        rejected(lambda:activate_lot_config(new),'待确认')
        assert runtime_review('instant',f['batch_id'])==before
        init_db();init_db()
        assert runtime_review('instant',f['batch_id'])==before
        with get_connection() as c: assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_registered_source_requires_full_provenance_and_cannot_replace_known_rules():
    source=dict(standard='测试用标准编号',name='合成测试标准',version='test-1',
        effective_date='2024-01-01',source_url='https://www.nhc.gov.cn/wjw/s9492/wsbz.shtml',
        sha256='a'*64,source_clause='测试条款1',source_page='1',requirement_text='仅供服务校验测试',
        verified_by='测试核查人',checked_on='2026-09-22',applicability_evidence='合成测试适用条件',kind='process')
    rejected(lambda:validate_registered_sources([{**source,'sha256':''}],context()),'补充标准须填写')
    rejected(lambda:validate_registered_sources([{**source,'withdrawn_on':'无效日期'}],context()),'废止日期格式')
    rejected(lambda:validate_registered_sources([{**source,'withdrawn_on':'2023-12-31'}],context()),'晚于实施日期')
    rejected(lambda:validate_registered_sources([{**source,'withdrawn_on':'2024-12-31'}],context()),'已废止')
    rejected(lambda:validate_registered_sources([{**source,'standard':'WS/T 645.1—2026'}],context()),'参考区间')
    rejected(lambda:validate_registered_sources([{**source,'standard':'WS/T 641—2026'}],context()),'尚未生效')
    with IsolatedDatabase():
        f,iid=draft('未收录项目',method='其他方法')
        ctx=context(technique='other')
        reviewed=record(iid,ctx,search_record=search('registered'),registered_standards=[source])
        assert reviewed['registered_standards'][0]['source_clause']=='测试条款1'
        assert not reviewed['registered_standards'][0]['automatic_evaluation']
        assert validate_project_quality(iid)==[]


if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_'):
            fn();print('PASS',name,flush=True)
