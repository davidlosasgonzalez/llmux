"""Shared filesystem paths for Free Claude Code configuration."""

from pathlib import Path

FCC_CONFIG_DIRNAME = ".fcc"
FCC_ENV_FILENAME = ".env"
LEGACY_REPO_DIRNAME = "free-claude-code"
LEGACY_XDG_CONFIG_DIRNAME = ".config"
FCC_LOGS_DIRNAME = "logs"
SERVER_LOG_FILENAME = "server.log"
VERDICT_MCP_LOG_FILENAME = "verdict-mcp.log"


def config_dir_path() -> Path:
    """Return the default user config directory."""

    return Path.home() / FCC_CONFIG_DIRNAME


def managed_env_path() -> Path:
    """Return the default user-managed env file path."""

    return config_dir_path() / FCC_ENV_FILENAME


def legacy_env_paths() -> tuple[Path, ...]:
    """Return legacy user env paths that can be migrated to ~/.fcc/.env."""

    home = Path.home()
    return (
        home / LEGACY_REPO_DIRNAME / FCC_ENV_FILENAME,
        home / LEGACY_XDG_CONFIG_DIRNAME / LEGACY_REPO_DIRNAME / FCC_ENV_FILENAME,
    )


def server_log_path() -> Path:
    """Return the canonical server log path."""

    return config_dir_path() / FCC_LOGS_DIRNAME / SERVER_LOG_FILENAME


def verdict_mcp_log_path() -> Path:
    """Return the canonical verdict MCP server log path."""

    return config_dir_path() / FCC_LOGS_DIRNAME / VERDICT_MCP_LOG_FILENAME
