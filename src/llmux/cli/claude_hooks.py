"""Installer for Claude Code edit-safety hooks used with ``llmux-claude``.

Installation is explicit (``llmux-claude install-hooks``). LLMux itself never
runs tools or edits project files at runtime — Claude Code owns hooks — so this
command only writes/merges into the target project's ``.claude/`` tree.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

HOOK_MARKER = "llmux-python-syntax-hook"

# PostToolUse: after Edit/Write on *.py, fail closed if the file no longer
# parses. Uses python3 only (Claude Code JSON on stdin). Do not depend on jq —
# missing jq caused a silent no-op on the advisor server (2026-07-24).
SYNTAX_HOOK_COMMAND = f"""\
_llmux_hook={HOOK_MARKER}; python3 -c '
import ast, json, sys
payload = json.load(sys.stdin)
path = (payload.get("tool_input") or {{}}).get("file_path") or ""
if not str(path).endswith(".py"):
    raise SystemExit(0)
try:
    ast.parse(open(path, encoding="utf-8").read())
except SyntaxError as exc:
    print(
        f"SYNTAX ERROR after editing {{path}} — the file no longer parses as valid Python. "
        "Stop, re-read the full file, and fix before continuing:",
        file=sys.stderr,
    )
    print(exc, file=sys.stderr)
    raise SystemExit(2)
'
"""

EDIT_SAFETY_RULE_NAME = "llmux-edit-safety.md"

EDIT_SAFETY_RULE_MD = """\
# Edit safety (llmux)

Installed by `llmux-claude install-hooks`. Applies to every session
(interactive and autonomous), not only auto mode.

## Circuit-breaker — stop repetitive edit loops

Four loop signals:

1. **Same Bash error** — identical stderr 3+ times in a row.
2. **Edit → revert** — edit then undo the same file, cycle repeats (2 cycles).
3. **Read without progress** — same file read 3+ times with no edit/output.
4. **Edit without re-read** — same (or near-same) Edit applied 2+ times on the
   same file without an intervening Read of the real post-edit state.

| Signal | Threshold | Action |
|--------|-----------|--------|
| Same Bash + same stderr | 3 | Stop; report `BLOCKED: loop detected` |
| Edit+revert same file | 2 cycles | Stop; ask the user |
| Read same file, no output | 3 | Stop with a short diagnosis |
| Edit repeated without re-read | 2 | Stop; re-read the full file; confirm it still parses |

## Behavioural guards

- Before a second `Edit` on the same file, `Read` it again — do not assume the
  previous edit left the file as intended.
- If a file is left inconsistent by your own edits, prefer restoring that single
  path from git over blind `sed`/`awk` surgery.
- The managed `PostToolUse` hook runs `ast.parse` on `*.py` after Edit/Write; if
  it reports a syntax error, fix that file before any other work.
"""


def settings_path(project_root: Path) -> Path:
    return project_root / ".claude" / "settings.json"


def rules_path(project_root: Path) -> Path:
    return project_root / ".claude" / "rules" / EDIT_SAFETY_RULE_NAME


def syntax_hook_entry() -> dict[str, Any]:
    return {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": SYNTAX_HOOK_COMMAND}],
    }


def _is_managed_post_tool_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if isinstance(command, str) and HOOK_MARKER in command:
            return True
    return False


def merge_syntax_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """Return settings with the managed PostToolUse syntax hook installed."""

    merged = dict(settings)
    hooks_raw = merged.get("hooks")
    hooks_root: dict[str, Any] = dict(hooks_raw) if isinstance(hooks_raw, dict) else {}

    post_raw = hooks_root.get("PostToolUse")
    if not isinstance(post_raw, list):
        post: list[object] = []
    else:
        post = [entry for entry in post_raw if not _is_managed_post_tool_entry(entry)]

    post.append(syntax_hook_entry())
    hooks_root["PostToolUse"] = post
    merged["hooks"] = hooks_root
    return merged


def install_hooks(project_root: Path | None = None) -> list[Path]:
    """Merge the syntax hook and write the edit-safety rule. Returns paths written."""

    root = (project_root or Path.cwd()).resolve()
    settings_file = settings_path(root)
    rule_file = rules_path(root)
    written: list[Path] = []

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if settings_file.is_file():
        try:
            loaded = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Refusing to modify invalid JSON at {settings_file}: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise SystemExit(
                f"Refusing to modify {settings_file}: top-level JSON must be an object"
            )
        existing = loaded
        backup = settings_file.with_suffix(".json.bak")
        shutil.copy2(settings_file, backup)
        print(f"Backed up existing settings to {backup}", file=sys.stderr)

    merged = merge_syntax_hook(existing)
    settings_file.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    written.append(settings_file)

    rule_file.parent.mkdir(parents=True, exist_ok=True)
    if rule_file.is_file():
        backup_rule = rule_file.with_suffix(".md.bak")
        shutil.copy2(rule_file, backup_rule)
        print(f"Backed up existing rule to {backup_rule}", file=sys.stderr)
    rule_file.write_text(EDIT_SAFETY_RULE_MD, encoding="utf-8")
    written.append(rule_file)

    return written


def install_hooks_cli(argv: Sequence[str] | None = None) -> None:
    """CLI for ``llmux-claude install-hooks``."""

    parser = argparse.ArgumentParser(
        prog="llmux-claude install-hooks",
        description=(
            "Install Claude Code PostToolUse Python syntax checks and an "
            "edit-safety rule into a project's .claude/ directory."
        ),
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Project root (default: current working directory).",
    )
    args = parser.parse_args(list(argv) if argv is not None else [])
    paths = install_hooks(args.path)
    for path in paths:
        print(f"Wrote {path}")
    print(
        "Hooks are owned by Claude Code in this project; restart or open a new "
        "session for them to take effect."
    )
