"""Small local input table: stable identities, accessible inputs, explicit key behavior."""
from functools import partial
import streamlit as st
from services.daily_draft_service import draft_rows,parse_rectangular_paste
from services.daily_overview_service import METHOD_LABELS
from services.value_type_service import get_input_value_type_label

HTML='<div class="daily-grid"><table><thead></thead><tbody></tbody></table></div>'
CSS='''
.daily-grid{max-height:480px;overflow:auto;border:1px solid #d7dfe9;border-radius:10px;font:14px system-ui;color:#24354c}
.daily-grid table{width:100%;min-width:660px;border-collapse:separate;border-spacing:0;table-layout:fixed}
.daily-grid th{position:sticky;top:0;background:#eef3fa;padding:10px;text-align:left;z-index:1}
.daily-grid td{padding:7px 10px;border-top:1px solid #e1e6ec;overflow-wrap:anywhere;background:#fff}
.daily-grid th:first-child{width:24%}.daily-grid th:nth-child(2){width:13%}.daily-grid th:nth-child(4){width:19%}
.daily-grid th:last-child{width:15%}.daily-grid input{width:100%;min-width:55px;box-sizing:border-box;border:1px solid #bac6d6;border-radius:5px;background:#fff;color:#14243b;padding:8px;font:inherit}
.daily-grid input:focus{outline:2px solid #315587;outline-offset:1px}.daily-grid small{display:block;color:#5d6b7d;margin-top:3px}
'''
JS='''
export default function(component){
 const {parentElement,data,setStateValue}=component;
 const table=parentElement.querySelector('table'),body=table.querySelector('tbody');
 const signature=data.rows.map(r=>r.key).join('|');
 if(table.dataset.signature!==signature){
  table.dataset.signature=signature;body.replaceChildren();
  const head=table.querySelector('thead');head.replaceChildren();const tr=document.createElement('tr');
  for(const label of ['检验项目','质控方法','水平','实际批号','尺度 / 单位','检测值']){const th=document.createElement('th');th.textContent=label;tr.append(th);}head.append(tr);
  for(const row of data.rows){
   const tr=document.createElement('tr');tr.dataset.key=row.key;
   for(const text of [row.name,row.method,row.level,row.lot,row.scale+' / '+row.unit]){const td=document.createElement('td');td.textContent=text;tr.append(td);}
   const td=document.createElement('td'),input=document.createElement('input');input.type='text';input.inputMode='decimal';input.autocomplete='off';input.dataset.key=row.key;
   input.setAttribute('aria-label',row.name+' '+row.method+' '+row.level+' 检测值');input.value=row.value;td.append(input);tr.append(td);body.append(tr);
  }
 }
 const inputs=Array.from(body.querySelectorAll('input'));
 const emit=()=>setStateValue('values',Object.fromEntries(inputs.map(i=>[i.dataset.key,i.value])));
 for(let index=0;index<inputs.length;index++){
  const input=inputs[index],row=data.rows[index];
  if(input!==parentElement.activeElement && input!==document.activeElement && input.value!==row.value)input.value=row.value;
  input.onblur=emit;
  input.onkeydown=e=>{
   if(e.key==='Enter'||e.key==='Tab'){
    e.preventDefault();emit();const next=index+(e.shiftKey?-1:1);
    if(next>=0&&next<inputs.length){inputs[next].focus();inputs[next].select();}
    else input.blur();
   }
  };
  input.onpaste=e=>{
   const text=e.clipboardData.getData('text/plain');
   if(text.includes('\\n')||text.includes('\\t')){e.preventDefault();emit();setStateValue('paste',{text,start:input.dataset.key,nonce:Date.now()});}
  };
 }
}
'''
_GRID=st.components.v2.component('daily_result_grid',html=HTML,css=CSS,js=JS)


def _active_key(draft):
    return f"daily_grid_{draft['draft_id']}_{draft['edit_version']}_"+','.join(draft['selected'])


def _changed(key):
    draft=st.session_state.get('daily_draft')
    if not draft or key!=_active_key(draft):return
    state=st.session_state.get(key,{})
    values=state.get('values') or {}
    visible={row['key'] for row in draft_rows(draft,selected_only=True)}
    for identifier,value in values.items():
        if identifier in visible:draft['values'][identifier]=str(value)
    draft['frozen']=None


def _pasted(key):
    draft=st.session_state.get('daily_draft')
    if not draft or key!=_active_key(draft):return
    paste=st.session_state.get(key,{}).get('paste')
    if paste:
        rows=draft_rows(draft,selected_only=True);start=next((i for i,r in enumerate(rows) if r['key']==paste['start']),None)
        if start is None:return
        draft['paste_preview']=parse_rectangular_paste(paste['text'],rows[start:]);draft['paste_text']=paste['text']
        draft['paste_preview']['context_revision']=draft['context']['context_revision']
        draft['paste_preview']['all_row_keys']=[r['key'] for r in rows]
        draft['grid_paste_text']=paste['text'];draft['frozen']=None


def render_daily_grid(draft):
    rows=draft_rows(draft,selected_only=True)
    key=_active_key(draft)
    _GRID(data={'rows':[{'key':r['key'],'name':r['item']['test_item_name'],'method':METHOD_LABELS[r['item']['qc_method']],
            'level':r['level']['level_name'],'lot':r['level']['lot_no'],'scale':get_input_value_type_label(r['item']['input_value_type']),
            'unit':r['item']['unit_symbol'],'value':r['value']} for r in rows]},
          key=key,default={'values':{r['key']:r['value'] for r in rows},'paste':None},
          on_values_change=partial(_changed,key),on_paste_change=partial(_pasted,key))
