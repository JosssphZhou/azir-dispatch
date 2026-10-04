"""Configuration, decision calls and event reporting shared by adapters."""
import copy
import json
import os
from pathlib import Path

from azir_dispatch import decide
from azir_dispatch.events import append_event
from azir_dispatch.redact import redact_state


def add_options(parser, *, suppress=False):
    import argparse
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument('--config', default=default)
    parser.add_argument('--log', default=default)


def load_settings(args):
    path = args.config or os.environ.get('AZIR_DISPATCH_CONFIG')
    from azir_dispatch.doctor import load_config
    from azir_dispatch.roles import apply_roles
    from azir_dispatch.setup_state import state_directory
    config = apply_roles(load_config(path))
    log = args.log or os.environ.get('AZIR_DISPATCH_LOG') or config.get('log', {}).get('path') or str(state_directory() / 'events.jsonl')
    return config, Path(log).expanduser()


def executor_config(config, executor):
    """Keep only combinations this adapter can execute, using public config."""
    result = copy.deepcopy(config)
    dispatch = result.setdefault('dispatch', {})
    available = dispatch.get('executors', {}).get(executor)
    if not isinstance(available, dict) or not available:
        raise ValueError(f'configure dispatch.executors.{executor}.models first')
    models = available.get('models', {})
    if not isinstance(models, dict):
        raise ValueError('models must be a table')
    for model, efforts in models.items():
        if not model or not isinstance(efforts, list) or not efforts or any(not isinstance(effort, str) or not effort for effort in efforts):
            raise ValueError('each model must have a nonempty list of effort strings')
    options = [f'{executor}:{model}:{effort}' for model, efforts in models.items() for effort in efforts]
    if not options or any(len(option.split(':')) != 3 for option in options):
        raise ValueError('models and efforts must be nonempty and contain no colon')
    point = result.get('points', {}).get('dispatch', {})
    if 'criteria' in point:
        criteria = point['criteria']
        known = {f'{name}:{model}:{effort}'
                 for name, settings in dispatch['executors'].items()
                 for model, efforts in settings.get('models', {}).items() for effort in efforts}
        if (not isinstance(criteria, dict) or any(choice not in known or not isinstance(text, str)
                                                  or not text.strip() for choice, text in criteria.items())):
            raise ValueError('criteria must describe existing choices with nonempty strings')
        # Descriptions follow the same candidate restriction as the executor pool.
        point['criteria'] = {choice: text for choice, text in criteria.items() if choice in options}
    dispatch['executors'] = {executor: available}
    override = config.get('adapters', {}).get(executor.replace('-', '_'), {}).get('default')
    default = override or dispatch.get('default')
    if override and override not in options:
        raise ValueError('adapter default must be a configured combination')
    dispatch['default'] = default if default in options else options[0]
    # This adapter intentionally executes only its own CLI. Role resolution was
    # already applied during setup; preserve this restricted candidate pool.
    result.pop('roles', None)
    return result


def combination(answer):
    executor, model, effort = answer.split(':')
    return dict(executor=executor, model=model, effort=effort)


def safe_text(text, config):
    return redact_state(str(text), config.get('redact', {}).get('patterns', []))


def record(log, kind, *, config, **fields):
    def safe(value):
        if isinstance(value, str):
            return safe_text(value, config)
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        return value
    event = append_event(log, kind, **{k: safe(v) for k, v in fields.items()})
    if kind == 'dispatch':
        from azir_dispatch.doctor import snapshot
        snapshot(config, steps={'first_dispatch': 'ok'}, probe_versions=False)
    return event


def ask(point, state, *, config, log, run_id, task_id, actor, caused_by=None,
        scope=None, requested=None):
    return decide(point, state, config=config, log=log, run_id=run_id,
                  task_id=task_id, actor=actor, caused_by=caused_by,
                  scope=scope, requested=requested)


def hint(point, result):
    if result['disposition'] == 'handback':
        return f'azir {point}: confidence is low; hand back to the dispatching session to decide (suggestion: {result["jev_choice"]}).'
    return f'azir {point}: {result["answer"]} (source={result["source"]}); this is a suggestion only for next_step/wrapup.'


def read_events(log):
    if not log.exists():
        return []
    result = []
    with log.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                result.append(event)
    return result
