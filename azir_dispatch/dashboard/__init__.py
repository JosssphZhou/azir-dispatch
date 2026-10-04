"""Terminal board for dispatch records.

Board.step(seconds) is the deterministic frame boundary used by the terminal
runner and by consumers that want to inspect a rendered frame.
"""

from __future__ import annotations

import argparse
import colorsys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import sys
import termios
import time
import tomllib
import tty
import unicodedata

from ..agent_icons import icon as agent_icon
from .setup_state import ROLE_CARDS, STEPS, SetupState, default_path, executor_label, model_label

W, H, FPS = 90, 46, 30
BG = (0, 0, 0)
DIM = (92, 98, 110)
GRAY = (150, 156, 166)
WHITE = (232, 234, 238)
BRIGHT = (255, 255, 255)
ORANGE = (240, 150, 110)
BLUE = (125, 170, 240)
GREEN = (125, 225, 150)
PURPLE = (180, 160, 240)
# 原型配色（jev演示原型/index.html）
JEV = (180, 140, 255)
GO = (79, 209, 139)
HAND = (242, 184, 75)
CYAN = (86, 204, 224)
RULE = (120, 140, 250)
EXEC = (90, 176, 255)
ADV = (255, 122, 168)
MGR = (232, 145, 107)
TITLE = (205, 182, 255)
LINE = (58, 64, 76)
TRACK = (34, 38, 46)
EMPTY = (38, 43, 52)
EMPTY_TEXT = (64, 70, 82)
# One card per dispatch tier, in the order of the JEV standby list; needles match "executor:model".
EMPTY_SLOTS = (("6.1 Sol", "Codex", ("6.1-sol",)), ("Luna", "Codex", ("luna",)),
               ("Gemini", "pi", ("gemini",)), ("Grok", "Grok", ("grok",)),
               ("Opus", "Claude", ("opus", "claude")))


@dataclass(frozen=True)
class Slot:
    """One executor card: its idle label and detail, and the needles that claim a worker."""
    label: str
    detail: str
    needles: tuple
    short: str
    role: dict | None = None
    role_key: str | None = None


WRAPUP = {"close_keep": "关窗格、留工作区", "keep": "保留窗格",
          "close_and_clean": "关窗格并清理"}
HISTORY_TYPES = {"decision", "dispatch", "done", "failed", "handback"}


def pretty_executor(value):
    value = single_line(value)
    return {"codex-cli": "Codex", "claude-code": "Claude", "claude": "Claude"}.get(value, value)


def pretty_model(value):
    value = single_line(value)
    parts = value.split("-")
    if len(parts) >= 2 and parts[0].lower() in ("gpt", "claude"):
        return model_label(value)
    return value


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def cw(ch):
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def tw(s):
    return sum(cw(ch) for ch in str(s))


def single_line(value):
    """Keep the first line and prevent fields from issuing terminal controls."""
    lines = str(value or "").splitlines()
    text = unicodedata.normalize("NFC", lines[0] if lines else "")
    return "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)


def decision_label(event):
    point = single_line(event.get("point") or event.get("question"))
    return {"dispatch": "选执行者", "next_step": "下一步", "wrapup": "收尾",
            "skill": "选技能"}.get(point, point)


def fit(value, width):
    """Clip by terminal cells, with an ellipsis when space allows."""
    s = single_line(value)
    if tw(s) <= width:
        return s
    if width <= 0:
        return ""
    out = ""
    for ch in s:
        if tw(out) + cw(ch) > width - 1:
            break
        out += ch
    return out + "…"


def fit_middle(value, width):
    """Keep both the executor prefix and effort suffix of a long answer."""
    s = single_line(value)
    if tw(s) <= width:
        return s
    if width <= 1:
        return "…" if width == 1 else ""
    suffix = ""
    for ch in reversed(s):
        if tw(suffix) + cw(ch) > (width - 1) // 2:
            break
        suffix = ch + suffix
    prefix = ""
    for ch in s:
        if tw(prefix) + cw(ch) > width - 1 - tw(suffix):
            break
        prefix += ch
    return prefix + "…" + suffix


def short_name(key):
    # jev_view 反过来导入本模块，这里延迟导入避免循环
    from ..jev_view import short_name as _short
    return _short(key)


