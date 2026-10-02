"""Return to daily views without throwing away their independent session drafts."""
import streamlit as st
from copy import deepcopy
from uuid import uuid4


def return_to_project_workspace(template_id=None):
    from ui.common import GLOBAL_PAGE_SESSION_KEYS
    for key in GLOBAL_PAGE_SESSION_KEYS:
        st.session_state[key] = False
    if template_id is not None:
        st.session_state['workspace_project_id'] = int(template_id)
    st.session_state['pending_top_level_method'] = '主页'
    st.session_state.pop('daily_return_page', None)
    st.rerun()


def prepare_project_daily_entry(template_id):
    """Switch the view's project, retaining separate drafts and their request IDs."""
    from services.project_config_service import get_project_template
    template = dict(get_project_template(int(template_id)))
    tid = int(template['id'])
    old = st.session_state.get('daily_draft')
    saved = st.session_state.setdefault('daily_saved_drafts', {})
    if old and old['context']['selection'].get('template_id') != tid:
        selection_key = str(old['context']['selection'])
        saved.pop(selection_key, None)
        saved[selection_key] = deepcopy(old)
        st.session_state.pop('daily_draft', None)
    if not st.session_state.get('daily_draft'):
        matching = [draft for draft in saved.values()
                    if draft['context']['selection'].get('template_id') == tid]
        if matching:
            st.session_state['daily_draft'] = deepcopy(matching[-1])
    st.session_state['daily_entry_template_id'] = tid
    st.session_state['workspace_project_id'] = tid
    st.session_state['entry_instrument'] = template['lab_instrument_id']
    st.session_state['entry_product'] = template['qc_material_id']
    draft = st.session_state.get('daily_draft')
    st.session_state['entry_lot'] = draft['context']['selection']['qc_material_lot_id'] if draft else None
    if draft:
        selection = draft['context']['selection']
        key = f"entry_combination_{tid}_{selection['lab_instrument_id']}_{selection['qc_material_id']}_{selection['qc_material_lot_id']}"
        st.session_state[key] = selection['lot_config_id']
    st.session_state.pop('daily_pending_context', None)
    st.session_state.pop('daily_restore_selection', None)
    return template


def open_project_daily_entry(template_id):
    from ui.common import open_global_page
    prepare_project_daily_entry(template_id)
    open_global_page('show_daily_entry_page')

SCROLL_JS = r'''
export default function(component) {
 // Streamlit v2 re-executes this function on data changes, but calls its
 // returned cleanup only when the component unmounts. Dispose the previous
 // execution ourselves, including a change from a daily page to an auxiliary.
 const registry=Symbol.for('ljqc.dailyScrollMemory.cleanup');
 if(typeof globalThis[registry]==='function')globalThis[registry]();
 delete globalThis[registry];
 const {data}=component;
 if(!data.page)return;
 const main=document.querySelector('[data-testid="stMain"]')||document.scrollingElement;
 if(!main)return;
 const key=location.pathname+':LJQC:daily-scroll:'+data.page;
 const mark=key+':restored',nonce=String(data.restore_nonce||0);
 const read=(name)=>{try{return sessionStorage.getItem(name);}catch{return null;}};
 const write=(name,value)=>{try{sessionStorage.setItem(name,value);}catch{}};
 let active=true,frame=0,restoring=false,raf=0,lastHeight=-1,stable=0,hold=false;
 const save=()=>{if(active&&!restoring&&!hold)write(key,String(main.scrollTop));};
 const userInterrupt=()=>{
  restoring=false;cancelAnimationFrame(raf);write(mark,nonce);hold=false;save();
 };
 const capture=()=>{
  // Freeze the value before a button can navigate. The old main container may
  // emit scroll=0 while React replaces its children, before cleanup runs.
  userInterrupt();save();hold=true;
 };
 const pointer=(event)=>{
  const bounds=main.getBoundingClientRect();
  const onScrollbar=event.clientX>=bounds.right-Math.max(16,main.offsetWidth-main.clientWidth)
    &&event.clientX<=bounds.right&&event.clientY>=bounds.top&&event.clientY<=bounds.bottom;
  if(onScrollbar||event.pointerType==='touch')userInterrupt();else capture();
 };
 const keyboard=(event)=>{
  if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' ','Tab'].includes(event.key))userInterrupt();
 };
 const stored=Number(read(key));
 if(read(key)!==null&&read(mark)!==nonce&&Number.isFinite(stored)){
  restoring=true;
  const restore=()=>{
   if(!active||!restoring)return;
   const height=main.scrollHeight,max=Math.max(0,height-main.clientHeight);
   stable=height===lastHeight?stable+1:0;lastHeight=height;frame++;
   if(stable>=6||frame>=120){
    main.scrollTop=Math.min(stored,max);
    if(max>=stored||frame>=120){restoring=false;write(mark,nonce);return;}
   }
   raf=requestAnimationFrame(restore);
  };
  raf=requestAnimationFrame(restore);
 }else write(mark,nonce);
 main.addEventListener('scroll',save,{passive:true});
 document.addEventListener('pointerdown',pointer,true);
 document.addEventListener('click',capture,true);
 document.addEventListener('keydown',keyboard,true);
 main.addEventListener('wheel',userInterrupt,{passive:true});
 main.addEventListener('touchstart',userInterrupt,{passive:true});
 const cleanup=()=>{
  if(!active)return;
  // Do not read scrollTop after navigation has replaced the daily DOM.
  active=false;cancelAnimationFrame(raf);
  main.removeEventListener('scroll',save);
  document.removeEventListener('pointerdown',pointer,true);
  document.removeEventListener('click',capture,true);
  document.removeEventListener('keydown',keyboard,true);
  main.removeEventListener('wheel',userInterrupt);
  main.removeEventListener('touchstart',userInterrupt);
  if(globalThis[registry]===cleanup)delete globalThis[registry];
 };
 globalThis[registry]=cleanup;
 return cleanup;
}
'''


def _render_scroll_memory():
    page=next((p for p in ('show_daily_entry_page','show_daily_overview_page') if st.session_state.get(p)),None)
    component=st.components.v2.component('daily_page_scroll_memory',html='<span hidden aria-hidden="true"></span>',js=SCROLL_JS)
    component(data={'page':page,'restore_nonce':st.session_state.get('daily_scroll_restore',{}).get(page,0)},
        key='daily_page_scroll_memory',height=0)


def remember_daily_return():
    for page in ('show_daily_entry_page','show_daily_overview_page'):
        if st.session_state.get(page):
            st.session_state['daily_return_page']=page
            restore=st.session_state.setdefault('daily_scroll_restore',{})
            # Browser sessionStorage survives a server restart; a counter can
            # collide with an earlier session's completed restoration marker.
            restore[page]=uuid4().hex
            return


def render_daily_return():
    _render_scroll_memory()
    if st.session_state.get('show_daily_entry_page') or st.session_state.get('show_daily_overview_page'):return
    page=st.session_state.get('daily_return_page')
    if page and not st.session_state.get(page):
        if st.button('返回日常录入' if page=='show_daily_entry_page' else '返回今日总览',key='daily_return'):
            from ui.common import open_global_page
            open_global_page(page)
