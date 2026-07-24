"""Tests for ``llmux-claude install-hooks``."""

import json
import subprocess
import sys
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from llmux.cli.claude_hooks import (
    BREAKER_MARKER,
    BREAKER_SCRIPT_NAME,
    COMMIT_GUARD_MARKER,
    COMMIT_GUARD_SCRIPT_NAME,
    COMPACT_REINJECT_MARKER,
    COMPACT_REINJECT_SCRIPT_NAME,
    EDIT_SAFETY_RULE_NAME,
    HOOK_MARKER,
    PYTEST_GUARD_MARKER,
    PYTEST_GUARD_SCRIPT_NAME,
    SYNTAX_SCRIPT_NAME,
    install_hooks,
    install_hooks_cli,
    merge_hooks,
    merge_syntax_hook,
    rules_path,
    settings_path,
)
from llmux.cli.launchers.claude import launch


def _run_asset(name: str, payload: dict) -> subprocess.CompletedProcess[bytes]:
    ref = files("llmux.cli.hook_assets").joinpath(name)
    with as_file(ref) as script:
        return subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            check=False,
        )


def test_syntax_hook_rejects_broken_python(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def f(\n", encoding="utf-8")
    proc = _run_asset(
        SYNTAX_SCRIPT_NAME,
        {"tool_input": {"file_path": str(broken)}},
    )
    assert proc.returncode == 2
    assert b"SYNTAX ERROR after editing" in proc.stderr


def test_syntax_hook_accepts_valid_python(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    proc = _run_asset(
        SYNTAX_SCRIPT_NAME,
        {"tool_input": {"file_path": str(good)}},
    )
    assert proc.returncode == 0


def test_syntax_hook_skips_non_python(tmp_path: Path) -> None:
    other = tmp_path / "notes.md"
    other.write_text("not python\n", encoding="utf-8")
    proc = _run_asset(
        SYNTAX_SCRIPT_NAME,
        {"tool_input": {"file_path": str(other)}},
    )
    assert proc.returncode == 0


def test_breaker_blocks_second_edit_without_read(tmp_path: Path) -> None:
    target = str((tmp_path / "a.py").resolve())
    session = "test-session-dirty"

    post = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": target},
        },
    )
    assert post.returncode == 0

    blocked = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": target},
        },
    )
    assert blocked.returncode == 2
    assert b"BLOCKED" in blocked.stderr

    read = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": target},
        },
    )
    assert read.returncode == 0

    allowed = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": target},
        },
    )
    assert allowed.returncode == 0


def test_breaker_blocks_after_two_edit_failures(tmp_path: Path) -> None:
    target = str((tmp_path / "b.py").resolve())
    session = "test-session-fails"

    for _ in range(2):
        fail = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Edit",
                "tool_input": {"file_path": target},
                "error": "String to replace not found in file.",
            },
        )
        assert fail.returncode == 0

    blocked = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": target},
        },
    )
    assert blocked.returncode == 2
    assert b"failed Edit/Write" in blocked.stderr


def test_breaker_blocks_repeated_bash_failures() -> None:
    session = "test-session-bash"
    command = "pytest tests/missing.py"
    error = "file or directory not found: tests/missing.py"

    for _ in range(3):
        fail = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "error": error,
            },
        )
        assert fail.returncode == 0

    blocked = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    )
    assert blocked.returncode == 2
    assert b"same Bash command failed" in blocked.stderr


def test_breaker_blocks_bash_python_mutation() -> None:
    for command in (
        "sed -i 's/a/b/' src/foo.py",
        "ruff format src/",
        "ruff check --fix src/foo.py",
        "echo x > src/foo.py",
    ):
        proc = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": f"bash-mutate-{hash(command)}",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            },
        )
        assert proc.returncode == 2, command
        assert b"silently rewrite Python" in proc.stderr


def test_breaker_allows_sed_on_non_python() -> None:
    proc = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": "bash-sed-txt",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "sed -i 's/a/b/' notes.txt"},
        },
    )
    assert proc.returncode == 0


def test_breaker_blocks_read_without_progress(tmp_path: Path) -> None:
    target = str((tmp_path / "stuck.py").resolve())
    session = "test-session-read-streak"

    for i in range(3):
        proc = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": target},
            },
        )
        assert proc.returncode == 0, i

    blocked = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": target},
        },
    )
    assert blocked.returncode == 2
    assert b"no Edit/Write/Bash progress" in blocked.stderr

    # Successful Bash resets streaks.
    post = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "uv run pytest -q"},
        },
    )
    assert post.returncode == 0
    allowed = _run_asset(
        BREAKER_SCRIPT_NAME,
        {
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": target},
        },
    )
    assert allowed.returncode == 0


