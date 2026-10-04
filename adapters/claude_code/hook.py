#!/usr/bin/env python3
"""Read a Claude Code hook JSON object; emit one hook JSON object."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adapters.common import (add_options, ask, combination, executor_config,
                             hint, load_settings, read_events, record, safe_text)

ACTOR = 'claude-code'


def feedback(event, message, **fields):
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': message, **fields}}


def handle_hook(data, config, log):
    event = data.get('hook_event_name')
    if data.get('tool_name') not in ('Agent', 'Task') or data.get('agent_id'):
        return {}
    session = data.get('session_id')
    call = data.get('tool_use_id')
    if not session or not call:
        raise ValueError('session_id and tool_use_id are required')
    run_id = f'{session}:{call}'
    task_id = run_id
    tool_input = data.get('tool_input', {})
    if not isinstance(tool_input, dict):
        raise ValueError('tool_input must be an object')
    context = json.dumps(tool_input, ensure_ascii=False)
    dispatch_config = executor_config(config, ACTOR)
    identity = dict(config=config, run_id=run_id, task_id=task_id, actor=ACTOR)
    decisions = dict(config=dispatch_config, log=log, run_id=run_id, task_id=task_id, actor=ACTOR)
    if event == 'PreToolUse':
        result = ask('dispatch', context, **decisions)
        if result['disposition'] == 'handback':
            record(log, 'handback', reason='dispatch confidence below threshold', caused_by=result['event_id'], **identity)
            return feedback(event, hint('dispatch', result), permissionDecision='deny', permissionDecisionReason=hint('dispatch', result))
        suggested = combination(result['answer'])
        skill_result = ask('skill', context, scope='development', caused_by=result['event_id'], **decisions)
        if skill_result['disposition'] == 'handback':
            record(log, 'handback', reason='skill decision handed back', caused_by=skill_result['event_id'], **identity)
            message = hint('skill', skill_result)
            return feedback(event, message, permissionDecision='deny', permissionDecisionReason=message)
        skill = skill_result['answer']
        routes = config.get('adapters', {}).get('claude_code', {}).get('routes', {})
        route = routes.get(result['answer'])
        requested = dict(executor=ACTOR, model=tool_input.get('model', 'inherit'), effort=None)
        actual = suggested if route else requested
        fields = {}
        message = hint('dispatch', result) + ' ' + hint('skill', skill_result)
        if route is not None and (not isinstance(route, str) or not route.strip()):
            raise ValueError('route must name a subagent')
        if route:
            # A route names a user-defined agent with matching model/effort.
            fields['updatedInput'] = dict(tool_input, subagent_type=route, model=suggested['model'])
            message += f' Selected configured subagent {route}.'
        else:
            message += ' No configured subagent route; retain the original executor parameters and treat the executor selection as advice.'
        if skill != 'none':
            updated = fields.setdefault('updatedInput', dict(tool_input))
            updated['prompt'] = f'用 {skill} 技能，{tool_input.get("prompt", "")}'
        record(log, 'dispatch', requested=requested, suggested=suggested, actual=actual,
               executor=actual['executor'], model=actual['model'], effort=actual['effort'],
               task=safe_text(tool_input.get('prompt', ''), config)[:200], target=run_id,
               skill_requested=None, skill_suggested=skill_result['jev_choice'], skill=skill,
               caused_by=skill_result['event_id'], **identity)
        return feedback(event, message, **fields)
    if event not in ('PostToolUse', 'PostToolUseFailure'):
        return {}
    events = [e for e in read_events(log) if e.get('run_id') == run_id]
    response = data.get('tool_response', {})
    # A successful Agent call may only acknowledge a background launch.
    if event == 'PostToolUse' and isinstance(response, dict) and (tool_input.get('run_in_background') is True or response.get('status') in ('async_launched', 'running', 'queued') or response.get('isAsync') is True):
        return feedback(event, 'azir: background launch acknowledged; completion is not established. Report its final outcome with the report command.')
    dispatch = next((e for e in reversed(events) if e.get('type') == 'dispatch'), None)
    if dispatch is None:
        return feedback(event, 'azir: no matching dispatch record; outcome was not attributed.')
    failed = event == 'PostToolUseFailure' or (isinstance(response, dict) and response.get('status') in ('failed', 'error', 'interrupted'))
    detail = data.get('error') or json.dumps(response, ensure_ascii=False)
    outcome = record(log, 'failed' if failed else 'done', target=run_id, detail=detail,
                     caused_by=dispatch['id'], **identity)
    point = 'next_step' if failed else 'wrapup'
    result = ask(point, detail, caused_by=outcome['id'], **decisions)
    return feedback(event, hint(point, result))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    parser.add_argument('--report', choices=('done', 'failed'))
    parser.add_argument('--run-id')
    parser.add_argument('--detail-file')
    args = parser.parse_args(argv)
    try:
        config, log = load_settings(args)
        if args.report:
            if not args.run_id or ':' not in args.run_id or not args.detail_file:
                raise ValueError('report needs run-id and detail-file')
            session, call = args.run_id.rsplit(':', 1)
            if not any(e.get('run_id') == args.run_id and e.get('type') == 'dispatch' for e in read_events(log)):
                raise ValueError('no matching dispatch')
            detail = Path(args.detail_file).read_text()
            data = {'hook_event_name': 'PostToolUseFailure' if args.report == 'failed' else 'PostToolUse',
                    'session_id': session, 'tool_use_id': call, 'tool_name': 'Agent',
                    'tool_input': {}, 'tool_response': detail, 'error': detail if args.report == 'failed' else None}
        else:
            data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError('hook input must be an object')
        output = handle_hook(data, config, log)
    except (OSError, ValueError, TypeError, KeyError):
        if args.report:
            print('azir: outcome could not be recorded; check the report arguments and event log.', file=sys.stderr)
            return 2
        # Fail open without changing input or granting permission.
        output = {'systemMessage': 'azir adapter error; dispatching session must decide. Check the adapter configuration and event log.'}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
