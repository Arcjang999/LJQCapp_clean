"""Exercise the actual frozen activation page against its production trust.

Only disposable native GitHub runners may run this probe.  The test code is a
short-lived server-issued customer code, never a signing key or trust override.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid


def _rerun(connection, port: int, *, page_hash: str = '', widgets=()):
    from streamlit.proto.BackMsg_pb2 import BackMsg
    message = BackMsg()
    rerun = message.rerun_script
    rerun.query_string = ''
    rerun.page_script_hash = page_hash
    rerun.page_name = ''
    rerun.context_info.url = f'http://127.0.0.1:{port}/'
    rerun.context_info.locale = 'zh-CN'
    rerun.context_info.timezone = 'Asia/Shanghai'
    for identifier, kind, value in widgets:
        state = rerun.widget_states.widgets.add()
        state.id = identifier
        setattr(state, kind, value)
    connection.send(message.SerializeToString())


def _page(connection) -> dict:
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    result = {'headings': [], 'text': [], 'errors': [], 'inputs': {}, 'buttons': {}, 'hash': ''}
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            raw = connection.recv(timeout=min(15, max(.1, deadline - time.monotonic())))
        except TimeoutError:
            continue
        if not isinstance(raw, bytes):
            raise ValueError('Frozen activation returned an unexpected transport frame')
        message = ForwardMsg()
        message.ParseFromString(raw)
        if message.HasField('new_session'):
            result = {'headings': [], 'text': [], 'errors': [], 'inputs': {}, 'buttons': {},
                      'hash': message.new_session.page_script_hash or message.new_session.main_script_hash}
        if message.HasField('delta') and message.delta.HasField('new_element'):
            element = message.delta.new_element
            if element.HasField('exception'):
                raise ValueError('The frozen activation page raised an exception')
            if element.HasField('heading'):
                result['headings'].append(element.heading.body)
            if element.HasField('markdown'):
                result['text'].append(element.markdown.body)
            if element.HasField('alert') and element.alert.format == element.alert.ERROR:
                result['errors'].append(element.alert.body)
            if element.HasField('text_input'):
                result['inputs'][element.text_input.label] = element.text_input.id
            if element.HasField('button'):
                result['buttons'][element.button.label] = element.button.id
        if message.HasField('script_finished'):
            if message.script_finished == ForwardMsg.FINISHED_WITH_COMPILE_ERROR:
                raise ValueError('The frozen activation page did not compile')
            if message.script_finished == ForwardMsg.FINISHED_SUCCESSFULLY:
                return result
    raise ValueError('The frozen activation page did not finish within its bounded timeout')


def _activation(page: dict) -> bool:
    return ('软件激活' in page['headings'] and '验证码' in page['inputs']
            and '验证并进入软件' in page['buttons'])


def _business_page(page: dict) -> bool:
    visible = '\n'.join(page['headings'] + page['text'])
    return (not _activation(page) and not page['errors']
            and any(label in visible for label in ('首次引导', '选择质控方法', '项目工作台', '我的项目')))


def verify_frozen_license(service: Path, target: str, output: Path, *, env: dict | None = None,
                          code: str | None = None) -> dict:
    actual = ('windows-x86_64' if sys.platform == 'win32' else
              'macos-arm64' if sys.platform == 'darwin' and platform.machine() == 'arm64' else None)
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or actual != target):
        raise ValueError('Frozen activation acceptance requires the matching disposable native GitHub runner')
    env = dict(os.environ if env is None else env)
    code = code if code is not None else env.get('LJQC_CI_LICENSE_CODE', '')
    if not code or len(code) > 160:
        raise ValueError('A short-lived CI activation code must be configured before native acceptance')
    service, output = service.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / 'native-license-verification.json'
    if receipt_path.exists():
        raise ValueError('Frozen activation acceptance cannot overwrite an existing receipt')
    root = Path(__file__).resolve().parents[1]
    declarations = list(service.parent.rglob('protected_runtime_manifest.json'))
    if len(declarations) != 1:
        raise ValueError('The frozen service has no unique protected runtime declaration')
    declaration = json.loads(declarations[0].read_text(encoding='utf-8'))
    trust = declarations[0].parent / 'resources/desktop_license_trust.json'
    expected = root / 'resources/desktop_license_trust.json'
    digest = hashlib.sha256(trust.read_bytes()).hexdigest()
    if digest != declaration.get('license_trust_sha256') or trust.read_bytes() != expected.read_bytes():
        raise ValueError('Frozen authorization must use the unchanged production trust')
    qa = Path(tempfile.mkdtemp(prefix='ljqc-frozen-license-')).resolve()
    data = qa / 'business'
    runtime = data / 'runtime'
    runtime.mkdir(parents=True)
    # Licensing storage is intentionally independent from movable business data.
    # Windows can use a fresh OS profile directory; Mac runners are themselves
    # disposable and must have no preexisting LJQC authorization identity.
    if target == 'windows-x86_64':
        if not env.get('LOCALAPPDATA'):
            raise ValueError('The isolated Windows profile is unavailable')
        state = Path(env['LOCALAPPDATA']) / 'LJQCAppLicensing'
    else:
        state = Path.home() / 'Library/Application Support/LJQCAppLicensing'
    if state.exists():
        raise ValueError('Native activation acceptance requires a fresh authorization identity')
    env.update(LJQC_DATA_ROOT=str(data), LJQC_DEPLOYMENT_MODE='desktop', PYTHONUTF8='1')
    # The customer program only receives the code through the actual form.
    env.pop('LJQC_CI_LICENSE_CODE', None)
    from websockets.sync.client import connect
    for attempt in range(2):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        request = runtime / ('shutdown-' + uuid.uuid4().hex + '.request')
        log_path = output / ('frozen-license-' + str(attempt) + '.log')
        process = None
        try:
            with log_path.open('wb') as log:
                options = ({'creationflags': subprocess.CREATE_NO_WINDOW} if target == 'windows-x86_64'
                           else {'start_new_session': True})
                process = subprocess.Popen([str(service), '--port', str(port), '--address', '127.0.0.1'],
                    cwd=service.parent, env={**env, 'LJQC_SHUTDOWN_FILE': str(request)},
                    stdout=log, stderr=subprocess.STDOUT, **options)
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise ValueError('Frozen licensed service exited before its health endpoint')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    time.sleep(.25)
                else:
                    raise ValueError('Frozen licensed service health timed out')
                options = dict(origin=f'http://127.0.0.1:{port}', subprotocols=['streamlit'],
                               open_timeout=15, close_timeout=5, max_size=64 * 1024**2)
                if 'proxy' in inspect.signature(connect).parameters:
                    options['proxy'] = None
                with connect(f'ws://127.0.0.1:{port}/_stcore/stream', **options) as connection:
                    _rerun(connection, port)
                    page = _page(connection)
                    if attempt == 0:
                        if not _activation(page) or list(data.rglob('*.db')):
                            raise ValueError('An unactivated frozen program entered business data')
                        _rerun(connection, port, page_hash=page['hash'], widgets=(
                            (page['inputs']['验证码'], 'string_value', 'CI-INVALID-CODE'),
                            (page['buttons']['验证并进入软件'], 'trigger_value', True)))
                        page = _page(connection)
                        if not _activation(page) or list(data.rglob('*.db')):
                            raise ValueError('An incorrect code opened the frozen business interface')
                        _rerun(connection, port, page_hash=page['hash'], widgets=(
                            (page['inputs']['验证码'], 'string_value', code),
                            (page['buttons']['验证并进入软件'], 'trigger_value', True)))
                        page = _page(connection)
                    if not _business_page(page) or not list(data.rglob('*.db')):
                        raise ValueError('The production-signed activation did not enter the frozen business interface')
                    if not (state / 'license.json').is_file():
                        raise ValueError('The frozen program did not retain its device authorization')
        finally:
            try:
                if process is not None and process.poll() is None:
                    request.write_text('stop\n', encoding='ascii')
                    process.wait(timeout=40)
            finally:
                # A shutdown failure still exports only bounded, sanitized text.
                if log_path.is_file():
                    text = log_path.read_text(encoding='utf-8', errors='replace')
                    log_path.write_text(text.replace(code, '[masked activation code]')[:65536], encoding='utf-8')
        if process is None or process.returncode:
            raise ValueError('The frozen licensed service did not exit normally')
    receipt = dict(status='passed', target=target, production_trust_sha256=digest,
        activation_required_before_use=True, wrong_code_rejected=True, activated=True,
        restart_without_code=True, real_frozen_business_session=True,
        private_signing_key_used=False, trust_overridden=False, production_business_data_used=False)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', type=Path, required=True)
    parser.add_argument('--target', choices=('windows-x86_64', 'macos-arm64'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify_frozen_license(args.service, args.target, args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
