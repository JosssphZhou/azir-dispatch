"""Setup's versioned, deliberately limited configuration and answer format."""
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tomllib

from .core import _point
from .roles import KEYS, DEFAULT_ROLES, apply_roles, command_path, validate_roles

EXECUTORS = ('codex', 'claude-code', 'cursor-agent', 'gemini', 'grok', 'agy')
SCOPES = ('development', 'review')
POINTS = ('dispatch', 'next_step', 'wrapup', 'skill')
ROOT = Path(__file__).resolve().parents[1]


class SetupError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def clean_environment():
    # Do not inspect credential values, including during child process probes.
    return {key: os.environ[key] for key in os.environ
            if key not in (*KEYS, 'AZIR_DISPATCH_CONFIG', 'AZIR_DISPATCH_LOG')}


def command_output(argv):
    process = None
    def reap():
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=clean_environment(), text=True, start_new_session=True)
        stdout, _ = process.communicate(timeout=3)
        return stdout.strip() if process.returncode == 0 else ''
    except subprocess.TimeoutExpired:
        reap()
        return ''
    except (OSError, UnicodeError):
        reap()
        return ''
    except BaseException:
        reap()
        raise


def environment():
    found = []
    for command in ('codex', 'claude', 'cursor-agent', 'gemini', 'grok', 'agy'):
        executor = 'claude-code' if command == 'claude' else command
        path = command_path(command)
        output = command_output([path, '--version']) if path else ''
        version = re.search(r'(?<!\w)\d+\.\d+(?:\.\d+)?(?!\w)', output)
        found.append(dict(command=command, executor=executor, found=bool(path),
                          version=version[0] if version else None,
                          version_identified=bool(version), adapter_supported=executor in ('codex', 'claude-code'),
                          account_and_model='unverified'))
    size = shutil.get_terminal_size((80, 24))
    return dict(executors=found, python=sys.version.split()[0],
                terminal=dict(columns=size.columns, lines=size.lines, recommended='90x46'),
                terminal_warning=size.columns < 90 or size.lines < 46,
                gitleaks=bool(shutil.which('gitleaks')),
                can_dispatch=any(item['found'] for item in found),
                api_key_present='OPENROUTER_API_KEY' in tuple(os.environ))


def privacy_proposals():
    proposals = []
    git = shutil.which('git')
    for key, source, replacement in [('name', 'git user.name', '<NAME>'),
                                     ('email', 'git user.email', '<EMAIL>'),
                                     ('home', 'HOME', '<HOME>')]:
        literal = str(Path.home()) if key == 'home' else command_output([git, 'config', 'user.' + key]) if git else ''
        if literal:
            pattern = re.escape(literal)
            if key == 'home':
                pattern += r'(?=/|$)'
            elif key == 'name':
                pattern = r'(?<!\w)' + pattern + r'(?!\w)'
            proposals.append(dict(key=key, source=source, effect=replacement,
                                  pattern=pattern, replacement=replacement))
    return proposals


def skill_inventory():
    result = []
    for directory in [Path.home() / '.claude/skills', Path.home() / '.agents/skills']:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob('*/SKILL.md')):
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                continue
            if not text.startswith('---\n'):
                continue
            header = text.split('---', 2)[1]
            match = re.search(r'^name:\s*([^\n]+)', header, re.MULTILINE)
            if match:
                name = match[1].strip().strip('"\'')
                if name and name != 'none':
                    result.append(dict(name=name, source=str(path.absolute())))
    return result


