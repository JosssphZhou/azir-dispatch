"""Atomic shared installation progress; never store credential values."""
from datetime import datetime, timezone
import json
import os

from .setup_io import atomic, guarded, directory_path

STEPS = ('environment', 'herdr', 'repo', 'models', 'board', 'first_dispatch')


def state_directory():
    return directory_path(os.environ.get('AZIR_DISPATCH_STATE_DIR', '~/.local/state/azir-dispatch'))


def update_state(*, refresh=True, **fields):
    path = state_directory() / 'setup-state.json'
    raw = guarded(path)
    try:
        previous = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        previous = {}
    # Retain only the public schema, so a stale/foreign file cannot copy secrets.
    steps = {name: 'pending' for name in STEPS}
    if isinstance(previous, dict) and isinstance(previous.get('steps'), dict):
        for name in STEPS:
            value = previous['steps'].get(name)
            if value in ('ok', 'missing', 'failed', 'pending'):
                steps[name] = value
    steps.update(fields.pop('steps', {}))
    value = dict(version=1, updated_at=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                 steps=steps)
    for name in ('agents', 'keys', 'roles', 'jev_mode', 'hints', 'tools'):
        if name in fields:
            value[name] = fields[name]
    if not refresh and isinstance(previous, dict):
        if {k: v for k, v in previous.items() if k != 'updated_at'} == {k: v for k, v in value.items() if k != 'updated_at'}:
            return previous
    # Callers that advance a step refresh detection too, rather than reusing
    # arbitrary strings from an older state file.
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
    return value
