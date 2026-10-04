import contextlib
import fcntl
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from azir_dispatch import decide
from azir_dispatch import core, events
from azir_dispatch.events import append_event


class FakeJev:
    def __init__(self, response=None, delay=0, status=200):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                owner.requests.append(json.loads(self.rfile.read(length)))
                if owner.delay:
                    import time
                    time.sleep(owner.delay)
                body = owner.response
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                try:
                    self.send_response(owner.status)
                    self.end_headers()
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def log_message(self, *args):
                pass

        self.response = response or {}
        self.delay = delay
        self.status = status
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/decisions"


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "events.jsonl"
        self.config = {
            "jev": {"timeout": 3},
            "dispatch": {
                "default": "codex:model-a:high",
                "executors": {"codex": {"models": {"model-a": ["high", "medium"]}}},
            },
        }
        self.old_key = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        self.addCleanup(self.restore_key)

    def restore_key(self):
        if self.old_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self.old_key

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_confident_choice_is_used_and_logged(self):
        response = {"answers": {"dispatch": {
            "choice": "codex:model-a:medium", "confidence": 0.9,
            "probabilities": {"codex:model-a:medium": 0.9, "codex:model-a:high": 0.1},
        }}, "usage": {"cost": 0.002}}
        with FakeJev(response) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("dispatch", "A simple task", config=self.config, log=self.log, run_id="run-1", actor="manager")
            self.assertEqual((result["answer"], result["source"]), ("codex:model-a:medium", "jev"))
            event, = self.events()
            self.assertEqual(event["type"], "decision")
            self.assertEqual(event["run_id"], "run-1")
            self.assertEqual(event["id"], result["event_id"])
            self.assertEqual(event["cost"], 0.002)
            self.assertEqual(event["probabilities"], response["answers"]["dispatch"]["probabilities"])
            self.assertEqual(event["state_head"], "A simple task")
            self.assertEqual(event["task_title"], "A simple task")
            self.assertEqual(event["options"], fake.requests[0]["questions"]["dispatch"]["criteria"])
            self.assertIn("局部修改", fake.requests[0]["questions"]["dispatch"]["criteria"]["codex:model-a:medium"])

    def test_low_confidence_hands_back_without_answer_and_preserves_jev_choice(self):
        response = {"answers": {"next_step": {"choice": "rethink", "confidence": 0.5}}, "usage": {"cost": 0.001}}
        with FakeJev(response) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "An attempt failed", config=self.config, log=self.log)
        self.assertEqual((result["source"], result["disposition"], result["answer"], result["jev_choice"]), ("jev", "handback", None, "rethink"))
        self.assertEqual(self.events()[0]["disposition"], "handback")

    def test_missing_probabilities_are_recorded_as_null_and_state_head_is_filtered(self):
        state = "xhousefu " + ("visible " * 100)
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            self.config["redact"] = {"patterns": ["xhousefu"]}
            decide("next_step", state, config=self.config, log=self.log)
        event = self.events()[0]
        self.assertIsNone(event["probabilities"])
        self.assertNotIn("xhousefu", event["state_head"])
        self.assertEqual(event["state_head"], fake.requests[0]["state"][:400])
        self.assertEqual(event["task_title"], fake.requests[0]["state"].splitlines()[0][:120])

    def test_point_specific_threshold_and_equality(self):
        self.config["points"] = {"wrapup": {"threshold": 0.95}}
        with FakeJev({"answers": {"wrapup": {"choice": "close_keep", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            wrapup = decide("wrapup", "Task done", config=self.config, log=self.log)
        with FakeJev({"answers": {"dispatch": {"choice": "codex:model-a:medium", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            dispatch = decide("dispatch", "Another task", config=self.config, log=self.log)
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.7}}}) as fake:
            self.config["jev"]["url"] = fake.url
            equality = decide("next_step", "Attempt failed", config=self.config, log=self.log)
        self.assertEqual((wrapup["disposition"], dispatch["disposition"], equality["disposition"]), ("handback", "apply", "apply"))

    def decide_with_http_timeout(self, *, record_delay=0):
        # Keep the HTTP timeout and recording reserve independent of scheduling.
        # The event writer and its deadline checks still run against a real file.
        now = 100.0
        clock = SimpleNamespace(monotonic=lambda: now)
        budget = self.config["jev"].get("timeout", 3)
        record = core.append_event

        def filtered(value, patterns, deadline):
            nonlocal now
            now += 0.05
            return value

        def timed_out(request, deadline):
            nonlocal now
            self.assertEqual(request.get_method(), "POST")
            self.assertAlmostEqual(deadline, 100.0 + budget)
            self.assertAlmostEqual(deadline - now, budget - 0.05)
            now = deadline - 0.1
            raise TimeoutError

        def delayed_record(*args, **kwargs):
            nonlocal now
            now += record_delay
            return record(*args, **kwargs)

        with patch.object(core, "time", clock), patch.object(events, "time", clock), \
                patch.object(core, "filter_value", side_effect=filtered), \
                patch.object(core, "_fetch", side_effect=timed_out) as fetch, \
                patch.object(core, "append_event", side_effect=delayed_record):
            start = clock.monotonic()
            result = decide("next_step", "Failed", config=self.config, log=self.log)
            elapsed = clock.monotonic() - start
        fetch.assert_called_once()
        return result, elapsed

    def test_timeout_uses_default_and_records_timeout(self):
        self.config["jev"]["timeout"] = 0.4
        result, elapsed = self.decide_with_http_timeout()
        self.assertLess(elapsed, 0.8)
        self.assertEqual((result["source"], result["answer"], self.events()[0]["error"]), ("default", "stop", "timeout"))
        self.assertAlmostEqual(elapsed, 0.3)
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["disposition"], "apply")
        event, = self.events()
        self.assertEqual(result["event_id"], event["id"])

    def test_default_three_second_budget_cuts_off_slow_jev(self):
        self.config["jev"].pop("timeout")
        result, elapsed = self.decide_with_http_timeout()
        self.assertLess(elapsed, 3.3)
        self.assertEqual((result["source"], result["error"]), ("default", "timeout"))
        self.assertEqual(len(self.events()), 1)
        self.assertAlmostEqual(elapsed, 2.9)
        self.assertEqual((result["answer"], result["disposition"]), ("stop", "apply"))
        self.assertEqual(result["event_id"], self.events()[0]["id"])

    def test_expired_budget_hands_back_without_answer_or_event(self):
        for budget in (0.4, 3):
            for record_delay in (0.1, 0.15):
                with self.subTest(budget=budget, record_delay=record_delay):
                    self.config["jev"]["timeout"] = budget
                    result, _ = self.decide_with_http_timeout(record_delay=record_delay)
                    self.assertEqual((result["source"], result["disposition"], result["answer"],
                                      result["event_id"], result["error"]),
                                     ("default", "handback", None, None, "记录失败"))
                    self.assertEqual(self.log.read_bytes(), b"")

    def test_missing_key_sends_nothing(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        with FakeJev() as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=self.log)
            self.assertEqual(fake.requests, [])
        self.assertEqual((result["source"], self.events()[0]["error"]), ("rules", "missing_api_key"))

    def test_invalid_confidence_is_default(self):
        for answer in ({"choice": "continue"}, {"choice": "continue", "confidence": "0.9"}, {"choice": "continue", "confidence": 1.5}):
            with self.subTest(answer=answer):
                with FakeJev({"answers": {"next_step": answer}}) as fake:
                    self.config["jev"]["url"] = fake.url
                    result = decide("next_step", "Failed", config=self.config, log=self.log)
                self.assertEqual(result["source"], "default")
                self.assertEqual(result["disposition"], "apply")

    def test_nonfinite_cost_never_writes_invalid_json(self):
        response = {"answers": {"next_step": {"choice": "continue", "confidence": 0.9}},
                    "usage": {"cost": float("nan")}}
        with FakeJev(response) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=self.log)
        self.assertEqual((result["source"], result["error"]), ("default", "invalid_response"))
        json.loads(self.log.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))

    def test_invalid_choice_and_non_json_use_default(self):
        for response in ({"answers": {"next_step": {"choice": "unknown", "confidence": 0.9}}}, b"not-json"):
            with self.subTest(response=response):
                with FakeJev(response) as fake:
                    self.config["jev"]["url"] = fake.url
                    result = decide("next_step", "Failed", config=self.config, log=self.log)
                self.assertEqual(result["source"], "default")

    def test_http_error_uses_default_and_keeps_unknown_cost(self):
        with FakeJev({"error": "upstream"}, status=503) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=self.log)
        self.assertEqual((result["source"], result["cost"], result["error"]), ("default", None, "http_error"))

    def test_redirect_is_rejected_without_forwarding_authorization(self):
        destination_requests = []

        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                destination_requests.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}).encode())

            def log_message(self, *args):
                pass

        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/elsewhere")
                self.end_headers()

            def log_message(self, *args):
                pass

        origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (origin, destination)]
        try:
            for thread in threads:
                thread.start()
            self.config["jev"]["url"] = f"http://127.0.0.1:{origin.server_port}/decisions"
            result = decide("next_step", "Failed", config=self.config, log=self.log)
        finally:
            for server in (origin, destination):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()
        self.assertEqual((result["source"], result["error"]), ("default", "http_error"))
        self.assertEqual(destination_requests, [])

    def test_key_command_fallback_can_call_jev(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.config["jev"]["key_command"] = "printf test-key"
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=self.log)
        self.assertEqual((result["source"], result["answer"]), ("jev", "continue"))

    def test_state_is_redacted_before_request_and_preview(self):
        self.config["redact"] = {"patterns": ["PRIVATEWORD"]}
        state = ("sk-or-v1-abc123 ghp_abcdef Bearer xyz password=hunter2 "
                 "-----BEGIN PRIVATE KEY-----\nprivatebits\n-----END PRIVATE KEY----- "
                 "aaaaa.bbbbb.ccccc PRIVATEWORD")
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            decide("next_step", state, config=self.config, log=self.log)
        transmitted = fake.requests[0]["state"]
        preview = self.events()[0]["state_preview"]
        for secret in ("sk-or-v1-abc123", "ghp_abcdef", "Bearer xyz", "hunter2", "privatebits", "aaaaa.bbbbb.ccccc", "PRIVATEWORD"):
            self.assertNotIn(secret, json.dumps(fake.requests[0]))
            self.assertNotIn(secret, preview)

    def test_chinese_adjacent_credentials_never_reach_request_or_preview(self):
        self.config["redact"] = {"strict": True, "patterns": ["PRIVATEWORD"]}
        secrets = ("sk-or-v1-fake456", "sk-fake456", "ghp_fake456", "gho_fake456",
                   "github_pat_fake456", "xoxb-fake456", "xoxp-fake456", "AKIAFAKE456",
                   "Bearer fake456", "password=hunter2", "token=fake456",
                   "secret=fake456", "aaaaa.bbbbb.ccccc", "PRIVATEWORD")
        state = " ".join("密钥" + value for value in secrets)
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            decide("next_step", state, config=self.config, log=self.log)
        request_body = json.dumps(fake.requests[0], ensure_ascii=False)
        preview = self.events()[0]["state_preview"]
        for value in secrets:
            self.assertNotIn(value, request_body)
            self.assertNotIn(value, preview)

    def test_strict_mode_requires_extra_patterns_and_skips_request(self):
        self.config["redact"] = {"strict": True, "patterns": ["["]}
        with FakeJev() as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=self.log)
            self.assertEqual(fake.requests, [])
        self.assertEqual(result["source"], "default")

    def test_catastrophic_extra_regex_is_cut_off_by_decision_budget(self):
        code = (
            "import json,sys,time; from azir_dispatch import decide; "
            "start=time.monotonic(); "
            "result=decide('next_step','a'*28+'!',config={'jev':{'timeout':1},'redact':{'strict':True,'patterns':['(a+)+$']}},log=sys.argv[1]); "
            "print(json.dumps({'result':result,'elapsed':time.monotonic()-start}))"
        )
        run = subprocess.run([sys.executable, "-c", code, str(self.log)], capture_output=True, text=True, timeout=4)
        self.assertEqual(run.returncode, 0, run.stderr)
        output = json.loads(run.stdout)
        self.assertLess(output["elapsed"], 1.4)
        self.assertEqual((output["result"]["source"], output["result"]["error"]), ("default", "filter_timeout"))

    def test_state_is_truncated_after_redaction(self):
        self.config["redact"] = {"max_state_chars": 12}
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            decide("next_step", "abcdefghij 123456", config=self.config, log=self.log)
            self.assertEqual(fake.requests[0]["state"], "abcdefghij 1")
        self.assertTrue(self.events()[0]["truncated"])

    def test_invalid_dispatch_configuration_is_rejected(self):
        self.config["dispatch"]["executors"] = {}
        with self.assertRaises(ValueError):
            decide("dispatch", "Task", config=self.config, log=self.log)
        self.config["dispatch"]["default"] = "missing:model:high"
        self.config["dispatch"]["executors"] = {"codex": {"models": {"model-a": ["high"]}}}
        with self.assertRaises(ValueError):
            decide("dispatch", "Task", config=self.config, log=self.log)

    def test_task_linkage_and_utc_timestamp(self):
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            decide("next_step", "Failed", config=self.config, log=self.log, run_id="run-1", actor="manager", task_id="task-1", caused_by="event-0")
        event = self.events()[0]
        self.assertEqual((event["task_id"], event["caused_by"]), ("task-1", "event-0"))
        self.assertRegex(event["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_key_command_deadline_kills_child_process_group(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        script = Path(self.temp.name) / "slow-key.sh"
        pid_file = Path(self.temp.name) / "child.pid"
        script.write_text(f"#!/bin/sh\nsleep 10 &\necho $! > '{pid_file}'\nwait\n")
        script.chmod(0o700)
        self.config["jev"]["key_command"] = str(script)
        self.config["jev"]["timeout"] = 3
        start = time.monotonic()
        result = decide("next_step", "Failed", config=self.config, log=self.log)
        self.assertLess(time.monotonic() - start, 3.3)
        self.assertEqual((result["source"], result["error"]), ("default", "timeout"))
        child_pid = int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_record_failure_prevents_jev_choice_from_applying(self):
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}}) as fake:
            self.config["jev"]["url"] = fake.url
            result = decide("next_step", "Failed", config=self.config, log=Path(self.temp.name))
        self.assertEqual((result["source"], result["disposition"], result["answer"], result["event_id"], result["error"]),
                         ("default", "handback", None, None, "记录失败"))

    def test_locked_log_hands_back_without_answer_or_event(self):
        self.log.touch()
        with self.log.open("r+b") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            self.config["jev"]["url"] = "http://127.0.0.1:1/decisions"
            start = time.monotonic()
            result = decide("dispatch", "Task", config=self.config, log=self.log)
            elapsed = time.monotonic() - start
            fcntl.flock(locked, fcntl.LOCK_UN)
        self.assertLess(elapsed, 1)
        self.assertEqual((result["source"], result["disposition"], result["answer"], result["event_id"], result["error"]),
                         ("default", "handback", None, None, "记录失败"))
        self.assertEqual(self.log.stat().st_size, 0)

    def test_incomplete_tail_is_preserved_and_separated(self):
        self.log.write_text('{"partial":')
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        command = [sys.executable, str(script), "record", "done", "--run-id", "run-1", "--log", str(self.log), "--field", "target=worker", "--field", "detail=complete"]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines[0], '{"partial":')
        self.assertEqual(json.loads(lines[1])["type"], "done")

    def test_new_log_directories_are_private_and_existing_wide_directory_warns(self):
        nested = Path(self.temp.name) / "new-parent" / "new-child" / "events.jsonl"
        old_umask = os.umask(0o022)
        try:
            append_event(nested, "done", run_id="run-1", target="worker", detail="complete")
        finally:
            os.umask(old_umask)
        for path in (nested.parent.parent, nested.parent):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o600)

        wide = Path(self.temp.name) / "existing-wide"
        wide.mkdir(mode=0o755)
        wide.chmod(0o755)
        with self.assertWarnsRegex(RuntimeWarning, '0700'):
            append_event(wide / "events.jsonl", "done", run_id="run-2", target="worker", detail="complete")
        self.assertEqual(stat.S_IMODE(wide.stat().st_mode), 0o755)

    def test_dispatch_record_keeps_requested_suggested_and_actual(self):
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        requested = '{"executor":"codex","model":"model-a","effort":"medium"}'
        actual = '{"executor":"claude-code","model":"model-b","effort":"high"}'
        command = [sys.executable, str(script), "record", "dispatch", "--run-id", "run-1", "--task-id", "task-1", "--log", str(self.log), "--field", f"requested={requested}", "--field", f"suggested={actual}", "--field", f"actual={actual}", "--field", "task=Test task", "--field", "target=worker"]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        event = self.events()[0]
        self.assertEqual(event["requested"]["executor"], "codex")
        self.assertEqual(event["actual"]["executor"], "claude-code")
        self.assertEqual(event["executor"], event["actual"]["executor"])
        self.assertEqual(event["task_id"], "task-1")

    def test_record_text_and_stdout_are_redacted_with_extra_rules(self):
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        config_file = Path(self.temp.name) / "redact.toml"
        config_file.write_text('[redact]\npatterns = ["PRIVATEWORD"]\n')
        commands = [
            ["failed", "--field", "target=worker", "--field", "detail=密钥sk-or-v1-fake456 PRIVATEWORD"],
            ["handback", "--field", "reason=授权Bearer fake456 PRIVATEWORD"],
            ["dispatch", "--field", 'requested={"executor":"codex","model":"model-a","effort":"high"}',
             "--field", "suggested=null", "--field", 'actual={"executor":"codex","model":"model-a","effort":"high"}',
             "--field", "task=密钥ghp_fake456 PRIVATEWORD", "--field", "target=worker"],
        ]
        outputs = []
        for fields in commands:
            result = subprocess.run([sys.executable, str(script), "record", fields[0], "--run-id", "run-1",
                                     "--config", str(config_file), "--log", str(self.log), *fields[1:]],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(result.stdout)
        combined = "".join(outputs) + self.log.read_text()
        for secret in ("sk-or-v1-fake456", "Bearer fake456", "ghp_fake456", "PRIVATEWORD"):
            self.assertNotIn(secret, combined)

    def test_record_cli_is_atomic_across_processes(self):
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        command = [sys.executable, str(script), "record", "done", "--run-id", "run-1", "--log", str(self.log), "--field", "target=worker", "--field", "detail=complete"]
        worker_code = "from azir_dispatch.events import append_event; import sys; [append_event(sys.argv[1], 'done', run_id='run-1', target='worker', detail='complete') for _ in range(50)]"
        workers = [subprocess.Popen([sys.executable, "-c", worker_code, str(self.log)], stderr=subprocess.PIPE) for _ in range(20)]
        for worker in workers:
            _, stderr = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, stderr.decode())
        self.assertEqual(len(self.events()), 1000)
        self.assertEqual(len({event["id"] for event in self.events()}), 1000)
        cli = subprocess.run(command, capture_output=True)
        self.assertEqual(cli.returncode, 0, cli.stderr.decode())
        self.assertEqual(len(self.events()), 1001)

    def test_cli_returns_json_on_jev_failure_and_parameter_error(self):
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        config_file = Path(self.temp.name) / "config.toml"
        config_file.write_text('[jev]\nurl = "http://127.0.0.1:1/decisions"\n')
        command = [sys.executable, str(script), "decide", "dispatch", "--state-file", "-", "--config", str(config_file), "--log", str(self.log)]
        result = subprocess.run(command, input="Test state", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["source"], "default")
        missing = subprocess.run([sys.executable, str(script), "decide", "dispatch"], capture_output=True)
        self.assertEqual(missing.returncode, 2)
        bad = Path(self.temp.name) / "bad.toml"
        bad.write_text('[dispatch]\ndefault = "missing:model:high"\n')
        invalid = subprocess.run([sys.executable, str(script), "decide", "dispatch", "--state-file", "-", "--config", str(bad), "--log", str(self.log)], input="Task", text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 2)

    def test_cli_runtime_failures_are_one_json_line_without_traceback(self):
        script = Path(__file__).resolve().parents[1] / "bin" / "azir-dispatch"
        with FakeJev({"answers": {"next_step": {"choice": "continue", "confidence": 0.9}}, "usage": None}) as fake:
            config_file = Path(self.temp.name) / "jev.toml"
            config_file.write_text(f'[jev]\nurl = "{fake.url}"\n')
            usage = subprocess.run([sys.executable, str(script), "decide", "next_step", "--state-file", "-",
                                    "--config", str(config_file), "--log", str(self.log)],
                                   input="Failed", text=True, capture_output=True, env={**os.environ, "OPENROUTER_API_KEY": "test-key"})
        bad_state = Path(self.temp.name) / "bad-state.txt"
        bad_state.write_bytes(b"\xff")
        invalid_text = subprocess.run([sys.executable, str(script), "decide", "next_step", "--state-file", str(bad_state),
                                       "--log", str(self.log)], text=True, capture_output=True)
        record = subprocess.run([sys.executable, str(script), "record", "done", "--run-id", "run-1",
                                 "--log", self.temp.name, "--field", "target=worker", "--field", "detail=complete"],
                                text=True, capture_output=True)
        for result, expected in ((usage, "invalid_response"), (invalid_text, "state_read_error"), (record, "record_failed")):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertEqual(json.loads(result.stdout)["error"], expected)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
