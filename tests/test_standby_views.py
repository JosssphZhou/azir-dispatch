# Standby screens of the JEV demo tab: JEV view before/without live decisions, and the executor area.
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from azir_dispatch.exec_idle import ExecIdle
from azir_dispatch.jev_slot import SLOT_SECONDS
from azir_dispatch.jev_view import View, cell_width

FIVE_TIERS = """
[points.dispatch]
threshold = 0.65
[points.dispatch.criteria]
"codex-cli:gpt-6.1-sol:high" = "复杂任务：修改代码逻辑、开发新功能、修复故障"
"codex-cli:gpt-6-luna:medium" = "简单任务：只读统计、查找内容"
"pi:gemini-3.7-flash:default" = "写作任务：中文文章、文案、润色改写"
"grok:grok-build:default" = "联网调研任务：搜索最新资讯、X 帖子"
"claude:claude-opus-5-5:high" = "规划和前端类任务：写规格、拆任务"
"""


def decision(confidence=0.79):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {"type": "decision", "ts": now, "point": "dispatch", "id": "abc",
            "question": "当前任务应该派给谁？", "task_title": "统计文档",
            "options": {"codex-cli:gpt-6.1-sol:high": "复杂", "codex-cli:gpt-6-luna:medium": "简单"},
            "probabilities": {"codex-cli:gpt-6.1-sol:high": 0.1, "codex-cli:gpt-6-luna:medium": 0.9},
            "jev_choice": "codex-cli:gpt-6-luna:medium", "answer": "codex-cli:gpt-6-luna:medium",
            "confidence": confidence, "threshold": 0.7, "source": "jev", "disposition": "apply",
            "latency_ms": 800, "cost": 0.00004,
            "state_head": "统计文档 工作树 `[REDACTED]` 里的文件"}


def fits(frame):
    rows = frame.text.splitlines()
    return len(rows) <= frame.height and all(cell_width(row) <= frame.width for row in rows)


class StandbyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "events.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_empty_log_shows_standby_with_five_configured_tiers_and_threshold(self):
        self.log.write_text("")
        config = Path(self.dir.name) / "azir-dispatch.toml"
        config.write_text(FIVE_TIERS)
        frame = View(self.log, width=60, height=16, config=config).step(1.0)
        for text in ("JEV 待命", "6.1 Sol", "Luna", "Gemini", "Grok", "Opus", "$0.10 / $0.50",
                     "$2.00 / $10.00", "Claude 订阅", "Google 订阅", "SuperGrok 订阅", "把握线 0.65", "今日判断 0 次",
                     "写作任务：中文文章", "联网调研任务：搜索最新资讯"):
            self.assertIn(text, frame.text)
        tier_rows = [row for row in frame.text.splitlines() if row.startswith("› ")]
        self.assertEqual(len(tier_rows), 5)
        self.assertNotIn("工程经理", frame.text)
        self.assertNotIn("codex-cli:", frame.text)
        # The herdr pane border already reads "JEV 判断"; the body must not repeat it.
        self.assertNotIn("JEV 判断", frame.text)
        self.assertTrue(fits(frame))

    def test_history_is_replayed_and_bars_grow(self):
        self.log.write_text(json.dumps(decision(), ensure_ascii=False) + "\n")
        view = View(self.log, width=60, height=16)
        view.step(0.0)
        early, late = view.step(SLOT_SECONDS + 0.3), view.step(1.5)
        self.assertIn("回放", late.text)
        self.assertIn("0.79", late.text)
        self.assertNotIn("JEV 判断", late.text)
        self.assertLess(sum(early.bar_lengths), sum(late.bar_lengths))
        self.assertEqual(view.today, 1)
        # Short names and the threshold line; what JEV saw is no longer shown.
        self.assertIn("▸ ❂ Luna", late.text)
        self.assertIn("6.1 Sol", late.text)
        self.assertNotIn("codex-cli:", late.text)
        self.assertIn("线 0.70", late.text)
        self.assertNotIn("JEV 看到的", late.text)
        self.assertNotIn("REDACTED", late.text)

    def test_new_decision_switches_replay_to_live(self):
        self.log.write_text(json.dumps(decision(), ensure_ascii=False) + "\n")
        view = View(self.log, width=60, height=16)
        view.step(2.0)
        with self.log.open("a") as stream:
            stream.write(json.dumps(decision(0.91), ensure_ascii=False) + "\n")
        view.step(0.0)
        frame = view.step(SLOT_SECONDS + 1.4)
        self.assertNotIn("回放", frame.text)
        self.assertIn("0.91", frame.text)

    def test_narrow_panes_degrade_without_overflow(self):
        self.log.write_text("")
        for width, height in ((47, 17), (40, 10), (30, 8), (12, 3)):
            with self.subTest(size=(width, height)):
                self.assertTrue(fits(View(self.log, width=width, height=height).step(1.0)))
                self.assertTrue(fits(ExecIdle(width, height).step(1.0)))

    def test_executor_area_shows_five_idle_stations_without_shell_prompt(self):
        names = ("6.1 Sol", "Luna", "Gemini", "Grok", "Opus")
        for width, height in ((60, 15), (47, 16), (47, 17)):
            frame = ExecIdle(width, height).step(0.7)
            positions = [frame.text.index(name) for name in names]
            self.assertEqual(positions, sorted(positions))  # same order as the JEV standby list
            self.assertEqual(frame.text.count("空闲"), 6)  # five stations and the header count
            self.assertIn("空闲 5 / 5", frame.text)
            self.assertNotIn("%", frame.text)
            self.assertNotIn("等待任务书", frame.text)
            self.assertNotIn("工程经理", frame.text)
            self.assertTrue(fits(frame))

