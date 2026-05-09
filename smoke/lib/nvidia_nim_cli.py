"""Claude Code CLI characterization helpers for NVIDIA NIM smoke tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smoke.lib.config import SmokeConfig, redacted
from smoke.lib.server import RunningServer

REGRESSION_CLASSIFICATIONS = frozenset({"harness_bug", "product_failure"})

_HTTP_REGRESSION_PATTERNS = (
    r'POST /v1/messages[^"\n]* HTTP/1\.1" 4(?!01|03|04|08|09)\d\d',
    r'POST /v1/messages[^"\n]* HTTP/1\.1" 5\d\d',
)
_UPSTREAM_UNAVAILABLE_MARKERS = (
    "upstream_unavailable",
    "readtimeout",
    "connecterror",
    "connection refused",
    "timed out",
    "rate limit",
    "429",
    "overloaded",
    "capacity",
    "upstream provider",
)
_MISSING_ENV_MARKERS = (
    "api key",
    "not logged in",
    "authentication",
    "permission denied",
)


@dataclass(frozen=True, slots=True)
class ClaudeCliRun:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


@dataclass(frozen=True, slots=True)
class NimCliMatrixOutcome:
    model: str
    full_model: str
    source: str
    feature: str
    outcome: str
    classification: str
    duration_s: float
    cli_returncode: int | None
    token_evidence: dict[str, Any]
    request_count: int
    log_path: str
    stdout_excerpt: str
    stderr_excerpt: str
    log_excerpt: str


def run_claude_cli(
    *,
    claude_bin: str,
    server: RunningServer,
    config: SmokeConfig,
    cwd: Path,
    prompt: str,
    tools: str | None,
    extra_args: tuple[str, ...] = (),
    session_id: str | None = None,
    resume_session_id: str | None = None,
    no_session_persistence: bool = True,
) -> ClaudeCliRun:
    """Run Claude Code CLI against the local smoke proxy."""
    cwd.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [claude_bin, "--bare"]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    if session_id:
        cmd.extend(["--session-id", session_id])
    cmd.extend(
        [
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "--model",
            "sonnet",
        ]
    )
    if no_session_persistence:
        cmd.append("--no-session-persistence")
    if tools is not None:
        cmd.extend(["--tools", tools])
        if tools:
            cmd.extend(["--allowedTools", tools])
    cmd.extend(extra_args)
    cmd.extend(["-p", prompt])

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = server.base_url
    env["ANTHROPIC_API_URL"] = f"{server.base_url}/v1"
    env.setdefault("ANTHROPIC_API_KEY", "sk-smoke-proxy")
    if config.settings.anthropic_auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = config.settings.anthropic_auth_token
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=config.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ClaudeCliRun(
            command=tuple(cmd),
            returncode=None,
            stdout=_coerce_timeout_text(exc.stdout),
            stderr=_coerce_timeout_text(exc.stderr),
            duration_s=time.monotonic() - started,
            timed_out=True,
        )

    return ClaudeCliRun(
        command=tuple(cmd),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_s=time.monotonic() - started,
    )


def read_log_offset(log_path: Path) -> int:
    """Return the current text length of a smoke server log."""
    if not log_path.is_file():
        return 0
    return len(log_path.read_text(encoding="utf-8", errors="replace"))


def read_log_delta(log_path: Path, offset: int) -> str:
    """Return smoke server log text written after ``offset``."""
    if not log_path.is_file():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text[offset:]


def token_evidence(
    *,
    feature: str,
    marker: str,
    run: ClaudeCliRun,
    log_delta: str,
) -> dict[str, Any]:
    """Collect compact evidence for a CLI feature probe."""
    combined = f"{run.combined_output}\n{log_delta}"
    lower = combined.lower()
    return {
        "feature": feature,
        "marker_present": bool(marker and marker in combined),
        "thinking_delta_count": combined.count("thinking_delta"),
        "tool_use_count": combined.count('"tool_use"'),
        "tool_result_count": combined.count('"tool_result"'),
        "task_tool_count": combined.count('"name": "Task"')
        + combined.count('"name":"Task"'),
        "run_in_background_false": "run_in_background" in combined and "false" in lower,
        "compact_boundary": "compact_boundary" in combined,
        "compact_metadata": "compact_metadata" in combined,
        "http_422": 'HTTP/1.1" 422' in combined,
        "http_500": bool(re.search(r'HTTP/1\.1" 5\d\d', combined)),
        "timed_out": run.timed_out,
    }


def classify_probe(
    *,
    run: ClaudeCliRun,
    log_delta: str,
    marker: str,
    requires_tool_result: bool = False,
    requires_task: bool = False,
    requires_compact: bool = False,
) -> tuple[str, str]:
    """Classify a probe without failing compatibility characterization failures."""
    combined = f"{run.combined_output}\n{log_delta}"
    lower = combined.lower()

    if _has_proxy_regression(log_delta):
        return "failed", "product_failure"
    if run.returncode != 0 and any(
        marker_text in lower for marker_text in _MISSING_ENV_MARKERS
    ):
        return "skipped", "missing_env"
    if run.timed_out:
        return "failed", "probe_timeout"

    marker_ok = not marker or marker in combined
    tool_ok = not requires_tool_result or '"tool_result"' in combined
    task_ok = not requires_task or (
        ('"name": "Task"' in combined or '"name":"Task"' in combined)
        and "run_in_background" in combined
        and "false" in lower
    )
    compact_ok = not requires_compact or (
        "compact_boundary" in combined
        or "compact_metadata" in combined
        or "/compact" in combined
        or "compact" in lower
    )
    cli_ok = run.returncode == 0

    if cli_ok and marker_ok and tool_ok and task_ok and compact_ok:
        return "passed", "passed"
    if any(marker_text in lower for marker_text in _UPSTREAM_UNAVAILABLE_MARKERS):
        return "failed", "upstream_unavailable"
    if not _has_proxy_request(log_delta):
        return "failed", "harness_bug"
    return "failed", "model_feature_failure"


def make_outcome(
    *,
    model: str,
    full_model: str,
    source: str,
    feature: str,
    marker: str,
    run: ClaudeCliRun,
    log_delta: str,
    log_path: Path,
    requires_tool_result: bool = False,
    requires_task: bool = False,
    requires_compact: bool = False,
) -> NimCliMatrixOutcome:
    """Build one report outcome from a CLI run and its server log delta."""
    outcome, classification = classify_probe(
        run=run,
        log_delta=log_delta,
        marker=marker,
        requires_tool_result=requires_tool_result,
        requires_task=requires_task,
        requires_compact=requires_compact,
    )
    evidence = token_evidence(
        feature=feature,
        marker=marker,
        run=run,
        log_delta=log_delta,
    )
    return NimCliMatrixOutcome(
        model=model,
        full_model=full_model,
        source=source,
        feature=feature,
        outcome=outcome,
        classification=classification,
        duration_s=round(run.duration_s, 3),
        cli_returncode=run.returncode,
        token_evidence=evidence,
        request_count=_request_count(log_delta),
        log_path=str(log_path),
        stdout_excerpt=_excerpt(run.stdout),
        stderr_excerpt=_excerpt(run.stderr),
        log_excerpt=_excerpt(log_delta),
    )


def write_matrix_report(
    config: SmokeConfig,
    outcomes: list[NimCliMatrixOutcome],
) -> Path:
    """Write the NVIDIA NIM CLI compatibility matrix report."""
    config.results_dir.mkdir(parents=True, exist_ok=True)
    path = (
        config.results_dir
        / f"nvidia-nim-cli-matrix-{config.worker_id}-{int(time.time())}.json"
    )
    payload = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "worker_id": config.worker_id,
        "target": "nvidia_nim_cli",
        "models": sorted({outcome.full_model for outcome in outcomes}),
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def regression_failures(outcomes: list[NimCliMatrixOutcome]) -> list[str]:
    """Return report lines for classifications that should fail pytest."""
    return [
        f"{outcome.full_model} {outcome.feature}: {outcome.classification}"
        for outcome in outcomes
        if outcome.classification in REGRESSION_CLASSIFICATIONS
    ]


def _has_proxy_regression(log_delta: str) -> bool:
    if "CREATE_MESSAGE_ERROR" in log_delta:
        return True
    return any(re.search(pattern, log_delta) for pattern in _HTTP_REGRESSION_PATTERNS)


def _has_proxy_request(log_delta: str) -> bool:
    return "POST /v1/messages" in log_delta or "API_REQUEST:" in log_delta


def _request_count(log_delta: str) -> int:
    access_log_count = log_delta.count("POST /v1/messages")
    service_log_count = log_delta.count("API_REQUEST:")
    return max(access_log_count, service_log_count)


def _excerpt(value: str, *, max_chars: int = 2400) -> str:
    if len(value) <= max_chars:
        return redacted(value)
    return redacted(value[-max_chars:])


def _coerce_timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
