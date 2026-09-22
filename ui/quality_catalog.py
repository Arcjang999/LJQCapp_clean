"""A test-item-first catalog for the laboratory's current quality requirements."""
import pandas as pd
import streamlit as st

from services.quality_catalog_service import browse_quality_catalog, catalog_display_rows, CATEGORIES
from services.search_service import SEARCH_HELP
from ui.quality_targets import render_spec


def render_quality_catalog():
    st.caption('按检验项目查找现行质量要求，核对检测方法和适用条件后采用。')
    section = st.radio('查看内容', ['projects', 'general'],
        format_func=lambda v: {'projects':'检验项目要求', 'general':'通用质控要求'}[v],
        horizontal=True, key='quality_catalog_section')
    query = st.text_input('搜索检验项目或标准', key='quality_search',
        placeholder='例如：HBV DNA、乙肝核酸、IgG、免球蛋白', help=SEARCH_HELP)
    category = ''
    if section == 'projects':
        category = st.radio('检验类别', ['', *CATEGORIES],
            format_func=lambda v: v or '全部', horizontal=True, key='quality_category_filter')
    records = browse_quality_catalog(query, category=category, section=section)
    if section == 'projects' and not query and not category:
        counts = {c: sum(r['category'] == c for r in records) for c in CATEGORIES}
        st.caption(' · '.join(f'{c} {n} 条' for c,n in counts.items() if n))
    else:
        st.caption(f'找到 {len(records)} 条要求')
    if not records:
        st.info('未找到匹配的现行要求。请调整关键词或检验类别；仍未找到时，可在质量目标设置中补充依据。')
        return
    frame = pd.DataFrame(catalog_display_rows(records))
    st.dataframe(frame, hide_index=True, width='stretch', height=min(360, 36 * (len(frame) + 1) + 4))
    st.download_button('导出查询结果', frame.to_csv(index=False).encode('utf-8-sig'),
        '现行质量要求.csv', 'text/csv', key='quality_catalog_export')
    by_id = {r['id']: r for r in records}
    key = 'quality_catalog_detail'
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = None
    selected = st.selectbox('查看要求详情', [None, *by_id], key=key, filter_mode='fuzzy',
        format_func=lambda v: '请选择要求' if v is None else by_id[v]['name']+'｜'+by_id[v]['standard'])
    if selected:
        row = by_id[selected]
        st.markdown('**'+row['name']+'**')
        if row['kind'] in ('numeric', 'custom'):
            render_spec(row['spec'])
        else:
            st.caption(row['source']['standard']+'｜'+row['source']['name'])
            st.write('适用条件：'+row['scope'])
            for requirement in row['spec']['requirements']:
                st.write(requirement)
            st.caption('请按以上要求人工核对，并记录对照结果和处理情况。')
            st.caption('条款：'+row['spec']['source_clause']+'｜原文第 '+str(row['spec']['source_page'])+' 页（含封面）')
            st.link_button('查看依据原文', row['source']['source_url'])
            for related in row.get('related_sources', []):
                st.markdown('**'+related['name']+'**')
                st.caption(related['source_type'])
                for rule in row.get('related_rules', []):
                    if rule['source_id'] != related['id']:
                        continue
                    for requirement in rule['requirements']:
                        st.write(requirement)
                    st.caption('条款：'+rule['source_clause']+'｜原文第 '+rule['source_page']+' 页（含封面）')
                st.link_button('查看 '+related['standard']+' 原文', related['source_url'])
