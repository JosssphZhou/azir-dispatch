"""Deadline-bound redaction for untrusted extra regular expressions."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class FilterTimeout(Exception):
    pass


class FilterError(Exception):
    pass


def filter_value(value, patterns, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0.1:
        raise FilterTimeout
    try:
        body = json.dumps({"value": value, "patterns": patterns}, ensure_ascii=False)
        worker = Path(__file__).with_name("_redact_worker.py")
        process = subprocess.Popen([sys.executable, str(worker)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            stdout, _ = process.communicate(body.encode("utf-8"), timeout=max(0.001, deadline - time.monotonic() - 0.1))
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise FilterTimeout from exc
        if process.returncode != 0:
            raise FilterError
        return json.loads(stdout)
    except FilterTimeout:
        raise
    except Exception as exc:
        raise FilterError from exc
