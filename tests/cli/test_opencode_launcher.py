"""Tests for fcc-opencode launcher config generation."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from free_claude_code.cli.launchers.opencode import (
    anthropic_compatible_base_url,
    build_opencode_config_dict,
    build_opencode_launcher_env,
    resolve_opencode_model,
    write_opencode_config,
)


def test_anthropic_compatible_base_url_appends_v1():
    assert anthropic_compatible_base_url("http://127.0.0.1:8082") == (
        "http://127.0.0.1:8082/v1"
    )
    assert anthropic_compatible_base_url("http://127.0.0.1:8082/") == (
        "http://127.0.0.1:8082/v1"
    )
    assert anthropic_compatible_base_url("http://127.0.0.1:8082/v1") == (
        "http://127.0.0.1:8082/v1"
    )


def test_build_opencode_config_includes_fcc_provider_and_mcp():
    cfg = build_opencode_config_dict(
        proxy_root_url="http://127.0.0.1:8082",
        auth_token="",
        model="cerebras/gpt-oss-120b",
        council_command=["fcc-council", "serve-mcp"],
    )
    assert cfg["model"] == "fcc/cerebras/gpt-oss-120b"
    provider = cfg["provider"]["fcc"]
    assert provider["npm"] == "@ai-sdk/anthropic"
    assert provider["options"]["baseURL"] == "http://127.0.0.1:8082/v1"
    assert provider["options"]["apiKey"] == "fcc-no-auth"
    assert "cerebras/gpt-oss-120b" in provider["models"]
    mcp = cfg["mcp"]["free-llm-verdict"]
    assert mcp["type"] == "local"
    assert mcp["command"] == ["fcc-council", "serve-mcp"]
    assert mcp["enabled"] is True


def test_write_opencode_config_roundtrip(tmp_path: Path):
    path = write_opencode_config(
        proxy_root_url="http://127.0.0.1:9",
        auth_token="secret",
        model="claude-sonnet-4-5",
        council_command=["fcc-council", "serve-mcp"],
        config_dir=tmp_path,
    )
    assert path == tmp_path / "opencode.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["provider"]["fcc"]["options"]["apiKey"] == "secret"


def test_launcher_env_sets_opencode_config(tmp_path: Path):
    config = tmp_path / "opencode.json"
    env = build_opencode_launcher_env(config_path=config, base_env={"FOO": "1"})
    assert env["FOO"] == "1"
    assert env["OPENCODE_CONFIG"] == str(config)


def test_resolve_opencode_model_defaults():
    settings = MagicMock()
    settings.model = ""
    assert resolve_opencode_model(settings) == "claude-sonnet-4-5"
    settings.model = "  groq/x  "
    assert resolve_opencode_model(settings) == "groq/x"
