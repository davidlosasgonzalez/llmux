#!/usr/bin/env python3
"""SessionStart(compact): re-inject continuity after Claude Code autocompact.

Claude Code summarizes the transcript when the context window fills. That is
correct for token limits but drops verification detail — models then claim
work is done from memory of the summary. Re-inject git status, recent
commits, and a verify-before-done reminder into the fresh context.
"""

import contextlib
import json
import subprocess
import sys


def _run(argv: list[str]) -> str:
    try:
        return subprocess.check_output(
            argv,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        return ""


def main() -> int:
    # Consume stdin payload (SessionStart JSON) even if unused.
    with contextlib.suppress(json.JSONDecodeError):
        json.load(sys.stdin)

    parts = [
        "LLMux continuity after compact — do not trust summary claims blindly:",
        "- Re-read files you will edit; prefer disk over memory of this chat.",
        "- Before saying a task is done: run the relevant tests "
        "(`uv run pytest …`) and confirm the staged/working tree matches the "
        "claim.",
        "- Commit messages must match staged diffs (claim guard enforces this).",
    ]
    status = _run(["git", "status", "-sb"])
    if status.strip():
        parts.append("git status:\n" + status.strip()[:2000])
    log = _run(["git", "log", "-5", "--oneline"])
    if log.strip():
        parts.append("recent commits:\n" + log.strip()[:800])
    # SessionStart hooks: stdout is injected as context (Claude Code docs).
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
