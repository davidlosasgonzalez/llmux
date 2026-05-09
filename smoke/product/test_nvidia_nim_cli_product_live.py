from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from smoke.lib.config import ProviderModel, SmokeConfig
from smoke.lib.e2e import SmokeServerDriver
from smoke.lib.nvidia_nim_cli import (
    ClaudeCliRun,
    NimCliMatrixOutcome,
    make_outcome,
    read_log_delta,
    read_log_offset,
    regression_failures,
    run_claude_cli,
    write_matrix_report,
)
from smoke.lib.server import RunningServer

pytestmark = [pytest.mark.live, pytest.mark.smoke_target("nvidia_nim_cli")]


def test_nvidia_nim_cli_matrix_e2e(smoke_config: SmokeConfig, tmp_path: Path) -> None:
    if not smoke_config.has_provider_configuration("nvidia_nim"):
        pytest.skip("missing_env: NVIDIA_NIM_API_KEY is not configured")

    claude_bin = shutil.which(smoke_config.claude_bin)
    if not claude_bin:
        pytest.skip(f"missing_env: Claude CLI not found: {smoke_config.claude_bin}")

    provider_models = smoke_config.nvidia_nim_cli_models()
    if not provider_models:
        pytest.skip("missing_env: no NVIDIA NIM CLI smoke models configured")

    outcomes: list[NimCliMatrixOutcome] = []
    for provider_model in provider_models:
        with SmokeServerDriver(
            smoke_config,
            name=f"product-nvidia-nim-cli-{_slug(provider_model.model_name)}",
            env_overrides={
                "MODEL": provider_model.full_model,
                "MESSAGING_PLATFORM": "none",
                "ENABLE_MODEL_THINKING": "true",
                "LOG_RAW_API_PAYLOADS": "true",
                "LOG_RAW_SSE_EVENTS": "true",
            },
        ).run() as server:
            model_dir = tmp_path / _slug(provider_model.model_name)
            outcomes.extend(
                [
                    _basic_text(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                    _thinking(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                    _tool_use_roundtrip(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                    _interleaved_thinking_tool(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                    _subagent_task(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                    _compact_command(
                        claude_bin, server, smoke_config, provider_model, model_dir
                    ),
                ]
            )

    report_path = write_matrix_report(smoke_config, outcomes)
    failures = regression_failures(outcomes)
    assert not failures, (
        f"NVIDIA NIM CLI matrix regressions written to {report_path}:\n"
        + "\n".join(failures)
    )


def _basic_text(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("BASIC")
    return _run_probe(
        claude_bin=claude_bin,
        server=server,
        smoke_config=smoke_config,
        provider_model=provider_model,
        workspace=model_dir / "basic_text",
        feature="basic_text",
        marker=marker,
        prompt=f"Reply with exactly {marker} and no other text.",
        tools="",
    )


def _thinking(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("THINK")
    return _run_probe(
        claude_bin=claude_bin,
        server=server,
        smoke_config=smoke_config,
        provider_model=provider_model,
        workspace=model_dir / "thinking",
        feature="thinking",
        marker=marker,
        prompt=(
            "Think privately about the request, then reply with exactly "
            f"{marker} and no other text."
        ),
        tools="",
        extra_args=("--effort", "high"),
    )


def _tool_use_roundtrip(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("TOOL")
    workspace = model_dir / "tool_use_roundtrip"
    (workspace / "smoke-read.txt").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "smoke-read.txt").write_text(marker, encoding="utf-8")
    return _run_probe(
        claude_bin=claude_bin,
        server=server,
        smoke_config=smoke_config,
        provider_model=provider_model,
        workspace=workspace,
        feature="tool_use_roundtrip",
        marker=marker,
        prompt=(
            "Use the Read tool to read smoke-read.txt. Reply with exactly the "
            "secret token from that file and no other text."
        ),
        tools="Read",
        requires_tool_result=True,
    )


def _interleaved_thinking_tool(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("INTERLEAVED")
    workspace = model_dir / "interleaved_thinking_tool"
    (workspace / "smoke-interleaved.txt").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "smoke-interleaved.txt").write_text(marker, encoding="utf-8")
    return _run_probe(
        claude_bin=claude_bin,
        server=server,
        smoke_config=smoke_config,
        provider_model=provider_model,
        workspace=workspace,
        feature="interleaved_thinking_tool",
        marker=marker,
        prompt=(
            "Think privately, use Read on smoke-interleaved.txt, then reply with "
            "exactly the secret token from that file and no other text."
        ),
        tools="Read",
        extra_args=("--effort", "high"),
        requires_tool_result=True,
    )


def _subagent_task(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("TASK")
    workspace = model_dir / "subagent_task"
    (workspace / "smoke-subagent.txt").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "smoke-subagent.txt").write_text(marker, encoding="utf-8")
    agents = json.dumps(
        {
            "smoke_reader": {
                "description": "Reads one requested file and returns its token.",
                "prompt": (
                    "Read the requested file with Read and return only the token "
                    "inside it."
                ),
            }
        }
    )
    return _run_probe(
        claude_bin=claude_bin,
        server=server,
        smoke_config=smoke_config,
        provider_model=provider_model,
        workspace=workspace,
        feature="subagent_task",
        marker=marker,
        prompt=(
            "Use the smoke_reader subagent with Task to read smoke-subagent.txt. "
            "Reply with exactly the token the subagent returns and no other text."
        ),
        tools="Task,Read",
        extra_args=("--agents", agents),
        requires_tool_result=True,
    )


def _compact_command(
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    model_dir: Path,
) -> NimCliMatrixOutcome:
    marker = _marker("COMPACT")
    workspace = model_dir / "compact_command"
    session_id = str(uuid.uuid4())
    offset = read_log_offset(server.log_path)
    first = run_claude_cli(
        claude_bin=claude_bin,
        server=server,
        config=smoke_config,
        cwd=workspace,
        prompt=f"Remember this smoke token: {marker}. Reply with exactly {marker}.",
        tools="",
        session_id=session_id,
        no_session_persistence=False,
    )
    second = run_claude_cli(
        claude_bin=claude_bin,
        server=server,
        config=smoke_config,
        cwd=workspace,
        prompt=f"/compact preserve {marker}",
        tools="",
        resume_session_id=session_id,
        no_session_persistence=False,
    )
    log_delta = read_log_delta(server.log_path, offset)
    run = ClaudeCliRun(
        command=(*first.command, "&&", *second.command),
        returncode=second.returncode if first.returncode == 0 else first.returncode,
        stdout=f"{first.stdout}\n{second.stdout}",
        stderr=f"{first.stderr}\n{second.stderr}",
        duration_s=first.duration_s + second.duration_s,
        timed_out=first.timed_out or second.timed_out,
    )
    return make_outcome(
        model=provider_model.model_name,
        full_model=provider_model.full_model,
        source=provider_model.source,
        feature="compact_command",
        marker="",
        run=run,
        log_delta=log_delta,
        log_path=server.log_path,
        requires_compact=True,
    )


def _run_probe(
    *,
    claude_bin: str,
    server: RunningServer,
    smoke_config: SmokeConfig,
    provider_model: ProviderModel,
    workspace: Path,
    feature: str,
    marker: str,
    prompt: str,
    tools: str | None,
    extra_args: tuple[str, ...] = (),
    requires_tool_result: bool = False,
    requires_task: bool = False,
) -> NimCliMatrixOutcome:
    offset = read_log_offset(server.log_path)
    run = run_claude_cli(
        claude_bin=claude_bin,
        server=server,
        config=smoke_config,
        cwd=workspace,
        prompt=prompt,
        tools=tools,
        extra_args=extra_args,
    )
    log_delta = read_log_delta(server.log_path, offset)
    return make_outcome(
        model=provider_model.model_name,
        full_model=provider_model.full_model,
        source=provider_model.source,
        feature=feature,
        marker=marker,
        run=run,
        log_delta=log_delta,
        log_path=server.log_path,
        requires_tool_result=requires_tool_result,
        requires_task=requires_task,
    )


def _marker(prefix: str) -> str:
    return f"FCC_NIM_{prefix}_{uuid.uuid4().hex[:8].upper()}"


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-")
