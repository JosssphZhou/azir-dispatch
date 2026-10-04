"""Readable summary of a setup plan or result, for a person at a terminal."""
import os
import sys

from .roles import discover, resolve_roles
from .roles_data import label
from .ui_text import tr


def _paint(code, text, color):
    return f'\x1b[{code}m{text}\x1b[0m' if color else text


def wants_color():
    return sys.stdout.isatty() and os.environ.get('TERM', '') not in ('', 'dumb') and 'NO_COLOR' not in os.environ


def summary(result, config, path, *, color=None):
    color = wants_color() if color is None else color
    applied = result.get('status') == 'applied'
    lines = [_paint('1;38;2;205;182;255', tr('setup.applied' if applied else 'setup.title'), color), '']
    changed = bool(result.get('changes'))
    action = tr('setup.wrote' if applied else 'setup.will_write') if changed else tr('setup.unchanged')
    lines.append(f"  {_paint('90', tr('setup.file').ljust(14), color)}{path}  ({action})")
    lines.append(f"  {_paint('90', tr('setup.mode').ljust(14), color)}{config.get('jev', {}).get('mode', 'offline')}")
    hooks = result.get('hooks')
    lines.append(f"  {_paint('90', tr('setup.hooks').ljust(14), color)}"
                 + (f"{hooks.get('status', 'planned')} ({hooks.get('target', '')})" if hooks else tr('setup.hooks_none')))
    lines += ['', tr('setup.roles')]
    if 'roles' in config:
        resolved = resolve_roles(config, discover())
        for role, item in resolved.items():
            name = tr('role.' + role)
            if item['status'] == 'ok':
                note = '  ' + _paint('33', '(' + tr('s.fallback', fallback=label(item['executor'])) + ')', color) if item['fallback'] else ''
                lines.append(f"  {name.ljust(10)}{label(item['executor']).ljust(14)}{item['model'].ljust(22)}{item.get('effort', 'inherit')}{note}")
            else:
                lines.append(_paint('90', f"  {name.ljust(10)}{'—'.ljust(14)}{tr('not_configured') if item['status'] != 'missing' else tr('doctor.no_role')}", color))
    else:
        for scope, combo in config.get('dispatch', {}).get('defaults', {}).items():
            lines.append(f'  {scope.ljust(12)}{combo}')
    lines += ['', tr('setup.next')]
    if not applied and result.get('status') in ('planned', 'needs_confirmation'):
        lines.append('  ' + tr('setup.next_apply'))
    lines += ['  ' + tr('setup.next_roles'), '  ' + tr('setup.next_demo'),
              _paint('90', '  ' + tr('setup.json_hint'), color)]
    return '\n'.join(lines)
