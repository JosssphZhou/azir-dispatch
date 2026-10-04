"""`azir-dispatch roles`: a terminal screen for roles and models, plus its JSON and --set forms."""
import json
import os
import re
import select
import shutil
import sys
import termios
import tty

from . import model_catalog, roles_data
from .dashboard import (ADV, BRIGHT, DIM, EMPTY, EXEC, GO, GRAY, HAND, LINE, MGR, ORANGE, TITLE, WHITE, fit, tw)
from .setup_io import SetupError
from .ui_text import tr

MIN_COLS, MIN_ROWS = 78, 17
ROLE_COLORS = {'main': MGR, 'dev': EXEC, 'review': EXEC, 'research': EXEC, 'advisor': ADV}
STATUS_COLORS = {'verified': GO, 'connected': GO, 'unverified': GRAY, 'optional': DIM, 'failed': ORANGE,
                 'not_in_list': ORANGE, 'fallback': HAND, 'missing': ORANGE}
KEYS = {'\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left',
        '\x1bOA': 'up', '\x1bOB': 'down', '\x1bOC': 'right', '\x1bOD': 'left'}


def paint(text, color=None, *, bold=False, bg=None, on=True):
    if not on or not (color or bold or bg):
        return text
    codes = []
    if bold:
        codes.append('1')
    if color:
        codes.append('38;2;%d;%d;%d' % color)
    if bg:
        codes.append('48;2;%d;%d;%d' % bg)
    return '\x1b[' + ';'.join(codes) + 'm' + text + '\x1b[0m'


def pad(text, width):
    text = fit(text, width)
    return text + ' ' * (width - tw(text))


def status_text(row):
    status = row['status']
    if status == 'fallback':
        return tr('s.fallback', fallback=roles_data.label(row['fallback']['executor']))
    if status == 'not_in_list':
        return tr('s.not_in_list', executor=roles_data.label(row['executor']))
    if status == 'failed':
        key = 's.failed.' + str((row.get('reason') or {}).get('key'))
        return tr(key) if tr(key) != key else tr('s.failed')
    return tr('s.' + status)


def failure_text(reason):
    if not reason or not reason.get('key'):
        return ''
    try:
        return tr('r.' + reason['key'], **reason.get('values', {}))
    except (KeyError, IndexError):
        return str(reason['key'])


def describe(role, row, data):
    """Two lines: what the role does, and what happens when its executor is missing."""
    lines = [tr(f'role.{role}.desc')]
    if role == 'advisor' or row['executor'] is None:
        return lines
    fallback = roles_data.fallback_target(data['roles'], data['agents'], role, data['config'])
    if fallback and fallback['executor'] != row['executor']:
        lines.append(tr('falls_back', fallback=roles_data.label(fallback['executor']), executor=roles_data.label(row['executor'])))
    else:
        lines.append(tr('no_fallback'))
    return lines


ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def box(lines, width, *, on, height=None):
    """Draw lines inside a thin frame, like the boxes on the dispatch board."""
    inner = width - 4
    edge = lambda ch: paint(ch, LINE, on=on)
    result = [edge('┌' + '─' * (width - 2) + '┐')]
    body = list(lines)
    while height is not None and len(body) < height - 2:
        body.append('')
    for line in body:
        visible = tw(ANSI_RE.sub('', line))
        result.append(edge('│') + ' ' + line + ' ' * max(0, inner - visible) + ' ' + edge('│'))
    result.append(edge('└' + '─' * (width - 2) + '┘'))
    return result


