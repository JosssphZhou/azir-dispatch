import json
import tempfile
import unittest
from pathlib import Path

from azir_dispatch.jev_view import RULE, EventSource, View, _ansi, cell_width, option_label


def event(point="dispatch", options=None, probabilities=None, confidence=0.91,
          threshold=0.7, disposition="apply", source="jev", state="任务状态开头"):
    options = options or {"codex:model-a:high": "代码任务", "claude:model-b:high": "视觉任务"}
    return {"type": "decision", "ts": "2026-10-01T12:00:00.000Z", "point": point,
            "question": "应该交给谁？", "options": options, "jev_choice": next(iter(options)),
            "answer": next(iter(options)) if disposition == "apply" else None,
            "confidence": confidence, "threshold": threshold, "disposition": disposition,
            "source": source, "probabilities": probabilities, "state_head": state,
            "task_title": "demo task title", "latency_ms": 190, "cost": 0.00002,
            "id": "feedbeef1234"}


class JevViewTests(unittest.TestCase):
    def test_two_and_four_options_show_probability_bars_and_selected_choice(self):
        for options in (
            {"甲": "代码任务", "乙": "视觉任务"},
            {"甲": "代码任务", "乙": "视觉任务", "丙": "写作任务", "丁": "研究任务"},
        ):
            with self.subTest(count=len(options)), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "events.jsonl"
                probabilities = {name: (0.7 if name == "甲" else 0.1) for name in options}
                log.write_text(json.dumps(event(options=options, probabilities=probabilities), ensure_ascii=False) + "\n")
                view = View(log, replay=True, width=82, height=16)
                frame = view.step(2.0)
                self.assertIn("选执行者 · demo task title", frame.text)
                self.assertIn("▸ 甲", frame.text)
                self.assertIn("70%", frame.text)
                self.assertIn("10%", frame.text)
                self.assertIn("线 0.70", frame.text)
                self.assertIn("直接执行", frame.text)
                # The squeezed pane keeps only four blocks.
                for removed in ("应该交给谁？", "代码任务", "190 ms", "$0.00002", "个选项",
                                "JEV 看到的", "把握历史", "最近判断"):
                    self.assertNotIn(removed, frame.text)
                self.assertTrue(frame.selected_rows)
                self.assertTrue(all(cell_width(row) <= 82 for row in frame.text.splitlines()))
                self.assertLessEqual(len(frame.text.splitlines()), 16)

    def test_rules_decision_shows_rules_destination_without_thinking(self):
        # Written by `azir-dispatch decide` with no OpenRouter key: no JEV choice or confidence.
        fixture = Path(__file__).parent / "fixtures" / "rules-decisions.jsonl"
        for line in fixture.read_text(encoding="utf-8").splitlines():
            sample = json.loads(line)
            with self.subTest(point=sample["point"]), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "events.jsonl"
                log.write_text(line + "\n", encoding="utf-8")
                view = View(log, replay=True, width=82, height=17)
                first = view.step(0.0)
                self.assertNotIn("推理中", first.text)
                frame = view.step(5.0)
                conclusion_row = [row for row in frame.text.splitlines() if row.strip()][-1]
                self.assertEqual(conclusion_row.strip(), "→ 规则")
                for other in ("直接执行", "建议", "交回工程经理", "走默认"):
                    self.assertNotIn(other, frame.text)
                marked = next(row for row in frame.text.splitlines() if "▸" in row)
                self.assertIn(option_label(sample["answer"]), marked)
                conclusion = [run for row, runs in frame.color_runs.items()
                              if "→ 规则" in frame.text.splitlines()[row] for run in runs]
                self.assertIn(RULE, [color for _, _, color in conclusion])

    def test_destinations_cover_handback_apply_advisory_and_default(self):
        cases = [
            (event(disposition="handback", confidence=0.4), "交回"),
            (event(point="dispatch"), "直接执行"),
            (event(point="next_step"), "建议"),
            (event(source="default", point="dispatch"), "JEV 不可用，走默认"),
        ]
        for sample, destination in cases:
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "events.jsonl"
                log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
                frame = View(log, replay=True, width=82, height=16).step(2.0)
                self.assertIn(destination, frame.text)

    def test_old_event_without_probabilities_and_state_displays_missing_label(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            sample = event()
            sample.pop("probabilities")
            sample.pop("state_head")
            sample["state_preview"] = "legacy preview"
            log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
            frame = View(log, replay=True, width=82, height=16).step(2.0)
            self.assertIn("无", frame.text)
            self.assertNotIn("legacy preview", frame.text)

    def test_legacy_array_options_render_each_candidate_without_probabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            sample = event(options=["甲", "乙"])
            sample["jev_choice"] = "乙"
            sample["answer"] = "乙"
            sample.pop("probabilities")
            log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
            frame = View(log, replay=True, width=82, height=17).step(2.0)

            option_rows = [row for row in frame.text.splitlines() if "无" in row and ("甲" in row or "乙" in row)]
            self.assertEqual(len(option_rows), 2)
            self.assertIn("甲", option_rows[0])
            self.assertIn("无", option_rows[0])
            self.assertIn("▸ 乙", option_rows[1])
            self.assertIn("无", option_rows[1])
            rows = frame.text.splitlines()
            conclusion = next(i for i, row in enumerate(rows) if "直接执行" in row)
            # The chosen option and the conclusion line are bold.
            self.assertEqual(frame.selected_rows, {rows.index(option_rows[1]), conclusion})

    def test_narrow_45x14_pane_keeps_all_four_blocks_whole(self):
        wrapups = [
            (event(point="wrapup", options={"close_and_clean": "清理", "close_keep": "留工作区", "keep": "保留"},
                   probabilities={"close_and_clean": 0.03, "close_keep": 0.67, "keep": 0.3},
                   confidence=0.52, threshold=0.8, disposition="handback"), "交回工程经理"),
            (event(point="wrapup", options={"close_and_clean": "清理", "close_keep": "留工作区", "keep": "保留"},
                   probabilities={"close_and_clean": 0.01, "close_keep": 0.89, "keep": 0.1},
                   confidence=0.83, threshold=0.8), "建议"),
        ]
        for sample, destination in wrapups:
            sample["jev_choice"] = "close_keep"
            sample["task_title"] = "Agent code-review 已报告完成。"
            for width, height in ((45, 14), (60, 16)):
                with self.subTest(destination=destination, size=(width, height)), \
                        tempfile.TemporaryDirectory() as directory:
                    log = Path(directory) / "events.jsonl"
                    log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
                    frame = View(log, replay=True, width=width, height=height).step(3.0)
                    rows = [row.strip() for row in frame.text.splitlines() if row.strip()]
                    self.assertTrue(rows[0].startswith("收尾 · code-review"))
                    self.assertNotIn("已报告完成", frame.text)
                    self.assertIn("▸ 关窗格留工作区", frame.text)
                    self.assertIn("保留窗格", frame.text)
                    # Options under 5% and raw English ids are not shown.
                    self.assertNotIn("关窗格并清理", frame.text)
                    self.assertNotIn("close_keep", frame.text)
                    self.assertTrue(rows[-2].startswith("把握 0."))
                    self.assertTrue(rows[-2].endswith("线 0.80"))
                    self.assertEqual(rows[-1], "→ " + destination)
                    self.assertNotIn("…", frame.text)
                    self.assertEqual(frame.overflow_errors(), [])

    def test_long_task_name_is_cut_between_words(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            sample = event(point="skill")
            sample["task_title"] = "review the dispatcher handoff for herdr-jev wrapup"
            log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
            frame = View(log, width=45, height=14).step(3.0)
            head = next(row for row in frame.text.splitlines() if row.strip())
            self.assertTrue(head.endswith("…"))
            last_word = head[:-1].split()[-1]
            self.assertIn(last_word, sample["task_title"].split())
            self.assertEqual(frame.overflow_errors(), [])

    def test_option_names_shorten_in_the_middle_and_canvas_never_overflows(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            options = {"executor-with-an-extremely-long-model-name-and-high": "one",
                       "second-executor-with-an-extremely-long-model-name": "two"}
            log.write_text(json.dumps(event(options=options,
                                            probabilities={key: 0.5 for key in options}), ensure_ascii=False) + "\n")
            frame = View(log, replay=True, width=82, height=16).step(2.0)
            self.assertIn("…", frame.text)
            self.assertEqual(frame.overflow_errors(), [])

    def test_watching_waits_for_complete_lines_and_rebuilds_on_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            sample = json.dumps(event(), ensure_ascii=False)
            log.write_text(sample[:20])
            source = EventSource(log)
            self.assertEqual(source.poll().events, [])
            with log.open("a") as stream:
                stream.write(sample[20:] + "\n")
            self.assertEqual(len(source.poll().events), 1)
            with log.open("a") as stream:
                stream.write('{"broken":}\n')
            self.assertEqual(source.poll().bad_lines, 1)
            replacement = log.with_suffix(".new")
            replacement.write_text(json.dumps(event(point="wrapup"), ensure_ascii=False) + "\n")
            replacement.replace(log)
            batch = source.poll()
            self.assertTrue(batch.rebuild)
            self.assertEqual(batch.events[-1]["point"], "wrapup")

    def test_real_legacy_fixture_renders_the_latest_decision(self):
        fixture = Path(__file__).parent / "fixtures/jev-decisions-real.jsonl"
        frame = View(fixture, replay=True, width=60).step(2.0)
        self.assertIn("收尾", frame.text)
        self.assertIn("交回", frame.text)
        self.assertNotIn("把握历史", frame.text)
        self.assertEqual(frame.overflow_errors(), [])

    def test_new_decision_bar_animates_over_about_one_second(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            # Dispatch decisions open with the slot machine; skill decisions show the bars directly.
            sample = event(point="skill", options={"甲": "one", "乙": "two"}, probabilities={"甲": 1, "乙": 0})
            log.write_text("")
            view = View(log, width=82, height=17)
            view.step(0.0)
            with log.open("a") as stream:
                stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
            start = view.step(0.0)
            self.assertEqual(start.overflow_errors(), [])
            self.assertIn("JEV 推理中", start.text)
            view.step(0.7)
            middle = view.step(0.5)
            self.assertEqual(middle.overflow_errors(), [])
            end = view.step(0.5)
            self.assertEqual(end.overflow_errors(), [])
            self.assertLess(start.bar_lengths[0], middle.bar_lengths[0])
            self.assertLess(middle.bar_lengths[0], end.bar_lengths[0])

    def test_live_arrival_shows_spinner_then_fills_probability_and_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            log.write_text("")
            view = View(log, width=82, height=17)
            view.step(0.0)
            sample = event(point="skill", options={"甲": "代码任务", "乙": "视觉任务"},
                           probabilities={"甲": 0.9, "乙": 0.1}, confidence=0.86)
            with log.open("a") as stream:
                stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
            thinking = view.step(0.05)
            self.assertIn("JEV 推理中", thinking.text)
            self.assertGreater(thinking.bar_lengths[0], 0)
            settled = view.step(1.6)
            self.assertIn("→ 直接执行", settled.text)
            self.assertIn("0.86", settled.text)
            self.assertIn("90%", settled.text)
            self.assertEqual(settled.overflow_errors(), [])

    def test_truecolor_runs_include_a_gradient_for_selected_probability(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            sample = event(options={"甲": "代码任务", "乙": "视觉任务"},
                           probabilities={"甲": 0.9, "乙": 0.1})
            log.write_text(json.dumps(sample, ensure_ascii=False) + "\n")
            frame = View(log, replay=True, width=82, height=17).step(1.0)
            selected_row = next(i for i, row in enumerate(frame.text.splitlines()) if row.startswith("▸"))
            self.assertIn(selected_row, frame.selected_rows)
            colors = [color for _, _, color in frame.color_runs[selected_row]]
            self.assertGreater(len(set(colors)), 1)
            self.assertIn("\x1b[38;2;", _ansi(frame))
            # The chosen option is marked by ▸ and its gradient bar, not by reverse video.
            self.assertNotIn("\x1b[1;7m", _ansi(frame))

if __name__ == "__main__":
    unittest.main()
