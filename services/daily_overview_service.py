"""Read-only daily aggregation of saved routine records and B1 handling state."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
import logging
from time import perf_counter

from database import get_connection, get_instant_results, read_snapshot
from services.search_service import fuzzy_match
from services.quality_target_service import batch_quality_summary
from services.out_of_control_service import list_pending

METHOD_LABELS = {'lj':'单水平（LJ）', 'zscore':'多水平法', 'instant':'即时法'}
CLASS_LABELS = {'accept':'在控','normal':'在控','warning':'警告','reject':'失控','pending':'尚待判读',None:'尚待判读'}
TABLES = {'lj':('results','lj_result_id','lj_result'), 'zscore':('zscore_runs','zscore_run_id','zscore_run'),
          'instant':('instant_results','instant_result_id','instant_result')}


def _json(value):
    return json.loads(value) if value else {}


def list_overview_bindings(*,include_disabled=False):
    with read_snapshot() as c:
        rows = [dict(r) for r in c.execute('''SELECT i.id AS lot_config_item_id,i.source_template_item_id AS template_item_id,
          i.qc_method,i.input_value_type,i.level_count,i.sort_order,i.target_n,
          c.id AS lot_config_id,c.template_id,c.config_name,c.lab_instrument_id,c.qc_material_id,c.qc_material_lot_id,
          c.status AS config_status,t.template_name,d.chinese_name AS test_item_name,d.abbreviation AS test_item_abbreviation,
          l.display_name AS instrument_name,m.generic_name AS material_name,u.symbol AS unit_symbol,
          b.runtime_batch_id,b.runtime_project_id,b.binding_status
          FROM qc_lot_config_items i JOIN qc_lot_configs c ON c.id=i.lot_config_id
          JOIN qc_project_templates t ON t.id=c.template_id JOIN md_test_items d ON d.id=i.test_item_id
          JOIN lab_instruments l ON l.id=c.lab_instrument_id JOIN md_qc_materials m ON m.id=c.qc_material_id
          LEFT JOIN md_units u ON u.id=i.unit_id LEFT JOIN qc_workbench_bindings b ON b.lot_config_item_id=i.id
          WHERE ?=1 OR (i.is_disabled=0 AND i.is_enabled=1 AND c.is_disabled=0 AND t.is_disabled=0)
          ORDER BY c.lab_instrument_id,c.template_id,i.sort_order,i.id''',(int(include_disabled),))]
        transferred = []
        for row in rows:
            if row['qc_method'] != 'instant' or row['runtime_batch_id'] is None:
                continue
            for target in c.execute('''SELECT id,project_id FROM batches WHERE source_method='instant'
                AND source_instant_batch_id=? AND (?=1 OR is_disabled=0) ORDER BY id''',
                (row['runtime_batch_id'], int(include_disabled))):
                transferred.append(dict(row, qc_method='lj', runtime_batch_id=target['id'],
                    runtime_project_id=target['project_id'], transfer_source_batch_id=row['runtime_batch_id'],
                    config_name=row['config_name']+' · 转入单水平（LJ）'))
        for row in rows + transferred:
            row['overview_key'] = f"{row['qc_method']}:{row['runtime_batch_id']}:{row['lot_config_item_id']}"
        return rows + transferred


def _record(row, method, levels):
    payload = _json(row.get('evaluation_json'))
    data = payload.get('result',payload) if method=='lj' else payload.get('run',payload)
    phase = data.get('phase',row.get('phase'))
    phase = 'formal' if phase in ('formal_qc','正式数据') else 'building' if phase in ('target_building','建靶数据','待建靶') else 'unknown'
    classification = data.get('status') if method=='lj' else data.get('run_status',row.get('run_status'))
    classification = {'符合质控':'accept','在控':'accept','失控':'reject','警告':'warning'}.get(classification,classification)
    if phase!='formal': classification=None
    item = dict(source_type=TABLES[method][2],source_id=row['id'],test_time=row['test_time'],phase=phase,
        classification=classification,conclusion=CLASS_LABELS.get(classification,'尚待判读') if phase=='formal' else '参数建立期' if phase=='building' else '判读依据待核实',
        context_id=row.get('context_id'),evaluation_id=row.get('evaluation_id'),operator=row.get('operator',''),
        manual_note=row.get('manual_note',''),levels=levels)
    item['values'] = [{'level_order':r.get('level_order',index+1),'value':r.get('log_value') if row.get('input_value_type')=='log' else r.get('raw_value',r.get('value')),
                       'lot_no':r.get('lot_no',''),'level_name':r.get('level_name',''),'qc_level_id':r.get('qc_level_id')} for index,r in enumerate(levels)]
    return item


def _separate_quality_versions(connection,method,batch_id,month,quality):
    if method not in ('lj','zscore'):return quality
    first=date.fromisoformat(month+'-01')
    after=date(first.year+(first.month==12),first.month%12+1,1)
    table,column,_=TABLES[method]
    records=[dict(row) for row in connection.execute(f'''SELECT r.*,x.target_profile_id,e.evaluation_json
        FROM {table} r JOIN qc_result_contexts x ON x.{column}=r.id
        LEFT JOIN qc_result_evaluations e ON e.id=(SELECT MAX(id) FROM qc_result_evaluations WHERE context_id=x.id)
        WHERE r.batch_id=? AND datetime(r.test_time)>=datetime(?) AND datetime(r.test_time)<datetime(?)''',
        (batch_id,first.isoformat(),after.isoformat()))]
    profiles={r['target_profile_id'] for r in records if _record(r,method,[])['phase']=='formal'}
    if len(profiles)>1:
        return {**quality,'rows':[],
            'evaluation_reason':'本月包含多个均值和标准差版本，未合并作质量达标评价；请在月报查看按参数版本分段的结果。',
            'statistics_scope':'本月完整正式期记录；参数版本分别评价，不混合计算。'}
    return quality


def get_daily_overview(day=None, *, lab_instrument_id=None, template_id=None, qc_material_id=None, qc_method=None, search=''):
    started=perf_counter()
    day=date.fromisoformat(str(day or date.today())[:10]);start=datetime.combine(day,time.min);end=start+timedelta(days=1)
    bindings=list_overview_bindings()
    rows=[r for r in bindings if (lab_instrument_id is None or r['lab_instrument_id']==int(lab_instrument_id))
          and (template_id is None or r['template_id']==int(template_id)) and (qc_material_id is None or r['qc_material_id']==int(qc_material_id))
          and (not qc_method or r['qc_method']==qc_method) and fuzzy_match(search,r['template_name'],r['test_item_name'],r['test_item_abbreviation'],r['instrument_name'],r['material_name'])]
    month=day.strftime('%Y-%m');items=[]
    with read_snapshot() as c:
        for binding in rows:
            item=dict(binding);method=item['qc_method'];bid=item['runtime_batch_id'];records=[]
            item.update(records=records,count=0,ever_reject=False,latest=None,phase='请先确认项目批次后再录入',quality={'period':month,'rows':[]},read_error='')
            if bid is None:
                items.append(item);continue
            table,column,_=TABLES[method]
            source_rows=[dict(r) for r in c.execute(f'''SELECT r.*,x.id context_id,x.config_snapshot_json,x.provenance,
              e.id evaluation_id,e.evaluation_json FROM {table} r LEFT JOIN qc_result_contexts x ON x.{column}=r.id
              LEFT JOIN qc_result_evaluations e ON e.id=(SELECT MAX(e2.id) FROM qc_result_evaluations e2 WHERE e2.context_id=x.id)
              WHERE r.batch_id=? AND datetime(r.test_time)>=datetime(?) AND datetime(r.test_time)<datetime(?) ORDER BY datetime(r.test_time),r.id''',
              (bid,start.isoformat(' '),end.isoformat(' ')))]
            for source in source_rows:
                # Transfer copies retain the original measurement time. Their
                # instant originals already contribute to that day's count.
                if method == 'lj' and source.get('provenance') == 'instant_transfer':
                    continue
                config=_json(source.get('config_snapshot_json'))
                if config.get('record_type','routine')!='routine' or config.get('purpose','routine') not in ('routine',''):continue
                source['input_value_type']=binding['input_value_type']
                material=[dict(r) for r in c.execute('SELECT * FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order',(source.get('context_id'),))]
                raw=[dict(r) for r in c.execute('SELECT * FROM zscore_level_results WHERE run_id=? ORDER BY level_id,id',(source['id'],))] if method=='zscore' else [dict(source)]
                for n,level in enumerate(raw):
                    level.update(material[n] if n<len(material) else {})
                records.append(_record(source,method,raw))
            if method=='instant':
                from services.instant_service import analyze_instant_results
                history=get_instant_results(bid,include_manual_note=True)
                history=history[history['test_time']<end]
                _,summary=analyze_instant_results(history)
                item['instant_summary']=summary
                saved={r['id']:_json(r.get('evaluation_json')) for r in source_rows}
                for record in records:
                    evaluation=saved.get(record['source_id'],{})
                    sequence=evaluation.get('effective_sequence')
                    conclusion='判读依据待核实'
                    if evaluation:
                        if not evaluation.get('is_effective',1):conclusion='已禁用'
                        elif evaluation.get('is_outlier_suspect'):conclusion='疑似离群，待复核'
                        elif sequence is not None and int(sequence)<3:conclusion='尚不足3个有效点'
                        else:conclusion='即时法检验'
                    record.update(phase='instant',classification=None,conclusion=conclusion,
                        effective_sequence=sequence,analysis_prompt=evaluation.get('analysis_prompt',''))
                item['phase']='即时法过渡期'
                item['stage_note']=summary.get('transfer_message') or f"{summary['effective_count']} 个有效点"
            elif records:
                item['phase']={'formal':'正式期','building':'参数建立期','unknown':'判读依据待核实'}[records[-1]['phase']]
            else:
                item['phase']='今日无记录'
            item.update(count=len(records),latest=records[-1] if records else None,
                        ever_reject=any(r['phase']=='formal' and r['classification']=='reject' for r in records))
            try:
                quality=batch_quality_summary(method,bid,month) or {}
                quality=_separate_quality_versions(c,method,bid,month,quality)
                item['quality']={**quality,'period':month,'rows':quality.get('rows',[])}
            except Exception:
                logging.getLogger(__name__).exception('Daily CV read failed for %s/%s',method,bid)
                item['quality']={'period':month,'rows':[],'error':'质量评价读取失败，请重试。'}
            items.append(item)
    pending=list_pending(start_date=day.isoformat(),end_date=day.isoformat(),instrument_id=lab_instrument_id)
    references={(i['qc_method'],i['runtime_batch_id']):i for i in list_overview_bindings(include_disabled=True)}
    def relevant(row):
        origin=row['origin_snapshot'];source=origin.get('config_snapshot') or {}
        reference=references.get((origin['qc_method'],origin['batch_id']),{})
        return ((template_id is None or reference.get('template_id')==int(template_id))
            and (qc_material_id is None or source.get('qc_material_id',reference.get('qc_material_id'))==int(qc_material_id))
            and (not qc_method or origin['qc_method']==qc_method)
            and fuzzy_match(search,reference.get('template_name',''),origin.get('project_name',''),
                source.get('test_item_name',''),source.get('test_item_abbreviation',''),
                origin.get('instrument_name',''),source.get('qc_material_name','')))
    pending={**pending,'items':[r for r in pending['items'] if relevant(r)],'cross_day':[r for r in pending['cross_day'] if relevant(r)],
             'verification_gaps':[r for r in pending['verification_gaps'] if relevant(r)]}
    all_pending=pending['items']+pending['cross_day']
    pending.update(count=len(all_pending),total_count=len(all_pending),pending_count=len(all_pending))
    for item in items:
        item['pending']=[r for r in all_pending if (r['origin_snapshot']['qc_method'],r['origin_snapshot']['batch_id'])==(item['qc_method'],item['runtime_batch_id'])]
    return {'date':day.isoformat(),'items':items,'pending':pending,'count':sum(i['count'] for i in items),
            'missing_rate':None,'expected_count':None,'quality_period':month,'elapsed_ms':round((perf_counter()-started)*1000,2)}
