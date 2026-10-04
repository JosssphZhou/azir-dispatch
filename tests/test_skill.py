import json
import subprocess
import sys

from adapter_support import AdapterCase, ROOT


SKILL_CONFIG = '''
[skill]
default = { development = "none", review = "code-review", writing = "draft" }
[skill.candidates]
development = ["tdd", "diagnosing-bugs", "implement", "none"]
review = ["code-review", "none"]
writing = ["draft", "none"]
[points.skill]
threshold = 0.7
'''


class SkillDecideTests(AdapterCase):
    def setUp(self):
        super().setUp()
        self.config.write_text(self.config.read_text().replace(
            '[skill]\ndefault = { development = "none", review = "none" }\n'
            '[skill.candidates]\ndevelopment = ["tdd", "diagnosing-bugs", "implement", "none"]\n'
            'review = ["code-review", "none"]\n', SKILL_CONFIG))

    def decide_skill(self, *extra, state='Implement a small module with tests.'):
        return self.core('decide', 'skill', '--state-file', '-',
                         '--scope', 'development', *extra, stdin=state)

    def core(self, command, kind, *extra, stdin=''):
        return subprocess.run([sys.executable, str(ROOT / 'bin/azir-dispatch'),
                               command, kind, '--config', str(self.config),
                               '--log', str(self.log), *extra], input=stdin,
                              text=True, capture_output=True, env=self.env,
                              cwd=self.work, timeout=10)

    def test_selected_skill_uses_scope_candidates_and_requested_context(self):
        self.skill_choice = 'tdd'
        result = self.decide_skill('--requested', 'implement', '--run-id', 'run-fixture')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output['answer'], output['source'], output['disposition']),
                         ('tdd', 'jev', 'apply'))
        request, = self.requests
        self.assertEqual(set(request['questions']['skill']['criteria']),
                         {'tdd', 'diagnosing-bugs', 'implement', 'none'})
        self.assertIn('调用方建议的技能：implement', request['state'])
        event, = self.events()
        self.assertEqual((event['scope'], event['requested'], event['point']),
                         ('development', 'implement', 'skill'))
        self.assertEqual(event['id'], output['event_id'])

    def test_low_confidence_returns_suggestion_without_adopting_it(self):
        self.skill_choice, self.skill_confidence = 'tdd', 0.69
        result = self.decide_skill()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output['answer'], output['jev_choice'], output['disposition']),
                         (None, 'tdd', 'handback'))

    def test_scope_can_be_added_and_threshold_is_independent_of_dispatch(self):
        self.skill_choice, self.skill_confidence = 'draft', 0.85
        self.config.write_text(self.config.read_text().replace('threshold = 0.7', 'threshold = 0.85'))
        result = self.decide_skill('--scope', 'writing')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output['answer'], output['threshold'], output['disposition']),
                         ('draft', 0.85, 'apply'))
        self.assertEqual(set(self.requests[0]['questions']['skill']['criteria']), {'draft', 'none'})

    def test_illegal_jev_skill_uses_scope_default(self):
        self.skill_choice = 'not-configured'
        result = self.decide_skill('--scope', 'review')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output['answer'], output['source'], output['error']),
                         ('code-review', 'default', 'invalid_choice'))

    def test_unavailable_jev_uses_the_requested_scope_default(self):
        self.env['OPENROUTER_API_KEY'] = ''
        for scope, expected in (('development', 'none'), ('review', 'code-review'), ('writing', 'draft')):
            with self.subTest(scope=scope):
                result = self.decide_skill('--scope', scope)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual((output['answer'], output['source'], output['disposition']),
                                 (expected, 'rules', 'apply'))
        self.assertEqual(self.requests, [])

    def test_scope_missing_unknown_or_wrong_point_is_an_argument_error(self):
        for extra in (('skill',), ('skill', '--scope', 'absent'),
                      ('dispatch', '--scope', 'development'),
                      ('dispatch', '--requested', 'tdd')):
            with self.subTest(extra=extra):
                result = self.core('decide', extra[0], '--state-file', '-', *extra[1:], stdin='Task')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.events(), [])

    def test_requested_is_filtered_and_survives_long_task_truncation(self):
        self.skill_choice = 'tdd'
        with self.config.open('a') as stream:
            stream.write("\n[redact]\npatterns = ['SYNTHETIC-SENSITIVE']\n")
        result = self.decide_skill('--requested', 'SYNTHETIC-SENSITIVE', state='x' * 5000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('调用方建议的技能：[REDACTED]', self.requests[0]['state'])
        self.assertNotIn('SYNTHETIC-SENSITIVE', self.log.read_text())
        self.assertEqual(self.events()[0]['requested'], '[REDACTED]')
        self.assertTrue(self.events()[0]['truncated'])

    def test_invalid_candidates_or_default_fail_before_request(self):
        original = self.config.read_text()
        for replacement in ('"tdd"', '[]', '["tdd", "tdd", "none"]',
                            '["tdd", 7, "none"]', '["tdd", ""]', '["tdd"]'):
            with self.subTest(replacement=replacement):
                self.config.write_text(original.replace(
                    '["tdd", "diagnosing-bugs", "implement", "none"]', replacement))
                result = self.decide_skill()
                self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.requests, [])

    def test_dispatch_record_accepts_three_nullable_skill_fields(self):
        combo = json.dumps({'executor': 'codex', 'model': 'model-a', 'effort': 'high'})
        for requested, suggested, skill in (('implement', 'tdd', 'tdd'), (None, None, None),
                                             ('implement', 'tdd', None)):
            with self.subTest(requested=requested, suggested=suggested, skill=skill):
                result = self.core('record', 'dispatch', '--run-id', 'run-fixture',
                                   '--field', 'requested=' + combo, '--field', 'suggested=null',
                                   '--field', 'actual=' + combo, '--field', 'task=Implement a module',
                                   '--field', 'target=worker-fixture',
                                   '--field', 'skill_requested=' + json.dumps(requested),
                                   '--field', 'skill_suggested=' + json.dumps(suggested),
                                   '--field', 'skill=' + json.dumps(skill))
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual((output['skill_requested'], output['skill_suggested'], output['skill']),
                                 (requested, suggested, skill))
                self.assertEqual(self.events()[-1]['skill'], skill)

    def test_invalid_skill_record_values_are_argument_errors(self):
        combo = json.dumps({'executor': 'codex', 'model': 'model-a', 'effort': 'high'})
        for field in ('skill', 'skill_requested', 'skill_suggested'):
            for value in ('7', 'true', '{}', '[]'):
                with self.subTest(field=field, value=value):
                    result = self.core('record', 'dispatch', '--run-id', 'run-fixture',
                                       '--field', 'requested=' + combo, '--field', 'suggested=null',
                                       '--field', 'actual=' + combo, '--field', 'task=Task',
                                       '--field', 'target=worker', '--field', field + '=' + value)
                    self.assertEqual(result.returncode, 2)
        self.assertEqual(self.events(), [])
