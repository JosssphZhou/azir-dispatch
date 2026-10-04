"""Rows, JSON, `--set` and saving for the roles screen. No drawing happens here."""
import copy
from pathlib import Path
import tomllib

from . import model_catalog
from .roles import AGENTS, DEFAULT_ROLES, EXECUTOR_COMMANDS, apply_roles, command_path, discover, resolve_roles, validate_roles
from .setup_config import SetupError, candidate, environment, privacy_proposals, skill_inventory, toml_bytes
from .setup_io import Change, commit, directory_path, guarded, unfinished
from .setup_state import state_directory

ROLE_ORDER = tuple(DEFAULT_ROLES)
# Executors a role can be set to. The advisor takes only these. It is reached through a logged-in browser, not a CLI.
ADVISOR_EXECUTORS = ('chatgpt-pro',)
EXECUTOR_ORDER = ('claude', 'codex', 'cursor-agent', 'gemini', 'grok', 'agy')
LABELS = {'claude': 'Claude Code', 'codex': 'Codex', 'cursor-agent': 'Cursor', 'gemini': 'Gemini CLI',
          'grok': 'Grok', 'agy': 'AGy', 'gpt-pro': 'GPT Pro', 'chatgpt-pro': 'ChatGPT Pro'}
DEFAULT_CONFIG_DIR = '~/.config/azir-dispatch'


def label(executor):
    executor = model_catalog.canonical(executor)
    return LABELS.get(executor, executor)


def config_dir_from(args_dir=None):
    import os
    if args_dir:
        return args_dir
    configured = os.environ.get('AZIR_DISPATCH_CONFIG')
    return str(Path(configured).expanduser().parent) if configured else DEFAULT_CONFIG_DIR


def load_config(config_dir):
    path = directory_path(config_dir) / 'config.toml'
    raw = guarded(path)
    return path, (tomllib.loads(raw.decode()) if raw else {})


def current_roles(config):
    """The roles in the config file, filled with defaults for any role it leaves out."""
    configured = copy.deepcopy((config or {}).get('roles', {}))
    validate_roles(configured)
    return {role: configured.get(role) or copy.deepcopy(DEFAULT_ROLES[role]) for role in ROLE_ORDER}


def installed(agents, executor, config=None):
    if executor == 'chatgpt-pro':
        from .advisor import available
        return available(config or {})
    command = EXECUTOR_COMMANDS[executor]
    return bool(agents.get(command)) if command in AGENTS else bool(command_path(command))


def executor_choices(role, agents, config=None):
    pool = ADVISOR_EXECUTORS if role == 'advisor' else EXECUTOR_ORDER
    return [name for name in pool if installed(agents, name, config)]


def build_rows(roles, agents, catalogs, checks, config=None):
    resolved = resolve_roles({**(config or {}), 'roles': roles}, agents)
    rows = []
    for role in ROLE_ORDER:
        preferred = roles[role]['preferred']
        executor = model_catalog.canonical(preferred['executor'])
        model, effort = preferred['model'], preferred.get('effort', 'inherit')
        outcome = resolved[role]
        row = dict(role=role, executor=executor, model=model, effort=effort, fallback=None, reason=None)
        if outcome['status'] != 'ok':
            row['status'] = 'optional' if role == 'advisor' else 'missing'
            if role == 'advisor':
                row.update(executor=None, model=None, effort=None)
        elif outcome['fallback']:
            row['status'] = 'fallback'
            row['fallback'] = dict(executor=model_catalog.canonical(outcome['executor']),
                                   model=outcome['model'], effort=outcome.get('effort', 'inherit'))
        elif executor == 'chatgpt-pro':
            # Connected means ego lite is signed in to ChatGPT with the Pro tier, which `doctor` already checked.
            row['status'] = 'connected'
        else:
            check = checks.get(model_catalog.check_key(executor, model, effort))
            catalog = catalogs.get(executor, {})
            if check and check.get('ok'):
                row['status'] = 'verified'
            elif check:
                row['status'] = 'failed'
                row['reason'] = dict(key=check.get('reason'), values=check.get('values', {}))
            elif catalog.get('listed') and model != 'inherit' and model not in catalog['models']:
                row['status'] = 'not_in_list'
            else:
                row['status'] = 'unverified'
        rows.append(row)
    return rows


def rows_of(data):
    return build_rows(data['roles'], data['agents'], data['catalogs'], data['checks'], data['config'])


def fallback_target(roles, agents, role, config=None):
    """The first installed fallback, used for the 'Falls back to ...' line even while the preferred one works."""
    settings = roles[role]
    for item in settings['fallbacks']:
        if installed(agents, item['executor'], config):
            return dict(item, executor=model_catalog.canonical(item['executor']))
    return None


