"""Roles screen: model lists, JSON, --set, one-message tests, and the doctor and setup text around it."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

from azir_dispatch import model_catalog
from dashboard_terminal import Screen

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_EN = ''' AZIR DISPATCH · Roles & models                                              detected: claude codex
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│   ROLE      EXECUTOR      MODEL                     EFFORT   STATUS                              │
│   main      Claude Code   claude-opus-5-5           high     · not tested                        │
│   dev       Codex         gpt-6.1-sol               high     · not tested                        │
│   review    Codex         gpt-6.1-sol               high     · not tested                        │
│   research  Codex         gpt-6.1-sol               high     · not tested                        │
│   advisor   —             not configured            —        optional                            │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
'''
SNAPSHOT_ZH = ''' AZIR DISPATCH · 角色与模型                                                  已检测到: claude codex
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│   角色      执行者        型号                      强度     状态                                │
│   主会话    Claude Code   claude-opus-5-5           high     · 未测试                            │
│   开发      Codex         gpt-6.1-sol               high     · 未测试                            │
│   审查      Codex         gpt-6.1-sol               high     · 未测试                            │
│   调研      Codex         gpt-6.1-sol               high     · 未测试                            │
│   顾问      —             未配置                    —        可选                                │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
'''
CODEX_CATALOG = {'models': [
    {'slug': 'gpt-6.1-sol', 'visibility': 'list',
     'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'high'}, {'effort': 'max'}]},
    {'slug': 'gpt-6-luna', 'visibility': 'list', 'supported_reasoning_levels': [{'effort': 'low'}]},
    {'slug': 'hidden-model', 'visibility': 'hide'}]}
FAKE = '''#!{python}
import json, os, sys
args = sys.argv[1:]
mode = os.environ.get('FAKE_MODE', 'ok')
if '--version' in args:
    print('fake-cli 1.2.3'); raise SystemExit(0)
if args[:2] == ['debug', 'models']:
    print(json.dumps({catalog})); raise SystemExit(0)
if mode == 'login':
    print('Error: not logged in. Run login first.', file=sys.stderr); raise SystemExit(1)
if mode == 'nomodel':
    print('The model `' + (args[args.index('-m') + 1] if '-m' in args else 'x') + '` does not exist or you do not have access.', file=sys.stderr); raise SystemExit(1)
if mode == 'odd':
    print('hello there'); raise SystemExit(0)
print('pong')
'''


class FakeEnvironment(unittest.TestCase):
    """Fake agent CLIs on a private PATH, with HOME and state in a temporary directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.tools = self.work / 'bin'
        self.tools.mkdir()
        self.home = self.work / 'home'
        self.home.mkdir()
        self.state = self.work / 'state'
        self.env = dict(HOME=str(self.home), PATH=str(self.tools), AZIR_DISPATCH_STATE_DIR=str(self.state),
                        PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', TERM='dumb', LANG='en_US.UTF-8')
        self.fake('claude')
        self.fake('codex', catalog=CODEX_CATALOG)

    def fake(self, name, catalog=None):
        path = self.tools / name
        path.write_text(FAKE.format(python=sys.executable, catalog=json.dumps(catalog or {'models': []})))
        path.chmod(0o700)

    def cli(self, *args, env=None, stdin=None):
        return subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'), *args],
                              env={**self.env, **(env or {})}, cwd=self.work, text=True, input=stdin,
                              capture_output=True, timeout=30)

    def roles_json(self, *args, env=None):
        result = self.cli('roles', '--json', *args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class RolesTests(FakeEnvironment):
    # --- screen text -------------------------------------------------------

    def test_screen_snapshot_in_english(self):
        self.assertEqual(self.cli('roles').stdout, SNAPSHOT_EN)

    def test_screen_snapshot_in_chinese_follows_lang(self):
        self.assertEqual(self.cli('roles', env=dict(LANG='zh_CN.UTF-8')).stdout, SNAPSHOT_ZH)
        # LC_ALL wins over LANG, as for any program.
        self.assertEqual(self.cli('roles', env=dict(LANG='zh_CN.UTF-8', LC_ALL='en_US.UTF-8')).stdout, SNAPSHOT_EN)

    def test_missing_cli_shows_the_fallback_on_that_row(self):
        (self.tools / 'codex').unlink()
        shown = self.cli('roles').stdout
        dev = next(line for line in shown.splitlines() if ' dev ' in line)
        self.assertIn('Codex', dev)
        self.assertIn('→ Claude Code', dev)
        data = self.roles_json()
        self.assertTrue(data['roles']['dev']['using_fallback'])
        self.assertEqual(data['roles']['dev']['status'], 'fallback')

    def test_advisor_row_follows_the_chatgpt_pro_connection(self):
        from azir_dispatch import roles_data
        from azir_dispatch.roles import DEFAULT_ROLES
        import copy
        roles = {role: copy.deepcopy(DEFAULT_ROLES[role]) for role in roles_data.ROLE_ORDER}
        agents = {'claude': True, 'codex': True}
        advisor = lambda ready: next(r for r in roles_data.build_rows(roles, agents, {}, {}, {}) if r['role'] == 'advisor')
        with patch.dict(os.environ, self.env, clear=True), patch('azir_dispatch.advisor.available', return_value=False):
            row = advisor(False)
            self.assertEqual((row['status'], row['executor']), ('optional', None))
        with patch.dict(os.environ, self.env, clear=True), patch('azir_dispatch.advisor.available', return_value=True):
            row = advisor(True)
            self.assertEqual((row['status'], row['executor'], row['model']), ('connected', 'chatgpt-pro', 'GPT Pro'))
            self.assertEqual(roles_data.executor_choices('advisor', agents, {}), ['chatgpt-pro'])
            self.assertEqual(roles_data.label('chatgpt-pro'), 'ChatGPT Pro')

    # --- json and model lists ---------------------------------------------

    def test_json_lists_roles_status_and_models_from_the_cli(self):
        data = self.roles_json()
        self.assertEqual(data['detected'], ['claude', 'codex'])
        self.assertEqual(data['roles']['dev']['combination'], 'codex:gpt-6.1-sol:high')
        self.assertEqual(data['roles']['dev']['status'], 'unverified')
        self.assertEqual(data['roles']['advisor']['status'], 'optional')
        self.assertIsNone(data['roles']['advisor']['combination'])
        self.assertEqual(data['available']['codex']['models'], ['gpt-6.1-sol', 'gpt-6-luna'])
        self.assertTrue(data['available']['codex']['listed'])
        # Claude Code has no list command: it offers its aliases and says the list is not authoritative.
        self.assertFalse(data['available']['claude']['listed'])
        self.assertIn('opus', data['available']['claude']['models'])

    def test_default_model_missing_from_the_cli_list_becomes_inherit(self):
        self.fake('codex', catalog={'models': [{'slug': 'only-this', 'visibility': 'list'}]})
        data = self.roles_json()
        self.assertEqual(data['roles']['dev']['combination'], 'codex:inherit:high')

    def test_saved_model_missing_from_the_list_is_flagged_not_rewritten(self):
        self.assertEqual(self.cli('roles', '--set', 'dev=codex:gpt-6.1-sol:high').returncode, 0)
        self.fake('codex', catalog={'models': [{'slug': 'only-this', 'visibility': 'list'}]})
        data = self.roles_json(env=dict(AZIR_DISPATCH_STATE_DIR=str(self.work / 'fresh-state')))
        self.assertEqual(data['roles']['dev']['status'], 'not_in_list')
        self.assertEqual(data['roles']['dev']['model'], 'gpt-6.1-sol')

    def test_parsers_read_the_real_cli_formats(self):
        self.assertEqual(model_catalog.parse_codex(json.dumps(CODEX_CATALOG)),
                         (['gpt-6.1-sol', 'gpt-6-luna'], {'gpt-6.1-sol': ['low', 'high', 'max'], 'gpt-6-luna': ['low']}))
        self.assertEqual(model_catalog.parse_agy('Fetching available models...\ngemini-3.1-pro-high\tGemini 3.1 Pro (High)\nclaude-sonnet-4-6\tClaude Sonnet 4.6\n')[0],
                         ['gemini-3.1-pro-high', 'claude-sonnet-4-6'])
        self.assertEqual(model_catalog.parse_cursor('Available models\n\nauto - Auto (default)\ngpt-5.2 - GPT-5.2\n')[0], ['auto', 'gpt-5.2'])
        self.assertEqual(model_catalog.parse_grok('You are using XAI_API_KEY.\n\nAvailable models:\n  * grok-4.7 (default)\n  - grok-4.6\n')[0],
                         ['grok-4.7', 'grok-4.6'])

    # --- --set ------------------------------------------------------------

    def test_set_saves_roles_and_moves_dispatch_defaults(self):
        result = self.cli('roles', '--set', 'dev=codex:gpt-6-luna:low', '--set', 'main=claude:opus:high')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dev: codex:gpt-6.1-sol:high → codex:gpt-6-luna:low', result.stdout)
        self.assertIn('main: claude:claude-opus-5-5:high → claude:opus:high', result.stdout)
        path = self.home / '.config/azir-dispatch/config.toml'
        self.assertIn(str(path), result.stdout)
        config = tomllib.loads(path.read_text())
        self.assertEqual(config['roles']['dev']['preferred'], dict(executor='codex', model='gpt-6-luna', effort='low'))
        self.assertEqual(config['dispatch']['defaults']['development'], 'codex:gpt-6-luna:low')
        self.assertEqual(config['dispatch']['default'], 'codex:gpt-6-luna:low')
        self.assertEqual(config['dispatch']['defaults']['frontend'], 'claude-code:opus:high')
        self.assertEqual(config['roles']['review']['preferred']['model'], 'gpt-6.1-sol')
        # The saved choice shows up again, and decide uses it.
        self.assertEqual(self.roles_json()['roles']['dev']['combination'], 'codex:gpt-6-luna:low')
        decided = self.cli('decide', 'dispatch', '--scope', 'development', '--state-file', '-', stdin='A task')
        self.assertEqual(json.loads(decided.stdout)['answer'], 'codex:gpt-6-luna:low')

    def test_set_rejects_bad_input_without_writing(self):
        path = self.home / '.config/azir-dispatch/config.toml'
        for argument, message in [('dev=codex:no-such-model:high', 'has no model no-such-model'),
                                  ('dev=codex:gpt-6.1-sol:turbo', 'takes effort'),
                                  ('dev=nonesuch:x:y', 'unknown executor'),
                                  ('boss=codex:gpt-6.1-sol:high', 'expected role='),
                                  ('dev=codex:gpt-6.1-sol', 'expected role=')]:
            with self.subTest(argument=argument):
                result = self.cli('roles', '--set', argument)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse(path.exists())

    def test_set_prints_json_when_asked(self):
        result = self.cli('roles', '--set', 'review=claude:inherit:inherit', '--json')
        data = json.loads(result.stdout)
        self.assertEqual(data['changes'], [dict(role='review', before='codex:gpt-6.1-sol:high', after='claude:inherit:inherit')])
        self.assertEqual(data['roles']['review']['combination'], 'claude:inherit:inherit')

    def test_set_keeps_the_existing_config(self):
        applied = self.cli('setup', '--answers-json', '-', '--apply', '--json', stdin='{"version":1,"jev":{"mode":"rules"}}')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(self.cli('roles', '--set', 'dev=codex:gpt-6-luna:low').returncode, 0)
        config = tomllib.loads((self.home / '.config/azir-dispatch/config.toml').read_text())
        self.assertEqual(config['jev']['mode'], 'rules')

    # --- one-message tests -------------------------------------------------

    def run_test(self, mode, executor='codex', model='gpt-6.1-sol'):
        with patch.dict(os.environ, {**self.env, 'FAKE_MODE': mode}, clear=True):
            return model_catalog.run_test(executor, model, 'high', timeout=20)

    def test_a_test_reports_success_and_each_failure_reason(self):
        self.assertTrue(self.run_test('ok')['ok'])
        self.assertEqual(self.run_test('login')['reason'], 'not_logged_in')
        failure = self.run_test('nomodel', model='gpt-9')
        self.assertEqual((failure['reason'], failure['values']), ('model_not_found', dict(model='gpt-9')))
        self.assertEqual(self.run_test('odd')['reason'], 'unexpected')
        (self.tools / 'codex').unlink()
        self.assertEqual(self.run_test('ok')['reason'], 'command_missing')

    def test_real_cli_error_texts_are_classified(self):
        # Texts copied from real runs of the CLIs on 2026-10-04.
        samples = [
            ('codex', 'ERROR: unexpected status 401 Unauthorized: Missing bearer or basic authentication in header', 'not_logged_in'),
            ('claude', 'Not logged in · Please run /login', 'not_logged_in'),
            ('codex', 'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The \'gpt-nope-9\' model is not supported when using Codex with a ChatGPT account."}}', 'model_not_found'),
            ('claude', "There's an issue with the selected model (nope-model-9). It may not exist or you may not have access to it.", 'model_not_found'),
            ('codex', 'ERROR: stream disconnected before completion', 'error'),
        ]
        for command, text, expected in samples:
            with self.subTest(text=text[:40]):
                self.assertEqual(model_catalog.classify(text, 'm', command, 1)[0], expected)

    def test_a_test_sends_one_short_message_with_the_chosen_model(self):
        argv = model_catalog.argv_for_test('codex', 'gpt-6.1-sol', 'high')
        self.assertEqual(argv[1:3], ['exec', '--skip-git-repo-check'])
        self.assertIn('gpt-6.1-sol', argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(argv[-1], model_catalog.ECHO_PROMPT)
        inherited = model_catalog.argv_for_test('claude', 'inherit', 'inherit')
        self.assertNotIn('--model', inherited)
        self.assertNotIn('--effort', inherited)

    def test_recorded_test_turns_unverified_into_verified_or_failed(self):
        with patch.dict(os.environ, self.env, clear=True):
            model_catalog.record_check('codex', 'gpt-6.1-sol', 'high', dict(ok=True, reason=None, values={}))
        self.assertEqual(self.roles_json()['roles']['dev']['status'], 'verified')
        with patch.dict(os.environ, self.env, clear=True):
            model_catalog.record_check('codex', 'gpt-6.1-sol', 'high',
                                       dict(ok=False, reason='not_logged_in', values=dict(command='codex')))
        data = self.roles_json()
        self.assertEqual(data['roles']['dev']['status'], 'failed')
        self.assertEqual(data['roles']['dev']['reason']['key'], 'not_logged_in')
        self.assertEqual(self.roles_json()['roles']['review']['status'], 'failed')

    # --- interactive screen -----------------------------------------------

    def drive(self, steps, cols=100, rows=24, env=None):
        """Run the real screen in a pty, send keys, and return the last full frame as text plus the exit code."""
        import fcntl, struct, termios
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        process = subprocess.Popen([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'roles'],
                                   env={**self.env, 'TERM': 'xterm-256color', **(env or {})}, cwd=self.work,
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = ''
        def pump(seconds):
            nonlocal output
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        output += os.read(master, 65536).decode(errors='replace')
                    except OSError:
                        return
        try:
            pump(2.5)
            for key in steps:
                os.write(master, key.encode())
                pump(0.5)
            pump(0.5)
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
        frame = output.split('\x1b[?1049h')[-1].split('\x1b[?1049l')[0]
        screen = Screen(cols, rows)
        screen.feed(frame.rsplit('\x1b[2J', 1)[-1] if '\x1b[2J' in frame else frame)
        return screen.text(), process.returncode, output

    def test_screen_changes_executor_model_effort_and_saves(self):
        steps = ['\x1b[B',          # select dev
                 '\x1b[C',          # executor: Codex -> next installed (claude wraps around)
                 'm', '\x1b[B', '\r',   # open the model list, pick the first alias after inherit
                 'e',               # effort cycles
                 's']               # save
        text, code, raw = self.drive(steps + ['q'])
        self.assertEqual(code, 0, raw)
        config = tomllib.loads((self.home / '.config/azir-dispatch/config.toml').read_text())
        preferred = config['roles']['dev']['preferred']
        self.assertEqual(preferred['executor'], 'claude')
        self.assertEqual(preferred['model'], 'opus')
        self.assertNotEqual(preferred['effort'], 'inherit')
        self.assertEqual(config['dispatch']['defaults']['development'].split(':')[0], 'claude-code')

    def test_keys_that_arrive_in_one_read_are_all_handled(self):
        # Holding an arrow key or pasting delivers several keys per read.
        text, code, raw = self.drive(['\x1b[B\x1b[B', 'm', '\x1b[B\x1b[B\x1b[A\r', 's', 'q'])
        self.assertEqual(code, 0, raw)
        config = tomllib.loads((self.home / '.config/azir-dispatch/config.toml').read_text())
        self.assertEqual(config['roles']['review']['preferred']['model'], 'gpt-6-luna')
        self.assertEqual(config['roles']['dev']['preferred']['model'], 'gpt-6.1-sol')

    def test_screen_asks_before_a_test_and_shows_the_result(self):
        text, code, raw = self.drive(['\x1b[B', 't', 'n', 'q'], env=dict(FAKE_MODE='ok'))
        self.assertIn('Test codex:gpt-6.1-sol:high?', raw)
        self.assertIn('uses a little quota', raw)
        self.assertFalse((self.state / 'role-checks.json').exists())
        text, code, raw = self.drive(['\x1b[B', 't', 'y', 'q'], env=dict(FAKE_MODE='ok'))
        self.assertIn('✓ codex:gpt-6.1-sol:high replied', raw)
        self.assertEqual(self.roles_json()['roles']['dev']['status'], 'verified')
        text, code, raw = self.drive(['\x1b[B', 't', 'y', 'q'], env=dict(FAKE_MODE='login'))
        self.assertIn('not logged in', raw)
        self.assertEqual(self.roles_json()['roles']['dev']['status'], 'failed')

    def test_screen_declining_the_test_spends_nothing(self):
        text, code, raw = self.drive(['t', 'n', 'q'], env=dict(FAKE_MODE='ok'))
        self.assertFalse((self.state / 'role-checks.json').exists())

    def test_small_terminal_gets_a_notice_instead_of_a_broken_screen(self):
        text, code, raw = self.drive(['q'], cols=60, rows=12)
        self.assertIn('Terminal too small', text)
        self.assertNotIn('ROLE', text)

    def test_quit_with_unsaved_changes_needs_a_second_q(self):
        text, code, raw = self.drive(['\x1b[C', 'q', 'q'])
        self.assertEqual(code, 0)
        self.assertIn('unsaved changes', raw)
        self.assertFalse((self.home / '.config/azir-dispatch/config.toml').exists())


class DoctorAndSetupTextTests(FakeEnvironment):
    """Doctor colors and the setup summary, in the same fake environment."""

    def doctor_text(self, term='xterm-256color'):
        from argparse import Namespace
        from azir_dispatch.doctor import run
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        with patch.dict(os.environ, {**self.env, 'TERM': term}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(run(Namespace(config=None, json=False)), 0)
        return output.getvalue()

    def test_doctor_greys_optional_items_and_reds_only_what_blocks(self):
        shown = self.doctor_text()
        lines = {line.split('\x1b[0m')[0].split('m')[-1] for line in shown.splitlines()}
        for name in ('OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'advisor', 'gemini'):
            line = next(line for line in shown.splitlines() if name in line)
            self.assertTrue(line.startswith('\x1b[90m'), line)
            self.assertNotIn('\x1b[31m', line)
            self.assertIn('optional', line)
        # herdr, git and python3 are not on this PATH: those are the red ones.
        for name in ('herdr', 'git', 'python3'):
            line = next(line for line in shown.splitlines() if name in line)
            self.assertTrue(line.startswith('\x1b[31m✗'), line)
        self.assertIn('azir-dispatch roles', shown)
        self.assertNotIn('\x1b[', self.doctor_text(term='dumb'))

    def test_doctor_text_is_english_and_follows_lang_for_chinese(self):
        english = self.cli('doctor').stdout
        self.assertNotRegex(english, r'[一-鿿]')
        chinese = self.cli('doctor', env=dict(LANG='zh_CN.UTF-8')).stdout
        self.assertIn('可选', chinese)
        self.assertIn('下一步：运行 `azir-dispatch roles`', chinese)
        hints = json.loads(self.cli('doctor', '--json').stdout)['hints']
        self.assertNotRegex(json.dumps(hints, ensure_ascii=False), r'[一-鿿]')
        self.assertEqual(sorted(json.loads(self.cli('doctor', '--json').stdout)), sorted(json.loads(self.cli('doctor', '--json', env=dict(LANG='zh_CN.UTF-8')).stdout)))

    def setup_output(self, *args, tty):
        from argparse import Namespace
        from azir_dispatch.setup import run
        class Terminal(io.StringIO):
            def isatty(self):
                return tty
        output = Terminal()
        arguments = dict(answers=None, answers_json=None, config_dir=str(self.home / '.config/azir-dispatch'),
                         apply=False, allow_hooks=None, allow_online_test=False, json=False)
        arguments.update(dict(a.split('=', 1) for a in ()))
        namespace = Namespace(**{**arguments, **dict(args)})
        stdin = io.StringIO('{"version":1,"jev":{"mode":"rules"}}')
        with patch.dict(os.environ, {**self.env, 'TERM': 'dumb'}, clear=True), patch('sys.stdin', stdin), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors := io.StringIO()):
            code = run(namespace)
        self.last_stderr = errors.getvalue()
        return code, output.getvalue()

    def test_setup_prints_a_readable_summary_in_a_terminal_and_json_otherwise(self):
        code, shown = self.setup_output(('answers_json', '-'), tty=True)
        self.assertEqual(code, 0)
        self.assertIn('Setup plan', shown)
        self.assertIn(str(self.home / '.config/azir-dispatch/config.toml'), shown)
        self.assertIn('will write', shown)
        self.assertIn('Claude Code', shown)
        self.assertIn('gpt-6.1-sol', shown)
        self.assertIn('azir-dispatch roles', shown)
        self.assertIn('--apply', shown)
        self.assertNotIn('{"status"', shown)
        self.assertNotIn('--- before', shown)
        self.assertLess(len(shown.splitlines()), 30)
        self.assertFalse((self.home / '.config/azir-dispatch/config.toml').exists())
        code, applied = self.setup_output(('answers_json', '-'), ('apply', True), tty=True)
        self.assertIn('Setup applied', applied)
        self.assertNotIn('"status"', applied)
        # No second copy of the plan as JSON on stderr, which a terminal would show above the summary.
        self.assertEqual(self.last_stderr, '')
        self.assertIn('wrote', applied)
        self.assertTrue((self.home / '.config/azir-dispatch/config.toml').exists())
        # With --json, or when stdout is a pipe, the result is JSON exactly as before.
        for tty, flag in [(True, True), (False, False)]:
            code, text = self.setup_output(('answers_json', '-'), ('json', flag), tty=tty)
            self.assertEqual(json.loads(text)['status'], 'planned')

    def test_setup_summary_in_chinese(self):
        from argparse import Namespace
        with patch.dict(os.environ, dict(LANG='zh_CN.UTF-8')):
            from azir_dispatch.setup_summary import summary
            text = summary(dict(status='planned', changes=[1]), dict(jev=dict(mode='rules'), dispatch=dict(defaults={'development': 'codex:inherit:inherit'})),
                           Path('/x/config.toml'), color=False)
        self.assertIn('安装计划', text)
        self.assertIn('azir-dispatch roles', text)


if __name__ == '__main__':
    unittest.main()
