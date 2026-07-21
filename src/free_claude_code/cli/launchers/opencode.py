"""Installed `fcc-opencode` launcher."""

import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from free_claude_code.cli.proxy_auth import proxy_auth_token
from free_claude_code.config.paths import OPENCODE_CONFIG_FILENAME, config_dir_path
from free_claude_code.config.server_urls import local_proxy_root_url
from free_claude_code.config.settings import Settings, get_settings

from .common import preflight_proxy, resolve_client_binary, run_client_process

_BINARY_NAME = "opencode"
_DISPLAY_NAME = "OpenCode"
_INSTALL_HINT = (
    "Install OpenCode with: curl -fsSL https://opencode.ai/install | bash\n"
    "Or: brew install opencode"
)
_OPENCODE_CONFIG_ENV = "OPENCODE_CONFIG"


def launch(argv: Sequence[str] | None = None) -> None:
    """Launch OpenCode pointed at the local FCC Anthropic proxy."""

    settings = get_settings()
    proxy_root_url = local_proxy_root_url(settings)
    if error := preflight_proxy(proxy_root_url):
        print(
            f"Free Claude Code proxy is not reachable at {proxy_root_url}: {error}",
            file=sys.stderr,
        )
        print("Start it in another terminal with: fcc-server", file=sys.stderr)
        raise SystemExit(1)

    binary_path = resolve_client_binary(
        binary_name=_BINARY_NAME,
        display_name=_DISPLAY_NAME,
        install_hint=_INSTALL_HINT,
    )
    config_path = write_opencode_config(
        proxy_root_url=proxy_root_url,
        auth_token=settings.anthropic_auth_token,
        model=resolve_opencode_model(settings),
        council_command=resolve_council_command(),
    )
    args = list(sys.argv[1:] if argv is None else argv)
    run_client_process(
        command=[binary_path, *args],
        env=build_opencode_launcher_env(
            config_path=config_path,
            base_env=os.environ,
        ),
        binary_name=_BINARY_NAME,
        display_name=_DISPLAY_NAME,
        install_hint=_INSTALL_HINT,
    )


def resolve_opencode_model(settings: Settings) -> str:
    """Return the default FCC model id exposed to OpenCode."""

    return (settings.model or "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"


def resolve_council_command() -> list[str]:
    """Return argv that starts the Council MCP server over stdio."""

    council = shutil.which("fcc-council")
    if council is not None:
        return [council, "serve-mcp"]
    return [sys.executable, "-m", "free_claude_code.council.cli", "serve-mcp"]


def anthropic_compatible_base_url(proxy_root_url: str) -> str:
    """OpenCode Anthropic SDK posts to ``{baseURL}/messages``; FCC serves ``/v1/messages``."""

    root = proxy_root_url.rstrip("/")
    if root.endswith("/v1"):
        return root
    return f"{root}/v1"


def build_opencode_config_dict(
    *,
    proxy_root_url: str,
    auth_token: str,
    model: str,
    council_command: Sequence[str],
) -> dict[str, object]:
    """Return OpenCode config that routes through FCC and registers Council MCP."""

    model_id = model.strip() or "claude-sonnet-4-5"
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"fcc/{model_id}",
        "provider": {
            "fcc": {
                "npm": "@ai-sdk/anthropic",
                "name": "Free Claude Code",
                "options": {
                    "baseURL": anthropic_compatible_base_url(proxy_root_url),
                    "apiKey": proxy_auth_token(auth_token),
                },
                "models": {
                    model_id: {
                        "name": f"FCC ({model_id})",
                    }
                },
            }
        },
        "mcp": {
            "free-llm-verdict": {
                "type": "local",
                "command": list(council_command),
                "enabled": True,
            }
        },
    }


def write_opencode_config(
    *,
    proxy_root_url: str,
    auth_token: str,
    model: str,
    council_command: Sequence[str],
    config_dir: Path | None = None,
) -> Path:
    """Write generated OpenCode config under ``~/.fcc/`` and return its path."""

    target_dir = config_dir if config_dir is not None else config_dir_path()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / OPENCODE_CONFIG_FILENAME
    payload = build_opencode_config_dict(
        proxy_root_url=proxy_root_url,
        auth_token=auth_token,
        model=model,
        council_command=council_command,
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_opencode_launcher_env(
    *,
    config_path: Path,
    base_env: Mapping[str, str],
) -> dict[str, str]:
    """Return env that forces OpenCode to use the generated FCC config."""

    env = dict(base_env)
    env[_OPENCODE_CONFIG_ENV] = str(config_path)
    return env


def opencode_binary_name() -> str:
    """Return the OpenCode binary name."""

    return _BINARY_NAME
