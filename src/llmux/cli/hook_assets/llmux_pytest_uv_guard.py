#!/usr/bin/env python3
"""PreToolUse Bash: require ``uv run pytest`` (project venv), not bare pytest."""

import json
import re
import sys

MARKER = "llmux-pytest-uv-guard"

_BARE_PYTEST = re.compile(
    r"(^|[;&|]\s*)(pytest|python3?\s+-m\s+pytest)\b",
)


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return 0
    tool = str(payload.get("tool_name") or "")
    if tool and tool != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    command = str(tool_input.get("command") or "")
    if _BARE_PYTEST.search(command) and "uv run" not in command:
        reason = (
            f"Blocked: run tests with `uv run pytest ...` so the project venv "
            f"is used. ({MARKER})"
        )
        print(reason, file=sys.stderr)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
