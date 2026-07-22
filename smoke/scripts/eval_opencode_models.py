"""Repeatable OpenCode agentic model eval (Phase 6 / C1).

Picks free candidates from ``GET /v1/models``, runs three small agentic tasks
via ``opencode run``, scores pass/fail, prints a markdown table, and can update
``MODEL`` in ``~/.fcc/.env``.

Exclude ``github_models/*`` (free tier ~4k tokens/request).

Usage::

    uv run python smoke/scripts/eval_opencode_models.py
    uv run python smoke/scripts/eval_opencode_models.py --apply-winner
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.cli.launchers.opencode import (
    build_opencode_launcher_env,
    write_opencode_config,
)
from free_claude_code.cli.proxy_auth import proxy_auth_token
from free_claude_code.config.paths import managed_env_path
from free_claude_code.config.server_urls import local_proxy_root_url
from free_claude_code.config.settings import get_settings

PrepareFn = Callable[[Path], None]
VerifyFn = Callable[[Path], bool]

# Classes from the backlog; first available match wins per class.
CANDIDATE_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qwen-coder", ("qwen3-coder", "qwen3.6-27b", "qwen3-coder-next")),
    ("kimi", ("kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5", "kimi-k2")),
    ("gpt-oss-120b", ("gpt-oss-120b",)),
    ("deepseek", ("deepseek-v4-flash", "deepseek-v3.2", "deepseek-v4-pro")),
    (
        "gemini-flash",
        (
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
            "gemini-2.5-flash",
        ),
    ),
    ("groq-llama", ("llama-3.3-70b-versatile",)),
)

# Prefer OpenRouter for Kimi when NIM returns 404 for the hosted function.
PREFERRED_PROVIDERS = (
    "cerebras",
    "groq",
    "open_router",
    "nvidia_nim",
    "gemini",
)

EXCLUDED_PROVIDER_PREFIXES = ("github_models/",)


@dataclass(frozen=True, slots=True)
class AgenticTask:
    task_id: str
    prompt: str
    prepare: PrepareFn | None = None
    verify: VerifyFn | None = None


def _prepare_fix_add(root: Path) -> None:
    (root / "broken_add.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )


def _verify_fix_add(root: Path) -> bool:
    path = root / "broken_add.py"
    if not path.is_file():
        return False
    compact = path.read_text(encoding="utf-8").replace(" ", "")
    return "returna+b" in compact


def _prepare_read_report(root: Path) -> None:
    (root / "secret_value.txt").write_text("C1_SECRET_42\n", encoding="utf-8")


def _verify_write_marker(root: Path) -> bool:
    path = root / "marker.txt"
    return path.is_file() and path.read_text(encoding="utf-8").strip() == "C1_WRITE_OK"


def _verify_read_report(root: Path) -> bool:
    path = root / "answer.txt"
    return path.is_file() and path.read_text(encoding="utf-8").strip() == "C1_SECRET_42"


AGENTIC_TASKS: tuple[AgenticTask, ...] = (
    AgenticTask(
        task_id="write_marker",
        prompt=(
            "Working directory is the current project root. "
            "Create a file named marker.txt (relative path only) containing "
            "exactly the text C1_WRITE_OK and nothing else. Do not ask questions."
        ),
        verify=_verify_write_marker,
    ),
    AgenticTask(
        task_id="fix_add",
        prompt=(
            "The file broken_add.py is wrong. Fix it so add(a, b) returns a+b. "
            "Keep the function name add. Do not ask questions."
        ),
        prepare=_prepare_fix_add,
        verify=_verify_fix_add,
    ),
    AgenticTask(
        task_id="read_report",
        prompt=(
            "Read secret_value.txt and create answer.txt containing exactly "
            "that file's trimmed contents. Do not ask questions."
        ),
        prepare=_prepare_read_report,
        verify=_verify_read_report,
    ),
)


@dataclass(slots=True)
class TaskResult:
    model: str
    task_id: str
    passed: bool
    latency_s: float
    detail: str


def fetch_model_ids(proxy_root: str, *, auth_token: str) -> list[str]:
    url = f"{proxy_root.rstrip('/')}/v1/models"
    request = urllib.request.Request(url, method="GET")
    token = proxy_auth_token(auth_token)
    if token and token != "fcc-no-auth":
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [item["id"] for item in payload.get("data", []) if "id" in item]


def provider_model_refs(gateway_ids: list[str]) -> list[str]:
    """Strip Claude gateway prefixes → ``provider/model`` refs used by Settings.MODEL."""
    refs: list[str] = []
    for model_id in gateway_ids:
        if model_id.startswith("anthropic/"):
            refs.append(model_id.removeprefix("anthropic/"))
        elif model_id.startswith("claude-3-freecc-no-thinking/"):
            continue
        else:
            # Already a provider/model style id advertised without gateway prefix.
            if "/" in model_id and not model_id.startswith("claude-"):
                refs.append(model_id)
    return refs


def select_candidates(
    refs: list[str],
    *,
    max_models: int = 6,
) -> list[str]:
    """Pick one model per candidate class, preferring free-friendly providers."""

    filtered = [
        ref
        for ref in refs
        if not any(ref.startswith(prefix) for prefix in EXCLUDED_PROVIDER_PREFIXES)
    ]

    def rank(ref: str) -> tuple[int, int, str]:
        provider = ref.split("/", 1)[0]
        try:
            provider_rank = PREFERRED_PROVIDERS.index(provider)
        except ValueError:
            provider_rank = 99
        return (provider_rank, len(ref), ref)

    chosen: list[str] = []
    for _label, needles in CANDIDATE_CLASSES:
        matches = [
            ref
            for ref in filtered
            if any(needle.lower() in ref.lower() for needle in needles)
        ]
        if not matches:
            continue
        best = sorted(set(matches), key=rank)[0]
        if best not in chosen:
            chosen.append(best)
        if len(chosen) >= max_models:
            break
    return chosen


def _prepare_workspace(task: AgenticTask, root: Path) -> None:
    if callable(task.prepare):
        task.prepare(root)


def _check_task(task: AgenticTask, root: Path) -> bool:
    if not callable(task.verify):
        return False
    try:
        return bool(task.verify(root))
    except Exception:
        return False


def run_opencode_task(
    *,
    model: str,
    task: AgenticTask,
    proxy_root: str,
    auth_token: str,
    opencode_bin: str,
    timeout_s: float,
) -> TaskResult:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fcc-c1-") as tmp:
        root = Path(tmp)
        _prepare_workspace(task, root)
        config_path = write_opencode_config(
            proxy_root_url=proxy_root,
            auth_token=auth_token,
            model=model,
            verdict_command=["false"],  # keep eval free of Verdict MCP noise
            config_dir=root / "cfg",
        )
        # Drop MCP; cap output tokens (Groq qwen rejects >16384).
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload.pop("mcp", None)
        models = payload.get("provider", {}).get("fcc", {}).get("models", {})
        if (
            isinstance(models, dict)
            and model in models
            and isinstance(models[model], dict)
        ):
            models[model]["limit"] = {"context": 128000, "output": 8192}
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        env = build_opencode_launcher_env(config_path=config_path, base_env=os.environ)
        try:
            completed = subprocess.run(
                [
                    opencode_bin,
                    "run",
                    "--dir",
                    str(root),
                    "--auto",
                    "--pure",
                    "-m",
                    f"fcc/{model}",
                    task.prompt,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TaskResult(
                model=model,
                task_id=task.task_id,
                passed=False,
                latency_s=time.perf_counter() - started,
                detail="timeout",
            )
        passed = _check_task(task, root)
        detail = "ok" if passed else f"exit={completed.returncode}"
        if not passed and completed.stderr:
            detail = f"{detail}; stderr={completed.stderr[-300:]}"
        return TaskResult(
            model=model,
            task_id=task.task_id,
            passed=passed,
            latency_s=time.perf_counter() - started,
            detail=detail,
        )


def render_markdown_table(
    results: list[TaskResult], candidates: list[str]
) -> tuple[str, str]:
    task_ids = [task.task_id for task in AGENTIC_TASKS]
    lines = [
        "| model | " + " | ".join(task_ids) + " | pass | avg_s |",
        "| --- | " + " | ".join(["---"] * len(task_ids)) + " | --- | --- |",
    ]
    by_model: dict[str, list[TaskResult]] = {model: [] for model in candidates}
    for result in results:
        by_model.setdefault(result.model, []).append(result)

    ranking: list[tuple[int, float, str]] = []
    for model in candidates:
        rows = by_model.get(model, [])
        cells: list[str] = []
        for task_id in task_ids:
            match = next((row for row in rows if row.task_id == task_id), None)
            cells.append("✅" if match and match.passed else "❌")
        passed = sum(1 for row in rows if row.passed)
        avg = (sum(row.latency_s for row in rows) / len(rows)) if rows else 0.0
        ranking.append((passed, -avg, model))
        lines.append(
            f"| `{model}` | "
            + " | ".join(cells)
            + f" | {passed}/{len(task_ids)} | {avg:.1f} |"
        )

    ranking.sort(reverse=True)
    winner = ranking[0][2] if ranking else ""
    lines.append("")
    lines.append(f"**Winner:** `{winner}`" if winner else "**Winner:** none")
    return "\n".join(lines), winner


def update_managed_model(env_path: Path, model: str) -> None:
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    if re.search(r"(?m)^MODEL=", text):
        text = re.sub(r"(?m)^MODEL=.*$", f"MODEL={model}", text)
    else:
        text = text.rstrip() + f"\nMODEL={model}\n"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-models", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--apply-winner",
        action="store_true",
        help="Write winning MODEL into ~/.fcc/.env",
    )
    parser.add_argument(
        "--results",
        default="docs/evals/2026-07-21-c1-opencode-models.md",
        help="Markdown output path (repo-relative).",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model refs to force (skip auto-select).",
    )
    args = parser.parse_args(argv)

    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        home_bin = Path.home() / ".opencode" / "bin" / "opencode"
        opencode_bin = str(home_bin) if home_bin.is_file() else None
    if not opencode_bin:
        print("opencode binary not found on PATH or ~/.opencode/bin", file=sys.stderr)
        return 2

    settings = get_settings()
    proxy_root = local_proxy_root_url(settings)
    try:
        gateway_ids = fetch_model_ids(
            proxy_root, auth_token=settings.anthropic_auth_token
        )
    except urllib.error.URLError as exc:
        print(f"proxy unreachable at {proxy_root}: {exc}", file=sys.stderr)
        return 1

    if args.models.strip():
        candidates = [item.strip() for item in args.models.split(",") if item.strip()]
    else:
        candidates = select_candidates(
            provider_model_refs(gateway_ids),
            max_models=args.max_models,
        )
    if not candidates:
        print("no candidates selected from /v1/models", file=sys.stderr)
        return 1

    print(f"proxy={proxy_root}")
    print("candidates:")
    for model in candidates:
        print(f"  - {model}")

    results: list[TaskResult] = []
    for model in candidates:
        for task in AGENTIC_TASKS:
            print(f"→ {model} :: {task.task_id} …", flush=True)
            result = run_opencode_task(
                model=model,
                task=task,
                proxy_root=proxy_root,
                auth_token=settings.anthropic_auth_token,
                opencode_bin=opencode_bin,
                timeout_s=args.timeout,
            )
            results.append(result)
            mark = "PASS" if result.passed else "FAIL"
            print(f"  {mark} {result.latency_s:.1f}s ({result.detail})", flush=True)

    table, winner = render_markdown_table(results, candidates)
    print()
    print(table)

    results_path = Path(args.results)
    if not results_path.is_absolute():
        results_path = Path(__file__).resolve().parents[2] / results_path
    results_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "# C1 — OpenCode agentic model eval",
            "",
            f"Date: 2026-07-21 · proxy `{proxy_root}` · opencode `{opencode_bin}`",
            "",
            "Tasks: write_marker, fix_add, read_report via `opencode run`.",
            "Excluded: `github_models/*`.",
            "",
            table,
            "",
        ]
    )
    results_path.write_text(body + "\n", encoding="utf-8")
    print(f"wrote {results_path}")

    if args.apply_winner:
        if not winner:
            print("no winner to apply", file=sys.stderr)
            return 1
        env_path = managed_env_path()
        update_managed_model(env_path, winner)
        print(f"updated MODEL={winner} in {env_path}")
        # Refresh generated OpenCode config for next fcc-opencode launch.
        write_opencode_config(
            proxy_root_url=proxy_root,
            auth_token=settings.anthropic_auth_token,
            model=winner,
            verdict_command=["fcc-verdict", "serve-mcp"],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
