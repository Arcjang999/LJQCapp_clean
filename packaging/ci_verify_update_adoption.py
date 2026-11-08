"""GitHub-hosted-only acceptance: real 1.00 install -> bootstrap -> real patch.

No business Qt window is opened. A temporary QA signing key is used only by the
source manager in this process; packaged production trust is never modified.
Only bounded JSON/log receipts may be copied out of the temporary runner tree.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]

# Immutable, already published update components from the first accepted pair.
# These identify actual frozen 02 helpers, not source-generated stand-ins. They
# are independent of the unchanged 1.00 base download catalog.
LEGACY_HELPERS = {
    "windows-x86_64": {
        "url": "https://159.75.243.120/downloads/update-bootstrap/1/2026110702/windows-x86_64/LJQCApp-UpdateBootstrap-1-2026110702-windows-x64-Setup.exe",
        "size_bytes": 39021001,
        "sha256": "813c75b7df56ef5e91bcc89a1db554f40af36972746181ac45ad126bc91983ea"},
    "macos-arm64": {
        "url": "https://159.75.243.120/downloads/update-bootstrap/1/2026110702/macos-arm64/LJQCApp-UpdateBootstrap-1-2026110702-macos-arm64-pending-signing.dmg",
        "size_bytes": 49730713,
        "sha256": "e4db85d3f172a8e6095cf0c01b02bab9c9086480347a5c955441f8bc61a00f93"},
}
LEGACY_COMMIT = "2adb7721060582308164a4b10a13d8d3c66dd14f"
LEGACY_SOURCE_DIGEST = "9b8100574296fa4d1083d9b91013d34276d57f65c8a7f59bba1e3596dcbabc72"


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def require_ephemeral_runner(target: str) -> Path:
    """Refuse before any download/install unless this is the intended CI host."""
    expected = "windows-x86_64" if sys.platform == "win32" else "macos-arm64" if sys.platform == "darwin" and platform.machine() == "arm64" else None
    if (os.environ.get("GITHUB_ACTIONS", "").lower() != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or not os.environ.get("GITHUB_RUN_ID", "").isdecimal()
            or not os.environ.get("GITHUB_JOB")
            or re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")) is None
            or Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve() != ROOT
            or expected != target):
        raise ValueError("Real installer adoption acceptance is restricted to the matching ephemeral GitHub-hosted runner")
    temporary = Path(os.environ.get("RUNNER_TEMP", ""))
    if not temporary.is_absolute() or not temporary.is_dir() or temporary.is_symlink():
        raise ValueError("The GitHub runner temporary directory is invalid")
    if temporary.resolve() == ROOT or temporary.resolve().is_relative_to(ROOT):
        raise ValueError("Acceptance data must remain outside uploaded checkout artifacts")
    return temporary.resolve()


def refuse_existing_windows_installation() -> None:
    import winreg
    for app_id in ("{D0A767BD-0CF0-4197-BA45-024CD72945B3}", "{E8BE4330-8DA6-43B3-A8C0-E197A5018085}"):
        name = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + app_id + "_is1"
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            handle.Close()
            raise ValueError("The ephemeral runner already contains an LJQC installation; it was not touched")


def create_business_fixture(root: Path) -> dict:
    """Insert QA rows into the original frozen application's existing schema."""
    expected = root / "data/qc_lj_app.db"
    if not expected.is_file() or expected.is_symlink():
        raise ValueError("The original 1.00 frozen application must initialize its QA schema first")
    from pypdf import PdfWriter
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    writer.write(pdf)
    content = pdf.getvalue()
    sha = hashlib.sha256(content).hexdigest()
    timestamp = "2026-11-07 00:00:00"
    with closing(sqlite3.connect(expected)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        project = connection.execute("INSERT INTO projects(name,method_type,input_value_type) VALUES(?,?,?)",
                                     ("CI adoption retention project", "lj", "raw")).lastrowid
        batch = connection.execute("""INSERT INTO batches(project_id,instrument,reagent,qc_material,concentration,lot_no,target_n)
            VALUES(?,?,?,?,?,?,?)""", (project, "QA instrument", "QA reagent", "QA material", "one level", "QA-001", 20)).lastrowid
        result = connection.execute("INSERT INTO results(batch_id,test_time,operator,value) VALUES(?,?,?,?)",
                                    (batch, timestamp, "CI QA", 123.5)).lastrowid
        report = connection.execute("""INSERT INTO report_exports(report_type,project_id,batch_id,report_month,generated_at,input_value_type,method_label,file_name)
            VALUES(?,?,?,?,?,?,?,?)""", ("lj", project, batch, "2026-11", timestamp, "raw", "L-J", "qa-retained.pdf")).lastrowid
        connection.execute("INSERT INTO report_export_files(export_id,pdf_bytes,sha256) VALUES(?,?,?)", (report, content, sha))
        event = connection.execute("""INSERT INTO qc_ooc_events(source_type,source_id,qc_method,original_classification,origin_snapshot_json,opened_by,opened_at)
            VALUES(?,?,?,?,?,?,?)""", ("lj_result", result, "lj", "warning", "{}", "CI QA", timestamp)).lastrowid
        connection.execute("""INSERT INTO qc_ooc_revisions(event_id,revision_no,status,action,content_json,saved_by,saved_at)
            VALUES(?,1,'pending','open','{}',?,?)""", (event, "CI QA", timestamp))
        file_id, relative = "qa-adoption-attachment", "qa-evidence/one.pdf"
        connection.execute("""INSERT INTO qc_managed_files(file_id,kind,event_id,original_name,media_type,size_bytes,sha256,relative_path,uploaded_at,uploaded_by)
            VALUES(?,'attachment',?,?,?,?,?,?,?,?)""", (file_id, event, "qa-evidence.pdf", "application/pdf", len(content), sha, relative, timestamp, "CI QA"))
        connection.execute("INSERT INTO qc_event_revision_attachments(event_id,revision_no,file_id) VALUES(?,1,?)", (event, file_id))
        connection.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           ("first_use_onboarding", '{"version":1,"step":"project","status":"completed"}'))
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("The complete-schema QA fixture has an invalid relationship")
        tables = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    attachment = expected.parent / (expected.stem + "_files") / relative
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(content)
    return {"database": str(expected), "project_id": project, "batch_id": batch,
            "report_id": report, "report_sha256": sha, "report_size_bytes": len(content),
            "attachment_relative_path": relative, "attachment_sha256": sha, "schema_tables": tables}


