"""Exercise deployed fonts and real report generation without opening live data.

Run with the same unprivileged account, Python environment, and MPLCONFIGDIR as
the application, for example::

    .venv/bin/python scripts/verify_cloud_runtime.py --output-dir /tmp/qc-acceptance

The only database used is created in TemporaryDirectory and removed on exit.
The PDFs, font proof, and JSON results remain in the requested output directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import warnings


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["MPLBACKEND"] = "Agg"

FONT_SAMPLE = "实验室质控检测报告：建靶、在控、警告、失控；水平 ±σ −123.45"


def _runtime_details() -> dict:
    packages = {}
    for package in ("streamlit", "pandas", "numpy", "matplotlib", "pypdf"):
        packages[package] = version(package)
    details = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "packages": packages,
        "display": os.environ.get("DISPLAY", ""),
        "mplconfigdir": os.environ.get("MPLCONFIGDIR", ""),
    }
    if Path("/proc/meminfo").exists():
        details["host_memory_kib"] = {
            name: int(value.split()[0])
            for line in Path("/proc/meminfo").read_text().splitlines()
            for name, value in [line.split(":", 1)]
            if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        }
    return details


def _memory_details() -> dict:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    peak_bytes = peak if sys.platform == "darwin" else peak * 1024
    details = {"process_peak_rss_mib": round(peak_bytes / 1024**2, 2)}
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                details["process_current_rss_mib"] = round(int(line.split()[1]) / 1024, 2)
    details["measurement_scope"] = "One sequential isolated report verification process; not a concurrency capacity measurement."
    return details


@contextmanager
def _isolated_database():
    import database

    attribute_names = (
        "PROJECT_DATA_DIR", "PROJECT_DB_PATH", "PROJECT_LEGACY_DB_PATH",
        "DATA_DIR", "DEFAULT_DB_PATH", "STORAGE_CONFIG_PATH",
        "LEGACY_DB_CANDIDATES", "DB_PATH",
    )
    original = {name: getattr(database, name) for name in attribute_names}
    with TemporaryDirectory(prefix="ljqc-cloud-verification-") as directory:
        folder = Path(directory)
        path = folder / "verification.db"
        database.PROJECT_DATA_DIR = database.DATA_DIR = folder
        database.PROJECT_DB_PATH = database.DEFAULT_DB_PATH = database.DB_PATH = path
        database.PROJECT_LEGACY_DB_PATH = folder / "unused-legacy.db"
        database.STORAGE_CONFIG_PATH = folder / "storage_config.json"
        database.LEGACY_DB_CANDIDATES = []
        try:
            database.init_db()
            yield path
            if database.get_db_path().resolve() != path.resolve():
                raise AssertionError("Verification changed its isolated database path")
        finally:
            for name, value in original.items():
                setattr(database, name, value)


def _font_proof(output_dir: Path) -> dict:
    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ft2font import FT2Font
    import plotting
    import zscore_plotting
    from services.report_pdf_layout import _build_pdf_rc_params
    from services.report_service import _resolve_pdf_font_name

    result = {"backend": matplotlib.get_backend(), "contexts": {}}
    if str(matplotlib.get_backend()).lower() != "agg":
        raise AssertionError(f"Expected headless Agg backend, got {matplotlib.get_backend()}")
    contexts = {
        "lj_chart": {"font.family": "sans-serif", "font.sans-serif": plotting.CONFIGURED_FONT_FALLBACKS},
        "zscore_chart": {"font.family": "sans-serif", "font.sans-serif": zscore_plotting.CONFIGURED_FONT_FALLBACKS},
        "report": _build_pdf_rc_params(_resolve_pdf_font_name()),
    }
    figure = None
    try:
        for name, params in contexts.items():
            with plt.rc_context(params):
                figure, axis = plt.subplots(figsize=(10, 2.2), dpi=140)
                text = axis.text(0.02, 0.5, FONT_SAMPLE, va="center", fontsize=15)
                axis.set_axis_off()
                # Resolve the actual Text object's FontProperties, not just a
                # configured font name or fontconfig's preferred font.
                chosen_path = font_manager.findfont(text.get_fontproperties(), fallback_to_default=False)
                charmap = FT2Font(chosen_path).get_charmap()
                missing = sorted({character for character in FONT_SAMPLE if not character.isspace() and ord(character) not in charmap})
                if missing:
                    raise AssertionError(f"{name}: actual font {chosen_path} lacks {''.join(missing)!r}")
                figure.canvas.draw()
                proof_path = output_dir / f"font-proof-{name}.png"
                figure.savefig(proof_path, bbox_inches="tight")
                result["contexts"][name] = {
                    "configured_fonts": list(params["font.sans-serif"]),
                    "actual_font_file": chosen_path,
                    "sample": FONT_SAMPLE,
                    "missing_glyphs": missing,
                    "proof_png": str(proof_path),
                    "pdf_fonttype": plt.rcParams["pdf.fonttype"],
                }
                plt.close(figure)
                figure = None
    finally:
        if figure is not None:
            plt.close(figure)
    return result


def _check_pdf(pdf_bytes: bytes, expected_chinese: str, destination: Path) -> dict:
    from pypdf import PdfReader

    if not pdf_bytes.startswith(b"%PDF-"):
        raise AssertionError("Report builder did not return a PDF")
    destination.write_bytes(pdf_bytes)
    reader = PdfReader(BytesIO(pdf_bytes))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    if expected_chinese not in extracted or "质控报告" not in extracted:
        raise AssertionError(f"Chinese text could not be extracted from {destination.name}")
    fonts = {}
    for page in reader.pages:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources else {}
        for reference in resources.get("/Font", {}).get_object().values() if resources.get("/Font") else []:
            font = reference.get_object()
            subtype = str(font.get("/Subtype", ""))
            base_name = str(font.get("/BaseFont", ""))
            if subtype == "/Type3":
                raise AssertionError(f"{destination.name}: Type3 font {base_name} prevents reliable Chinese text extraction")
            descendants = font.get("/DescendantFonts")
            actual_fonts = [item.get_object() for item in descendants] if descendants else [font]
            if subtype == "/Type0" and not font.get("/ToUnicode"):
                raise AssertionError(f"{destination.name}: font {base_name} lacks a Unicode map")
            for actual in actual_fonts:
                descriptor = actual.get("/FontDescriptor")
                descriptor = descriptor.get_object() if descriptor else {}
                embedded_keys = [key for key in ("/FontFile", "/FontFile2", "/FontFile3") if descriptor.get(key)]
                if not embedded_keys or not all(descriptor[key].get_object().get_data() for key in embedded_keys):
                    raise AssertionError(f"{destination.name}: font {base_name} has no embedded font program")
                fonts[base_name] = {"subtype": subtype, "embedded_font_streams": embedded_keys}
    if not fonts:
        raise AssertionError(f"{destination.name}: no embedded fonts found")
    result = {"path": str(destination), "bytes": len(pdf_bytes), "pages": len(reader.pages),
              "chinese_text_extraction": "passed", "fonts": fonts}
    renderer = shutil.which("pdftoppm")
    if renderer:
        prefix = destination.with_suffix("")
        subprocess.run([renderer, "-f", "1", "-singlefile", "-scale-to", "1600", "-png", str(destination), str(prefix)],
                       check=True, capture_output=True, text=True, timeout=90)
        result["first_page_png"] = str(prefix.with_suffix(".png"))
    else:
        result["first_page_png"] = None
        result["render_note"] = "pdftoppm unavailable; PDF structural and text checks completed."
    return result


def verify(output_dir: Path) -> dict:
    result = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "status": "failed"}
    started = time.monotonic()
    try:
        result["runtime"] = _runtime_details()
        with warnings.catch_warnings(record=True) as captured:
            warnings.filterwarnings("error", message=r"Glyph .*missing from font")
            with _isolated_database():
                from services.report_service import (
                    build_lj_monthly_report_package, build_lj_monthly_report_pdf,
                    build_zscore_monthly_report_package, build_zscore_monthly_report_pdf,
                )
                from tests.lj_monthly_report_smoke_test import seed_lj_batch_with_formal_monthly_data
                from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data

                result["database_scope"] = "Fresh TemporaryDirectory database; live database not opened; verification database removed on exit."
                result["fonts"] = _font_proof(output_dir)
                _, lj_batch = seed_lj_batch_with_formal_monthly_data()
                lj_package = build_lj_monthly_report_package(lj_batch, "2026-04")
                result["lj_report"] = _check_pdf(build_lj_monthly_report_pdf(lj_package), "LJ 月报项目", output_dir / "lj-report.pdf")
                _, zscore_batch = seed_zscore_batch_with_formal_monthly_data()
                zscore_package = build_zscore_monthly_report_package(zscore_batch, "2026-04")
                result["zscore_report"] = _check_pdf(build_zscore_monthly_report_pdf(zscore_package), "Z-score 月报项目", output_dir / "zscore-2-level-report.pdf")
            result["non_glyph_warnings"] = sorted({str(item.message) for item in captured})
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["memory"] = _memory_details()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to retain generated PDFs, PNGs, and verification.json")
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = verify(output_dir)
    (output_dir / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
