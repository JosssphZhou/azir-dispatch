"""看板读 setup-state.json 画安装进度，以及内置演示回放和录屏同一时刻的画面。"""

import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from azir_dispatch.dashboard import BG, Board, main, tw

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "azir_dispatch" / "demo" / "video-run.jsonl"
AGENTS = {"claude": False, "codex": False, "grok": False, "agy": False, "cursor-agent": False, "gemini": False}
UNSET = {"status": "optional_unconfigured", "executor": None, "model": None, "fallback": False}


def role(executor, model, fallback=False):
    return {"status": "ok", "executor": executor, "model": model, "fallback": fallback}


def state(agents=(), roles=None, jev_mode="rules", **steps):
    base = {"environment": "ok", "herdr": "ok", "repo": "ok", "models": "ok", "board": "ok",
            "first_dispatch": "pending"}
    return {"version": 1, "steps": {**base, **steps}, "agents": {**AGENTS, **{a: True for a in agents}},
            "keys": {"OPENROUTER_API_KEY": jev_mode == "jev"}, "roles": roles or {}, "jev_mode": jev_mode,
            "hints": {"first_dispatch": "让 agent 派一个只读小任务"}}


CLAUDE_ONLY = state(("claude",), {"main": role("claude", "claude-opus-5-5"),
                                  "dev": role("claude", "claude-opus-5-5", True),
                                  "review": role("claude", "claude-opus-5-5", True),
                                  "research": role("claude", "claude-opus-5-5", True), "advisor": UNSET})
CODEX_ONLY = state(("codex",), {"main": role("codex", "gpt-6.1-sol", True), "dev": role("codex", "gpt-6.1-sol"),
                                "review": role("codex", "gpt-6.1-sol"),
                                "research": role("codex", "gpt-6-luna", True), "advisor": UNSET}, jev_mode="jev")
BOTH = state(("claude", "codex"), {"main": role("claude", "claude-opus-5-5"), "dev": role("codex", "gpt-6.1-sol"),
                                   "review": role("codex", "gpt-6.1-sol"),
                                   "research": role("claude", "claude-sonnet-5-5"),
                                   "advisor": role("codex", "gpt-6-pro")}, jev_mode="jev", first_dispatch="ok")
RULES = {**BOTH, "jev_mode": "rules", "steps": {**BOTH["steps"], "first_dispatch": "pending"}}


class SetupBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / "events.jsonl"
        self.log.write_text("", encoding="utf-8")
        self.path = self.root / "setup-state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        # Same as the checker: write a temporary file, then rename it into place.
        temp = self.path.with_suffix(".tmp")
        temp.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    def frame(self, data=None, size=(80, 36), seconds=0.0):
        if data is not None:
            self.write(data)
        board = Board(log=self.log, advisor_dir=None, history=[], setup_state=self.path)
        board.resize(*size)
        frame = board.step(seconds)
        self.assertEqual(frame.canvas.border_errors(), [])
        self.assertEqual(frame.canvas.overflow_errors(), [])
        return frame

    def test_missing_file_draws_setup_not_started_with_the_usual_cards(self):
        frame = self.frame()
        self.assertIn("还没开始安装", frame.text)
        self.assertIn("○ herdr", frame.text)
        self.assertNotIn("✓", frame.text.splitlines()[1])
        self.assertEqual(frame.text.count("空闲"), 5)

    def test_claude_only_fills_every_role_with_claude_and_marks_fallbacks(self):
        frame = self.frame(CLAUDE_ONLY)
        lines = frame.text.splitlines()
        self.assertIn("✓ herdr", lines[1])
        self.assertIn("● 首次派发", lines[1])
        self.assertIn("Opus 5.5 · Claude", frame.text)
        self.assertEqual(frame.text.count("备选"), 3)
        self.assertIn("未配置，可选", frame.text)
        self.assertIn("规则模式", frame.text)
        self.assertIn("✓ claude", frame.text)
        self.assertIn("✗ codex", frame.text)
        self.assertIn("下一步 · 首次派发：让 agent 派一个只读小任务", frame.text)
        long_hint = dict(CLAUDE_ONLY, hints={"first_dispatch": "很长的提示" * 20})
        self.assertIn("…  ·  q 退出", self.frame(long_hint).text)

    def test_codex_only_fills_roles_with_codex_models(self):
        frame = self.frame(CODEX_ONLY)
        self.assertIn("GPT-6.1 Sol · 备选", frame.text)
        self.assertIn("GPT-6 Luna", frame.text)
        self.assertEqual(frame.text.count("备选"), 2)
        self.assertNotIn("规则模式", frame.text)
        self.assertIn("✓ codex", frame.text)

    def test_both_agents_after_first_dispatch_returns_to_the_normal_subtitle(self):
        frame = self.frame(BOTH)
        self.assertIn("主会话  ·  JEV  ·  执行者  ·  顾问", frame.text)
        self.assertIn("GPT-6 Pro", frame.text)
        self.assertIn("Sonnet 5.5", frame.text)
        self.assertNotIn("未配置", frame.text)
        self.assertNotIn("备选", frame.text)
        self.assertIn("执行者 0", frame.text)

    def test_rules_mode_marks_jev_and_first_dispatch_still_animates(self):
        frame = self.frame(RULES)
        self.assertIn("JEV · 分流层  规则模式", frame.text)
        board = Board(log=self.log, advisor_dir=None, history=[], setup_state=self.path)
        board.resize(80, 36)
        board.step(0)
        self.log.write_text(json.dumps({"type": "dispatch", "ts": "2026-10-04T01:00:00.000Z", "target": "first-task",
                                        "actual": {"executor": "codex-cli", "model": "gpt-6.1-sol"}}) + "\n")
        frame = board.step(0.1)
        self.assertEqual(frame.packet, "jev_sub")
        self.assertIn("first-task", frame.text)

    def test_broken_or_odd_files_read_as_not_started(self):
        for data in ("{\"steps\": ", "[]", "\xff"):
            with self.subTest(data=data):
                frame = self.frame(data)
                self.assertIn("安装状态文件读不出，按还没开始安装显示", frame.text)
                self.assertEqual(frame.text.count("空闲"), 5)
        # Fields of the wrong type count as not done, never as an error.
        frame = self.frame(json.dumps({"steps": [], "roles": "x", "agents": None, "hints": 3}))
        self.assertIn("● 环境", frame.text)
        self.assertEqual(frame.text.count("待选模型"), 3)

    def test_agents_follow_the_keys_in_the_file(self):
        many = dict(CLAUDE_ONLY, agents={"newcli": True, **AGENTS, "claude": True,
                                         **{f"extra-{i}": False for i in range(6)}})
        frame = self.frame(many)
        self.assertIn("✓ newcli", frame.text)
        self.assertIn("✗ agy", frame.text)
        self.assertRegex(frame.text, r"\+\d+")

    def test_rewritten_file_shows_on_the_next_frame_and_lights_the_new_step(self):
        self.write(state(herdr="missing", models="pending", board="pending"))
        board = Board(log=self.log, advisor_dir=None, history=[], setup_state=self.path)
        board.resize(80, 36)
        first = board.step(0)
        self.assertIn("✗ herdr", first.text)
        self.write(state(models="pending", board="pending"))
        second = board.step(1 / 30)
        row = second.text.splitlines()[1]
        self.assertIn("✓ herdr", row)
        x = tw(row[:row.index("✓ herdr")])
        self.assertNotEqual(second.canvas.cells[1][x][2], BG)
        later = board.step(3)
        self.assertEqual(later.canvas.cells[1][x][2], BG)

    def test_check_passes_for_every_state_and_size(self):
        for name, data in (("claude", CLAUDE_ONLY), ("codex", CODEX_ONLY), ("both", BOTH), ("rules", RULES)):
            self.write(data)
            for size in ("80x36", "85x47", "100x52", "160x40"):
                with self.subTest(state=name, size=size), redirect_stdout(StringIO()) as out:
                    code = main(["--log", str(self.log), "--no-advisor-scan", "--history", str(self.root / "none"),
                                 "--setup-state", str(self.path), "--check", "--size", size, "--seconds", "0.2"])
                    self.assertEqual(code, 0, out.getvalue())


class DemoReplayTests(unittest.TestCase):
    def once(self, seconds, size="84x46"):
        with redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["--replay", str(DEMO), "--once", "--size", size, "--seconds", str(seconds)]), 0)
        return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())

    def plain(self, seconds):
        board = Board(log=DEMO, replay=True, advisor_dir=None)
        board.resize(84, 46)
        frame = None
        for number in range(int(seconds * 30) + 1):
            frame = board.step(number / 30)
        return frame

    def test_demo_plays_the_recorded_round(self):
        start = self.plain(3)
        self.assertTrue(start.advisor_active)
        self.assertIn("问顾问", start.text)
        self.assertEqual(start.executors, 0)
        running = self.plain(12)
        self.assertEqual(running.executors, 2)
        self.assertIn("doc-stats → Codex GPT-6 Luna", running.text)
        self.assertIn("code-review → Codex GPT-6.1 Sol", running.text)
        self.assertEqual(running.text.count("运行中"), 2)
        wrapped = self.plain(97)
        self.assertIn("close_keep 关窗格、留工作区  0.53  交回", wrapped.text)
        self.assertIn("close_keep 关窗格、留工作区  0.85  建议", wrapped.text)

    def test_demo_replay_ignores_the_local_setup_state(self):
        self.assertIn("主会话  ·  JEV  ·  执行者  ·  顾问", self.once(0))

    def test_demo_file_has_no_local_paths_or_private_text(self):
        text = DEMO.read_text(encoding="utf-8")
        for word in ("/" + "Users/", "zh" + "ousefu", "azir 仓库", "herdr-jev", "state_preview", "question"):
            self.assertNotIn(word, text)
        for line in text.splitlines():
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
