"""Remove credential-like text before sending or previewing state."""

import re


PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
ASSIGNMENT = re.compile(r"(?i)(password|token|secret)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
BEARER = re.compile(r"(?i)Bearer\s+[^\s,;]+")
API_KEY = re.compile(r"(?i)(?<![A-Za-z0-9_-])(?:sk-or-|sk-|ghp_|gho_|github_pat_|xox[bp]-|AKIA)[A-Za-z0-9_-]+")
JWT = re.compile(r"[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")


def redact_state(state, patterns=()):
    result = PRIVATE_KEY.sub("[REDACTED]", state)
    result = BEARER.sub("Bearer [REDACTED]", result)
    result = ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)
    result = API_KEY.sub("[REDACTED]", result)
    result = JWT.sub("[REDACTED]", result)
    for pattern in patterns:
        if isinstance(pattern, dict):
            result = re.sub(pattern['pattern'], lambda _: pattern['replacement'], result)
        else:
            result = re.sub(pattern, "[REDACTED]", result)
    return result


def redact_value(value, patterns=()):
    if isinstance(value, str):
        return redact_state(value, patterns)
    if isinstance(value, list):
        return [redact_value(item, patterns) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, patterns) for key, item in value.items()}
    return value
