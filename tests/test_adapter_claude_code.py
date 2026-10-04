import json
from adapter_support import AdapterCase


class ClaudeCodeTests(AdapterCase):
    def test_low_skill_confidence_denies_without_modified_input(self):
        self.skill_choice, self.skill_confidence = 'tdd', 0.4
        result = self.hook()
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertNotIn('updatedInput', output)
        self.assertIn('skill', output['additionalContext'])
        self.assertEqual([e['type'] for e in self.events()], ['decision', 'decision', 'handback'])
        self.assertEqual(self.events()[1]['jev_choice'], 'tdd')

    def test_skill_can_update_prompt_without_an_executor_route(self):
        self.skill_choice = 'tdd'
        self.config.write_text(self.config.read_text().replace(
            '[adapters.claude_code.routes]\n"claude-code:model-b:high" = "worker-high"\n', ''))
        original = {'prompt': 'Write a module', 'subagent_type': 'general-purpose', 'model': 'other-model'}
        output = json.loads(self.hook(tool_input=original).stdout)['hookSpecificOutput']
        self.assertEqual(output['updatedInput'], dict(original, prompt='用 tdd 技能，Write a module'))
        self.assertEqual(self.events()[-1]['actual']['model'], 'other-model')

    def test_unavailable_jev_uses_default_skill_prefix(self):
        self.env['OPENROUTER_API_KEY'] = ''
        self.config.write_text(self.config.read_text().replace('development = "none"', 'development = "tdd"'))
        output = json.loads(self.hook().stdout)['hookSpecificOutput']
        self.assertEqual(output['updatedInput']['prompt'], '用 tdd 技能，Write a small module')
        self.assertEqual(self.requests, [])
        self.assertEqual((self.events()[-1]['skill_suggested'], self.events()[-1]['skill']), (None, 'tdd'))

    def test_selected_skill_prefix_preserves_all_subagent_inputs(self):
        self.skill_choice = 'tdd'
        original = {'prompt': 'Write a small module', 'subagent_type': 'general-purpose',
                    'description': 'Module task', 'run_in_background': False}
        result = self.hook(tool_input=original)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['updatedInput'], dict(original,
                         prompt='用 tdd 技能，Write a small module',
                         subagent_type='worker-high', model='model-b'))
        skill_event = next(e for e in self.events() if e.get('point') == 'skill')
        self.assertEqual(skill_event['scope'], 'development')
        dispatch = self.events()[-1]
        self.assertEqual((dispatch['skill_requested'], dispatch['skill_suggested'], dispatch['skill']),
                         (None, 'tdd', 'tdd'))
        self.assertEqual(dispatch['caused_by'], skill_event['id'])

    def test_confident_dispatch_changes_agent_type_and_records_decision(self):
        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['updatedInput'], {'prompt': 'Write a small module', 'subagent_type': 'worker-high', 'model': 'model-b'})
        events = self.events()
        self.assertEqual([e['type'] for e in events], ['decision', 'decision', 'dispatch'])
        self.assertEqual(events[-1]['skill'], 'none')
        self.assertEqual(events[-1]['actual'], {'executor': 'claude-code', 'model': 'model-b', 'effort': 'high'})
        self.assertEqual(set(self.requests[0]['questions']['dispatch']['criteria']), {'claude-code:model-b:high'})

    def test_low_confidence_leaves_parameters_and_returns_to_parent(self):
        self.confidence = 0.2
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertNotIn('updatedInput', output)
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertIn('dispatching session', output['additionalContext'])
        self.assertEqual(self.events()[0]['disposition'], 'handback')
        self.assertEqual([e['type'] for e in self.events()], ['decision', 'handback'])

    def test_finished_agent_returns_wrapup_hint_to_parent(self):
        self.hook()
        result = self.hook('PostToolUse', tool_response={'status': 'completed', 'content': 'Module written'})
        self.assertEqual(result.returncode, 0)
        self.assertIn('wrapup', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
        events = self.events()
        self.assertEqual([e['type'] for e in events], ['decision', 'decision', 'dispatch', 'done', 'decision'])
        self.assertEqual(events[-1]['point'], 'wrapup')
        self.assertEqual(events[-1]['caused_by'], events[-2]['id'])
        self.assertEqual(len({e['run_id'] for e in events}), 1)

    def test_failed_agent_records_failure_and_asks_next_step(self):
        self.hook()
        result = self.hook('PostToolUseFailure', error='server unavailable; token=fixture-secret')
        self.assertIn('next_step', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
        events = self.events()
        self.assertEqual(events[-2]['type'], 'failed')
        self.assertEqual(events[-1]['point'], 'next_step')
        self.assertNotIn('fixture-secret', self.log.read_text())

    def test_background_acknowledgement_is_not_completion(self):
        self.hook()
        result = self.hook('PostToolUse', tool_response={'isAsync': True, 'agentId': 'a-fixture'})
        self.assertIn('background', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('done', [e['type'] for e in self.events()])

    def test_unmapped_selection_remains_a_suggestion(self):
        with self.config.open('a') as stream:
            stream.write('\n[adapters.claude_code]\nroutes = {}\n')
        # Replace only this temporary fixture, not a user configuration.
        self.config.write_text(self.config.read_text().replace('[adapters.claude_code.routes]\n"claude-code:model-b:high" = "worker-high"\n', ''))
        result = self.hook()
        self.assertNotIn('updatedInput', json.loads(result.stdout)['hookSpecificOutput'])
        self.assertEqual(self.events()[-1]['actual']['model'], 'inherit')
        self.assertEqual(self.events()[-1]['suggested']['model'], 'model-b')

    def test_nested_and_unrelated_hooks_do_nothing(self):
        for fields in ({'agent_id': 'child'}, {'tool_name': 'Read'}):
            result = self.hook(**fields)
            self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(self.events(), [])

    def test_background_outcome_can_be_reported_explicitly(self):
        self.hook()
        detail = self.work / 'outcome.txt'
        detail.write_text('Background worker finished')
        result = self.invoke('adapters/claude_code/hook.py', '--report', 'done', '--run-id', 'session-fixture:call-fixture', '--detail-file', str(detail))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('wrapup', result.stdout)
        self.assertEqual(self.events()[-2]['type'], 'done')

    def test_successful_tool_can_return_a_failed_agent_status(self):
        self.hook()
        result = self.hook('PostToolUse', tool_response={'status': 'failed', 'error': 'quota exceeded'})
        self.assertIn('next_step', result.stdout)
        self.assertEqual(self.events()[-2]['type'], 'failed')

    def test_route_does_not_keep_a_conflicting_model_override(self):
        result = self.hook(tool_input={'prompt': 'Write a module', 'subagent_type': 'general-purpose', 'model': 'other-model'})
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['updatedInput']['model'], 'model-b')

    def test_explicit_outcome_without_dispatch_fails(self):
        detail = self.work / 'outcome.txt'
        detail.write_text('Complete')
        result = self.invoke('adapters/claude_code/hook.py', '--report', 'done', '--run-id', 'unknown:call', '--detail-file', str(detail))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.events(), [])

    def test_dispatch_filters_complete_pem_before_shortening_task(self):
        key_type = 'PRIVATE KEY'
        pem = (f'-----BEGIN {key_type}-----\n' + 'SYNTHETICPRIVATE' * 30
               + f'\n-----END {key_type}-----')
        task = pem + '\nExplain the failure.'
        result = self.hook(tool_input={'prompt': task, 'subagent_type': 'general-purpose'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SYNTHETICPRIVATE', self.log.read_text())
        self.assertTrue(self.requests)
        self.assertNotIn('SYNTHETICPRIVATE', json.dumps(self.requests))
        self.assertEqual(self.events()[-1]['task'], '[REDACTED]\nExplain the failure.')
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['updatedInput']['prompt'], task)

    def test_dispatch_applies_extra_filters_before_shortening_task(self):
        with self.config.open('a') as stream:
            stream.write("\n[redact]\npatterns = ['CUSTOMBEGIN(?s:.*?)CUSTOMEND']\n")
        task = 'CUSTOMBEGIN\n' + 'SYNTHETICCUSTOM' * 30 + '\nCUSTOMEND\nExplain the failure.'
        result = self.hook(tool_input={'prompt': task, 'subagent_type': 'general-purpose'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SYNTHETICCUSTOM', self.log.read_text())
        self.assertTrue(self.requests)
        self.assertNotIn('SYNTHETICCUSTOM', json.dumps(self.requests))
        self.assertEqual(self.events()[-1]['task'], '[REDACTED]\nExplain the failure.')
