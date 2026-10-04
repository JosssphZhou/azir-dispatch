"""Read setup-state.json, written by the setup checker, for the board.

The fields are listed in docs/dashboard.md. A missing, unreadable or malformed
file reads as "setup not started"; the board never raises because of it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

STEPS = (("environment", "环境"), ("herdr", "herdr"), ("repo", "仓库"),
         ("models", "模型"), ("board", "调度图"), ("first_dispatch", "首次派发"))
STEP_STATUSES = {"ok", "missing", "failed", "pending"}
# Executor cards when the file exists: one per role that does the work.
ROLE_CARDS = (("dev", "开发"), ("review", "审查"), ("research", "调研"))
ROLE_STATUSES = {"ok", "missing", "optional_unconfigured"}
EXECUTOR_NAMES = {"claude": "Claude", "claude-code": "Claude", "codex": "Codex", "codex-cli": "Codex",
                  "grok": "Grok", "gemini": "Gemini", "cursor-agent": "Cursor"}
FLASH_SECONDS = 1.2


def default_path():
    root = os.environ.get("AZIR_DISPATCH_STATE_DIR") or Path.home() / ".local/state/azir-dispatch"
    return Path(root).expanduser() / "setup-state.json"


def model_label(model):
    """claude-opus-5-5 → Opus 5.5, gpt-6.1-sol → GPT-6.1 Sol; other names stay as written."""
    model = str(model or "")
    parts = model.split("-")
    if len(parts) >= 2 and parts[0].lower() == "claude":
        name = parts[1].capitalize()
        version = ".".join(part for part in parts[2:] if part.isdigit())
        return f"{name} {version}".strip()
    if len(parts) >= 2 and parts[0].lower() == "gpt":
        return " ".join(["GPT-" + parts[1]] + [part.capitalize() for part in parts[2:]])
    if len(parts) >= 2 and parts[0].isalpha():
        return " ".join(part.capitalize() if part.isalpha() else part for part in parts)
    return model


def executor_label(executor):
    executor = str(executor or "")
    return EXECUTOR_NAMES.get(executor.lower(), executor)


class SetupState:
    """Follow one setup-state.json; poll() rereads it whenever the file changes."""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.identity = ()  # never equal to a real stat, so the first poll reads
        self.data = None
        self.broken = False
        self.lit = {}
        self._marks = None

    def poll(self, now):
        """Reread on a new inode, size or mtime; True when the file changed."""
        try:
            stat = self.path.stat()
            identity = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            identity = None
        if identity == self.identity:
            return False
        self.identity = identity
        data, broken = None, False
        if identity is not None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                broken = True
            else:
                data = raw if isinstance(raw, dict) else None
                broken = data is None
        self.data, self.broken = data, broken
        marks = self._done_marks()
        if self._marks is not None:
            # Light up only what turned ok since the last read, not what was ok on opening.
            for key, value in marks.items():
                if value and self._marks.get(key) != value:
                    self.lit[key] = now
        self._marks = marks
        return True

    def _done_marks(self):
        marks = {f"step:{key}": status == "ok" for key, status in self.steps().items()}
        for key, role in self.roles().items():
            marks[f"role:{key}"] = role["model"] if role["status"] == "ok" else ""
        return marks

    def flash(self, key, now):
        """1 right after the item turned ok, fading to 0."""
        started = self.lit.get(key)
        if started is None or now < started:
            return 0.0
        return max(0.0, 1.0 - (now - started) / FLASH_SECONDS)

    @property
    def started(self):
        return self.data is not None

    def _section(self, name):
        value = (self.data or {}).get(name)
        return value if isinstance(value, dict) else {}

    def steps(self):
        steps = self._section("steps")
        return {key: steps.get(key) if steps.get(key) in STEP_STATUSES else "pending"
                for key, _ in STEPS}

    @property
    def complete(self):
        return self.started and all(status == "ok" for status in self.steps().values())

    def next_step(self):
        """(key, label, status) of the first step that is not ok, or None."""
        steps = self.steps()
        return next(((key, label, steps[key]) for key, label in STEPS if steps[key] != "ok"), None)

    def agents(self):
        """(name, found) in file order; the keys are whatever the checker reported."""
        return [(str(name), value is True) for name, value in self._section("agents").items()]

    def roles(self):
        roles = {}
        for key, value in self._section("roles").items():
            if not isinstance(value, dict):
                continue
            status = value.get("status") if value.get("status") in ROLE_STATUSES else "missing"
            roles[str(key)] = {"status": status,
                               "executor": str(value.get("executor") or ""),
                               "model": str(value.get("model") or ""),
                               "fallback": value.get("fallback") is True}
        return roles

    @property
    def rules_mode(self):
        return (self.data or {}).get("jev_mode") == "rules"

    def hint(self, key):
        value = self._section("hints").get(key)
        return value if isinstance(value, str) else ""
