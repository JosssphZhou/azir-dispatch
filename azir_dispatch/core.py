"""Decision points and the single public decision entry point."""

import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .events import append_event
from .filtering import FilterError, FilterTimeout, filter_value


DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Point:
    name: str
    question: str
    options: dict[str, str]
    default: str
    threshold: float


def _dispatch_description(executor, model, effort):
    role = ("界面视觉设计、配色排版和长文写作" if "claude" in executor.lower()
            else "代码开发、脚本维护和代码审查")
    level = {
        "low": "范围明确、风险低的机械修改",
        "medium": "局部修改，依赖关系明确",
        "high": "常规任务，需要分析实现和验证结果",
        "xhigh": "跨模块重大重构、共享基础设施或安全敏感改动",
        "max": "复杂任务，需要深入分析多个方案",
    }.get(effort, "按任务要求选择此推理等级")
    return f"{role}；{level}。执行者 {executor}，型号 {model}，推理等级 {effort}。"


def _point(name, config, scope=None):
    points = config.get("points", {})
    if not isinstance(points, dict):
        raise ValueError("points must be a table")
    settings = points.get(name, {})
    if not isinstance(settings, dict):
        raise ValueError("point settings must be a table")
    threshold = float(settings.get("threshold", 0.7))
    if name == "dispatch":
        dispatch = config.get("dispatch", {})
        executors = dispatch.get("executors", {"codex": {"models": {"model-a": ["medium"]}}})
        options = {}
        for executor, executor_settings in executors.items():
            for model, efforts in executor_settings.get("models", {}).items():
                for effort in efforts:
                    options[f"{executor}:{model}:{effort}"] = _dispatch_description(executor, model, effort)
        default = dispatch.get("default", next(iter(options), ""))
        if scope is not None:
            defaults = dispatch.get('defaults', {})
            if not isinstance(scope, str) or scope not in defaults:
                raise ValueError('dispatch scope must be configured')
            default = defaults[scope]
        question = "依据当前任务类型和复杂程度，应该选择哪个执行者、型号和推理等级？"
    elif name == "skill":
        skill = config.get("skill", {})
        if not isinstance(skill, dict):
            raise ValueError("skill must be a table")
        candidates = skill.get("candidates", {})
        defaults = skill.get("default", {})
        if not isinstance(candidates, dict) or not isinstance(defaults, dict):
            raise ValueError("skill candidates and defaults must be tables")
        if not isinstance(scope, str) or not scope or scope not in candidates:
            raise ValueError("skill scope must be configured")
        choices = candidates[scope]
        if (not isinstance(choices, list) or not choices
                or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
                or len(set(choices)) != len(choices)):
            raise ValueError("skill candidates must be nonempty strings")
        descriptions = {
            "none": "没有适用的专门技能，按任务书执行，不指定技能。",
            "tdd": "开发功能或修复缺陷，需要先写失败测试，再实现并验证。",
            "diagnosing-bugs": "排查错误、失败或性能回退，先复现并验证原因，再修复。",
            "implement": "需求和实现范围已明确，按任务书完成开发与验证。",
            "code-review": "只读检查代码是否符合编码标准和任务规格，提交审查报告。",
        }
        options = {choice: descriptions.get(choice, f"任务明确要求使用 {choice} 技能时选择。")
                   for choice in choices}
        default = defaults.get(scope)
        if not isinstance(default, str):
            raise ValueError("skill default must be a string")
        question = f"在 {scope} 范围内，依据当前任务要求应该使用哪个技能？"
    elif name == "next_step":
        options = {
            "continue": "按审查意见或报错修改后重新提交（常规退修）。存在可修复问题时选择。",
            "rethink": "换一种做法重新设计。原方案已被证据否定，局部修复无法解决时选择。",
            "stop": "停下交回，需要人决定。缺少授权、规格冲突或外部阻塞时选择。审查不通过本身不等于停下。",
        }
        default = settings.get('default', 'stop')
        question = "审查不通过或执行失败后，下一步应该常规退修、重新设计还是停下交回？"
    elif name == "wrapup":
        options = {
            "close_and_clean": "任务通过审查、所有改动已保存并完成合并、工作树无未提交内容且已有清理授权时，关闭窗格并清理工作树。",
            "close_keep": "任务通过审查，执行者无需继续，但任务分支或工作树还需保留供合并和复核时，关闭窗格、保留工作树。",
            "keep": "任务尚未通过审查、需要退修或执行者仍有后续工作时，保留窗格和工作树。",
        }
        default = settings.get('default', 'keep')
        question = "依据审查结果、后续工作和清理授权，完成任务后应该如何处理执行窗格和工作树？"
    else:
        raise ValueError(f"unknown decision point: {name}")
    if not options or default not in options:
        raise ValueError(f"empty options or invalid default for {name}")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be finite and between 0 and 1")
    for field in ("question", "instructions"):
        if field in settings and (not isinstance(settings[field], str) or not settings[field].strip()):
            raise ValueError(f"{field} must be a nonempty string")
    question = settings.get("question", question)
    if "instructions" in settings:
        question += "\n" + settings["instructions"]
    criteria = settings.get("criteria", {})
    if not isinstance(criteria, dict):
        raise ValueError("criteria must be a table")
    known_choices = set(options)
    if name == "skill":
        known_choices.update(choice for choices in candidates.values() if isinstance(choices, list)
                             for choice in choices if isinstance(choice, str))
    for choice, description in criteria.items():
        if choice not in known_choices or not isinstance(description, str) or not description.strip():
            raise ValueError("criteria must describe existing choices with nonempty strings")
        if choice in options:
            options[choice] = description
    return Point(name, question, options, default, threshold)


