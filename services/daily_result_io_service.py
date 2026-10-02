"""Daily result files. Hidden identity metadata is validated, never trusted as authority."""
from copy import deepcopy
from hashlib import sha256
import io
import json

import pandas as pd
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET
from services.export_utils import dataframes_to_xlsx_bytes,xlsx_bytes_to_dataframes
from services.daily_draft_service import draft_rows
from services.value_type_service import get_input_value_type_label

COLUMNS=['仪器','项目','检验项目','实际质控品批号','水平','实际试剂批号','检测时间','检测人','检测值','输入类型','记录用途','备注']
FORMAT='LJQC-daily-routine-1'


def _labels(row,draft):
    item,level=row['item'],row['level'];key=str(item['lot_config_item_id'])
    reagent=next((r for r in item.get('reagent_options',[]) if r['id']==draft['reagents'].get(key)),{})
    return [item['instrument_name'],item.get('project_name',''),item['test_item_name'],level['lot_no'],level.get('level_name',''),
            reagent.get('lot_no',''),draft['test_time'],draft['operator'],row['value'],get_input_value_type_label(item['input_value_type']),'常规质控',draft['notes'].get(key,'')]


def export_daily_workbook(draft, *, template=False):
    records=[];metadata=[[FORMAT,json.dumps(draft['context']['selection'],ensure_ascii=False)]]
    for index,row in enumerate(draft_rows(draft,selected_only=True),2):
        labels=_labels(row,draft)
        if template:labels[8]='';labels[11]=''
        records.append(labels)
        metadata.append([index,json.dumps({'row_key':row['key'],'lot_config_item_id':row['item']['lot_config_item_id'],
            'qc_level_id':row['level']['qc_level_id'],'input_value_type':row['item']['input_value_type'],
            'unit_id':row['item'].get('unit_id'),'reagent_lot_id':draft['reagents'].get(str(row['item']['lot_config_item_id']))},ensure_ascii=False)])
    data=dataframes_to_xlsx_bytes({'常规质控结果':pd.DataFrame(records,columns=COLUMNS),'关系':pd.DataFrame(metadata,columns=['格式','定位'])})
    out=io.BytesIO()
    with ZipFile(io.BytesIO(data)) as source,ZipFile(out,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            content=source.read(name)
            if name=='xl/workbook.xml':
                root=ET.fromstring(content)
                for sheet in root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet'):
                    if sheet.attrib.get('name')=='关系':sheet.set('state','hidden')
                content=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            target.writestr(name,content)
    return out.getvalue()


def preview_daily_workbook(data,draft):
    errors=[];changes=[];digest=sha256(data).hexdigest()
    try:
        book=xlsx_bytes_to_dataframes(data)
        if list(book)!=['常规质控结果','关系']:raise ValueError('请使用本页常规质控结果模板，配置文件不能在此导入。')
        sheet=book['常规质控结果'];metadata=book['关系'].values.tolist()
        if not metadata or metadata[0][0]!=FORMAT:raise ValueError('文件用途不匹配，请重新下载本组结果模板。')
        if json.loads(metadata[0][1])!=draft['context']['selection']:raise ValueError('文件与当前仪器、质控品或实际批次不一致。')
        data_rows=[list(sheet.columns)]+sheet.values.tolist()
        if list(data_rows[0])!=COLUMNS:raise ValueError('表头或列数发生变化，请使用原模板。')
        rows=draft_rows(draft,selected_only=True)
        by_key={r['key']:r for r in rows};seen=set();times=set();operators=set();item_notes={};item_reagents={}
        if len(data_rows)-1!=len(rows) or len(metadata)-1!=len(rows):errors.append('文件行数与当前整组项目及全部水平不一致。')
        for line,values in enumerate(data_rows[1:],2):
            if len(values)!=len(COLUMNS):errors.append(f'第{line}行列数不一致。');continue
            try:identity=json.loads(metadata[line-1][1]);key=identity['row_key'];row=by_key[key]
            except (IndexError,KeyError,TypeError,ValueError):errors.append(f'第{line}行无法明确对应当前检测项与水平。');continue
            if key in seen:errors.append(f'第{line}行重复检测项与水平。');continue
            seen.add(key);expected=_labels(row,draft);item=row['item'];level=row['level']
            if identity['lot_config_item_id']!=item['lot_config_item_id'] or identity['qc_level_id']!=level['qc_level_id'] or identity.get('unit_id')!=item.get('unit_id') or identity.get('input_value_type')!=item['input_value_type']:
                errors.append(f'第{line}行项目、尺度或单位不一致。')
            for index in (0,1,2,3,4,9,10):
                if str(values[index] or '')!=str(expected[index] or ''):errors.append(f'第{line}行{COLUMNS[index]}不一致，请在原配置入口核对。')
            reagent=[r for r in item.get('reagent_options',[]) if r['lot_no']==str(values[5] or '')]
            if len(reagent)!=1:errors.append(f'第{line}行实际试剂批号无法唯一对应可用批号。')
            when=values[6].strftime('%Y-%m-%d %H:%M:%S') if hasattr(values[6],'strftime') else str(values[6] or '')
            operator=str(values[7] or '').strip();times.add(when);operators.add(operator)
            note=str(values[11] or '');ik=str(item['lot_config_item_id'])
            if ik in item_notes and item_notes[ik]!=note:errors.append(f'第{line}行同一多水平检测的备注不一致。')
            item_notes[ik]=note
            reagent_id=reagent[0]['id'] if len(reagent)==1 else None
            if ik in item_reagents and item_reagents[ik]!=reagent_id:
                errors.append(f'第{line}行同一多水平检测的实际试剂批号不一致。')
            item_reagents[ik]=reagent_id
            from services.value_type_service import parse_project_input_value
            _,_,value_error=parse_project_input_value(str(values[8]) if values[8] is not None else '',item['input_value_type'])
            if value_error:errors.append(f'第{line}行：{value_error}')
            changes.append({'line':line,'key':key,'value':str(values[8]) if values[8] is not None else '',
                            'note':note,'reagent_lot_id':reagent_id,
                            'test_item_name':item['test_item_name'],'level_name':level.get('level_name','')})
        if seen!=set(by_key):errors.append('文件缺少完整项目或质控水平。')
        if len(times)!=1 or not next(iter(times),''):errors.append('本次整组须填写同一有效检测时间。')
        if len(times)==1:
            from services.lot_lifecycle_service import timestamp
            try:timestamp(next(iter(times)))
            except (ValueError,TypeError):errors.append('检测时间格式无效，请按年-月-日 时:分:秒填写。')
        if len(operators)!=1 or not next(iter(operators),''):errors.append('本次整组须填写同一检测人。')
        return {'valid':not errors,'errors':errors,'rows':changes,'file_hash':digest,
                'context_revision':draft['context']['context_revision'],'selected':list(draft['selected']),
                'test_time':next(iter(times),'') if len(times)==1 else '', 'operator':next(iter(operators),'') if len(operators)==1 else ''}
    except Exception as exc:
        return {'valid':False,'errors':[str(exc) if isinstance(exc,ValueError) else '无法读取结果文件，请检查是否为本页模板。'],'rows':[],'file_hash':digest}


def apply_daily_workbook(draft,preview):
    if not preview['valid']:raise ValueError('请修正文件问题后重新预览。')
    if preview.get('context_revision')!=draft['context']['context_revision'] or preview.get('selected')!=draft['selected']:
        raise ValueError('本组项目或材料已变化，请重新预览结果文件。')
    for row in preview['rows']:
        draft['values'][row['key']]=row['value'];key=row['key'].split(':')[0]
        draft['notes'][key]=row['note'];draft['reagents'][key]=row['reagent_lot_id']
    draft.update(test_time=preview['test_time'],operator=preview['operator'],frozen=None)
    draft['edit_version']+=1
