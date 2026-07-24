#!/usr/bin/env python3
"""PreToolUse Bash: block git commits whose message claims work the staged diff lacks.

Catches false-close commits: message names paths or asserts completed work that
is not in the index.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

MARKER = "llmux-commit-claim-guard"

_PATH_CLAIM = re.compile(
    r"`((?:src|tests|scripts|smoke)/[^`\s]+)`|"
    r"(?<![\w./-])((?:src|tests|scripts|smoke)/[\w./-]+\.(?:py|md|toml|yml|yaml))",
)

_DONEISH = re.compile(
    r"\b(?:fixed|implemented|implementado|implementada|completed|completado|"
    r"completada|done|resuelto|unificar|unificado|unificada|"
    r"única fuente|unica fuente)\b",
    re.I,
)


def _deny(reason: str) -> int:
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


def _commit_message(command: str) -> str | None:
    if not re.search(r"git\s+commit\b", command):
        return None

    # -m '...' / -m "..."
    m = re.search(
        r"git\s+commit\b[^;&|]*?-m\s+(['\"])(?P<msg>.*?)(?<!\\)\1", command, re.S
    )
    if m:
        return m.group("msg")

    # -F path (message file)
    fm = re.search(r"git\s+commit\b[^;&|]*?-F\s+(?P<path>\S+)", command)
    if fm:
        raw = fm.group("path").strip("'\"")
        try:
            return Path(raw).read_text(encoding="utf-8")
        except OSError:
            return ""

    # Heredoc: <<'EOF' / <<"EOF" / <<EOF … EOF
    if "<<" in command:
        hm = re.search(
            r"<<\s*['\"]?(?P<tag>\w+)['\"]?\s*\n(?P<body>.*?)\n(?P=tag)\b",
            command,
            re.S,
        )
        if hm:
            return hm.group("body")
        return ""

    # Bare ``git commit`` / ``git commit --amend`` without inline message.
    return ""


def _staged_text() -> dict[str, str]:
    try:
        listed = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "-z"],
            stderr=subprocess.DEVNULL,
        )
    except OSError, subprocess.CalledProcessError:
        return {}
    paths = [p.decode() for p in listed.split(b"\0") if p]
    out: dict[str, str] = {}
    for path in paths:
        try:
            blob = subprocess.check_output(
                ["git", "show", f":{path}"],
                stderr=subprocess.DEVNULL,
            )
        except OSError, subprocess.CalledProcessError:
            out[path] = ""
            continue
        try:
            out[path] = blob.decode("utf-8")
        except UnicodeDecodeError:
            out[path] = ""
    return out


def _claimed_paths(msg: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _PATH_CLAIM.finditer(msg):
        path = match.group(1) or match.group(2)
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def _check_path_claims(msg: str, staged: dict[str, str]) -> str | None:
    claimed = _claimed_paths(msg)
    if not claimed:
        return None
    missing = [p for p in claimed if p not in staged]
    if missing:
        return (
            f"BLOCKED: commit message names {missing[0]} but that path is not "
            f"staged. Stage the claimed files or drop them from the message. "
            f"({MARKER})"
        )
    return None


def _check_empty_done_claim(msg: str, staged: dict[str, str]) -> str | None:
    if not msg.strip() or staged:
        return None
    if not _DONEISH.search(msg):
        return None
    return (
        f"BLOCKED: commit message claims completed work but the index is empty. "
        f"Stage the real changes first. ({MARKER})"
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
    msg = _commit_message(command)
    if msg is None:
        return 0

    staged = _staged_text()
    err = _check_path_claims(msg, staged)
    if err:
        return _deny(err)
    err = _check_empty_done_claim(msg, staged)
    if err:
        return _deny(err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
