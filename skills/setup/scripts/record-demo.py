#!/usr/bin/env python3
"""Record the real demo in a bounded PTY without touching existing sessions."""
import argparse
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=3)
    args = parser.parse_args()
    if not 0 < args.duration <= 60:
        parser.error("duration must be between 0 and 60 seconds")
    root = Path(__file__).resolve().parents[3]
    # Exclusive creation protects an earlier recording, even through a symlink.
    with args.output.open("xb") as capture:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 52, 120, 0, 0))
        env = dict(os.environ, TERM="xterm-256color")
        try:
            worker = subprocess.Popen(
                [sys.executable, str(root / "bin/azir-dispatch"), "demo"],
                cwd=root, stdin=slave, stdout=slave, stderr=slave,
                env=env, start_new_session=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        deadline = time.monotonic() + args.duration
        stopped = False
        try:
            while True:
                if not stopped and time.monotonic() >= deadline:
                    if worker.poll() is None:
                        os.write(master, b"q")
                    stopped = True
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    capture.write(data)
                    if sys.stdout.isatty():
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                elif worker.poll() is not None:
                    break
                if stopped and time.monotonic() > deadline + 5:
                    raise TimeoutError("demo did not stop after q")
            code = worker.wait(timeout=5)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
            os.close(master)
        if code:
            print("Demo failed; inspect the terminal recording.", file=sys.stderr)
            return code if code > 0 else 1
    if args.output.stat().st_size == 0:
        print("Demo produced no terminal output.", file=sys.stderr)
        return 1
    print(f"Demo recorded: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
