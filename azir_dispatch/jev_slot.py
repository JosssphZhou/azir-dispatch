"""Slot machine animation played before a new dispatch decision settles."""

from __future__ import annotations

import math
import random
import zlib

from .agent_icons import icon
from .jev_view import LABELS, SPINNER, _plain, cell_width, short_name
from .view_output import DIM, GRAY, PINK, PURPLE, WHITE, Rows, gradient, mix

# Reels stop one after another, each with a short bounce, then the win flash.
STOPS = (1.25, 1.65, 2.05)
BOUNCE = 0.22
SLOT_SECONDS = 2.8
REEL_WIDTH = 11
GOLD, HOT = (255, 214, 120), (255, 160, 220)
SPARK_CHARS = "✦✧⋆✦·"


def _reel_position(t, stop, travel):
    """Rows travelled by a reel: fast start, ease-out stop, then a bounce past the target."""
    if t <= 0:
        return 0.0, 0.0
    if t < stop:
        u = t / stop
        return travel * (1 - (1 - u) ** 3), 3 * travel * (1 - u) ** 2 / stop
    u = (t - stop) / BOUNCE
    if u >= 1:
        return float(travel), 0.0
    return travel + 0.95 * math.sin(math.pi * u) * (1 - u) ** 0.6, 0.0


def _neon(col, start, width, flash=0.0):
    color = mix(PURPLE, PINK, (col - start) / max(1, width - 1))
    return mix(color, WHITE, flash)


