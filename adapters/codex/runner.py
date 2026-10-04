"""Run a task using a configured Codex model and reasoning effort."""
import argparse
import json
import subprocess
import sys
import uuid
import tempfile
from pathlib import Path

from adapters.common import (add_options, ask, combination, executor_config,
                             hint, load_settings, record, safe_text)


def run_task(args, config, log):
    task = Path(args.task_file).expanduser().read_text()
    if args.cwd and not Path(args.cwd).is_dir():
        raise ValueError('cwd must be an existing directory')
    selected_config = executor_config(config, 'codex')
    run_id = args.run_id or uuid.uuid4().hex
    task_id = args.task_id or run_id
    actor = 'codex'
    decisions = dict(config=selected_config, log=log, run_id=run_id, task_id=task_id, actor=actor)
    identity = dict(config=config, run_id=run_id, task_id=task_id, actor=actor)
    result = ask('dispatch', task, **decisions)
    if result['disposition'] == 'handback':
        record(log, 'handback', reason='dispatch confidence below threshold', caused_by=result['event_id'], **identity)
        print(hint('dispatch', result), file=sys.stderr)
        return 4
    selected = combination(result['answer'])
    default = combination(selected_config['dispatch']['default'])
    skill_result = ask('skill', task, scope='development', caused_by=result['event_id'], **decisions)
    if skill_result['disposition'] == 'handback':
        record(log, 'handback', reason='skill decision handed back', caused_by=skill_result['event_id'], **identity)
        print(hint('skill', skill_result), file=sys.stderr)
        return 4
    skill = skill_result['answer']
    prompt = f'用 {skill} 技能，{task}' if skill != 'none' else task
    dispatch = record(log, 'dispatch', requested=default,
                      suggested=combination(result['jev_choice']) if result['jev_choice'] else None,
                      actual=selected, **selected, task=safe_text(task, config)[:200], target=run_id,
                      skill_requested=None, skill_suggested=skill_result['jev_choice'], skill=skill,
                      caused_by=skill_result['event_id'], **identity)
    command = args.codex_command or config.get('adapters', {}).get('codex', {}).get('command', 'codex')
    argv = [str(Path(command).expanduser()), 'exec']
    if selected['model'] != 'inherit':
        argv += ['-m', selected['model']]
    if selected['effort'] != 'inherit':
        argv += ['-c', 'model_reasoning_effort=' + json.dumps(selected['effort'])]
    if args.cwd:
        argv += ['-C', str(Path(args.cwd).resolve())]
    argv += ['-']
    # Preserve terminal output and credential boundaries until full redaction.
    stderr_chunks = []
    tail = ''
    code = 130
    process = None
    try:
        with tempfile.TemporaryFile(mode='w+t') as input_file:
            input_file.write(prompt)
            input_file.seek(0)
            process = subprocess.Popen(argv, stdin=input_file, stderr=subprocess.PIPE)
            for line in iter(process.stderr.readline, b''):
                text = line.decode('utf-8', errors='replace')
                sys.stderr.write(text)
                stderr_chunks.append(text)
            process.stderr.close()
            code = process.wait()
            tail = safe_text(''.join(stderr_chunks), config)[-4000:]
    except OSError:
        code, tail = 127, 'codex could not be started'
    except KeyboardInterrupt:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        tail = 'codex run interrupted'
    outcome = record(log, 'failed' if code else 'done', target=run_id,
                     detail=f'exit_code={code}\n{tail}' if code else 'exit_code=0',
                     caused_by=dispatch['id'], **identity)
    point = 'next_step' if code else 'wrapup'
    followup = ask(point, f'Exit code: {code}\nRecent stderr:\n{tail}\nTask:\n{task}', caused_by=outcome['id'], **decisions)
    print(hint(point, followup), file=sys.stderr)
    return code if code >= 0 else 128 - code


def main(argv=None):
    parser = argparse.ArgumentParser(prog='azir-dispatch-codex')
    add_options(parser)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run')
    add_options(run, suppress=True)
    run.add_argument('--task-file', required=True)
    run.add_argument('--cwd')
    run.add_argument('--codex-command')
    run.add_argument('--run-id')
    run.add_argument('--task-id')
    args = parser.parse_args(argv)
    try:
        config, log = load_settings(args)
        return run_task(args, config, log)
    except (OSError, ValueError, TypeError, KeyError):
        print('azir codex adapter: invalid input, configuration or event log; inspect these before retrying.', file=sys.stderr)
        return 2
