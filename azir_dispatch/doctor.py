"""Presence-only credential inspection and public installation checklist."""
import os
from pathlib import Path
import re
import sys
import tomllib

from .roles import AGENTS, KEYS, command_path, discover, resolve_roles
from .setup_config import ROOT, command_output
from .setup_state import update_state
from .ui_text import is_zh, tr

HINTS_EN = {
    'claude': 'Install Claude Code and log in, then rerun doctor.',
    'codex': 'Install Codex CLI and log in, then rerun doctor.',
    'cursor-agent': 'Install Cursor CLI and put cursor-agent on PATH.',
    'gemini': 'Install Gemini CLI and put gemini on PATH.',
    'grok': 'Install Grok CLI on PATH or at ~/.grok/bin/grok.',
    'agy': 'Install AGy CLI and put agy on PATH.',
    'herdr': 'Run curl -fsSL https://herdr.dev/install.sh | sh, then rerun doctor.',
    'git': 'Install git with your system package manager, then rerun doctor.',
    'python3': 'Install Python 3.11 or newer and put python3 on PATH.',
    'OPENROUTER_API_KEY': 'Set it in your own shell to use JEV online. Rules mode works without it.',
    'ANTHROPIC_API_KEY': 'Set it in your own shell if you want it. A logged-in CLI also works.',
    'OPENAI_API_KEY': 'Set it in your own shell if you want it. A logged-in CLI also works.',
    'models': 'Run `azir-dispatch roles` to pick executors and models, then log in and test them.',
    'environment': 'Install and log in to at least one agent CLI.',
    'repo': 'Clone this repository, then run python3 bin/azir-dispatch doctor in its root.',
    'board': 'Run python3 bin/azir-dispatch demo.',
    'first_dispatch': 'Follow skills/setup/SKILL.md to run the first dispatch.',
    'advisor': 'Follow skills/setup/SKILL.md to set up ego lite, log in to ChatGPT and test it.',
}
HINTS_ZH = {
    'claude': '安装 Claude Code 并登录，再重跑 doctor。',
    'codex': '安装 Codex CLI 并登录，再重跑 doctor。',
    'cursor-agent': '安装 Cursor CLI 并将 cursor-agent 加入 PATH。',
    'gemini': '安装 Gemini CLI 并将 gemini 加入 PATH。',
    'grok': '安装 Grok CLI，放入 PATH 或 ~/.grok/bin/grok。',
    'agy': '安装 AGy CLI 并将 agy 加入 PATH。',
    'herdr': '运行 curl -fsSL https://herdr.dev/install.sh | sh，再重跑 doctor。',
    'git': '用系统包管理器安装 git，再重跑 doctor。',
    'python3': '安装 Python 3.11 或更新版本，并将 python3 加入 PATH。',
    'OPENROUTER_API_KEY': '想在线使用 JEV 时，在自己的 shell 里设置。不设也能用规则模式。',
    'ANTHROPIC_API_KEY': '需要时在自己的 shell 里设置。CLI 登录也可使用。',
    'OPENAI_API_KEY': '需要时在自己的 shell 里设置。CLI 登录也可使用。',
    'models': '运行 `azir-dispatch roles` 选执行者和型号，登录后测试。',
    'environment': '安装并登录至少一个 agent CLI。',
    'repo': '克隆本仓库后在根目录运行 python3 bin/azir-dispatch doctor。',
    'board': '运行 python3 bin/azir-dispatch demo。',
    'first_dispatch': '按 skills/setup/SKILL.md 运行第一次派发。',
    'advisor': '按 skills/setup/SKILL.md 配置 ego lite、登录 ChatGPT 并测试。',
}


def hints():
    return HINTS_ZH if is_zh() else HINTS_EN


def load_config(path=None):
    explicit = path or os.environ.get('AZIR_DISPATCH_CONFIG')
    candidate = Path(explicit or '~/.config/azir-dispatch/config.toml').expanduser()
    return tomllib.loads(candidate.read_text(encoding='utf-8')) if explicit or candidate.exists() else {}


