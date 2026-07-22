"""Installed `fcc-opencode` launcher."""

import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from free_claude_code.cli.proxy_auth import proxy_auth_token
from free_claude_code.config.model_refs import parse_model_fallbacks
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
        fallback_models=parse_model_fallbacks(settings.model_fallbacks),
        verdict_command=resolve_verdict_command(),
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


def resolve_verdict_command() -> list[str]:
    """Return argv that starts the Verdict MCP server over stdio."""

    verdict = shutil.which("fcc-verdict")
    if verdict is not None:
        return [verdict, "serve-mcp"]
    return [sys.executable, "-m", "free_claude_code.verdict.cli", "serve-mcp"]


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
    verdict_command: Sequence[str],
    fallback_models: Sequence[str] = (),
    reviewer_model: str | None = None,
) -> dict[str, object]:
    """Return OpenCode config that routes through FCC and registers Verdict MCP."""

    model_id = model.strip() or "claude-sonnet-4-5"
    models: dict[str, object] = {
        model_id: {"name": f"FCC ({model_id})"},
    }
    for fallback in fallback_models:
        text = fallback.strip()
        if text and text not in models:
            models[text] = {"name": f"FCC ({text})"}

    review_model = (reviewer_model or "").strip()
    if not review_model:
        for fallback in fallback_models:
            if fallback.strip() and fallback.strip() != model_id:
                review_model = fallback.strip()
                break
    if review_model and review_model not in models:
        models[review_model] = {"name": f"FCC ({review_model})"}

    agents: dict[str, object] = {
        "build": {
            "mode": "primary",
            "model": f"fcc/{model_id}",
        },
    }
    config: dict[str, object] = {
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
                "models": models,
            }
        },
        "mcp": {
            "free-llm-verdict": {
                "type": "local",
                "command": list(verdict_command),
                "enabled": True,
            }
        },
        "agent": agents,
        "command": {
            "verdict": {
                "description": "Run free multi-model verdict deliberation via MCP",
                "template": (
                    "Run a free-only verdict deliberation on: $ARGUMENTS\n"
                    "Use the free-llm-verdict MCP tool `evaluate` with depth quick "
                    "(research on when facts matter). Return the verdict and key dissent."
                ),
            }
        },
    }
    if review_model:
        agents["second-opinion"] = {
            "description": (
                "Second-opinion reviewer using a different FCC model family; "
                "read-only critique of plans and patches"
            ),
            "mode": "subagent",
            "model": f"fcc/{review_model}",
            "prompt": (
                "You are a skeptical code reviewer. Critique plans and diffs. "
                "Do not edit files; prefer read/grep tools. Be concrete."
            ),
            "permission": {
                "edit": "deny",
                "bash": "ask",
            },
        }
    return config


def write_opencode_config(
    *,
    proxy_root_url: str,
    auth_token: str,
    model: str,
    verdict_command: Sequence[str],
    fallback_models: Sequence[str] = (),
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
        verdict_command=verdict_command,
        fallback_models=fallback_models,
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
