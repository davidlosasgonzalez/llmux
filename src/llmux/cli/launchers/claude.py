"""Installed `llmux-claude` launcher."""

import os
import sys
from collections.abc import Sequence

from llmux.cli.claude_env import (
    CLAUDE_BINARY_NAME,
    build_claude_proxy_env,
)
from llmux.cli.claude_hooks import install_hooks_cli
from llmux.config.server_urls import local_proxy_root_url
from llmux.config.settings import get_settings

from .common import preflight_proxy, resolve_client_binary, run_client_process

_DISPLAY_NAME = "Claude Code"
_INSTALL_HINT = "Install Claude Code with: npm install -g @anthropic-ai/claude-code"
_INSTALL_HOOKS_CMD = "install-hooks"


def launch(argv: Sequence[str] | None = None) -> None:
    """Launch Claude Code with LLMux proxy environment variables.

    ``llmux-claude install-hooks`` is a LLMux-owned subcommand (does not need
    the proxy) that installs Claude Code edit-safety hooks into a project.
    """

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == _INSTALL_HOOKS_CMD:
        install_hooks_cli(args[1:])
        return

    settings = get_settings()
    proxy_root_url = local_proxy_root_url(settings)
    if error := preflight_proxy(proxy_root_url):
        print(
            f"LLMux proxy is not reachable at {proxy_root_url}: {error}",
            file=sys.stderr,
        )
        print("Start it in another terminal with: llmux-server", file=sys.stderr)
        raise SystemExit(1)

    binary_name = claude_binary_name()
    binary_path = resolve_client_binary(
        binary_name=binary_name,
        display_name=_DISPLAY_NAME,
        install_hint=_INSTALL_HINT,
    )
    run_client_process(
        command=build_claude_launcher_command(binary_path=binary_path, argv=args),
        env=build_claude_proxy_env(
            proxy_root_url=proxy_root_url,
            auth_token=settings.anthropic_auth_token,
            base_env=os.environ,
        ),
        binary_name=binary_name,
        display_name=_DISPLAY_NAME,
        install_hint=_INSTALL_HINT,
    )


def claude_binary_name() -> str:
    """Return the Claude Code binary name."""

    return CLAUDE_BINARY_NAME


def build_claude_launcher_command(
    *, binary_path: str, argv: Sequence[str]
) -> list[str]:
    """Return the Claude wrapper command without changing user arguments."""

    return [binary_path, *argv]
