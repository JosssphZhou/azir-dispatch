"""Setup contracts through the CLI and an isolated home directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import select
import signal
import time
import pty

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.home = self.work / 'home'
        self.home.mkdir()
        self.tools = self.work / 'bin'
        self.tools.mkdir()
        self.env = {'HOME': str(self.home), 'PATH': str(self.tools),
                    'PYTHONPATH': str(ROOT), 'PYTHONIOENCODING': 'utf-8',
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        self.config = self.home / '.config/azir-dispatch/config.toml'
        self.guard = self.work / 'guard'
        self.guard.mkdir()
        self.network = self.work / 'network-attempts'
        self.key_reads = self.work / 'key-read-attempts'
        self.guard.joinpath('sitecustomize.py').write_text('''import os, sys
from pathlib import Path
def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'urllib.Request') and not os.environ.get('TEST_ALLOW_NETWORK'):
        Path(os.environ['TEST_NETWORK_ATTEMPTS']).write_text(event)
        raise RuntimeError('unexpected offline network access')
sys.addaudithook(audit)
if os.environ.get('TEST_FORBID_KEY_READ'):
    original_getitem = os._Environ.__getitem__
    def getitem(self, key):
        value = original_getitem(self, key)
        if key == 'OPENROUTER_API_KEY':
            Path(original_getitem(os.environ, 'TEST_KEY_READ_ATTEMPTS')).write_text('key value read')
            raise RuntimeError('setup must only inspect key presence')
        return value
    os._Environ.__getitem__ = getitem
''')
        self.env['PYTHONPATH'] = str(self.guard) + os.pathsep + str(ROOT)
        self.env['TEST_NETWORK_ATTEMPTS'] = str(self.network)
        self.env['TEST_KEY_READ_ATTEMPTS'] = str(self.key_reads)

    def tearDown(self):
        self.assertFalse(self.network.exists(), 'offline flow attempted network access')
        self.assertFalse(self.key_reads.exists(), 'offline setup read the API key value')

    def fake_executor(self, command):
        path = self.tools / command
        path.write_text('#!' + sys.executable + '''
import json, os, sys
from pathlib import Path
with Path(os.environ['TEST_EXECUTOR_CALLS']).open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if '--version' in sys.argv:
    print('test-cli 1.2.3')
else:
    print(sys.stdin.read())
''')
        path.chmod(0o700)
        calls = self.work / 'executor-calls'
        self.env['TEST_EXECUTOR_CALLS'] = str(calls)
        return path, calls

    def run_setup(self, *args, env=None):
        return subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'),
                               'setup', *args], cwd=self.work, env=env or self.env,
                              capture_output=True, text=True, timeout=25)

    def answers(self, text):
        path = self.work / 'answers.toml'
        path.write_text('version = 1\n' + text)
        return str(path)

    def interactive(self, replies=None, interrupt=False, change_after_plan=None, human=False):
        master, slave = pty.openpty()
        # A terminal prints a readable summary; these tests read the JSON result unless they ask for the summary.
        process = subprocess.Popen([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'setup', *([] if human else ['--json'])],
                                   cwd=self.work, env=self.env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b''
        replies = iter(replies or [])
        deadline = time.monotonic() + 15
        prompts = 0
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    output += chunk
                except OSError:
                    break
                count = output.count(b'> ')
                while prompts < count:
                    prompts += 1
                    if b'Apply local configuration?' in output and change_after_plan:
                        change_after_plan()
                        change_after_plan = None
                    if interrupt:
                        process.send_signal(signal.SIGINT)
                        interrupt = False
                    else:
                        reply = next(replies, None)
                        os.write(master, b'\x04' if reply is None else (reply + '\n').encode())
            process.wait(timeout=5)
            while select.select([master], [], [], 0)[0]:
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    output += chunk
                except OSError:
                    break
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
        return process.returncode, output.decode(errors='replace')

    def test_empty_home_plan_writes_nothing(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(plan['environment']['can_dispatch'])
        self.assertIn('online_jev', plan['unverified'])

    def test_json_answers_cover_agent_install_choices_and_keep_hook_authorization_separate(self):
        skill = self.home / '.agents/skills/review/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: review-helper\n---\nRead-only review helper.\n')
        project = self.work / 'project'
        project.mkdir()
        answers = {
            'version': 1,
            'defaults': {'development': 'codex:model-c:high', 'review': 'claude-code:model-a:medium'},
            'executors': {
                'codex': {'models': {'model-c': ['high']}},
                'claude-code': {'models': {'model-a': ['medium']}},
            },
            'jev': {'mode': 'offline'},
            'points': {
                'dispatch': {'enabled': True, 'threshold': 0.75},
                'next_step': {'enabled': False, 'threshold': 0.6},
                'wrapup': {'enabled': True, 'threshold': 0.85},
                'skill': {'enabled': True, 'threshold': 0.7},
            },
            'skills': {
                'development': [],
                'review': [{'name': 'review-helper', 'source': str(skill)}],
            },
            'privacy': {'name': False, 'email': True, 'home': True, 'strict': False, 'max_state_chars': 2500},
            'hooks': {'project': str(project), 'scope': 'local'},
            'dashboard': {'title': 'Test Board'},
        }
        answers_path = self.work / 'answers.json'
        answers_path.write_text(json.dumps(answers))

        plan_result = self.run_setup('--answers-json', str(answers_path), '--config-dir', str(self.work / 'config'))
        self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
        plan = json.loads(plan_result.stdout)
        self.assertEqual(plan['status'], 'planned')
        self.assertTrue(plan['hooks'])
        self.assertFalse((project / '.claude/settings.local.json').exists())

        applied = self.run_setup('--answers-json', str(answers_path), '--config-dir', str(self.work / 'config'), '--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['status'], 'applied')
        config_path = self.work / 'config/config.toml'
        config = tomllib.loads(config_path.read_text())
        self.assertEqual(config['dispatch']['defaults'], answers['defaults'])
        self.assertEqual(config['dispatch']['executors']['codex']['models'], {'model-c': ['high']})
        self.assertEqual(config['skill']['candidates']['review'], ['review-helper', 'none'])
        self.assertFalse(config['points']['next_step']['enabled'])
        self.assertEqual(config['points']['wrapup']['threshold'], 0.85)
        self.assertEqual(config['redact']['max_state_chars'], 2500)
        self.assertEqual(config['dashboard']['title'], 'Test Board')
        self.assertFalse((project / '.claude/settings.local.json').exists())

        authorized = self.run_setup('--answers-json', str(answers_path), '--config-dir', str(self.work / 'config'),
                                    '--apply', '--allow-hooks', str(project))
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        self.assertTrue((project / '.claude/settings.local.json').exists())

    def test_json_answers_can_be_read_from_stdin(self):
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'setup',
                                 '--answers-json', '-', '--config-dir', str(self.work / 'config')],
                                cwd=self.work, env=self.env, input='{"version": 1, "jev": {"mode": "offline"}}',
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'planned')

    def test_apply_validates_offline_and_is_byte_and_mtime_idempotent(self):
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'applied')
        config = tomllib.loads(self.config.read_text())
        self.assertEqual(config['config_version'], 1)
        self.assertEqual(config['jev']['mode'], 'offline')
        self.assertEqual(config['dispatch']['default'], 'codex:inherit:inherit')
        self.assertEqual(config['points']['wrapup']['threshold'], 0.8)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(result.stdout)['verification']['adapter_offline_protocol'], 'passed')
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        second = self.run_setup('--apply')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)['changes'], [])
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()})

    def test_hook_install_requires_project_authorization_and_preserves_other_settings(self):
        project = self.work / 'project'
        settings = project / '.claude/settings.local.json'
        settings.parent.mkdir(parents=True)
        other = {'permissions': {'allow': ['Read']}, 'env': {'SAMPLE': 'retained'},
                 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
                     {'type': 'command', 'command': 'echo retained', 'timeout': 5}]}]}}
        original = json.dumps(other).encode()
        settings.write_bytes(original)
        answers = self.answers('[hooks]\nproject = ' + json.dumps(str(project)))
        plan = self.run_setup('--answers', answers)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(settings.read_bytes(), original)
        applied = self.run_setup('--answers', answers, '--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(settings.read_bytes(), original)
        authorized = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        merged = json.loads(settings.read_text())
        self.assertEqual(merged['hooks']['PreToolUse'][0], other['hooks']['PreToolUse'][0])
        self.assertEqual(len(merged['hooks']['PreToolUse']), 2)
        self.assertEqual(merged['permissions'], other['permissions'])
        self.assertEqual(merged['env'], other['env'])
        self.assertFalse((project / '.claude/settings.json').exists())
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        timestamp = settings.stat().st_mtime_ns
        again = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(settings.stat().st_mtime_ns, timestamp)
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()})
        backups = list((self.home / '.local/state/azir-dispatch/backups').rglob('*'))
        self.assertTrue(any(p.is_file() and p.read_bytes() == original for p in backups))

    def test_interactive_cancellation_never_writes_targets(self):
        for replies, interrupt in [(None, False), (None, True),
                                    (['', '', '', 'n', 'y', '', 'n'], False)]:
            with self.subTest(replies=replies, interrupt=interrupt):
                code, output = self.interactive(replies, interrupt)
                self.assertIn('needs_confirmation', output)
                self.assertFalse(self.config.exists())
                self.assertEqual(list(self.home.iterdir()), [])

    def test_interactive_apply_and_concurrent_edit_detection(self):
        replies = ['', '', '', 'n', 'y', '', 'y']
        code, output = self.interactive(replies)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.config.exists(), output)
        original = self.config.read_bytes()
        def mutate():
            self.config.write_bytes(original + b'\n# editor change\n')
        code, output = self.interactive(replies, change_after_plan=mutate)
        self.assertEqual(code, 2, output)
        self.assertIn('target changed after planning', output)
        self.assertEqual(self.config.read_bytes(), original + b'\n# editor change\n')

    def test_inherited_codex_settings_are_not_passed_as_model_or_effort(self):
        executable, calls = self.fake_executor('codex')
        answers = self.answers('[defaults]\ndevelopment="codex:inherit:inherit"\nreview="codex:inherit:inherit"')
        applied = self.run_setup('--answers', answers, '--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        task = self.work / 'task.md'
        task.write_text('A synthetic task')
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch-codex'),
                                 'run', '--config', str(self.config), '--task-file', str(task),
                                 '--log', str(self.work / 'events.jsonl')],
                                cwd=self.work, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in calls.read_text().splitlines()], [['--version'], ['exec', '-']])

    def test_online_call_needs_separate_permission_and_sends_only_synthetic_state(self):
        from test_decide import FakeJev
        applied = self.run_setup('--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        response = {'answers': {'next_step': {'choice': 'stop', 'confidence': 0.9}}, 'usage': {'cost': 0}}
        with FakeJev(response) as server:
            self.config.write_text(self.config.read_text().replace('https://openrouter.ai/api/alpha/decisions', server.url))
            answers = self.answers('[jev]\nmode = "online"')
            self.env['OPENROUTER_API_KEY'] = 'SYNTHETIC_SETUP_CREDENTIAL'
            self.env['TEST_ALLOW_NETWORK'] = '1'
            without = self.run_setup('--answers', answers, '--apply')
            self.assertEqual(without.returncode, 0, without.stderr)
            self.assertEqual(server.requests, [])
            with_permission = self.run_setup('--answers', answers, '--apply', '--allow-online-test')
            self.assertEqual(with_permission.returncode, 0, with_permission.stderr)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(server.requests[0]['state'], 'azir setup test')
            self.assertEqual(json.loads(with_permission.stdout)['verification']['online_jev'], 'tested')
            self.assertNotIn('SYNTHETIC_SETUP_CREDENTIAL', without.stdout + without.stderr + with_permission.stdout + with_permission.stderr)
            for path in self.home.rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'SYNTHETIC_SETUP_CREDENTIAL', path.read_bytes())

    def test_failed_second_replacement_restores_bytes_and_next_run_reports_transaction(self):
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        original = self.config.read_bytes()
        project = self.work / 'project'
        target = project / '.claude/settings.local.json'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'{"permissions":{"allow":["Read"]}}\n')
        hook_old = target.read_bytes()
        with self.guard.joinpath('sitecustomize.py').open('a') as stream:
            stream.write('''
if os.environ.get('TEST_FAIL_REPLACE'):
    original_replace = os.replace
    def replace(source, target, *args, **kwargs):
        if str(target).endswith('settings.local.json'):
            raise OSError('synthetic second replacement failure')
        return original_replace(source, target, *args, **kwargs)
    os.replace = replace
''')
        self.env['TEST_FAIL_REPLACE'] = '1'
        answers = self.answers('[dashboard]\ntitle = "changed"')
        result = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(target.read_bytes(), hook_old)
        self.env.pop('TEST_FAIL_REPLACE')
        next_run = self.run_setup('--apply')
        self.assertEqual(next_run.returncode, 2, next_run.stderr)
        self.assertIn('unfinished transaction', next_run.stderr)
        self.assertTrue(json.loads(next_run.stdout)['transaction'].endswith('.json'))
        self.assertEqual(list(self.home.rglob('.azir-*')), [])
        self.assertEqual(list(project.rglob('.azir-*')), [])

    def test_user_modified_owned_hook_and_invalid_json_leave_original_bytes(self):
        project = self.work / 'project'
        project.mkdir()
        installed = self.run_setup('--apply', '--allow-hooks', str(project))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        target = project / '.claude/settings.local.json'
        content = json.loads(target.read_text())
        content['hooks']['PreToolUse'][-1]['hooks'][0]['command'] += ' user-edit'
        target.write_text(json.dumps(content))
        before = target.read_bytes()
        conflict = self.run_setup('--apply', '--allow-hooks', str(project))
        self.assertEqual(conflict.returncode, 2, conflict.stderr)
        self.assertIn('changed or disappeared', conflict.stderr)
        self.assertEqual(target.read_bytes(), before)
        for invalid in [b'{/* comment */ "hooks": {}}', b'{"hooks":{},}',
                        b'{"hooks":{},"hooks":{}}', b'{"value":NaN}', b'[]']:
            with self.subTest(invalid=invalid):
                target.write_bytes(invalid)
                result = self.run_setup('--apply', '--allow-hooks', str(project))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('manual_merge', json.loads(result.stdout))
                self.assertEqual(target.read_bytes(), invalid)

    def test_shared_hooks_require_explicit_scope(self):
        project = self.work / 'project'
        project.mkdir()
        answers = self.answers('[hooks]\nproject = ' + json.dumps(str(project)) + '\nscope = "shared"')
        result = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((project / '.claude/settings.json').is_file())
        self.assertFalse((project / '.claude/settings.local.json').exists())

    def test_answers_strict_schema_and_existing_unknown_fields_are_not_overwritten(self):
        for text in ['typo = true', 'version = 1', '[jev]\nmode="offline"\nkey_command="forbidden"',
                     '[privacy]\npatterns=[".*"]', '[hooks]\nscope="wrong"',
                     '[points.dispatch]\nthreshold=1.1', '[points.skill]\nenabled=true',
                     '[defaults]\ndevelopment="codex:absent:high"',
                     '[executors.codex.models]\ninherit=[]']:
            with self.subTest(text=text):
                result = self.run_setup('--answers', self.answers(text), '--apply')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.config.exists())
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        self.config.write_text(self.config.read_text() + '\n[unknown]\nvalue=true\n')
        original = self.config.read_bytes()
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.config.read_bytes(), original)

    def test_offline_secret_env_is_never_read_by_probes_or_written_or_logged(self):
        executable, calls = self.fake_executor('codex')
        self.env['OPENROUTER_API_KEY'] = 'SYNTHETIC_SETUP_CREDENTIAL'
        self.env['TEST_FORBID_KEY_READ'] = '1'
        # Make the probe itself fail if it receives the key; detection must remove it.
        executable.write_text(executable.read_text().replace('import json, os, sys',
                              'import json, os, sys\nassert "OPENROUTER_API_KEY" not in os.environ'))
        for args in [(), ('--apply',)]:
            result = self.run_setup(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('SYNTHETIC_SETUP_CREDENTIAL', result.stdout + result.stderr)
        self.assertEqual([json.loads(line) for line in calls.read_text().splitlines()], [['--version'], ['--version']])
        for path in self.home.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'SYNTHETIC_SETUP_CREDENTIAL', path.read_bytes())

    def test_symlink_targets_and_parent_directories_are_rejected(self):
        original = self.work / 'original.toml'
        original.write_bytes(b'unchanged')
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(original)
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(original.read_bytes(), b'unchanged')
        self.config.unlink()
        project = self.work / 'project'
        project.mkdir()
        project.joinpath('.claude').symlink_to(self.config.parent, target_is_directory=True)
        result = self.run_setup('--apply', '--allow-hooks', str(project))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.config.exists())

    def test_ancestor_aliases_are_normalized_for_setup_and_hook_authorization(self):
        alias = self.work / 'alias'
        alias.symlink_to(self.work, target_is_directory=True)
        project = self.work / 'project'
        project.mkdir()
        state = self.work / 'state'
        self.env['AZIR_DISPATCH_STATE_DIR'] = str(alias / 'state')
        answers = self.answers('[hooks]\nproject=' + json.dumps(str(alias / 'project')))
        result = self.run_setup('--answers', answers, '--apply',
                                '--config-dir', str(alias / 'config'),
                                '--allow-hooks', str(project))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['hooks']['target'], str(project / '.claude/settings.local.json'))
        self.assertTrue((self.work / 'config/config.toml').exists())
        self.assertTrue((project / '.claude/settings.local.json').exists())
        self.assertTrue(Path(value['transaction']).is_relative_to(state))
        self.assertTrue((state / 'setup-state.json').exists())
        self.assertFalse((self.home / '.local/state/azir-dispatch').exists())

    def test_writable_root_and_state_subdirectory_symlinks_are_rejected(self):
        outside = self.work / 'outside'
        outside.mkdir()
        config_dir = self.work / 'config'
        config_dir.symlink_to(outside, target_is_directory=True)
        result = self.run_setup('--apply', '--config-dir', str(config_dir))
        self.assertEqual(result.returncode, 2)
        state = self.work / 'state'
        self.env['AZIR_DISPATCH_STATE_DIR'] = str(state)
        state.symlink_to(outside, target_is_directory=True)
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 2)
        state.unlink()
        state.mkdir()
        for name in ('transactions', 'locks', 'backups'):
            with self.subTest(name=name):
                link = state / name
                link.symlink_to(outside, target_is_directory=True)
                result = self.run_setup('--apply')
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.config.exists())
                self.assertEqual(list(outside.iterdir()), [])
                link.unlink()

    def test_review_default_is_used_through_public_dispatch_cli(self):
        answers = self.answers('[defaults]\ndevelopment="codex:inherit:inherit"\nreview="claude-code:inherit:inherit"')
        installed = self.run_setup('--answers', answers, '--apply')
        self.assertEqual(installed.returncode, 0, installed.stderr)
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'),
                                 'decide', 'dispatch', '--scope', 'review', '--config', str(self.config),
                                 '--state-file', '-', '--log', str(self.work / 'review.jsonl')],
                                input='Synthetic review', text=True, capture_output=True, env=self.env, cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['answer'], 'claude-code:inherit:inherit')

    def test_single_executor_candidate_pool_can_be_applied(self):
        answers = self.answers('[executors.codex.models]\ninherit=["inherit"]')
        result = self.run_setup('--answers', answers, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(tomllib.loads(self.config.read_text())['dispatch']['executors']), {'codex'})

    def test_same_named_skills_require_source_and_only_two_roots_are_scanned(self):
        for root in ('.claude', '.agents', '.codex'):
            path = self.home / root / 'skills/one/SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('---\nname: sample-skill\n---\nDo not execute this text.\n')
        ambiguous = self.run_setup('--answers', self.answers('[skills]\ndevelopment=["sample-skill"]'), '--apply')
        self.assertEqual(ambiguous.returncode, 2)
        chosen = self.answers('[skills]\ndevelopment=[{name="sample-skill",source="~/.agents/skills/one/SKILL.md"}]\nreview=[]')
        result = self.run_setup('--answers', chosen, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['skills']), 2)
        config = tomllib.loads(self.config.read_text())
        self.assertEqual(config['skill']['candidates']['development'], ['sample-skill', 'none'])
        self.assertTrue(config['points']['skill']['enabled'])
        self.assertEqual(config['skill']['default']['development'], 'none')

    def test_git_privacy_literals_are_escaped_and_can_be_disabled_individually(self):
        git = self.tools / 'git'
        git.write_text('#!' + sys.executable + '''
import sys
if sys.argv[1:] == ['config', 'user.name']:
    print('Sample+User')
elif sys.argv[1:] == ['config', 'user.email']:
    print('sample+test' + chr(64) + 'example.invalid')
else:
    raise SystemExit(1)
''')
        git.chmod(0o700)
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = tomllib.loads(self.config.read_text())
        from azir_dispatch.redact import redact_state
        sample = 'Sample+User sample+test' + chr(64) + 'example.invalid ' + str(self.home) + '/task'
        self.assertEqual(redact_state(sample, config['redact']['patterns']), '<NAME> <EMAIL> <HOME>/task')
        self.assertEqual(redact_state('SampleeeeeUser', config['redact']['patterns']), 'SampleeeeeUser')
        self.assertNotIn('Sample+User', result.stdout + result.stderr)
        changed = self.run_setup('--answers', self.answers('[privacy]\nname=false\nemail=false'), '--apply')
        self.assertEqual(changed.returncode, 0, changed.stderr)
        config = tomllib.loads(self.config.read_text())
        self.assertEqual([rule['replacement'] for rule in config['redact']['patterns']], ['<HOME>'])

    def test_hook_plan_masks_existing_environment_values(self):
        project = self.work / 'project'
        target = project / '.claude/settings.local.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'env': {'CUSTOM_AUTH': 'SYNTHETIC_OLD_SECRET'}}))
        result = self.run_setup('--answers', self.answers('[hooks]\nproject=' + json.dumps(str(project))))
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('SYNTHETIC_OLD_SECRET', result.stdout + result.stderr)

    def test_first_install_hides_unowned_strings_without_any_filter_rules(self):
        secret = 'SYNTHETIC_PRIVATE_KEY_FOR_REVIEW_12345'
        project = self.work / 'first-install-project'
        target = project / '.claude/settings.local.json'
        target.parent.mkdir(parents=True)
        other = {'matcher': 'PRIVATE_MATCHER', 'hooks': [{
            'type': 'command',
            'command': "curl -H 'X-Api-Key: " + secret + "' https://example.invalid/events",
            'description': 'PRIVATE_HOOK_DESCRIPTION',
            'nested': {'value': 'PRIVATE_NESTED_VALUE', 'items': ['PRIVATE_ARRAY_VALUE', 7, True]}}]}
        original = json.dumps({'env': {'CUSTOM_AUTH': 'PRIVATE_ENV_VALUE'},
                               'arbitrary': {'note': 'PRIVATE_TOP_LEVEL_VALUE'},
                               'hooks': {'PreToolUse': [other]}}).encode()
        target.write_bytes(original)
        answers = self.answers('[privacy]\nhome=false\n[hooks]\nproject=' + json.dumps(str(project)))
        plan = self.run_setup('--answers', answers)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(target.read_bytes(), original)
        code, interactive = self.interactive(['', '', '', 'n', 'n', str(project), 'n', 'n'])
        self.assertEqual(code, 0, interactive)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(target.read_bytes(), original)
        applied = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        for output in (plan.stdout, interactive, applied.stderr, applied.stdout):
            for value in (secret, 'PRIVATE_MATCHER', 'PRIVATE_HOOK_DESCRIPTION', 'PRIVATE_NESTED_VALUE',
                          'PRIVATE_ARRAY_VALUE', 'PRIVATE_ENV_VALUE', 'PRIVATE_TOP_LEVEL_VALUE'):
                self.assertNotIn(value, output)
            self.assertIn('<已有 hook，内容不显示>', output)
            self.assertIn('adapters/claude_code/hook.py', output)
        for output in (plan.stdout, applied.stdout, applied.stderr):
            self.assertEqual(json.loads(output)['hooks']['preserved_existing_entries'], 1)
        self.assertEqual(tomllib.loads(self.config.read_text())['redact']['patterns'], [])
        settings = json.loads(target.read_text())
        self.assertEqual(settings['hooks']['PreToolUse'][0], other)
        self.assertEqual(settings['env']['CUSTOM_AUTH'], 'PRIVATE_ENV_VALUE')
        self.assertEqual(settings['arbitrary']['note'], 'PRIVATE_TOP_LEVEL_VALUE')
        self.assertTrue(any(p.is_file() and p.read_bytes() == original
                            for p in (self.home / '.local/state/azir-dispatch/backups').rglob('*')))

    def test_display_uses_removed_and_added_rules_without_altering_write_bytes(self):
        from azir_dispatch.setup import describe_changes
        from azir_dispatch.setup_config import toml_bytes
        from azir_dispatch.setup_io import Change

        removed_secret = 'REMOVED_RULE_PRIVATE_VALUE'
        added_secret = 'ADDED_RULE_PRIVATE_VALUE'
        builtin_secret = 'BUILTIN_PRIVATE_VALUE'
        old_rule = '(?<=Old header: )' + removed_secret
        new_rule = {'pattern': '(?<=New header:\\n)' + added_secret,
                    'replacement': '<CUSTOM_HIDDEN>'}
        existing = {'redact': {'patterns': [old_rule]}}
        candidate = {'redact': {'patterns': [new_rule]}}
        command = ('Old header: ' + removed_secret + '\nNew header:\n' + added_secret
                   + '\nAuthorization: Bearer ' + builtin_secret)
        existing['dashboard'] = {'title': command}
        candidate['dashboard'] = {'title': command + ' changed'}
        original = json.dumps({'hooks': {'PreToolUse': [{'command': command}]}}).encode()
        updated = json.dumps({'hooks': {'PreToolUse': [{'command': command},
                                                     {'command': 'azir-hook'}]}}).encode()
        changes = [Change(self.config, toml_bytes(existing), toml_bytes(candidate)),
                   Change(self.work / 'settings.json', original, updated)]
        before = [(change.old, change.new) for change in changes]
        plan = describe_changes(changes, [], True, True, existing, candidate)
        display = json.dumps(plan)
        for secret in (removed_secret, added_secret, builtin_secret):
            self.assertNotIn(secret, display)
        self.assertNotIn(old_rule, display)
        self.assertNotIn(new_rule['pattern'], display)
        self.assertTrue(plan[0]['filter_rules']['changed'])
        self.assertIn('<CUSTOM_HIDDEN>', plan[0]['diff'])
        self.assertIn('<已有 hook，内容不显示>', plan[1]['diff'])
        self.assertEqual([(change.old, change.new) for change in changes], before)
        self.assertEqual(existing['redact']['patterns'], [old_rule])
        self.assertEqual(candidate['redact']['patterns'], [new_rule])

    def test_azir_like_hook_with_extra_fields_is_not_trusted_for_display(self):
        from azir_dispatch.setup_hooks import hook_fragment

        secret = 'SYNTHETIC_UNOWNED_AZIR_LOOKALIKE'
        project = self.work / 'lookalike-project'
        target = project / '.claude/settings.local.json'
        target.parent.mkdir(parents=True)
        entry = hook_fragment(self.config)['PreToolUse'][0]
        entry['hooks'][0]['metadata'] = {'note': secret}
        original = json.dumps({'hooks': {'PreToolUse': [entry]}}).encode()
        target.write_bytes(original)
        result = self.run_setup('--answers', self.answers('[privacy]\nhome=false\n[hooks]\nproject=' + json.dumps(str(project))))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn('<已有 hook，内容不显示>', result.stdout)
        self.assertIn('adapters/claude_code/hook.py', result.stdout)
        self.assertEqual(json.loads(result.stdout)['hooks']['preserved_existing_entries'], 1)
        self.assertEqual(target.read_bytes(), original)

    def test_configured_hook_secret_is_hidden_in_plan_interactive_and_apply_output(self):
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        secret = 'SYNTHETIC_PRIVATE_KEY_FOR_REVIEW_12345'
        # A context-specific rule hides the header value but does not match its own
        # definition. Definitions must be concealed separately from filtering values.
        rule = '(?<=X-Api-Key: )' + secret
        config = self.config.read_text().replace('"patterns" = [', '"patterns" = [' + json.dumps(rule) + ', ', 1)
        self.config.write_text(config)
        project = self.work / 'secret-project'
        target = project / '.claude/settings.local.json'
        target.parent.mkdir(parents=True)
        command = "curl -H 'X-Api-Key: " + secret + "' https://example.invalid/events"
        other = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': command}]}
        original = json.dumps({'hooks': {'PreToolUse': [other]}}).encode()
        target.write_bytes(original)
        answers = self.answers('[hooks]\nproject=' + json.dumps(str(project)) + '\n[dashboard]\ntitle="Changed title"')
        plan = self.run_setup('--answers', answers)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertNotIn(secret, plan.stdout + plan.stderr)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.config.read_text(), config)
        code, output = self.interactive(['', '', '', 'n', 'y', str(project), 'n', 'n'])
        self.assertEqual(code, 0, output)
        self.assertNotIn(secret, output)
        self.assertEqual(target.read_bytes(), original)
        applied = self.run_setup('--answers', answers, '--apply', '--allow-hooks', str(project))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertNotIn(secret, applied.stdout + applied.stderr)
        self.assertEqual(json.loads(target.read_text())['hooks']['PreToolUse'][0], other)
        self.assertIn(rule, tomllib.loads(self.config.read_text())['redact']['patterns'])
        self.assertTrue(any(p.is_file() and p.read_bytes() == original
                            for p in (self.home / '.local/state/azir-dispatch/backups').rglob('*')))

    def test_invalid_nested_existing_config_is_rejected_without_rewriting(self):
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        original = self.config.read_text()
        invalid = original.replace('"home" = true', '"home" = "yes"')
        self.config.write_text(invalid)
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.config.read_text(), invalid)

    def test_setup_preserves_main_point_descriptions_and_scoped_disabled_decision(self):
        from azir_dispatch.setup_config import toml_bytes

        self.assertEqual(self.run_setup('--apply').returncode, 0)
        config = tomllib.loads(self.config.read_text())
        descriptions = {}
        choices = {'dispatch': config['dispatch']['default'], 'next_step': 'continue',
                   'wrapup': 'keep', 'skill': 'none'}
        for point, choice in choices.items():
            descriptions[point] = {'question': point + ' question',
                                   'instructions': 'Configured background',
                                   'criteria': {choice: 'Configured choice description'}}
            config['points'][point].update(descriptions[point])
        config['jev']['mode'] = 'online'
        config['points']['dispatch']['enabled'] = False
        self.config.write_bytes(toml_bytes(config))
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = tomllib.loads(self.config.read_text())
        for point, fields in descriptions.items():
            for field, expected in fields.items():
                self.assertEqual(saved['points'][point][field], expected)
        self.env.update(TEST_FORBID_KEY_READ='1', OPENROUTER_API_KEY='SYNTHETIC_UNUSED_KEY')
        log = self.work / 'scoped-decision.jsonl'
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'decide',
                                 'dispatch', '--scope', 'review', '--config', str(self.config),
                                 '--log', str(log), '--state-file', '-'], input='Synthetic task',
                                text=True, capture_output=True, cwd=self.work, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision['answer'], saved['dispatch']['defaults']['review'])
        self.assertEqual(decision['error'], 'disabled')
        event = json.loads(log.read_text())
        self.assertEqual(event['scope'], 'review')
        self.assertEqual(event['question'], 'dispatch question\nConfigured background')
        self.assertEqual(event['options'][choices['dispatch']], 'Configured choice description')

    def test_explicit_default_model_and_effort_reach_the_wrapper(self):
        executable, calls = self.fake_executor('codex')
        answers = self.answers('[defaults]\ndevelopment="codex:sample-model:high"\nreview="codex:sample-model:low"\n[executors.codex.models]\nsample-model=["low","high"]')
        installed = self.run_setup('--answers', answers, '--apply')
        self.assertEqual(installed.returncode, 0, installed.stderr)
        task = self.work / 'task.md'
        task.write_text('Synthetic task')
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch-codex'),
                                 'run', '--config', str(self.config), '--task-file', str(task),
                                 '--log', str(self.work / 'events.jsonl')],
                                text=True, capture_output=True, env=self.env, cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(calls.read_text().splitlines()[-1]),
                         ['exec', '-m', 'sample-model', '-c', 'model_reasoning_effort="high"', '-'])

    def test_invalid_existing_default_reports_json_without_traceback(self):
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        self.config.write_text(self.config.read_text().replace('"default" = "codex:inherit:inherit"', '"default" = { invalid = true }', 1))
        original = self.config.read_bytes()
        result = self.run_setup('--apply')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'failed')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.config.read_bytes(), original)

    def test_version_probe_timeout_does_not_block_planning(self):
        executable, calls = self.fake_executor('claude')
        executable.write_text(executable.read_text().replace("print('test-cli 1.2.3')", "import time\n    time.sleep(15)"))
        start = time.monotonic()
        result = self.run_setup()
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(result.returncode, 0, result.stderr)
        claude = next(item for item in json.loads(result.stdout)['environment']['executors'] if item['command'] == 'claude')
        self.assertTrue(claude['found'])
        self.assertFalse(claude['version_identified'])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_runtime_offline_mode_never_runs_a_configured_key_command(self):
        self.assertEqual(self.run_setup('--apply').returncode, 0)
        command, calls = self.fake_executor('key-helper')
        self.config.write_text(self.config.read_text().replace('["jev"]', '["jev"]\nkey_command=' + json.dumps(str(command))))
        self.env['OPENROUTER_API_KEY'] = 'SYNTHETIC_SETUP_CREDENTIAL'
        self.env['TEST_FORBID_KEY_READ'] = '1'
        result = subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'decide',
                                 'dispatch', '--config', str(self.config), '--state-file', '-',
                                 '--log', str(self.work / 'events.jsonl')],
                                input='Synthetic task', text=True, capture_output=True, env=self.env, cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['error'], 'offline')
        self.assertFalse(calls.exists())

    def test_interrupt_during_version_probe_reaps_the_probe_process(self):
        executable, calls = self.fake_executor('claude')
        pid_file = self.work / 'probe-pid'
        self.env['TEST_PROBE_PID'] = str(pid_file)
        executable.write_text(executable.read_text().replace("print('test-cli 1.2.3')", "Path(os.environ['TEST_PROBE_PID']).write_text(str(os.getpid()))\n    import time\n    time.sleep(15)"))
        process = subprocess.Popen([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'setup'],
                                   env=self.env, cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child = None
        try:
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists())
            child = int(pid_file.read_text())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['status'], 'needs_confirmation')
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if child:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
