"""Model lists read from each CLI, one-shot account tests, and the record of what was tested."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .roles import EXECUTOR_COMMANDS, command_path
from .setup_io import atomic, guarded
from .setup_state import state_directory

ECHO_PROMPT = 'Reply with exactly the single word: pong'
LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
# Claude Code accepts aliases for its latest models, so they are listed even though it has no list command.
CLAUDE_ALIASES = ('opus', 'sonnet', 'haiku')
CATALOG_TTL = 600
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')


def canonical(executor):
    return 'claude' if executor == 'claude-code' else executor


def _run(argv, timeout, stdin=subprocess.DEVNULL):
    return subprocess.run(argv, stdin=stdin, capture_output=True, text=True, timeout=timeout,
                          cwd=tempfile.gettempdir(), errors='replace')


def parse_codex(text):
    data = json.loads(text)
    models, efforts = [], {}
    for item in data.get('models', []):
        slug = item.get('slug')
        if not slug or item.get('visibility') not in (None, 'list'):
            continue
        models.append(slug)
        levels = [entry['effort'] for entry in item.get('supported_reasoning_levels', [])
                  if isinstance(entry, dict) and entry.get('effort')]
        if levels:
            efforts[slug] = levels
    return models, efforts


def parse_agy(text):
    return [line.split('\t')[0].strip() for line in text.splitlines() if '\t' in line and line.split('\t')[0].strip()], {}


def parse_cursor(text):
    models = []
    for line in ANSI.sub('', text).splitlines():
        match = re.match(r'^([A-Za-z0-9][\w.\-]*) - ', line.strip())
        if match:
            models.append(match[1])
    return models, {}


def parse_grok(text):
    return [m[1] for m in (re.match(r'^\s*[-*]\s+([\w.\-]+)', line) for line in text.splitlines()) if m], {}


LISTERS = {
    'codex': (['codex', 'debug', 'models'], parse_codex),
    'agy': (['agy', 'models'], parse_agy),
    'cursor-agent': (['cursor-agent', '--list-models'], parse_cursor),
    'grok': (['grok', 'models'], parse_grok),
}
# Claude Code and Gemini CLI have no list command. Efforts known from their --help.
EFFORT_LEVELS = {'claude': LEVELS, 'agy': LEVELS, 'grok': ('low', 'medium', 'high'), 'codex': LEVELS}


def probe(executor, timeout=12):
    """Return dict(listed, models, efforts). listed=False means the CLI has no list command or it failed."""
    executor = canonical(executor)
    result = dict(listed=False, models=[], efforts={})
    if executor == 'claude':
        result['models'] = list(CLAUDE_ALIASES)
        return result
    spec = LISTERS.get(executor)
    path = command_path(executor) if spec else None
    if not spec or not path:
        return result
    try:
        done = _run([path, *spec[0][1:]], timeout)
        models, efforts = spec[1](done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return result
    if done.returncode == 0 and models:
        result.update(listed=True, models=models, efforts=efforts)
    return result


def _cache_path():
    return state_directory() / 'model-catalog.json'


def catalogs(executors, use_cache=True):
    """Probe several executors in parallel; cache successes briefly so reopening the screen is quick."""
    executors = sorted({canonical(name) for name in executors})
    try:
        raw = guarded(_cache_path())
        cached = json.loads(raw) if raw and use_cache else {}
    except (OSError, ValueError, TypeError):
        cached = {}
    now = time.time()
    result, todo = {}, []
    for name in executors:
        entry = cached.get(name)
        if (isinstance(entry, dict) and now - entry.get('at', 0) < CATALOG_TTL
                and isinstance(entry.get('catalog'), dict) and entry['catalog'].get('listed')):
            result[name] = entry['catalog']
        else:
            todo.append(name)
    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            for name, catalog in zip(todo, pool.map(probe, todo)):
                result[name] = catalog
                if catalog['listed']:
                    cached[name] = dict(at=now, catalog=catalog)
        try:
            atomic(_cache_path(), json.dumps(cached).encode())
        except OSError:
            pass
    return result


def efforts_for(executor, model, catalog):
    executor = canonical(executor)
    per_model = (catalog or {}).get('efforts', {})
    levels = per_model.get(model) or EFFORT_LEVELS.get(executor, ())
    return ['inherit', *levels]


def argv_for_test(executor, model, effort):
    """One short non-interactive message, with no session kept where the CLI allows it."""
    executor = canonical(executor)
    path = command_path(EXECUTOR_COMMANDS[executor]) or EXECUTOR_COMMANDS[executor]
    model_args = lambda flag: [flag, model] if model != 'inherit' else []
    if executor == 'claude':
        return [path, '-p', ECHO_PROMPT, '--no-session-persistence', *model_args('--model'),
                *(['--effort', effort] if effort != 'inherit' else [])]
    if executor == 'codex':
        return [path, 'exec', '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', *model_args('-m'),
                *(['-c', f'model_reasoning_effort="{effort}"'] if effort != 'inherit' else []), ECHO_PROMPT]
    if executor == 'grok':
        return [path, '-p', ECHO_PROMPT, *model_args('-m'),
                *(['--reasoning-effort', effort] if effort != 'inherit' else [])]
    if executor == 'agy':
        return [path, '-p', ECHO_PROMPT, *model_args('--model'),
                *(['--effort', effort] if effort != 'inherit' else [])]
    if executor == 'cursor-agent':
        return [path, '-p', *model_args('--model'), ECHO_PROMPT]
    if executor == 'gemini':
        return [path, '-p', ECHO_PROMPT, '--skip-trust', *model_args('-m')]
    raise ValueError('executor cannot be tested')


LOGIN = re.compile(r'not (?:logged|signed) in|log ?in|sign ?in|unauthori[sz]ed|authenticat|api key|credential|401|\bauth\b', re.I)
NO_MODEL = re.compile(r'(?:model[^\n]{0,80}(?:not found|does not exist|unknown|invalid|not supported|unavailable|not available|no access|may not exist))'
                      r'|(?:(?:unknown|invalid|unsupported|no such)[^\n]{0,20}model)', re.I)


def classify(output, model, command, returncode):
    """Map CLI output to a stable reason key plus the values the message needs."""
    text = ANSI.sub('', output)
    if NO_MODEL.search(text):
        return 'model_not_found', dict(model=model)
    if LOGIN.search(text):
        return 'not_logged_in', dict(command=command)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return 'error', dict(detail=(lines[-1] if lines else f'exit {returncode}')[:160])


def run_test(executor, model, effort, timeout=120):
    """Send one echo message. Returns dict(ok, reason, values, seconds)."""
    executor = canonical(executor)
    command = EXECUTOR_COMMANDS.get(executor, executor)
    if executor not in ('claude', 'codex', 'grok', 'agy', 'cursor-agent', 'gemini'):
        return dict(ok=False, reason='error', values=dict(detail='this executor cannot be tested here'), seconds=0.0)
    if not command_path(command):
        return dict(ok=False, reason='command_missing', values=dict(command=command), seconds=0.0)
    started = time.monotonic()
    try:
        done = _run(argv_for_test(executor, model, effort), timeout)
    except subprocess.TimeoutExpired:
        return dict(ok=False, reason='timeout', values=dict(seconds=timeout), seconds=time.monotonic() - started)
    except OSError:
        return dict(ok=False, reason='command_missing', values=dict(command=command), seconds=0.0)
    seconds = time.monotonic() - started
    output = (done.stdout or '') + '\n' + (done.stderr or '')
    if done.returncode == 0 and re.search(r'\bpong\b', ANSI.sub('', done.stdout or ''), re.I):
        return dict(ok=True, reason=None, values={}, seconds=seconds)
    if done.returncode == 0:
        # The CLI answered but not with the echo word; the account and model work.
        reason, values = classify(output, model, command, 0)
        if reason == 'error':
            lines = [line.strip() for line in (done.stdout or '').splitlines() if line.strip()]
            return dict(ok=False, reason='unexpected', values=dict(detail=(lines[-1] if lines else 'empty')[:120]), seconds=seconds)
        return dict(ok=False, reason=reason, values=values, seconds=seconds)
    reason, values = classify(output, model, command, done.returncode)
    return dict(ok=False, reason=reason, values=values, seconds=seconds)


# --- record of tests -------------------------------------------------------

def check_key(executor, model, effort):
    return f'{canonical(executor)}:{model}:{effort}'


def _checks_path():
    return state_directory() / 'role-checks.json'


def load_checks():
    try:
        raw = guarded(_checks_path())
        value = json.loads(raw) if raw else {}
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_check(executor, model, effort, outcome):
    checks = load_checks()
    checks[check_key(executor, model, effort)] = dict(
        ok=bool(outcome['ok']), reason=outcome['reason'], values=outcome['values'],
        at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'))
    atomic(_checks_path(), (json.dumps(checks, ensure_ascii=False, indent=2) + '\n').encode())
