"""Compile desktop application modules without shipping their readable sources.

The source checkout remains the development/web runtime.  Native desktop builds
use a separate directory containing native extension modules and two deliberately
small entry scripts.  C/object files stay in the private compiler work directory.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import chdir
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

MANIFEST = "protected_runtime_manifest.json"
PACKAGES = ("services", "ui", "pages", "migrations", "desktop_licensing")
ROOT_MODULES = ("app_main", "chart_layout", "database", "import_review", "plotting",
                "qc_logic", "zscore_logic", "zscore_plotting", "run_app")
APP_ENTRY = "from app_main import main\n\nmain()\n"
SERVICE_ENTRY = "from run_app import main\n\nraise SystemExit(main())\n"
DATA_DIRECTORIES = ("data/dictionaries", "data/catalogs", "resources")
HELP_FILES = ("docs/OPERATION_GUIDE.md", "docs/current_user_guide.md")
NATIVE_SUFFIXES = tuple(importlib.machinery.EXTENSION_SUFFIXES)
LICENSE_MODULES = frozenset(('desktop_licensing.client', 'desktop_licensing.protocol',
    'desktop_licensing.device', 'desktop_licensing.runtime', 'desktop_licensing.trust_anchor'))


def license_trust_digest(root: Path) -> str:
    """Bind the public resource to the trust pin that will be compiled."""
    # Direct Windows invocation starts with packaging/ as sys.path[0]. The
    # source root is needed only for this build-time public-policy validation.
    source_path = str(root.resolve())
    added = source_path not in sys.path
    if added:
        sys.path.insert(0, source_path)
    try:
        from desktop_licensing.protocol import TrustPolicy
    finally:
        if added:
            sys.path.remove(source_path)
    raw = (root / 'resources/desktop_license_trust.json').read_bytes()
    TrustPolicy.from_bytes(raw)
    tree = ast.parse((root / 'desktop_licensing/trust_anchor.py').read_text(encoding='utf-8'))
    pins = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'TRUST_SHA256' for target in node.targets)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    digest = hashlib.sha256(raw).hexdigest()
    if pins != [digest]:
        raise ValueError('The public license trust does not match its compiled pin')
    return digest


def application_modules(root: Path) -> dict[str, Path]:
    root = root.resolve()
    result = {name: root / (name + ".py") for name in ROOT_MODULES}
    for package in PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "__init__.py":
                continue
            result[".".join(path.relative_to(root).with_suffix("").parts)] = path
        # Empty initializers can be replaced by empty package markers.  Reject
        # newly added executable initialization instead of silently discarding it.
        tree = ast.parse((root / package / "__init__.py").read_text(encoding="utf-8"))
        if any(not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)) for node in tree.body):
            raise ValueError("Package initializer needs explicit protection: " + package)
    missing = [str(path) for path in result.values() if not path.is_file()]
    if missing:
        raise ValueError("Protected application source is missing: " + ", ".join(missing))
    return result


def imported_modules(sources: dict[str, Path]) -> list[str]:
    """Preserve dependencies hidden from PyInstaller by native compilation."""
    result = set(sources)
    for name, path in sources.items():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                result.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module == "__future__":
                    continue
                result.add(node.module)
                for alias in node.names:
                    candidate = node.module + "." + alias.name
                    if candidate in sources or (node.module == "PIL" and alias.name == "Image"):
                        result.add(candidate)
    return sorted(result)


def compile_runtime(root: Path, output: Path, work: Path, *, jobs: int = 2) -> dict:
    """Use the build interpreter/architecture; never modify the source checkout."""
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension

    root, output, work = root.resolve(), output.resolve(), work.resolve()
    if output.exists() or work.exists() or output == work or output.is_relative_to(work):
        raise ValueError("Protected runtime and compiler work directories must be fresh and separate")
    sources = application_modules(root)
    trust_digest = license_trust_digest(root)
    if sys.platform == "darwin":
        os.environ["ARCHFLAGS"] = "-arch " + platform.machine()
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    flags = ["/O2"] if os.name == "nt" else ["-O2", "-g0"]
    extensions = [Extension(name, [str(path)], extra_compile_args=flags,
        define_macros=[("CYTHON_CLINE_IN_TRACEBACK", "0")]) for name, path in sources.items()]
    extensions = cythonize(extensions, build_dir=str(work / "cython"), nthreads=max(1, jobs),
        compiler_directives={"language_level": 3, "annotation_typing": False, "infer_types": False,
            "binding": True, "embedsignature": False, "emit_code_comments": False},
        annotate=False, quiet=True)
    # MSVC mirrors the source directory beneath build_temp for objects and
    # import libraries. An absolute generated-C path repeats the entire work
    # prefix there and exceeds Windows toolchain path limits.
    for extension in extensions:
        extension.sources = [str(Path(source).resolve(strict=True).relative_to(work))
                             for source in extension.sources]
    distribution = Distribution({"name": "ljqc-protected-runtime", "ext_modules": extensions})
    command = distribution.get_command_obj("build_ext")
    command.build_lib = str(output)
    command.build_temp = str(work / "objects")
    command.parallel = max(1, jobs)
    command.ensure_finalized()
    # build_lib/build_temp stay absolute; only C source arguments are relative.
    # chdir restores the caller's directory even when compilation fails.
    with chdir(work):
        command.run()
    for package in PACKAGES:
        (output / package / "__init__.py").write_text("", encoding="utf-8")
    (output / "app.py").write_bytes(APP_ENTRY.encode("utf-8"))
    (output / "service_entry.py").write_bytes(SERVICE_ENTRY.encode("utf-8"))
    compiled = []
    for name in sorted(sources):
        expected = output.joinpath(*name.split("."))
        files = [path for path in expected.parent.glob(expected.name + ".*")
                 if path.name.endswith(NATIVE_SUFFIXES)]
        if len(files) != 1:
            raise ValueError("Expected one native extension for " + name)
        path = files[0]
        compiled.append({"module": name, "file": path.relative_to(output).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"format": "ljqc-protected-runtime", "format_version": 1,
        "python_tag": sys.implementation.cache_tag, "modules": compiled,
        "license_trust_sha256": trust_digest,
        "hiddenimports": imported_modules(sources), "readable_business_source_included": False}
    (output / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for source, destination in application_datas(root, output):
        source = Path(source)
        target = output / destination / source.name
        if source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    read_manifest(output)
    return manifest


def read_manifest(runtime: Path) -> dict:
    runtime = runtime.resolve()
    value = json.loads((runtime / MANIFEST).read_text(encoding="utf-8"))
    if (value.get("format") != "ljqc-protected-runtime" or value.get("format_version") != 1
            or value.get("python_tag") != sys.implementation.cache_tag
            or value.get("readable_business_source_included") is not False
            or not isinstance(value.get("modules"), list) or not value["modules"]):
        raise ValueError("Protected runtime manifest is invalid")
    names = set()
    for entry in value["modules"]:
        name, relative = entry.get("module", ""), Path(entry.get("file", ""))
        if not name or name in names or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Protected runtime module declaration is invalid")
        names.add(name)
        path = runtime / relative
        if (not path.is_file() or path.is_symlink() or not path.name.endswith(NATIVE_SUFFIXES)
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256")):
            raise ValueError("Protected native module is missing or changed: " + name)
    if not (set(ROOT_MODULES) | LICENSE_MODULES).issubset(names):
        raise ValueError("Protected runtime omits a required application module")
    trust_file = runtime / 'resources/desktop_license_trust.json'
    if (not trust_file.is_file() or hashlib.sha256(trust_file.read_bytes()).hexdigest()
            != value.get('license_trust_sha256')):
        raise ValueError('Protected runtime license trust is missing or changed')
    if (runtime / "app.py").read_text(encoding="utf-8") != APP_ENTRY:
        raise ValueError("Protected Streamlit entry contains unexpected source")
    allowed = {"app.py": APP_ENTRY, "service_entry.py": SERVICE_ENTRY,
               **{package + "/__init__.py": "" for package in PACKAGES}}
    for path in runtime.rglob("*"):
        if path.is_symlink():
            raise ValueError("Protected runtime must not contain symlinks")
        if path.suffix in {".py", ".pyc", ".pyo", ".c", ".cpp", ".h", ".o", ".obj"}:
            relative = path.relative_to(runtime).as_posix()
            if relative not in allowed or path.read_text(encoding="utf-8") != allowed[relative]:
                raise ValueError("Readable or intermediate source in protected runtime: " + relative)
    return value


def protected_runtime(root: Path) -> tuple[Path, dict]:
    configured = os.environ.get("LJQC_PROTECTED_RUNTIME", "").strip()
    if not configured:
        raise ValueError("Compile the protected runtime before building a desktop program")
    runtime = Path(configured).resolve()
    if runtime == root.resolve():
        raise ValueError("The desktop runtime must be separate from the source checkout")
    manifest = read_manifest(runtime)
    expected = set(application_modules(root))
    if {row["module"] for row in manifest["modules"]} != expected:
        raise ValueError("Protected runtime does not match the application module inventory")
    if license_trust_digest(root) != manifest['license_trust_sha256']:
        raise ValueError('The protected runtime was built for a different license trust')
    return runtime, manifest


def application_datas(root: Path, runtime: Path) -> list[tuple[str, str]]:
    """Only the thin entry, public resources and build manifest are data files."""
    result = [(str(runtime / "app.py"), "."), (str(runtime / MANIFEST), "."),
              (str(root / "release.json"), ".")]
    for name in DATA_DIRECTORIES:
        for path in sorted((root / name).rglob("*")):
            if path.is_file():
                if path.suffix.lower() in {".py", ".pyc", ".pyo", ".c", ".cpp", ".h"}:
                    raise ValueError("Source file in public desktop resources: " + str(path))
                result.append((str(path), str(path.parent.relative_to(root))))
    for name in HELP_FILES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError("Missing required help resource: " + name)
        result.append((str(path), str(path.parent.relative_to(root))))
    guide = root / 'docs/OPERATION_GUIDE.md'
    for reference in re.findall(r'!\[[^\]]*\]\((images/first-use/[^\s)]+)\)',
                                guide.read_text(encoding='utf-8')):
        path = guide.parent / reference
        if not path.is_file():
            raise FileNotFoundError('Missing required help image: ' + reference)
    for path in sorted((root / "docs/images/first-use").glob("*")):
        if path.is_file():
            result.append((str(path), "docs/images/first-use"))
    return result


def assert_native_analysis(analysis, manifest: dict, *, required_modules: set[str] | None = None) -> None:
    expected = {row["module"] for row in manifest["modules"]}
    unprotected = expected & {name for name, *_ in analysis.pure}
    if unprotected:
        raise ValueError("Desktop analysis fell back to Python business bytecode: " + ", ".join(sorted(unprotected)))
    binaries = {name.replace("\\", "/") for name, *_ in analysis.binaries}
    required = expected if required_modules is None else required_modules
    missing = [row["module"] for row in manifest["modules"]
               if row["module"] in required and row["file"] not in binaries]
    if missing:
        raise ValueError("Desktop analysis omitted native business modules: " + ", ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    value = compile_runtime(args.root, args.output, args.work, jobs=args.jobs)
    print(json.dumps({"status": "compiled", "modules": len(value["modules"]), "runtime": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
