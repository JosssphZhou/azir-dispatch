"""Shared terminal loop, row layout and optional PNG export for the demonstration views."""

import math
import os
from pathlib import Path
import select
import shutil
import sys
import termios
import time
import tty

# Same palette as the dashboard; JEV is drawn as a purple-to-pink gradient.
PURPLE, PINK = (180, 140, 255), (255, 122, 168)
WHITE, GRAY, DIM = (232, 234, 238), (150, 156, 166), (93, 102, 114)
GREEN, ORANGE, BLUE, TEAL = (125, 225, 150), (240, 150, 110), (125, 170, 240), (110, 205, 215)
BACKGROUND = (12, 12, 16)


def mix(start, end, t):
    t = max(0.0, min(1.0, float(t)))
    return tuple(round(start[i] + (end[i] - start[i]) * t) for i in range(3))


def gradient(start_col, text, start=PURPLE, end=PINK):
    """Color runs that shade each cell of text from start to end."""
    from .jev_view import cell_width

    runs, col, total = [], start_col, max(1, cell_width(text) - 1)
    for ch in text:
        size = cell_width(ch)
        runs.append((col, col + size, mix(start, end, (col - start_col) / total)))
        col += size
    return runs


class Rows:
    """Rows with a drop priority, so short panes lose decoration before content."""

    def __init__(self, width, height):
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.items = []

    def add(self, priority=0):
        row = Row(self.width)
        self.items.append((priority, row))
        return row

    def frame(self, top_margin=0):
        from .jev_view import Frame

        items = list(self.items)
        while len(items) > self.height:
            worst = max(priority for priority, _ in items)
            if worst == 0:
                items = items[:self.height]
                break
            index = max(i for i, (priority, _) in enumerate(items) if priority == worst)
            items.pop(index)
        margin = min(top_margin, self.height - len(items))
        rows = [Row(self.width)] * max(0, margin) + [row for _, row in items]
        self.kept = rows
        text = "\n".join(row.text() for row in rows)
        colors = {index: row.runs for index, row in enumerate(rows) if row.runs}
        return Frame(text, set(), colors, [], self.width, self.height)


class Row:
    """One terminal row as a cell grid; later text overwrites earlier text."""

    def __init__(self, width):
        self.width = width
        self.chars = [" "] * width  # "" marks the right half of a wide character
        self.colors = [None] * width

    def _clear(self, col):
        # Writing over half of a wide character blanks the other half.
        if self.chars[col] == "" and col > 0:
            self.chars[col - 1] = " "
        if col + 1 < self.width and self.chars[col + 1] == "":
            self.chars[col + 1] = " "

    def put(self, col, text, color=None, runs=None):
        """Place text at a cell column; anything past the pane edge is cut."""
        from .jev_view import cell_width, fit

        if col < 0 or col >= self.width or not text:
            return col
        text = fit(text, self.width - col)
        for ch in text:
            size = cell_width(ch)
            if size == 0:
                continue
            if col + size > self.width:
                break
            self._clear(col)
            if size == 2:
                self._clear(col + 1)
            rgb = next((c for s, e, c in runs if s <= col < e), None) if runs is not None else color
            self.chars[col], self.colors[col] = ch, rgb
            if size == 2:
                self.chars[col + 1], self.colors[col + 1] = "", rgb
            col += size
        return col

    def right(self, text, color=None, margin=0, gradient_colors=None):
        from .jev_view import cell_width

        col = max(0, self.width - margin - cell_width(text))
        runs = gradient(col, text, *gradient_colors) if gradient_colors else None
        return self.put(col, text, color, runs)

    @property
    def runs(self):
        out, start = [], None
        for col in range(self.width + 1):
            rgb = self.colors[col] if col < self.width else None
            if start is not None and (rgb != self.colors[start] or col == self.width):
                out.append((start, col, self.colors[start]))
                start = None
            if start is None and rgb is not None:
                start = col
        return out

    def text(self):
        return "".join(self.chars).rstrip()


def add_output_args(parser):
    parser.add_argument("--once", action="store_true", help="输出一帧后退出")
    parser.add_argument("--png", type=Path, help="把 --once 的这一帧另存为 PNG，需要 Pillow")
    parser.add_argument("--width", type=int, help="画面列数，默认读取终端")
    parser.add_argument("--height", type=int, help="画面行数，默认读取终端")
    parser.add_argument("--at", type=float, default=2.0, help="单帧的动画时刻，单位秒")


