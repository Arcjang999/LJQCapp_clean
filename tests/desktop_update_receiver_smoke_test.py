"""Observed sudo policy-query regression; no sudo or account changes are run."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ljqc_receiver_sudo_test", ROOT / "scripts/install_desktop_update_receiver.py")
receiver = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(receiver)
HOST = "VM-0-7-ubuntu"
DENIAL = f"User ljqc-update is not allowed to run sudo on {HOST}.\n"


class ReceiverSudoPolicyTests(unittest.TestCase):
    def query(self, *, returncode=0, stdout=DENIAL, stderr=""):
        result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        with patch.object(receiver.socket, "gethostname", return_value=HOST), \
             patch.object(receiver.subprocess, "run", return_value=result) as run:
            receiver.assert_no_sudo_access()
        return run

    def test_observed_zero_exit_with_explicit_denial_is_accepted(self):
        self.query(returncode=0)

    def test_nonzero_exit_is_accepted_only_with_the_same_explicit_denial(self):
        self.query(returncode=1)
        self.query(returncode=1, stdout=DENIAL.rstrip("\n"))

    def test_query_is_noninteractive_bounded_and_forces_c_locale(self):
        original = {key: os.environ.get(key) for key in ("LC_ALL", "LANG", "LANGUAGE")}
        with patch.dict(os.environ, {"LC_ALL": "zh_CN.UTF-8", "LANG": "zh_CN.UTF-8", "LANGUAGE": "zh_CN"}):
            run = self.query()
            self.assertEqual(os.environ["LC_ALL"], "zh_CN.UTF-8")
        arguments, keywords = run.call_args
        self.assertEqual(arguments[0], ["/usr/bin/sudo", "-n", "-l", "-U", "ljqc-update"])
        self.assertFalse(keywords["check"])
        self.assertEqual(keywords["timeout"], 30)
        self.assertEqual({key: keywords["env"][key] for key in ("LC_ALL", "LANG", "LANGUAGE")},
                         {"LC_ALL": "C", "LANG": "C", "LANGUAGE": "C"})
        self.assertEqual({key: os.environ.get(key) for key in original}, original)

    def test_any_grant_output_is_rejected_even_when_exit_is_nonzero(self):
        grant = f"User ljqc-update may run the following commands on {HOST}:\n    (ALL : ALL) ALL\n"
        for returncode in (0, 1, 2):
            with self.subTest(returncode=returncode), self.assertRaises(ValueError):
                self.query(returncode=returncode, stdout=grant)

    def test_empty_or_unknown_output_never_proves_no_sudo(self):
        for stdout in ("", "\n", "not allowed\n", "sudo policy unavailable\n"):
            for returncode in (0, 1, 2, 127):
                with self.subTest(stdout=stdout, returncode=returncode), self.assertRaises(ValueError):
                    self.query(returncode=returncode, stdout=stdout)

    def test_warning_or_error_stderr_rejects_an_otherwise_explicit_denial(self):
        for stderr in ("sudo: unable to resolve host\n", "sudo: a policy plugin error occurred\n", "\n", " "):
            with self.subTest(stderr=stderr), self.assertRaises(ValueError):
                self.query(stderr=stderr)

    def test_wrong_account_host_and_mixed_or_extra_lines_are_rejected(self):
        messages = (DENIAL.replace("ljqc-update", "ubuntu"), DENIAL.replace(HOST, "another-host"),
                    " " + DENIAL, DENIAL + "\n", DENIAL + "    (root) /bin/sh\n",
                    "Matching Defaults entries for ljqc-update:\n" + DENIAL,
                    DENIAL.rstrip(".\n") + "\n")
        for stdout in messages:
            with self.subTest(stdout=stdout), self.assertRaises(ValueError):
                self.query(stdout=stdout)

    def test_unexpected_status_and_signal_reject_even_an_explicit_denial(self):
        for returncode in (2, 127, -15, True, None):
            with self.subTest(returncode=returncode), self.assertRaises(ValueError):
                self.query(returncode=returncode)

    def test_missing_command_and_timeout_stop_closed(self):
        for failure in (FileNotFoundError("sudo unavailable"), subprocess.TimeoutExpired("sudo", 30)):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(receiver.subprocess, "run", side_effect=failure), self.assertRaises(ValueError):
                receiver.assert_no_sudo_access()

    def test_locked_dedicated_account_calls_the_explicit_no_sudo_gate(self):
        user = SimpleNamespace(pw_uid=995, pw_gid=982, pw_dir=str(receiver.HOME), pw_shell=str(receiver.SHELL))
        group = SimpleNamespace(gr_name="ljqc-update", gr_mem=[])
        password = subprocess.CompletedProcess(args=[], returncode=0, stdout="ljqc-update L 2026-11-08 0 99999 7 -1\n", stderr="")
        with patch.object(receiver, "pwd", SimpleNamespace(getpwnam=lambda name: user), create=True), \
             patch.object(receiver, "grp", SimpleNamespace(getgrgid=lambda gid: group), create=True), \
             patch.object(receiver.os, "getgrouplist", return_value=[982], create=True), \
             patch.object(receiver.os, "geteuid", return_value=0, create=True), \
             patch.object(receiver, "command", return_value=password), \
             patch.object(receiver, "assert_no_sudo_access") as gate:
            self.assertIs(receiver.account(), user)
            gate.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
