import os
from pathlib import Path

import pytest

from free_claude_code.core.version import package_version
from smoke.lib.child_process import cmd_fcc_init, cmd_fcc_version, run_captured_text
from smoke.lib.config import SmokeConfig

pytestmark = [pytest.mark.live, pytest.mark.smoke_target("cli")]


def test_entrypoint_init_e2e(smoke_config: SmokeConfig, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    result = run_captured_text(
        cmd_fcc_init(),
        cwd=smoke_config.root,
        env=env,
        timeout=smoke_config.timeout_s,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    env_file = tmp_path / ".fcc" / ".env"
    assert env_file.is_file()
    assert env_file.read_text(encoding="utf-8").strip()


def test_entrypoint_version_e2e(smoke_config: SmokeConfig, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    result = run_captured_text(
        cmd_fcc_version(),
        cwd=smoke_config.root,
        env=env,
        timeout=smoke_config.timeout_s,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == f"free-claude-code {package_version()}\n"
    assert result.stderr == ""
    assert not (tmp_path / ".fcc" / ".env").exists()