def size_from_args(args, default=(60, 16)):
    terminal = shutil.get_terminal_size(default)
    return max(1, args.width or terminal.columns), max(1, args.height or terminal.lines)


FALLBACK_FONTS = (Path("/System/Library/Fonts/Menlo.ttc"),
                  Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"))


def _font_path():
    candidates = (
        Path.home() / "Library/Fonts/MapleMono-CN-Regular.ttf",
        Path("/Library/Fonts/MapleMono-CN-Regular.ttf"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("PNG 导出找不到中文等宽字体")


def save_png(frame, path, scale=2):
    """Draw a frame cell by cell, the way a 24-bit terminal shows it."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG 导出需要 Pillow：python3 -m pip install Pillow") from exc
    from .jev_view import cell_width

    font = ImageFont.truetype(str(_font_path()), 15 * scale)
    # Terminals fall back to other fonts for glyphs the main font lacks; do the same.
    try:
        from fontTools.ttLib import TTFont

        def cmap_of(path):
            return TTFont(str(path), fontNumber=0).getBestCmap() or {}
    except ImportError:  # fontTools is optional; without it every glyph uses the main font
        cmap_of = None
    faces = [(font, cmap_of(_font_path()) if cmap_of else None)]
    for extra in FALLBACK_FONTS:
        if cmap_of and extra.is_file():
            faces.append((ImageFont.truetype(str(extra), 15 * scale), cmap_of(extra)))

    def face_for(ch):
        return next((face for face, cmap in faces if cmap is None or ord(ch) in cmap), font)

    cell, line, pad = 9 * scale, 20 * scale, 10 * scale
    picture = Image.new("RGB", (frame.width * cell + 2 * pad, frame.height * line + 2 * pad), BACKGROUND)
    draw = ImageDraw.Draw(picture)
    # Pane outline so overflow past the right or bottom edge is visible.
    draw.rectangle((pad - 2, pad - 2, pad + frame.width * cell + 1, pad + frame.height * line + 1),
                   outline=(40, 42, 50))
    for y, row in enumerate(frame.text.splitlines()):
        col = 0
        for ch in row:
            size = cell_width(ch)
            color = next((rgb for start, end, rgb in frame.color_runs.get(y, [])
                          if start <= col < end), WHITE)
            x, top = pad + col * cell, pad + y * line
            eighths = "▏▎▍▌▋▊▉█"
            if ch in eighths:
                part = (eighths.index(ch) + 1) / 8
                draw.rectangle((x, top + 3 * scale, x + round(cell * part) - 1, top + line - 4 * scale), fill=color)
            elif size and ch != " ":
                draw.text((x, top + 2 * scale), ch, font=face_for(ch), fill=color)
            col += size
    picture.save(path, format="PNG")


def run_view(view, args, step=1 / 30):
    from .jev_view import _ansi

    if args.png and not args.once:
        raise ValueError("--png 必须和 --once 一起使用")
    if not math.isfinite(args.at) or args.at < 0:
        raise ValueError("--at 必须是有限的非负数")
    if args.once or not sys.stdout.isatty():
        view.step(0.0)
        frame = view.step(args.at)
        if args.png:
            save_png(frame, args.png)
        sys.stdout.write((_ansi(frame) if sys.stdout.isatty() else frame.text) + "\n")
        return 0
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd) if os.isatty(fd) else None
    try:
        if old is not None:
            tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l\x1b[2J")
        last = time.monotonic()
        while True:
            now = time.monotonic()
            columns, lines = shutil.get_terminal_size((view.width, view.height))
            if hasattr(view, "resize") and not (args.width or args.height):
                view.resize(columns, lines)
            frame = view.step(now - last)
            last = now
            # Explicit cursor positions avoid wrapping at the right and bottom edges.
            out = ["\x1b[H"]
            rendered = _ansi(frame).split("\n")
            for index in range(frame.height):
                row = rendered[index] if index < len(rendered) else ""
                out.append(f"\x1b[{index + 1};1H\x1b[2K" + row)
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            if old is not None:
                ready, _, _ = select.select([fd], [], [], step)
                if ready and os.read(fd, 1).lower() == b"q":
                    break
            else:
                time.sleep(step)
    except KeyboardInterrupt:
        pass
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[0m\x1b[?25h\n")
        sys.stdout.flush()
    return 0
