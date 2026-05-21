from __future__ import annotations

from contextlib import contextmanager
import logging
import os
import time
from typing import Iterator


_TRUE_VALUES = {"1", "true", "yes", "on", "debug"}


def profiling_enabled() -> bool:
    return os.getenv("LJQC_PROFILE", "").strip().casefold() in _TRUE_VALUES


@contextmanager
def profile_timer(name: str, **fields: object) -> Iterator[None]:
    if not profiling_enabled():
        yield
        return

    start_time = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        field_text = " ".join(
            f"{key}={value}" for key, value in fields.items() if value is not None
        )
        suffix = f" {field_text}" if field_text else ""
        logging.getLogger("ljqc.profile").warning("[perf] %s %.2fms%s", name, elapsed_ms, suffix)
