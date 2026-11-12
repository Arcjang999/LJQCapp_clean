"""Desktop builds must fail closed when readable business code returns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import protected_runtime as protection
from release_payload import assert_protected_payload, validate_payload


def source_fixture(root: Path):
    root.mkdir()
    for name in protection.ROOT_MODULES:
        (root / (name + ".py")).write_text("VALUE = 1\n", encoding="utf-8")
    for name in protection.PACKAGES:
        (root / name).mkdir()
        (root / name / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
        (root / name / "business.py").write_text("VALUE = 1\n", encoding="utf-8")


def runtime_fixture(root: Path):
    root.mkdir()
    modules = []
    for name in (*protection.ROOT_MODULES, *sorted(protection.LICENSE_MODULES), "services.business"):
        path = root.joinpath(*name.split(".")).with_suffix(protection.NATIVE_SUFFIXES[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"isolated native module placeholder")
        modules.append(dict(module=name, file=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (root / "app.py").write_text(protection.APP_ENTRY, encoding="utf-8")
    (root / 'resources').mkdir()
    trust = b'{"format_version":1,"origin":"https://example.invalid","keys":{}}'
    (root / 'resources/desktop_license_trust.json').write_bytes(trust)
    value = dict(format="ljqc-protected-runtime", format_version=1,
        python_tag=sys.implementation.cache_tag, modules=modules,
        hiddenimports=[row["module"] for row in modules], readable_business_source_included=False,
        license_trust_sha256=hashlib.sha256(trust).hexdigest())
    (root / protection.MANIFEST).write_text(json.dumps(value), encoding="utf-8")
    return value


class ProtectedRuntimeTests(unittest.TestCase):
    def test_all_application_files_are_discovered_and_new_package_initialization_is_not_lost(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            source_fixture(root)
            (root / "services" / "new_business.py").write_text("VALUE = 2\n")
            modules = protection.application_modules(root)
            self.assertIn("services.new_business", modules)
            (root / "services" / "__init__.py").write_text("from .business import VALUE\n")
            with self.assertRaisesRegex(ValueError, "initializer"):
                protection.application_modules(root)

    def test_hidden_import_inventory_covers_dynamic_application_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "business.py"
            path.write_text("from PIL import Image\nfrom services import other\nimport pandas as pd\n")
            names = protection.imported_modules({"services.business": path, "services.other": path})
            self.assertTrue({"PIL.Image", "services.business", "services.other", "pandas"}.issubset(names))

    def test_compiled_module_tampering_and_source_fallback_both_block_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            value = runtime_fixture(runtime)
            self.assertEqual(protection.read_manifest(runtime), value)
            binary = runtime / value["modules"][0]["file"]
            binary.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                protection.read_manifest(runtime)
            analysis = SimpleNamespace(pure=[("qc_logic", "source.py", "PYMODULE")], binaries=[])
            with self.assertRaisesRegex(ValueError, "fell back"):
                protection.assert_native_analysis(analysis, value)

    def test_final_payload_rejects_business_source_next_to_native_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime_fixture(runtime)
            self.assertEqual(assert_protected_payload(runtime), len(protection.ROOT_MODULES) + len(protection.LICENSE_MODULES) + 1)
            (runtime / "services" / "business.py").write_text("SECRET_LOGIC = 1\n")
            with self.assertRaisesRegex(ValueError, "Readable business source"):
                validate_payload(runtime)

    def test_final_payload_rejects_missing_native_module_and_nontrivial_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            value = runtime_fixture(runtime)
            (runtime / "app.py").write_text(protection.APP_ENTRY + "print('business code')\n")
            with self.assertRaisesRegex(ValueError, "unexpected application source"):
                assert_protected_payload(runtime)
            (runtime / "app.py").write_text(protection.APP_ENTRY)
            (runtime / value["modules"][0]["file"]).unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                assert_protected_payload(runtime)

    def test_outer_launcher_cannot_reintroduce_source_or_business_bytecode(self):
        for leaked in ("qc_logic.py", "services/business.py", "ui/__pycache__/editor.cpython-313.pyc", "app.py"):
            with self.subTest(leaked=leaked), tempfile.TemporaryDirectory() as temporary:
                payload = Path(temporary)
                runtime_fixture(payload / "service")
                path = payload / "launcher" / leaked
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"readable business logic")
                with self.assertRaises(ValueError):
                    assert_protected_payload(payload)

    def test_replaced_license_trust_or_shipped_server_material_blocks_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            runtime_fixture(runtime)
            target = runtime / 'resources/desktop_license_trust.json'
            previous = target.read_bytes()
            target.write_bytes(b'{"attacker":"key"}')
            with self.assertRaisesRegex(ValueError, 'license trust'):
                assert_protected_payload(runtime)
            target.write_bytes(previous)
            (runtime / 'licensing_server').mkdir()
            (runtime / 'licensing_server/server.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Mutable or trial directory'):
                validate_payload(runtime)

    def test_public_resources_never_accept_python_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "resources"
            resources.mkdir()
            (resources / "forgotten_source.py").write_text("SECRET = 1\n")
            with self.assertRaisesRegex(ValueError, "Source file in public"):
                protection.application_datas(root, root / "runtime")


if __name__ == "__main__":
    unittest.main(verbosity=2)