class Canvas:
    """True-colour fixed canvas adapted from the workflow prototype."""

    def __init__(self, width=W, height=H):
        self.width, self.height = width, height
        self.clear()

    def clear(self):
        self.cells = [[[" ", WHITE, BG, False] for _ in range(self.width)] for _ in range(self.height)]
        self.boxes = []

    def shift(self, dx, dy):
        if not dx and not dy:
            return
        cells = [[[' ', WHITE, BG, False] for _ in range(self.width)] for _ in range(self.height)]
        for y, row in enumerate(self.cells):
            for x, cell in enumerate(row):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    cells[ny][nx] = cell
        self.cells = cells
        self.boxes = [(x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                      for x0, y0, x1, y1 in self.boxes]

    def put(self, x, y, value, fg=WHITE, bold=False, bg=None):
        if not 0 <= y < self.height:
            return x
        previous = None
        for ch in single_line(value):
            width = cw(ch)
            if width == 0:
                if previous is not None:
                    self.cells[y][previous][0] += ch
                continue
            if 0 <= x and x + width <= self.width:
                self.cells[y][x] = [ch, fg, bg or self.cells[y][x][2], bold]
                previous = x
                if width == 2:
                    self.cells[y][x + 1] = ["", fg, bg or self.cells[y][x + 1][2], bold]
            x += width
        return x

    def center(self, cx, y, value, fg=WHITE, bold=False, max_width=None):
        left, right = 0, self.width
        for x0, y0, x1, y1 in self.boxes:
            if x0 < cx < x1 and y0 < y < y1:
                left, right = max(left, x0 + 1), min(right, x1)
        width = max(0, 2 * min(cx - left, right - cx))
        value = fit(value, min(width, max_width) if max_width is not None else width)
        return self.put(cx - tw(value) // 2, y, value, fg, bold)

    def inside(self, x0, x1, x, y, value, fg=WHITE, bold=False):
        x = max(0, x0 + 1, x)
        self.put(x, y, fit(value, max(0, min(self.width, x1) - x)), fg, bold)

    def box(self, x0, y0, x1, y1, fg):
        self.boxes.append((x0, y0, x1, y1))
        self.put(x0, y0, "┌" + "─" * (x1 - x0 - 1) + "┐", fg)
        for y in range(y0 + 1, y1):
            self.put(x0, y, "│", fg)
            self.put(x1, y, "│", fg)
        self.put(x0, y1, "└" + "─" * (x1 - x0 - 1) + "┘", fg)

    def text(self):
        return "\n".join("".join(cell[0] for cell in row) for row in self.cells)

    def render_lines(self):
        """One frame as plain SGR-coloured lines, for --once and screenshots."""
        lines = []
        for row in self.cells:
            out, last = [], None
            for ch, fg, bg, bold in row:
                if not ch:
                    continue
                key = fg, bg, bold
                if key != last:
                    out.append(f"\x1b[0;{'1;' if bold else ''}38;2;{fg[0]};{fg[1]};{fg[2]};48;2;{bg[0]};{bg[1]};{bg[2]}m")
                    last = key
                out.append(ch)
            lines.append("".join(out) + "\x1b[0m")
        return "\n".join(lines) + "\n"

    def render(self, ox=0, oy=0):
        out, last = [], None
        for y, row in enumerate(self.cells):
            out.append(f"\x1b[{oy + y + 1};{ox + 1}H")
            for ch, fg, bg, bold in row:
                if not ch:
                    continue
                key = fg, bg, bold
                if key != last:
                    out.append(f"\x1b[0;{'1;' if bold else ''}38;2;{fg[0]};{fg[1]};{fg[2]};48;2;{bg[0]};{bg[1]};{bg[2]}m")
                    last = key
                out.append(ch)
        return "".join(out)

    def border_errors(self):
        bad = []
        for x0, y0, x1, y1 in self.boxes:
            for x, y, ch in ((x0, y0, "┌"), (x1, y0, "┐"),
                             (x0, y1, "└"), (x1, y1, "┘")):
                if self.cells[y][x][0] != ch:
                    bad.append((x, y))
            for y in range(y0 + 1, y1):
                for x in (x0, x1):
                    if self.cells[y][x][0] != "│":
                        bad.append((x, y))
            for x in range(x0 + 1, x1):
                for y in (y0, y1):
                    if self.cells[y][x][0] != "─":
                        bad.append((x, y))
        return bad

    def overflow_errors(self):
        """Check the emitted row width and controls, including PTY CR/LF drift.

        render() positions each row at the canvas origin. Printable rows of
        exactly W cells cannot write outside that canvas in a larger terminal.
        """
        bad = []
        for y, row in enumerate(self.cells):
            text = "".join(cell[0] for cell in row)
            if tw(text) != self.width or any(unicodedata.category(ch).startswith("C") for ch in text):
                bad.append(y)
        return bad


def parse_line(line):
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None, True
    if not isinstance(event, dict):
        return None, True
    if event.get("type") not in {
        "decision", "dispatch", "done", "failed", "handback", "advisor"
    }:
        return None, False
    return event, False


def load_history(patterns, exclude=None):
    """Today's records from earlier logs, oldest first, for the board's history."""
    today = datetime.now().date()
    found = []
    for pattern in patterns or ():
        pattern = Path(str(pattern)).expanduser()
        for path in sorted(pattern.parent.glob(pattern.name)):
            if exclude is not None and path.resolve() == exclude:
                continue
            try:
                lines = path.read_bytes().split(b"\n")
            except OSError:
                continue
            for line in lines:
                event, _ = parse_line(line)
                if event is None or event.get("type") not in HISTORY_TYPES:
                    continue
                ts = timestamp(event.get("ts"))
                if ts is not None and datetime.fromtimestamp(ts).date() == today:
                    found.append((ts, event))
    found.sort(key=lambda item: item[0])
    return [event for _, event in found]


def timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


@dataclass
class EventBatch:
    events: list
    rebuild: bool
    bad_lines: int


class EventSource:
    """Follow complete JSONL records and detect truncation or replacement."""

    def __init__(self, path, replay=False):
        self.path = Path(path)
        self.replay = replay
        self.offset = 0
        self.fragment = b""
        self.identity = None
        self.initialized = False
        try:
            self.started_empty = not replay and self.path.stat().st_size == 0
        except FileNotFoundError:
            self.started_empty = not replay
        self.events = []
        self.index = 0
        self.bad_lines = 0
        if replay:
            self.events, self.bad_lines = self._read_all()

    def _read_all(self):
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return [], 0
        events, bad = [], 0
        for line in data.split(b"\n")[:-1]:
            event, invalid = parse_line(line)
            bad += invalid
            if event is not None:
                events.append(event)
        return events, bad

    def poll(self):
        if self.replay:
            events = self.events[self.index:]
            self.index = len(self.events)
            return EventBatch(events, False, self.bad_lines)
        try:
            stat = self.path.stat()
            identity = stat.st_dev, stat.st_ino
            rebuild = ((not self.initialized and not self.started_empty)
                       or (self.initialized and (identity != self.identity or stat.st_size < self.offset)))
            if rebuild:
                self.offset, self.fragment = 0, b""
                self.bad_lines = 0
            self.identity = identity
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except FileNotFoundError:
            if not self.initialized:
                self.started_empty = True
            return EventBatch([], False, self.bad_lines)
        data = self.fragment + chunk
        parts = data.split(b"\n")
        self.fragment = parts.pop()
        events = []
        for line in parts:
            event, invalid = parse_line(line)
            self.bad_lines += invalid
            if event is not None:
                events.append(event)
        self.initialized = True
        return EventBatch(events, rebuild, self.bad_lines)


def advisor_entries(record):
    """Extract only advisor use metadata, never answers or session content."""
    candidates = [record]
    if isinstance(record, dict):
        message = record.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            candidates += message["content"]
    for item in candidates:
        if isinstance(item, dict) and item.get("type") == "server_tool_use" and item.get("name") == "advisor":
            yield {"type": "advisor", "id": item.get("id"),
                   "ts": record.get("timestamp", record.get("ts")),
                   "session": record.get("sessionId", ""), "cwd": record.get("cwd", "")}


class AdvisorSource:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.positions = {}
        self.fragments = {}

    def poll(self):
        if not self.directory.exists():
            return []
        found = []
        for path in self.directory.rglob("*.jsonl"):
            try:
                size = path.stat().st_size
                old = self.positions.get(path, 0)
                if size < old:
                    old, self.fragments[path] = 0, b""
                with path.open("rb") as stream:
                    stream.seek(old)
                    chunk = stream.read()
                    self.positions[path] = stream.tell()
            except (FileNotFoundError, PermissionError):
                continue
            parts = (self.fragments.get(path, b"") + chunk).split(b"\n")
            self.fragments[path] = parts.pop()
            for line in parts:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    found.extend(advisor_entries(record))
        return found


@dataclass
class Frame:
    text: str
    canvas: Canvas
    packet: str | None
    executors: int
    advisor_active: bool
    advisor_calls: int


class Board:
    def __init__(self, log=None, advisor_dir=None, replay=False, speed=1.0,
                 retention=60.0, title="AZIR WORKFLOW",
                 main_title="主会话", main_subtitle="", advisor_active_seconds=20.0,
                 history=None, setup_state=None):
        log = Path(log or os.environ.get("AZIR_DISPATCH_LOG") or Path.home() / ".local/state/azir-dispatch/events.jsonl").expanduser()
        if history is None:
            history = [log.parent / "演示" / "旧记录" / "*.jsonl"]
        self.history = [] if replay else load_history(history, exclude=log.resolve())
        self.source = EventSource(log, replay)
        self.advisors = AdvisorSource(advisor_dir) if advisor_dir is not None else None
        self.replay, self.speed = replay, max(float(speed), 0.01)
        self.retention = float(retention)
        self.advisor_active_seconds = float(advisor_active_seconds)
        self.title, self.main_title, self.main_subtitle = title, main_title, main_subtitle
        self.pending = deque()
        self.decisions = deque(maxlen=8)
        self.workers = {}
        self.recent_events = deque(maxlen=12)
        self.log_advisor_ids = set()
        self.session_advisor_ids = set()
        self.log_advisor_meta = {}
        self.session_advisor_meta = {}
        self.last_log_advisor = None
        self.last_session_advisor = None
        self.advisor_until = -1.0
        self.next_advisor_scan = 0.0
        self.advisor_scanned = False
        self.bad_lines = 0
        self.today = datetime.now().date()
        self.packet = None
        self.packet_started = 0.0
        self.packet_ends = 0.0
        self.packet_target = None
        self.arrival_kind = None
        self.arrival_target = None
        self.arrived_until = -1.0
        self.jev_sweep_started = -1.0
        self.jev_sweep_until = -1.0
        self.next_ready = 0.0
        self.origin = None
        self.event_origin = None
        self.seen_advisors = set()
        self.frame = 0
        self.canvas = Canvas()
        self.setup = SetupState(setup_state) if setup_state else None
        self.slot_defs = self._slot_defs()
        self._seed_history()

    def _seed_history(self):
        """Show today's earlier decisions and records until new ones arrive."""
        for event in self.history:
            if event["type"] == "decision":
                self.decisions.appendleft(event)
            self.recent_events.appendleft(event)

    def _slot_defs(self):
        """The model tiers, or one card per working role once setup has written its state."""
        if self.setup is None or not self.setup.started:
            return [Slot(label, executor, needles, label) for label, executor, needles in EMPTY_SLOTS]
        roles = self.setup.roles()
        slots = []
        for key, label in ROLE_CARDS:
            role = roles.get(key)
            model = role["model"] if role else ""
            slots.append(Slot(label, executor_label(role["executor"]) if role else "",
                              (model.lower(),) if model else (), model_label(model) or label, role, key))
        return slots

    def resize(self, width, height):
        self.canvas = Canvas(width, height)

    @property
    def advisor_calls(self):
        unmatched = list(self.session_advisor_meta.values())
        duplicates = 0
        for session, ts in self.log_advisor_meta.values():
            if not session or ts is None:
                continue
            for i, (other_session, other_ts) in enumerate(unmatched):
                if session == other_session and other_ts is not None and abs(ts - other_ts) <= 1:
                    duplicates += 1
                    unmatched.pop(i)
                    break
        return len(self.log_advisor_ids) + len(self.session_advisor_ids) - duplicates

    @property
    def last_advisor(self):
        return max((value for value in (self.last_log_advisor, self.last_session_advisor)
                    if value is not None), default=None)

    def _count_advisor(self, event, session=False):
        ts = timestamp(event.get("ts"))
        if not self.replay and ts is not None and datetime.fromtimestamp(ts).date() != self.today:
            return
        key = (event.get("session"), event.get("id") or event.get("ts"))
        if session:
            self.session_advisor_ids.add(key)
            self.session_advisor_meta[key] = (event.get("session"), ts)
            if ts is not None:
                self.last_session_advisor = max(self.last_session_advisor or ts, ts)
        else:
            self.log_advisor_ids.add(key)
            self.log_advisor_meta[key] = (event.get("session"), ts)
            if ts is not None:
                self.last_log_advisor = max(self.last_log_advisor or ts, ts)

    def _rebuild(self, events, now):
        self.pending.clear()
        self.decisions.clear()
        self.workers.clear()
        self.recent_events.clear()
        self._seed_history()
        self.log_advisor_ids.clear()
        self.log_advisor_meta.clear()
        self.last_log_advisor = None
        self.advisor_until = -1.0
        self.packet = None
        self.packet_target = None
        self.next_ready = now
        for event in events:
            self._apply(event, now, historical=True)
        for target, worker in list(self.workers.items()):
            if worker["expires"] is not None and now >= worker["expires"]:
                del self.workers[target]

    def _enqueue(self, events, now, advisor=False, historical=False):
        for event in events:
            if advisor:
                key = (event.get("session"), event.get("id"), event.get("ts"))
                if key in self.seen_advisors:
                    continue
                self.seen_advisors.add(key)
                self._count_advisor(event, session=True)
                if not historical:
                    self.pending.append((now, event, True))
                continue
            ts = timestamp(event.get("ts"))
            if self.replay and ts is not None:
                if self.event_origin is None:
                    self.event_origin = ts
                due = self.origin + max(0.0, (ts - self.event_origin) / self.speed)
            else:
                due = now
            self.pending.append((due, event, False))

    def _apply(self, event, now, counted=False, historical=False):
        kind = event["type"]
        target = str(event.get("target") or "")
        if kind == "decision":
            self.decisions.appendleft(event)
            self.packet = "jev_main" if event.get("disposition") == "handback" else "main_jev"
            if not historical:
                self.jev_sweep_started = now
                self.jev_sweep_until = now + 0.5
        elif kind == "dispatch":
            if target in self.workers:
                self.workers.pop(target)
            self.workers[target] = {"event": event, "status": "运行中", "expires": None}
            self.packet, self.packet_target = "jev_sub", target
        elif kind in ("done", "failed"):
            if target in self.workers:
                if target not in self._visible_targets():
                    # 只有被挤出可见卡位的执行者才挪到最后，可见的保持原位不跳动
                    self.workers[target] = self.workers.pop(target)
                self.workers[target]["status"] = "完成" if kind == "done" else "失败"
                ts = timestamp(event.get("ts"))
                remaining = max(0.0, ts + self.retention - time.time()) if historical and ts is not None else self.retention
                self.workers[target]["expires"] = now + remaining
            self.packet, self.packet_target = "sub_ret", target
        elif kind == "handback":
            self.packet = "jev_main"
        elif kind == "advisor":
            if not counted:
                self._count_advisor(event)
            if not historical:
                self.advisor_until = now + self.advisor_active_seconds
            self.packet = None
        if historical:
            self.packet = None
            self.packet_target = None
            self.recent_events.appendleft(event)
            return
        self.packet_started = now
        self.packet_ends = now + (0.5 if self.packet else 0.35)
        self.arrived_until = -1.0
        self.next_ready = self.packet_ends
        self.recent_events.appendleft(event)

    def step(self, now):
        if self.origin is None:
            self.origin = now
        if self.setup is not None and self.setup.poll(now):
            self.slot_defs = self._slot_defs()
        if not self.replay and datetime.now().date() != self.today:
            self.today = datetime.now().date()
            self.log_advisor_ids.clear()
            self.session_advisor_ids.clear()
            self.log_advisor_meta.clear()
            self.session_advisor_meta.clear()
        batch = self.source.poll()
        self.bad_lines = batch.bad_lines
        if batch.rebuild and not self.replay:
            self._rebuild(batch.events, now)
        else:
            self._enqueue(batch.events, now)
        if self.advisors and now >= self.next_advisor_scan:
            self._enqueue(self.advisors.poll(), now, advisor=True, historical=not self.advisor_scanned)
            self.advisor_scanned = True
            self.next_advisor_scan = now + 1.0
        for target, worker in list(self.workers.items()):
            if worker["expires"] is not None and now >= worker["expires"]:
                del self.workers[target]
        if self.pending and now >= self.next_ready and now >= self.pending[0][0]:
            _, event, counted = self.pending.popleft()
            self._apply(event, now, counted)
        if self.packet and now >= self.packet_ends:
            self.arrival_kind, self.arrival_target = self.packet, self.packet_target
            self.arrived_until = now + 0.3
            self.packet = None
        self.frame += 1
        self._draw(now)
        return Frame(self.canvas.text(), self.canvas, self.packet, len(self.workers),
                     now < self.advisor_until, self.advisor_calls)

    def _layout(self):
        """Every rectangle and connector row of the board, in canvas cells."""
        width, height = self.canvas.width, self.canvas.height
        lw, lh = min(width, 100), min(height, 52)
        ox, oy = (width - lw) // 2, (height - lh) // 2
        extra = max(0, lh - 36)
        jev_h = 10 + extra * 3 // 5
        mcx = lw // 2
        mgr = (mcx - 15, 2, mcx + 14, 5)
        adv_x1 = max(17, lw // 5)
        adv = (1, 7, adv_x1, 6 + jev_h)
        jev = (adv_x1 + 3, 7, lw - 2, 6 + jev_h)
        jcx = (jev[0] + jev[2]) // 2
        count = len(self.slot_defs)
        card_w = (lw - 2 - 2 * (count - 1)) // count
        card_top = jev[3] + 3
        cards = []
        for i in range(count):
            x0 = 1 + i * (card_w + 2)
            x1 = lw - 2 if i == count - 1 else x0 + card_w - 1
            cards.append((x0, card_top, x1, card_top + 5))
        ret_top = card_top + 5 + 3
        ret = (lw // 4, ret_top, lw - lw // 4 - 1, ret_top + 2)
        log = (1, ret[3] + 1, lw - 2, lh - 2)

        def move(rect):
            return (rect[0] + ox, rect[1] + oy, rect[2] + ox, rect[3] + oy)

        cards = [move(rect) for rect in cards]
        return {
            "ox": ox, "oy": oy, "w": lw, "h": lh,
            "title_y": oy, "sub_y": oy + 1, "cx": ox + mcx,
            "mgr": move(mgr), "adv": move(adv), "jev": move(jev), "cards": cards,
            "ret": move(ret), "log": move(log), "footer_y": oy + lh - 1,
            "mgr_row": oy + 6, "jev_row": oy + jev[3] + 1, "split_row": oy + jev[3] + 2,
            "card_row": cards[0][3] + 1, "join_row": cards[0][3] + 2,
            "mcx": ox + mcx, "jcx": ox + jcx,
            "card_cx": [(x0 + x1) // 2 for x0, _, x1, _ in cards],
            "rcx": (ret[0] + ret[2]) // 2 + ox,
        }

    def _slots(self):
        """(target, worker) per card. A worker takes the card of its model; the
        newest worker of a model wins it and older ones move to a free card."""
        slots, spill = [None] * len(self.slot_defs), []
        for target, worker in reversed(list(self.workers.items())):
            event = worker["event"]
            actual = event.get("actual") if isinstance(event.get("actual"), dict) else event
            key = f"{actual.get('executor') or ''}:{actual.get('model') or ''}".lower()
            index = next((i for i, slot in enumerate(self.slot_defs)
                          if any(needle in key for needle in slot.needles)), None)
            if index is None or slots[index] is not None:
                spill.append((target, worker))
            else:
                slots[index] = (target, worker)
        # The rest fill free cards in dispatch order; when they do not fit, the newest stay visible.
        free = [i for i, slot in enumerate(slots) if slot is None]
        rest = list(reversed(spill))[-len(free):] if free else []
        for index, item in zip(free, rest):
            slots[index] = item
        return slots

    def _visible_targets(self):
        return {slot[0] for slot in self._slots() if slot is not None}

    def _positions(self):
        """Visible workers paired with the centre column of their card slot."""
        centers = self._layout()["card_cx"]
        return [(slot, x) for slot, x in zip(self._slots(), centers) if slot is not None]

    def _target_x(self, target, layout):
        for slot, x in zip(self._slots(), layout["card_cx"]):
            if slot is not None and slot[0] == target:
                return x
        return layout["card_cx"][len(layout["card_cx"]) // 2]

    @staticmethod
    def _run(y, a, b):
        step = 1 if b >= a else -1
        return [(x, y) for x in range(a, b + step, step)]

    def _path(self, kind, target):
        layout = self._layout()
        mcx, jcx, rcx = layout["mcx"], layout["jcx"], layout["rcx"]
        if kind == "main_jev":
            return self._run(layout["mgr_row"], mcx, jcx)
        if kind == "jev_main":
            return self._run(layout["mgr_row"], jcx, mcx)
        x = self._target_x(target, layout)
        if kind == "jev_sub":
            return [(jcx, layout["jev_row"])] + self._run(layout["split_row"], jcx, x)
        return [(x, layout["card_row"])] + self._run(layout["join_row"], x, rcx)

    def _glow(self, rect, color, strength):
        c = self.canvas
        x0, y0, x1, y1 = rect
        tint = mix(BG, color, strength)
        for y in range(y0 - 1, y1 + 2):
            for x in range(x0 - 1, x1 + 2):
                if 0 <= x < c.width and 0 <= y < c.height and (
                        x in (x0 - 1, x1 + 1) or y in (y0 - 1, y1 + 1)):
                    c.cells[y][x][2] = tint

    def _connectors(self, layout):
        c = self.canvas
        mcx, jcx, rcx = layout["mcx"], layout["jcx"], layout["rcx"]
        # 主会话 → JEV
        y = layout["mgr_row"]
        if mcx == jcx:
            c.put(mcx, y, "┆", LINE)
        else:
            c.put(min(mcx, jcx), y, "┄" * abs(jcx - mcx), LINE)
            c.put(mcx, y, "╰" if jcx > mcx else "╯", LINE)
            c.put(jcx, y, "╮" if jcx > mcx else "╭", LINE)
        # 主会话 → 顾问
        mgr, adv = layout["mgr"], layout["adv"]
        acx = (adv[0] + adv[2]) // 2
        my = (mgr[1] + mgr[3]) // 2
        c.put(acx, my, "╭" + "┄" * (mgr[0] - acx - 1), LINE)
        for yy in range(my + 1, adv[1]):
            c.put(acx, yy, "┆", LINE)
        # JEV → 执行者
        xs = layout["card_cx"]
        c.put(jcx, layout["jev_row"], "┆", LINE)
        y = layout["split_row"]
        c.put(xs[0], y, "╭" + "┄" * (xs[-1] - xs[0] - 1) + "╮", LINE)
        for x in xs[1:-1]:
            c.put(x, y, "┬", LINE)
        c.put(jcx, y, "┼" if jcx in xs[1:-1] else "┴", LINE)
        # 执行者 → 回到主会话
        for x in xs:
            c.put(x, layout["card_row"], "┆", LINE)
        y = layout["join_row"]
        c.put(xs[0], y, "╰" + "┄" * (xs[-1] - xs[0] - 1) + "╯", LINE)
        for x in xs[1:-1]:
            c.put(x, y, "┴", LINE)
        c.put(rcx, y, "┼" if rcx in xs[1:-1] else "┬", LINE)

    def _title(self, layout, f, now=0.0):
        c = self.canvas
        title = fit(self.title, layout["w"] - 4)
        x = layout["cx"] - tw(title) // 2
        n = max(1, len(title))
        for k, ch in enumerate(title):
            wave = 0.5 + 0.5 * math.sin((k / n) * math.pi * 2 - f * 0.06)
            x = c.put(x, layout["title_y"], ch, mix(TITLE, BRIGHT, wave), True)
        if self.setup is not None and not self.setup.complete:
            self._draw_steps(layout, f, now)
        else:
            c.center(layout["cx"], layout["sub_y"], "主会话  ·  JEV  ·  执行者  ·  顾问", GRAY)

    def _draw_steps(self, layout, f, now):
        """The six setup steps on the subtitle row; a step that just turned ok lights up."""
        c, setup = self.canvas, self.setup
        steps = setup.steps() if setup.started else {}
        current = setup.next_step() if setup.started else None
        items = []
        for key, label in STEPS:
            status = steps.get(key, "pending")
            if status == "ok":
                glyph, color = "✓", GO
            elif status in ("missing", "failed"):
                glyph, color = "✗", ORANGE
            elif current is not None and current[0] == key:
                glyph, color = "●", mix(TITLE, BRIGHT, 0.5 + 0.5 * math.sin(f * 0.15))
            else:
                glyph, color = "○", DIM
            items.append((f"{glyph} {label}", color, setup.flash(f"step:{key}", now)))
        width = layout["w"] - 4
        for gap in (" ─ ", "  ", " "):
            total = sum(tw(text) for text, _, _ in items) + tw(gap) * (len(items) - 1)
            if total <= width:
                break
        x = layout["cx"] - min(total, width) // 2
        right = layout["cx"] + width // 2
        y = layout["sub_y"]
        for i, (text, color, flash) in enumerate(items):
            if i:
                x = c.put(x, y, gap, LINE)
            start = x
            x = c.put(x, y, fit(text, max(0, right - x)), mix(color, BRIGHT, flash), flash > 0 or color == GO)
            if flash > 0:
                for cell in range(max(0, start - 1), min(c.width, x + 1)):
                    c.cells[y][cell][2] = mix(BG, GO, 0.45 * flash)

    def _draw_agents(self, layout):
        """Agent command lines the setup checker found, to the right of the main box."""
        c = self.canvas
        agents = self.setup.agents() if self.setup is not None else []
        if not agents:
            return
        mgr = layout["mgr"]
        x0, x1 = mgr[2] + 3, layout["ox"] + layout["w"] - 2
        width, rows = x1 - x0, mgr[3] - mgr[1] + 1
        if width < 8:
            return
        lines, line = [], []
        for name, found in agents:
            item = ("✓ " if found else "✗ ") + name
            used = sum(tw(text) + 2 for text, _ in line)
            if line and used + tw(item) > width:
                lines.append(line)
                line = []
            line.append((item, found))
        lines.append(line)
        if len(lines) > rows:
            shown = lines[:rows]
            hidden = sum(len(rest) for rest in lines[rows:])
            last = shown[-1]
            while last and sum(tw(text) + 2 for text, _ in last) + tw(f"+{hidden}") > width:
                hidden += 1
                last.pop()
            last.append((f"+{hidden}", None))
            lines = shown
        y = mgr[1] + (1 if len(lines) < rows else 0)
        if len(lines) + 1 < rows:
            c.put(x0, mgr[1], fit("终端 agent", width), DIM)
            y = mgr[1] + 1
        for line in lines:
            x = x0
            for text, found in line:
                if found is None:
                    x = c.put(x, y, text, DIM)
                else:
                    c.put(x, y, text[0], GO if found else ORANGE)
                    x = c.put(x + 2, y, fit(text[2:], max(0, x1 - x - 2)), WHITE if found else DIM)
                x += 2
            y += 1

    def _role(self, key):
        if self.setup is None or not self.setup.started:
            return None
        return self.setup.roles().get(key)

    def _draw_role_card(self, rect, slot, now):
        """An idle card for one working role: its model once chosen, 备选 when it is a fallback."""
        c = self.canvas
        x0, y0, x1, y1 = rect
        inner = x1 - x0 - 3
        role = slot.role
        flash = self.setup.flash(f"role:{slot.role_key}", now)
        ready = role is not None and role["status"] == "ok"
        c.box(x0, y0, x1, y1, mix(mix(BG, EXEC, 0.45) if ready else EMPTY, BRIGHT, flash))
        mark, brand = agent_icon(f"{role['executor']}:{role['model']}") if ready else ("", None)
        x = x0 + 2
        if mark:
            c.put(x, y0 + 1, mark, brand)
            x += 2
        x = c.put(x, y0 + 1, fit(slot.label, max(0, x1 - x)), WHITE if ready else DIM, ready)
        if ready and role["fallback"] and x + 2 + tw("备选") <= x1:
            c.put(x + 2, y0 + 1, "备选", HAND)
        if ready:
            c.put(x0 + 2, y0 + 2, fit(model_label(role["model"]) or slot.detail, inner), WHITE)
            c.put(x0 + 2, y0 + 3, fit(slot.detail, inner), GRAY)
            c.put(x0 + 2, y0 + 4, "空闲", EMPTY_TEXT)
        elif role is not None and role["status"] == "missing":
            c.put(x0 + 2, y0 + 4, fit("未配置", inner), ORANGE)
        else:
            c.put(x0 + 2, y0 + 4, fit("待选模型", inner), EMPTY_TEXT)

    def _decision_row(self, event):
        try:
            value = max(0.0, min(1.0, float(event.get("confidence") or 0)))
        except (TypeError, ValueError):
            value = 0.0
        handback = event.get("disposition") == "handback"
        destination = ("交回" if handback else
                       "JEV 不可用，走默认" if event.get("source") == "default" else
                       "规则" if event.get("source") == "rules" else
                       "建议" if event.get("point") in ("next_step", "wrapup") else "直接执行")
        color = {"交回": HAND, "建议": CYAN, "直接执行": GO, "规则": RULE}.get(destination, GRAY)
        answer = single_line(event.get("jev_choice") if handback else event.get("answer")
                             or event.get("jev_choice") or "")
        if event.get("point") == "wrapup" and answer in WRAPUP:
            answer = f"{answer} {WRAPUP[answer]}"
        return answer, value, destination, color

    def _draw_jev(self, layout, now):
        c = self.canvas
        x0, y0, x1, y1 = jev = layout["jev"]
        lit = self.packet in ("main_jev", "jev_main")
        self._glow(jev, JEV, 0.22 if lit else 0.11)
        c.box(x0, y0, x1, y1, mix(JEV, BRIGHT, 0.45 if lit else 0))
        self._sweep(c, x0, y0, x1, y1, now)
        x = c.put(x0 + 2, y0 + 1, "JEV · 分流层", JEV, True)
        if self.setup is not None and self.setup.rules_mode and x + 2 + tw("规则模式") < x1 - 6:
            c.put(x + 2, y0 + 1, "规则模式", HAND, True)
        rows = max(0, (y1 - y0 - 2) // 2)
        shown = list(self.decisions)[:rows]
        if not shown:
            c.center((x0 + x1) // 2, (y0 + y1) // 2 + 1, "等待判断", DIM)
            return
        parts = [self._decision_row(event) for event in shown]
        dest_w = max(8, max(tw(p[2]) for p in parts))
        dest_x = x1 - 1 - dest_w
        conf_x = dest_x - 6
        bar_w = 10 if x1 - x0 > 50 else 6
        bar_x = conf_x - 1 - bar_w
        answer_w = max(4, bar_x - 2 - (x0 + 2))
        for i, (event, (answer, value, destination, color)) in enumerate(zip(shown, parts)):
            y = y0 + 2 + i * 2
            c.inside(x0, x1 - 7, x0 + 2, y, decision_label(event), DIM)
            ts = timestamp(event.get("ts"))
            if ts is not None:
                c.put(x1 - 6, y, datetime.fromtimestamp(ts).strftime("%H:%M"), DIM)
            mark, brand = agent_icon(answer) if ":" in answer else ("", None)
            if mark:
                c.put(x0 + 2, y + 1, mark, brand)
                c.put(x0 + 4, y + 1, fit_middle(short_name(answer), answer_w - 2), WHITE, True)
            else:
                c.put(x0 + 2, y + 1, fit_middle(answer, answer_w), WHITE, True)
            n = round(value * bar_w)
            c.put(bar_x, y + 1, "━" * n, color)
            c.put(bar_x + n, y + 1, "━" * (bar_w - n), TRACK)
            c.put(conf_x, y + 1, f"{value:.2f}", GRAY)
            c.put(dest_x, y + 1, destination, color)

    def _draw_card(self, rect, slot, worker, target, now=0.0):
        c = self.canvas
        x0, y0, x1, y1 = rect
        inner = x1 - x0 - 3
        if worker is None and self.slot_defs[slot].role_key is not None:
            self._draw_role_card(rect, self.slot_defs[slot], now)
            return
        if worker is None:
            c.box(x0, y0, x1, y1, EMPTY)
            label, executor = self.slot_defs[slot].label, self.slot_defs[slot].detail
            mark, brand = agent_icon(label)
            c.put(x0 + 2, y0 + 1, mark, mix(brand, BG, 0.35))
            c.put(x0 + 4, y0 + 1, fit(label, inner - 2), DIM)
            c.put(x0 + 2, y0 + 2, fit(executor, inner), EMPTY_TEXT)
            c.put(x0 + 2, y0 + 4, "空闲", EMPTY_TEXT)
            return
        status = worker["status"]
        col = EXEC if status == "运行中" else GO if status == "完成" else ORANGE
        c.box(x0, y0, x1, y1, mix(BG, col, 0.75))
        event = worker["event"]
        actual = event.get("actual") if isinstance(event.get("actual"), dict) else event
        suggested = event.get("suggested")
        changed = isinstance(suggested, dict) and any(
            suggested.get(key) != actual.get(key) for key in ("executor", "model", "effort"))
        c.put(x0 + 2, y0 + 1, fit(target, inner), WHITE, True)
        key = f"{actual.get('executor') or ''}:{actual.get('model') or ''}"
        if any(needle in key.lower() for needle in self.slot_defs[slot].needles):
            # Narrow cards: the short tier name, with the executor moved to the detail line.
            who = self.slot_defs[slot].short
            prefix = pretty_executor(actual.get("executor"))
        else:
            who = f"{pretty_executor(actual.get('executor'))} {pretty_model(actual.get('model'))}".strip()
            prefix = ""
        mark, brand = agent_icon(key)
        if mark:
            c.put(x0 + 2, y0 + 2, mark, brand)
            c.put(x0 + 4, y0 + 2, fit(who, inner - 2), GRAY)
        else:
            c.put(x0 + 2, y0 + 2, fit(who, inner), GRAY)
        effort = str(actual.get("effort") or "")
        detail = f"{effort} {'已改派' if changed else ''}".strip()
        if prefix:
            detail = f"{prefix} {detail}" if detail else prefix
        skill = event.get("skill")
        if isinstance(skill, str) and skill and skill != "none":
            with_skill = f"{detail} · {skill}" if detail else skill
            if tw(with_skill) <= inner:
                detail = with_skill
        c.put(x0 + 2, y0 + 3, fit(detail, inner), ORANGE if changed else DIM)
        mark = "● " if status == "运行中" else "✓ " if status == "完成" else "✗ "
        c.put(x0 + 2, y0 + 4, mark + status, col, True)

    def _draw(self, now):
        c, f = self.canvas, self.frame
        c.clear()
        if c.width < 80 or c.height < 36:
            c.put(0, 0, fit(f"终端太小：最小 80×36，当前 {c.width}×{c.height}", c.width), GRAY)
            return
        layout = self._layout()
        self._title(layout, f, now)

        mgr = layout["mgr"]
        main_lit = self.packet in ("main_jev", "jev_main", "sub_ret")
        if main_lit:
            self._glow(mgr, MGR, 0.14)
        c.box(*mgr, mix(MGR, BRIGHT, 0.5 if main_lit else 0))
        mx = (mgr[0] + mgr[2]) // 2
        c.center(mx, mgr[1] + 1, self.main_title, WHITE, True, 25)
        main = self._role("main")
        if self.main_subtitle or main is None:
            c.center(mx, mgr[1] + 2, self.main_subtitle, GRAY, max_width=25)
        elif main["status"] == "ok":
            model, executor = model_label(main["model"]), executor_label(main["executor"])
            backup = "备选" if main["fallback"] else ""
            # A narrow box drops the executor first; the model and 备选 always stay.
            who = " · ".join(part for part in (model, executor, backup) if part)
            if tw(who) > 25:
                who = " · ".join(part for part in (model or executor, backup) if part)
            c.center(mx, mgr[1] + 2, who, mix(GRAY, BRIGHT, self.setup.flash("role:main", now)), max_width=25)
        else:
            c.center(mx, mgr[1] + 2, "未配置", ORANGE, max_width=25)
        self._draw_agents(layout)

        self._connectors(layout)

        adv = layout["adv"]
        advisor_on = now < self.advisor_until
        acol = ADV if advisor_on else mix(BG, ADV, 0.35)
        c.box(*adv, acol)
        ax = (adv[0] + adv[2]) // 2
        aw = adv[2] - adv[0] - 2
        c.center(ax, adv[1] + 2, "顾问", ADV if advisor_on else WHITE, True)
        advisor = self._role("advisor")
        if advisor is not None and advisor["status"] != "ok" and not advisor_on:
            unset = advisor["status"] == "optional_unconfigured"
            c.center(ax, adv[1] + 4, "未配置，可选" if unset else "未配置", DIM if unset else ORANGE, max_width=aw)
        else:
            if advisor is not None:
                c.center(ax, adv[1] + 3, model_label(advisor["model"]) or executor_label(advisor["executor"]),
                         mix(GRAY, BRIGHT, self.setup.flash("role:advisor", now)), max_width=aw)
            c.center(ax, adv[1] + 4, "正在回答" if advisor_on else "待命", ADV if advisor_on else DIM, max_width=aw)
        if self.last_advisor is not None:
            c.center(ax, adv[1] + 6, "最近一次 " + datetime.fromtimestamp(self.last_advisor).strftime("%H:%M"), GRAY, max_width=aw)
        c.center(ax, adv[1] + 7, f"今日调用 {self.advisor_calls} 次", GRAY, max_width=aw)
        if advisor_on:
            x0, y0, x1, y1 = adv
            ring = ([(x, y0) for x in range(x0, x1 + 1)] + [(x1, y) for y in range(y0 + 1, y1 + 1)]
                    + [(x, y1) for x in range(x1 - 1, x0 - 1, -1)] + [(x0, y) for y in range(y1 - 1, y0, -1)])
            for k in range(8):
                x, y = ring[(f * 2 - k) % len(ring)]
                c.cells[y][x][1] = mix(ADV, BRIGHT, 1 - k / 8)

        self._draw_jev(layout, now)

        slots = self._slots()
        for slot, rect in enumerate(layout["cards"]):
            if slots[slot] is not None:
                target, worker = slots[slot]
                self._draw_card(rect, slot, worker, target, now)
            else:
                self._draw_card(rect, slot, None, "", now)
        hidden = len(self.workers) - sum(item is not None for item in slots)
        if hidden:
            note = f"还有 {hidden} 个"
            c.put(layout["jev"][2] - tw(note), layout["jev_row"], note, GRAY)

        ret = layout["ret"]
        c.box(*ret, mix(LINE, BRIGHT, 0.5 if self.packet == "sub_ret" else 0))
        c.center((ret[0] + ret[2]) // 2, ret[1] + 1, "回到主会话  ·  审查  ·  验证", GRAY)

        if self.packet:
            self._draw_packet(c, self._path(self.packet, self.packet_target), now)

        log = layout["log"]
        c.box(*log, LINE)
        c.put(log[0] + 2, log[1] + 1, "会话记录", GRAY)
        rows = max(0, log[3] - log[1] - 2)
        for i, event in enumerate(list(self.recent_events)[:rows]):
            y = log[1] + 2 + i
            label, desc, color = self._record(event)
            ts = timestamp(event.get("ts"))
            x = c.put(log[0] + 2, y, datetime.fromtimestamp(ts).strftime("%H:%M") if ts is not None else "--:--", DIM)
            x = c.put(x + 2, y, label, color, True)
            c.inside(log[0], log[2], x + 2, y, desc, WHITE)
        footer = f"执行者 {len(self.workers)}  ·  顾问今日 {self.advisor_calls} 次  ·  q 退出"
        if self.bad_lines:
            footer += f"  ·  跳过 {self.bad_lines} 行坏记录"
        setup_note = self._setup_note()
        if setup_note:
            # Clip the note, not the way out.
            note = fit(setup_note, layout["w"] - 2 - tw("  ·  q 退出"))
            c.center(layout["cx"], layout["footer_y"], f"{note}  ·  q 退出", GRAY)
        else:
            c.center(layout["cx"], layout["footer_y"], footer, DIM)
        self._light_arrival(c, now)

    def _setup_note(self):
        """What the footer says while setup is unfinished: the next step and how to do it."""
        setup = self.setup
        if setup is None or setup.complete:
            return ""
        if setup.broken:
            return "安装状态文件读不出，按还没开始安装显示"
        if not setup.started:
            return "还没开始安装 · 让你的 agent 读 skills/setup/SKILL.md"
        key, label, status = setup.next_step()
        hint = single_line(setup.hint(key))
        verb = "没装好" if status in ("missing", "failed") else "下一步"
        return f"{verb} · {label}" + (f"：{hint}" if hint else "")

    def _record(self, event):
        kind = event["type"]
        if kind == "decision":
            answer, value, destination, _ = self._decision_row(event)
            return "判断", f"{decision_label(event)}  {short_name(answer) if ':' in answer else answer}  {value:.2f}  {destination}", JEV
        if kind == "dispatch":
            actual = event.get("actual") if isinstance(event.get("actual"), dict) else event
            who = f"{pretty_executor(actual.get('executor'))} {pretty_model(actual.get('model'))}".strip()
            target = single_line(event.get("target"))
            return "派发", f"{target} → {who}" if who else target, GO
        if kind == "done":
            return "完成", single_line(event.get("target")), EXEC
        if kind == "failed":
            return "失败", single_line(event.get("target")), ORANGE
        if kind == "handback":
            return "交回", single_line(event.get("reason") or event.get("target")), HAND
        return "问顾问", "", ADV

    def _draw_packet(self, canvas, points, now):
        if not points:
            return
        progress = min(1.0, max(0.0, (now - self.packet_started) / 0.5))
        head = int(progress * (len(points) - 1))
        col = GO if self.packet == "jev_sub" else EXEC if self.packet == "sub_ret" else MGR
        for k, (char, intensity) in enumerate((("●", 1.0), ("•", 0.68), ("∙", 0.43), ("·", 0.24))):
            j = head - k
            if j >= 0:
                x, y = points[j]
                if canvas.cells[y][x][0] not in "┌┐└┘│":
                    color = BRIGHT if k == 0 else mix(BG, col, intensity)
                    canvas.put(x, y, char, color, True)

    def _sweep(self, canvas, x0, y0, x1, y1, now):
        if not self.jev_sweep_started <= now <= self.jev_sweep_until:
            return
        progress = min(1.0, (now - self.jev_sweep_started) / 0.5)
        center = x0 + round(progress * (x1 - x0))
        for x in range(max(x0 + 1, center - 3), min(x1, center + 4)):
            strength = 1.0 - abs(x - center) / 4
            canvas.cells[y0][x][1] = mix(JEV, BRIGHT, strength)

    def _arrival_box(self):
        layout = self._layout()
        if self.arrival_kind == "main_jev":
            return layout["jev"]
        if self.arrival_kind == "jev_main":
            return layout["mgr"]
        if self.arrival_kind == "sub_ret":
            return layout["ret"]
        if self.arrival_kind == "jev_sub":
            for slot, rect in zip(self._slots(), layout["cards"]):
                if slot is not None and slot[0] == self.arrival_target:
                    return rect
        return None

    def _light_arrival(self, canvas, now):
        if now >= self.arrived_until:
            return
        box = self._arrival_box()
        if box not in canvas.boxes:
            return
        x0, y0, x1, y1 = box
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                canvas.cells[y][x][1] = mix(canvas.cells[y][x][1], BRIGHT, 0.85)
        for y in range(y0 + 1, y1):
            for x in (x0, x1):
                canvas.cells[y][x][1] = mix(canvas.cells[y][x][1], BRIGHT, 0.85)


def settings(path):
    if path is None:
        return {}
    with Path(path).open("rb") as stream:
        return tomllib.load(stream).get("dashboard", {})


def parse_size(value):
    try:
        width, height = value.lower().split("x", 1)
        size = int(width), int(height)
    except (AttributeError, ValueError):
        raise argparse.ArgumentTypeError("尺寸格式应为列x行，例如 88x45")
    if min(size) <= 0:
        raise argparse.ArgumentTypeError("尺寸必须大于 0")
    return size


def main(argv=None):
    parser = argparse.ArgumentParser(description="azir dispatch terminal board")
    parser.add_argument("--log", help="JSON Lines event file")
    parser.add_argument("--replay", help="Replay a JSON Lines file")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--config", help="TOML configuration")
    parser.add_argument("--advisor-dir", help="Claude Code session directory")
    parser.add_argument("--no-advisor-scan", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check every frame's borders and canvas bounds")
    parser.add_argument("--size", type=parse_size, default=(W, H), metavar="列x行",
                        help="指定 --check 画布尺寸")
    parser.add_argument("--seconds", type=float, default=10.0, help="Duration for --check, or the moment --once prints")
    parser.add_argument("--once", action="store_true", help="Print one frame at --size after --seconds and exit")
    parser.add_argument("--history", action="append", help="Glob of earlier logs whose records from today are shown")
    parser.add_argument("--setup-state", help="setup-state.json to draw setup progress from; live mode reads the default path")
    parser.add_argument("--no-setup", action="store_true", help="Do not draw setup progress")
    args = parser.parse_args(argv)
    config = settings(args.config)
    advisor_dir = None if args.no_advisor_scan or args.replay else (
        args.advisor_dir or config.get("advisor_dir") or Path.home() / ".claude/projects")
    setup_state = args.setup_state or config.get("setup_state")
    if args.no_setup or setup_state is False or (args.replay and not setup_state):
        setup_state = None
    elif not setup_state:
        setup_state = default_path()
    board = Board(log=args.replay or args.log or os.environ.get("AZIR_DISPATCH_LOG") or config.get("log"), advisor_dir=advisor_dir,
                  replay=bool(args.replay), speed=args.speed,
                  retention=config.get("retention_seconds", 60),
                  advisor_active_seconds=config.get("advisor_active_seconds", 20),
                  title=config.get("title", "AZIR WORKFLOW"),
                  main_title=config.get("main_title", "主会话"),
                  main_subtitle=config.get("main_subtitle", ""),
                  history=args.history or (([config["history"]] if isinstance(config["history"], str)
                                            else config["history"]) if "history" in config else None),
                  setup_state=setup_state)
    if args.once:
        width, height = args.size
        board.resize(width, height)
        frame = None
        for frame_number in range(max(1, int(args.seconds * FPS) + 1)):
            frame = board.step(frame_number / FPS)
        sys.stdout.write(frame.canvas.render_lines())
        return 0
    if args.check:
        width, height = args.size
        if width < 80 or height < 36:
            print(f"终端太小：最小 80×36，指定 {width}×{height}")
            return 1
        board.resize(width, height)
        for frame_number in range(max(1, int(args.seconds * FPS))):
            frame = board.step(frame_number / FPS)
            overflow = frame.canvas.overflow_errors()
            if overflow:
                print(f"文字可能画到画布外：第 {frame_number + 1} 帧，行 {overflow[:8]}")
                return 1
            bad = frame.canvas.border_errors()
            if bad:
                print(f"框线被压掉：第 {frame_number + 1} 帧 {bad[:8]}")
                return 1
        print(f"检查通过：{max(1, int(args.seconds * FPS))} 帧，框线都完整，画布外没有字符；画布 {width}x{height}")
        return 0
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("请在终端里运行。预检可用 --check。")
        return 1
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def restore(*_):
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()

    signal.signal(signal.SIGTERM, lambda *_: (restore(), sys.exit(0)))
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    tty.setcbreak(fd)
    last_size = None
    start = time.monotonic()
    next_frame = start
    try:
        while True:
            cols, rows = shutil.get_terminal_size((W, H))
            resized = (cols, rows) != last_size
            last_size = cols, rows
            if cols < 80 or rows < 36:
                output = f"\x1b[2J\x1b[H终端太小：最小 80×36，当前 {cols}×{rows}"
            else:
                if resized:
                    board.resize(cols, rows)
                frame = board.step(time.monotonic() - start)
                output = ("\x1b[2J" if resized else "") + frame.canvas.render()
            sys.stdout.write(output)
            sys.stdout.flush()
            next_frame = max(next_frame + 1 / FPS, time.monotonic())
            ready, _, _ = select.select([sys.stdin], [], [], max(0.0, next_frame - time.monotonic()))
            if ready and sys.stdin.read(1) in ("q", "Q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        restore()
    return 0
