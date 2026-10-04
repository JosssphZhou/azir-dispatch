"""看板按原型布局：执行者空位、今天的历史判断、连线和最小尺寸。"""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from azir_dispatch.dashboard import Board, main


def iso(delta=timedelta()):
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def decision(answer, ts, point="dispatch", disposition="apply", confidence=0.9):
    return {"type": "decision", "ts": ts, "point": point, "answer": answer, "jev_choice": answer,
            "confidence": confidence, "source": "jev", "disposition": disposition}


def write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


class BoardLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / "events.jsonl"
        self.log.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def board(self, size=(85, 47), **kwargs):
        board = Board(log=self.log, advisor_dir=None, **kwargs)
        board.resize(*size)
        return board

    def test_empty_board_draws_five_idle_slots_and_return_box(self):
        frame = self.board().step(0)
        self.assertEqual(frame.text.count("空闲"), 5)
        for word in ("6.1 Sol", "Luna", "Gemini", "Grok", "Opus", "回到工程经理", "等待判断", "主会话  ·  JEV"):
            self.assertIn(word, frame.text)
        self.assertEqual(frame.canvas.border_errors(), [])

    def test_today_history_fills_jev_and_records_without_phantom_workers(self):
        write(self.root / "演示" / "旧记录" / "events-old.jsonl", [
            decision("昨天的选择", iso(timedelta(days=-1))),
            decision("close_keep", iso(timedelta(minutes=-30)), point="wrapup",
                     disposition="handback", confidence=0.57),
            {"type": "dispatch", "ts": iso(timedelta(minutes=-20)), "target": "旧任务",
             "actual": {"executor": "codex-cli", "model": "gpt-6.1-sol", "effort": "high"}},
            decision("codex-cli:gpt-6-luna:medium", iso(timedelta(minutes=-10))),
        ])
        frame = self.board().step(0)
        self.assertIn("close_keep 关窗格、留工作区", frame.text)
        self.assertIn("Luna", frame.text)
        self.assertNotIn("昨天的选择", frame.text)
        self.assertNotIn("等待判断", frame.text)
        self.assertIn("旧任务 → Codex GPT-6.1 Sol", frame.text)
        self.assertEqual(frame.executors, 0)
        self.assertEqual(frame.text.count("空闲"), 5)
        newest = frame.text.index("❂ Luna")
        self.assertLess(newest, frame.text.index("close_keep 关窗格"))

    def test_history_comes_back_after_log_is_replaced(self):
        write(self.root / "演示" / "旧记录" / "events-old.jsonl",
              [decision("历史选择", iso(timedelta(minutes=-5)))])
        write(self.log, [decision("新选择", iso())])
        board = self.board()
        frame = board.step(0)
        self.assertIn("历史选择", frame.text)
        self.assertIn("新选择", frame.text)

    def test_advisor_record_is_chinese(self):
        write(self.log, [{"type": "advisor", "id": "a", "session": "s", "ts": iso()}])
        frame = self.board().step(0)
        self.assertIn("问顾问", frame.text)
        self.assertNotIn("advisor", frame.text)

    def test_dispatch_ball_moves_along_connector_to_its_card(self):
        board = self.board()
        board.step(0)
        write(self.log, [{"type": "dispatch", "ts": iso(), "target": "任务甲",
                          "actual": {"executor": "codex-cli", "model": "gpt-6.1-sol", "effort": "high"}}])
        frame = board.step(0.1)
        self.assertEqual(frame.packet, "jev_sub")
        layout = board._layout()
        path = board._path("jev_sub", "任务甲")
        self.assertEqual(path[0], (layout["jcx"], layout["jev_row"]))
        self.assertEqual(path[-1], (layout["card_cx"][0], layout["split_row"]))
        self.assertEqual(path[-1][1] + 1, layout["cards"][0][1])
        frame = board.step(0.35)
        self.assertTrue(any(frame.canvas.cells[y][x][0] == "●" for x, y in path))

    def test_worker_takes_the_card_of_its_model(self):
        write(self.log, [{"type": "dispatch", "target": "写文案", "executor": "pi", "model": "gemini-3.7-flash"},
                         {"type": "dispatch", "target": "查资讯", "executor": "grok", "model": "grok-build"}])
        rows = self.board().step(0).text.splitlines()
        card_row = next(row for row in rows if "写文案" in row)
        idle_row = next(row for row in rows if "Luna" in row)
        # Gemini and Grok are the third and fourth cards, after the idle 6.1 Sol and Luna cards.
        self.assertLess(idle_row.index("Luna"), card_row.index("写文案"))
        self.assertLess(card_row.index("写文案"), card_row.index("查资讯"))
        self.assertIn("✻ Opus", idle_row)

    def test_finished_worker_keeps_its_card_position(self):
        write(self.log, [{"type": "dispatch", "target": name, "executor": "codex", "model": "m"}
                         for name in ("甲任务", "乙任务")] + [{"type": "done", "target": "甲任务"}])
        board = self.board()
        frame = board.step(0)
        self.assertLess(frame.text.index("甲任务"), frame.text.index("乙任务"))
        self.assertIn("完成", frame.text)

    def test_supported_and_small_sizes_do_not_crash(self):
        write(self.log, [decision("选择甲", iso())])
        for size in ((80, 36), (85, 47), (120, 40), (165, 60)):
            with self.subTest(size=size):
                frame = self.board(size).step(0)
                self.assertEqual(frame.canvas.border_errors(), [])
                self.assertEqual(frame.canvas.overflow_errors(), [])
                self.assertIn("回到工程经理", frame.text)
        for size in ((60, 20), (79, 35), (10, 3)):
            with self.subTest(size=size):
                frame = self.board(size).step(0)
                self.assertIn("终端太小", frame.text)
                self.assertEqual(frame.canvas.overflow_errors(), [])

    def test_once_prints_a_single_frame_of_the_requested_height(self):
        write(self.log, [decision("选择甲", iso())])
        output = StringIO()
        with redirect_stdout(output):
            result = main(["--log", str(self.log), "--no-advisor-scan", "--once",
                           "--size", "85x47", "--seconds", "0"])
        self.assertEqual(result, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 47)
        self.assertIn("\x1b[0;", lines[0])
        self.assertIn("选择甲", output.getvalue())


if __name__ == "__main__":
    unittest.main()
