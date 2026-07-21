"""``fcc-agent`` CLI — local coding agent against the FCC proxy."""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from free_claude_code.cli.launchers.common import preflight_proxy
from free_claude_code.config.paths import config_dir_path
from free_claude_code.config.server_urls import local_proxy_root_url
from free_claude_code.config.settings import get_settings
from free_claude_code.core.quota import DailyExhaustionStore
from free_claude_code.core.version import package_version

from .job_store import (
    create_job,
    load_job,
    spawn_job_worker,
)
from .jobs import JobStatus
from .loop import AgentLoop, AgentStopReason
from .permissions import AllowlistPermissionGate, console_confirm
from .proxy_client import FallbackProxyClient, HttpProxyClient
from .tools import ToolRegistry
from .workspace import Workspace, WorkspaceError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcc-agent",
        description="Own-agent harness: coding agent that uses the local FCC proxy.",
    )
    parser.add_argument("--version", action="store_true", help="Print version.")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Task for the agent. If omitted, read from stdin.",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root (default: ALLOWED_DIR or cwd).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id routed by the proxy (default: Settings.model).",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=40,
        help="Hard cap on model↔tool rounds (default 40).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve write/edit/bash (dangerous; for scripts/CI).",
    )
    parser.add_argument(
        "--fallback-model",
        action="append",
        default=[],
        dest="fallback_models",
        help="Extra model ids to try on 429/quota (repeatable).",
    )
    return parser


