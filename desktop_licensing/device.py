"""Private per-device credentials, outside movable application business data."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .protocol import LicenseError, canonical_json, strict_json


class DeviceIdentityError(LicenseError):
    pass


def default_state_dir() -> Path:
    """License state must never follow a selected database or business backup."""
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA', '').strip()
        if not local:
            raise DeviceIdentityError('无法确定本机授权资料目录。')
        return Path(local) / 'LJQCAppLicensing'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'LJQCAppLicensing'
    return Path.home() / '.local' / 'share' / 'LJQCAppLicensing'


def machine_fingerprint() -> str:
    """Hash an OS machine identifier; no address, MAC or raw serial is sent."""
    identifier = ''
    try:
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                identifier = str(winreg.QueryValueEx(key, 'MachineGuid')[0]).strip()
        elif sys.platform == 'darwin':
            result = subprocess.run(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=5)
            if len(result.stdout) > 64 * 1024:
                raise ValueError('hardware response too large')
            match = re.search(rb'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{16,64})"', result.stdout)
            identifier = match.group(1).decode('ascii') if match else ''
        else:
            for path in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')):
                if path.is_file():
                    identifier = path.read_text(encoding='ascii').strip()
                    if identifier:
                        break
        if not identifier or len(identifier) > 256:
            raise ValueError('machine identifier unavailable')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise DeviceIdentityError('无法识别本机设备，请联系软件维护人员。') from error
    return hashlib.sha256(('LJQCApp-device-v1\n' + sys.platform + '\n' + identifier.lower()).encode('utf-8')).hexdigest()


def _secure_permissions(path: Path, *, directory: bool = False) -> None:
    os.chmod(path, 0o700 if directory else 0o600)
    if os.name != 'nt':
        return
    # chmod alone does not set an owner-only Windows DACL. Apply one explicitly;
    # no shell is used and the SID comes from the current Windows access token.
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = ctypes.WinDLL('advapi32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    token = wintypes.HANDLE()
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD))
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    sid_string = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError(ctypes.get_last_error(), 'OpenProcessToken')
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        information = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, information, size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), 'GetTokenInformation')
        sid = ctypes.cast(information, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise OSError(ctypes.get_last_error(), 'ConvertSidToStringSid')
        rights = '(A;OICI;FA;;;' if directory else '(A;;FA;;;'
        sddl = 'D:P' + rights + sid_string.value + ')'
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1,
                ctypes.byref(descriptor), None):
            raise OSError(ctypes.get_last_error(), 'ConvertStringSecurityDescriptor')
        if not advapi32.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError(ctypes.get_last_error(), 'SetFileSecurity')
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)
        if sid_string:
            kernel32.LocalFree(ctypes.cast(sid_string, wintypes.HLOCAL))
        if token:
            kernel32.CloseHandle(token)


def ensure_private_directory(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise DeviceIdentityError('本机授权资料目录无效。')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise DeviceIdentityError('本机授权资料目录无效。')
    _secure_permissions(path, directory=True)
    return path


def read_private_json(path: Path) -> dict | None:
    if path.is_symlink():
        raise DeviceIdentityError('本机授权资料不能使用链接文件。')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise DeviceIdentityError('本机授权资料格式无效。')
    _secure_permissions(path)
    data = strict_json(path.read_bytes())
    if not isinstance(data, dict):
        raise DeviceIdentityError('本机授权资料格式无效。')
    return data


def write_private_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise DeviceIdentityError('本机授权资料不能使用链接文件。')
    encoded = canonical_json(value)
    if len(encoded) > 64 * 1024:
        raise DeviceIdentityError('本机授权资料大小无效。')
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _secure_permissions(temporary)
        os.replace(temporary, path)
        _secure_permissions(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


@contextmanager
def state_lock(directory: Path, name: str):
    path = directory / name
    if path.is_symlink():
        raise DeviceIdentityError('本机授权锁定记录无效。')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    handle = os.fdopen(descriptor, 'r+b')
    locked = False
    try:
        _secure_permissions(path)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@dataclass(frozen=True)
class DeviceIdentity:
    machine_hash: str
    private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> str:
        return base64.b64encode(self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.private_key.sign(payload)).decode('ascii')


def load_or_create_device(directory: str | Path, *, machine_hash: str | None = None) -> DeviceIdentity:
    directory = ensure_private_directory(directory)
    fingerprint = machine_fingerprint() if machine_hash is None else machine_hash
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise DeviceIdentityError('本机设备标识无效。')
    with state_lock(directory, 'device.lock'):
        path = directory / 'device.json'
        data = read_private_json(path)
        if data is not None:
            if (set(data) != {'format_version', 'machine_hash', 'private_key'}
                    or type(data['format_version']) is not int or data['format_version'] != 1
                    or data['machine_hash'] != fingerprint):
                raise DeviceIdentityError('授权资料与本机不匹配，请恢复本机授权资料或联系软件维护人员。')
            try:
                raw = base64.b64decode(data['private_key'], validate=True)
                key = Ed25519PrivateKey.from_private_bytes(raw)
            except (ValueError, TypeError) as error:
                raise DeviceIdentityError('本机授权密钥无法读取，请联系软件维护人员。') from error
            return DeviceIdentity(fingerprint, key)
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        write_private_json(path, dict(format_version=1, machine_hash=fingerprint,
                                     private_key=base64.b64encode(raw).decode('ascii')))
        return DeviceIdentity(fingerprint, key)