def adapt_defaults(roles, configured, catalogs):
    """A default the user's CLI does not list becomes `inherit`, so a fresh setup never names a model the account lacks."""
    for role, settings in roles.items():
        if role in configured:
            continue
        for item in [settings['preferred'], *settings['fallbacks']]:
            catalog = catalogs.get(model_catalog.canonical(item['executor']), {})
            if catalog.get('listed') and item['model'] != 'inherit' and item['model'] not in catalog['models']:
                item['model'] = 'inherit'


def gather(config_dir, use_cache=True):
    path, config = load_config(config_dir)
    roles = current_roles(config)
    agents = discover()
    wanted = {model_catalog.canonical(r['preferred']['executor']) for r in roles.values()}
    wanted |= {name for name in EXECUTOR_ORDER if agents.get(EXECUTOR_COMMANDS[name])}
    catalogs = model_catalog.catalogs([n for n in wanted if n in EXECUTOR_ORDER], use_cache)
    adapt_defaults(roles, config.get('roles', {}), catalogs)
    return dict(path=path, exists=bool(config), config=config, roles=roles, agents=agents, catalogs=catalogs,
                checks=model_catalog.load_checks())


def combo(item):
    return f"{item['executor']}:{item['model']}:{item.get('effort', 'inherit')}"


def to_json(data):
    rows = rows_of(data)
    roles = {}
    for row in rows:
        role = row['role']
        fallback = row['fallback'] or fallback_target(data['roles'], data['agents'], role)
        roles[role] = dict(executor=row['executor'], model=row['model'], effort=row['effort'],
                           status=row['status'], reason=row['reason'],
                           using_fallback=row['status'] == 'fallback',
                           fallback=fallback and combo(fallback),
                           combination=row['executor'] and combo(row))
    available = {name: dict(listed=catalog['listed'], models=catalog['models'],
                            efforts=model_catalog.efforts_for(name, catalog['models'][0] if catalog['models'] else '', catalog))
                 for name, catalog in data['catalogs'].items()}
    return dict(config=str(data['path']), config_exists=data['exists'],
                detected=[name for name in EXECUTOR_ORDER if data['agents'].get(EXECUTOR_COMMANDS[name])],
                roles=roles, available=available)


def parse_set(text):
    """'dev=codex:gpt-6.1-sol:high' -> (role, preferred). Raises ValueError with a readable message."""
    role, separator, value = text.partition('=')
    parts = value.split(':')
    if not separator or role not in ROLE_ORDER or len(parts) != 3 or not all(parts):
        raise ValueError(f'expected role=executor:model:effort with role in {", ".join(ROLE_ORDER)}: {text}')
    executor = model_catalog.canonical(parts[0])
    if executor not in EXECUTOR_COMMANDS:
        raise ValueError(f'unknown executor {parts[0]}; choose one of {", ".join(EXECUTOR_ORDER)}')
    return role, dict(executor=executor, model=parts[1], effort=parts[2])


def apply_set(data, texts):
    """Change preferred combinations in data['roles']. Returns lines describing each change."""
    lines = []
    for text in texts:
        role, new = parse_set(text)
        executor, model = new['executor'], new['model']
        if role != 'advisor' and executor not in EXECUTOR_ORDER:
            raise ValueError(f'{executor} cannot be used for {role}')
        catalog = data['catalogs'].get(executor) or model_catalog.probe(executor)
        if catalog.get('listed') and model != 'inherit' and model not in catalog['models']:
            raise ValueError(f'{executor} has no model {model}. It lists: ' + ', '.join(catalog['models'][:12]))
        efforts = model_catalog.efforts_for(executor, model, catalog)
        if new['effort'] not in efforts and len(efforts) > 1:
            raise ValueError(f"{executor} {model} takes effort {', '.join(efforts)}; got {new['effort']}")
        before = combo(data['roles'][role]['preferred'])
        data['roles'][role]['preferred'] = new
        lines.append((role, before, combo(new)))
    return lines


def save_roles(roles, config_dir):
    """Write roles into config.toml the same way `setup --apply` does, so dispatch defaults follow."""
    from .doctor import snapshot
    state = state_directory()
    unfinished(state)
    path = directory_path(config_dir) / 'config.toml'
    old = guarded(path)
    existing = tomllib.loads(old.decode()) if old else {}
    detected = environment()
    config = candidate(existing, dict(version=1, roles=copy.deepcopy(roles)), detected,
                       privacy_proposals(), skill_inventory())
    new = old if existing == config else toml_bytes(config)
    commit([Change(path, old, new)], state)
    snapshot(config, steps={'models': 'ok' if detected['can_dispatch'] else 'missing'}, probe_versions=False, refresh=False)
    return path
