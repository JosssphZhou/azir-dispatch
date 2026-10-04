import argparse
import json
import os
import sys
import time
from pathlib import Path

from .core import _point, decide
from .events import append_event
from .filtering import FilterError, FilterTimeout, filter_value


SKILL_FIELDS = {"skill_requested", "skill_suggested", "skill"}
RECORD_FIELDS = {
    "dispatch": {"requested", "suggested", "actual", "task", "target"} | SKILL_FIELDS,
    "done": {"target", "detail"},
    "failed": {"target", "detail"},
    "handback": {"reason"},
    "advisor": {"session", "cwd"},
}


def _runtime_decision(error):
    return {"answer": None, "jev_choice": None, "confidence": None,
            "threshold": None, "source": "default", "disposition": "handback",
            "cost": 0, "event_id": None, "error": error}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="azir-dispatch")
    subcommands = parser.add_subparsers(dest="command", required=True)
    from .setup import add_parser, run as setup_run
    add_parser(subcommands)
    from . import doctor, demo
    doctor.add_parser(subcommands)
    demo.add_parser(subcommands)
    decide_parser = subcommands.add_parser("decide")
    decide_parser.add_argument("point", choices=["dispatch", "next_step", "wrapup", "skill"])
    decide_parser.add_argument("--scope")
    decide_parser.add_argument("--requested")
    decide_parser.add_argument("--state-file", required=True)
    decide_parser.add_argument("--config")
    decide_parser.add_argument("--log")
    decide_parser.add_argument("--run-id")
    decide_parser.add_argument("--actor")
    decide_parser.add_argument("--task-id")
    decide_parser.add_argument("--caused-by")
    record_parser = subcommands.add_parser("record")
    record_parser.add_argument("type", choices=RECORD_FIELDS)
    record_parser.add_argument("--run-id", required=True)
    record_parser.add_argument("--actor")
    record_parser.add_argument("--task-id")
    record_parser.add_argument("--caused-by")
    record_parser.add_argument("--config")
    record_parser.add_argument("--log")
    record_parser.add_argument("--field", action="append", default=[])
    args = parser.parse_args(argv)

    if args.command == "setup":
        return setup_run(args)
    if args.command == 'doctor':
        return doctor.run(args)
    if args.command == 'demo':
        return demo.run(args)

    try:
        from .roles import apply_roles
        config = apply_roles(doctor.load_config(args.config))
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        parser.error("cannot read config")
    if not isinstance(config.get("log", {}), dict):
        parser.error("invalid log configuration")
    from .setup_state import state_directory
    log = args.log or os.environ.get("AZIR_DISPATCH_LOG") or config.get("log", {}).get("path") or str(state_directory() / 'events.jsonl')

    if args.command == "decide":
        if (args.scope is not None and args.point not in ('dispatch', 'skill')) or (args.requested is not None and args.point != 'skill'):
            parser.error("--scope applies to dispatch or skill; --requested applies only to skill")
        try:
            if args.requested is not None and not args.requested.strip():
                raise ValueError("requested must be nonempty")
            _point(args.point, config, args.scope)
        except (ValueError, TypeError, AttributeError):
            parser.error("invalid decision scope or configuration")
        try:
            state = sys.stdin.read() if args.state_file == "-" else Path(args.state_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            result = _runtime_decision("state_read_error")
        else:
            try:
                result = decide(args.point, state, config=config, log=log,
                                run_id=args.run_id, actor=args.actor,
                                task_id=args.task_id, caused_by=args.caused_by,
                                scope=args.scope, requested=args.requested)
            except (ValueError, TypeError):
                parser.error("invalid configuration")
            except Exception:
                result = _runtime_decision("runtime_error")
    else:
        fields = {}
        for field in args.field:
            if "=" not in field:
                parser.error("--field must be k=v")
            key, value = field.split("=", 1)
            if key not in RECORD_FIELDS[args.type] or key in fields:
                parser.error(f"invalid or repeated field: {key}")
            fields[key] = value
        required = RECORD_FIELDS[args.type] - SKILL_FIELDS if args.type == "dispatch" else RECORD_FIELDS[args.type]
        if not required <= set(fields):
            parser.error(f"{args.type} requires: {', '.join(sorted(required))}")
        if args.type == "dispatch":
            try:
                for key in SKILL_FIELDS:
                    value = json.loads(fields[key]) if key in fields else None
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f"{key} must be null or a skill string")
                    fields[key] = value
                for key in ("requested", "suggested", "actual"):
                    fields[key] = json.loads(fields[key])
                for key in ("requested", "actual"):
                    if not isinstance(fields[key], dict) or set(fields[key]) != {"executor", "model", "effort"} or not all(isinstance(value, str) for value in fields[key].values()):
                        raise ValueError(f"{key} must contain executor, model, effort")
                if fields["suggested"] is not None and (not isinstance(fields["suggested"], dict) or set(fields["suggested"]) != {"executor", "model", "effort"} or not all(isinstance(value, str) for value in fields["suggested"].values())):
                    raise ValueError("suggested must be null or a combination")
                for key in ("executor", "model", "effort"):
                    fields[key] = fields["actual"][key]
            except (ValueError, TypeError) as exc:
                parser.error(f"invalid dispatch fields: {exc}")
        try:
            redaction = config.get("redact", {})
            if redaction.get("strict") and not redaction.get("patterns"):
                raise FilterError
            values = filter_value({"fields": fields, "run_id": args.run_id,
                                   "actor": args.actor, "task_id": args.task_id,
                                   "caused_by": args.caused_by},
                                  redaction.get("patterns", []),
                                  time.monotonic() + float(config.get("jev", {}).get("timeout", 3)))
            result = append_event(log, args.type, run_id=values["run_id"],
                                  actor=values["actor"], task_id=values["task_id"],
                                  caused_by=values["caused_by"], **values["fields"])
            if args.type == 'dispatch':
                doctor.snapshot(config, steps={'first_dispatch': 'ok'}, probe_versions=False)
        except FilterTimeout:
            result = {"ok": False, "error": "filter_timeout", "event_id": None}
        except FilterError:
            result = {"ok": False, "error": "filter_error", "event_id": None}
        except (OSError, ValueError, TypeError):
            result = {"ok": False, "error": "record_failed", "event_id": None}
        except Exception:
            result = {"ok": False, "error": "runtime_error", "event_id": None}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