class AgentIconTests(unittest.TestCase):
    KEYS = ("codex-cli:gpt-6.1-sol:high", "codex-cli:gpt-6-luna:medium", "claude:claude-opus-5-5:high",
            "pi:gemini-3.7-flash:default", "grok:grok-build:default")

    def test_each_executor_has_a_one_cell_icon_and_a_three_row_logo(self):
        from azir_dispatch.agent_icons import icon, logo_rows

        marks = set()
        for key in self.KEYS:
            with self.subTest(key=key):
                mark, rgb = icon(key)
                self.assertEqual(cell_width(mark), 1)
                self.assertIsNotNone(rgb)
                marks.add(mark)
                logo = logo_rows(key)
                self.assertEqual(len(logo), 3)
                self.assertTrue(all(cell_width(text) == 7 for text, _ in logo))
        self.assertEqual(len(marks), 4)  # Sol and Luna share the OpenAI mark
        self.assertEqual(icon("unknown:model:x"), ("", None))

    def test_long_condition_is_cut_to_width_not_dropped(self):
        from azir_dispatch.jev_view import short_condition

        self.assertEqual(short_condition("claude:x:high", "规划和前端类任务：写规格"), "规划和前端类任务")
        cut = short_condition("x:y:z", "这是一个非常非常长的类别名称：说明")
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(cell_width(cut), 16)

    def test_executor_cards_draw_logos_and_names(self):
        frame = ExecIdle(60, 15).step(0.7)
        self.assertIn("█", frame.text)
        for name in ("6.1 Sol", "Luna", "Gemini", "Grok", "Opus"):
            self.assertIn(name, frame.text)
        self.assertTrue(fits(frame))


class SlotMachineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "events.jsonl"
        self.log.write_text("")

    def tearDown(self):
        self.dir.cleanup()

    def arrive(self, sample, width=60, height=16):
        view = View(self.log, width=width, height=height)
        view.step(0.0)
        with self.log.open("a") as stream:
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
        return view

    def five_way(self, point="dispatch"):
        sample = decision(1.0)
        sample["point"] = point
        sample["options"] = {"codex-cli:gpt-6.1-sol:high": "复杂", "codex-cli:gpt-6-luna:medium": "简单",
                             "pi:gemini-3.7-flash:default": "写作", "grok:grok-build:default": "调研",
                             "claude:claude-opus-5-5:high": "前端"}
        sample["probabilities"] = {key: 0.0 for key in sample["options"]}
        sample["probabilities"]["grok:grok-build:default"] = 1.0
        sample["jev_choice"] = sample["answer"] = "grok:grok-build:default"
        return sample

    def test_new_dispatch_spins_then_lands_on_the_choice_and_settles_into_bars(self):
        view = self.arrive(self.five_way())
        spinning = view.step(0.1)
        self.assertIn("JEV 推理中", spinning.text)
        self.assertIn("╔", spinning.text)
        self.assertTrue(fits(spinning))
        won = view.step(2.5 - 0.1)
        center = [row for row in won.text.splitlines() if "▶" in row]
        self.assertEqual(len(center), 1)
        self.assertEqual(center[0].count("⊘ Grok"), 3)
        self.assertIn("JEV 选中 · 把握 1.00", won.text)
        self.assertTrue(won.selected_rows)
        self.assertTrue(fits(won))
        settled = view.step(SLOT_SECONDS + 2.0 - 2.5)
        self.assertNotIn("╔", settled.text)
        self.assertIn("▸ ⊘ Grok", settled.text)
        self.assertIn("100%", settled.text)
        # Candidates at 0% are left out of the settled view.
        self.assertNotIn("Luna", settled.text)

    def test_replay_loop_plays_the_slot_machine_again(self):
        self.log.write_text(json.dumps(self.five_way(), ensure_ascii=False) + "\n")
        view = View(self.log, width=60, height=16)
        view.step(0.0)
        self.assertIn("╔", view.step(0.5).text)
        view.step(SLOT_SECONDS + 3.0)
        cycle_start = view.step(7.0 + SLOT_SECONDS - (SLOT_SECONDS + 3.5) + 0.2)
        self.assertIn("╔", cycle_start.text)
        self.assertIn("回放", cycle_start.text)

    def test_wrapup_and_next_step_do_not_play_the_slot_machine(self):
        for point in ("wrapup", "next_step"):
            with self.subTest(point=point):
                self.log.write_text("")
                frame = self.arrive(self.five_way(point)).step(0.1)
                self.assertNotIn("╔", frame.text)
                self.assertNotIn("JEV 选中", frame.text)

    def test_slot_frames_fit_small_panes(self):
        for width, height in ((60, 16), (47, 17), (40, 10), (30, 8), (12, 3)):
            for moment in (0.1, 1.0, 2.2, 2.5):
                with self.subTest(size=(width, height), moment=moment):
                    self.log.write_text("")
                    view = self.arrive(self.five_way(), width, height)
                    self.assertTrue(fits(view.step(moment)))


if __name__ == "__main__":
    unittest.main()
