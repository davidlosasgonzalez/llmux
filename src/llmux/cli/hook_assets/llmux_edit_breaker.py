#!/usr/bin/env python3
"""Hard edit/bash circuit-breaker for Claude Code (llmux-claude install-hooks).

Enforces the soft rules that models ignore under pressure:

- After a successful Edit/Write, the path is "dirty" until Read — the next
  Edit/Write on that path is denied (exit 2) so old_string cannot target a
  stale view (or a silently reformatted file).
- After 2 failed Edit/Write attempts on the same path without an intervening
  Read, further Edit/Write on that path is denied.
- After 3 identical Bash failures (same command + same error fingerprint),
  that command is denied on PreToolUse.

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

_MARKER = "llmux-edit-breaker"


def _state_path(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"{_MARKER}-{digest}.json"


def _load(session_id: str) -> dict[str, Any]:
    path = _state_path(session_id)
    if not path.is_file():
        return {
            "dirty": {},
            "edit_fails": {},
            "bash_fails": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {
            "dirty": {},
            "edit_fails": {},
            "bash_fails": {},
        }
    if not isinstance(data, dict):
        return {
            "dirty": {},
            "edit_fails": {},
            "bash_fails": {},
        }
    dirty = data.get("dirty")
    edit_fails = data.get("edit_fails")
    bash_fails = data.get("bash_fails")
    return {
        "dirty": dict(dirty) if isinstance(dirty, dict) else {},
        "edit_fails": dict(edit_fails) if isinstance(edit_fails, dict) else {},
        "bash_fails": dict(bash_fails) if isinstance(bash_fails, dict) else {},
    }


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


def _bash_key(command: str, error: str) -> str:
    # Collapse whitespace so trivial re-wrapping does not reset the counter.
    cmd = re.sub(r"\s+", " ", command).strip()
    err = re.sub(r"\s+", " ", error).strip()
    # Keep stderr short — full traces vary by line noise but the head is stable.
    err_head = err[:400]
    raw = f"{cmd}\n{err_head}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cmd_only_key(command: str) -> str:
    cmd = re.sub(r"\s+", " ", command).strip()
    return hashlib.sha256(cmd.encode()).hexdigest()


def _deny(reason: str) -> int:
    print(reason, file=sys.stderr)
    # Also emit structured deny for clients that prefer JSON decisions.
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision), file=sys.stdout)
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
    state["dirty"].pop(path, None)
    state["edit_fails"].pop(path, None)


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
        if state["dirty"].get(path):
            return _deny(
                f"BLOCKED: {path} was edited since your last Read of it. "
                f"Read the file again before the next Edit/Write so old_string "
                f"matches disk. ({_MARKER})"
            )
        return 0

    if tool == "Bash":
        command = _bash_command(payload)
        if not command:
            return 0
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
    # A successful edit resets the failure streak for that path.
    state["edit_fails"].pop(path, None)
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
        # Treat a failed edit as dirty so a blind retry cannot proceed without Read.
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
