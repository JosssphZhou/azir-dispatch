"""Run configurable regular expressions outside the caller process."""

import json
import sys

from redact import redact_value


def main():
    try:
        request = json.load(sys.stdin)
        result = redact_value(request["value"], request["patterns"])
        json.dump(result, sys.stdout, ensure_ascii=False)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
