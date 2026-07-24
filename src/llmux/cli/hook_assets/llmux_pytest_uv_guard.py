#!/usr/bin/env python3
"""PreToolUse Bash: require ``uv run pytest`` (project venv), not bare pytest."""

import json
import re
import sys

MARKER = "llmux-pytest-uv-guard"

_SEGMENT_SEP = re.compile(r"[;&|\n]")
_BARE_PYTEST = re.compile(r"^(?:pytest|python3?\s+-m\s+pytest)\b")


def _segments(command: str) -> list[str]:
    return [s.strip() for s in _SEGMENT_SEP.split(command) if s.strip()]


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
    for segment in _segments(command):
        if _BARE_PYTEST.match(segment):
            reason = (
                f"Blocked: run tests with `uv run pytest ...` so the project "
                f"venv is used. ({MARKER})"
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
