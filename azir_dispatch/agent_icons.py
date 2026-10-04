"""Brand icons for executors: a one-cell inline mark and a 3-row half-block card logo."""

from __future__ import annotations

from .view_output import mix

CLAUDE = (217, 119, 87)      # #D97757
SOL = (16, 163, 127)         # #10A37F
LUNA = (128, 222, 234)
GEMINI_A, GEMINI_B = (66, 133, 244), (161, 66, 244)  # #4285F4 → #A142F4
GROK = (236, 238, 242)
CODEX = (200, 205, 212)

# 6 pixel rows each, drawn two pixel rows per terminal row with ▀ ▄ █.
LOGOS = {
    "claude": (".#.#.#.",
               "..###..",
               "#######",
               "..###..",
               ".#.#.#.",
               "......."),
    "openai": ("..###..",
               ".##.##.",
               "##...##",
               "##...##",
               ".##.##.",
               "..###.."),
    "gemini": ("...#...",
               "..###..",
               "#######",
               "#######",
               "..###..",
               "...#..."),
    "grok":   ("..###.#",
               ".#..##.",
               "#..#..#",
               "#.#...#",
               ".##..#.",
               "#.###.."),
}
LOGO_WIDTH, LOGO_HEIGHT = 7, 3


def family(key):
    """Brand family of an executor key such as "codex-cli:gpt-6.1-sol:high" or a short name."""
    lowered = str(key).lower()
    if "claude" in lowered or "opus" in lowered or "sonnet" in lowered or "haiku" in lowered:
        return "claude"
    if "gemini" in lowered:
        return "gemini"
    if "grok" in lowered:
        return "grok"
    if any(word in lowered for word in ("codex", "gpt", "sol", "luna", "openai")):
        return "openai"
    return None


def color(key, t=0.5):
    """Brand color; t picks a point on the Gemini gradient."""
    lowered = str(key).lower()
    name = family(key)
    if name == "claude":
        return CLAUDE
    if name == "gemini":
        return mix(GEMINI_A, GEMINI_B, t)
    if name == "grok":
        return GROK
    if name == "openai":
        return LUNA if "luna" in lowered else (SOL if "sol" in lowered else CODEX)
    return None


ICONS = {"claude": "✻", "openai": "❂", "gemini": "✦", "grok": "⊘"}


def icon(key):
    """(character, color) of the one-cell icon, or ("", None) for unknown executors."""
    name = family(key)
    return (ICONS[name], color(key)) if name else ("", None)


def put_icon(row, col, key):
    """Write the inline icon and a space; returns the column after them."""
    char, rgb = icon(key)
    if not char:
        return col
    row.put(col, char, rgb)
    return col + 2


def logo_rows(key):
    """Three strings plus per-cell colors for the card logo, or None for unknown executors."""
    name = family(key)
    if name is None:
        return None
    pixels = LOGOS[name]
    rows = []
    for top, bottom in zip(pixels[0::2], pixels[1::2]):
        text = "".join("█" if a == "#" and b == "#" else "▀" if a == "#" else "▄" if b == "#" else " "
                       for a, b in zip(top, bottom))
        colors = [color(key, i / (LOGO_WIDTH - 1)) for i in range(LOGO_WIDTH)]
        rows.append((text, colors))
    return rows


def put_logo(rows, col, key):
    """Draw the card logo into three Row objects starting at col."""
    logo = logo_rows(key)
    if logo is None:
        return
    for row, (text, colors) in zip(rows, logo):
        for i, ch in enumerate(text):
            if ch != " ":
                row.put(col + i, ch, colors[i])
