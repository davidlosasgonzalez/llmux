"""Shared filesystem paths for Free Claude Code configuration."""

from pathlib import Path

FCC_CONFIG_DIRNAME = ".fcc"
FCC_ENV_FILENAME = ".env"
CLAUDE_WORKSPACE_DIRNAME = "agent_workspace"


def config_dir_path() -> Path:
    """Return the default user config directory."""

    return Path.home() / FCC_CONFIG_DIRNAME


def managed_env_path() -> Path:
    """Return the default user-managed env file path."""

    return config_dir_path() / FCC_ENV_FILENAME


def default_claude_workspace_path() -> Path:
    """Return the default Claude workspace path."""

    return config_dir_path() / CLAUDE_WORKSPACE_DIRNAME
