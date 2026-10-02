"""Session drafts and strict rectangular paste mapping; never writes business data."""
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
import csv
import io


def new_draft(context, *, operator='', test_time=None):
    return {'draft_id':uuid4().hex,'submission_id':uuid4().hex,'context':deepcopy(context),
            'test_time':test_time or context.get('test_time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'operator':operator,
            'selected':[str(i['lot_config_item_id']) for i in context['items']],
            'values':{row_key(i,l):'' for i in context['items'] for l in i['levels']},
            'notes':{str(i['lot_config_item_id']):'' for i in context['items']},
            'reagents':{str(i['lot_config_item_id']):i.get('suggested_reagent_lot_id') for i in context['items']},
            'allow_same_time':False,'edit_version':0,'errors':[],'frozen':None}


def row_key(item,level):
    return f"{item['lot_config_item_id']}:{level['qc_level_id']}"


def draft_rows(draft, *, selected_only=False):
    rows=[]
    for item in draft['context']['items']:
        if selected_only and str(item['lot_config_item_id']) not in draft['selected']:continue
        for level in item['levels']:
            key=row_key(item,level)
            rows.append({'key':key,'item':item,'level':level,'value':draft['values'].get(key,'')})
    return rows


def parse_rectangular_paste(text, rows):
    """Exactly one value column, or value+note columns. Empty cells stay empty."""
    if not text:return {'valid':False,'errors':['请粘贴检测值。'],'rows':[]}
    parsed=list(csv.reader(io.StringIO(text),delimiter='\t'))
    errors=[]
    if len(parsed)!=len(rows):errors.append(f'共有 {len(parsed)} 行，当前需要 {len(rows)} 行；请核对全部水平。')
    width=len(parsed[0]) if parsed else 0
    if width not in (1,2):errors.append('只接受检测值一列，或检测值与备注两列。')
    proposed=[];notes={}
    for index,cells in enumerate(parsed):
        if len(cells)!=width:errors.append(f'第 {index+1} 行列数不一致。')
        if len(cells)>2:errors.append(f'第 {index+1} 行多出列，未截取。')
        if index<len(rows):
            item_key=rows[index]['key'].split(':')[0]
            if len(cells)==2:
                if item_key in notes and notes[item_key]!=cells[1]:errors.append(f'第 {index+1} 行：同一多水平检测的备注不一致。')
                notes[item_key]=cells[1]
            if cells and 'item' in rows[index]:
                from services.value_type_service import parse_project_input_value
                _,_,error=parse_project_input_value(cells[0],rows[index]['item']['input_value_type'])
                if error:errors.append(f'第 {index+1} 行：{error}')
            proposed.append({'key':rows[index]['key'],'value':cells[0] if cells else '',
                             'note':cells[1] if len(cells)==2 else None,'line':index+1})
    return {'valid':not errors,'errors':errors,'rows':proposed}


def apply_paste(draft,preview):
    if not preview['valid']:raise ValueError('请先修正粘贴的行列数量。')
    current=[r['key'] for r in draft_rows(draft,selected_only=True)]
    if (preview.get('context_revision',draft['context']['context_revision'])!=draft['context']['context_revision']
            or preview.get('all_row_keys',current)!=current or any(r['key'] not in current for r in preview['rows'])):
        raise ValueError('项目或材料已经改变，请重新预览粘贴内容。')
    for row in preview['rows']:
        draft['values'][row['key']]=row['value']
        if row['note'] is not None:draft['notes'][row['key'].split(':')[0]]=row['note']
    draft['edit_version']+=1;draft['frozen']=None


def build_request(draft):
    items=[]
    for item in draft['context']['items']:
        key=str(item['lot_config_item_id'])
        if key not in draft['selected']:continue
        items.append({'row_key':key,'lot_config_item_id':item['lot_config_item_id'],
            'levels':[{'qc_level_id':level['qc_level_id'],'value':draft['values'].get(row_key(item,level),'')} for level in item['levels']],
            'reagent_lot_id':draft['reagents'].get(key),'manual_note':draft['notes'].get(key,''),
            'allow_same_time':draft['allow_same_time']})
    return {'submission_id':draft['submission_id'],'selection':deepcopy(draft['context']['selection']),
            'context_revision':draft['context']['context_revision'],'test_time':draft['test_time'],'operator':draft['operator'],
            'purpose':'routine','items':items}
