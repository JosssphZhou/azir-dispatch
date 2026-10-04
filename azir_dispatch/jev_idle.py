"""Standby frame of the JEV view, shown before the first decision is recorded."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib

from .agent_icons import put_icon
from .jev_view import SPINNER, cell_width, short_name
from .view_output import DIM, GRAY, PINK, PURPLE, WHITE, Rows, gradient, mix

DEFAULT_CONFIG = Path.home() / "projects/azir/config/azir-dispatch.toml"
THRESHOLD = 0.70
# Prices are model metadata, not part of the config: USD per million tokens, input / output.
PRICES = (("6.1-sol", "$2.00 / $10.00"), ("luna", "$0.10 / $0.50"), ("opus", "Claude 订阅"),
          ("gemini", "Google 订阅"), ("grok", "SuperGrok 订阅"))
# Used only when the config file cannot be read.
FALLBACK_CRITERIA = {
    "codex-cli:gpt-6.1-sol:high": "复杂任务：修改代码逻辑、开发新功能、修复故障、代码审查",
    "codex-cli:gpt-6-luna:medium": "简单任务：只读统计、查找内容、整理清单、改文档措辞",
    "claude:claude-opus-5-5:high": "规划和前端类任务：写规格、拆任务、架构方案",
}


def load_dispatch(config=None):
    """(criteria, threshold) of the dispatch point from a TOML path or a parsed dict."""
    try:
        if not isinstance(config, dict):
            config = tomllib.loads(Path(config or DEFAULT_CONFIG).read_text(encoding="utf-8"))
        point = config["points"]["dispatch"]
        criteria = point["criteria"]
        if not isinstance(criteria, dict) or not criteria:
            raise ValueError
        threshold = float(point.get("threshold", THRESHOLD))
        return {str(k): str(v) for k, v in criteria.items()}, threshold
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
        return dict(FALLBACK_CRITERIA), THRESHOLD


def price(key):
    lowered = str(key).lower()
    return next((text for needle, text in PRICES if needle in lowered), "")


def condition(description, width):
    """Category and leading items of the description, cut only at item boundaries."""
    text = str(description).strip()
    head, _, body = text.partition("：")
    clause = re.split(r"[，,。；;（(]", body or head, maxsplit=1)[0]
    items = [item.strip() for item in clause.split("、") if item.strip()]
    for prefix in ((head + "：") if body else "", ""):
        out = ""
        for item in items:
            candidate = f"{out}、{item}" if out else prefix + item
            if cell_width(candidate) > width:
                break
            out = candidate
        if out:
            return out
    return head if cell_width(head) <= width else ""


def draw_standby(view):
    width, elapsed = view.width, view.elapsed
    criteria, threshold = view.dispatch_criteria
    rows = Rows(width, view.height)

    head = rows.add(0)
    head.put(0, SPINNER[int(elapsed * 10) % len(SPINNER)], PURPLE)
    title = "JEV 待命"
    head.put(2, title, runs=gradient(2, title))
    count = f"今日判断 {view.today} 次"
    if width >= cell_width(count) + 14:
        head.right(count, GRAY)

    rows.add(5)
    rule = rows.add(3)
    rule.put(0, "─" * width, runs=gradient(0, "─" * width, (90, 70, 140), (140, 60, 100)))

    tiers = [(key, short_name(key), description, price(key)) for key, description in criteria.items()]
    name_width = max(cell_width(name) for _, name, _, _ in tiers) + 4
    show_price = width >= 44
    price_width = max(cell_width(text) for _, _, _, text in tiers) if show_price else 0
    cond_col = 2 + name_width
    cond_width = width - cond_col - (price_width + 2 if show_price else 0)
    caption = rows.add(3)
    caption.put(0, "候选执行者", DIM)
    if show_price:
        caption.right("每百万 token 输入 / 输出", DIM)
    # A light runs down the candidate list while JEV waits.
    scan = (elapsed * 3.2) % (len(tiers) + 3) - 1
    for index, (key, name, description, cost) in enumerate(tiers):
        glow = max(0.0, 1.0 - abs(index - scan) / 1.4)
        row = rows.add(0)
        row.put(0, "›", mix((44, 46, 58), PINK, glow))
        row.put(put_icon(row, 2, key), name, mix(WHITE, PINK, glow * 0.7))
        if cond_width >= 6:
            row.put(cond_col, condition(description, cond_width), mix(DIM, GRAY, glow))
        if show_price and cost:
            row.right(cost, GRAY)

    rows.add(4)
    gauge = rows.add(0)
    gauge.put(0, "把握", GRAY)
    track_col, track_width = 5, max(4, width - 9)
    marker = track_col + round((track_width - 1) * threshold)
    gauge.put(track_col, "▆" * track_width, (44, 46, 58))
    gauge.put(marker, "┃", PINK)
    gauge.right("—", DIM, margin=1)
    label = rows.add(1)
    text = f"▲ 把握线 {threshold:.2f}"
    col = marker if marker + cell_width(text) <= width else max(0, marker + 1 - cell_width(text))
    if col != marker:
        text = f"把握线 {threshold:.2f} ▲"
    label.put(col, text, PINK)
    return rows.frame(top_margin=max(0, (view.height - len(rows.items)) // 2))
