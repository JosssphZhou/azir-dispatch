import json
from adapter_support import AdapterCase


class CodexTests(AdapterCase):
    def setUp(self):
        super().setUp()
        self.choice = 'codex:model-a:high'
        self.task = self.work / 'task.txt'
        self.task.write_text('Implement a module\nwith tests.')
        self.fake = self.work / 'fake-codex'
        self.received = self.work / 'received.json'
        self.fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ["RECEIVED"]).write_text(json.dumps({"args":sys.argv[1:],"stdin":sys.stdin.read(),"cwd":os.getcwd()}))\nprint(os.environ.get("FAKE_ERROR", ""), file=sys.stderr)\nsys.exit(int(os.environ.get("FAKE_EXIT", "0")))\n')
        self.fake.chmod(0o700)
        self.env['RECEIVED'] = str(self.received)

    def run_task(self, *extra):
        return self.invoke('bin/azir-dispatch-codex', 'run', '--task-file', str(self.task), '--codex-command', str(self.fake), '--cwd', str(self.work), *extra)

    def test_selected_skill_prefix_reaches_codex_and_is_recorded(self):
        self.skill_choice = 'tdd'
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.received.read_text())['stdin'],
                         '用 tdd 技能，' + self.task.read_text())
        skill_event = next(e for e in self.events() if e.get('point') == 'skill')
        dispatch = next(e for e in self.events() if e['type'] == 'dispatch')
        self.assertEqual(skill_event['scope'], 'development')
        self.assertEqual(skill_event['caused_by'], self.events()[0]['id'])
        self.assertEqual(dispatch['caused_by'], skill_event['id'])
        self.assertEqual((dispatch['skill_requested'], dispatch['skill_suggested'], dispatch['skill']),
                         (None, 'tdd', 'tdd'))

    def test_low_skill_confidence_never_launches_codex(self):
        self.skill_choice, self.skill_confidence = 'tdd', 0.4
        result = self.run_task()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertFalse(self.received.exists())
        self.assertIn('skill', result.stderr)
        self.assertEqual([e['type'] for e in self.events()], ['decision', 'decision', 'handback'])
        self.assertEqual(self.events()[1]['jev_choice'], 'tdd')

    def test_unavailable_jev_uses_default_skill_prefix(self):
        self.env['OPENROUTER_API_KEY'] = ''
        self.config.write_text(self.config.read_text().replace('development = "none"', 'development = "tdd"'))
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.received.read_text())['stdin'], '用 tdd 技能，' + self.task.read_text())
        dispatch = next(e for e in self.events() if e['type'] == 'dispatch')
        self.assertEqual((dispatch['skill_suggested'], dispatch['skill']), (None, 'tdd'))
        self.assertEqual(self.requests, [])

    def test_selected_model_and_effort_reach_codex(self):
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        received = json.loads(self.received.read_text())
        self.assertEqual(received['args'], ['exec', '-m', 'model-a', '-c', 'model_reasoning_effort="high"', '-C', str(self.work.resolve()), '-'])
        self.assertEqual(received['stdin'], self.task.read_text())
        self.assertEqual([e['type'] for e in self.events()], ['decision', 'decision', 'dispatch', 'done', 'decision'])
        self.assertEqual(self.events()[1]['answer'], 'none')
        self.assertEqual(set(self.requests[0]['questions']['dispatch']['criteria']), {'codex:model-a:medium', 'codex:model-a:high'})

    def test_low_confidence_exits_four_without_launching_codex(self):
        self.confidence = 0.1
        result = self.run_task()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertFalse(self.received.exists())
        self.assertIn('hand back', result.stderr)
        self.assertEqual([e['type'] for e in self.events()], ['decision', 'handback'])

    def test_failed_codex_records_exit_code_and_next_step(self):
        self.env['FAKE_EXIT'] = '7'
        result = self.run_task()
        self.assertEqual(result.returncode, 7)
        self.assertIn('next_step: stop', result.stderr)
        self.assertEqual(self.events()[-2]['type'], 'failed')
        self.assertIn('exit_code=7', self.events()[-2]['detail'])
        self.assertEqual(self.events()[-1]['point'], 'next_step')

    def test_missing_key_uses_default_and_never_requests_jev(self):
        self.env['OPENROUTER_API_KEY'] = ''
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        received = json.loads(self.received.read_text())
        self.assertIn('model_reasoning_effort="medium"', received['args'])
        self.assertEqual(self.events()[0]['source'], 'rules')
        self.assertEqual(self.requests, [])

    def test_command_can_come_from_configuration(self):
        with self.config.open('a') as stream:
            stream.write('\n[adapters.codex]\ncommand = ' + json.dumps(str(self.fake)) + '\n')
        result = self.invoke('bin/azir-dispatch-codex', 'run', '--task-file', str(self.task))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.received.exists())

    def test_missing_executable_reports_failed(self):
        self.fake.unlink()
        result = self.run_task()
        self.assertEqual(result.returncode, 127)
        self.assertEqual(self.events()[-2]['type'], 'failed')
        self.assertIn('next_step', result.stderr)

    def test_invalid_adapter_default_never_launches(self):
        with self.config.open('a') as stream:
            stream.write('\n[adapters.codex]\ndefault = "codex:missing:high"\n')
        result = self.run_task()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.received.exists())
        self.assertEqual(self.requests, [])

    def test_efforts_must_be_a_list_not_a_string(self):
        self.config.write_text(self.config.read_text().replace('model-a = ["medium", "high"]', 'model-a = "high"'))
        result = self.run_task()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.received.exists())
        self.assertEqual(self.requests, [])

    def test_long_task_does_not_hide_recent_failure_from_next_step(self):
        self.task.write_text('x' * 6000)
        self.env['FAKE_EXIT'] = '7'
        self.env['FAKE_ERROR'] = 'failure-detail'
        result = self.run_task()
        self.assertEqual(result.returncode, 7)
        self.assertIn('failure-detail', self.requests[-1]['state'])

    def test_dispatch_filters_complete_pem_before_shortening_task(self):
        key_type = 'PRIVATE KEY'
        pem = (f'-----BEGIN {key_type}-----\n' + 'SYNTHETICPRIVATE' * 30
               + f'\n-----END {key_type}-----')
        task = pem + '\nExplain the failure.'
        self.task.write_text(task)
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SYNTHETICPRIVATE', self.log.read_text())
        self.assertTrue(self.requests)
        self.assertNotIn('SYNTHETICPRIVATE', json.dumps(self.requests))
        self.assertEqual(next(e for e in self.events() if e['type'] == 'dispatch')['task'], '[REDACTED]\nExplain the failure.')
        self.assertEqual(json.loads(self.received.read_text())['stdin'], task)

    def test_dispatch_applies_extra_filters_before_shortening_task(self):
        with self.config.open('a') as stream:
            stream.write("\n[redact]\npatterns = ['CUSTOMBEGIN(?s:.*?)CUSTOMEND']\n")
        task = 'CUSTOMBEGIN\n' + 'SYNTHETICCUSTOM' * 30 + '\nCUSTOMEND\nExplain the failure.'
        self.task.write_text(task)
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SYNTHETICCUSTOM', self.log.read_text())
        self.assertTrue(self.requests)
        self.assertNotIn('SYNTHETICCUSTOM', json.dumps(self.requests))
        self.assertEqual(next(e for e in self.events() if e['type'] == 'dispatch')['task'], '[REDACTED]\nExplain the failure.')
        self.assertEqual(json.loads(self.received.read_text())['stdin'], task)

    def test_failure_filters_complete_error_before_retaining_tail(self):
        with self.config.open('a') as stream:
            stream.write("\n[redact]\npatterns = ['CUSTOMBEGIN(?s:.*?)CUSTOMEND']\n")
        token = 'SYNTHETIC_TOKEN_' + 'A' * 48
        self.assertEqual(len(token), 64)
        token_error = 'token=' + token + '\n' + 'x' * 3934
        self.assertEqual(len(token_error + '\n'), 4006)
        key_type = 'PRIVATE KEY'
        pem_error = (f'-----BEGIN {key_type}-----\n' + 'SYNTHETICPRIVATE' * 30
                     + f'\n-----END {key_type}-----\n' + 'x' * 3934)
        custom_error = 'CUSTOMBEGIN\n' + 'SYNTHETICCUSTOM' * 30 + '\nCUSTOMEND\n' + 'x' * 3934
        for error, sensitive in ((token_error, token), (pem_error, 'SYNTHETICPRIVATE'),
                                 (custom_error, 'SYNTHETICCUSTOM')):
            with self.subTest(sensitive=sensitive):
                self.env['FAKE_EXIT'] = '7'
                self.env['FAKE_ERROR'] = error
                result = self.run_task()
                self.assertEqual(result.returncode, 7)
                self.assertNotIn(sensitive, self.log.read_text())
                self.assertTrue(self.requests)
                self.assertNotIn(sensitive, json.dumps(self.requests))
                self.assertIn('next_step', self.requests[-1]['questions'])
                failed = self.events()[-2]
                self.assertEqual(failed['type'], 'failed')
                self.assertIn('[REDACTED]', failed['detail'])
                self.assertIn('x' * 100, failed['detail'])
