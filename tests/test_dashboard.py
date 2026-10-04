import tempfile
import unittest
from pathlib import Path
from datetime import datetime
import json
import os
from datetime import timezone

from azir_dispatch.dashboard import Board, Canvas, CYAN, GO, GRAY, HAND, JEV, RULE, main
from dashboard_terminal import Screen
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch


FIXTURE = Path(__file__).parent / "fixtures" / "events-sample.jsonl"
REAL_FIXTURE = Path(__file__).parent / "fixtures" / "dashboard-real-sample.jsonl"


class DashboardTests(unittest.TestCase):
    def assert_terminal_frame(self, frame):
        screen = Screen()
        # A PTY with ONLCR converts literal LF to CR/LF before display.
        screen.feed(frame.canvas.render(25, 2).replace("\n", "\r\n"))
        self.assertEqual(screen.outside(25, 2, frame.canvas.width, frame.canvas.height), [])
        self.assertEqual(frame.canvas.border_errors(), [])
        for x0, y0, x1, y1 in frame.canvas.boxes:
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    if x in (x0, x1) or y in (y0, y1):
                        self.assertEqual(screen.cells[y + 2][x + 25], frame.canvas.cells[y][x][0])

    def test_multiline_question_cannot_escape_centered_canvas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(json.dumps({'type': 'decision', 'point': 'dispatch',
                'question': '短问题\n这是背景', 'answer': 'codex:model:high'}) + '\n')
            frame = Board(log=path, advisor_dir=None).step(0)
            self.assert_terminal_frame(frame)
            self.assertNotIn('这是背景', frame.text)

    def test_long_mixed_fields_and_controls_keep_every_frame_in_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            long = ('中文English' * 23)[:200]
            background = '背景说明全文不可显示'
            question = ('短问题\n' + background + long)[:200]
            target = (long[:80] + '\n' + background + long)[:200]
            events = [
                {'type': 'decision', 'point': 'dispatch', 'question': question,
                 'state_preview': (background + long)[:200], 'answer': long, 'confidence': 0.8},
                {'type': 'dispatch', 'target': target, 'executor': long + '\rSECRET',
                 'model': long + '\tSECRET', 'effort': long},
                {'type': 'handback', 'reason': '短原因\n' + background},
                {'type': 'done', 'target': target},
            ]
            for field in ('question', 'state_preview'):
                self.assertEqual(len(events[0][field]), 200)
            self.assertEqual(len(target), 200)
            path.write_text(''.join(json.dumps(e) + '\n' for e in events))
            board = Board(log=path, advisor_dir=None, replay=True,
                          title=long, main_title=long, main_subtitle=long)
            for i in range(300):
                with self.subTest(frame=i):
                    frame = board.step(i / 30)
                    self.assert_terminal_frame(frame)
                    self.assertNotIn(background, frame.text)
                    self.assertNotIn('SECRET', frame.text)

    def test_canvas_clips_centered_and_inside_text_to_box(self):
        canvas = Canvas()
        canvas.box(20, 3, 40, 8, (255, 255, 255))
        canvas.center(30, 4, '中文English' * 30)
        canvas.inside(20, 40, -100, 5, '中文English' * 30)
        self.assertEqual(canvas.border_errors(), [])
        for y in (4, 5):
            self.assertTrue(all(c[0] == ' ' for c in canvas.cells[y][:20]))
            self.assertTrue(all(c[0] == ' ' for c in canvas.cells[y][41:]))

    def test_control_characters_and_combining_marks_cannot_move_cursor(self):
        canvas = Canvas()
        canvas.put(2, 40, '中文\tA\x1b[1;1H\bB\x00C\r背景')
        canvas.put(2, 41, 'e\u0301q\u0301中文')
        screen = Screen()
        screen.feed(canvas.render(25, 2).replace('\n', '\r\n'))
        self.assertEqual(screen.outside(), [])
        self.assertEqual(canvas.overflow_errors(), [])
        self.assertNotIn('背景', canvas.text())
        self.assertNotIn('\x1b', canvas.text())

    def test_check_rejects_output_that_would_escape_canvas(self):
        board = Board(log=FIXTURE, advisor_dir=None, replay=True)
        frame = board.step(0)
        frame.canvas.cells[40][10][0] = '\n'
        output = StringIO()
        with patch('azir_dispatch.dashboard.Board') as mocked, redirect_stdout(output):
            mocked.return_value.step.return_value = frame
            result = main(['--check', '--seconds', '0.01', '--no-advisor-scan'])
        self.assertEqual(result, 1)
        self.assertIn('画布外', output.getvalue())

    def test_check_replays_both_fixtures_at_supported_sizes(self):
        for fixture in (FIXTURE, REAL_FIXTURE):
            for size in ("80x36", "88x45", "90x46", "165x47"):
                with self.subTest(fixture=fixture.name, size=size):
                    output = StringIO()
                    args = ["--replay", str(fixture), "--check", "--size", size,
                            "--seconds", "0.1", "--no-advisor-scan"]
                    if fixture == REAL_FIXTURE:
                        args.extend(("--speed", "100"))
                        args[args.index("--seconds") + 1] = "0.7"
                    with redirect_stdout(output):
                        result = main(args)
                    self.assertEqual(result, 0, output.getvalue())
                    self.assertIn("框线都完整，画布外没有字符", output.getvalue())

    def test_resize_redraws_all_cells_without_old_layout_content(self):
        board = Board(log=FIXTURE, advisor_dir=None, replay=True)
        for width, height in ((165, 47), (80, 36), (88, 45), (90, 46)):
            with self.subTest(size=(width, height)):
                board.resize(width, height)
                frame = board.step(0)
                self.assertEqual(len(frame.canvas.cells), height)
                self.assertTrue(all(len(row) == width for row in frame.canvas.cells))
                self.assertEqual(frame.canvas.border_errors(), [])
                self.assertEqual(frame.canvas.overflow_errors(), [])
                # 最小尺寸也按原型画出底部「回到主会话」框和退出提示
                self.assertIn("审查  ·  验证", frame.text)
                self.assertIn("q 退出", frame.text)

    def test_check_rejects_terminal_below_minimum_size(self):
        output = StringIO()
        with redirect_stdout(output):
            result = main(["--check", "--size", "79x36", "--no-advisor-scan"])
        self.assertEqual(result, 1)
        self.assertIn("最小 80×36", output.getvalue())

    def test_decision_sweeps_jev_border_then_flashes_on_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Board(log=Path(directory) / "missing.jsonl", advisor_dir=None, replay=True)
            board._apply({"type": "decision", "point": "dispatch", "answer": "codex",
                          "confidence": 0.9}, 0)
            board._draw(0.25)
            x0, y0, x1, _ = board._layout()["jev"]
            x = (x0 + x1) // 2
            sweep_color = board.canvas.cells[y0][x][1]
            self.assertGreater(sum(sweep_color), sum(JEV))
            board.step(0.51)
            arrival_color = board.canvas.cells[y0][x][1]
            self.assertGreater(sum(arrival_color), sum(JEV))
            board.step(0.82)
            self.assertEqual(board.canvas.cells[y0][x][1], JEV)

    def test_packet_trail_uses_three_gradually_dimmer_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Board(log=Path(directory) / "missing.jsonl", advisor_dir=None)
            board.workers = {
                name: {"event": {"target": name}, "status": "运行中", "expires": None}
                for name in ("甲", "乙", "丙")
            }
            board.packet = "sub_ret"
            board.packet_target = "甲"
            board.packet_started = 0
            board._draw(0.25)
            path = board._path("sub_ret", "甲")
            progress = 0.5
            head = int(progress * (len(path) - 1))
            colors = [board.canvas.cells[path[head - k][1]][path[head - k][0]][1]
                      for k in range(4)]
            brightness = [sum(color) for color in colors]
            self.assertGreater(brightness[0], brightness[1])
            self.assertGreater(brightness[1], brightness[2])
            self.assertGreater(brightness[2], brightness[3])

    def test_worker_skill_fits_without_displacing_executor_model_or_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dispatch.jsonl'
            event = {'type': 'dispatch', 'target': 'worker', 'executor': 'codex',
                     'model': 'model-a', 'effort': 'high', 'skill': 'tdd'}
            for skill, visible in (('tdd', True), ('skill-name-too-long-for-worker-box', False)):
                with self.subTest(skill=skill):
                    path.write_text(json.dumps(dict(event, skill=skill)) + '\n')
                    frame = Board(log=path, advisor_dir=None, replay=True).step(0)
                    for text in ('codex', 'model-a', 'high', '运行中'):
                        self.assertIn(text, frame.text)
                    self.assertEqual(skill in frame.text, visible)
                    self.assertEqual(frame.canvas.border_errors(), [])

    def test_skill_label_answer_confidence_and_destination_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'skill.jsonl'
            for disposition, confidence, source, destination in (
                    ('apply', 0.9, 'jev', '直接执行'),
                    ('handback', 0.4, 'jev', '交回'),
                    ('apply', None, 'default', 'JEV 不可用，走默认')):
                with self.subTest(disposition=disposition, source=source):
                    path.write_text(json.dumps({'type': 'decision', 'point': 'skill',
                        'answer': None if disposition == 'handback' else 'tdd',
                        'jev_choice': 'tdd', 'confidence': confidence,
                        'source': source, 'disposition': disposition}) + '\n')
                    frame = Board(log=path, advisor_dir=None, replay=True).step(0)
                    self.assertIn('选技能', frame.text)
                    row = next(row for row in frame.text.splitlines() if 'tdd' in row)
                    self.assertIn(destination, row)
                    self.assertIn(f'{confidence or 0:.2f}', row)
                    self.assertEqual(frame.canvas.border_errors(), [])

    def test_four_decision_points_show_their_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'decision.jsonl'
            for point, applied_destination in (
                    ('dispatch', '直接执行'), ('skill', '直接执行'),
                    ('next_step', '建议'), ('wrapup', '建议')):
                for disposition, confidence, source, destination in (
                        ('apply', 0.9, 'jev', applied_destination),
                        ('handback', 0.4, 'jev', '交回'),
                        ('apply', None, 'default', 'JEV 不可用，走默认')):
                    with self.subTest(point=point, disposition=disposition, source=source):
                        event = {'type': 'decision', 'point': point,
                                 'answer': None if disposition == 'handback' else 'selected',
                                 'jev_choice': 'selected', 'confidence': confidence,
                                 'threshold': 0.7, 'source': source, 'disposition': disposition}
                        path.write_text(json.dumps(event) + '\n', encoding='utf-8')
                        frame = Board(log=path, advisor_dir=None, replay=True).step(0)
                        row = next(row for row in frame.text.splitlines() if 'selected' in row)
                        self.assertIn(destination, row)
                        if destination == '建议':
                            self.assertNotIn('直接执行', row)
                        self.assert_terminal_frame(frame)

    def test_rules_decisions_show_rules_destination_in_jev_box_and_record(self):
        # Written by `azir-dispatch decide` with no OpenRouter key: source=rules, disposition=apply.
        path = FIXTURE.with_name("rules-decisions.jsonl")
        for size in ((80, 36), (100, 52)):
            with self.subTest(size=size):
                board = Board(log=path, advisor_dir=None, replay=True)
                board.resize(*size)
                for tick in range(50):
                    frame = board.step(tick / 10)
                rows = frame.text.splitlines()
                jev_rows = [row for row in rows if "━" in row]
                self.assertEqual(len(jev_rows), 3)
                for row in jev_rows:
                    self.assertIn("规则", row)
                    for other in ("直接执行", "建议", "交回", "走默认"):
                        self.assertNotIn(other, row)
                record = rows[next(i for i, row in enumerate(rows) if "会话记录" in row) + 1:]
                self.assertIn("判断  收尾  keep 保留窗格  0.00  规则", "\n".join(record))
                colors = {cell[1] for row in board.canvas.cells for cell in row if cell[0] == "规"}
                self.assertIn(RULE, colors)
                self.assertNotIn(RULE, (GO, HAND, CYAN, GRAY))
                self.assertEqual(frame.canvas.border_errors(), [])

    def test_real_core_dispatch_answer_is_visible(self):
        path = FIXTURE.with_name("core-dispatch.jsonl")
        event = json.loads(path.read_text(encoding="utf-8"))
        frame = Board(log=path, advisor_dir=None, replay=True).step(0)
        self.assertIn("model-a", frame.text)  # 看板显示短名
        self.assertIn("选执行者", frame.text)
        self.assertIn("JEV 不可用，走默认", frame.text)
        self.assertNotIn(event["question"], frame.text)
        self.assertEqual(frame.canvas.border_errors(), [])

    def test_long_handback_suggestion_keeps_executor_and_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            event = {"type": "decision", "point": "dispatch", "question": "Long core question",
                     "answer": None, "jev_choice": "codex:model-with-a-very-long-configured-name:high",
                     "confidence": 0.42, "source": "jev", "disposition": "handback"}
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            frame = Board(log=path, advisor_dir=None, replay=True).step(0)
            row = next(row for row in frame.text.splitlines() if "0.42" in row)
            for word in ("model-with", "0.42", "交回"):
                self.assertIn(word, row)
            self.assertEqual(frame.canvas.border_errors(), [])

    def test_four_decision_labels_and_answers_stay_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            events = [{"type": "decision", "point": point, "question": "Long original question",
                       "answer": "selected-" + str(i), "confidence": 0.8,
                       "source": "jev", "disposition": "apply"}
                      for i, point in enumerate(("dispatch", "next_step", "wrapup", "custom-point"))]
            path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
            board = Board(log=path, advisor_dir=None, replay=True)
            for i in range(4):
                frame = board.step(i * 0.6)
                self.assertEqual(frame.canvas.border_errors(), [])
            for word in ("选执行者", "下一步", "收尾", "custom-point", "selected-0", "selected-1", "selected-2", "selected-3"):
                self.assertIn(word, frame.text)

    def test_file_ready_before_first_poll_is_new_when_start_was_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            for exists in (False, True):
                path = Path(tmp) / f"events-{exists}.jsonl"
                if exists:
                    path.write_text("", encoding="utf-8")
                board = Board(log=path, advisor_dir=None)
                event = {"type": "decision", "question": "新判断", "answer": "继续",
                         "confidence": 0.8, "source": "jev", "disposition": "apply"}
                path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
                self.assertEqual(board.step(0).packet, "main_jev")

    def test_first_file_created_after_start_animates_decision_and_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            board = Board(log=path, advisor_dir=None)
            self.assertIsNone(board.step(0).packet)
            decision = {"type": "decision", "question": "选执行者", "answer": "执行器甲",
                        "confidence": 0.9, "source": "jev", "disposition": "apply"}
            path.write_text(json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(board.step(1).packet, "main_jev")
            dispatch = {"type": "dispatch", "target": "任务甲", "executor": "执行器甲", "model": "型号甲"}
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(dispatch, ensure_ascii=False) + "\n")
            frame = board.step(1.6)
            self.assertEqual(frame.packet, "jev_sub")
            self.assertEqual(frame.executors, 1)

    def test_decision_then_dispatch_appear_in_order(self):
        board = Board(log=FIXTURE, advisor_dir=None, replay=True, speed=1)
        first = board.step(0.0)
        self.assertIn("选执行者", first.text)
        self.assertIn("甲", first.text)
        self.assertIn("直接执行", first.text)
        self.assertEqual(first.packet, "main_jev")
        self.assertEqual(first.executors, 0)
        second = board.step(1.0)
        self.assertEqual(second.packet, "jev_sub")
        self.assertEqual(second.executors, 1)
        self.assertIn("任务甲", second.text)
        self.assertIn("执行器甲", second.text)
        self.assertIn("型号甲", second.text)

    def test_full_replay_matches_role_activity_and_retention(self):
        board = Board(log=FIXTURE, advisor_dir=None, replay=True, retention=2)
        frames = [board.step(float(second)) for second in range(8)]
        self.assertEqual([f.packet for f in frames],
                         ["main_jev", "jev_sub", None, "sub_ret", "jev_main", "jev_main", "jev_sub", "sub_ret"])
        self.assertTrue(frames[2].advisor_active)
        self.assertEqual(frames[2].advisor_calls, 1)
        self.assertIn("失败", frames[3].text)
        self.assertIn("下一步", frames[4].text)
        self.assertIn("交回", frames[4].text)
        self.assertIn("完成", frames[7].text)
        self.assertEqual(board.step(9.1).executors, 0)
        self.assertFalse(board.step(25).advisor_active)

    def test_missing_empty_bad_and_half_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            board = Board(log=path, advisor_dir=None)
            self.assertEqual(board.step(0).executors, 0)
            path.write_text("", encoding="utf-8")
            self.assertEqual(board.step(1).executors, 0)
            event = {"type": "dispatch", "target": "增量任务", "executor": "执行器", "model": "型号"}
            with path.open("a", encoding="utf-8") as out:
                out.write("bad line\n" + json.dumps(event, ensure_ascii=False)[:-2])
            self.assertEqual(board.step(2).executors, 0)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(event, ensure_ascii=False)[-2:] + "\n")
            self.assertEqual(board.step(3).executors, 1)
            self.assertIn("跳过 1 行坏记录", board.step(4).text)

    def test_fast_events_queue_and_worker_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "many.jsonl"
            records = [dict(type="dispatch", target=f"任务{i}", executor=f"执行器{i}", model="型号",
                            ts="2026-10-01T12:00:00+08:00") for i in range(7)]
            path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in records), encoding="utf-8")
            board = Board(log=path, replay=True)
            frames = [board.step(i * 0.6) for i in range(7)]
            self.assertEqual([f.executors for f in frames], [1, 2, 3, 4, 5, 6, 7])
            self.assertIn("还有 2 个", frames[-1].text)  # five cards
            self.assertIn("任务6", frames[-1].text)

    def test_advisor_session_scan_counts_only_real_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "sessions"
            directory.mkdir()
            now = datetime.now().astimezone().isoformat()
            records = [dict(type="server_tool_use", name="advisor", id="tool-1", timestamp=now,
                            cwd="sample-project", sessionId="sample-session"),
                       dict(type="server_tool_use", name="other", id="tool-2", timestamp=now)]
            (directory / "sample.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            board = Board(log=Path(tmp) / "missing.jsonl", advisor_dir=directory)
            frame = board.step(0)
            self.assertEqual(frame.advisor_calls, 1)
            self.assertFalse(frame.advisor_active)
            records[0]["id"] = "tool-3"
            with (directory / "sample.jsonl").open("a") as out:
                out.write(json.dumps(records[0]) + "\n")
            active = board.step(1)
            self.assertEqual(active.advisor_calls, 2)
            self.assertTrue(active.advisor_active)
            self.assertIn("最近一次", active.text)
            self.assertFalse(board.step(26).advisor_active)
            self.assertEqual(board.step(26).advisor_calls, 2)

    def test_default_route_and_configurable_titles_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "default.jsonl"
            path.write_text(json.dumps({"type": "decision", "question": "下一步", "answer": "继续",
                                        "confidence": 0.3, "source": "default", "disposition": "apply"}, ensure_ascii=False) + "\n", encoding="utf-8")
            frame = Board(log=path, replay=True, title="我的工作流", main_title="工程经理",
                          main_subtitle="背后还有主 Agent").step(0)
            for text in ("我的工作流", "工程经理", "背后还有主 Agent", "JEV 不可用，走默认"):
                self.assertIn(text, frame.text)

    def test_completed_hidden_worker_becomes_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hidden.jsonl"
            records = [{"type": "dispatch", "target": f"任务{i}", "executor": "执行器", "model": "型号"}
                       for i in range(5)] + [{"type": "done", "target": "任务0"}]
            path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in records), encoding="utf-8")
            board = Board(log=path, replay=True)
            frames = [board.step(i * 0.6) for i in range(6)]
            self.assertIn("任务0", frames[-1].text)
            self.assertIn("完成", frames[-1].text)

    def test_live_start_rebuilds_ten_workers_then_animates_only_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.jsonl"
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            records = [{"type": "dispatch", "ts": ts, "target": f"任务{i}",
                        "actual": {"executor": "执行器", "model": "型号", "effort": "high"}}
                       for i in range(10)]
            path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in records), encoding="utf-8")
            board = Board(log=path, advisor_dir=None)
            first = board.step(0)
            self.assertEqual(first.executors, 10)
            self.assertIsNone(first.packet)
            self.assertIn("还有 5 个", first.text)
            added = {"type": "decision", "ts": ts, "question": "选执行者", "jev_choice": "执行器",
                     "answer": "执行器", "confidence": 0.8, "source": "jev", "disposition": "apply"}
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(added, ensure_ascii=False) + "\n")
            second = board.step(1)
            self.assertEqual(second.packet, "main_jev")
            self.assertEqual(second.executors, 10)

    def test_actual_selection_and_changed_assignment_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "changed.jsonl"
            event = {"type": "dispatch", "target": "任务甲", "requested": {"executor": "甲"},
                     "suggested": {"executor": "甲", "model": "模型甲", "effort": "high"},
                     "actual": {"executor": "乙", "model": "模型乙", "effort": "low"}}
            path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
            frame = Board(log=path, replay=True).step(0)
            self.assertIn("乙", frame.text)
            self.assertIn("模型乙", frame.text)
            self.assertIn("low", frame.text)
            self.assertIn("已改派", frame.text)

    def test_log_and_session_record_same_advisor_call_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            (root / "events.jsonl").write_text(json.dumps({"type": "advisor", "id": "event-1",
                                                             "session": "session-1", "ts": ts}) + "\n")
            (sessions / "sample.jsonl").write_text(json.dumps({"type": "server_tool_use", "name": "advisor",
                                                                  "id": "tool-1", "sessionId": "session-1",
                                                                  "timestamp": ts}) + "\n")
            board = Board(log=root / "events.jsonl", advisor_dir=sessions)
            self.assertEqual(board.step(0).advisor_calls, 1)

    def test_replaced_file_rebuilds_without_recount_or_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            event = {"type": "advisor", "ts": ts, "session": "sample-session", "id": "one"}
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            board = Board(log=path, advisor_dir=None)
            self.assertEqual(board.step(0).advisor_calls, 1)
            replacement = Path(tmp) / "replacement.jsonl"
            replacement.write_text(json.dumps(event) + "\n", encoding="utf-8")
            os.replace(replacement, path)
            frame = board.step(1)
            self.assertEqual(frame.advisor_calls, 1)
            self.assertIsNone(frame.packet)
            self.assertFalse(frame.advisor_active)

    def test_truncated_file_rebuilds_recent_decision_without_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            first = {"type": "decision", "question": "旧问题", "answer": "旧答案",
                     "source": "jev", "disposition": "apply", "confidence": 0.8}
            path.write_text(json.dumps(first, ensure_ascii=False) + "\n" + " " * 100)
            board = Board(log=path, advisor_dir=None)
            self.assertIn("旧问题", board.step(0).text)
            second = {"type": "decision", "question": "新问题", "answer": "新答案",
                      "source": "jev", "disposition": "apply", "confidence": 0.9}
            path.write_text(json.dumps(second, ensure_ascii=False) + "\n")
            frame = board.step(1)
            self.assertIsNone(frame.packet)
            self.assertIn("新问题", frame.text)
            self.assertNotIn("旧问题", frame.text)

    def test_each_replay_frame_keeps_borders_and_no_prototype_data(self):
        board = Board(log=FIXTURE, replay=True)
        for i in range(300):
            frame = board.step(i / 30)
            self.assertEqual(frame.canvas.border_errors(), [], f"frame {i}")
            for word in ("which file", "src/auth.ts", "forks", "tokens", "FABLE", "Opus 5.5"):
                self.assertNotIn(word, frame.text)


if __name__ == "__main__":
    unittest.main()