def initialize_original_schema(base: Path, target: str, root: Path, env: dict, logs: Path) -> None:
    """Open one local protobuf session on the original headless frozen service.

    The original 06 CLI has no init-only command: --reset-db deletes and defers
    initialization. A normal Streamlit rerun executes its own unmodified app.py
    against a fresh QA root, without launching Qt or a browser window.
    """
    if root.exists():
        raise ValueError("Original-schema acceptance requires a completely fresh QA data root")
    service = base / ("_internal/app/LJQCAppService.exe" if target == "windows-x86_64"
                      else "Contents/Resources/app/LJQCAppService")
    if not service.is_file():
        raise ValueError("The installed original service is missing")
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    request = runtime / ("shutdown-" + uuid.uuid4().hex + ".request")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = None
    with (logs / "original-frozen-initialization.log").open("wb") as log:
        try:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if target == "windows-x86_64" else {"start_new_session": True}
            process = subprocess.Popen([str(service), "--port", str(port), "--address", "127.0.0.1"],
                cwd=service.parent, env={**env, "LJQC_SHUTDOWN_FILE": str(request)},
                stdout=log, stderr=subprocess.STDOUT, **options)
            deadline = time.monotonic() + 90
            while True:
                if process.poll() is not None:
                    raise ValueError("The original frozen service exited before initialization")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise ValueError("The original frozen service did not become healthy")
                time.sleep(0.25)
            from websockets.sync.client import connect
            from streamlit.proto.BackMsg_pb2 import BackMsg
            from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
            import inspect
            options = {"origin": f"http://127.0.0.1:{port}", "subprotocols": ["streamlit"],
                       "open_timeout": 15, "close_timeout": 5, "max_size": 64 * 1024**2}
            if "proxy" in inspect.signature(connect).parameters:
                options["proxy"] = None
            with connect(f"ws://127.0.0.1:{port}/_stcore/stream", **options) as connection:
                message = BackMsg()
                message.rerun_script.query_string = ""
                message.rerun_script.page_script_hash = ""
                message.rerun_script.page_name = ""
                message.rerun_script.context_info.url = f"http://127.0.0.1:{port}/"
                message.rerun_script.context_info.locale = "zh-CN"
                message.rerun_script.context_info.timezone = "Asia/Shanghai"
                connection.send(message.SerializeToString())
                deadline = time.monotonic() + 120
                successful = False
                while time.monotonic() < deadline:
                    try:
                        raw = connection.recv(timeout=min(15, max(0.1, deadline - time.monotonic())))
                    except TimeoutError:
                        continue
                    if not isinstance(raw, bytes):
                        raise ValueError("The original application returned a non-protobuf frame")
                    forward = ForwardMsg()
                    forward.ParseFromString(raw)
                    if (forward.HasField("delta") and forward.delta.HasField("new_element")
                            and forward.delta.new_element.HasField("exception")):
                        raise ValueError("The original frozen application's first run raised an exception")
                    if forward.HasField("script_finished"):
                        if forward.script_finished == ForwardMsg.FINISHED_WITH_COMPILE_ERROR:
                            raise ValueError("The original frozen application's script did not compile")
                        if forward.script_finished == ForwardMsg.FINISHED_SUCCESSFULLY:
                            successful = True
                            break
                if not successful:
                    raise ValueError("The original frozen application's initialization did not complete")
        finally:
            if process is not None and process.poll() is None:
                request.write_text("stop\n", encoding="ascii")
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=15)
                    raise ValueError("The isolated original service did not exit normally")
            if process is not None and process.returncode:
                raise ValueError("The original frozen service returned a failure exit code")
    database = root / "data/qc_lj_app.db"
    if not database.is_file():
        raise ValueError("The original frozen app did not create its own database")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("The original frozen database did not pass its integrity check")
        for table in ("projects", "batches", "results", "report_exports"):
            if connection.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]:
                raise ValueError("The original fresh installation unexpectedly seeded business rows")


