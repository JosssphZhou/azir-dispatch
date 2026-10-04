"""Exercise the test entry point and the real CLI at the data-protection boundary."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_suite(tmp_path, source, *, baseline=None):
    suite = tmp_path / 'suite'
    suite.mkdir()
    for name in ('conftest.py', 'state_isolation.py'):
        shutil.copyfile(ROOT / 'tests' / name, suite / name)
    (suite / 'test_probe.py').write_text(source)
    home = tmp_path / 'original-home'
    home.mkdir()
    state = home / '.local/state/azir-dispatch'
    if baseline is not None:
        state.mkdir(parents=True)
        (state / 'setup-state.json').write_text(baseline)
    env = dict(os.environ, HOME=str(home), AZIR_DISPATCH_STATE_DIR=str(state),
               AZIR_DISPATCH_LOG=str(state / 'events.jsonl'),
               AZIR_DISPATCH_CONFIG=str(home / 'config.toml'))
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(suite)],
                            cwd=suite, env=env, text=True, capture_output=True, timeout=30)
    return result, home


def test_collection_and_each_test_use_private_paths_and_subprocesses_inherit_them(tmp_path):
    result, home = run_suite(tmp_path, f'''
import json
import os
from pathlib import Path
import subprocess
import sys

collection_home = Path.home()
(collection_home / 'collection-write').write_text('collection')
seen = set()

def exercise():
    home = Path.home()
    assert home != collection_home and home not in seen
    seen.add(home)
    assert not (home / 'collection-write').exists()
    for name in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME',
                 'AZIR_DISPATCH_STATE_DIR', 'AZIR_DISPATCH_LOG', 'AZIR_DISPATCH_CONFIG'):
        assert Path(os.environ[name]).is_relative_to(home)
    command = [sys.executable, {str(ROOT / 'bin/azir-dispatch')!r},
               'record', 'dispatch', '--run-id', 'isolation-probe',
               '--field', 'requested={{"executor":"codex","model":"model-a","effort":"high"}}',
               '--field', 'suggested=null',
               '--field', 'actual={{"executor":"codex","model":"model-a","effort":"high"}}',
               '--field', 'task=Isolation regression', '--field', 'target=fixture']
    run = subprocess.run(command, text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['type'] == 'dispatch'
    state = Path(os.environ['AZIR_DISPATCH_STATE_DIR']) / 'setup-state.json'
    assert json.loads(state.read_text())['steps']['first_dispatch'] == 'ok'
    assert len(Path(os.environ['AZIR_DISPATCH_LOG']).read_text().splitlines()) == 1

def test_first():
    exercise()

def test_second():
    exercise()
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 passed' in result.stdout
    assert list(home.iterdir()) == []


@pytest.mark.parametrize('existing', [False, True], ids=['new-file', 'existing-file'])
def test_session_guard_fails_on_state_write_from_a_subprocess(tmp_path, existing):
    # Deliberately bypass HOME isolation, but only target this test's temporary home.
    target = tmp_path / 'original-home/.local/state/azir-dispatch/setup-state.json'
    source = f'''
from pathlib import Path
import subprocess
import sys

target = Path({str(target)!r})
'''
    if existing:
        source += f'''
def test_leak():
    subprocess.run([sys.executable, '-c',
                    "from pathlib import Path; Path({str(target)!r}).write_text('changed')"], check=True)
'''
    else:
        source += '''
def test_leak():
    subprocess.run([sys.executable, '-c',
                    f"from pathlib import Path; p = Path({str(target)!r}); p.parent.mkdir(parents=True); p.write_text('leak')"], check=True)
'''
    result, _ = run_suite(tmp_path, source, baseline='original' if existing else None)
    assert result.returncode == 1, result.stdout + result.stderr
    assert '1 passed' in result.stdout
    assert 'azir-dispatch real-home state leak' in result.stdout
    assert ('modified: ' if existing else 'created: ') + str(target) in result.stdout
