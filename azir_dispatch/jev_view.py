"""Compact, animated terminal view of the latest JEV decision."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import random
import re
import select
import shutil
import sys
import termios
import time
import tty
import unicodedata
import zlib

from .dashboard import EventSource, timestamp


LABELS = {"dispatch": "选执行者", "skill": "选技能", "next_step": "下一步", "wrapup": "收尾"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPARKS = "▁▂▃▄▅▆▇█"
REPLAY_CYCLE = 7.0
THINKING = 0.6
BAR = "▆"
TRACK, BAR_GRAY, GRAY_TEXT, WHITE = (44, 46, 58), (100, 106, 120), (150, 156, 166), (232, 234, 238)
# Short display names for dispatch candidates; keys look like "executor:model:effort".
MODEL_NAMES = (("6.1-sol", "6.1 Sol"), ("sol", "Sol"), ("luna", "Luna"), ("opus", "Opus"),
               ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable"),
               ("gemini", "Gemini"), ("grok", "Grok"))
OPTION_LABELS = {
    "close_and_clean": "关窗格并清理", "close_keep": "关窗格留工作区", "keep": "保留窗格",
    "continue": "按意见退修", "rethink": "重新设计", "stop": "交回",
}
PURPLE, PINK, GRAY = (180, 140, 255), (255, 122, 168), (93, 102, 114)
RULE = (120, 140, 250)
GREEN, ORANGE, DIM = (79, 209, 139), (242, 184, 75), (93, 102, 114)


def cell_width(value):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in str(value))


def _plain(value):
    text = unicodedata.normalize("NFC", str(value or ""))
    return "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)


def fit(value, width, middle=False):
    text = _plain(value)
    if cell_width(text) <= width:
        return text
    if width <= 0:
        return ""
    if width == 1:
        return "…"
    if not middle:
        out = ""
        for ch in text:
            if cell_width(out + ch) > width - 1:
                break
            out += ch
        return out + "…"
    left_width = (width - 1 + 1) // 2
    right_width = width - 1 - left_width
    left, right = "", ""
    for ch in text:
        if cell_width(left + ch) > left_width:
            break
        left += ch
    for ch in reversed(text):
        if cell_width(right + ch) > right_width:
            break
        right = ch + right
    return left + "…" + right


def _pad(value, width, middle=False):
    value = fit(value, width, middle)
    return value + " " * max(0, width - cell_width(value))


def wrap(value, width, limit):
    lines, line = [], ""
    for ch in _plain(value):
        if ch == "\n":
            lines.append(line.rstrip())
            line = ""
        elif cell_width(line + ch) <= width:
            line += ch
        else:
            lines.append(line.rstrip())
            line = ch.lstrip()
        if len(lines) >= limit:
            return lines[:limit]
    if line and len(lines) < limit:
        lines.append(line.rstrip())
    return lines[:limit]


def short_name(key):
    key = str(key)
    lowered = key.lower()
    if ":" in key:
        for needle, label in MODEL_NAMES:
            if needle in lowered:
                return label
        parts = [part for part in key.split(":") if part]
        return parts[1] if len(parts) > 1 else key
    return key


MIN_SHOWN = 0.05


def option_label(key):
    """Icon plus short model name, or the Chinese option name; never the raw English id."""
    from .agent_icons import icon

    key = str(key)
    if ":" in key:
        mark, _ = icon(key)
        return f"{mark} {short_name(key)}" if mark else short_name(key)
    return OPTION_LABELS.get(key, short_name(key))


def short_task(title):
    """Task name without the "Agent X 已报告完成。" sentence around it."""
    text = " ".join(_plain(title).split())
    match = re.match(r"^Agent\s+(\S+)\s+已报告完成", text)
    if match:
        return match.group(1)
    return text.rstrip("。.")


def fit_words(value, width):
    """Like fit, but an ASCII word is never cut in half: drop it whole instead."""
    text = _plain(value)
    if cell_width(text) <= width:
        return text
    cut = fit(text, width)[:-1]
    rest = text[len(cut):]
    if cut and rest and cut[-1].isascii() and cut[-1].isalnum() and rest[0].isascii() and rest[0].isalnum():
        trimmed = cut.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        cut = trimmed if trimmed.strip() else cut
    return cut.rstrip(" ·-_") + "…"


def short_condition(key, description):
    """At most a few characters of the option description, never a cut-off sentence."""
    if str(key) in OPTION_LABELS:
        return OPTION_LABELS[str(key)]
    text = _plain(description).strip()
    for mark in "：:，,。；;（(":
        text = text.split(mark, 1)[0]
    text = text.strip()
    # Long categories are cut at the display width instead of being dropped.
    return fit(text, 16) if text else ""


def seen_text(state):
    """First line of what JEV saw, cut before any redaction placeholder."""
    if state is None:
        return "无"
    text = " ".join(_plain(state).split())
    text = text.split("[REDACTED]", 1)[0].rstrip(" `（(：:，,、")
    return text or "无"


def _mix(start, end, position, length):
    t = 1.0 if length <= 1 else position / (length - 1)
    return tuple(round(start[i] + (end[i] - start[i]) * t) for i in range(3))


@dataclass
class Frame:
    text: str
    selected_rows: set[int]
    color_runs: dict[int, list[tuple[int, int, tuple[int, int, int]]]]
    bar_lengths: list[int]
    width: int
    height: int

    def overflow_errors(self):
        rows = self.text.splitlines()
        errors = [i for i, row in enumerate(rows) if cell_width(row) > self.width]
        errors.extend([len(rows)] * max(0, len(rows) - self.height))
        return errors


class View:
    def __init__(self, log=None, replay=False, width=82, height=17, config=None):
        from .jev_idle import load_dispatch

        self.dispatch_criteria = load_dispatch(config)
        self.source = EventSource(log or Path.home() / ".claude/state/azir-dispatch/events.jsonl", replay)
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.decisions = deque(maxlen=14)
        self.current = None
        self.elapsed = 0.0
        self.animation_started = None
        self.selected_rows = set()
        self.color_runs = {}
        self.bar_lengths = []
        self.bad_lines = 0
        self.frame_index = 0
        # live: a decision arrived while the view was running. Decisions that
        # were already in the log at start are replayed on a loop instead.
        self.live = False
        self.today = 0
        self.replay_origin = 0.0

    def resize(self, width, height):
        self.width, self.height = max(1, min(82, int(width))), max(1, int(height))

    def _accept(self, events, rebuild):
        decisions = [item for item in events if item.get("type") == "decision"]
        if rebuild:
            self.decisions.clear()
            self.current, self.live, self.today = None, False, 0
            self.replay_origin = self.elapsed
        today = datetime.now().date()
        for event in decisions:
            self.decisions.append(event)
            self.current = event
            ts = timestamp(event.get("ts"))
            if ts is not None and datetime.fromtimestamp(ts).date() == today:
                self.today += 1
            historical = rebuild or self.source.replay
            # Records already in the log open on the settled view; only the
            # replay loop and live arrivals play the slot machine.
            lead = 2.0 + (self._slot_seconds(event) if historical else 0.0)
            self.animation_started = self.elapsed - lead if historical else self.elapsed
            if not rebuild:
                self.live = True

    def step(self, elapsed=1 / 30):
        self.elapsed += max(0.0, float(elapsed))
        self.frame_index += 1
        batch = self.source.poll()
        self.bad_lines = batch.bad_lines
        self._accept(batch.events, batch.rebuild)
        return self.draw()

    def _destination(self, event):
        if event.get("source") == "default":
            return "JEV 不可用，走默认"
        if event.get("source") == "rules":
            return "→ 规则"
        if event.get("disposition") == "handback":
            return "→ 交回主会话"
        if event.get("point") in ("dispatch", "skill"):
            return "→ 直接执行"
        return "→ 建议"

    @staticmethod
    def _slot_seconds(event):
        from .jev_slot import SLOT_SECONDS

        return SLOT_SECONDS if event.get("point") == "dispatch" else 0.0

    def draw(self):
        if self.current is None:
            from .jev_idle import draw_standby
            return draw_standby(self)
        if not self.live:
            # Loop the latest recorded decision: slot machine for dispatch,
            # bars grow from zero, the confidence counts up, then it starts over.
            cycle = REPLAY_CYCLE + self._slot_seconds(self.current)
            self.animation_started = self.elapsed - ((self.elapsed - self.replay_origin) % cycle)
            return self._draw_decision(tag="回放")
        return self._draw_decision()

    def _stage(self, event):
        """Seconds into the bar animation; the slot machine replaces the thinking phase."""
        stage = max(0.0, self.elapsed - (self.animation_started or 0.0))
        slot = self._slot_seconds(event)
        if event.get("source") == "rules":
            return stage + THINKING  # JEV was never asked, so there is no thinking phase
        return stage - slot + THINKING if slot else stage

    def _draw_decision(self, tag=None):
        event = self.current
        slot = self._slot_seconds(event)
        if slot:
            from .jev_slot import draw_slot

            t = max(0.0, self.elapsed - (self.animation_started or 0.0))
            if t < slot:
                frame = draw_slot(self, event, t, tag)
                if frame is not None:
                    self.selected_rows, self.color_runs = frame.selected_rows, frame.color_runs
                    self.bar_lengths = []
                    return frame
        bar_lengths = []
        # Settled view: four blocks only, because the pane is often squeezed to
        # about 45x14. Head, option bars, confidence gauge, conclusion.
        width = self.width
        point = LABELS.get(event.get("point"), _plain(event.get("point") or "判断"))
        state = event.get("state_head")
        task_title = short_task(event.get("task_title") or (str(state).splitlines()[0] if state else ""))
        head_text = f"{point} · {task_title}" if task_title else point

        options = event.get("options")
        if isinstance(options, dict):
            pass
        elif isinstance(options, list):
            # Older decision records stored candidate names as an array and
            # did not include descriptions or probabilities.
            options = {str(name): "" for name in options}
        else:
            options = {}
        probabilities = event.get("probabilities")
        probabilities = probabilities if isinstance(probabilities, dict) else {}
        if not options:
            options = {str(event.get("jev_choice") or event.get("answer") or "无"): ""}

        def probability_of(name):
            value = probabilities.get(name)
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0):
                return min(1.0, float(value))
            return None

        # Rules and default decisions have no JEV choice; mark the answer that was applied.
        choice = str(event.get("jev_choice") or event.get("answer"))
        # Options under 5% are left out; the chosen one always stays.
        names = [str(name) for name in options
                 if name == choice or probability_of(str(name)) is None or probability_of(str(name)) >= MIN_SHOWN]
        names = names or [str(name) for name in options]

        from .agent_icons import icon

        entries = [(name, option_label(name)) for name in names]
        percent_width = 4
        name_width = max(4, min(14, max(cell_width(label) for _, label in entries)))
        bar_col = 2 + name_width + 1
        bar_width = max(0, width - bar_col - 1 - percent_width)
        if bar_width < 6:
            name_width = max(2, width - 2 - 1 - 6 - 1 - percent_width)
            bar_col = 2 + name_width + 1
            bar_width = max(0, width - bar_col - 1 - percent_width)

        stage = self._stage(event)
        thinking = stage < THINKING
        fill_progress = min(1.0, max(0.0, (stage - 0.6) / 1.0))
        fill_progress = 1 - (1 - fill_progress) ** 3
        rng = random.Random(zlib.crc32(str(event.get("id", "jev")).encode()) + self.frame_index)

        # Each block is a list of (text, runs, selected) rows.
        head_block = []
        # The replay tag only shows when the task name still fits beside it.
        if tag and cell_width(head_text) + cell_width(tag) + 2 <= width:
            head_width = width - cell_width(tag) - 2
            head = _pad(head_text, head_width)
            head_block.append((f"{head}  {tag}", [(0, head_width, PURPLE), (head_width + 2, width, PINK)], False))
        else:
            text = fit_words(head_text, width)
            head_block.append((text, [(0, width, PURPLE)], False))

        option_block = []
        for name, label in entries:
            fraction = probability_of(name)
            if thinking:
                length = rng.randint(1, max(1, round(bar_width * 0.6)))
            else:
                length = round(bar_width * fraction * fill_progress) if fraction is not None else 0
            length = min(length, bar_width)
            bar_lengths.append(length)
            picked = name == choice
            if thinking:
                percent = "···"
            elif fraction is None:
                percent = "无"
            else:
                percent = f"{round(fraction * 100)}%"
            text = (("▸ " if picked else "  ") + _pad(label, name_width, middle=True) + " "
                    + BAR * bar_width + " " + " " * max(0, percent_width - cell_width(percent)) + percent)
            mark, brand = icon(name) if ":" in name else ("", None)
            runs = ([(2, 3, brand)] if mark and label.startswith(mark) else []) + [
                (0, 2 + name_width, WHITE if picked else GRAY_TEXT)]
            for i in range(bar_width):
                if i >= length:
                    color = TRACK
                elif thinking:
                    color = (110, 90, 160)
                elif picked:
                    color = _mix(PURPLE, PINK, i, max(1, length))
                else:
                    color = BAR_GRAY
                runs.append((bar_col + i, bar_col + i + 1, color))
            runs.append((bar_col + bar_width + 1, width, WHITE if picked else GRAY_TEXT))
            option_block.append((text, runs, picked))

        confidence, threshold = event.get("confidence"), event.get("threshold")
        confidence_valid = (isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                            and math.isfinite(confidence) and 0 <= confidence <= 1)
        threshold_valid = (isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
                           and math.isfinite(threshold) and 0 <= threshold <= 1)
        passed = confidence_valid and threshold_valid and confidence >= threshold
        confidence_color = GREEN if passed else ORANGE
        count_progress = min(1.0, max(0.0, (stage - 0.6) / 0.8))
        count_progress = 1 - (1 - count_progress) ** 3
        shown = math.floor(float(confidence or 0) * count_progress * 100) / 100 if confidence_valid else None
        value_text = f"{shown:.2f}" if shown is not None else "—"
        line_text = f"线 {threshold:.2f}" if threshold_valid else "线 无"
        head = f"把握 {value_text:>4}  "
        gauge_col = cell_width(head)
        gauge_width = width - gauge_col - cell_width(line_text) - 2
        if gauge_width < 6:
            gauge_width = max(0, width - gauge_col)
            line_text = ""
        marker = (min(gauge_width - 1, round((gauge_width - 1) * threshold))
                  if threshold_valid and gauge_width else -1)
        gauge_fill = round(gauge_width * (float(confidence or 0) if confidence_valid else 0) * count_progress)
        gauge = "".join("┃" if index == marker else BAR for index in range(gauge_width))
        runs = [(0, 2, GRAY_TEXT), (3, gauge_col, confidence_color if shown is not None else GRAY_TEXT)]
        for index in range(gauge_width):
            color = PINK if index == marker else (confidence_color if index < gauge_fill else TRACK)
            runs.append((gauge_col + index, gauge_col + index + 1, color))
        if line_text:
            runs.append((gauge_col + gauge_width + 2, width, PINK))
        gauge_block = [(head + gauge + ("  " + line_text if line_text else ""), runs, False)]

        if thinking:
            spinner = SPINNER[int(stage * 15) % len(SPINNER)]
            status, status_color = f"{spinner}  JEV 推理中…", PURPLE
        else:
            status = self._destination(event)
            status_color = {"default": GRAY, "rules": RULE}.get(event.get("source"), confidence_color)
        status = fit_words(status, width)
        # Bold through selected_rows; the conclusion is the one line to read from afar.
        conclusion_block = [(status, [(0, width, status_color)], True)]

        blocks = [head_block, option_block, gauge_block, conclusion_block]
        content = sum(len(block) for block in blocks)
        spare = self.height - content
        gaps = [0, 0, 0]
        for index in (0, 1, 2, 1, 2, 0):
            if spare <= 0:
                break
            gaps[index] += 1
            spare -= 1
        option_gap = 1 if spare >= len(option_block) - 1 and len(option_block) > 1 else 0
        if option_gap:
            spare -= len(option_block) - 1
        top = max(0, spare) // 2  # leftover rows split above and below, so the blocks sit centred

        rows, selected, colors = [""] * top, set(), {}
        for block_index, block in enumerate(blocks):
            for row_index, (text, runs, bold) in enumerate(block):
                if block is option_block and row_index and option_gap:
                    rows.append("")
                rows.append(fit(text, width))
                if bold:
                    selected.add(len(rows) - 1)
                colors[len(rows) - 1] = runs
            if block_index < 3:
                rows.extend([""] * gaps[block_index])

        if len(rows) > self.height:
            rows = rows[:self.height]
            selected = {row for row in selected if row < self.height}
            colors = {row: runs for row, runs in colors.items() if row < self.height}
        self.selected_rows, self.color_runs = selected, colors
        self.bar_lengths = bar_lengths
        return Frame("\n".join(rows), selected, colors, bar_lengths, self.width, self.height)


def _rgb(rgb):
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _ansi(frame):
    output = []
    for row_index, row in enumerate(frame.text.splitlines()):
        runs = frame.color_runs.get(row_index, [])
        if row_index in frame.selected_rows:
            output.append("\x1b[1m")
        active = None
        col = 0
        for ch in row:
            rgb = next((color for start, end, color in runs if start <= col < end), None)
            if rgb != active:
                output.append(_rgb(rgb) if rgb else ("\x1b[39m" if active else ""))
                active = rgb
            output.append(ch)
            col += cell_width(ch)
        output.append("\x1b[0m")
        if row_index + 1 < len(frame.text.splitlines()):
            output.append("\n")
    return "".join(output)


def _args(argv=None):
    from .view_output import add_output_args

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=str(Path.home() / ".claude/state/azir-dispatch/events.jsonl"))
    parser.add_argument("--replay", metavar="记录")
    parser.add_argument("--check", action="store_true", help="检查回放帧没有超出终端宽高")
    parser.add_argument("--config", help="派发配置，默认 ~/projects/azir/config/azir-dispatch.toml")
    add_output_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    from .view_output import run_view, size_from_args

    args = _args(argv)
    replay = args.replay is not None
    path = args.replay or args.log
    width, height = size_from_args(args, (82, 17))
    view = View(path, replay=replay, width=min(82, width), height=height, config=args.config)
    if args.check:
        failures = []
        for _ in range(1 if replay else 2):
            failures.extend(view.step(1.0).overflow_errors())
        print(f"JEV view frame check: {len(failures)} overflow rows")
        return 1 if failures else 0
    return run_view(view, args)


if __name__ == "__main__":
    raise SystemExit(main())
