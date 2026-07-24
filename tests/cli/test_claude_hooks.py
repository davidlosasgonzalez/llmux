"""Tests for ``llmux-claude install-hooks``."""

import json
import subprocess
from pathlib import Path

import pytest

from llmux.cli.claude_hooks import (
    EDIT_SAFETY_RULE_NAME,
    HOOK_MARKER,
    SYNTAX_HOOK_COMMAND,
    install_hooks,
    install_hooks_cli,
    merge_syntax_hook,
    rules_path,
    settings_path,
)
from llmux.cli.launchers.claude import launch


def test_syntax_hook_command_rejects_broken_python(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def f(\n", encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(broken)}}).encode()
    proc = subprocess.run(
        ["bash", "-lc", SYNTAX_HOOK_COMMAND],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert b"SYNTAX ERROR after editing" in proc.stderr


def test_syntax_hook_command_accepts_valid_python(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(good)}}).encode()
    proc = subprocess.run(
        ["bash", "-lc", SYNTAX_HOOK_COMMAND],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0


def test_syntax_hook_command_skips_non_python(tmp_path: Path) -> None:
    other = tmp_path / "notes.md"
    other.write_text("not python\n", encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(other)}}).encode()
    proc = subprocess.run(
        ["bash", "-lc", SYNTAX_HOOK_COMMAND],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0


def test_merge_syntax_hook_creates_post_tool_use() -> None:
    merged = merge_syntax_hook({})
    post = merged["hooks"]["PostToolUse"]
    assert len(post) == 1
    assert HOOK_MARKER in post[0]["hooks"][0]["command"]


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
    post = merged["hooks"]["PostToolUse"]
    assert len(post) == 2
    assert post[0]["hooks"][0]["command"] == "echo other"
    assert HOOK_MARKER in post[1]["hooks"][0]["command"]
    assert merged["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]


def test_merge_syntax_hook_replaces_managed_entry_idempotently() -> None:
    first = merge_syntax_hook({})
    second = merge_syntax_hook(first)
    assert len(second["hooks"]["PostToolUse"]) == 1
    assert HOOK_MARKER in second["hooks"]["PostToolUse"][0]["hooks"][0]["command"]


def test_install_hooks_writes_settings_and_rule(tmp_path: Path) -> None:
    paths = install_hooks(tmp_path)
    settings = settings_path(tmp_path)
    rule = rules_path(tmp_path)
    assert paths == [settings, rule]
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert HOOK_MARKER in data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "Circuit-breaker" in rule.read_text(encoding="utf-8")
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
    assert HOOK_MARKER in data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]


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
        # Ensure we don't touch proxy / binary resolution.
        mp.setattr(
            "llmux.cli.launchers.claude.get_settings",
            lambda: (_ for _ in ()).throw(AssertionError("should not load settings")),
        )
        launch(["install-hooks", "--path", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Wrote" in out
    assert settings_path(tmp_path).is_file()
    assert rules_path(tmp_path).is_file()


def test_install_hooks_cli_help_does_not_write(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        install_hooks_cli(["--help"])
    assert exc_info.value.code == 0
    assert not settings_path(tmp_path).exists()
