"""Standby screen for the executor area: three empty workstations until an executor arrives."""

from __future__ import annotations

import argparse
import math

from .agent_icons import put_icon, put_logo
from .jev_view import SPINNER, cell_width
from .view_output import (BLUE, DIM, GRAY, GREEN, ORANGE, PINK, PURPLE, TEAL, WHITE, Rows,
                          add_output_args, gradient, mix, run_view, size_from_args)


STATIONS = (
    {"name": "6.1 Sol", "model": "gpt-6.1-sol", "carrier": "Codex · high", "color": BLUE},
    {"name": "Luna", "model": "gpt-6-luna", "carrier": "Codex · medium", "color": TEAL},
    {"name": "Gemini", "model": "gemini-3.7-flash", "carrier": "pi · default", "color": (125, 110, 244)},
    {"name": "Grok", "model": "grok-build", "carrier": "Grok · default", "color": (225, 228, 235)},
    {"name": "Opus", "model": "claude-opus-5-5", "carrier": "Claude · high", "color": ORANGE},
)
IDLE_DOT = (70, 120, 88)


class ExecIdle:
    def __init__(self, width=60, height=15):
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.elapsed = 0.0

    def resize(self, width, height):
        self.width, self.height = max(1, int(width)), max(1, int(height))

    def step(self, elapsed=1 / 20):
        self.elapsed += max(0.0, float(elapsed))
        return self.draw()

    def _breath(self, index):
        # One slow sine per station, offset so the three dots do not pulse together.
        return 0.5 + 0.5 * math.sin(self.elapsed * 2 * math.pi / 2.6 - index * 2.1)

    def _cursor(self):
        return "▌" if int(self.elapsed * 2) % 2 == 0 else " "

    def _header(self, rows):
        row = rows.add(0)
        spinner = SPINNER[int(self.elapsed * 10) % len(SPINNER)]
        row.put(1, spinner, PURPLE)
        title = "执行者待命"
        row.put(3, title, runs=gradient(3, title))
        row.right(f"空闲 {len(STATIONS)} / {len(STATIONS)}", GRAY, margin=1)

    def draw(self):
        if self.width >= 5 * 11 and self.height >= 11:
            return self._columns()
        if self.width >= 34 and self.height >= 3 * len(STATIONS) + 1:
            return self._stack()
        return self._list()

    def _columns(self):
        """Five compact cards: logo, short name and status."""
        rows = Rows(self.width, self.height)
        self._header(rows)
        rows.add(3)
        card = self.width // len(STATIONS)
        lines = [rows.add(p) for p in (0, 0, 0, 0, 2, 0, 0, 0)]
        for index, station in enumerate(STATIONS):
            x0, inner = index * card, card - 2
            breath = self._breath(index)
            edge = mix(DIM, station["color"], 0.25 + 0.35 * breath)
            lines[0].put(x0, "╭" + "─" * inner + "╮", edge)
            for row in lines[1:-1]:
                row.put(x0, "│", edge)
                row.put(x0 + card - 1, "│", edge)
            put_logo(lines[1:4], x0 + (card - 7) // 2, station["model"])
            name = station["name"][:inner]
            lines[5].put(x0 + (card - cell_width(name)) // 2, name, station["color"])
            status = x0 + (card - 6) // 2
            lines[6].put(status, "●", mix(IDLE_DOT, GREEN, breath))
            lines[6].put(status + 2, "空闲", GREEN)
            lines[7].put(x0, "╰" + "─" * inner + "╯", edge)
        return rows.frame(top_margin=max(0, (self.height - 10) // 2))

    def _stack(self):
        rows = Rows(self.width, self.height)
        self._header(rows)
        rows.add(3)
        inner = self.width - 2
        for index, station in enumerate(STATIONS):
            breath = self._breath(index)
            edge = mix(DIM, station["color"], 0.25 + 0.35 * breath)
            top = rows.add(0)
            top.put(0, "╭" + "─" * inner + "╮", edge)
            top.put(2, " " * (cell_width(station["name"]) + 5))
            top.put(put_icon(top, 3, station["model"]), station["name"], station["color"])
            status = " ● 空闲 "
            col = self.width - 2 - cell_width(status)
            top.put(col, status, GREEN)
            top.put(col + 1, "●", mix(IDLE_DOT, GREEN, breath))
            body = rows.add(0)
            body.put(0, "│", edge)
            body.put(2, station["model"], WHITE)
            body.put(2 + len(station["model"]) + 2, station["carrier"], GRAY)
            body.put(self.width - 1, "│", edge)
            rows.add(0).put(0, "╰" + "─" * inner + "╯", edge)
        return rows.frame(top_margin=max(0, (self.height - 14) // 2))

    def _list(self):
        rows = Rows(self.width, self.height)
        self._header(rows)
        for index, station in enumerate(STATIONS):
            row = rows.add(0)
            row.put(1, "●", mix(IDLE_DOT, GREEN, self._breath(index)))
            row.put(put_icon(row, 3, station["model"]), station["name"], station["color"])
            row.put(14, "空闲", GREEN)
            row.put(19, station["model"], GRAY)
        return rows.frame()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_output_args(parser)
    args = parser.parse_args(argv)
    width, height = size_from_args(args, (60, 15))
    return run_view(ExecIdle(width, height), args)


if __name__ == "__main__":
    raise SystemExit(main())
