"""Portable command discovery and ordered role preferences."""
import copy
import os
from pathlib import Path
import shutil

AGENTS = ('claude', 'codex', 'cursor-agent', 'gemini', 'grok', 'agy')
KEYS = ('OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')
EXECUTOR_COMMANDS = {'claude-code': 'claude', **{name: name for name in AGENTS},
                     'gpt-pro': 'gpt-pro', 'chatgpt-pro': 'ego-browser'}


def command_path(command):
    path = shutil.which(command)
    if not path and command == 'grok':
        candidate = Path.home() / '.grok/bin/grok'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            path = str(candidate)
    return path


def discover():
    return {name: bool(command_path(name)) for name in AGENTS}


def option(executor, model='inherit', effort='inherit'):
    return dict(executor=executor, model=model, effort=effort)


CLAUDE = option('claude', 'claude-opus-5-5', 'high')
CODEX = option('codex', 'gpt-6.1-sol', 'high')
OTHERS = [option(name) for name in ('cursor-agent', 'gemini', 'grok', 'agy')]
DEFAULT_ROLES = {
    'main': dict(preferred=CLAUDE, fallbacks=[CODEX, *OTHERS]),
    'dev': dict(preferred=CODEX, fallbacks=[CLAUDE, *OTHERS]),
    'review': dict(preferred=CODEX, fallbacks=[CLAUDE, *OTHERS]),
    'research': dict(preferred=CODEX, fallbacks=[CLAUDE, *OTHERS]),
    'advisor': dict(preferred=option('chatgpt-pro', 'GPT Pro', 'pro'), fallbacks=[]),
}


def validate_roles(roles):
    if not isinstance(roles, dict) or set(roles) - set(DEFAULT_ROLES):
        raise ValueError('invalid roles')
    for settings in roles.values():
        if (not isinstance(settings, dict) or set(settings) != {'preferred', 'fallbacks'}
                or not isinstance(settings['fallbacks'], list)):
            raise ValueError('roles require preferred and fallbacks')
        for item in [settings['preferred'], *settings['fallbacks']]:
            if (not isinstance(item, dict) or set(item) - {'executor', 'model', 'effort'}
                    or not {'executor', 'model'} <= set(item)
                    or item['executor'] not in EXECUTOR_COMMANDS
                    or any(not isinstance(v, str) or not v or ':' in v for v in item.values())):
                raise ValueError('invalid role combination')


def resolve_roles(config=None, agents=None, *, advisor_ready=None):
    configured = (config or {}).get('roles', {})
    validate_roles(configured)
    agents = discover() if agents is None else agents
    result = {}
    for role, defaults in DEFAULT_ROLES.items():
        settings = configured.get(role, defaults)
        choices = [settings['preferred'], *settings['fallbacks']]
        for index, item in enumerate(choices):
            command = EXECUTOR_COMMANDS[item['executor']]
            available = agents.get(command, False) if command in AGENTS else bool(command_path(command))
            if item['executor'] == 'chatgpt-pro':
                from .advisor import available as advisor_available
                available = advisor_available(config or {}) if advisor_ready is None else advisor_ready
            if available:
                result[role] = dict(status='ok', **item, fallback=index != 0)
                break
        else:
            result[role] = dict(status='optional_unconfigured' if role == 'advisor' else 'missing',
                                executor=None, model=None, fallback=False)
    return result


def apply_roles(config, agents=None):
    """Feed the same mapping into actual dispatch defaults and candidate pools."""
    result = copy.deepcopy(config)
    if 'roles' not in result:
        return result
    roles = resolve_roles(result, agents)
    agents = discover() if agents is None else agents
    dispatch = result.setdefault('dispatch', {})
    executors = dispatch.setdefault('executors', {})
    available = {name: settings for name, settings in executors.items()
                 if agents.get(EXECUTOR_COMMANDS.get(name, name), False)}
    if any(agents.values()):
        dispatch['executors'] = executors = available
    defaults = dispatch.setdefault('defaults', {})
    for scope, role in [('development', 'dev'), ('review', 'review'), ('frontend', 'main')]:
        selected = roles[role]
        if selected['status'] != 'ok':
            continue
        name = 'claude-code' if selected['executor'] == 'claude' else selected['executor']
        model, effort = selected['model'], selected.get('effort', 'inherit')
        efforts = executors.setdefault(name, {}).setdefault('models', {}).setdefault(model, [])
        if effort not in efforts:
            efforts.append(effort)
        defaults[scope] = f'{name}:{model}:{effort}'
    if 'development' in defaults:
        dispatch['default'] = defaults['development']
    for name in ('codex', 'claude-code'):
        selected = next((defaults[scope] for scope in ('development', 'review', 'frontend')
                         if defaults.get(scope, '').startswith(name + ':')), None)
        adapter = result.get('adapters', {}).get(name.replace('-', '_'))
        if selected and adapter is not None:
            adapter['default'] = selected
    known = {f'{name}:{model}:{effort}' for name, settings in executors.items()
             for model, efforts in settings.get('models', {}).items() for effort in efforts}
    point = result.get('points', {}).get('dispatch', {})
    if isinstance(point.get('criteria'), dict):
        point['criteria'] = {name: description for name, description in point['criteria'].items() if name in known}
    return result
