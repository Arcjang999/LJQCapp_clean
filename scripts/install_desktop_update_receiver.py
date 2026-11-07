"""Prepare the isolated desktop-update receiver; never modify business services.

Default operations are read-only. `install` must be run as root after reviewing
`check`; it provisions only the dedicated receiver and its static subtrees.
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tarfile

if os.name == "posix":
    import grp
    import pwd

SOURCE = Path(__file__).resolve().parents[1]
USER = "ljqc-update"
TOOLS = Path("/opt/ljqc-update-publisher")
HOME = TOOLS / "home"
STAGING = Path("/var/lib/ljqc-update-publisher")
PUBLIC = Path("/var/www/ljqc-public")
BUSINESS = Path("/var/lib/ljqc")
SHELL = TOOLS / "receiver-shell"
TRUST = TOOLS / "resources/desktop_update_trust.json"
FORCED = (f"{TOOLS}/venv/bin/python -I {TOOLS}/packaging/publish_desktop_update.py receive-and-promote "
          f"--staging-root {STAGING} --public-root {PUBLIC} --trust {TRUST}")
MANIFEST = "receiver-tools-manifest.json"
FILES = (
    "scripts/install_desktop_update_receiver.py", "packaging/publish_desktop_update.py",
    "desktop_updates/__init__.py", "desktop_updates/protocol.py", "desktop_updates/transport.py",
    "resources/desktop_update_trust.json",
)
WRAPPER = f'''#!/bin/sh
# Root-owned constrained login shell. No arbitrary command or interactive shell.
[ "$#" -eq 2 ] && [ "$1" = "-c" ] && [ "$2" = '{FORCED}' ] || exit 126
[ "${{SSH_ORIGINAL_COMMAND-}}" = 'ljqc-update-upload' ] || exit 126
exec /usr/bin/env -i HOME='{HOME}' USER='{USER}' LOGNAME='{USER}' PATH='/usr/bin:/bin' LANG='C.UTF-8' SSH_ORIGINAL_COMMAND="${{SSH_ORIGINAL_COMMAND-}}" {FORCED}
'''
NGINX_PROPOSAL = r'''# PROPOSAL ONLY: insert inside the existing HTTPS server block after review.
# Do not replace the existing server, TLS, ACME, /downloads/ or proxy settings.
location ^~ /updates/ {
    root /var/www/ljqc-public;
    default_type application/json;
    autoindex off;
    if ($uri !~ "\.json$") { return 404; }
    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options "nosniff" always;
    limit_except GET HEAD { deny all; }
    try_files $uri =404;
}
# Optional HTTP server addition, reviewed separately:
# location ^~ /updates/ { return 301 https://159.75.243.120$request_uri; }
'''


def digest(path: Path) -> str:
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()


def public_key(path: Path) -> tuple[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("The authorized-key input must be a small ordinary public-key file")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError("Provide exactly one Ed25519 SSH public key, never a private key")
    parts = lines[0].strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Only an ssh-ed25519 public key is accepted")
    wire = base64.b64decode(parts[1], validate=True)
    if len(wire) < 4:
        raise ValueError("Invalid SSH public-key encoding")
    size = struct.unpack(">I", wire[:4])[0]
    if wire[4:4 + size] != b"ssh-ed25519" or len(wire) < 8 + size:
        raise ValueError("Invalid Ed25519 SSH public-key record")
    length = struct.unpack(">I", wire[4 + size:8 + size])[0]
    if length != 32 or len(wire) != 8 + size + length:
        raise ValueError("An Ed25519 SSH public key must contain exactly 32 key bytes")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")
    normalized = "ssh-ed25519 " + parts[1] + " ljqc-update-deploy"
    return normalized, fingerprint


def reject_symlink_components(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("Receiver paths must be absolute")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("A managed directory contains a symbolic link: " + str(cursor))


def directory(path: Path, uid: int | None, mode: int, *, required: bool = False) -> None:
    reject_symlink_components(path)
    if not path.exists():
        if required:
            raise ValueError("A required existing directory is missing: " + str(path))
        return
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or uid is None or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != mode):
        raise ValueError("Existing directory ownership/mode differs; no permission was expanded: " + str(path))


def checked_existing_file(path: Path, content: bytes, mode: int) -> None:
    reject_symlink_components(path.parent)
    if path.is_symlink():
        raise ValueError("A managed file is a symbolic link: " + str(path))
    if path.exists():
        info = path.stat()
        if (not path.is_file() or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode
                or path.read_bytes() != content):
            raise ValueError("Existing tool/configuration differs; review it before replacement: " + str(path))


def read_source_manifest(source: Path) -> dict:
    manifest_path = source / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Use the explicit receiver tool bundle with its manifest")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (value.get("format") != "ljqc-update-receiver-tools" or value.get("format_version") != 1
            or value.get("mutable_data_included") is not False or value.get("source_dirty") is not False
            or set(value.get("files", {})) != set(FILES)):
        raise ValueError("Receiver tool bundle provenance is incomplete")
    for name in FILES:
        path = source / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve())
                or digest(path) != value["files"][name]):
            raise ValueError("A receiver source file differs from its recorded digest: " + name)
    trust = json.loads((source / "resources/desktop_update_trust.json").read_text(encoding="utf-8"))
    if (trust.get("product") != "LJQCApp" or trust.get("channel") != "dev"
            or trust.get("base_release") != "1.0.0-2026110606"
            or trust.get("origin") != "https://159.75.243.120" or not trust.get("keys")):
        raise ValueError("The receiver public trust does not match this project's fixed baseline")
    for key in trust["keys"].values():
        if len(base64.b64decode(key, validate=True)) != 32:
            raise ValueError("Invalid release public key in the receiver bundle")
    return value


def make_bundle(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("Receiver tool bundles are immutable; choose a new output path")
    process = subprocess.run(["git", "-C", str(SOURCE), "status", "--porcelain"], capture_output=True, text=True, check=True)
    if process.stdout.strip():
        raise ValueError("Commit the reviewed source checkpoint before exporting receiver tools")
    commit = subprocess.run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    files = {}
    for name in FILES:
        path = SOURCE / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(SOURCE.resolve()):
            raise ValueError("Receiver export accepts only its fixed program/public-resource allowlist")
        files[name] = digest(path)
    manifest = {"format": "ljqc-update-receiver-tools", "format_version": 1,
                "source_commit": commit, "source_dirty": False, "mutable_data_included": False,
                "files": files}
    import io
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode, info.mtime = 0o644, 0
        return info
    with tarfile.open(output, "x:gz", dereference=False) as archive:
        for name in FILES:
            archive.add(SOURCE / name, arcname=name, recursive=False, filter=normalized)
        info = tarfile.TarInfo(MANIFEST)
        info = normalized(info)
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    return {"status": "receiver_tools_exported", "file": str(output.resolve()),
            "sha256": digest(output), "size_bytes": output.stat().st_size,
            "source_commit": commit, "mutable_data_included": False, "private_keys_included": False}


def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=check, capture_output=True, text=True)


def account() -> pwd.struct_passwd | None:
    try:
        value = pwd.getpwnam(USER)
    except KeyError:
        try:
            grp.getgrnam(USER)
        except KeyError:
            return None
        raise ValueError("The dedicated group already exists without its expected account")
    if (value.pw_uid == 0 or value.pw_dir != str(HOME) or value.pw_shell != str(SHELL)
            or grp.getgrgid(value.pw_gid).gr_name != USER
            or set(grp.getgrgid(value.pw_gid).gr_mem) - {USER}
            or set(os.getgrouplist(USER, value.pw_gid)) != {value.pw_gid}):
        raise ValueError("Existing receiver account differs or has additional group access; no account was changed")
    if os.geteuid() == 0:
        status = command(["passwd", "-S", USER]).stdout.split()
        if len(status) < 2 or status[1] != "L":
            raise ValueError("The dedicated account password must already be locked")
        if command(["sudo", "-n", "-l", "-U", USER], check=False).returncode == 0:
            raise ValueError("The dedicated account unexpectedly has sudo access")
    return value


def check_existing_venv() -> None:
    root = TOOLS / "venv"
    if not root.exists():
        return
    for path in root.rglob("*"):
        info = path.lstat()
        if info.st_uid != 0 or (not path.is_symlink() and stat.S_IMODE(info.st_mode) & 0o7022):
            raise ValueError("Existing receiver runtime is writable outside root: " + str(path))
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Existing receiver runtime contains an unexpected external link: " + str(path))
    executable = root / "bin/python"
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("The dedicated runtime must use an ordinary copied Python executable")


def check_sshd() -> None:
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    result = command([sshd, "-T", "-C", f"user={USER},host=localhost,addr=127.0.0.1"])
    config = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        config.setdefault(key, []).append(value)
    if (config.get("pubkeyauthentication") != ["yes"] or config.get("chrootdirectory", ["none"]) != ["none"]
            or config.get("forcecommand", ["none"]) != ["none"]
            or config.get("authenticationmethods", ["any"])[0] not in ("any", "publickey")):
        raise ValueError("Effective SSH policy needs manual review for the dedicated receiver")
    if config.get("permituserenvironment", ["no"]) != ["no"]:
        raise ValueError("SSH PermitUserEnvironment must be disabled before installing the constrained receiver")
    if config.get("usepam") != ["yes"]:
        raise ValueError("This Ubuntu locked-account setup requires UsePAM=yes for public-key login")
    accepted = " ".join(config.get("acceptenv", [])).split()
    for name in accepted:
        if name in ("LANG", "LANGUAGE", "LC_*"):
            continue
        if name.startswith("LC_") and name.replace("_", "").isalnum():
            continue
        raise ValueError("SSH AcceptEnv permits a non-locale variable; review policy before installing the constrained receiver")
    authorized = " ".join(config.get("authorizedkeysfile", []))
    if not any(path in authorized.split() for path in (".ssh/authorized_keys", "%h/.ssh/authorized_keys")):
        raise ValueError("SSH does not use the expected root-owned authorized_keys location")
    for setting, name, deny in (("allowusers", USER, False), ("denyusers", USER, True),
                                ("allowgroups", USER, False), ("denygroups", USER, True)):
        patterns = " ".join(config.get(setting, [])).split()
        if patterns:
            matched = any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
            if matched == deny:
                raise ValueError("SSH allow/deny policy excludes the dedicated receiver; no SSH configuration was changed")


def preflight(key_file: Path) -> tuple[dict, str, dict]:
    manifest = read_source_manifest(SOURCE)
    key, fingerprint = public_key(key_file)
    value = account()
    uid = value.pw_uid if value else None
    for path in (TOOLS.parent, STAGING.parent, PUBLIC.parent):
        directory(path, 0, 0o755, required=True)
    directory(PUBLIC, 0, 0o755, required=True)
    directory(PUBLIC / "downloads", 0, 0o755, required=True)
    for path in (TOOLS, TOOLS / "scripts", TOOLS / "packaging", TOOLS / "desktop_updates",
                 TOOLS / "resources", HOME, HOME / ".ssh", PUBLIC / "updates", PUBLIC / "updates/desktop"):
        directory(path, 0, 0o755)
    directory(TOOLS / "venv", 0, 0o755)
    directory(TOOLS / "venv/bin", 0, 0o755)
    check_existing_venv()
    for path in (PUBLIC / "downloads/patches", PUBLIC / "downloads/update-bootstrap", PUBLIC / "updates/desktop/dev"):
        directory(path, uid, 0o755)
    directory(STAGING, uid, 0o700)
    reject_symlink_components(BUSINESS)
    if not BUSINESS.is_dir() or BUSINESS.stat().st_mode & 0o007:
        raise ValueError("Business-directory isolation must be reviewed first; this script never changes its permissions")
    if value and (BUSINESS.stat().st_uid == value.pw_uid or BUSINESS.stat().st_gid == value.pw_gid):
        raise ValueError("Receiver account overlaps business-directory ownership")
    if value and os.geteuid() == 0:
        for access in ("-r", "-x"):
            if command(["runuser", "-u", USER, "--", "/usr/bin/test", access, str(BUSINESS)], check=False).returncode == 0:
                raise ValueError("The existing receiver can access the business directory; no permissions were changed")
    for name in FILES:
        checked_existing_file(TOOLS / name, (SOURCE / name).read_bytes(), 0o444)
    checked_existing_file(TOOLS / MANIFEST, (SOURCE / MANIFEST).read_bytes(), 0o444)
    authorized = 'restrict,command="' + FORCED + '" ' + key + "\n"
    checked_existing_file(HOME / ".ssh/authorized_keys", authorized.encode(), 0o644)
    checked_existing_file(SHELL, WRAPPER.encode(), 0o555)
    checked_existing_file(TOOLS / "nginx-updates-location.proposal.conf", NGINX_PROPOSAL.encode(), 0o444)
    if os.geteuid() == 0:
        check_sshd()
    return {"status": "preflight_passed", "user": USER, "create_account": value is None,
            "ssh_public_key_fingerprint": fingerprint, "source_commit": manifest["source_commit"],
            "forced_command": FORCED, "nginx_configuration_changed": False,
            "business_configuration_changed": False}, authorized, manifest


def create_directory(path: Path, uid: int, gid: int, mode: int) -> None:
    if not path.exists():
        path.mkdir(parents=True, mode=mode)
        os.chown(path, uid, gid)
        path.chmod(mode)


def create_file(path: Path, content: bytes, mode: int) -> None:
    if not path.exists():
        with path.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chown(path, 0, 0)
        path.chmod(mode)


def install(key_file: Path, python: str) -> dict:
    if os.geteuid() != 0:
        raise ValueError("The reviewed installation must run as root")
    result, authorized, manifest = preflight(key_file)
    for path in (TOOLS, TOOLS / "scripts", TOOLS / "packaging", TOOLS / "desktop_updates", TOOLS / "resources", HOME, HOME / ".ssh"):
        create_directory(path, 0, 0, 0o755)
    for name in FILES:
        create_file(TOOLS / name, (SOURCE / name).read_bytes(), 0o444)
    create_file(TOOLS / MANIFEST, (SOURCE / MANIFEST).read_bytes(), 0o444)
    create_file(SHELL, WRAPPER.encode(), 0o555)
    if not (TOOLS / "venv").exists():
        command([python, "-m", "venv", "--copies", str(TOOLS / "venv")])
        command([str(TOOLS / "venv/bin/python"), "-I", "-m", "pip", "install", "--disable-pip-version-check", "cryptography==50.0.2"])
    command([str(TOOLS / "venv/bin/python"), "-I", "-c", "import cryptography; assert cryptography.__version__ == '50.0.2'"])
    if account() is None:
        command(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", str(HOME), "--shell", str(SHELL), USER])
        command(["passwd", "-l", USER])
    value = account()
    assert value is not None
    # Match Group conditions may start applying only after a new group exists.
    # Recheck before installing any usable authentication key.
    check_sshd()
    for path in (PUBLIC / "updates", PUBLIC / "updates/desktop"):
        create_directory(path, 0, 0, 0o755)
    for path in (PUBLIC / "downloads/patches", PUBLIC / "downloads/update-bootstrap", PUBLIC / "updates/desktop/dev"):
        create_directory(path, value.pw_uid, value.pw_gid, 0o755)
    create_directory(STAGING, value.pw_uid, value.pw_gid, 0o700)
    for access in ("-r", "-x"):
        if command(["runuser", "-u", USER, "--", "/usr/bin/test", access, str(BUSINESS)], check=False).returncode == 0:
            raise ValueError("Receiver could access the business directory; no SSH key was installed")
    create_file(HOME / ".ssh/authorized_keys", authorized.encode(), 0o644)
    create_file(TOOLS / "nginx-updates-location.proposal.conf", NGINX_PROPOSAL.encode(), 0o444)
    if command(["runuser", "-u", USER, "--", str(SHELL), "-c", "id"], check=False).returncode != 126:
        raise ValueError("The constrained receiver shell did not reject an arbitrary command")
    # Verify exactly what was installed; never recursively chmod/chown a tree.
    preflight(key_file)
    return {**result, "status": "receiver_installed", "create_account": False,
            "arbitrary_shell_rejected": True, "business_directory_inaccessible": True,
            "nginx_proposal": str(TOOLS / "nginx-updates-location.proposal.conf"),
            "public_key_installed": True, "source_commit": manifest["source_commit"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exporting = sub.add_parser("bundle")
    exporting.add_argument("--output", type=Path, required=True)
    for name in ("check", "install"):
        operation = sub.add_parser(name)
        operation.add_argument("--authorized-key-file", type=Path, required=True)
        if name == "install":
            operation.add_argument("--python", default="/usr/bin/python3")
    args = parser.parse_args()
    if args.command != "bundle" and sys.platform != "linux":
        raise ValueError("Receiver check/install operations are intended for the Ubuntu cloud server")
    if args.command == "bundle":
        result = make_bundle(args.output)
    elif args.command == "check":
        result, _, _ = preflight(args.authorized_key_file)
    else:
        result = install(args.authorized_key_file, args.python)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "stopped", "reason": str(error),
                          "nginx_configuration_changed": False, "business_configuration_changed": False}), file=sys.stderr)
        raise SystemExit(1)