def _build_jobs_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcc-agent jobs",
        description="Unattended agent job queue for SSH sessions.",
    )
    sub = parser.add_subparsers(dest="jobs_command", required=True)

    enqueue = sub.add_parser(
        "enqueue", help="Queue a job and run it in the background."
    )
    enqueue.add_argument("prompt", help="Task for the agent.")
    enqueue.add_argument("--workspace", default=None)
    enqueue.add_argument("--model", default=None)
    enqueue.add_argument("--max-turns", type=int, default=40)
    enqueue.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Job wall-clock timeout in seconds (default: MESSAGING_AGENT_JOB_TIMEOUT_S).",
    )
    enqueue.add_argument(
        "--no-auto-approve",
        action="store_true",
        help="Require console confirm (not useful for detached SSH jobs).",
    )

    status = sub.add_parser("status", help="Show job status.")
    status.add_argument("job_id", help="Job id from enqueue.")

    result = sub.add_parser(
        "result", help="Print job final text (and exit non-zero on failure)."
    )
    result.add_argument("job_id", help="Job id from enqueue.")

    run = sub.add_parser("_run", help=argparse.SUPPRESS)
    run.add_argument("job_id")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "jobs":
        _jobs_main(raw[1:])
        return

    parser = _build_parser()
    args = parser.parse_args(raw)

    if args.version:
        print(f"fcc-agent {package_version()}")
        return

    prompt = args.prompt
    if prompt is None:
        if sys.stdin.isatty():
            parser.error("prompt required (or pipe it on stdin)")
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("empty prompt")

    settings = get_settings()
    proxy_root = local_proxy_root_url(settings)
    if error := preflight_proxy(proxy_root):
        print(
            f"fcc-agent: local proxy at {proxy_root} is not reachable ({error}).\n"
            "Start it with `fcc-server`, then retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    workspace_root = _resolve_workspace(args.workspace, settings.allowed_dir)
    try:
        workspace = Workspace(workspace_root)
    except WorkspaceError as exc:
        print(f"fcc-agent: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    model = (args.model or settings.model or "claude-sonnet-4-5").strip()
    permissions = AllowlistPermissionGate(
        confirm=None if args.yes else console_confirm,
        auto_approve=args.yes,
    )
    client: HttpProxyClient | FallbackProxyClient = HttpProxyClient(
        proxy_root,
        auth_token=settings.anthropic_auth_token,
    )
    if args.fallback_models:
        client = FallbackProxyClient(
            inner=client,
            fallback_models=list(args.fallback_models),
            exhaustion=DailyExhaustionStore(config_dir_path() / "agent_quota.db"),
        )
    tools = ToolRegistry(workspace, permissions)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=tools,
        model=model,
        max_turns=args.max_turns,
    )

    print(f"fcc-agent · workspace={workspace.root} · model={model}", file=sys.stderr)
    result = asyncio.run(loop.run(prompt))
    if result.final_text:
        print(result.final_text)
    if result.stop_reason == AgentStopReason.COMPLETED:
        return
    print(
        f"\n[fcc-agent] stopped: {result.stop_reason.value}"
        + (f" — {result.detail}" if result.detail else ""),
        file=sys.stderr,
    )
    raise SystemExit(2 if result.stop_reason == AgentStopReason.ERROR else 0)


def _jobs_main(argv: Sequence[str]) -> None:
    parser = _build_jobs_parser()
    args = parser.parse_args(list(argv))
    if args.jobs_command == "enqueue":
        _jobs_enqueue(args)
    elif args.jobs_command == "status":
        _jobs_status(args.job_id)
    elif args.jobs_command == "result":
        _jobs_result(args.job_id)
    elif args.jobs_command == "_run":
        _jobs_run(args.job_id)
    else:
        parser.error(f"unknown jobs command: {args.jobs_command}")


def _jobs_enqueue(args: argparse.Namespace) -> None:
    settings = get_settings()
    workspace_root = _resolve_workspace(args.workspace, settings.allowed_dir)
    model = (args.model or settings.model or "claude-sonnet-4-5").strip()
    timeout = (
        float(args.timeout)
        if args.timeout is not None
        else float(settings.messaging_agent_job_timeout_s)
    )
    job = create_job(
        prompt=args.prompt,
        workspace=str(workspace_root.expanduser().resolve()),
        model=model,
        max_turns=args.max_turns,
        job_timeout_s=timeout,
        auto_approve=not args.no_auto_approve,
    )
    pid = spawn_job_worker(job.job_id)
    job.pid = pid
    job.save()
    print(job.job_id)


def _jobs_status(job_id: str) -> None:
    job = load_job(job_id)
    if job is None:
        print(f"fcc-agent jobs: unknown job_id {job_id}", file=sys.stderr)
        raise SystemExit(1)
    print(f"job_id={job.job_id}")
    print(f"status={job.status}")
    print(f"model={job.model}")
    print(f"workspace={job.workspace}")
    if job.detail:
        print(f"detail={job.detail}")
    if job.stop_reason:
        print(f"stop_reason={job.stop_reason}")
    if job.pid is not None:
        print(f"pid={job.pid}")


def _jobs_result(job_id: str) -> None:
    job = load_job(job_id)
    if job is None:
        print(f"fcc-agent jobs: unknown job_id {job_id}", file=sys.stderr)
        raise SystemExit(1)
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        print(f"fcc-agent jobs: job {job_id} still {job.status}", file=sys.stderr)
        raise SystemExit(3)
    if job.final_text:
        print(job.final_text)
    if job.status == JobStatus.COMPLETED.value:
        return
    if job.detail:
        print(job.detail, file=sys.stderr)
    raise SystemExit(2)


def _jobs_run(job_id: str) -> None:
    job = load_job(job_id)
    if job is None:
        print(f"fcc-agent jobs: unknown job_id {job_id}", file=sys.stderr)
        raise SystemExit(1)

    settings = get_settings()
    proxy_root = local_proxy_root_url(settings)
    if error := preflight_proxy(proxy_root):
        job.status = JobStatus.FAILED.value
        job.detail = f"proxy unreachable: {error}"
        job.finished_at = time.time()
        job.save()
        raise SystemExit(1)

    try:
        workspace = Workspace(Path(job.workspace))
    except WorkspaceError as exc:
        job.status = JobStatus.FAILED.value
        job.detail = str(exc)
        job.finished_at = time.time()
        job.save()
        raise SystemExit(1) from exc

    job.status = JobStatus.RUNNING.value
    job.started_at = time.time()
    job.save()

    permissions = AllowlistPermissionGate(
        confirm=None if job.auto_approve else console_confirm,
        auto_approve=job.auto_approve,
    )
    client = HttpProxyClient(proxy_root, auth_token=settings.anthropic_auth_token)
    tools = ToolRegistry(workspace, permissions)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=tools,
        model=job.model,
        max_turns=job.max_turns,
    )

    try:
        result = asyncio.run(
            asyncio.wait_for(loop.run(job.prompt), timeout=job.job_timeout_s)
        )
        job.final_text = result.final_text
        job.stop_reason = result.stop_reason.value
        job.detail = result.detail
        if result.stop_reason == AgentStopReason.COMPLETED:
            job.status = JobStatus.COMPLETED.value
        else:
            job.status = JobStatus.FAILED.value
            if not job.detail:
                job.detail = result.stop_reason.value
    except TimeoutError:
        job.status = JobStatus.FAILED.value
        job.detail = f"exceeded job_timeout_s={job.job_timeout_s}"
    except Exception as exc:
        job.status = JobStatus.FAILED.value
        job.detail = str(exc)
    finally:
        job.finished_at = time.time()
        job.save()


def _resolve_workspace(cli_workspace: str | None, allowed_dir: str) -> Path:
    if cli_workspace:
        return Path(cli_workspace).expanduser()
    if allowed_dir.strip():
        return Path(allowed_dir).expanduser()
    return Path(os.getcwd())


if __name__ == "__main__":
    main()
