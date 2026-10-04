"""Advisor setup regressions and protection of budget and stored content."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from unittest.mock import patch

import pytest

from azir_dispatch import advisor
from azir_dispatch.dashboard import Board
from azir_dispatch.setup_config import toml_bytes, validate_answers
from azir_dispatch.roles import resolve_roles

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/advisor-chatgpt-pro/scripts'


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def usage():
    result = module('pro_usage', 'pro_usage.py')
    result.CONFIG = {'advisor': {'enabled': True, 'weekly_limit': 2}}
    return result


def test_setup_real_entry_accepts_optional_advisor_and_preserves_disable(tmp_path, monkeypatch):
    tools = tmp_path / 'bin'
    tools.mkdir()
    codex = tools / 'codex'
    codex.write_text('#!' + sys.executable + '\nprint("test-cli 1.2.3")\n')
    codex.chmod(0o700)
    monkeypatch.setenv('PATH', str(tools))
    answers = {'version': 1, 'advisor': {'enabled': True, 'weekly_limit': 7}}
    config_dir = tmp_path / 'config'
    command = [sys.executable, str(ROOT / 'bin/azir-dispatch'), 'setup', '--answers-json', '-',
               '--config-dir', str(config_dir), '--apply']
    result = subprocess.run(command, input=json.dumps(answers), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = tomllib.loads((config_dir / 'config.toml').read_text())
    assert config['advisor'] == {'provider': 'chatgpt-pro', 'enabled': True, 'weekly_limit': 7}
    assert config['roles']['advisor']['preferred']['executor'] == 'chatgpt-pro'
    state = json.loads((Path(os.environ['AZIR_DISPATCH_STATE_DIR']) / 'setup-state.json').read_text())
    assert state['roles']['advisor']['status'] == 'optional_unconfigured'
    disabled = subprocess.run(command, input=json.dumps({'version': 1, 'advisor': {'enabled': False}}),
                              capture_output=True, text=True)
    assert disabled.returncode == 0, disabled.stderr
    saved = tomllib.loads((config_dir / 'config.toml').read_text())
    assert saved['advisor']['weekly_limit'] == 7
    assert not saved['advisor']['enabled']


def test_invalid_budget_and_credential_fields_cannot_enter_config():
    for value in (0, -1, True, '50', 1.5):
        with pytest.raises(ValueError):
            validate_answers({'version': 1, 'advisor': {'weekly_limit': value}})
    for value in ({'password': 'synthetic'}, {'provider': 'other'}, {'enabled': 'yes'}):
        with pytest.raises(ValueError):
            validate_answers({'version': 1, 'advisor': value})


def test_missing_browser_or_login_never_claims_configured(tmp_path, monkeypatch):
    config = {'advisor': {'enabled': True}}
    advisor.mark_verified()
    with patch.object(advisor, 'browser_installed', return_value=False):
        assert resolve_roles(config, {})['advisor']['status'] == 'optional_unconfigured'
    with patch.object(advisor, 'browser_installed', return_value=True), patch.object(advisor, 'probe_ready', return_value=False):
        assert not advisor.available(config, probe=True)
    with patch.object(advisor, 'browser_installed', return_value=True), patch.object(advisor, 'probe_ready', return_value=True):
        assert advisor.available(config, probe=True)
        assert not advisor.available({'advisor': {'enabled': False}}, probe=True)


def test_budget_is_shared_locked_and_metadata_only(usage):
    first, _ = usage.reserve('space-1')
    second, _ = usage.reserve('space-2')
    assert first and second
    assert usage.reserve('space-3')[0] is None
    usage.cancel(first, 'SYNTHETIC_PRIVATE_BODY')
    third, _ = usage.reserve('space-3')
    assert third
    board = Board(log=Path(os.environ['AZIR_DISPATCH_STATE_DIR']) / 'events.jsonl', advisor_dir=None)
    board.step(0)
    usage.confirm(second, 'https://chatgpt.com/c/00000000-0000-0000-0000-000000000001')
    assert board.step(1).advisor_active
    records = usage.load(usage.usage_file())
    assert len(records) == 2
    state = Path(os.environ['AZIR_DISPATCH_STATE_DIR'])
    text = (state / 'events.jsonl').read_text() + Path(usage.usage_file()).read_text()
    assert 'SYNTHETIC_PRIVATE_BODY' not in text
    event = json.loads((state / 'events.jsonl').read_text())
    assert event['type'] == 'advisor' and event['source'] == 'chatgpt-pro'
    assert event['session'] == '00000000-0000-0000-0000-000000000001'
    assert not {'question', 'answer', 'purpose', 'prompt', 'cwd'} & event.keys()
    assert Path(usage.usage_file()).stat().st_mode & 0o777 == 0o600
    assert (state / 'events.jsonl').stat().st_mode & 0o777 == 0o600
    frame = Board(log=state / 'events.jsonl', advisor_dir=None).step(0)
    assert frame.advisor_calls == 1


def test_corrupt_and_symlink_budget_fail_closed(usage, tmp_path):
    path = Path(usage.usage_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken\n')
    with pytest.raises(ValueError):
        usage.reserve('space')
    path.unlink()
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        usage.reserve('space')
    assert outside.read_text() == 'unchanged'


@pytest.mark.parametrize('response,expected,used,events', [
    ('sent', 0, 1, 1), ('before', 4, 0, 0), ('unknown', 5, 1, 1), ('out_of_order', 5, 1, 1),
])
def test_send_entry_receipts_refund_only_definite_pre_click_failures(usage, tmp_path, monkeypatch,
                                                                  response, expected, used, events):
    send = module('public_pro_send', 'pro-send.py')
    config = tmp_path / 'config.toml'
    config.write_bytes(toml_bytes(usage.CONFIG))
    prompt = tmp_path / 'prompt'
    prompt.write_text('SYNTHETIC_PRIVATE_BODY question')
    monkeypatch.setattr(sys, 'argv', ['pro-send.py', '--space', '1', '--prompt-file', str(prompt), '--config', str(config)])
    monkeypatch.setattr(advisor, 'browser_installed', lambda: True)
    monkeypatch.setattr(advisor, 'check_space', lambda _: {'logged_in': True, 'pro': True})
    def fake_browser(command, *, input, **kwargs):
        import re
        tag = json.loads(re.search(r'const receiptTag = (".*")', input)[1])
        lines = {'sent': ['SEND_CLICKING', 'SENT', 'URL https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'],
                 'before': ['UPLOAD_FAILED'], 'unknown': ['SEND_CLICKING'],
                 'out_of_order': ['SENT', 'SEND_CLICKING', 'URL https://chatgpt.com/c/00000000-0000-0000-0000-000000000001']}[response]
        return subprocess.CompletedProcess(command, 0, '', '\n'.join(tag + ' ' + line for line in lines) + '\n')
    monkeypatch.setattr(send.subprocess, 'run', fake_browser)
    if expected:
        with pytest.raises(SystemExit) as caught:
            send.main()
        assert caught.value.code == expected
    else:
        assert send.main() is None
    assert len(usage.in_window(usage.load(usage.usage_file()))) == used
    log = Path(os.environ['AZIR_DISPATCH_STATE_DIR']) / 'events.jsonl'
    assert (len(log.read_text().splitlines()) if log.exists() else 0) == events
    stored = Path(usage.usage_file()).read_text() + (log.read_text() if log.exists() else '')
    assert 'SYNTHETIC_PRIVATE_BODY' not in stored


def test_guard_blocks_chatgpt_and_unknown_sends_without_exposing_input(monkeypatch):
    guard = module('public_guard', 'guard-hook.py')
    assert guard.check('ego-browser nodejs -e "chatgpt; page.click(\'send-button\')"')
    assert guard.check('ego-browser nodejs -e "page.keyboard.press(\'Enter\')"')
    assert not guard.check('python3 skills/advisor-chatgpt-pro/scripts/pro-send.py --space 1')
    monkeypatch.setattr(guard, 'query_spaces', lambda ids: {'1': ['https://chatgpt.com/']})
    assert guard.check('ego-browser nodejs -e "taskSpace(1); page.goto(\'https://example.com\'); page.keyboard.press(\'Enter\')"')


def test_competing_processes_cannot_exceed_the_same_budget(tmp_path):
    script = f'''import sys
sys.path.insert(0, {str(SCRIPTS)!r})
import pro_usage
pro_usage.CONFIG = {{'advisor': {{'enabled': True, 'weekly_limit': 1}}}}
rid, _ = pro_usage.reserve('concurrent-space')
print(bool(rid))
'''
    children = [subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = [child.communicate(timeout=15) for child in children]
    assert all(child.returncode == 0 for child in children), results
    assert sorted(out.strip() for out, _ in results) == ['False', 'True']


def test_send_rejects_logged_out_or_non_pro_before_any_budget_write(usage, tmp_path, monkeypatch):
    send = module('public_pro_send_preflight', 'pro-send.py')
    config = tmp_path / 'config.toml'
    config.write_bytes(toml_bytes(usage.CONFIG))
    prompt = tmp_path / 'prompt'
    prompt.write_text('SYNTHETIC_PRIVATE_BODY')
    monkeypatch.setattr(sys, 'argv', ['pro-send.py', '--space', '1', '--prompt-file', str(prompt), '--config', str(config)])
    monkeypatch.setattr(advisor, 'browser_installed', lambda: True)
    for state in ({'logged_in': False, 'pro': True}, {'logged_in': True, 'pro': False}):
        monkeypatch.setattr(advisor, 'check_space', lambda _, state=state: state)
        assert send.main() == 4
        assert not Path(usage.usage_file()).exists()
        assert not (Path(os.environ['AZIR_DISPATCH_STATE_DIR']) / 'events.jsonl').exists()