def _remaining(deadline):
    return max(0, deadline - time.monotonic())


def _command_key(command, deadline):
    process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=max(0.001, _remaining(deadline) - 0.1))
        if process.returncode != 0:
            raise RuntimeError("key command failed")
        return stdout.decode("utf-8").strip()
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise


def _fetch(request, deadline):
    """Bound the whole HTTP exchange, including a slow response body."""
    if _remaining(deadline) <= 0.1:
        raise TimeoutError
    finished = threading.Event()
    outcome = {}

    def run():
        try:
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=max(0.001, _remaining(deadline))) as response:
                outcome["payload"] = json.load(response)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=run, daemon=True).start()
    if not finished.wait(timeout=max(0.001, _remaining(deadline) - 0.1)):
        raise TimeoutError
    if "error" in outcome:
        raise outcome["error"]
    return outcome["payload"]


def decide(point, state, *, config, log, run_id=None, actor=None, task_id=None, caused_by=None,
           scope=None, requested=None):
    """Choose an answer, write one event, and return the adopted decision."""
    if (scope is not None and point not in ('dispatch', 'skill')) or (requested is not None and point != 'skill'):
        raise ValueError("scope applies to dispatch or skill; requested applies only to skill")
    if requested is not None and (not isinstance(requested, str) or not requested.strip()):
        raise ValueError("requested must be a nonempty skill string")
    from .roles import apply_roles
    config = apply_roles(config)
    point_def = _point(point, config, scope)
    jev = config.get("jev", {})
    redaction = config.get("redact", {})
    budget = float(jev.get("timeout", 3))
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("timeout must be finite and positive")
    max_chars = int(redaction.get("max_state_chars", 4000))
    if max_chars <= 0:
        raise ValueError("max_state_chars must be positive")

    started = time.monotonic()
    deadline = started + budget
    choice = None
    confidence = None
    cost = 0
    error = None
    truncated = False
    safe_state = ""
    safe_requested = None
    safe_question = ""
    safe_options = {}
    safe_task_title = ""
    probabilities = None

    try:
        patterns = redaction.get("patterns", [])
        if redaction.get("strict") and not patterns:
            raise RedactionFailure("missing_extra_rules")
        context = f"调用方建议的技能：{requested}\n{state}" if requested is not None else state
        # Filter prompt descriptions too: configuration may contain private team context.
        filtered = filter_value({"state": context, "requested": requested,
                                 "question": point_def.question, "options": point_def.options},
                                patterns, deadline)
        safe_state, safe_requested = filtered["state"], filtered["requested"]
        safe_question, safe_options = filtered["question"], filtered["options"]
        if redaction.get("strict") and not safe_state.strip():
            raise RedactionFailure("empty_filtered_state")
        truncated = len(safe_state) > max_chars
        safe_state = safe_state[:max_chars]
        safe_task_title = safe_state.splitlines()[0][:120] if safe_state.splitlines() else ""
    except RedactionFailure as exc:
        error = str(exc)
    except FilterTimeout:
        error = "filter_timeout"
    except FilterError:
        error = "filter_error"
    except Exception:
        error = "filter_error"

    if not error and not _remaining(deadline):
        error = "timeout"
    key = None
    if not error and jev.get("mode") in ('offline', 'rules'):
        error = jev['mode']
    if not error and config.get("points", {}).get(point, {}).get("enabled") is False:
        error = "disabled"
    if not error:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key and jev.get("key_command"):
            try:
                key = _command_key(jev["key_command"], deadline)
            except subprocess.TimeoutExpired:
                error = "timeout"
            except (OSError, ValueError, RuntimeError, UnicodeError):
                error = "key_command_failed"
        if not key and not error:
            error = "missing_api_key"
    if not error and not _remaining(deadline):
        error = "timeout"

    if not error:
        cost = None
        body = {"model": jev.get("model", DEFAULT_MODEL), "state": safe_state,
                "questions": {point: {"type": "choice", "instructions": safe_question,
                                      "criteria": safe_options}}}
        try:
            request = urllib.request.Request(jev.get("url", DEFAULT_URL),
                                             data=json.dumps(body).encode("utf-8"),
                                             headers={"Authorization": f"Bearer {key}",
                                                      "Content-Type": "application/json"}, method="POST")
            payload = _fetch(request, deadline)
            if not _remaining(deadline):
                raise TimeoutError
            answer_data = payload["answers"][point]
            choice = answer_data["choice"]
            confidence = answer_data["confidence"]
            probabilities = answer_data.get("probabilities")
            usage = payload.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError("invalid usage")
            cost = usage.get("cost")
            if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))
                                     or not math.isfinite(cost) or cost < 0):
                raise ValueError("invalid cost")
            if choice not in point_def.options:
                raise InvalidChoice
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("invalid confidence")
        except (socket.timeout, TimeoutError):
            error = "timeout"
        except urllib.error.HTTPError as exc:
            exc.close()
            error = "http_error"
        except InvalidChoice:
            error = "invalid_choice"
        except urllib.error.URLError as exc:
            error = "timeout" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "network_error"
        except (OSError, ValueError, KeyError, TypeError):
            error = "invalid_response"
        if error:
            choice = None
            confidence = None
            cost = None

    source = 'rules' if error in ('offline', 'rules', 'missing_api_key', 'disabled') else 'default' if error else 'jev'
    disposition = "handback" if not error and confidence < point_def.threshold else "apply"
    answer = None if disposition == "handback" else choice if source == "jev" else point_def.default
    event_fields = {"point": point, "question": safe_question, "options": safe_options,
                    "jev_choice": choice, "answer": answer, "confidence": confidence,
                    "threshold": point_def.threshold, "source": source,
                    "disposition": disposition, "cost": cost,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": error, "state_preview": safe_state[:200],
                    "state_head": safe_state[:400], "task_title": safe_task_title,
                    "probabilities": probabilities,
                    "truncated": truncated}
    rules_mode = (source == 'rules' or jev.get('mode') in ('offline', 'rules')
                  or ('OPENROUTER_API_KEY' not in set(os.environ) and not jev.get('key_command')))
    event_fields['jev_mode'] = 'rules' if rules_mode else 'jev'
    if point == "skill":
        event_fields.update(scope=scope, requested=safe_requested)
    elif point == 'dispatch' and scope is not None:
        event_fields.update(scope=scope)
    try:
        event = append_event(log, "decision", run_id=run_id, actor=actor,
                             task_id=task_id, caused_by=caused_by, deadline=deadline,
                             **event_fields)
        event_id = event["id"]
    except (OSError, ValueError):
        source = "default"
        disposition = "handback"
        answer = None
        error = "记录失败"
        event_id = None
    return {"answer": answer, "jev_choice": choice, "confidence": confidence,
            "threshold": point_def.threshold, "source": source,
            "disposition": disposition, "cost": cost, "event_id": event_id,
            "error": error}


class InvalidChoice(Exception):
    pass


class RedactionFailure(Exception):
    pass