class TemporaryKnownFolders:
    """Redirect only this disposable CI user's shortcut folders, then restore."""
    FOLDERS = {
        "StartMenu": "625B53C3-AB48-4EC1-BA1F-A1EF4146FC19",
        "Programs": "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",
        "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    }
    KEYS = ("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders",
            "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders")

    def __init__(self, qa: Path):
        self.qa, self.original, self.registry_before = qa, {}, {}
        import ctypes
        self.ctypes = ctypes
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        self.shell.SHGetKnownFolderPath.restype = ctypes.c_long
        self.shell.SHSetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_wchar_p]
        self.shell.SHSetKnownFolderPath.restype = ctypes.c_long
        self.ole = ctypes.WinDLL("ole32")
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    def _guid(self, name):
        return self.ctypes.create_string_buffer(uuid.UUID(self.FOLDERS[name]).bytes_le)

    def get(self, name: str) -> Path:
        value = self.ctypes.c_wchar_p()
        code = self.shell.SHGetKnownFolderPath(self._guid(name), 0, None, self.ctypes.byref(value))
        if code < 0:
            raise ValueError("Cannot read the disposable runner's shortcut folder")
        try:
            return Path(value.value)
        finally:
            self.ole.CoTaskMemFree(self.ctypes.cast(value, self.ctypes.c_void_p))

    def set(self, name: str, path: Path) -> None:
        code = self.shell.SHSetKnownFolderPath(self._guid(name), 0, None, str(path))
        if code < 0:
            raise ValueError("Cannot redirect/restore the disposable runner's shortcut folder")

    def _values(self, path: str) -> dict:
        import winreg
        values = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (value, kind)
                index += 1
        return values

    def begin(self) -> None:
        self.registry_before = {key: self._values(key) for key in self.KEYS}
        self.original = {name: self.get(name) for name in self.FOLDERS}
        for name in self.FOLDERS:
            path = self.qa / "KnownFolders" / name
            path.mkdir(parents=True)
            self.set(name, path)
            if self.get(name).resolve() != path.resolve():
                raise ValueError("Native Windows shortcut folder did not resolve inside QA")

    def restore(self) -> None:
        import winreg
        changed = {}
        for key, before in self.registry_before.items():
            current = self._values(key)
            changed[key] = {name for name in set(before) | set(current) if before.get(name) != current.get(name)}
        for name, path in reversed(list(self.original.items())):
            self.set(name, path)
        for key_path, names in changed.items():
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                for name in names:
                    if name in self.registry_before[key_path]:
                        value, kind = self.registry_before[key_path][name]
                        winreg.SetValueEx(key, name, 0, kind, value)
                    else:
                        try:
                            winreg.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
        if any(self.get(name).resolve() != path.resolve() for name, path in self.original.items()):
            raise ValueError("A disposable runner shortcut folder was not restored")


