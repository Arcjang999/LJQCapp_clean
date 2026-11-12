"""Build an isolated Apple Silicon application and pending-signing DMG."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.release_info import get_release_info, release_input_files
from release_payload import assert_protected_payload, digest_file, write_payload_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/installers')
    parser.add_argument('--skip-dmg', action='store_true', help='Build only the native program for a cumulative update patch')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Mac 安装包须在 Apple 芯片 Mac 上构建。')
    info = get_release_info(include_source_state=True).as_dict()
    now = datetime.now(timezone.utc)
    info.update(build_time=now.isoformat(), target='macos-arm64', signing_status='pending-developer-id-and-notarization')
    stage = args.output.resolve() / f"{info['version']}-{info['build_number']}-macos-arm64-{now.strftime('%Y%m%dT%H%M%SZ')}"
    stage.mkdir(parents=True, exist_ok=False)
    source = stage / 'source'
    for path in release_input_files(ROOT):
        target = source / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # The build reads only this fixed source snapshot, even if development continues.
    digest = hashlib.sha256()
    for path in release_input_files(source):
        digest.update(path.relative_to(source).as_posix().encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    if digest.hexdigest() != info['source_digest']:
        raise RuntimeError('源码在复制期间变化，请保留本次记录后重新构建。')
    (source / 'source_identity.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    metadata = stage / 'build_info.json'
    metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    protected = stage / 'protected_runtime'
    env = {**os.environ, 'LJQC_BUILD_INFO': str(metadata), 'PYTHONPATH': str(source),
           'LJQC_PROTECTED_RUNTIME': str(protected), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYINSTALLER_CONFIG_DIR': str(stage / 'pyinstaller-cache')}
    logs = stage / 'logs'
    logs.mkdir()
    def run(command: list[str], name: str) -> None:
        print(name + '...', flush=True)
        with (logs / (name + '.log')).open('w', encoding='utf-8') as log:
            subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    run([sys.executable, str(source / 'packaging/protected_runtime.py'), '--root', str(source),
         '--output', str(protected), '--work', str(stage / 'protected_compiler')], 'compile-business-modules')
    iconset = stage / 'LJQCApp.iconset'
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            run(['sips', '-z', str(size * scale), str(size * scale), str(source / 'assets/app_icon.png'), '--out', str(iconset / name)], 'icon-' + name)
    icon = stage / 'LJQCApp.icns'
    run(['iconutil', '-c', 'icns', str(iconset), '-o', str(icon)], 'iconutil')
    env['LJQC_MAC_ICON'] = str(icon)
    for name, spec in (('service', 'LJQCAppMacService.spec'), ('desktop', 'LJQCAppMac.spec')):
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(stage / 'dist'),
             '--workpath', str(stage / 'work' / name), str(source / 'packaging' / spec)], name)
    # Documents may be managed by File Provider, which asynchronously attaches
    # FinderInfo to .app folders. Assemble/sign the disk image in a local temp
    # directory so its sealed bundle is not changed after signature verification.
    image_root = Path(tempfile.mkdtemp(prefix='ljqcapp-mac-image-', dir='/private/tmp')) / 'image'
    image_root.mkdir()
    app = image_root / 'LJQCApp.app'
    shutil.copytree(stage / 'dist/LJQCApp.app', app, symlinks=True)
    shutil.copytree(stage / 'dist/LJQCAppService', app / 'Contents/Resources/app', symlinks=True)
    shutil.copy2(metadata, app / 'Contents/Resources/build_info.json')
    # Finder can attach presentation metadata to newly assembled .app folders.
    # Remove only signing-incompatible resource forks; keep quarantine intact.
    for attribute in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
        with (logs / ('clean-' + attribute + '.log')).open('w') as log:
            result = subprocess.run(['xattr', '-dr', attribute, str(app)], stdout=log, stderr=subprocess.STDOUT)
            if result.returncode not in (0, 1):
                raise RuntimeError('Cannot clean bundle presentation metadata')
    # Ad-hoc signing permits verification of this build. It is not Developer ID signing.
    run(['codesign', '--force', '--deep', '--sign', '-', str(app)], 'adhoc-sign')
    run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], 'verify-signature')
    (image_root / '安装说明.txt').write_text(
        'LJQCApp ' + info['version'] + '（Apple 芯片 Mac，macOS 13 或以上）\n\n'
        '将 LJQCApp 拖入 Applications 后打开。新安装从空白资料开始，并自动显示首次引导。\n'
        '升级前请正常退出原程序，并在系统设置中保存完整备份。替换程序会继续使用原资料目录。\n'
        '资料目录：~/Library/Application Support/LJQCApp；卸载程序不会删除资料。\n\n'
        '本包待 Apple Developer ID 签名和公证，供安装验收；尚不是已公证的公开分发版本。\n'
        '旧解压版迁入：先在原软件保存完整备份，再从新软件的系统设置恢复完整备份。\n'
        '需要诊断时，在终端运行："/Applications/LJQCApp.app/Contents/MacOS/LJQCApp" --diagnose\n', encoding='utf-8')
    assert_protected_payload(image_root)
    write_payload_manifest(image_root, info)
    shutil.copy2(image_root / 'release_payload_manifest.json', stage / 'release_payload_manifest.json')
    if args.skip_dmg:
        summary = {**info, 'artifact_kind': 'program-payload', 'application': str(app),
                   'payload_root': str(image_root), 'source_snapshot': str(source),
                   'base_installer_rebuilt': False}
        (stage / 'artifact.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return
    (image_root / 'Applications').symlink_to('/Applications')
    dmg = stage / f"LJQCApp-{info['version']}-{info['build_number']}-macos-arm64-pending-signing.dmg"
    run(['hdiutil', 'create', '-volname', 'LJQCApp', '-srcfolder', str(image_root), '-format', 'UDZO', '-ov', str(dmg)], 'create-dmg')
    summary = {**info, 'artifact': str(dmg), 'size_bytes': dmg.stat().st_size, 'sha256': digest_file(dmg),
               'application': str(app), 'source_snapshot': str(source)}
    (stage / 'artifact.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
