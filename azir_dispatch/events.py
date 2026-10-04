import fcntl
import json
import os
import stat
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

from .redact import redact_value


class RecordError(OSError):
    pass


def _prepare_directory(parent):
    missing = []
    cursor = parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            os.chmod(directory, 0o700)
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        warnings.warn("existing log directory permissions are broader than 0700", RuntimeWarning, stacklevel=2)


def append_event(log, kind, *, run_id=None, actor=None, task_id=None, caused_by=None, deadline=None, **fields):
    event = {"v": 1, "id": uuid.uuid4().hex,
             "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
             "type": kind, "run_id": run_id or uuid.uuid4().hex,
             "task_id": task_id, "caused_by": caused_by,
             "actor": actor or "unknown", **fields}
    event = redact_value(event)
    path = Path(log).expanduser()
    _prepare_directory(path.parent)
    line = (json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if deadline is not None and time.monotonic() >= deadline:
            raise RecordError("record deadline exceeded")
        lock_deadline = min(time.monotonic() + 0.5, deadline) if deadline else time.monotonic() + 0.5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= lock_deadline:
                    raise RecordError("record lock timeout") from exc
                time.sleep(min(0.01, max(0, lock_deadline - time.monotonic())))
        size = os.fstat(fd).st_size
        if deadline is not None and time.monotonic() >= deadline:
            raise RecordError("record deadline exceeded")
        if size and os.pread(fd, 1, size - 1) != b"\n":
            os.write(fd, b"\n")
        if os.write(fd, line) != len(line):
            raise RecordError("short event write")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return event
