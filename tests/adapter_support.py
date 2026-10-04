"""Temporary files and a loopback-only fake Decisions API."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.log = self.work / 'events.jsonl'
        self.config = self.work / 'config.toml'
        self.choice = 'claude-code:model-b:high'
        self.confidence = 0.95
        self.skill_choice = 'none'
        self.skill_confidence = 0.95
        self.requests = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.requests.append(body)
                point = next(iter(body['questions']))
                choice = case.choice if point == 'dispatch' else 'stop' if point == 'next_step' else 'keep'
                confidence = case.confidence
                if point == 'skill':
                    choice, confidence = case.skill_choice, case.skill_confidence
                data = json.dumps({'answers': {point: {'choice': choice, 'confidence': confidence}}, 'usage': {'cost': 0}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(stop)
        self.config.write_text(f'''[jev]
url = "http://127.0.0.1:{self.server.server_port}"
[dispatch]
default = "codex:model-a:medium"
[dispatch.executors.codex.models]
model-a = ["medium", "high"]
[dispatch.executors.claude-code.models]
model-b = ["high"]
[skill]
default = {{ development = "none", review = "none" }}
[skill.candidates]
development = ["tdd", "diagnosing-bugs", "implement", "none"]
review = ["code-review", "none"]
[adapters.claude_code.routes]
"claude-code:model-b:high" = "worker-high"
''')
        self.env = dict(os.environ, OPENROUTER_API_KEY='fixture-key', PYTHONPATH=str(ROOT))
        self.env.pop('AZIR_DISPATCH_LOG', None)
        self.env.pop('AZIR_DISPATCH_CONFIG', None)

    def invoke(self, script, *args, stdin=''):
        return subprocess.run([sys.executable, str(ROOT / script), '--config', str(self.config), '--log', str(self.log), *args], input=stdin, text=True, capture_output=True, env=self.env, cwd=self.work, timeout=10)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def hook(self, event='PreToolUse', **fields):
        data = {'session_id': 'session-fixture', 'hook_event_name': event, 'tool_name': 'Agent', 'tool_use_id': 'call-fixture', 'tool_input': {'prompt': 'Write a small module', 'subagent_type': 'general-purpose'}, **fields}
        return self.invoke('adapters/claude_code/hook.py', stdin=json.dumps(data))
