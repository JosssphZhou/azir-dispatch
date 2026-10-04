"""Question configuration at the real HTTP and event boundary."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from azir_dispatch import decide
from azir_dispatch.core import _point
from tests.test_decide import FakeJev


class PointCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'events.jsonl'
        self.config = {
            'jev': {'timeout': 3},
            'dispatch': {'default': 'codex:model-a:high',
                         'executors': {'codex': {'models': {'model-a': ['high', 'xhigh']}}}},
            'skill': {'candidates': {'development': ['tdd', 'none']},
                      'default': {'development': 'none'}},
        }
        self.key_patch = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-key'})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def test_configured_question_context_and_partial_criteria_reach_http(self):
        for name, choice in [('dispatch', 'codex:model-a:high'), ('next_step', 'continue'),
                             ('wrapup', 'close_keep'), ('skill', 'tdd')]:
            with self.subTest(point=name):
                self.config['points'] = {name: {'question': '应该选哪项？',
                                               'instructions': '按当前阶段和团队规则判断。',
                                               'criteria': {choice: '满足明确条件时选择。'}}}
                with FakeJev({'answers': {name: {'choice': choice, 'confidence': 0.9}}}) as fake:
                    self.config['jev']['url'] = fake.url
                    result = decide(name, '当前任务', config=self.config, log=self.log,
                                    scope='development' if name == 'skill' else None)
                request = fake.requests[0]['questions'][name]
                self.assertEqual(request['instructions'], '应该选哪项？\n按当前阶段和团队规则判断。')
                self.assertEqual(request['criteria'][choice], '满足明确条件时选择。')
                self.assertGreater(len(request['criteria']), 1)
                self.assertEqual(result['answer'], choice)

    def test_default_descriptions_explain_decision_conditions(self):
        expected = {'dispatch': ('codex:model-a:high', '常规'),
                    'next_step': ('continue', '退修'), 'wrapup': ('close_keep', '保留'),
                    'skill': ('tdd', '测试')}
        for name, (choice, fragment) in expected.items():
            with self.subTest(point=name):
                with FakeJev({'answers': {name: {'choice': choice, 'confidence': 0.9}}}) as fake:
                    self.config['jev']['url'] = fake.url
                    decide(name, '当前任务', config=self.config, log=self.log,
                           scope='development' if name == 'skill' else None)
                self.assertIn(fragment, fake.requests[0]['questions'][name]['criteria'][choice])

    def test_instructions_without_question_keep_builtin_question(self):
        self.config['points'] = {'next_step': {'instructions': '仅在缺少授权时交回。'}}
        point = _point('next_step', self.config)
        self.assertIn('下一步', point.question)
        self.assertTrue(point.question.endswith('\n仅在缺少授权时交回。'))

    def test_prompt_configuration_is_filtered_before_http_and_events(self):
        self.config['redact'] = {'strict': True, 'patterns': ['PRIVATEWORD']}
        self.config['points'] = {'next_step': {'question': 'PRIVATEWORD 下一步？',
                                             'instructions': 'token=secret-value',
                                             'criteria': {'continue': 'PRIVATEWORD 按意见退修'}}}
        with FakeJev({'answers': {'next_step': {'choice': 'continue', 'confidence': 0.9}}}) as fake:
            self.config['jev']['url'] = fake.url
            decide('next_step', '普通任务', config=self.config, log=self.log)
        for content in (json.dumps(fake.requests), self.log.read_text()):
            self.assertNotIn('PRIVATEWORD', content)
            self.assertNotIn('secret-value', content)
            self.assertIn('[REDACTED]', content)

    def test_invalid_prompt_configuration_fails_before_http_or_event(self):
        cases = [[], {'question': ''}, {'question': 1}, {'instructions': []},
                 {'instructions': ' '}, {'criteria': []}, {'criteria': {'unknown': '说明'}},
                 {'criteria': {'continue': ''}}, {'criteria': {'continue': 42}}]
        with FakeJev() as fake:
            self.config['jev']['url'] = fake.url
            for settings in cases:
                with self.subTest(settings=settings):
                    self.config['points'] = {'next_step': settings}
                    with self.assertRaises(ValueError):
                        decide('next_step', '当前任务', config=self.config, log=self.log)
            self.assertEqual(fake.requests, [])
        self.assertFalse(self.log.exists())

    def test_criteria_do_not_add_or_remove_candidates(self):
        self.config['points'] = {'next_step': {'criteria': {'continue': '常规退修'}}}
        self.assertEqual(set(_point('next_step', self.config).options), {'continue', 'rethink', 'stop'})

    def test_skill_criteria_cover_multiple_scopes_without_expanding_current_choices(self):
        self.config['skill']['candidates']['review'] = ['code-review', 'none']
        self.config['skill']['default']['review'] = 'none'
        self.config['points'] = {'skill': {'criteria': {'tdd': '先写测试', 'code-review': '只读审查'}}}
        point = _point('skill', self.config, 'review')
        self.assertEqual(point.options['code-review'], '只读审查')
        self.assertEqual(set(point.options), {'code-review', 'none'})
