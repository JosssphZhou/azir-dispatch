"""Versioned setup planning, separate authorizations, and verified application."""
import difflib
import json
import copy
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
import shlex
import sys
import tomllib

from .setup_config import (ROOT, SetupError, candidate, environment, privacy_proposals,
                           read_answers, read_answers_json, skill_inventory, toml_bytes, clean_environment)
from .setup_io import Change, commit, guarded, unfinished, digest, directory_path
from .setup_state import state_directory
from .ui_text import tr
from .redact import redact_state
from .filtering import FilterError, FilterTimeout, filter_value
from .setup_hooks import hook_fragment, plan_hooks
from .core import _point


def add_parser(commands):
    parser = commands.add_parser('setup', help='plan and configure azir dispatch')
    answers = parser.add_mutually_exclusive_group()
    answers.add_argument('--answers', help='version = 1 TOML answers')
    answers.add_argument('--answers-json', help='version = 1 JSON answers file, or - for stdin')
    parser.add_argument('--config-dir', default='~/.config/azir-dispatch')
    parser.add_argument('--apply', action='store_true', help='apply local configuration')
    parser.add_argument('--allow-hooks', metavar='PROJECT', help='authorize hooks in one project')
    parser.add_argument('--allow-online-test', action='store_true', help='authorize one online JEV test')
    parser.add_argument('--json', action='store_true', help='print the full result as JSON (the default when stdout is not a terminal)')


def verify(config):
    offline = copy.deepcopy(config)
    offline['jev']['mode'] = 'offline'
    with tempfile.TemporaryDirectory(prefix='azir-setup-') as directory:
        work = Path(directory)
        config_path = work / 'config.toml'
        config_path.write_bytes(toml_bytes(offline))
        hook_path = work / 'hook-config.toml'
        hook_config = copy.deepcopy(offline)
        hook_config.pop('roles', None)
        claude_configured = 'claude-code' in hook_config['dispatch']['executors']
        if not claude_configured:
            hook_config['dispatch']['executors']['claude-code'] = dict(models={'inherit': ['inherit']})
            hook_config.setdefault('adapters', {})['claude_code'] = dict(default='claude-code:inherit:inherit')
        hook_path.write_bytes(toml_bytes(hook_config))
        log = work / 'events.jsonl'
        env = clean_environment()
        # Synthetic adapter events must not mark a real installation's first
        # dispatch as complete or refresh its progress during verification.
        env['AZIR_DISPATCH_STATE_DIR'] = str(work / 'state')
        commands = [
            ([sys.executable, str(ROOT / 'bin/azir-dispatch'), 'decide', 'dispatch', '--config', str(config_path), '--state-file', '-', '--log', str(log)], 'setup protocol sample'),
            ([sys.executable, str(ROOT / 'adapters/claude_code/hook.py'), '--config', str(hook_path), '--log', str(log)], json.dumps(dict(hook_event_name='PreToolUse', session_id='setup-sample', tool_use_id='setup-call', tool_name='Agent', tool_input=dict(subagent_type='general-purpose', prompt='setup protocol sample')))),
            ([sys.executable, '-m', 'azir_dispatch.dashboard', '--config', str(config_path), '--replay', str(ROOT / 'tests/fixtures/events-sample.jsonl'), '--check'], None)]
        values = []
        for command, stdin in commands:
            result = subprocess.run(command, input=stdin, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, cwd=ROOT, timeout=15)
            if result.returncode:
                raise SetupError('candidate offline verification failed')
            values.append(result.stdout)
        decision = json.loads(values[0])
        hook = json.loads(values[1])
        hook_output = hook.get('hookSpecificOutput', {})
        if (decision.get('answer') != config['dispatch']['default']
                or hook_output.get('hookEventName') != 'PreToolUse'
                or hook_output.get('permissionDecision') == 'deny'):
            raise SetupError('candidate adapter offline protocol failed')
    return dict(config_valid='passed', offline_dispatch='passed', adapter_offline_protocol='passed', claude_code_configured=claude_configured, dashboard_readable='passed', online_jev='not_tested', hooks='not_written')


