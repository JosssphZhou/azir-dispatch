import json
from adapter_support import AdapterCase


class AdvisorWatchTests(AdapterCase):
    def test_repeated_scan_records_each_call_once_without_changing_source(self):
        sessions = self.work / 'sessions'
        sessions.mkdir()
        source = sessions / 'fixture.jsonl'
        original = json.dumps({'sessionId': 's-fixture', 'cwd': '/workspace/example', 'timestamp': '2026-10-01T01:00:00Z', 'message': {'content': [{'type': 'server_tool_use', 'name': 'advisor', 'id': 'advisor-fixture', 'input': {'private': 'NEVER_LOG_THIS'}}]}}) + '\n'
        source.write_text(original)
        for _ in range(2):
            result = self.invoke('adapters/claude_code/advisor_watch.py', '--sessions-dir', str(sessions))
            self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['type'], 'advisor')
        self.assertEqual(events[0]['advisor_call_id'], 'advisor-fixture')
        self.assertEqual(source.read_text(), original)
        self.assertNotIn('NEVER_LOG_THIS', self.log.read_text())

    def test_nested_and_top_level_calls_skip_bad_and_unfinished_lines(self):
        sessions = self.work / 'sessions'
        sessions.mkdir()
        source = sessions / 'fixture.jsonl'
        source.write_text('invalid\n' + json.dumps({'type': 'server_tool_use', 'name': 'advisor', 'id': 'one', 'timestamp': '2026-10-01T02:00:00Z'}) + '\n' + json.dumps({'message': {'content': [{'type': 'server_tool_use', 'name': 'other', 'id': 'ignore'}, {'type': 'server_tool_use', 'name': 'advisor', 'id': 'two'}]}}))
        result = self.invoke('adapters/claude_code/advisor_watch.py', '--sessions-dir', str(sessions))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e['advisor_call_id'] for e in self.events()], ['one'])
        with source.open('a') as stream:
            stream.write('\n')
        result = self.invoke('adapters/claude_code/advisor_watch.py', '--sessions-dir', str(sessions))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e['advisor_call_id'] for e in self.events()], ['one', 'two'])
        self.assertEqual(self.events()[0]['ts'], '2026-10-01T02:00:00Z')

    def test_session_directory_cannot_be_used_as_log_destination(self):
        sessions = self.work / 'sessions'
        sessions.mkdir()
        transcript = sessions / 'session.jsonl'
        original = '{"type":"server_tool_use","name":"advisor","id":"fixture"}\n'
        transcript.write_text(original)
        result = self.invoke('adapters/claude_code/advisor_watch.py', '--sessions-dir', str(sessions), '--log', str(transcript))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(transcript.read_text(), original)
