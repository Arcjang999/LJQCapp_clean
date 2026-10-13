"""Both help views read the same maintained operational guide."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import streamlit as st


@dataclass(frozen=True)
class OperationGuide:
    title: str
    body: str
    introduction: str
    sections: tuple[tuple[str, str], ...]
    resource_dir: Path


def load_operation_guide(path: Path | None = None) -> OperationGuide:
    """Read source or bundled help without retaining an outdated text cache."""
    guide_path = path if path is not None else Path(__file__).resolve().parents[1] / 'docs' / 'OPERATION_GUIDE.md'
    content = guide_path.read_text(encoding='utf-8').strip()
    first, separator, body = content.partition('\n')
    if not first.startswith('# ') or not separator:
        raise ValueError('操作说明缺少标题或正文。')
    body = body.strip()
    headings = list(re.finditer(r'^## (.+)$', body, re.MULTILINE))
    if not headings:
        raise ValueError('操作说明缺少操作章节。')
    sections = tuple(
        (heading.group(1).strip(), body[heading.end():headings[index + 1].start()
            if index + 1 < len(headings) else len(body)].strip())
        for index, heading in enumerate(headings)
    )
    if any(not title or not text for title, text in sections) or len({title for title, _ in sections}) != len(sections):
        raise ValueError('操作说明存在空章节或重复标题。')
    return OperationGuide(first[2:].strip(), body, body[:headings[0].start()].strip(), sections,
                          guide_path.resolve().parent)


def _help_image_path(resource_dir: Path, reference: str) -> Path:
    """Only local image files underneath the help document directory are read."""
    relative = Path(reference)
    if relative.is_absolute() or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', reference):
        raise ValueError('帮助图片须使用本地相对路径。')
    candidate = (resource_dir / relative).resolve()
    if not candidate.is_relative_to(resource_dir.resolve()) or candidate.suffix.lower() not in {
            '.png', '.jpg', '.jpeg', '.gif', '.webp'}:
        raise ValueError('帮助图片路径无效。')
    return candidate


def render_guide_content(content: str, *, resource_dir: Path) -> None:
    """Keep text and local screenshots in their document order in both views."""
    position = 0
    for match in re.finditer(r'^!\[([^\]]*)\]\(([^\n)]+)\)\s*$', content, re.MULTILINE):
        text = content[position:match.start()].strip()
        if text:
            st.markdown(text)
        alt, reference = match.groups()
        try:
            image_path = _help_image_path(resource_dir, reference.strip())
            st.image(image_path.read_bytes(), caption=alt or None, width=1400)
        except (OSError, ValueError):
            st.caption(f'{alt or "操作截图"}暂时无法显示，请按下方步骤操作。')
        position = match.end()
    text = content[position:].strip()
    if text:
        st.markdown(text)


def _read_help() -> OperationGuide | None:
    try:
        return load_operation_guide()
    except (OSError, ValueError):
        st.error('帮助资料暂时无法读取，请重新打开软件。仍无法显示时联系维护人员。')
        return None


def render_frontline_user_guide(guide: OperationGuide | None = None) -> None:
    """Show detailed sections from the same file used by the full help view."""
    guide = guide if guide is not None else _read_help()
    if guide is None:
        return
    if guide.introduction:
        render_guide_content(guide.introduction, resource_dir=guide.resource_dir)
    for index, (title, content) in enumerate(guide.sections):
        with st.expander(title, expanded=index == 0):
            render_guide_content(content, resource_dir=guide.resource_dir)


def render_user_guide_page() -> None:
    from ui.common import render_module_header

    if st.button('返回项目工作台', key='user_guide_home'):
        from ui.daily_navigation import return_to_project_workspace
        return_to_project_workspace()
    practical = bool(st.session_state.get('user_guide_practical'))
    render_module_header('操作说明' if practical else '使用说明',
        caption='先准备自己的资料，再建项、录入、处理失控、报告归档与备份。',
        tone='guide', eyebrow='操作帮助')
    guide = _read_help()
    if guide is None:
        return
    if practical:
        render_guide_content(guide.body, resource_dir=guide.resource_dir)
    else:
        render_frontline_user_guide(guide)
