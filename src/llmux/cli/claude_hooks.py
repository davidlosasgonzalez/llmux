"""Installer for Claude Code edit-safety hooks used with ``llmux-claude``.

Installation is explicit (``llmux-claude install-hooks``). LLMux itself never
runs tools or edits project files at runtime — Claude Code owns hooks — so this
command only writes/merges into the target project's ``.claude/`` tree.

Installed hooks:

- PostToolUse ``ast.parse`` on ``*.py`` after Edit/Write (fail closed). Does
  **not** run formatters — silent rewrites break the next Edit's ``old_string``.
- PreToolUse / PostToolUse / PostToolUseFailure circuit-breaker: require Read
  between edits on the same path; stop after repeated Edit or Bash failures.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

HOOK_MARKER = "llmux-python-syntax-hook"
BREAKER_MARKER = "llmux-edit-breaker"

SYNTAX_SCRIPT_NAME = "llmux_python_syntax.py"
BREAKER_SCRIPT_NAME = "llmux_edit_breaker.py"

EDIT_SAFETY_RULE_NAME = "llmux-edit-safety.md"

EDIT_SAFETY_RULE_MD = """\
# Edit safety (llmux)

Installed by `llmux-claude install-hooks`. Applies to every session
(interactive and autonomous), not only auto mode.

## Hard circuit-breaker (hooks)

Enforced by `.claude/hooks/llmux_edit_breaker.py` — not optional markdown:

1. After a successful Edit/Write, that path is dirty until you `Read` it.
   Another Edit/Write on the same path is **blocked** until you re-read.
2. After 2 failed Edit/Write attempts on the same path without a Read, further
   edits on that path are **blocked**.
3. After 3 identical Bash failures (same command + same error), that command
   is **blocked**.

## Soft signals (still apply)

1. **Same Bash error** — identical stderr 3+ times in a row (hard-blocked above).
2. **Edit → revert** — edit then undo the same file, cycle repeats (2 cycles).
3. **Read without progress** — same file read 3+ times with no edit/output.
4. **Edit without re-read** — hard-blocked above after the first successful edit.

| Signal | Threshold | Action |
|--------|-----------|--------|
| Same Bash + same stderr | 3 | Deny tool (`BLOCKED: loop detected`) |
| Edit+revert same file | 2 cycles | Stop; ask the user |
| Read same file, no output | 3 | Stop with a short diagnosis |
| Edit after dirty path / failed Edit x2 | -- | Deny until Read |

## Behavioural guards

- Before a second `Edit` on the same file, `Read` it again — the PreToolUse
  hook will deny otherwise.
- If a file is left inconsistent by your own edits, prefer restoring that single
  path from git over blind `sed`/`awk` surgery.
- The managed PostToolUse hook runs `ast.parse` on `*.py` after Edit/Write; if
  it reports a syntax error, fix that file before any other work.
- Do **not** add silent formatters (`ruff format`, `ruff check --fix`) to
  PostToolUse — they rewrite the file under Claude and cause Edit loops.
"""


def settings_path(project_root: Path) -> Path:
    return project_root / ".claude" / "settings.json"


def rules_path(project_root: Path) -> Path:
    return project_root / ".claude" / "rules" / EDIT_SAFETY_RULE_NAME


def hooks_dir(project_root: Path) -> Path:
    return project_root / ".claude" / "hooks"


def syntax_hook_command() -> str:
    return (
        f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{SYNTAX_SCRIPT_NAME}  # {HOOK_MARKER}"
    )


def breaker_hook_command() -> str:
    return (
        f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{BREAKER_SCRIPT_NAME}  "
        f"# {BREAKER_MARKER}"
    )


def syntax_hook_entry() -> dict[str, Any]:
    return {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": syntax_hook_command()}],
    }


def breaker_pre_entry() -> dict[str, Any]:
    return {
        "matcher": "Read|Edit|Write|Bash",
        "hooks": [{"type": "command", "command": breaker_hook_command()}],
    }


def breaker_post_entry() -> dict[str, Any]:
    return {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": breaker_hook_command()}],
    }


def breaker_failure_entry() -> dict[str, Any]:
    return {
        "matcher": "Edit|Write|Bash",
        "hooks": [{"type": "command", "command": breaker_hook_command()}],
    }


def _entry_has_marker(entry: object, marker: str) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if isinstance(command, str) and marker in command:
            return True
    return False


def _strip_marker_entries(entries: list[object], marker: str) -> list[object]:
    return [entry for entry in entries if not _entry_has_marker(entry, marker)]


def _ensure_list(hooks_root: dict[str, Any], key: str) -> list[object]:
    raw = hooks_root.get(key)
    if isinstance(raw, list):
        return list(raw)
    return []


def _is_ruff_autofix_post_entry(entry: object) -> bool:
    """Detect advisor-style PostToolUse that silently runs ruff --fix/format."""

    if not isinstance(entry, dict):
        return False
    matcher = str(entry.get("matcher") or "")
    if "Edit" not in matcher and "Write" not in matcher:
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        if "ruff" in command and ("--fix" in command or "format" in command):
            return True
    return False


def merge_hooks(
    settings: dict[str, Any], *, strip_ruff_autofix: bool = True
) -> dict[str, Any]:
    """Return settings with managed syntax + breaker hooks installed."""

    merged = dict(settings)
    hooks_raw = merged.get("hooks")
    hooks_root: dict[str, Any] = dict(hooks_raw) if isinstance(hooks_raw, dict) else {}

    pre = _strip_marker_entries(_ensure_list(hooks_root, "PreToolUse"), BREAKER_MARKER)
    post = _strip_marker_entries(_ensure_list(hooks_root, "PostToolUse"), HOOK_MARKER)
    post = _strip_marker_entries(post, BREAKER_MARKER)
    fail = _strip_marker_entries(
        _ensure_list(hooks_root, "PostToolUseFailure"), BREAKER_MARKER
    )

    if strip_ruff_autofix:
        post = [entry for entry in post if not _is_ruff_autofix_post_entry(entry)]

    pre.append(breaker_pre_entry())
    post.append(syntax_hook_entry())
    post.append(breaker_post_entry())
    fail.append(breaker_failure_entry())

    hooks_root["PreToolUse"] = pre
    hooks_root["PostToolUse"] = post
    hooks_root["PostToolUseFailure"] = fail
    merged["hooks"] = hooks_root
    return merged


def merge_syntax_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """Back-compat alias for ``merge_hooks``."""

    return merge_hooks(settings)


def _asset_text(name: str) -> str:
    return files("llmux.cli.hook_assets").joinpath(name).read_text(encoding="utf-8")


def install_hook_scripts(project_root: Path) -> list[Path]:
    dest_dir = hooks_dir(project_root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in (SYNTAX_SCRIPT_NAME, BREAKER_SCRIPT_NAME):
        dest = dest_dir / name
        dest.write_text(_asset_text(name), encoding="utf-8")
        dest.chmod(dest.stat().st_mode | 0o111)
        written.append(dest)
    return written


def install_hooks(project_root: Path | None = None) -> list[Path]:
    """Merge managed hooks and write scripts + edit-safety rule."""

    root = (project_root or Path.cwd()).resolve()
    settings_file = settings_path(root)
    rule_file = rules_path(root)
    written: list[Path] = []

    written.extend(install_hook_scripts(root))

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

    merged = merge_hooks(existing)
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
            "Install Claude Code PostToolUse Python syntax checks, a hard "
            "edit/bash circuit-breaker, and an edit-safety rule into a "
            "project's .claude/ directory. Strips silent ruff --fix/format "
            "PostToolUse hooks that cause Edit loops."
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
