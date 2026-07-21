"""CLI smoke for fcc-agent (no proxy required for --version / preflight)."""

from free_claude_code.agent.cli import _build_parser, _resolve_workspace
from free_claude_code.cli.launchers.common import preflight_proxy


def test_parser_accepts_prompt_and_yes():
    args = _build_parser().parse_args(["--yes", "do something"])
    assert args.prompt == "do something"
    assert args.yes is True


def test_resolve_workspace_prefers_cli(tmp_path):
    root = _resolve_workspace(str(tmp_path), "/ignored")
    assert root == tmp_path


def test_resolve_workspace_falls_back_to_allowed_dir(tmp_path):
    root = _resolve_workspace(None, str(tmp_path))
    assert root == tmp_path


def test_preflight_proxy_unreachable_returns_message():
    # High port unlikely to host fcc-server.
    err = preflight_proxy("http://127.0.0.1:1")
    assert err is not None


def test_fcc_agent_version_entrypoint(capsys):
    from free_claude_code.agent.cli import main

    main(["--version"])
    captured = capsys.readouterr()
    assert "fcc-agent" in captured.out
