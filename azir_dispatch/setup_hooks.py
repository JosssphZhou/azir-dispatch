"""Merge only hook entries proven to belong to this installation."""
import copy
import json
import shlex
import shutil
import subprocess
import sys

from .setup_config import ROOT, SetupError, clean_environment
from .setup_io import Change, digest, guarded, directory_path

EVENTS = ('PreToolUse', 'PostToolUse', 'PostToolUseFailure')
MATCHER = 'Agent|Task'


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate key')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('nonstandard JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def hook_fragment(config_path):
    command = shlex.join([sys.executable, str(ROOT / 'adapters/claude_code/hook.py'), '--config', str(config_path)])
    return {event: [dict(matcher=MATCHER, hooks=[dict(type='command', command=command, timeout=10)])] for event in EVENTS}


def fingerprint(entry):
    return digest(json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())


def ignored(project, target):
    git = shutil.which('git')
    if not git:
        return 'not_checked_git_missing'
    try:
        result = subprocess.run([git, '-C', str(project), 'check-ignore', '--quiet', '--', str(target)],
                                env=clean_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        return 'ignored' if result.returncode == 0 else 'not_ignored' if result.returncode == 1 else 'not_checked_not_git_project'
    except (OSError, subprocess.SubprocessError):
        return 'not_checked'


def plan_hooks(project, scope, config_path, state):
    project = directory_path(project)
    if not project.is_dir():
        raise SetupError('hooks project must be an existing directory')
    target = project / '.claude' / ('settings.json' if scope == 'shared' else 'settings.local.json')
    old = guarded(target)
    fragment = hook_fragment(config_path)
    try:
        settings = strict_json(old) if old is not None else {}
        if not isinstance(settings, dict):
            raise ValueError('settings root must be an object')
        hooks = settings.get('hooks', {})
        if not isinstance(hooks, dict):
            raise ValueError('hooks must be an object')
        for entries in hooks.values():
            if not isinstance(entries, list):
                raise ValueError('hook events must contain arrays')
            for entry in entries:
                if (not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list)
                        or ('matcher' in entry and not isinstance(entry['matcher'], str))
                        or any(not isinstance(item, dict) for item in entry['hooks'])):
                    raise ValueError('invalid hook entry')
    except (ValueError, TypeError, UnicodeError):
        raise SetupError('strict JSON settings cannot be merged; preserve the original and merge manually',
                         path=str(target), manual_merge={'hooks': fragment}) from None
    installs_path = state / 'installs.json'
    installs_old = guarded(installs_path)
    try:
        installs = strict_json(installs_old) if installs_old is not None else {'version': 1, 'entries': []}
        if not isinstance(installs, dict) or set(installs) != {'version', 'entries'} or installs['version'] != 1 or not isinstance(installs['entries'], list):
            raise ValueError('invalid installs record')
        for record in installs['entries']:
            if not isinstance(record, dict) or set(record) != {'target', 'event', 'matcher', 'command', 'fingerprint'} or not all(isinstance(value, str) for value in record.values()):
                raise ValueError('invalid install entry')
    except (ValueError, TypeError):
        raise SetupError('cannot read installation ownership record') from None
    settings = copy.deepcopy(settings)
    hooks = settings.setdefault('hooks', {})
    owned = [entry for entry in installs['entries'] if entry['target'] == str(target)]
    for record in owned:
        if record['event'] not in EVENTS:
            raise SetupError('unrecognized owned hook event')
        matches = [entry for entry in hooks.get(record['event'], []) if fingerprint(entry) == record['fingerprint']]
        if len(matches) != 1:
            raise SetupError('installed azir hook changed or disappeared; refusing to overwrite', path=str(target))
    preserved = sum(len(entries) for entries in hooks.values()) - len(owned)
    # Keep each owned entry's position; new entries append after all existing hooks.
    records = [entry for entry in installs['entries'] if entry['target'] != str(target)]
    for event in EVENTS:
        wanted = fragment[event][0]
        prior = [entry for entry in owned if entry['event'] == event]
        if len(prior) > 1:
            raise SetupError('duplicate ownership records')
        entries = hooks.setdefault(event, [])
        if prior:
            index = next(index for index, entry in enumerate(entries) if fingerprint(entry) == prior[0]['fingerprint'])
            entries[index] = wanted
        elif wanted in entries:
            # Without a record, do not claim ownership of a user's identical hook.
            continue
        else:
            entries.append(wanted)
        records.append(dict(target=str(target), event=event, matcher=MATCHER,
                            command=wanted['hooks'][0]['command'], fingerprint=fingerprint(wanted)))
    new = old if settings == (strict_json(old) if old is not None else {}) else (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode()
    installs_new = installs_old if records == installs['entries'] else (json.dumps(dict(version=1, entries=records), ensure_ascii=False, indent=2) + '\n').encode()
    changes = [Change(target, old, new)]
    if installs_new is not None:
        changes.append(Change(installs_path, installs_old, installs_new))
    return changes, dict(project=str(project), target=str(target), scope=scope,
                         preserved_existing_entries=preserved,
                         git_ignore=ignored(project, target), status='planned_real_session_unverified')
