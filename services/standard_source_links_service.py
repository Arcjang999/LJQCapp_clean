"""Display destinations for standards, separate from frozen quality evidence."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


# Exact domestic records checked on 2026-11-09. These supplement the source
# URL recorded with a requirement; they never replace its adopted evidence.
_DOMESTIC_RECORDS = {
    'wst230-2024': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=19F7C41D0A4A4F14E06397BE0A0A9EF9',
    'wst403-2024': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=19F7C41D0A4F4F14E06397BE0A0A9EF9',
    'wst406-2024': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=19F7C41D0A504F14E06397BE0A0A9EF9',
    'wst494-2017': 'https://std.samr.gov.cn/hb/search/stdHBDetailedCNF?id=8B1827F24FF4BB19E05397BE0A0AB44A',
    'wst641-2018': 'https://std.samr.gov.cn/hb/search/stdHBDetailedCNF?id=8B1827F149DCBB19E05397BE0A0AB44A',
    'wst641-2026': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=58FED2B32E43886BE06397BE0A0A5BB3',
    'wst645.1-2026': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=58FED2B32E44886BE06397BE0A0A5BB3',
    'wst645.2-2026': 'https://std.samr.gov.cn/hb/search/stdHBDetailed?id=58FED2B32E45886BE06397BE0A0A5BB3',
}


def _web_url(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(character.isspace() for character in parts.netloc)):
            return ''
    except ValueError:
        return ''
    return value


def _standard_id(source):
    if source.get('origin') == 'custom':
        return ''
    identifier = str(source.get('id') or '')
    match = re.search(r'WS\s*/?\s*T\s*(\d+(?:\.\d+)?)\s*[-—－]\s*(\d{4})',
                      str(source.get('standard') or ''), re.IGNORECASE)
    if not match:
        return identifier if identifier in _DOMESTIC_RECORDS else ''
    code = 'wst' + match.group(1) + '-' + match.group(2)
    if identifier == code or identifier.startswith(code + '-') or source.get('origin') == 'builtin':
        return code
    return ''


def standard_source_links(source):
    """Return the recorded original and an exact domestic information record."""
    original = _web_url(source.get('source_url'))
    links = []
    if original:
        parts = urlsplit(original)
        host = parts.hostname.lower()
        provider = ('国家卫生健康委' if host == 'nhc.gov.cn' or host.endswith('.nhc.gov.cn')
                    else '原文来源网站')
        label = '查看全文PDF' if unquote(parts.path).lower().endswith('.pdf') else '查看原文'
        links.append(dict(label=label, url=original, provider=provider))
    domestic = _DOMESTIC_RECORDS.get(_standard_id(source))
    if domestic and domestic != original:
        links.append(dict(label='国内标准信息', url=domestic,
                          provider='全国标准信息公共服务平台'))
    return links