def online_test(config):
    from .core import decide
    with tempfile.TemporaryDirectory(prefix='azir-online-test-') as directory:
        # No user task, candidate list, identity, file content or hook enters this call.
        result = decide('next_step', 'azir setup test', config={'jev': config['jev']},
                        log=Path(directory) / 'events.jsonl')
    return ('tested', None) if result['source'] == 'jev' else ('failed', result['error'])


def prompt(message, default=''):
    value = input(f'{message} [{default}] > ').strip()
    return value or default


def yes(message, default=False):
    answer = prompt(message, 'y' if default else 'n').lower()
    if answer not in ('y', 'yes', 'n', 'no', '是', '否'):
        raise SetupError('answer must be yes or no')
    return answer in ('y', 'yes', '是')


def show_environment(detected):
    for item in detected['executors']:
        print(f"{item['command']}: 找到={item['found']}，版本已识别={item['version_identified']}，适配器支持={item['adapter_supported']}，账号和型号未验证")
    print('Python ' + detected['python'])
    if detected['terminal_warning']:
        print('终端较小：看板建议 90×46，不影响安装。')
    if not detected['gitleaks']:
        print('gitleaks 未找到，本次未运行该秘密扫描器。')
    if not detected['can_dispatch']:
        print('未找到执行者，可以生成离线配置，目前不能派发。')


def interactive_answers(answers, existing, detected, proposals, inventory):
    answers = copy.deepcopy(answers)
    found = [item['executor'] for item in detected['executors'] if item['found']]
    fallback = (found[0] if found else 'codex') + ':inherit:inherit'
    defaults = existing.get('dispatch', {}).get('defaults', {scope: existing.get('dispatch', {}).get('default', fallback) for scope in ('development', 'review')})
    selected = answers.setdefault('defaults', {})
    for scope in ('development', 'review'):
        current = selected.get(scope, defaults.get(scope, fallback))
        from .setup_config import EXECUTORS
        executor = prompt(scope + ' 默认执行者 (' + ' / '.join(EXECUTORS) + ')', current.split(':')[0])
        if executor not in EXECUTORS:
            raise SetupError('unsupported executor')
        selected[scope] = current if current.startswith(executor + ':') else executor + ':inherit:inherit'
    answers['jev'] = dict(mode=prompt('JEV 工作模式 (rules / offline / online)', existing.get('jev', {}).get('mode', 'offline')))
    print('离线默认：执行者用配置默认，下一步 stop，收尾 keep，技能 none。')
    if yes('配置高级选项?'):
        executors = copy.deepcopy(existing.get('dispatch', {}).get('executors', {name: dict(models={'inherit': ['inherit']}) for name in ('codex', 'claude-code')}))
        for name in sorted({value.split(':')[0] for value in selected.values()}):
            models = prompt(name + ' 候选型号，逗号分隔；inherit 沿用自身设置', ','.join(executors.get(name, {}).get('models', {'inherit': ['inherit']})))
            pool = {}
            for model in models.split(','):
                model = model.strip()
                current_efforts = executors.get(name, {}).get('models', {}).get(model, ['inherit'])
                efforts = prompt(name + '/' + model + ' 候选思考等级，逗号分隔', ','.join(current_efforts))
                pool[model] = [value.strip() for value in efforts.split(',')]
            executors[name] = dict(models=pool)
        answers['executors'] = executors
        for scope in ('development', 'review'):
            name = selected[scope].split(':')[0]
            model, efforts = next(iter(executors[name]['models'].items()))
            selected[scope] = prompt(scope + ' 默认组合 executor:model:effort', f'{name}:{model}:{efforts[0]}')
        for index, item in enumerate(inventory, 1):
            print(f"技能 {index}: {item['name']}，来源 {item['source']}")
        skills = {}
        for scope in ('development', 'review'):
            indexes = prompt(scope + ' 技能编号，逗号分隔，留空不选择')
            try:
                numbers = [int(index.strip()) for index in indexes.split(',') if index.strip()]
                if any(index < 1 or index > len(inventory) for index in numbers):
                    raise ValueError
                skills[scope] = [inventory[index - 1] for index in numbers]
            except ValueError:
                raise SetupError('invalid skill selection') from None
        answers['skills'] = skills
        points = answers.setdefault('points', {})
        for point in ('dispatch', 'next_step', 'wrapup', 'skill'):
            current = existing.get('points', {}).get(point, {})
            enabled = yes(point + ' 判断点启用?', any(skills.values()) if point == 'skill' else current.get('enabled', True))
            try:
                threshold = float(prompt(point + ' 把握线', str(current.get('threshold', 0.8 if point == 'wrapup' else 0.7))))
            except ValueError:
                raise SetupError('invalid threshold') from None
            points[point] = dict(enabled=enabled, threshold=threshold)
        privacy = answers.setdefault('privacy', {})
        try:
            privacy['max_state_chars'] = int(prompt('现状截断长度', str(existing.get('redact', {}).get('max_state_chars', 4000))))
        except ValueError:
            raise SetupError('invalid truncation length') from None
        privacy['strict'] = yes('strict 过滤（需要至少一条规则）?', existing.get('redact', {}).get('strict', False))
    privacy = answers.setdefault('privacy', {})
    for item in proposals:
        print(f"隐私建议来源 {item['source']}，字面匹配，示例命中 1 次，效果 {item['effect']}")
        privacy[item['key']] = yes('启用 ' + item['key'] + ' 规则?', existing.get('setup', {}).get('privacy', {}).get(item['key'], True))
    project = prompt('Claude Code hooks 项目目录，留空跳过')
    if project:
        answers['hooks'] = dict(project=project, scope='shared' if yes('使用项目共享设置?') else 'local')
    return answers


