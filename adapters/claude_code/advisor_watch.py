#!/usr/bin/env python3
"""Export advisor call metadata from read-only Claude Code transcripts."""
import argparse
import fcntl
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adapters.common import add_options, load_settings, read_events, record


def advisor_entries(row):
    if not isinstance(row, dict):
        return
    content = row.get('message', {}).get('content', []) if isinstance(row.get('message'), dict) else []
    candidates = [row] + (content if isinstance(content, list) else [])
    for item in candidates:
        if isinstance(item, dict) and item.get('type') == 'server_tool_use' and item.get('name') == 'advisor' and isinstance(item.get('id'), str) and item['id']:
            yield dict(advisor_call_id=item['id'], session=row.get('sessionId', ''), cwd=row.get('cwd', ''), advisor_ts=row.get('timestamp', row.get('ts')))


def scan(directory, config, log):
    directory = Path(directory).expanduser()
    if log.resolve().is_relative_to(directory.resolve()):
        raise ValueError('event log must be outside the sessions directory')
    if not directory.is_dir():
        raise ValueError('sessions directory must exist')
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Deduplication uses the destination itself; the lock serializes exporters.
    with Path(str(log) + '.advisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seen = {e.get('advisor_call_id') for e in read_events(log) if e.get('type') == 'advisor'}
        added = 0
        skipped = 0
        for path in sorted(directory.rglob('*.jsonl')):
            try:
                with path.open() as stream:
                    for line in stream:
                        if not line.endswith('\n'):
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            skipped += 1
                            continue
                        for entry in advisor_entries(row):
                            call_id = entry['advisor_call_id']
                            if call_id in seen:
                                continue
                            timestamp = entry.pop('advisor_ts')
                            if isinstance(timestamp, str):
                                from datetime import datetime
                                try:
                                    parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                    if parsed.tzinfo is not None:
                                        entry['ts'] = timestamp
                                except ValueError:
                                    pass
                            record(log, 'advisor', config=config, run_id=f'advisor:{call_id}', actor='claude-code', **entry)
                            seen.add(call_id)
                            added += 1
            except (OSError, UnicodeError):
                skipped += 1
        return {'added': added, 'skipped': skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    parser.add_argument('--sessions-dir', default='~/.claude/projects/')
    args = parser.parse_args(argv)
    try:
        config, log = load_settings(args)
        result = scan(args.sessions_dir, config, log)
    except (OSError, ValueError):
        parser.error('cannot read sessions or write the event log')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
