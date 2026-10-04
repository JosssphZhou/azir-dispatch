#!/usr/bin/env python3
"""Local rolling seven-day budget. Store metadata only, under dispatch state."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from azir_dispatch.advisor import settings
from azir_dispatch.doctor import load_config
from azir_dispatch.events import append_event
from azir_dispatch.setup_io import guarded, private_directory
from azir_dispatch.setup_state import state_directory

WINDOW = dt.timedelta(days=7)
EXIT_BLOCKED = 3
CONFIG = None


def configure(path=None):
    global CONFIG
    CONFIG = load_config(path)
    settings(CONFIG)


def config():
    if CONFIG is None:
        configure()
    return CONFIG


def usage_file():
    return str(state_directory() / 'advisor-chatgpt-pro-usage.jsonl')


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('usage timestamps must include timezone')
    return result


def load(path):
    raw = guarded(Path(path))
    sends, updates, cancelled = {}, {}, set()
    for line in (raw or b'').decode('utf-8').splitlines():
        record = json.loads(line)  # Broken accounting must block, not reduce the count.
        if 'cancel' in record:
            cancelled.add(record['cancel'])
        elif 'update' in record:
            updates[record['update']] = record['session']
        elif 'id' in record:
            parse_time(record['time'])
            sends[record['id']] = record
        else:
            raise ValueError('invalid usage record')
    return [{**record, 'session': updates.get(rid, record['session'])}
            for rid, record in sends.items() if rid not in cancelled]


def in_window(records, at=None):
    at = at or now()
    return [r for r in records if at - WINDOW < parse_time(r['time'])]


def summary(records, at=None):
    used = len(in_window(records, at))
    limit = settings(config())['weekly_limit']
    return used, f'本周（滚动 7 天）已用 {used} 次，剩 {max(limit - used, 0)} 次，上限 {limit} 次。'


class Locked:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        guarded(self.path)
        private_directory(self.path.parent)
        lock = self.path.with_suffix('.lock')
        guarded(lock)
        self.fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(self.fd, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def append(path, record):
    guarded(Path(path))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        data = (json.dumps(record, separators=(',', ':')) + '\n').encode()
        if os.write(fd, data) != len(data):
            raise OSError('short usage write')
        os.fsync(fd)
    finally:
        os.close(fd)


def reserve(session):
    path = usage_file()
    with Locked(path):
        used, message = summary(load(path))
        if used >= settings(config())['weekly_limit']:
            return None, message + '拒绝发送。'
        rid = uuid.uuid4().hex
        append(path, dict(id=rid, time=now().isoformat(), session=str(session), source='chatgpt-pro'))
        return rid, message


def confirm(rid, url):
    match = re.fullmatch(r'https://chatgpt\.com/c/([0-9a-fA-F-]{36})', url)
    session = match[1] if match else rid  # Uncertain sends retain their reservation.
    path = usage_file()
    with Locked(path):
        append(path, dict(update=rid, session=session, time=now().isoformat()))
        cfg = config()
        log = os.environ.get('AZIR_DISPATCH_LOG') or cfg.get('log', {}).get('path') or str(state_directory() / 'events.jsonl')
        append_event(log, 'advisor', run_id=rid, actor='advisor', source='chatgpt-pro', session=session)


def cancel(rid, reason):
    path = usage_file()
    with Locked(path):
        # Reasons from page errors can contain material; retain only cancellation metadata.
        append(path, dict(cancel=rid, time=now().isoformat()))


def main():
    parser = argparse.ArgumentParser(description='GPT Pro 本地每周用量')
    parser.add_argument('action', choices=['status', 'check'])
    parser.add_argument('--config')
    args = parser.parse_args()
    configure(args.config)
    with Locked(usage_file()):
        used, message = summary(load(usage_file()))
    print(message)
    return EXIT_BLOCKED if args.action == 'check' and used >= settings(config())['weekly_limit'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError):
        print('无法读取用量或配置；停止发送。', file=sys.stderr)
        raise SystemExit(2)
