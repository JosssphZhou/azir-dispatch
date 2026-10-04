"""Presence-only credential inspection and public installation checklist."""
import os
from pathlib import Path
import re
import sys
import tomllib

from .roles import AGENTS, KEYS, command_path, discover, resolve_roles
from .setup_config import ROOT, command_output
from .setup_state import update_state

HINTS = {
    'claude': '安装 Claude Code 并登录，再重跑 doctor。',
    'codex': '安装 Codex CLI 并登录，再重跑 doctor。',
    'cursor-agent': '安装 Cursor CLI 并将 cursor-agent 加入 PATH。',
    'gemini': '安装 Gemini CLI 并将 gemini 加入 PATH。',
    'grok': '安装 Grok CLI，放入 PATH 或 ~/.grok/bin/grok。',
    'agy': '安装 AGy CLI 并将 agy 加入 PATH。',
    'herdr': '运行 curl -fsSL https://herdr.dev/install.sh | sh，再重跑 doctor。',
    'git': '用系统包管理器安装 git，再重跑 doctor。',
    'python3': '安装 Python 3.11 或更新版本，并将 python3 加入 PATH。',
    'OPENROUTER_API_KEY': '在自己的 shell 设置 OPENROUTER_API_KEY 后重跑 doctor；可先用规则模式。',
    'ANTHROPIC_API_KEY': '按需在自己的 shell 设置 ANTHROPIC_API_KEY；CLI 登录也可使用。',
    'OPENAI_API_KEY': '按需在自己的 shell 设置 OPENAI_API_KEY；CLI 登录也可使用。',
    'models': '按 roles 选择可用 CLI，登录后验证型号；确认后运行 setup --apply。',
}


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
    role_hints = {name: HINTS['models'] for name in ('main', 'dev', 'review', 'research')}
    role_hints['advisor'] = '顾问可选；有 GPT Pro 接入命令时在 roles.advisor 配置。'
    return update_state(refresh=refresh, steps=detected_steps, agents=agents, keys=keys,
                        roles=resolve_roles(config, agents), tools=tools,
                        jev_mode='jev' if keys['OPENROUTER_API_KEY'] and config.get('jev', {}).get('mode') not in ('offline', 'rules') else 'rules',
                        hints={**HINTS, **role_hints, 'environment': '安装并登录至少一个 agent CLI。',
                               'repo': '克隆本仓库后在根目录运行 python3 bin/azir-dispatch doctor。',
                               'board': '运行 python3 bin/azir-dispatch demo。',
                               'first_dispatch': '按 skills/setup/SKILL.md 运行第一次派发。'})


def add_parser(commands):
    parser = commands.add_parser('doctor', help='detect local CLI tools and installation progress')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--config')


def run(args):
    import json
    try:
        value = snapshot(load_config(args.config))
    except (OSError, ValueError, TypeError):
        print('检测失败：请检查配置格式和安装状态目录权限。', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(value, ensure_ascii=False))
        return 0
    color = sys.stdout.isatty() and os.environ.get('TERM', '') not in ('', 'dumb') and 'NO_COLOR' not in os.environ
    def row(name, ok, detail=''):
        mark = '✓' if ok else '✗'
        if color:
            mark = ('\x1b[32m' if ok else '\x1b[31m') + mark + '\x1b[0m'
        print(f'{mark} {name}' + (f'  {detail}' if detail else '') +
              (f"  {value['hints'].get(name, '')}" if not ok else ''))
    for name in AGENTS:
        row(name, value['agents'][name])
    for name, tool in value['tools'].items():
        row(name, tool['found'], tool['version'] or ('版本未识别' if tool['found'] else ''))
    for name, present in value['keys'].items():
        row(name, present, '已设置' if present else '未设置')
    for name, role in value['roles'].items():
        row(name, role['status'] == 'ok',
            f"{role['executor']} / {role['model']}" if role['status'] == 'ok' else
            '未配置，可选' if role['status'] == 'optional_unconfigured' else '暂无可用执行者')
    print('JEV：' + ('规则模式' if value['jev_mode'] == 'rules' else 'JEV 模式（在线能力未验证）'))
    return 0
