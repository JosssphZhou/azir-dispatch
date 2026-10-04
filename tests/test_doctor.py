"""Guided setup contracts in isolated, presence-only fake environments."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

from azir_dispatch import demo
from azir_dispatch.roles import AGENTS, DEFAULT_ROLES
from azir_dispatch.setup_config import toml_bytes

ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.tools = self.work / 'bin'
        self.tools.mkdir()
        self.home = self.work / 'home'
        self.home.mkdir()
        self.state = self.work / 'state'
        self.env = dict(HOME=str(self.home), PATH=str(self.tools),
                        AZIR_DISPATCH_STATE_DIR=str(self.state), PYTHONPATH=str(ROOT),
                        PYTHONIOENCODING='utf-8', TERM='dumb')

    def fake(self, name, *, home=False):
        path = self.home / '.grok/bin/grok' if home else self.tools / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!' + sys.executable + '\nimport os,sys\n'
                        'assert not any(k in os.environ for k in '
                        '("OPENROUTER_API_KEY","ANTHROPIC_API_KEY","OPENAI_API_KEY")) if "--version" in sys.argv else True\n'
                        'print("test-cli 1.2.3") if "--version" in sys.argv else print(sys.stdin.read())\n')
        path.chmod(0o700)
        return path

    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'), *args],
                              env=self.env, cwd=self.work, text=True, input=stdin,
                              capture_output=True, timeout=20)

    def doctor(self):
        result = self.cli('doctor', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state, json.loads((self.state / 'setup-state.json').read_text()))
        self.assertEqual((self.state / 'setup-state.json').stat().st_mode & 0o777, 0o600)
        return state

    def test_four_agent_environments_and_progress_contract(self):
        for present in (('claude',), ('codex',), ('claude', 'codex'), ()):
            with self.subTest(present=present):
                for name in ('claude', 'codex'):
                    (self.tools / name).unlink(missing_ok=True)
                for name in present:
                    self.fake(name)
                state = self.doctor()
                self.assertEqual(state['agents'], {name: name in present for name in AGENTS})
                self.assertEqual(state['jev_mode'], 'rules')
                self.assertEqual(state['steps']['environment'], 'ok' if present else 'missing')
                self.assertEqual(state['steps']['herdr'], 'missing')
                self.assertEqual(state['steps']['repo'], 'ok')
                self.assertEqual(state['roles']['advisor']['status'], 'optional_unconfigured')
                for role in ('main', 'dev', 'review', 'research'):
                    preferred = 'claude' if role == 'main' else 'codex'
                    selected = preferred if preferred in present else present[0] if present else None
                    self.assertEqual(state['roles'][role]['executor'], selected)
                    self.assertEqual(state['roles'][role]['fallback'], bool(selected and selected != preferred))
                human = self.cli('doctor')
                self.assertEqual(human.returncode, 0)
                self.assertNotIn('\x1b[', human.stdout)
                for name in AGENTS:
                    self.assertIn(('✓' if name in present else '✗') + ' ' + name, human.stdout)

    def test_state_under_an_ancestor_alias_uses_the_real_directory(self):
        alias = self.work / 'alias'
        alias.symlink_to(self.work.resolve(), target_is_directory=True)
        self.env['AZIR_DISPATCH_STATE_DIR'] = str(alias / 'state')
        self.doctor()
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_state_directory_and_state_file_symlinks_are_rejected(self):
        outside = self.work / 'outside'
        outside.mkdir()
        original = outside / 'original.json'
        original.write_bytes(b'unchanged')
        for destination in (outside, self.work / 'missing'):
            with self.subTest(destination=destination):
                self.state.symlink_to(destination, target_is_directory=True)
                result = self.cli('doctor', '--json')
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(list(outside.iterdir()), [original])
                self.assertFalse((self.work / 'missing').exists())
                self.state.unlink()
        self.state.mkdir()
        (self.state / 'setup-state.json').symlink_to(original)
        result = self.cli('doctor', '--json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(original.read_bytes(), b'unchanged')

    def test_grok_home_path_and_agy_alone_can_fill_roles(self):
        for name in ('grok', 'agy'):
            with self.subTest(name=name):
                path = self.fake(name, home=name == 'grok')
                state = self.doctor()
                self.assertTrue(state['agents'][name])
                for role in ('main', 'dev', 'review', 'research'):
                    self.assertEqual(state['roles'][role]['executor'], name)
                path.unlink()

    def test_grok_path_wins_and_invalid_roles_return_without_traceback(self):
        home = self.fake('grok', home=True)
        home.chmod(0o600)
        self.assertFalse(self.doctor()['agents']['grok'])
        self.fake('grok')
        self.assertTrue(self.doctor()['agents']['grok'])
        config = self.work / 'bad.toml'
        config.write_text('[roles.dev]\npreferred="bad"\nfallbacks=[]\n')
        for args in [('doctor', '--json'), ('decide', 'dispatch', '--state-file', '-')]:
            result = self.cli(*args, '--config', str(config), stdin='Task')
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('Traceback', result.stderr)

    def test_keys_never_reach_probes_checklist_json_or_state(self):
        self.env.update({name: 'SYNTHETIC_SECRET_' + name for name in
                         ('OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')})
        for tool in ('git', 'herdr', 'python3'):
            self.fake(tool)
        state = self.doctor()
        self.assertTrue(all(state['keys'].values()))
        self.assertEqual(state['jev_mode'], 'jev')
        for tool in state['tools'].values():
            self.assertEqual(tool['version'], '1.2.3')
        output = self.cli('doctor').stdout + (self.state / 'setup-state.json').read_text()
        for key in state['keys']:
            self.assertNotIn(self.env[key], output)

    def test_doctor_only_reads_key_names_and_refreshes_timestamp(self):
        from azir_dispatch.doctor import snapshot
        with patch.dict(os.environ, self.env, clear=True):
            os.environ['OPENROUTER_API_KEY'] = 'SYNTHETIC_SECRET'
            original = os._Environ.__getitem__
            def guarded_getitem(env, key):
                if key in ('OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'):
                    raise AssertionError('credential value inspected')
                return original(env, key)
            with patch.object(os._Environ, '__getitem__', guarded_getitem):
                first = snapshot(probe_versions=False, steps={'board': 'ok', 'first_dispatch': 'ok'})
                time.sleep(0.002)
                second = snapshot(probe_versions=False)
        self.assertNotEqual(first['updated_at'], second['updated_at'])
        self.assertEqual(second['steps']['board'], 'ok')
        self.assertEqual(second['steps']['first_dispatch'], 'ok')

    def test_color_requires_a_supported_tty(self):
        from argparse import Namespace
        from azir_dispatch.doctor import run
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        for term, no_color, expected in [('dumb', False, False), ('', False, False),
                                         ('xterm-256color', True, False), ('xterm-256color', False, True)]:
            env = {**self.env, 'TERM': term}
            if no_color:
                env['NO_COLOR'] = '1'
            output = Terminal()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                self.assertEqual(run(Namespace(config=None, json=False)), 0)
            self.assertEqual('\x1b[31m' in output.getvalue(), expected)

    def test_setup_role_defaults_are_used_by_decide_and_codex_adapter(self):
        self.fake('codex')
        applied = self.cli('setup', '--answers-json', '-', '--apply', stdin='{"version":1}')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        path = self.home / '.config/azir-dispatch/config.toml'
        config = tomllib.loads(path.read_text())
        self.assertEqual(config['dispatch']['default'], 'codex:gpt-6.1-sol:high')
        self.assertEqual(config['adapters']['codex']['default'], config['dispatch']['default'])
        self.assertEqual(json.loads((self.state / 'setup-state.json').read_text())['steps']['models'], 'ok')
        self.assertEqual(json.loads((self.state / 'setup-state.json').read_text())['steps']['first_dispatch'], 'pending')
        decided = self.cli('decide', 'dispatch', '--scope', 'review', '--state-file', '-', stdin='Sample task')
        self.assertEqual(json.loads(decided.stdout)['answer'], 'codex:gpt-6.1-sol:high')
        task = self.work / 'task.txt'
        task.write_text('Print setup verification only.')
        run = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch-codex'), 'run',
                              '--task-file', str(task)], env=self.env, cwd=self.work, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        events = [json.loads(line) for line in (self.state / 'events.jsonl').read_text().splitlines()]
        dispatched = next(event for event in events if event['type'] == 'dispatch')
        self.assertEqual(dispatched['actual']['model'], 'gpt-6.1-sol')
        self.assertEqual(json.loads((self.state / 'setup-state.json').read_text())['steps']['first_dispatch'], 'ok')

    def test_configured_role_fallbacks_and_rules_for_every_point(self):
        self.fake('agy')
        config = self.work / 'rules.toml'
        config.write_bytes(toml_bytes({'roles': DEFAULT_ROLES,
            'jev': {'mode': 'rules', 'key_command': 'must-not-run'},
            'points': {'next_step': {'default': 'continue'}, 'wrapup': {'default': 'close_keep'}},
            'skill': {'default': {'development': 'none'}, 'candidates': {'development': ['none']}}}))
        state = json.loads(self.cli('doctor', '--config', str(config), '--json').stdout)
        self.assertEqual(state['roles']['dev']['executor'], 'agy')
        for point, expected in [('dispatch', 'agy:inherit:inherit'), ('next_step', 'continue'),
                                ('wrapup', 'close_keep'), ('skill', 'none')]:
            extra = ['--scope', 'development'] if point == 'skill' else []
            result = self.cli('decide', point, '--config', str(config), '--state-file', '-', *extra, stdin='Sample task')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['answer'], expected)
            self.assertEqual(json.loads(result.stdout)['source'], 'rules')
        events = [json.loads(line) for line in (self.state / 'events.jsonl').read_text().splitlines()]
        self.assertTrue(all(e['source'] == 'rules' and e['jev_mode'] == 'rules' for e in events))
        config.write_bytes(toml_bytes({'jev': {'mode': 'online'},
                                      'points': {'next_step': {'enabled': False, 'default': 'continue'}}}))
        disabled = self.cli('decide', 'next_step', '--config', str(config), '--state-file', '-', stdin='Task')
        self.assertEqual(json.loads(disabled.stdout)['source'], 'rules')
        self.assertEqual(json.loads(disabled.stdout)['answer'], 'continue')

    def test_claude_grok_agy_only_setup_and_actual_default(self):
        for name in ('claude', 'grok', 'agy'):
            with self.subTest(name=name):
                executable = self.fake(name)
                config_dir = self.work / (name + '-config')
                applied = self.cli('setup', '--answers-json', '-', '--config-dir', str(config_dir),
                                   '--apply', stdin='{"version":1,"jev":{"mode":"rules"}}')
                self.assertEqual(applied.returncode, 0, applied.stderr)
                state = json.loads((self.state / 'setup-state.json').read_text())
                self.assertEqual(state['roles']['dev']['executor'], name)
                self.assertEqual(state['steps']['first_dispatch'], 'pending')
                decision = self.cli('decide', 'dispatch', '--config', str(config_dir / 'config.toml'),
                                    '--state-file', '-', stdin='Sample task')
                result = json.loads(decision.stdout)
                self.assertEqual(result['source'], 'rules')
                self.assertEqual(result['answer'].split(':')[0], 'claude-code' if name == 'claude' else name)
                executable.unlink()

    def test_demo_missing_returns_one_and_existing_calls_board(self):
        from argparse import Namespace
        args = Namespace(config=None, check=True, once=False, seconds=1, speed=2)
        stderr = io.StringIO()
        with patch.object(demo, 'DEMO_FILE', self.work / 'missing.jsonl'), contextlib.redirect_stderr(stderr):
            self.assertEqual(demo.run(args), 1)
        self.assertEqual(stderr.getvalue(), '演示事件文件缺失\n')
        fixture = ROOT / 'tests/fixtures/events-sample.jsonl'
        with patch.object(demo, 'DEMO_FILE', fixture), patch.dict(os.environ, self.env, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(demo.run(args), 0)
        self.assertEqual(json.loads((self.state / 'setup-state.json').read_text())['steps']['board'], 'ok')


if __name__ == '__main__':
    unittest.main()