def test_breaker_blocks_edit_revert_cycle(tmp_path: Path) -> None:
    target = str((tmp_path / "rev.py").resolve())
    session = "test-session-revert"

    def post_edit(old: str, new: str) -> None:
        proc = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PostToolUse",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": target,
                    "old_string": old,
                    "new_string": new,
                },
            },
        )
        assert proc.returncode == 0

    def read() -> None:
        proc = _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": target},
            },
        )
        assert proc.returncode == 0

    def pre_edit(old: str, new: str) -> subprocess.CompletedProcess[bytes]:
        return _run_asset(
            BREAKER_SCRIPT_NAME,
            {
                "session_id": session,
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": target,
                    "old_string": old,
                    "new_string": new,
                },
            },
        )

    # A→B
    assert pre_edit("A", "B").returncode == 0
    post_edit("A", "B")
    read()
    # B→A (revert cycle 1)
    assert pre_edit("B", "A").returncode == 0
    post_edit("B", "A")
    read()
    # A→B (revert cycle 2 → deny)
    blocked = pre_edit("A", "B")
    assert blocked.returncode == 2
    assert b"Edit" in blocked.stderr and b"revert" in blocked.stderr

    # Read clears the lock; a non-revert edit is allowed again.
    read()
    assert pre_edit("A", "C").returncode == 0


def test_pytest_guard_blocks_bare_pytest() -> None:
    blocked = _run_asset(
        PYTEST_GUARD_SCRIPT_NAME,
        {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}},
    )
    assert blocked.returncode == 2
    assert b"uv run pytest" in blocked.stderr

    chained = _run_asset(
        PYTEST_GUARD_SCRIPT_NAME,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run true; pytest -q"},
        },
    )
    assert chained.returncode == 2

    allowed = _run_asset(
        PYTEST_GUARD_SCRIPT_NAME,
        {"tool_name": "Bash", "tool_input": {"command": "uv run pytest -q"}},
    )
    assert allowed.returncode == 0


def test_commit_guard_blocks_empty_doneish_claim(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    ref = files("llmux.cli.hook_assets").joinpath(COMMIT_GUARD_SCRIPT_NAME)
    with as_file(ref) as script:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": (
                            'git commit -m "feat: implemented cash flow feature"'
                        )
                    },
                }
            ).encode(),
            capture_output=True,
            check=False,
            cwd=repo,
        )
    assert proc.returncode == 2
    assert b"index is empty" in proc.stderr


def test_commit_guard_allows_unrelated_commit_message() -> None:
    proc = _run_asset(
        COMMIT_GUARD_SCRIPT_NAME,
        {
            "tool_name": "Bash",
            "tool_input": {"command": 'git commit -m "docs: fix typo in README"'},
        },
    )
    assert proc.returncode == 0


def test_commit_guard_blocks_unstaged_path_claim(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    other = repo / "README.md"
    other.write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)

    ref = files("llmux.cli.hook_assets").joinpath(COMMIT_GUARD_SCRIPT_NAME)
    with as_file(ref) as script:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": (
                            'git commit -m "fix: update `src/radar/allocation.py`"'
                        )
                    },
                }
            ).encode(),
            capture_output=True,
            check=False,
            cwd=repo,
        )
    assert proc.returncode == 2
    assert b"not staged" in proc.stderr


def test_commit_guard_reads_message_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    msg_file = repo / "msg.txt"
    msg_file.write_text("fix: update `src/missing.py`\n", encoding="utf-8")
    # empty index → path claim fails
    ref = files("llmux.cli.hook_assets").joinpath(COMMIT_GUARD_SCRIPT_NAME)
    with as_file(ref) as script:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": f"git commit -F {msg_file}"},
                }
            ).encode(),
            capture_output=True,
            check=False,
            cwd=repo,
        )
    assert proc.returncode == 2
    assert b"not staged" in proc.stderr