def verify_adoption(target: str, candidates: Path, output: Path) -> dict:
    runner_temp = require_ephemeral_runner(target)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from desktop_updates.bootstrap import BASE_DIGEST, validate_baseline
    from desktop_updates.data_guard import (assert_attachments, assert_current, assert_retained,
                                            digest_file, logical_digest, snapshot)
    from desktop_updates.manager import UpdateManager
    from desktop_updates.protocol import canonical_json, strict_json
    from desktop_updates.transport import download_artifact
    from services.release_info import get_release_info

    candidates = candidates.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    qa = Path(tempfile.mkdtemp(prefix="ljqc-update-adoption-", dir=runner_temp)).resolve()
    if not qa.is_relative_to(runner_temp):
        raise ValueError("The adoption test escaped its runner temporary directory")
    logs = qa / "logs"
    logs.mkdir()
    data_root = qa / "customer-data"
    local = qa / "profile/LocalAppData"
    temporary = qa / "temp"
    temporary.mkdir()
    env = {**os.environ, "LJQC_DATA_ROOT": str(data_root), "LJQC_DEPLOYMENT_MODE": "desktop",
           "LOCALAPPDATA": str(local), "APPDATA": str(qa / "profile/AppData"), "USERPROFILE": str(qa / "profile"),
           "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary), "MPLCONFIGDIR": str(qa / "mpl"),
           "PYTHONUTF8": "1"}
    old_environment = os.environ.copy()
    os.environ.update(env)
    mounts, uninstallers = [], []
    known_folders = None
    result = {"status": "failed", "target": target, "base_release": "1.0.0-2026110606",
        "ephemeral_runner": True, "qa_root": str(qa), "native_business_gui_tested": False,
        "windows_10_11_customer_gui_tested": False, "gatekeeper_or_smartscreen_disabled": False,
        "production_trust_modified": False, "temporary_qa_trust_only": True,
        "source_manager_with_real_frozen_service": True, "production_data_access": False}

    def run(arguments: list[str], name: str, timeout: int = 180) -> subprocess.CompletedProcess:
        with (logs / (name + ".log")).open("wb") as log:
            return subprocess.run(arguments, cwd=qa, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  check=True, timeout=timeout)

    def install_inno(artifact: Path, destination: Path, name: str) -> Path:
        if not destination.resolve().is_relative_to(qa) or destination.exists():
            raise ValueError("The silent installer target is not a fresh QA directory")
        uninstaller = destination / "unins000.exe"
        uninstallers.append(uninstaller)
        run([str(artifact), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS",
             "/DIR=" + str(destination), "/LOG=" + str(logs / (name + "-inno.log"))], name, timeout=300)
        if not uninstaller.is_file() or not uninstaller.resolve().is_relative_to(qa):
            raise ValueError("The temporary installer did not provide its corresponding cleanup entry")
        return destination

    def mount_application(artifact: Path, application: str, destination: Path, name: str) -> Path:
        mount = qa / ("mount-" + name)
        mount.mkdir()
        run(["hdiutil", "verify", str(artifact)], name + "-verify", timeout=120)
        run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(artifact)], name + "-mount", timeout=120)
        mounts.append(mount)
        if not (mount / application).is_dir():
            raise ValueError("The native image does not contain its expected application")
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["ditto", str(mount / application), str(destination)], name + "-copy", timeout=180)
        if name == "bootstrap":
            helper_manifest = mount / "update_helper_manifest.json"
            if helper_manifest.is_symlink() or not helper_manifest.is_file():
                raise ValueError("The new helper image is missing its complete external byte manifest")
            shutil.copyfile(helper_manifest, destination.parent / helper_manifest.name)
        run(["codesign", "--verify", "--deep", "--strict", str(destination)], name + "-signature", timeout=120)
        run(["hdiutil", "detach", str(mount)], name + "-detach", timeout=120)
        mounts.remove(mount)
        return destination

    try:
        candidate = read_json(candidates / "candidate-update.json")
        bootstrap = read_json(candidates / "bootstrap-build-receipt.json")
        current = get_release_info(ROOT, include_source_state=True).as_dict()
        if (current["source_dirty"] or current["git_commit"] != os.environ["GITHUB_SHA"]
                or candidate.get("target") != target or bootstrap.get("target") != target
                or bootstrap.get("status") != "passed"):
            raise ValueError("Native adoption must use this clean successful CI source/target")
        for field, source in (("version", "version"), ("build_number", "build_number"),
                              ("git_commit", "source_commit"), ("source_digest", "source_digest")):
            if current[field] != candidate[source] or bootstrap["release"][field] != candidate[source]:
                raise ValueError("Bootstrap and patch do not have the same captured source")
        archive = candidates / candidate["artifact"]["url"].rsplit("/", 1)[-1]
        helper_artifact = candidates / bootstrap["artifact"]["file"]
        for path, identity in ((archive, candidate["artifact"]), (helper_artifact, bootstrap["artifact"])):
            if (path.is_symlink() or not path.is_file() or path.parent != candidates
                    or path.stat().st_size != identity["size_bytes"] or digest_file(path) != identity["sha256"]):
                raise ValueError("A candidate archive/bootstrap differs from its build receipt")
        baseline = read_json(ROOT / "resources/desktop_base_release.json")["releases"][target]
        download_dir = qa / "downloads"
        downloaded = download_artifact({"_trusted_origin": "https://159.75.243.120", "artifact": baseline},
                                      download_dir / baseline["url"].rsplit("/", 1)[-1])
        result["immutable_base_download"] = {"size_bytes": downloaded.stat().st_size, "sha256": digest_file(downloaded)}
        legacy_identity = LEGACY_HELPERS[target]
        legacy_artifact = download_artifact({"_trusted_origin": "https://159.75.243.120", "artifact": legacy_identity},
            download_dir / legacy_identity["url"].rsplit("/", 1)[-1])
        result["published_02_helper_download"] = {"size_bytes": legacy_artifact.stat().st_size,
                                                  "sha256": digest_file(legacy_artifact)}
        if target == "windows-x86_64":
            refuse_existing_windows_installation()
            known_folders = TemporaryKnownFolders(qa)
            with patch.dict(os.environ, old_environment, clear=True):
                known_folders.begin()
            installed = install_inno(downloaded, qa / "Installed/LJQCApp", "install-base")
            base = installed / "versions" / ("1.0.0-2026110606-" + BASE_DIGEST[:12])
            helper = install_inno(helper_artifact, qa / "BootstrapSource", "install-bootstrap")
            legacy_helper = install_inno(legacy_artifact, qa / "LegacyBootstrapSource", "install-legacy-bootstrap")
            helper_executable = helper / "LJQCAppUpdater.exe"
            helper_metadata = helper / "_internal/build_info.json"
            legacy_metadata = legacy_helper / "_internal/build_info.json"
        else:
            base = mount_application(downloaded, "LJQCApp.app", qa / "Installed/LJQCApp.app", "base")
            helper = mount_application(helper_artifact, "LJQCAppUpdater.app", qa / "BootstrapSource/LJQCAppUpdater.app", "bootstrap")
            legacy_helper = mount_application(legacy_artifact, "LJQCAppUpdater.app",
                qa / "LegacyBootstrapSource/LJQCAppUpdater.app", "legacy-bootstrap")
            helper_executable = helper / "Contents/MacOS/LJQCAppUpdater"
            helper_metadata = helper / "Contents/Resources/build_info.json"
            legacy_metadata = legacy_helper / "Contents/Resources/build_info.json"
        legacy_embedded = read_json(legacy_metadata)
        if (legacy_embedded.get("build_number") != 2026110702 or legacy_embedded.get("git_commit") != LEGACY_COMMIT
                or legacy_embedded.get("source_digest") != LEGACY_SOURCE_DIGEST
                or legacy_embedded.get("source_dirty") is not False or legacy_embedded.get("target") != target):
            raise ValueError("The downloaded public 02 helper carries an unexpected source identity")
        embedded = read_json(helper_metadata)
        if any(embedded.get(field) != current[field] for field in
               ("version", "build_number", "git_commit", "source_digest", "source_dirty")) or embedded.get("target") != target:
            raise ValueError("The actual installed frozen helper carries a different source identity")
        production_trust_before = digest_file(ROOT / "resources/desktop_update_trust.json")
        packaged_trust = helper / ("_internal/resources/desktop_update_trust.json" if target == "windows-x86_64"
                                   else "Contents/Resources/resources/desktop_update_trust.json")
        if not packaged_trust.is_file() or digest_file(packaged_trust) != production_trust_before:
            raise ValueError("The installed frozen helper has different production trust")
        run([str(helper_executable), "--version"], "installed-helper-version", timeout=60)
        run([str(helper_executable), "--self-test"], "installed-helper-public-config", timeout=60)
        # Exercise the helper extracted from the distributable Setup/DMG, not
        # just the pre-installer build directory or this source interpreter.
        from ci_verify_update import verify_frozen_tls
        result["installed_frozen_helper_production_tls"] = verify_frozen_tls(helper_executable, env=env,
            result_file=logs / "installed-helper-tls.json", log_file=logs / "installed-helper-tls.log")
        if data_root.exists():
            raise ValueError("A side-effect-free installed-helper probe touched business storage")

        def audit(event, arguments):
            if event != "sqlite3.connect" or str(arguments[0]) == ":memory:":
                return
            name = str(arguments[0])
            if name.startswith("file:"):
                from urllib.parse import urlsplit, unquote
                name = unquote(urlsplit(name).path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", name):
                    name = name[1:]
            if not Path(name).resolve().is_relative_to(qa):
                raise ValueError("Adoption acceptance attempted SQLite access outside the QA directory")
        sys.addaudithook(audit)
        initialize_original_schema(base, target, data_root, env, logs)
        fixture = create_business_fixture(data_root)
        before = snapshot(data_root, qa / "before-adoption")
        before_digest = logical_digest(Path(fixture["database"]))
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
        qa_trust = {**read_json(ROOT / "resources/desktop_update_trust.json"), "keys": {"qa-adoption-only": public}}
        trust_path = qa / "qa-only-trust.json"
        trust_path.write_bytes(canonical_json(qa_trust))
        trust_path.chmod(0o600)
        # Initial adoption/repair uses unchanged production trust. The temporary
        # QA key is selected only after repair for the isolated program apply.
        manager = UpdateManager(root=data_root, target=target)
        if target == "windows-x86_64":
            # Before allowing the old frozen component's real COM shortcut code,
            # verify WScript resolves both writable locations to the QA mapping.
            script = ("$s=New-Object -ComObject WScript.Shell; "
                      "@{Programs=$s.SpecialFolders.Item('Programs');Desktop=$s.SpecialFolders.Item('Desktop')}|ConvertTo-Json -Compress")
            locations = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                text=True, encoding="utf-8", capture_output=True, env=env, check=True, timeout=45)
            resolved = json.loads(locations.stdout.lstrip("\ufeff"))
            if any(Path(resolved[name]).resolve() != (qa / "KnownFolders" / name).resolve()
                   for name in ("Programs", "Desktop")):
                raise ValueError("The legacy component's real COM shortcut locations are not isolated to QA")
        legacy_executable = legacy_helper / ("LJQCAppUpdater.exe" if target == "windows-x86_64"
                                              else "Contents/MacOS/LJQCAppUpdater")
        run([str(legacy_executable), "--install", "--no-gui", "--base", str(base),
             "--helper", str(legacy_helper), "--data-root", str(data_root), "--target", target],
            "legacy-frozen-helper-adoption", timeout=180)
        receipt = read_json(manager.directory / "bootstrap.json")
        if target == "windows-x86_64" and (not receipt.get("shortcuts") or any(
                not Path(path).resolve().is_relative_to(qa) or not Path(path).is_file()
                for path in receipt["shortcuts"])):
            raise ValueError("The real legacy frozen component's shortcuts escaped its temporary QA mapping")
        result["real_02_frozen_cli_adoption"] = True
        old_state = manager.get_state()
        original = Path(receipt["original_program"])
        original_root = original.parent if target == "windows-x86_64" else original.parents[2]
        validate_baseline(original_root, target)
        assert_current(data_root, before)
        if old_state["current"]["build_number"] != 2026110606 or not Path(receipt["updater_entrypoint"]).is_file():
            raise ValueError("Real baseline/helper adoption did not register a valid stable entry")
        manager.check()  # Real signed public feed; retain a nonzero replay gate.
        if manager.get_state().get("highest_sequence", 0) <= 0:
            raise ValueError("The production metadata check did not establish a replay-protection sequence")
        # Repair a genuinely installed legacy component without registering the
        # base again or resetting current/sequence/customer data. Only the new
        # component source is eligible; its production trust is unchanged.
        state_before_repair = manager.state_path.read_bytes()
        bootstrap_path = manager.directory / "bootstrap.json"
        registration_before_repair = bootstrap_path.read_bytes()
        stable_executable = Path(receipt["updater_entrypoint"])
        stable_root = (stable_executable.parent if target == "windows-x86_64"
                       else stable_executable.parents[2])
        stable_metadata = stable_root / ("_internal/build_info.json" if target == "windows-x86_64"
                                         else "Contents/Resources/build_info.json")
        if read_json(stable_metadata).get("build_number") != 2026110702:
            raise ValueError("The real legacy component was not installed before repair")
        # Execute repair through the actual newly distributed frozen CLI. Its
        # data-root is this QA installation and its built-in production trust
        # remains unchanged; no --trust override exists on the customer CLI.
        run([str(helper_executable), "--install", "--no-gui", "--target", target,
             "--data-root", str(data_root)], "installed-frozen-helper-repair", timeout=180)
        repaired = read_json(bootstrap_path)
        if (manager.state_path.read_bytes() != state_before_repair
                or bootstrap_path.read_bytes() != registration_before_repair
                or repaired.get("updater_entrypoint") != receipt["updater_entrypoint"]):
            raise ValueError("Helper repair changed registered application state or its stable entry")
        from release_payload import validate_payload
        if validate_payload(stable_root) != validate_payload(helper):
            raise ValueError("The repaired stable component differs from the accepted frozen source")
        result["repaired_stable_helper_production_tls"] = verify_frozen_tls(stable_executable, env=env,
            result_file=logs / "repaired-stable-helper-tls.json", log_file=logs / "repaired-stable-helper-tls.log")
        validate_baseline(original_root, target)
        assert_current(data_root, before)
        result["real_02_helper_repaired_without_state_reset"] = True
        result["repair_retained_published_sequence"] = manager.get_state()["highest_sequence"]
        manager = UpdateManager(root=data_root, target=target, trust_path=trust_path)
        issued = datetime.now(timezone.utc)
        body = {**candidate, "issued_at": issued.isoformat(), "expires_at": (issued + timedelta(days=14)).isoformat(),
                "sequence": max(int(issued.timestamp() * 1_000_000), manager.get_state()["highest_sequence"] + 1)}
        raw = canonical_json({"signed": body, "key_id": "qa-adoption-only",
                              "signature": base64.b64encode(key.sign(canonical_json(body))).decode("ascii")})
        with patch("desktop_updates.manager.fetch_manifest", return_value=raw):
            accepted = manager.check()
        if accepted is None:
            raise ValueError("The actual base did not detect the higher signed QA patch")
        cached = manager.directory / "downloads" / (body["artifact"]["sha256"] + ".tar.gz")
        cached.parent.mkdir(parents=True)
        shutil.copyfile(archive, cached)
        verified_archive = manager.download(accepted)
        time.sleep(1.1)  # Make reference timestamp refresh regressions observable.
        state = manager.apply(accepted, verified_archive)
        if (state["current"]["build_number"] != candidate["build_number"]
                or state["current"]["source_commit"] != candidate["source_commit"]
                or state["previous"]["build_number"] != 2026110606 or manager.journal_path.exists()):
            raise ValueError("The real frozen patch did not complete its recoverable switch")
        assert_retained(qa / "before-adoption/database.db", Path(fixture["database"]))
        assert_attachments(Path(fixture["database"]), before["files"])
        after_digest = logical_digest(Path(fixture["database"]))
        with closing(sqlite3.connect(Path(fixture["database"]).as_uri() + "?mode=ro", uri=True)) as connection:
            pdf, sha = connection.execute("SELECT pdf_bytes,sha256 FROM report_export_files WHERE export_id=?", (fixture["report_id"],)).fetchone()
            if hashlib.sha256(pdf).hexdigest() != fixture["report_sha256"] or sha != fixture["report_sha256"]:
                raise ValueError("The original PDF BLOB was changed")
        validate_baseline(original_root, target)
        if digest_file(ROOT / "resources/desktop_update_trust.json") != production_trust_before or digest_file(packaged_trust) != production_trust_before:
            raise ValueError("QA acceptance modified production trust")
        with patch("desktop_updates.manager.fetch_manifest", return_value=raw):
            if manager.check() is not None:
                raise ValueError("The installed same-build patch was offered again")
        result.update(status="passed", release={field: candidate[field] for field in
            ("version", "build_number", "source_commit", "source_digest")},
            original_baseline_files_unchanged=True, real_baseline_installer_verified=True,
            real_frozen_helper_source_verified=True, actual_bootstrap_copy_and_registration=True,
            real_frozen_service_preflight_and_health=True, real_full_database_backup_and_retention=True,
            original_schema_created_by_frozen_100_app=True, fixture_did_not_run_new_schema_initializer=True,
            schema_tables=fixture["schema_tables"], pdf_blob_sha256=fixture["report_sha256"],
            managed_attachment_sha256=fixture["attachment_sha256"], original_typed_database_digest=before_digest,
            updated_typed_database_digest=after_digest, additive_schema_or_content_changed=after_digest != before_digest,
            no_repeated_update_offer=True, windows_shortcuts_redirected_to_qa=target == "windows-x86_64",
            mac_stable_entry_replaced_at_same_qa_path=target == "macos-arm64")
        return result
    except BaseException as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        cleanup_errors = []
        for mount in reversed(mounts):
            try:
                run(["hdiutil", "detach", str(mount)], "cleanup-mount", timeout=120)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(type(error).__name__ + ": detach")
        for uninstaller in reversed(uninstallers):
            try:
                if not uninstaller.is_file():
                    continue
                if not uninstaller.resolve().is_relative_to(qa):
                    raise ValueError("Refusing an uninstaller outside QA")
                run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "cleanup-" + uninstaller.parent.name, timeout=180)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                cleanup_errors.append(type(error).__name__ + ": uninstall")
        if target == "windows-x86_64" and uninstallers:
            deadline = time.monotonic() + 30
            while True:
                try:
                    refuse_existing_windows_installation()
                    break
                except ValueError:
                    if time.monotonic() >= deadline:
                        cleanup_errors.append("Temporary HKCU uninstall registration was not removed")
                        break
                    time.sleep(0.25)
        if known_folders is not None:
            try:
                with patch.dict(os.environ, old_environment, clear=True):
                    known_folders.restore()
                result["temporary_windows_known_folders_restored"] = True
            except (OSError, ValueError) as error:
                cleanup_errors.append(type(error).__name__ + ": KnownFolder restore")
                result["temporary_windows_known_folders_restored"] = False
        result["temporary_installer_cleanup"] = "passed" if not cleanup_errors else "failed"
        if cleanup_errors:
            result.update(status="failed", cleanup_errors=cleanup_errors)
        exported_logs = output / "adoption-verification-logs"
        exported_logs.mkdir(exist_ok=True)
        selected = list(logs.glob("*.log")) + list((data_root / "updates/transactions").glob("*/*.log"))
        for number, path in enumerate(selected[:24]):
            # Export only small tails; never copy a QA directory, database or trust.
            with path.open("rb") as content:
                content.seek(max(0, path.stat().st_size - 65536))
                (exported_logs / (f"{number:02d}-" + path.name)).write_bytes(content.read(65536))
        (output / "adoption-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.environ.clear()
        os.environ.update(old_environment)
        if cleanup_errors:
            raise RuntimeError("Temporary native installer cleanup did not finish; see bounded receipt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("windows-x86_64", "macos-arm64"), required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_adoption(args.target, args.candidates, args.output)
    print(json.dumps({"status": result["status"], "target": result["target"], "real_frozen_service_preflight_and_health": True}))


if __name__ == "__main__":
    main()
