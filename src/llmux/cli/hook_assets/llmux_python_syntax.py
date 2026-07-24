#!/usr/bin/env python3
"""PostToolUse: fail closed if a just-edited *.py file no longer parses.

Installed by ``llmux-claude install-hooks``. Do not run formatters here — silent
rewrites make the next Edit miss its old_string (advisor incident 2026-07-24).
"""

import ast
import json
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not str(path).endswith(".py"):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        ast.parse(source)
    except SyntaxError as exc:
        print(
            f"SYNTAX ERROR after editing {path} — the file no longer parses as "
            "valid Python. Stop, re-read the full file, and fix before continuing:",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
