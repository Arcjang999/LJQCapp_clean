"""Small publication gate shared by workflow execution and its intent checks."""
from __future__ import annotations


def should_publish(*, ref: str, event: str, requested: bool, automatic_enabled: bool) -> bool:
    if ref != "refs/heads/main":
        return False
    if requested:
        return True
    return event in ("push", "schedule") and automatic_enabled
