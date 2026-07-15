"""``fcc-council`` command-line interface.

Default output is Markdown; ``--output json`` returns the full structured
payload. The CLI is a thin shell over :class:`CouncilService` so it shares the
exact deliberation core with the MCP server.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from free_claude_code.core.version import package_version

from .config import load_config
from .errors import CouncilError, InsufficientFreeModelsError
from .models import CouncilResult, ModelRef
from .service import CouncilService, ProviderUsage, ProviderValidation
from .skill import install_skill, render_mcp_registration
from .storage import UsageRow


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcc-council",
        description="Free-only multi-model deliberation council for FCC.",
    )
    parser.add_argument("--version", action="store_true", help="Print version.")
    sub = parser.add_subparsers(dest="command")

    providers = sub.add_parser("providers", help="Show provider free-access status.")
    providers.add_argument("action", nargs="?", choices=["validate"], default=None)

    models = sub.add_parser("models", help="List discovered models.")
    models.add_argument(
        "--free-only",
        action="store_true",
        help="Only list free-eligible models (default in free-only mode).",
    )

    sub.add_parser("benchmark", help="Run the local calibration benchmark.")

    evaluate = sub.add_parser("evaluate", help="Run a council deliberation.")
    evaluate.add_argument("prompt", help="The question or task.")
    evaluate.add_argument("--depth", choices=["quick", "standard", "deep"])
    evaluate.add_argument("--task-type", default="auto")
    evaluate.add_argument("--file", action="append", default=[], dest="files")
    evaluate.add_argument("--privacy", choices=["public", "redacted", "local_only"])
    evaluate.add_argument("--max-rounds", type=int)
    evaluate.add_argument("--criteria", default="")

    usage = sub.add_parser("usage", help="Show approximate token/request usage.")
    usage.add_argument("--day", default=None, help="YYYY-MM-DD (default: today).")

    sub.add_parser("serve-mcp", help="Start the local MCP server (stdio).")
    sub.add_parser(
        "install-claude-skill", help="Install the deep-council Claude Code skill."
    )

    for name in ("providers", "models", "evaluate", "benchmark", "usage"):
        sub.choices[name].add_argument(
            "--output", choices=["markdown", "json"], default="markdown"
        )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.version:
        print(f"fcc-council {package_version()}")
        return
    if args.command is None:
        parser.print_help()
        return

    if args.command == "serve-mcp":
        _serve_mcp()
        return
    if args.command == "install-claude-skill":
        _install_skill()
        return

    try:
        asyncio.run(_run_async(args))
    except InsufficientFreeModelsError as exc:
        _fail_insufficient(exc)
    except CouncilError as exc:
        print(f"Council error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


async def _run_async(args: argparse.Namespace) -> None:
    service = CouncilService.create(config=load_config())
    try:
        if args.command == "providers":
            rows = await service.validate_providers()
            _emit(args.output, _providers_json(rows), _render_providers(rows))
        elif args.command == "models":
            models = await service.list_free_models()
            payload = [_model_json(m) for m in models]
            _emit(args.output, payload, _render_models(models))
        elif args.command == "benchmark":
            await _run_benchmark(service, args.output)
        elif args.command == "usage":
            summaries, rows = service.usage(day=args.day)
            _emit(
                args.output,
                _usage_json(summaries, rows),
                _render_usage(summaries, rows, args.day),
            )
        elif args.command == "evaluate":
            result, report_path = await service.evaluate(
                args.prompt,
                task_type=args.task_type,
                depth=args.depth,
                files=args.files,
                privacy=args.privacy,
                max_rounds=args.max_rounds,
                criteria=args.criteria,
            )
            _emit(
                args.output,
                {"report_path": str(report_path), **result.compact()},
                _render_result(result, report_path),
            )
    finally:
        await service.cleanup()


async def _run_benchmark(service: CouncilService, output: str) -> None:
    from .benchmark import run_benchmark

    models = await service.list_free_models()
    results = await run_benchmark(service.invoker, models, store=service.store)
    payload = [
        {
            "model": r.model_key,
            "category": r.task_category,
            "passed": r.passed,
            "json_ok": r.json_ok,
            "latency_s": round(r.latency_s, 2),
        }
        for r in results
    ]
    passed = sum(1 for r in results if r.passed)
    lines = [f"# Benchmark — {passed}/{len(results)} tasks passed", ""]
    for row in payload:
        mark = "✅" if row["passed"] else "❌"
        lines.append(
            f"- {mark} `{row['model']}` · {row['category']} · "
            f"json={'y' if row['json_ok'] else 'n'} · {row['latency_s']}s"
        )
    _emit(output, payload, "\n".join(lines))


def _serve_mcp() -> None:
    from .mcp_server import run_stdio

    run_stdio()


def _install_skill() -> None:
    created = install_skill()
    print("Installed the deep-council skill:")
    for path in created:
        print(f"  - {path}")
    print("\nTo register the MCP server, add this to your Claude Code MCP config:\n")
    print(render_mcp_registration())


# ---------------------------------------------------------------------- #
# Rendering
# ---------------------------------------------------------------------- #
def _emit(output: str, payload: object, markdown: str) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(markdown)


def _providers_json(rows: list[ProviderValidation]) -> list[dict[str, object]]:
    return [
        {
            "provider": r.provider,
            "authenticated": r.authenticated,
            "free_status": r.free_status,
            "tier": r.tier,
            "usable": r.usable,
            "note": r.note,
        }
        for r in rows
    ]


def _render_providers(rows: list[ProviderValidation]) -> str:
    lines = [
        "# Providers",
        "",
        "| Provider | Auth | Free status | Tier | Usable |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {r.provider} | {'OK' if r.authenticated else '—'} | "
        f"{r.free_status} | {r.tier} | {'yes' if r.usable else 'no'} |"
        for r in rows
    )
    lines.append("")
    lines.append("_API keys are never displayed._")
    return "\n".join(lines)


def _model_json(model: ModelRef) -> dict[str, object]:
    return {
        "provider": model.provider,
        "model_id": model.model_id,
        "family": model.family,
        "cost_status": model.cost_status.value,
        "supports_tools": model.supports_tools,
    }


def _render_models(models: list[ModelRef]) -> str:
    lines = ["# Free-eligible models", ""]
    if not models:
        lines.append("_No free-eligible models found. Configure provider keys._")
        return "\n".join(lines)
    lines.extend(
        f"- `{model.key}` · {model.family} · {model.cost_status.value}"
        for model in models
    )
    return "\n".join(lines)


def _usage_json(
    summaries: list[ProviderUsage], rows: list[UsageRow]
) -> dict[str, object]:
    return {
        "providers": [
            {
                "provider": s.provider,
                "requests": s.requests,
                "total_tokens": s.total_tokens,
                "rpd_limit": s.rpd_limit,
                "pct_of_rpd": s.pct_of_rpd,
                "budget_class": s.budget_class,
            }
            for s in summaries
        ],
        "models": [
            {
                "provider": r.provider,
                "model_key": r.model_key,
                "day": r.day,
                "requests": r.requests,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
            }
            for r in rows
        ],
    }


def _render_usage(
    summaries: list[ProviderUsage], rows: list[UsageRow], day: object
) -> str:
    when = day or "today"
    lines = [f"# Usage ({when}) — approximate", ""]
    if not summaries:
        lines.append("_No usage recorded yet. Run an evaluation first._")
        return "\n".join(lines)
    lines += [
        "| Provider | Requests | vs RPD | Tokens | Class |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        vs = (
            f"{s.requests}/{s.rpd_limit} ({s.pct_of_rpd}%)"
            if s.rpd_limit is not None
            else "—"
        )
        lines.append(
            f"| {s.provider} | {s.requests} | {vs} | {s.total_tokens:,} | "
            f"{s.budget_class} |"
        )
    lines += ["", "## By model", ""]
    lines.extend(
        f"- `{r.model_key}` · {r.requests} req · "
        f"{r.input_tokens:,} in / {r.output_tokens:,} out"
        for r in rows
    )
    lines += [
        "",
        "_Approximate: provider-reported tokens where available; "
        "RPD limits are best-effort and change often._",
    ]
    return "\n".join(lines)


def _render_result(result: CouncilResult, report_path: object) -> str:
    compact = result.compact()
    lines = [
        "# Council result",
        "",
        f"**Task type:** {compact['task_type']} · **Depth:** {compact['depth']} · "
        f"**Rounds:** {compact['rounds']}",
        "",
        "## Answer",
        str(compact["answer"]) or "_(no answer)_",
        "",
        "## Recommended action",
        str(compact["recommended_action"]) or "_(none)_",
    ]
    disagreements = compact["material_disagreements"]
    if isinstance(disagreements, list) and disagreements:
        lines += ["", "## Material disagreements"]
        lines += [f"- {item}" for item in disagreements]
    uncertainties = compact["uncertainties"]
    if isinstance(uncertainties, list) and uncertainties:
        lines += ["", "## Uncertainties"]
        lines += [f"- {item}" for item in uncertainties]
    lines += [
        "",
        f"**Confidence:** {compact['confidence']}",
        f"**Models used:** {', '.join(result.models_used)}",
        f"**Providers used:** {', '.join(result.providers_used)}",
        f"**Stop reason:** {compact['stop_reason']}",
    ]
    if result.quota_failures:
        lines += ["", "## Providers not used"]
        lines += [f"- {qf.provider}: {qf.reason}" for qf in result.quota_failures]
    if report_path:
        lines += ["", f"_Full report: {report_path}_"]
    return "\n".join(lines)


def _fail_insufficient(exc: InsufficientFreeModelsError) -> None:
    print(f"Council could not run: {exc}", file=sys.stderr)
    if exc.reasons:
        print("\nProvider status:", file=sys.stderr)
        for reason in exc.reasons:
            print(f"  - {reason}", file=sys.stderr)
    raise SystemExit(2)