def test_merge_hooks_creates_managed_entries() -> None:
    merged = merge_hooks({})
    assert any(
        HOOK_MARKER in e["hooks"][0]["command"] for e in merged["hooks"]["PostToolUse"]
    )
    assert any(
        BREAKER_MARKER in e["hooks"][0]["command"]
        for e in merged["hooks"]["PreToolUse"]
    )
    assert any(
        COMMIT_GUARD_MARKER in e["hooks"][0]["command"]
        for e in merged["hooks"]["PreToolUse"]
    )
    assert any(
        PYTEST_GUARD_MARKER in e["hooks"][0]["command"]
        for e in merged["hooks"]["PreToolUse"]
    )
    assert any(
        COMPACT_REINJECT_MARKER in e["hooks"][0]["command"]
        for e in merged["hooks"]["SessionStart"]
    )
    assert any(
        BREAKER_MARKER in e["hooks"][0]["command"]
        for e in merged["hooks"]["PostToolUseFailure"]
    )


def test_merge_hooks_strips_ruff_autofix_post_tool() -> None:
    existing = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 -c 'import subprocess; "
                            'subprocess.run(["uv","run","ruff","format",path])\'',
                        }
                    ],
                }
            ]
        }
    }
    merged = merge_hooks(existing)
    post_commands = [
        hook["command"]
        for entry in merged["hooks"]["PostToolUse"]
        for hook in entry["hooks"]
    ]
    assert not any("ruff" in cmd and "format" in cmd for cmd in post_commands)
    assert any(HOOK_MARKER in cmd for cmd in post_commands)


def test_merge_syntax_hook_preserves_unrelated_hooks() -> None:
    existing = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": []}],
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "echo other"}],
                }
            ],
        }
    }
    merged = merge_syntax_hook(existing)
    post_cmds = [e["hooks"][0]["command"] for e in merged["hooks"]["PostToolUse"]]
    assert "echo other" in post_cmds
    assert any(HOOK_MARKER in c for c in post_cmds)
    assert existing["hooks"]["PreToolUse"][0] in merged["hooks"]["PreToolUse"]


def test_merge_hooks_idempotent() -> None:
    first = merge_hooks({})
    second = merge_hooks(first)
    assert len(second["hooks"]["PostToolUse"]) == len(first["hooks"]["PostToolUse"])
    assert len(second["hooks"]["PreToolUse"]) == len(first["hooks"]["PreToolUse"])


def test_install_hooks_writes_scripts_settings_and_rule(tmp_path: Path) -> None:
    paths = install_hooks(tmp_path)
    settings = settings_path(tmp_path)
    rule = rules_path(tmp_path)
    syntax = tmp_path / ".claude" / "hooks" / SYNTAX_SCRIPT_NAME
    breaker = tmp_path / ".claude" / "hooks" / BREAKER_SCRIPT_NAME
    commit_guard = tmp_path / ".claude" / "hooks" / COMMIT_GUARD_SCRIPT_NAME
    pytest_guard = tmp_path / ".claude" / "hooks" / PYTEST_GUARD_SCRIPT_NAME
    compact = tmp_path / ".claude" / "hooks" / COMPACT_REINJECT_SCRIPT_NAME
    assert syntax.is_file()
    assert breaker.is_file()
    assert commit_guard.is_file()
    assert pytest_guard.is_file()
    assert compact.is_file()
    assert settings in paths
    assert rule in paths
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert any(
        HOOK_MARKER in e["hooks"][0]["command"] for e in data["hooks"]["PostToolUse"]
    )
    assert "Hard circuit-breaker" in rule.read_text(encoding="utf-8")
    assert rule.name == EDIT_SAFETY_RULE_NAME


def test_install_hooks_backs_up_existing_settings(tmp_path: Path) -> None:
    settings = settings_path(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '{"permissions": {"allow": ["Bash(ls *)"]}}\n', encoding="utf-8"
    )
    install_hooks(tmp_path)
    backup = settings.with_suffix(".json.bak")
    assert backup.is_file()
    assert '"Bash(ls *)"' in backup.read_text(encoding="utf-8")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["Bash(ls *)"]


def test_install_hooks_rejects_invalid_json(tmp_path: Path) -> None:
    settings = settings_path(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not-json", encoding="utf-8")
    with pytest.raises(SystemExit, match="invalid JSON"):
        install_hooks(tmp_path)


def test_launch_install_hooks_skips_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "llmux.cli.launchers.claude.get_settings",
            lambda: (_ for _ in ()).throw(AssertionError("should not load settings")),
        )
        launch(["install-hooks", "--path", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Wrote" in out
    assert settings_path(tmp_path).is_file()
    assert rules_path(tmp_path).is_file()
    assert (tmp_path / ".claude" / "hooks" / BREAKER_SCRIPT_NAME).is_file()


def test_install_hooks_cli_help_does_not_write(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        install_hooks_cli(["--help"])
    assert exc_info.value.code == 0
    assert not settings_path(tmp_path).exists()