def table(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise SetupError(f'{label}: unknown fields or invalid table')


def check_points(value, *, descriptions=False):
    table(value, POINTS, 'points')
    for point, options in value.items():
        allowed = ('enabled', 'threshold', 'question', 'instructions', 'criteria', 'default') if descriptions else ('enabled', 'threshold', 'default')
        table(options, allowed, 'points.' + point)
        if 'enabled' in options and type(options['enabled']) is not bool:
            raise SetupError('point enabled must be boolean')
        threshold = options.get('threshold', 0.7)
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise SetupError('threshold must be between 0 and 1')


def check_executors(value):
    table(value, EXECUTORS, 'executors')
    if not value:
        raise SetupError('executor candidates must not be empty')
    for executor, settings in value.items():
        table(settings, ('models',), executor)
        models = settings.get('models')
        if not isinstance(models, dict) or not models:
            raise SetupError('model candidates must not be empty')
        for model, efforts in models.items():
            if (not isinstance(model, str) or not model or ':' in model
                    or not isinstance(efforts, list) or not efforts
                    or any(not isinstance(effort, str) or not effort or ':' in effort for effort in efforts)
                    or len(set(efforts)) != len(efforts)):
                raise SetupError('invalid model or effort candidates')


def read_answers(path):
    try:
        answers = tomllib.loads(Path(path).read_text(encoding='utf-8')) if path else {'version': 1}
    except (OSError, UnicodeError, ValueError):
        raise SetupError('cannot read versioned TOML answers') from None
    return validate_answers(answers)


def read_answers_json(path):
    try:
        raw = sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
        answers = json.loads(raw) if raw else {'version': 1}
    except (OSError, UnicodeError, ValueError):
        raise SetupError('cannot read versioned JSON answers') from None
    return validate_answers(answers)


def validate_answers(answers):
    table(answers, ('version', 'defaults', 'executors', 'roles', 'jev', 'points', 'skills', 'privacy', 'hooks', 'dashboard'), 'answers')
    validate_roles(answers.get('roles', {}))
    if type(answers.get('version')) is not int or answers['version'] != 1:
        raise SetupError('answers require version = 1')
    for key, allowed in [('defaults', SCOPES), ('jev', ('mode',)), ('skills', SCOPES),
                         ('privacy', ('name', 'email', 'home', 'strict', 'max_state_chars')),
                         ('hooks', ('project', 'scope')), ('dashboard', ('title', 'main_title', 'main_subtitle'))]:
        table(answers.get(key, {}), allowed, key)
    check_points(answers.get('points', {}))
    if 'executors' in answers:
        check_executors(answers['executors'])
    if answers.get('jev', {}).get('mode', 'offline') not in ('offline', 'online', 'rules'):
        raise SetupError('JEV mode must be offline, online or rules')
    for value in answers.get('defaults', {}).values():
        if not isinstance(value, str) or len(value.split(':')) != 3:
            raise SetupError('default must be executor:model:effort')
    for scope, choices in answers.get('skills', {}).items():
        if not isinstance(choices, list):
            raise SetupError('skill candidates must be an array')
        for item in choices:
            if isinstance(item, dict):
                table(item, ('name', 'source'), 'skill choice')
                if set(item) != {'name', 'source'} or not all(isinstance(v, str) and v for v in item.values()):
                    raise SetupError('skill choice needs name and source')
            elif not isinstance(item, str) or not item or item == 'none':
                raise SetupError('skill choice must name a scanned skill')
    privacy = answers.get('privacy', {})
    for key in ('name', 'email', 'home', 'strict'):
        if key in privacy and type(privacy[key]) is not bool:
            raise SetupError('privacy switches must be boolean')
    if 'max_state_chars' in privacy and (type(privacy['max_state_chars']) is not int or privacy['max_state_chars'] <= 0):
        raise SetupError('max_state_chars must be a positive integer')
    hooks = answers.get('hooks', {})
    if hooks.get('scope', 'local') not in ('local', 'shared') or ('project' in hooks and (not isinstance(hooks['project'], str) or not hooks['project'])):
        raise SetupError('hooks require a project and local or shared scope')
    if any(not isinstance(v, str) for v in answers.get('dashboard', {}).values()):
        raise SetupError('dashboard labels must be strings')
    return answers


def validate_config(config):
    table(config, ('config_version', 'roles', 'jev', 'log', 'dispatch', 'points', 'skill', 'redact', 'dashboard', 'adapters', 'setup'), 'config')
    validate_roles(config.get('roles', {}))
    if type(config.get('config_version', 1)) is not int or config.get('config_version', 1) != 1:
        raise SetupError('unsupported config version')
    for key, allowed in [
        ('jev', ('url', 'model', 'timeout', 'mode')), ('log', ('path',)),
        ('dispatch', ('default', 'defaults', 'executors')), ('skill', ('default', 'candidates')),
        ('redact', ('strict', 'max_state_chars', 'patterns')),
        ('dashboard', ('title', 'main_title', 'main_subtitle', 'log', 'advisor_dir', 'retention_seconds', 'advisor_active_seconds')),
        ('adapters', ('codex', 'claude_code')),
        ('setup', ('privacy', 'skill_sources'))]:
        table(config.get(key, {}), allowed, 'config.' + key)
    check_executors(config.get('dispatch', {}).get('executors', {}))
    check_points(config.get('points', {}), descriptions=True)
    defaults = config.get('dispatch', {}).get('defaults', {})
    table(defaults, (*SCOPES, 'frontend'), 'dispatch.defaults')
    if (not isinstance(config['dispatch'].get('default'), str)
            or not all(isinstance(value, str) for value in defaults.values())):
        raise SetupError('dispatch defaults must be combination strings')
    table(config.get('setup', {}).get('privacy', {}), ('name', 'email', 'home'), 'setup.privacy')
    if any(type(value) is not bool for value in config.get('setup', {}).get('privacy', {}).values()):
        raise SetupError('stored privacy switches must be boolean')
    sources = config.get('setup', {}).get('skill_sources', {})
    table(sources, SCOPES, 'setup.skill_sources')
    for source_map in sources.values():
        if not isinstance(source_map, dict) or not all(isinstance(v, str) for v in source_map.values()):
            raise SetupError('invalid skill source map')
    for key in ('default', 'candidates'):
        table(config.get('skill', {}).get(key, {}), SCOPES, 'skill.' + key)
    for adapter, value in config.get('adapters', {}).items():
        table(value, ('default', 'command', 'routes', 'scope'), 'adapters.' + adapter)
        if 'routes' in value and (not isinstance(value['routes'], dict) or not all(isinstance(v, str) for v in value['routes'].values())):
            raise SetupError('invalid adapter routes')
        if value.get('scope', 'development') not in SCOPES:
            raise SetupError('invalid adapter scope')
        for key in ('default', 'command'):
            if key in value and (not isinstance(value[key], str) or not value[key]):
                raise SetupError('invalid adapter default or command')
        if adapter.replace('_', '-') in config['dispatch']['executors'] and 'default' in value:
            options = _point('dispatch', config).options
            if value['default'] not in options or not value['default'].startswith(adapter.replace('_', '-') + ':'):
                raise SetupError('adapter default must belong to its candidate pool')
    jev = config.get('jev', {})
    if jev.get('mode', 'offline') not in ('offline', 'online', 'rules'):
        raise SetupError('invalid JEV mode')
    timeout = jev.get('timeout', 3)
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise SetupError('invalid JEV timeout')
    for key in ('url', 'model'):
        if key in jev and (not isinstance(jev[key], str) or not jev[key]):
            raise SetupError('invalid JEV endpoint or model')
    for value in [config.get('log', {}).get('path', ''), *config.get('dashboard', {}).values()]:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise SetupError('invalid log or dashboard setting')
    for key, value in config.get('dashboard', {}).items():
        if key in ('retention_seconds', 'advisor_active_seconds'):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise SetupError('dashboard time limits must be positive')
        elif not isinstance(value, str):
            raise SetupError('dashboard labels and paths must be strings')
    if not isinstance(config.get('log', {}).get('path', ''), str):
        raise SetupError('event log path must be a string')
    redact = config.get('redact', {})
    if type(redact.get('strict', False)) is not bool or type(redact.get('max_state_chars', 4000)) is not int or redact.get('max_state_chars', 4000) <= 0:
        raise SetupError('invalid privacy settings')
    if not isinstance(redact.get('patterns', []), list):
        raise SetupError('invalid privacy patterns')
    for pattern in redact.get('patterns', []):
        if isinstance(pattern, dict):
            table(pattern, ('pattern', 'replacement'), 'privacy pattern')
            if set(pattern) != {'pattern', 'replacement'} or not all(isinstance(v, str) for v in pattern.values()):
                raise SetupError('invalid literal replacement')
            pattern = pattern['pattern']
        if not isinstance(pattern, str):
            raise SetupError('invalid privacy pattern')
        try:
            re.compile(pattern)
        except re.error:
            raise SetupError('invalid privacy regex') from None
    if redact.get('strict') and not redact.get('patterns'):
        raise SetupError('strict filtering needs at least one rule')
    for point in ('next_step', 'wrapup'):
        try:
            _point(point, config)
        except (ValueError, TypeError, AttributeError):
            raise SetupError('invalid point configuration') from None
    for scope in SCOPES:
        dispatch = copy.deepcopy(config)
        dispatch['dispatch']['default'] = defaults.get(scope, config['dispatch']['default'])
        try:
            _point('dispatch', dispatch)
            _point('skill', config, scope)
        except (ValueError, TypeError, AttributeError):
            raise SetupError('default answers must belong to nonempty candidates') from None
    if config.get('points', {}).get('skill', {}).get('enabled') and not any(
            choice != 'none' for values in config['skill']['candidates'].values() for choice in values):
        raise SetupError('enabled skill point needs a selected skill')


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) in (int, float):
        return repr(value)
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(key) + ' = ' + toml_value(item) for key, item in value.items()) + ' }'
    raise SetupError('unsupported TOML value')


