"""Shared fuzzy retrieval for UI lists. Never use this for identity or QC rules."""
from __future__ import annotations

from functools import lru_cache
import unicodedata

SEARCH_HELP = '支持部分名称、空格分隔的多个关键词、大小写及全半角混输，也可匹配少量漏字或拼写差异。'


@lru_cache(maxsize=8192)
def normalize_search(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _one_edit(left: str, right: str) -> bool:
    """One insertion, deletion, substitution or adjacent transposition."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        changed = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(changed) <= 1 or (len(changed) == 2 and changed[1] == changed[0] + 1
            and left[changed[0]] == right[changed[1]] and left[changed[1]] == right[changed[0]])
    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = 0
    while i < len(short) and short[i] == long[i]:
        i += 1
    return short[i:] == long[i + 1:]


def _token_matches(token: str, fields: tuple[str, ...]) -> bool:
    if any(token in field for field in fields):
        return True
    # Codes and short abbreviations remain literal: HBV must not retrieve HCV.
    if any(c.isdigit() for c in token):
        return False
    chinese = all('\u3400' <= c <= '\u9fff' for c in token)
    if chinese and len(token) >= 2:
        for field in fields:
            for start, char in enumerate(field):
                if char != token[0]:
                    continue
                end = start
                for char in token[1:]:
                    end = field.find(char, end + 1)
                    if end < 0 or end - start + 1 > len(token) + 2:
                        break
                else:
                    return True
    if token.isascii() and token.isalpha() and 4 <= len(token) <= 32:
        for field in fields:
            for length in (len(token) - 1, len(token), len(token) + 1):
                if any(_one_edit(token, field[start:start + length])
                       for start in range(len(field) - length + 1)):
                    return True
    return False


def fuzzy_match(query: object, *values: object) -> bool:
    """AND keywords across fields; punctuation is literal, never regex or SQL."""
    raw = str(query or '').strip()
    if not raw:
        return True
    fields = tuple(normalize_search(str(value)) for value in values if value is not None)
    tokens = tuple(filter(None, (normalize_search(part) for part in raw.split())))
    if not tokens:
        return any(unicodedata.normalize('NFKC', raw).casefold() in
                   unicodedata.normalize('NFKC', str(value)).casefold()
                   for value in values if value is not None)
    return all(_token_matches(token, fields) for token in tokens)


def filter_frame(frame, query: object, fields=None):
    if frame.empty or not str(query or '').strip():
        return frame
    fields = list(fields) if fields is not None else list(frame.columns)
    mask = frame[fields].fillna('').apply(lambda row: fuzzy_match(query, *row), axis=1)
    return frame.loc[mask]