def snapshot(config=None, *, steps=None, probe_versions=True, refresh=True):
    config = {} if config is None else config
    agents = discover()
    # Iterating the environment names avoids reading even a single key value.
    names = set(os.environ)
    keys = {name: name in names for name in KEYS}
    tools = {}
    for name in ('herdr', 'git', 'python3'):
        path = command_path(name)
        version = None
        if path and probe_versions:
            output = command_output([path, '--version'])
            match = re.search(r'(?<!\w)\d+\.\d+(?:\.\d+)?(?!\w)', output)
            version = match[0] if match else None
        tools[name] = dict(found=bool(path), version=version)
    detected_steps = dict(environment='ok' if any(agents.values()) else 'missing',
                          herdr='ok' if tools['herdr']['found'] else 'missing',
                          repo='ok' if (ROOT / 'bin/azir-dispatch').is_file() else 'missing')
    detected_steps.update(steps or {})
    table = hints()
    role_hints = {name: table['models'] for name in ('main', 'dev', 'review', 'research')}
    from .advisor import available
    advisor_ready = available(config, probe=True)
    role_hints['advisor'] = table['advisor']
    return update_state(refresh=refresh, steps=detected_steps, agents=agents, keys=keys,
                        roles=resolve_roles(config, agents, advisor_ready=advisor_ready), tools=tools,
                        jev_mode='jev' if keys['OPENROUTER_API_KEY'] and config.get('jev', {}).get('mode') not in ('offline', 'rules') else 'rules',
                        hints={**table, **role_hints})


def add_parser(commands):
    parser = commands.add_parser('doctor', help='detect local CLI tools and installation progress')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--config')


def run(args):
    import json
    try:
        value = snapshot(load_config(args.config))
    except (OSError, ValueError, TypeError):
        print(tr('doctor.failed'), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(value, ensure_ascii=False))
        return 0
    color = sys.stdout.isatty() and os.environ.get('TERM', '') not in ('', 'dumb') and 'NO_COLOR' not in os.environ
    paint = lambda code, text: f'\x1b[{code}m{text}\x1b[0m' if color else text
    any_agent = any(value['agents'].values())

    def row(name, state, detail='', hint=''):
        """state: ok (green check), required (red cross), optional (gray dash)."""
        mark = {'ok': paint('32', '✓'), 'required': paint('31', '✗'), 'optional': paint('90', '–')}[state]
        text = f'{mark} {name}' + (f'  {detail}' if detail else '')
        if state == 'optional':
            text = paint('90', f'– {name}' + (f'  {detail}' if detail else ''))
        print(text + (f'  {hint}' if hint and state != 'ok' else ''))
    for name in AGENTS:
        found = value['agents'][name]
        # Any one agent CLI is enough, so a missing one is only red when none is installed.
        row(name, 'ok' if found else ('optional' if any_agent else 'required'),
            '' if found else (tr('doctor.optional') + ' · ' if any_agent else '') + tr('doctor.not_installed'),
            value['hints'].get(name, ''))
    if not any_agent:
        print(paint('31', '✗ ') + tr('doctor.need_agent', names=', '.join(AGENTS)))
    for name, tool in value['tools'].items():
        row(name, 'ok' if tool['found'] else 'required',
            tool['version'] or (tr('doctor.version_unknown') if tool['found'] else ''), value['hints'].get(name, ''))
    for name, present in value['keys'].items():
        row(name, 'ok' if present else 'optional', tr('doctor.set') if present else tr('doctor.optional'),
            value['hints'].get(name, ''))
    for name, role in value['roles'].items():
        if role['status'] == 'ok':
            row(name, 'ok', f"{role['executor']} / {role['model']}")
        elif role['status'] == 'optional_unconfigured':
            row(name, 'optional', tr('doctor.optional'), value['hints'].get(name, ''))
        else:
            row(name, 'required', tr('doctor.no_role'))
    print(tr('doctor.jev_rules') if value['jev_mode'] == 'rules' else tr('doctor.jev_online'))
    print()
    print(tr('doctor.next'))
    return 0
