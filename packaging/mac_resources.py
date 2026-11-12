"""Public resources for native macOS desktop programs."""
from pathlib import Path

from protected_runtime import application_datas as protected_application_datas, protected_runtime


def application_datas(root: Path) -> list[tuple[str, str]]:
    runtime, _ = protected_runtime(root)
    return protected_application_datas(root, runtime)
