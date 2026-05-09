from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings
from smoke.lib.config import DEFAULT_TARGETS, SmokeConfig
from smoke.lib.nvidia_nim_cli import (
    ClaudeCliRun,
    make_outcome,
    regression_failures,
    write_matrix_report,
)


def _smoke_config(tmp_path: Path) -> SmokeConfig:
    return SmokeConfig(
        root=tmp_path,
        results_dir=tmp_path / ".smoke-results",
        live=False,
        interactive=False,
        targets=DEFAULT_TARGETS,
        provider_matrix=frozenset(),
        timeout_s=45.0,
        prompt="Reply with exactly: FCC_SMOKE_PONG",
        claude_bin="claude",
        worker_id="test-worker",
        settings=Settings.model_construct(anthropic_auth_token=""),
    )


def test_nvidia_nim_cli_matrix_report_shape_and_redaction(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "secret-nim-key")
    run = ClaudeCliRun(
        command=("claude", "-p", "redacted"),
        returncode=0,
        stdout="FCC_NIM_BASIC secret-nim-key",
        stderr="",
        duration_s=1.25,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="basic_text",
        marker="FCC_NIM_BASIC",
        run=run,
        log_delta='POST /v1/messages HTTP/1.1" 200 OK secret-nim-key',
        log_path=tmp_path / "server.log",
    )

    path = write_matrix_report(_smoke_config(tmp_path), [outcome])
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name.startswith("nvidia-nim-cli-matrix-test-worker-")
    assert payload["target"] == "nvidia_nim_cli"
    assert payload["models"] == ["nvidia_nim/z-ai/glm-5.1"]
    saved = payload["outcomes"][0]
    assert saved["feature"] == "basic_text"
    assert saved["classification"] == "passed"
    assert saved["request_count"] == 1
    assert saved["token_evidence"]["marker_present"] is True
    assert "secret-nim-key" not in path.read_text(encoding="utf-8")


def test_nvidia_nim_cli_matrix_regression_detection(tmp_path: Path) -> None:
    run = ClaudeCliRun(
        command=("claude", "-p", "x"),
        returncode=0,
        stdout="",
        stderr="",
        duration_s=0.1,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="basic_text",
        marker="FCC_NIM_BASIC",
        run=run,
        log_delta='POST /v1/messages HTTP/1.1" 500 Internal Server Error',
        log_path=tmp_path / "server.log",
    )

    assert outcome.classification == "product_failure"
    assert regression_failures([outcome]) == [
        "nvidia_nim/z-ai/glm-5.1 basic_text: product_failure"
    ]


def test_nvidia_nim_cli_matrix_model_feature_failures_do_not_regress(
    tmp_path: Path,
) -> None:
    run = ClaudeCliRun(
        command=("claude", "-p", "x"),
        returncode=0,
        stdout="ordinary answer",
        stderr="",
        duration_s=0.1,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="tool_use_roundtrip",
        marker="FCC_NIM_TOOL",
        run=run,
        log_delta='POST /v1/messages HTTP/1.1" 200 OK',
        log_path=tmp_path / "server.log",
        requires_tool_result=True,
    )

    assert outcome.classification == "model_feature_failure"
    assert regression_failures([outcome]) == []


def test_nvidia_nim_cli_raw_payload_log_counts_as_proxy_request(
    tmp_path: Path,
) -> None:
    run = ClaudeCliRun(
        command=("claude", "-p", "x"),
        returncode=0,
        stdout="ordinary answer",
        stderr="",
        duration_s=0.1,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="subagent_task",
        marker="FCC_NIM_TASK",
        run=run,
        log_delta="API_REQUEST: request_id=req_1 model=z-ai/glm-5.1 messages=2",
        log_path=tmp_path / "server.log",
        requires_task=True,
    )

    assert outcome.classification == "model_feature_failure"
    assert outcome.request_count == 1
    assert regression_failures([outcome]) == []


def test_nvidia_nim_cli_timeout_is_not_model_missing(
    tmp_path: Path,
) -> None:
    run = ClaudeCliRun(
        command=("claude", "-p", "x"),
        returncode=None,
        stdout='{"type":"assistant","content":[{"type":"text","text":"FCC_NIM_TOOL"}]}',
        stderr="",
        duration_s=45.0,
        timed_out=True,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="tool_use_roundtrip",
        marker="FCC_NIM_TOOL",
        run=run,
        log_delta="API_REQUEST: request_id=req_1 model=z-ai/glm-5.1 messages=2",
        log_path=tmp_path / "server.log",
    )

    assert outcome.classification == "probe_timeout"
    assert outcome.token_evidence["timed_out"] is True
    assert regression_failures([outcome]) == []


def test_nvidia_nim_cli_success_beats_verbose_timeout_words(tmp_path: Path) -> None:
    run = ClaudeCliRun(
        command=("claude", "-p", "x"),
        returncode=0,
        stdout="FCC_NIM_THINK",
        stderr="",
        duration_s=0.1,
    )
    outcome = make_outcome(
        model="z-ai/glm-5.1",
        full_model="nvidia_nim/z-ai/glm-5.1",
        source="nvidia_nim_cli_default",
        feature="thinking",
        marker="FCC_NIM_THINK",
        run=run,
        log_delta=(
            "API_REQUEST: request_id=req_1 model=z-ai/glm-5.1 messages=1 "
            "read_timeout_s=300"
        ),
        log_path=tmp_path / "server.log",
    )

    assert outcome.classification == "passed"
    assert outcome.request_count == 1