def render(state, size, *, color=True):
    """Return the screen as a list of lines. The frame is at most 100 columns wide."""
    data = state['data']
    cols, rows_available = size
    width = min(cols, 100)
    inner = width - 4
    rows = roles_data.rows_of(data)
    on = color
    out = []
    names = roles_data_detected(data)
    right_plain = tr('detected') + ': ' + (' '.join(names) or tr('none')) + ' '
    left = ' ' + tr('title')
    gap = max(2, width - tw(left) - tw(right_plain))
    right = paint(tr('detected') + ': ', GRAY, on=on) + (' '.join(paint(n, GO, on=on) for n in names) or paint(tr('none'), DIM, on=on)) + ' '
    out.append(paint(left, TITLE, bold=True, on=on) + ' ' * gap + right)
    model_w = max(16, min(26, inner - 10 - 14 - 9 - 24))
    status_w = inner - 2 - 10 - 14 - model_w - 9
    table = [paint('  ' + pad(tr('role'), 10) + pad(tr('executor'), 14) + pad(tr('model'), model_w)
                   + pad(tr('effort'), 9) + tr('status'), DIM, on=on)]
    for index, row in enumerate(rows):
        selected = index == state['selected']
        faded = row['status'] == 'fallback' or row['executor'] is None
        cells = [
            (pad(tr('role.' + row['role']), 10), ROLE_COLORS[row['role']]),
            (pad('—' if row['executor'] is None else roles_data.label(row['executor']), 14), DIM if faded else WHITE),
            (pad(tr('not_configured') if row['executor'] is None else row['model'], model_w), DIM if faded else WHITE),
            (pad('—' if row['executor'] is None else row['effort'], 9), DIM if faded else GRAY),
            (pad(status_text(row), status_w), STATUS_COLORS[row['status']]),
        ]
        bg = EMPTY if selected else None
        marker = paint('▸ ', ORANGE, bold=True, bg=bg, on=on) if selected else '  '
        table.append(marker + ''.join(paint(text, c, bold=selected and i == 0, bg=bg, on=on) for i, (text, c) in enumerate(cells)))
    out += box(table, width, on=on)
    selected_row = rows[max(0, state['selected'])]
    picker = state.get('picker')
    note = []
    if picker:
        note.append(paint(fit(tr('pick_model', role=tr('role.' + selected_row['role']),
                                 executor=roles_data.label(picker['executor'])), inner), TITLE, on=on))
        window = max(2, rows_available - len(out) - 5)
        start = max(0, min(picker['index'] - window // 2, len(picker['options']) - window))
        for number in range(start, min(len(picker['options']), start + window)):
            text = picker['options'][number][1]
            note.append(paint(pad('▸ ' + text, inner), WHITE, bold=True, bg=EMPTY, on=on) if number == picker['index']
                        else paint(pad('  ' + text, inner), GRAY, on=on))
    elif not state.get('static'):
        note += [paint(fit(line, inner), GRAY, on=on) for line in describe(selected_row['role'], selected_row, data)]
        detail = failure_text(selected_row.get('reason'))
        if detail and not state.get('message'):
            note.append(paint(fit(detail, inner), ORANGE, on=on))
    if state.get('message'):
        note.append('')
        note.append(paint(fit(state['message'], inner), ORANGE if state.get('message_error') else GO, on=on))
    if state.get('static'):
        return out
    footer = state.get('prompt')
    height = max(len(note) + 2, rows_available - len(out) - 1)
    out += box(note, width, on=on, height=height)
    out = out[:max(0, rows_available - 1)]
    if footer is not None:
        out.append(' ' + paint(fit(footer, width - 2), WHITE, bold=True, on=on))
    else:
        suffix = '  ' + tr('unsaved') if state.get('dirty') else ''
        out.append(' ' + paint(fit(tr('keys'), width - 2 - tw(suffix)), GRAY, on=on) + paint(suffix, HAND, on=on))
    return out


def roles_data_detected(data):
    return [name for name in roles_data.EXECUTOR_ORDER if data['agents'].get(roles_data.EXECUTOR_COMMANDS[name])]


def static_screen(data, *, color):
    lines = render(dict(data=data, selected=-1, static=True), (100, 14), color=color)
    return '\n'.join(line.rstrip() for line in lines if line is not None).rstrip()


# --- interactive loop ------------------------------------------------------

KEY_PATTERN = re.compile(r'\x1b[\[O][A-D]|\x1b|[^\x1b]')


def split_keys(text):
    """Split one read into keys. Held or pasted keys arrive together, so a read can hold several."""
    keys = []
    for token in KEY_PATTERN.findall(text):
        if token == '\x1b':
            keys.append('esc')
        elif token.startswith('\x1b'):
            keys.append(KEYS[token])
        else:
            keys.append(token)
    return keys


def read_keys(fd, timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return []
    return split_keys(os.read(fd, 256).decode('utf-8', errors='ignore'))


def model_options(state, row):
    data = state['data']
    catalog = data['catalogs'].get(row['executor'], {})
    models = ['inherit', *[m for m in catalog.get('models', []) if m != 'inherit']]
    if row['model'] not in models:
        models.insert(1, row['model'])
    options = [(m, tr('inherit_label') if m == 'inherit' else m) for m in models]
    options.append((None, '+ ' + tr('type_model')))
    return options


def change_executor(state, step):
    data = state['data']
    role = roles_data.ROLE_ORDER[state['selected']]
    choices = roles_data.executor_choices(role, data['agents'], data['config'])
    current = model_catalog.canonical(data['roles'][role]['preferred']['executor'])
    if not choices or (choices == [current]):
        return tr('no_executor'), True
    index = choices.index(current) if current in choices else (-1 if step > 0 else 0)
    new = choices[(index + step) % len(choices)]
    data['roles'][role]['preferred'] = dict(executor=new, model='inherit', effort='inherit')
    state['dirty'] = True
    return None, False


def change_effort(state):
    data = state['data']
    role = roles_data.ROLE_ORDER[state['selected']]
    preferred = data['roles'][role]['preferred']
    executor = model_catalog.canonical(preferred['executor'])
    options = model_catalog.efforts_for(executor, preferred['model'], data['catalogs'].get(executor))
    current = preferred.get('effort', 'inherit')
    index = options.index(current) if current in options else -1
    preferred['effort'] = options[(index + 1) % len(options)]
    state['dirty'] = True


def run_screen(config_dir):
    fd = sys.stdin.fileno()
    saved_attrs = termios.tcgetattr(fd)
    sys.stdout.write(tr('loading') + '\n')
    sys.stdout.flush()
    data = roles_data.gather(config_dir)
    state = dict(data=data, selected=0, dirty=False, message=None, message_error=False, picker=None, prompt=None)
    quit_armed = False
    pending = None
    manual = None
    out = sys.stdout

    def draw():
        size = shutil.get_terminal_size((80, 24))
        out.write('\x1b[H')
        if size.columns < MIN_COLS or size.lines < MIN_ROWS:
            text = tr('too_small', cols=MIN_COLS, rows=MIN_ROWS, have_cols=size.columns, have_rows=size.lines)
            out.write('\x1b[2J\x1b[H' + text)
        else:
            lines = render(state, (size.columns, size.lines))
            out.write('\x1b[2J\x1b[H' + '\n'.join(line + '\x1b[K' for line in lines))
        out.flush()

    try:
        tty.setcbreak(fd)
        out.write('\x1b[?1049h\x1b[?25l')
        queue = []
        size_seen = None
        need_draw = True
        while True:
            if not queue:
                size_now = tuple(shutil.get_terminal_size((80, 24)))
                if need_draw or size_now != size_seen:
                    draw()
                    need_draw, size_seen = False, size_now
                queue = read_keys(fd, 0.3)
                continue
            need_draw = True
            key = queue.pop(0)
            state['message'], state['message_error'] = None, False
            row_role = roles_data.ROLE_ORDER[state['selected']]
            if manual is not None:
                if key in ('\r', '\n'):
                    if manual.strip() and ':' not in manual:
                        data['roles'][row_role]['preferred'] = dict(data['roles'][row_role]['preferred'], model=manual.strip())
                        state['dirty'] = True
                    manual = None
                    state['prompt'] = None
                elif key == 'esc':
                    manual = None
                    state['prompt'] = None
                elif key in ('\x7f', '\b'):
                    manual = manual[:-1]
                    state['prompt'] = tr('manual_prompt') + manual
                elif key and len(key) == 1 and key.isprintable():
                    manual += key
                    state['prompt'] = tr('manual_prompt') + manual
                else:
                    state['prompt'] = tr('manual_prompt') + manual
                continue
            if pending is not None:
                if key in ('y', 'Y'):
                    role_now, preferred = pending
                    pending = None
                    state['prompt'] = None
                    combo_text = roles_data.combo(preferred)
                    state['message'] = tr('testing', combo=combo_text)
                    draw()
                    outcome = model_catalog.run_test(preferred['executor'], preferred['model'], preferred.get('effort', 'inherit'), timeout=90)
                    model_catalog.record_check(preferred['executor'], preferred['model'], preferred.get('effort', 'inherit'), outcome)
                    data['checks'] = model_catalog.load_checks()
                    if outcome['ok']:
                        state['message'] = tr('test_ok', combo=combo_text, seconds=outcome['seconds'])
                    else:
                        state['message'] = tr('test_fail', combo=combo_text, reason=failure_text(dict(key=outcome['reason'], values=outcome['values'])))
                        state['message_error'] = True
                else:
                    pending = None
                    state['prompt'] = None
                continue
            if state['picker']:
                picker = state['picker']
                if key == 'up':
                    picker['index'] = (picker['index'] - 1) % len(picker['options'])
                elif key == 'down':
                    picker['index'] = (picker['index'] + 1) % len(picker['options'])
                elif key == 'esc':
                    state['picker'] = None
                elif key in ('\r', '\n'):
                    value = picker['options'][picker['index']][0]
                    state['picker'] = None
                    if value is None:
                        manual = ''
                        state['prompt'] = tr('manual_prompt')
                    else:
                        data['roles'][row_role]['preferred'] = dict(data['roles'][row_role]['preferred'], model=value, effort='inherit')
                        state['dirty'] = True
                continue
            if key in ('q', 'Q', '\x03'):
                if state['dirty'] and not quit_armed and key != '\x03':
                    quit_armed = True
                    state['message'] = tr('unsaved') + ' q'
                    state['message_error'] = True
                    continue
                break
            quit_armed = False
            if key == 'up':
                state['selected'] = (state['selected'] - 1) % len(roles_data.ROLE_ORDER)
            elif key == 'down':
                state['selected'] = (state['selected'] + 1) % len(roles_data.ROLE_ORDER)
            elif key in ('left', 'right'):
                message, error = change_executor(state, 1 if key == 'right' else -1)
                state['message'], state['message_error'] = message, error
            elif key in ('m', 'M'):
                rows = roles_data.rows_of(data)
                row = rows[state['selected']]
                if row['executor'] in (None, 'chatgpt-pro'):
                    state['message'], state['message_error'] = tr('cannot_edit'), True
                else:
                    options = model_options(state, row)
                    values = [value for value, _ in options]
                    state['picker'] = dict(executor=row['executor'], options=options,
                                           index=values.index(row['model']) if row['model'] in values else 0)
            elif key in ('e', 'E'):
                if roles_data.rows_of(data)[state['selected']]['executor'] in (None, 'chatgpt-pro'):
                    state['message'], state['message_error'] = tr('cannot_edit'), True
                else:
                    change_effort(state)
            elif key in ('t', 'T'):
                preferred = data['roles'][row_role]['preferred']
                row = roles_data.rows_of(data)[state['selected']]
                if row['executor'] is None:
                    state['message'], state['message_error'] = tr('cannot_edit'), True
                elif row['executor'] == 'chatgpt-pro':
                    state['message'], state['message_error'] = tr('cannot_test'), True
                else:
                    pending = (row_role, dict(preferred, executor=row['executor']))
                    state['prompt'] = tr('confirm_test', combo=roles_data.combo(preferred))
            elif key in ('s', 'S'):
                try:
                    path = roles_data.save_roles(data['roles'], config_dir)
                    state['dirty'] = False
                    state['message'] = tr('saved', path=path)
                except (SetupError, OSError, ValueError) as exc:
                    state['message'], state['message_error'] = str(exc), True
    except KeyboardInterrupt:
        return 130
    finally:
        out.write('\x1b[?25h\x1b[?1049l')
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved_attrs)
    return 0


# --- command ---------------------------------------------------------------

def add_parser(commands):
    parser = commands.add_parser('roles', help='review and change roles and models')
    parser.add_argument('--json', action='store_true', help='print roles, status and available models as JSON')
    parser.add_argument('--set', action='append', default=[], metavar='ROLE=EXECUTOR:MODEL:EFFORT',
                        help='change a role and save it, for example dev=codex:gpt-6.1-sol:high')
    parser.add_argument('--print', action='store_true', dest='print_screen', help='print the screen once and exit')
    parser.add_argument('--config-dir', help='directory holding config.toml (default ~/.config/azir-dispatch)')


def run(args):
    config_dir = roles_data.config_dir_from(args.config_dir)
    try:
        data = roles_data.gather(config_dir)
        if args.set:
            before = [(role, roles_data.combo(data['roles'][role]['preferred'])) for role in roles_data.ROLE_ORDER]
            changes = roles_data.apply_set(data, args.set)
            path = roles_data.save_roles(data['roles'], config_dir)
            data = roles_data.gather(config_dir, use_cache=True)
            if args.json:
                print(json.dumps(dict(changes=[dict(role=r, before=b, after=a) for r, b, a in changes],
                                      saved=str(path), **roles_data.to_json(data)), ensure_ascii=False))
                return 0
            for role, old, new in changes:
                print(tr('setset_ok' if old != new else 'setset_same', role=role, before=old, after=new))
            print(tr('saved_to', path=path))
            return 0
        if args.json:
            print(json.dumps(roles_data.to_json(data), ensure_ascii=False))
            return 0
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.print_screen
        if interactive:
            return run_screen(config_dir)
        color = sys.stdout.isatty() and os.environ.get('TERM', '') not in ('', 'dumb') and 'NO_COLOR' not in os.environ
        print(static_screen(data, color=color))
        return 0
    except ValueError as exc:
        print(tr('setset_error', message=exc), file=sys.stderr)
        return 2
    except (SetupError, OSError) as exc:
        print(tr('setset_error', message=str(exc)), file=sys.stderr)
        return 2