def toml_bytes(config):
    lines = ['# Managed by azir-dispatch setup; inherit uses executor settings.']
    def emit(value, keys=()):
        if keys:
            lines.extend(['', '[' + '.'.join(json.dumps(key) for key in keys) + ']'])
        for key, item in value.items():
            if not isinstance(item, dict):
                lines.append(json.dumps(key) + ' = ' + toml_value(item))
        for key, item in value.items():
            if isinstance(item, dict):
                emit(item, (*keys, key))
    emit(config)
    data = ('\n'.join(lines) + '\n').encode()
    if tomllib.loads(data.decode()) != config:
        raise SetupError('TOML roundtrip failed')
    return data


def candidate(existing, answers, detected, proposals, inventory):
    config = copy.deepcopy(existing)
    if not config:
        found = [item['executor'] for item in detected['executors'] if item['found']]
        default = (found[0] if found else 'codex') + ':inherit:inherit'
        config = dict(config_version=1,
                      jev=dict(mode='offline', url='https://openrouter.ai/api/alpha/decisions', model='typesafe/jev-1.13', timeout=3),
                      log=dict(path=str(Path(os.environ.get('AZIR_DISPATCH_STATE_DIR', '~/.local/state/azir-dispatch')).expanduser() / 'events.jsonl')),
                      dispatch=dict(default=default, defaults={scope: default for scope in SCOPES},
                                    executors={name: dict(models={'inherit': ['inherit']}) for name in EXECUTORS}),
                      points={point: dict(enabled=point != 'skill', threshold=0.8 if point == 'wrapup' else 0.7) for point in POINTS},
                      skill=dict(default={scope: 'none' for scope in SCOPES}, candidates={scope: ['none'] for scope in SCOPES}),
                      redact=dict(strict=False, max_state_chars=4000, patterns=[]),
                      dashboard=dict(title='AZIR WORKFLOW', main_title='主会话', main_subtitle='', log='~/.local/state/azir-dispatch/events.jsonl'),
                      adapters=dict(codex=dict(command='codex', default='codex:inherit:inherit'),
                                    claude_code=dict(default='claude-code:inherit:inherit', routes={})),
                      setup=dict(privacy={item['key']: True for item in proposals}, skill_sources={scope: {} for scope in SCOPES}))
    else:
        validate_config(config)
    if 'roles' in answers:
        config['roles'] = copy.deepcopy(answers['roles'])
    elif not existing and not any(key in answers for key in ('defaults', 'executors')):
        config['roles'] = copy.deepcopy(DEFAULT_ROLES)
    if 'roles' in config:
        config = apply_roles(config, {item['command']: item['found'] for item in detected['executors']})
    config['config_version'] = 1
    config.setdefault('jev', {}).setdefault('mode', 'offline')
    config['jev'].update(answers.get('jev', {}))
    dispatch = config['dispatch']
    dispatch.setdefault('defaults', {scope: dispatch['default'] for scope in SCOPES})
    if 'executors' in answers:
        dispatch['executors'] = copy.deepcopy(answers['executors'])
        # Adapter defaults must follow a changed candidate pool.
        for name in ('codex', 'claude-code'):
            if name in dispatch['executors']:
                model, efforts = next(iter(dispatch['executors'][name]['models'].items()))
                config.setdefault('adapters', {}).setdefault(name.replace('-', '_'), {})['default'] = f'{name}:{model}:{efforts[0]}'
    dispatch['defaults'].update(answers.get('defaults', {}))
    dispatch['default'] = dispatch['defaults']['development']
    if 'defaults' in answers or 'executors' in answers:
        for name in ('codex', 'claude-code'):
            chosen = next((dispatch['defaults'][scope] for scope in SCOPES
                           if dispatch['defaults'][scope].startswith(name + ':')), None)
            if chosen:
                config.setdefault('adapters', {}).setdefault(name.replace('-', '_'), {})['default'] = chosen
    for point, values in answers.get('points', {}).items():
        config.setdefault('points', {}).setdefault(point, {}).update(values)
    setup = config.setdefault('setup', {})
    sources = setup.setdefault('skill_sources', {scope: {} for scope in SCOPES})
    for scope, choices in answers.get('skills', {}).items():
        names, selected = [], {}
        for choice in choices:
            name = choice if isinstance(choice, str) else choice['name']
            matches = [item for item in inventory if item['name'] == name]
            if isinstance(choice, dict):
                matches = [item for item in matches if Path(item['source']) == Path(choice['source']).expanduser().absolute()]
            if len(matches) != 1 or name in names:
                raise SetupError('skill missing, duplicated or ambiguous; select its source', skill=name)
            names.append(name)
            selected[name] = matches[0]['source']
        config['skill']['candidates'][scope] = names + ['none']
        config['skill']['default'][scope] = 'none'
        sources[scope] = selected
    if 'skills' in answers and 'enabled' not in answers.get('points', {}).get('skill', {}):
        config.setdefault('points', {}).setdefault('skill', {})['enabled'] = any(sources.values())
    privacy = answers.get('privacy', {})
    switches = setup.setdefault('privacy', {})
    # Existing hand-written regexes remain unless these suggestions are changed.
    regenerate = not existing or any(key in privacy for key in ('name', 'email', 'home'))
    old_rules = [{key: item[key] for key in ('pattern', 'replacement')} for item in proposals if switches.get(item['key'], True)]
    for key in ('name', 'email', 'home'):
        if key in privacy:
            switches[key] = privacy[key]
    redact = config.setdefault('redact', {})
    if regenerate:
        custom = [rule for rule in redact.get('patterns', []) if rule not in old_rules]
        redact['patterns'] = custom + [{key: item[key] for key in ('pattern', 'replacement')} for item in proposals if switches.get(item['key'], True)]
    for key in ('strict', 'max_state_chars'):
        if key in privacy:
            redact[key] = privacy[key]
    config.setdefault('dashboard', {}).update(answers.get('dashboard', {}))
    if not existing:
        config['dashboard']['log'] = config['log']['path']
    validate_config(config)
    return config
