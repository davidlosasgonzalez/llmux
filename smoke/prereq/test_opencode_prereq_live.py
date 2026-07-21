"""OpenCode → local FCC proxy smoke (B5). Skips when OpenCode is not installed."""

import json
import os
import shutil
from pathlib import Path

import pytest

from free_claude_code.cli.launchers.opencode import (
    build_opencode_launcher_env,
    write_opencode_config,
)
from smoke.lib.child_process import run_captured_text
from smoke.lib.config import SmokeConfig
from smoke.lib.server import start_server
from smoke.lib.skips import skip_upstream_unavailable

pytestmark = [pytest.mark.live, pytest.mark.smoke_target("cli")]


def test_opencode_run_hits_proxy_when_available(
    smoke_config: SmokeConfig, tmp_path: Path
) -> None:
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        pytest.skip("OpenCode CLI not found on PATH")

    models = smoke_config.provider_models()
    if not models:
        pytest.skip("no configured provider model available for OpenCode smoke")

    model = models[0].full_model
    with start_server(
        smoke_config,
        env_overrides={"MODEL": model, "MESSAGING_PLATFORM": "none"},
        name="opencode-cli",
    ) as server:
        config_path = write_opencode_config(
            proxy_root_url=server.base_url,
            auth_token=smoke_config.settings.anthropic_auth_token,
            model=model,
            council_command=["fcc-council", "serve-mcp"],
            config_dir=tmp_path,
        )
        # Avoid pulling Council MCP into the smoke path.
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload.pop("mcp", None)
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        env = build_opencode_launcher_env(
            config_path=config_path,
            base_env=os.environ,
        )
        result = run_captured_text(
            [opencode_bin, "run", "Reply with exactly FCC_SMOKE_PONG"],
            cwd=tmp_path,
            env=env,
            timeout=smoke_config.timeout_s,
            check=False,
        )
        server_log = server.log_path.read_text(encoding="utf-8", errors="replace")

    assert "POST /v1/messages" in server_log, (
        "OpenCode did not call the local Anthropic-compatible endpoint:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    if result.returncode != 0:
        skip_upstream_unavailable(
            f"opencode exit {result.returncode}: {result.stderr or result.stdout}"
        )
    if "FCC_SMOKE_PONG" not in result.stdout:
        skip_upstream_unavailable(
            f"OpenCode completed without expected text: {result.stdout[:500]!r}"
        )