def draw_slot(view, event, t, tag=None):
    """One slot frame at t seconds into the animation, or None when the pane is too small."""
    width, height = view.width, view.height
    options = event.get("options")
    keys = list(options) if isinstance(options, (dict, list)) else []
    choice = str(event.get("jev_choice") or event.get("answer") or (keys[0] if keys else ""))
    keys = [str(key) for key in keys] or [choice]
    if choice not in keys:
        keys.append(choice)
    names = [short_name(key) for key in keys]
    target = keys.index(choice)
    reels = 3 if width >= 3 * REEL_WIDTH + 2 + 6 else 1
    inner = reels * REEL_WIDTH + (reels - 1)
    box = inner + 2
    if width < box + 4 or height < 3:
        return None
    left = (width - box) // 2
    stops = STOPS[-reels:]
    settled = t >= stops[-1] + BOUNCE
    win = max(0.0, t - stops[-1] - BOUNCE * 0.5)
    flash = 0.75 if settled and win < 0.55 and int(win * 14) % 2 == 0 else 0.0
    seed = zlib.crc32(str(event.get("id", "jev")).encode())

    rows = Rows(width, height)
    header = rows.add(0)
    point = LABELS.get(event.get("point"), _plain(event.get("point") or "判断"))
    title = f"{point} · {event.get('task_title') or '无'}"
    header.put(0, title, PURPLE)
    if tag and width > cell_width(tag) + 8:
        header.right(tag, PINK)
    question = rows.add(2)
    question.put(0, event.get("question") or "", GRAY)
    rows.add(5)
    rows.add(4)

    # Border rows carry the bulbs; they chase while spinning and blink together after the win.
    def border(left_char, fill, right_char, row, phase):
        row.put(left, left_char + fill * inner + right_char,
                runs=[(c, c + 1, _neon(c, left, box, flash)) for c in range(left, left + box)])
        for index, col in enumerate(range(left + 2, left + box - 2, 3)):
            if settled:
                lit = int(win * 8) % 2 == 0
            else:
                lit = (index + int(t * 16) + phase) % 3 == 0
            color = (GOLD if index % 2 else HOT) if lit else (70, 50, 90)
            row.put(col, "●", color)

    top = rows.add(1)
    border("╔", "═", "╗", top, 0)
    offsets = (-2, -1, 0, 1, 2) if height >= 12 else (-1, 0, 1)
    reel_rows = {}
    for offset in offsets:
        reel_rows[offset] = rows.add(3 if abs(offset) == 2 else (1 if offset else 0))
    bottom = rows.add(1)
    border("╚", "═", "╝", bottom, 1)

    n = len(names)
    for reel in range(reels):
        stop = stops[reel]
        start_index = (seed >> (reel * 3)) % n
        # Whole spins plus the distance from where this reel starts to the chosen name.
        travel = n * (6 + 2 * reel) + (target - start_index) % n
        position, speed = _reel_position(t, stop, travel)
        blur = min(1.0, max(0.0, (speed - 6) / 30))
        landed = t >= stop + BOUNCE * 0.6
        center_index = start_index + round(position)
        x0 = left + 1 + reel * (REEL_WIDTH + 1)
        for offset, row in reel_rows.items():
            item = (center_index - offset) % n
            name = names[item]
            mark, brand = icon(keys[item])
            room = REEL_WIDTH - 2 - (2 if mark else 0)
            label = name if cell_width(name) <= room else name[:room - 1] + "…"
            start = x0 + (REEL_WIDTH - cell_width(label) - (2 if mark else 0)) // 2
            col = start + (2 if mark else 0)
            if offset == 0 and landed and settled:
                shimmer = (win * 1.6) % 1.4 - 0.2
                runs = []
                for i, (c, end, color) in enumerate(gradient(col, label, PINK, GOLD)):
                    pos = i / max(1, len(label) - 1)
                    runs.append((c, end, mix(color, WHITE, max(0.0, 1 - abs(pos - shimmer) * 4))))
                row.put(col, label, runs=runs)
            elif offset == 0:
                base = WHITE if landed else mix(WHITE, (150, 120, 200), blur * 0.7)
                row.put(col, label, base)
            elif settled and abs(offset) == 1:
                # The winning row is framed by two glowing lines.
                pulse = 0.5 + 0.5 * math.cos(win * 12)
                line = ("▁" if offset < 0 else "▔") * (REEL_WIDTH - 2)
                row.put(x0 + 1, line, runs=[(c, c + 1, mix(_neon(c, left, box), WHITE, 0.4 * pulse))
                                             for c in range(x0 + 1, x0 + REEL_WIDTH - 1)])
            elif settled:
                row.put(col, label, (34, 30, 46))
            else:
                fade = 0.42 if abs(offset) == 1 else 0.2
                color = mix((20, 18, 30), mix(GRAY, PURPLE, 0.5), fade * (1 - 0.4 * blur))
                if blur > 0.55 and not landed:
                    # Motion blur: neighbouring names smear into vertical streaks.
                    streak = "".join("│" if ch != " " else " " for ch in label)
                    row.put(col, streak, mix((20, 18, 30), PURPLE, 0.35 * blur))
                else:
                    row.put(col, label, color)
            if mark and not (settled and abs(offset) == 1):
                if offset == 0:
                    glow = brand if landed else mix(brand, (60, 50, 80), blur * 0.6)
                elif settled:
                    glow = (34, 30, 46)
                else:
                    glow = mix((20, 18, 30), brand, (0.5 if abs(offset) == 1 else 0.25) * (1 - 0.6 * blur))
                row.put(start, "│" if blur > 0.55 and offset and not landed else mark, glow)
            if reel:
                row.put(x0 - 1, "│", (90, 60, 130))
    for offset, row in reel_rows.items():
        side = [(c, c + 1, _neon(c, left, box, flash)) for c in (left, left + box - 1)]
        row.put(left, "║", runs=side)
        row.put(left + box - 1, "║", runs=side)
        if offset == 0:
            pointer = mix(PINK, WHITE, flash) if settled else PINK
            row.put(left - 2, "▶", pointer)
            row.put(left + box + 1, "◀", pointer)
    rows.add(4)

    status = rows.add(0)
    if settled:
        confidence = event.get("confidence")
        valid = isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        text = f"✦ JEV 选中 · 把握 {confidence:.2f} ✦" if valid else "✦ JEV 选中 ✦"
        shown = text[:max(1, int(len(text) * min(1.0, win / 0.25)))]
        col = max(0, (width - cell_width(text)) // 2)
        status.put(col, shown, runs=gradient(col, text, PINK, GOLD))
    else:
        text = f"{SPINNER[int(t * 15) % len(SPINNER)]}  JEV 推理中…"
        status.put(max(0, (width - cell_width(text)) // 2), text, PURPLE)

    rows.add(5)
    rows.add(5)
    frame_rows = [row for _, row in rows.items]
    if settled and win < 1.1:
        # Sparks start at the edge of the reel box and fly outwards, fading.
        rng = random.Random(seed)
        cy = frame_rows.index(reel_rows[0])
        cx = left + box / 2
        rx, ry = box / 2 + 1, len(offsets) / 2 + 1
        for i in range(26):
            angle = rng.uniform(0, 2 * math.pi)
            speed = rng.uniform(0.6, 1.4) * (1 - math.exp(-win * 5))
            x = round(cx + math.cos(angle) * (rx + 12 * speed))
            y = round(cy + math.sin(angle) * (ry + 3 * speed))
            if not 0 <= y < len(frame_rows) or not 0 <= x < width:
                continue
            inside = left - 2 <= x <= left + box + 1 and abs(y - cy) <= len(offsets) // 2 + 1
            if inside or frame_rows[y] in (header, question, status):
                continue
            fade = max(0.0, 1 - win / 1.1)
            color = mix((20, 18, 30), GOLD if i % 3 == 0 else (HOT if i % 3 == 1 else PURPLE), fade)
            frame_rows[y].put(x, SPARK_CHARS[i % len(SPARK_CHARS)], color)
    frame = rows.frame(top_margin=0)
    if settled:
        kept = rows.kept
        frame.selected_rows = {i for i, row in enumerate(kept) if row is reel_rows[0]}
    frame.slot = True
    return frame
