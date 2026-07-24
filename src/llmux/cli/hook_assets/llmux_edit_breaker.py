#!/usr/bin/env python3
"""Hard edit/bash circuit-breaker for Claude Code (llmux-claude install-hooks).

Enforces rules models ignore under pressure:

- After a successful Edit/Write, the path is dirty until Read.
- After 2 failed Edit/Write attempts on the same path without Read, deny.
- After 3 identical Bash failures (same command + same error), deny.
- Edit that exactly reverses the previous Edit on the same path (Edit↔revert)
  is counted; 2 cycles → deny until Read.
- Bash that silently mutates ``*.py`` (sed -i, redirects, ruff --fix/format,
  etc.) is denied — use the Edit tool so the dirty-path breaker can track it.

State is per Claude ``session_id`` under the system temp dir.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

EDIT_FAIL_LIMIT = 2
BASH_FAIL_LIMIT = 3
REVERT_CYCLE_LIMIT = 2

_MARKER = "llmux-edit-breaker"

# Bash that rewrites Python on disk without going through Edit/Write.
_PY_MUTATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsed\b[^\n]*\s-i\b"),
    re.compile(r"\bperl\b[^\n]*\s-i\b"),
    re.compile(r"\bruff\b[^\n]*\b(?:format|check)\b[^\n]*--fix\b"),
    re.compile(r"\bruff\b[^\n]*\bformat\b"),
    re.compile(r"\bblack\b"),
    re.compile(r"\bisort\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:cat|tee|printf|echo)\b[^\n]*>+\s*[^\s;|&]+\.py\b"),
    re.compile(r"\btee\b[^\n]*\b[\w./-]+\.py\b"),
    re.compile(r">\s*[\w./-]+\.py\b"),
    re.compile(r"\bcp\b[^\n]+\.py\b"),
    re.compile(r"\bmv\b[^\n]+\.py\b"),
    re.compile(r"\btruncate\b[^\n]+\.py\b"),
)


def _state_path(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"{_MARKER}-{digest}.json"


def _load(session_id: str) -> dict[str, Any]:
    path = _state_path(session_id)
    empty = {
        "dirty": {},
        "edit_fails": {},
        "bash_fails": {},
        "last_edit": {},
        "revert_cycles": {},
    }
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty
    out = dict(empty)
    for key in empty:
        raw = data.get(key)
        out[key] = dict(raw) if isinstance(raw, dict) else {}
    return out


def _save(session_id: str, state: dict[str, Any]) -> None:
    path = _state_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _norm_path(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve())
    except OSError:
        return text


def _file_path(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    return _norm_path(tool_input.get("file_path"))


def _bash_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or "").strip()


def _edit_strings(payload: dict[str, Any]) -> tuple[str, str]:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return "", ""
    return (
        str(tool_input.get("old_string") or ""),
        str(tool_input.get("new_string") or ""),
    )


def _bash_key(command: str, error: str) -> str:
    cmd = re.sub(r"\s+", " ", command).strip()
    err = re.sub(r"\s+", " ", error).strip()
    err_head = err[:400]
    return hashlib.sha256(f"{cmd}\n{err_head}".encode()).hexdigest()


def _cmd_only_key(command: str) -> str:
    cmd = re.sub(r"\s+", " ", command).strip()
    return hashlib.sha256(cmd.encode()).hexdigest()


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


def _additional_context(text: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUseFailure",
                    "additionalContext": text,
                }
            }
        )
    )
    return 0


def _clear_path(state: dict[str, Any], path: str) -> None:
    # Keep last_edit / revert_cycles across Read so Edit↔revert still detects
    # after the mandatory re-read that follows a successful Edit.
    state["dirty"].pop(path, None)
    state["edit_fails"].pop(path, None)


def _bash_mutates_python(command: str) -> bool:
    """True when Bash would rewrite Python sources outside Edit/Write."""

    # Formatters rewrite the tree even when argv omits an explicit ``.py``.
    if re.search(r"\bruff\b[^\n]*\bformat\b", command):
        return True
    if re.search(r"\bruff\b[^\n]*\bcheck\b[^\n]*--fix\b", command):
        return True
    if re.search(r"\b(?:black|isort)\b", command):
        return True
    if ".py" not in command:
        return False
    return any(p.search(command) for p in _PY_MUTATE_PATTERNS)


def _handle_pre(payload: dict[str, Any], state: dict[str, Any]) -> int:
    tool = str(payload.get("tool_name") or "")
    if tool == "Read":
        path = _file_path(payload)
        if path:
            _clear_path(state, path)
        return 0

    if tool in ("Edit", "Write"):
        path = _file_path(payload)
        if not path:
            return 0
        fails = int(state["edit_fails"].get(path) or 0)
        if fails >= EDIT_FAIL_LIMIT:
            return _deny(
                f"BLOCKED: loop detected — {fails} failed Edit/Write on {path} "
                f"without a successful Read. Re-read the full file (or restore "
                f"from git) before editing again. ({_MARKER})"
            )
        if int(state["revert_cycles"].get(path) or 0) >= REVERT_CYCLE_LIMIT:
            return _deny(
                f"BLOCKED: loop detected — Edit↔revert cycled "
                f"{REVERT_CYCLE_LIMIT}+ times on {path}. Stop oscillating; "
                f"Read the file and pick one direction. ({_MARKER})"
            )
        if state["dirty"].get(path):
            return _deny(
                f"BLOCKED: {path} was edited since your last Read of it. "
                f"Read the file again before the next Edit/Write so old_string "
                f"matches disk. ({_MARKER})"
            )
        # Detect pending revert against last successful edit (same turn sequence).
        if tool == "Edit":
            old_s, new_s = _edit_strings(payload)
            prev = state["last_edit"].get(path)
            if (
                isinstance(prev, dict)
                and old_s
                and new_s
                and prev.get("new") == old_s
                and prev.get("old") == new_s
            ):
                cycles = int(state["revert_cycles"].get(path) or 0) + 1
                state["revert_cycles"][path] = cycles
                if cycles >= REVERT_CYCLE_LIMIT:
                    return _deny(
                        f"BLOCKED: loop detected — this Edit reverts the previous "
                        f"one on {path} (cycle {cycles}). Read and decide. "
                        f"({_MARKER})"
                    )
        return 0

    if tool == "Bash":
        command = _bash_command(payload)
        if not command:
            return 0
        if _bash_mutates_python(command):
            return _deny(
                f"BLOCKED: Bash must not silently rewrite Python sources "
                f"(sed -i / redirects / ruff format|--fix / black / …). "
                f"Use the Edit or Write tool so the edit-safety breaker can "
                f"track dirty paths. ({_MARKER})"
            )
        cmd_key = _cmd_only_key(command)
        entry = state["bash_fails"].get(cmd_key)
        if isinstance(entry, dict) and int(entry.get("count") or 0) >= BASH_FAIL_LIMIT:
            return _deny(
                f"BLOCKED: loop detected — same Bash command failed "
                f"{BASH_FAIL_LIMIT}+ times with the same error. Diagnose instead "
                f"of retrying. Last error head: {entry.get('error_head', '')!s}. "
                f"({_MARKER})"
            )
        return 0

    return 0


def _handle_post_success(payload: dict[str, Any], state: dict[str, Any]) -> int:
    tool = str(payload.get("tool_name") or "")
    if tool not in ("Edit", "Write"):
        return 0
    path = _file_path(payload)
    if not path:
        return 0
    state["dirty"][path] = True
    state["edit_fails"].pop(path, None)
    if tool == "Edit":
        old_s, new_s = _edit_strings(payload)
        if old_s or new_s:
            prev = state["last_edit"].get(path)
            is_revert = (
                isinstance(prev, dict)
                and prev.get("new") == old_s
                and prev.get("old") == new_s
            )
            if not is_revert:
                state["revert_cycles"][path] = 0
            state["last_edit"][path] = {"old": old_s, "new": new_s}
    return 0


def _handle_post_failure(payload: dict[str, Any], state: dict[str, Any]) -> int:
    tool = str(payload.get("tool_name") or "")
    error = str(payload.get("error") or "")

    if tool in ("Edit", "Write"):
        path = _file_path(payload)
        if not path:
            return 0
        fails = int(state["edit_fails"].get(path) or 0) + 1
        state["edit_fails"][path] = fails
        state["dirty"][path] = True
        if fails >= EDIT_FAIL_LIMIT:
            return _additional_context(
                f"BLOCKED: loop detected — {fails} failed Edit/Write on {path}. "
                f"Do not retry the same Edit. Read the full file (or git restore "
                f"that path) before continuing. ({_MARKER})"
            )
        return _additional_context(
            f"Edit/Write failed on {path} ({fails}/{EDIT_FAIL_LIMIT} before block). "
            f"Read the file before retrying. ({_MARKER})"
        )

    if tool == "Bash":
        command = _bash_command(payload)
        if not command:
            return 0
        cmd_key = _cmd_only_key(command)
        err_key = _bash_key(command, error)
        prev = state["bash_fails"].get(cmd_key)
        if isinstance(prev, dict) and prev.get("err_key") == err_key:
            count = int(prev.get("count") or 0) + 1
        else:
            count = 1
        state["bash_fails"][cmd_key] = {
            "err_key": err_key,
            "count": count,
            "error_head": re.sub(r"\s+", " ", error).strip()[:400],
        }
        if count >= BASH_FAIL_LIMIT:
            return _additional_context(
                f"BLOCKED: loop detected — Bash failed {count} times with the "
                f"same error. Stop retrying; diagnose. ({_MARKER})"
            )
        return 0

    return 0


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return 0
    session_id = str(payload.get("session_id") or "unknown")
    event = str(payload.get("hook_event_name") or "")
    state = _load(session_id)
    code = 0
    if event == "PreToolUse":
        code = _handle_pre(payload, state)
    elif event == "PostToolUse":
        code = _handle_post_success(payload, state)
    elif event == "PostToolUseFailure":
        code = _handle_post_failure(payload, state)
    _save(session_id, state)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
