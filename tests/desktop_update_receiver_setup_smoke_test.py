"""Review-only receiver setup checks; never provision a real account or service."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ljqc_receiver_setup", ROOT / "scripts/install_desktop_update_receiver.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ReceiverSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-receiver-setup-qa-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_only_one_structurally_valid_public_key_is_accepted(self):
        tag = b"ssh-ed25519"
        wire = struct.pack(">I", len(tag)) + tag + struct.pack(">I", 32) + b"q" * 32
        key = self.root / "deployment.pub"
        key.write_text("ssh-ed25519 " + base64.b64encode(wire).decode() + " original comment\n")
        normalized, fingerprint = setup.public_key(key)
        self.assertTrue(normalized.endswith("ljqc-update-deploy"))
        self.assertTrue(fingerprint.startswith("SHA256:"))
        key.write_text(key.read_text() * 2)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            setup.public_key(key)

    def test_private_key_input_is_rejected_without_exposure(self):
        key = self.root / "incorrect-input"
        secret = "a-private-value-that-must-never-be-reported"
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + secret + "\n")
        with self.assertRaises(ValueError) as caught:
            setup.public_key(key)
        self.assertNotIn(secret, str(caught.exception))

    def test_existing_directory_permissions_are_not_changed(self):
        directory = self.root / "existing"
        directory.mkdir(mode=0o700)
        if os.name == "posix":
            with self.assertRaisesRegex(ValueError, "no permission was expanded"):
                setup.directory(directory, os.getuid(), 0o755)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        else:
            with self.assertRaises(ValueError):
                setup.directory(directory, None, 0o755)

    def test_symbolic_link_directory_is_rejected(self):
        if os.name != "posix":
            self.assertNotEqual(setup.USER, "root")
            return
        actual = self.root / "actual"
        actual.mkdir()
        indirect = self.root / "indirect"
        indirect.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            setup.reject_symlink_components(indirect / "nested")

    def test_receiver_shell_rejects_arbitrary_commands(self):
        if os.name != "posix":
            self.assertIn('SSH_ORIGINAL_COMMAND', setup.WRAPPER)
            return
        shell = self.root / "receiver-shell"
        shell.write_text(setup.WRAPPER)
        subprocess.run(["sh", "-n", str(shell)], check=True)
        for arguments in ([], ["-c", "id"], ["-c", "bash"], ["-c", setup.FORCED]):
            result = subprocess.run(["sh", str(shell), *arguments], capture_output=True,
                                    env={"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": "sftp"})
            self.assertEqual(result.returncode, 126)
            self.assertEqual(result.stdout, b"")

    def test_tool_bundle_allowlist_excludes_keys_and_business_data(self):
        source = self.root / "source"
        source.mkdir()
        for name in setup.FILES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("reviewed fixture source")
        (source / "private-deployment-key").write_text("secret fixture")
        (source / "business.db").write_bytes(b"SQLite format 3\x00")
        trust = {"product": "LJQCApp", "channel": "dev", "base_release": "1.0.0-2026110606",
                 "origin": "https://159.75.243.120", "keys": {"qa": base64.b64encode(b"p" * 32).decode()}}
        (source / "resources/desktop_update_trust.json").write_text(json.dumps(trust))
        output = self.root / "receiver-tools.tar.gz"
        responses = [SimpleNamespace(stdout=""), SimpleNamespace(stdout="a" * 40 + "\n")]
        with patch.object(setup, "SOURCE", source), patch.object(setup.subprocess, "run", side_effect=responses):
            result = setup.make_bundle(output)
        self.assertFalse(result["private_keys_included"])
        with tarfile.open(output) as archive:
            self.assertEqual(set(archive.getnames()), set(setup.FILES) | {setup.MANIFEST})
            self.assertTrue(all(member.uid == 0 and member.gid == 0 and member.mode == 0o644 for member in archive.getmembers()))
            extracted = self.root / "extracted"
            archive.extractall(extracted, filter="data")
        setup.read_source_manifest(extracted)
        (extracted / "packaging/publish_desktop_update.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "differs"):
            setup.read_source_manifest(extracted)

    def test_nginx_proposal_is_only_a_static_location(self):
        self.assertIn("location ^~ /updates/", setup.NGINX_PROPOSAL)
        self.assertIn('Cache-Control "no-store"', setup.NGINX_PROPOSAL)
        self.assertNotIn("proxy_pass", setup.NGINX_PROPOSAL)
        self.assertNotIn("ssl_certificate ", setup.NGINX_PROPOSAL)
        self.assertNotIn("systemctl", setup.NGINX_PROPOSAL)

    def runtime_fixture(self):
        tools = self.root / "dedicated-tools"
        executable = tools / "venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"inert fixture, never executed")
        return tools

    def test_existing_receiver_adds_only_missing_certifi_without_dependencies(self):
        tools = self.runtime_fixture()
        missing = {"runtime": "existing", "cryptography": "50.0.2", "certifi": "install-pinned-if-missing"}
        ready = {**missing, "certifi": "2026.7.22"}
        with patch.object(setup, "TOOLS", tools), patch.object(setup, "runtime_dependency_status", side_effect=[missing, ready]), \
                patch.object(setup, "check_existing_venv") as permissions, patch.object(setup, "command") as command:
            self.assertEqual(setup.ensure_runtime_dependencies("/usr/bin/python3"), ready)
        installs = [call.args[0] for call in command.call_args_list if "pip" in call.args[0]]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0][-1], "certifi==2026.7.22")
        self.assertIn("--no-deps", installs[0])
        self.assertNotIn("cryptography==50.0.2", installs[0])
        self.assertTrue(all(str(tools / "venv/bin/python") == call.args[0][0] for call in command.call_args_list))
        permissions.assert_called_once_with()

    def test_complete_existing_receiver_never_runs_pip_or_recreates_venv(self):
        tools = self.runtime_fixture()
        ready = {"runtime": "existing", "cryptography": "50.0.2", "certifi": "2026.7.22"}
        with patch.object(setup, "TOOLS", tools), patch.object(setup, "runtime_dependency_status", return_value=ready), \
                patch.object(setup, "check_existing_venv"), patch.object(setup, "command") as command:
            setup.ensure_runtime_dependencies("/usr/bin/python3")
        self.assertEqual(command.call_count, 1)
        self.assertNotIn("pip", command.call_args.args[0])
        self.assertIn("cert_store_stats", command.call_args.args[0][-1])
        self.assertIn("version('certifi')", command.call_args.args[0][-1])
        self.assertNotIn("certifi.__version__ ==", command.call_args.args[0][-1])

    def test_receiver_runtime_conflicts_fail_before_any_package_change(self):
        tools = self.runtime_fixture()
        for versions in ({"cryptography": "50.0.2", "certifi": "2026.10.18"},
                         {"cryptography": "49.0.0", "certifi": None},
                         {"cryptography": None, "certifi": "2026.7.22"}):
            with self.subTest(versions=versions), patch.object(setup, "TOOLS", tools), \
                    patch.object(setup, "command", return_value=SimpleNamespace(stdout=json.dumps(versions))) as command:
                with self.assertRaisesRegex(ValueError, "no packages were changed"):
                    setup.ensure_runtime_dependencies("/usr/bin/python3")
                self.assertEqual(command.call_count, 1)
                self.assertNotIn("pip", command.call_args.args[0])

    def test_missing_receiver_ca_after_pip_is_not_reported_as_installed(self):
        tools = self.runtime_fixture()
        missing = {"runtime": "existing", "cryptography": "50.0.2", "certifi": "install-pinned-if-missing"}
        with patch.object(setup, "TOOLS", tools), patch.object(setup, "runtime_dependency_status", return_value=missing), \
                patch.object(setup, "command"):
            with self.assertRaisesRegex(ValueError, "not installed correctly"):
                setup.ensure_runtime_dependencies("/usr/bin/python3")

    def test_ssh_policy_rejects_loader_environment_and_incompatible_locked_login(self):
        safe = ("pubkeyauthentication yes\nchrootdirectory none\nforcecommand none\n"
                "authenticationmethods any\npermituserenvironment no\nusepam yes\n"
                "acceptenv LANG\nacceptenv LC_*\nauthorizedkeysfile .ssh/authorized_keys\n")
        with patch.object(setup, "command", return_value=SimpleNamespace(stdout=safe)):
            setup.check_sshd()
        for invalid in (safe + "acceptenv LD_PRELOAD\n", safe.replace("permituserenvironment no", "permituserenvironment yes"),
                        safe.replace("usepam yes", "usepam no"), safe + "acceptenv BASH_ENV\n"):
            with patch.object(setup, "command", return_value=SimpleNamespace(stdout=invalid)):
                with self.assertRaises(ValueError):
                    setup.check_sshd()


if __name__ == "__main__":
    unittest.main(verbosity=2)