def safe_display(text, proposals):
    text = redact_state(text)
    # The privacy patterns themselves contain escaped identity literals.
    for item in proposals:
        text = text.replace(item['pattern'], '<literal:' + item['key'] + '>')
        text = text.replace(json.dumps(item['pattern'], ensure_ascii=False)[1:-1], '<literal:' + item['key'] + '>')
        text = re.sub(item['pattern'], lambda _: item['replacement'], text)
    return text


def hide_existing_strings(value, key=None):
    if isinstance(value, str):
        return '<已有 hook，内容不显示>' if key == 'command' else '<已有内容，内容不显示>'
    if isinstance(value, dict):
        return {key: hide_existing_strings(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [hide_existing_strings(item) for item in value]
    return value


def display_input(raw, path, fragment=None):
    """Construct a display copy; retain original Change bytes for commit/backup."""
    if not raw:
        return 'text', ''
    if path.suffix == '.toml':
        value = tomllib.loads(raw.decode())
        redaction = value.get('redact', {})
        if 'patterns' in redaction:
            # Regex source and replacement text may themselves contain credentials.
            redaction['patterns'] = ['<FILTER_RULE_HIDDEN>' for _ in redaction['patterns']]
        return 'toml', value
    if path.suffix == '.json':
        value = json.loads(raw)
        display = hide_existing_strings(value)
        if fragment and isinstance(value, dict):
            for event, expected in fragment.items():
                for index, entry in enumerate(value.get('hooks', {}).get(event, [])):
                    # Compare the entire entry, not a command substring or marker.
                    # User-added fields remain private even beside an azir command.
                    if entry == expected[0]:
                        display['hooks'][event][index] = copy.deepcopy(expected[0])
        return 'json', display
    return 'text', raw.decode()


def describe_changes(changes, proposals, apply_authorized, authorized_hooks, existing, candidate, hook_target=None):
    changed = [change for change in changes if change.changed]
    if not changed:
        return []
    patterns = []
    for config in (existing, candidate):
        for rule in config.get('redact', {}).get('patterns', []):
            if rule not in patterns:
                patterns.append(rule)
    fragment = hook_fragment(changes[0].path) if hook_target is not None else None
    documents = [display_input(raw, change.path, fragment if change.path == hook_target else None)
                 for change in changed for raw in (change.old, change.new)]
    # JSON has already been reduced to placeholders and exact generated entries.
    # Custom rules must not rewrite those placeholders or the generated commands.
    filtered_indexes = [index for index, (kind, _) in enumerate(documents) if kind != 'json']
    deadline = time.monotonic() + 3
    try:
        # Filter decoded strings first: JSON escaping must not defeat configured rules
        # on multiline commands. Then protect the serialized display as well.
        values = [value for _, value in documents]
        if filtered_indexes:
            filtered = filter_value([values[index] for index in filtered_indexes], patterns, deadline)
            for index, value in zip(filtered_indexes, filtered):
                values[index] = value
        rendered = []
        for (kind, _), value in zip(documents, values):
            if kind == 'toml':
                text = toml_bytes(value).decode()
            elif kind == 'json':
                text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
            else:
                text = value
            rendered.append(text)
        if filtered_indexes:
            filtered = filter_value([rendered[index] for index in filtered_indexes], patterns, deadline)
            for index, value in zip(filtered_indexes, filtered):
                rendered[index] = value
    except (FilterError, FilterTimeout):
        raise SetupError('cannot safely display the plan; filtering failed or timed out') from None
    result = []
    for index, change in enumerate(changed):
        old, new = rendered[index * 2:index * 2 + 2]
        diff = ''.join(difflib.unified_diff(safe_display(old, proposals).splitlines(True), safe_display(new, proposals).splitlines(True), fromfile='before', tofile='after'))
        entry = dict(path=str(change.path), operation='update' if change.old is not None else 'create',
                     old_hash=digest(change.old), new_hash=digest(change.new), mode='0600',
                     authorized=apply_authorized and (change is changes[0] or authorized_hooks), diff=diff,
                     backup='private state/backups/<transaction>/' if change.old is not None else None)
        if change.path.suffix == '.toml':
            before = existing.get('redact', {}).get('patterns', [])
            after = candidate.get('redact', {}).get('patterns', [])
            entry['filter_rules'] = dict(before_count=len(before), after_count=len(after),
                                         changed=before != after, definitions='hidden')
        result.append(entry)
    return result


def run(args):
    answer_source = args.answers or args.answers_json
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not answer_source
    # A person at a terminal gets a readable summary; pipes, agents and --json get the JSON.
    human = sys.stdout.isatty() and not getattr(args, 'json', False)
    result = None

    def emit(value, config=None, path=None):
        if human and config is not None:
            from .setup_summary import summary
            print(summary(value, config, path))
        elif human:
            print(tr('setup.status.needs_confirmation'))
        else:
            print(json.dumps(value, ensure_ascii=False))
    try:
        state = state_directory()
        unfinished(state)
        answers = read_answers_json(args.answers_json) if args.answers_json else read_answers(args.answers)
        path = directory_path(args.config_dir) / 'config.toml'
        old = guarded(path)
        existing = tomllib.loads(old.decode()) if old else {}
        detected, proposals, inventory = environment(), privacy_proposals(), skill_inventory()
        if interactive:
            show_environment(detected)
            answers = interactive_answers(answers, existing, detected, proposals, inventory)
        config = candidate(existing, answers, detected, proposals, inventory)
        if args.allow_online_test and config['jev']['mode'] != 'online':
            raise SetupError('online test contradicts offline mode; explicitly select online')
        new = old if existing == config else toml_bytes(config)
        changes = [Change(path, old, new)]
        hook_answers = answers.get('hooks', {})
        project = hook_answers.get('project') or args.allow_hooks
        hooks = None
        if args.allow_hooks and project and directory_path(args.allow_hooks) != directory_path(project):
            raise SetupError('hook authorization must match the planned project')
        if project:
            if 'claude-code' not in config['dispatch']['executors']:
                raise SetupError('Claude Code hooks require a claude-code candidate pool')
            hook_changes, hooks = plan_hooks(project, hook_answers.get('scope', 'local'), path, state)
            hooks['authorized'] = bool(args.allow_hooks)
            changes += hook_changes
        result = dict(status='planned', environment=detected, skills=inventory,
                      privacy=[dict(key=item['key'], source=item['source'], effect=item['effect'], enabled=config.get('setup', {}).get('privacy', {}).get(item['key'], True)) for item in proposals],
                      changes=describe_changes(changes, proposals, args.apply, bool(args.allow_hooks), existing, config,
                                               Path(hooks['target']) if hooks else None), hooks=hooks,
                      offline_defaults=dict(dispatch=config['dispatch']['defaults'], next_step=_point('next_step', config).default,
                                            wrapup=_point('wrapup', config).default, skill=config['skill']['default']),
                      codex_command=shlex.join(['azir-dispatch-codex', 'run', '--config', str(path), '--task-file', 'task.md']),
                      unverified=['offline_adapter_protocol', 'dashboard_readable', 'online_jev', 'real_hook_session', 'executor_account_and_model'],
                      verification=dict(config_valid='passed', offline_dispatch='not_run', adapter_offline_protocol='not_run', dashboard_readable='not_run', hooks='not_written', online_jev='not_tested'),
                      authorizations=dict(config=args.apply, hooks=bool(args.allow_hooks), online_test=args.allow_online_test),
                      storage=dict(transactions=str(state / 'transactions'), backups=str(state / 'backups'), locks=str(state / 'locks'), new_directory_mode='0700', file_mode='0600'),
                      online_test=dict(endpoint=config['jev']['url'], model=config['jev']['model'], text='azir setup test', retries=0) if args.allow_online_test else None,
                      summary='Review the local configuration plan before applying.')
        if interactive:
            print('计划摘要：配置、hooks 与安装记录；新目录 0700，文件 0600，备份仅在私有状态目录。')
            for change in result['changes']:
                print(change['path'])
                print(change['diff'])
            if not args.apply and not yes('Apply local configuration? 保存本地配置?'):
                result['status'] = 'needs_confirmation'
                emit(result, config, path)
                return 0
            args.apply = True
            if hooks and not args.allow_hooks:
                args.allow_hooks = project if yes('授权在此项目写入 hooks?') else None
            if config['jev']['mode'] == 'online' and not args.allow_online_test:
                print('在线测试接收方 ' + config['jev']['url'] + '，型号 ' + config['jev']['model'] + '；仅发 azir setup test，不重试。')
                args.allow_online_test = yes('授权一次真实 JEV 测试?')
            result['authorizations'] = dict(config=args.apply, hooks=bool(args.allow_hooks), online_test=args.allow_online_test)
            result['changes'] = describe_changes(changes, proposals, args.apply, bool(args.allow_hooks), existing, config,
                                                 Path(hooks['target']) if hooks else None)
        if args.apply:
            # For noninteractive application, the reviewed plan is also visible on stderr
            # before writes; stdout remains exactly one machine-readable result.
            if not interactive and not human:
                print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
            result['verification'] = verify(config)
            result['unverified'] = ['online_jev', 'real_hook_session', 'executor_account_and_model']
            authorized_changes = changes if args.allow_hooks else changes[:1]
            for change in authorized_changes:
                if digest(guarded(change.path)) != digest(change.old):
                    raise SetupError('target changed after planning; regenerate the plan', path=str(change.path))
            result['transaction'] = commit(authorized_changes, state)
            if args.allow_hooks:
                result['verification']['hooks'] = 'written_real_session_unverified'
                hooks['status'] = 'written_real_session_unverified'
                hooks['authorized'] = True
            result['status'] = 'applied'
            from .doctor import snapshot
            snapshot(config, steps={'models': 'ok' if detected['can_dispatch'] else 'missing'}, probe_versions=False, refresh=False)
            if args.allow_online_test:
                status, error = online_test(config)
                result['verification']['online_jev'] = status
                result['online_error'] = error
                if status == 'tested':
                    result['unverified'].remove('online_jev')
        elif args.allow_online_test or args.allow_hooks:
            result['status'] = 'needs_confirmation'
            result['summary'] = 'Application requires --apply; the plan has not been submitted.'
        emit(result, config, path)
        return 0
    except (EOFError, KeyboardInterrupt):
        emit(dict(status='needs_confirmation', reason='cancelled', unverified=['online_jev', 'real_hook_session', 'executor_account_and_model']))
        return 0
    except (SetupError, OSError, ValueError, TypeError, UnicodeError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else 'cannot read, validate or commit configuration'
        print(message, file=sys.stderr)
        failure = dict(status='failed', error=message)
        if isinstance(exc, SetupError):
            failure.update(exc.details)
        if not human:
            print(json.dumps(failure, ensure_ascii=False))
        return 2
