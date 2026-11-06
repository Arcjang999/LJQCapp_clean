"""Explicit resources shared by the macOS build specifications."""
from pathlib import Path


def application_datas(root: Path) -> list[tuple[str, str]]:
    result = []
    for name in ("app.py", "database.py", "import_review.py", "plotting.py", "qc_logic.py",
                 "zscore_logic.py", "zscore_plotting.py", "release.json"):
        result.append((str(root / name), "."))
    for name in ("migrations", "pages", "services", "ui", "data/dictionaries", "data/catalogs", "resources"):
        for path in sorted((root / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                result.append((str(path), str(path.parent.relative_to(root))))
    for name in ("OPERATION_GUIDE.md", "current_user_guide.md"):
        result.append((str(root / "docs" / name), "docs"))
    for path in sorted((root / "docs/images/first-use").glob("*")):
        if path.is_file():
            result.append((str(path), "docs/images/first-use"))
    return result
