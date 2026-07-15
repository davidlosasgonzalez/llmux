"""Local MCP server exposing the council over stdio.

Tools: ``council_evaluate``, ``council_models``, ``council_status`` and
``council_validate_providers``. The server binds to stdio only — it is never
exposed on a network socket. The ``mcp`` dependency is imported lazily so the
base package does not require it.
"""

from typing import Any

from .config import load_config
from .errors import InsufficientFreeModelsError
from .service import CouncilService


async def _evaluate(
    prompt: str,
    task_type: str,
    depth: str | None,
    files: list[str] | None,
    privacy: str | None,
    max_rounds: int | None,
) -> dict[str, Any]:
    service = CouncilService.create(config=load_config())
    try:
        result, report_path = await service.evaluate(
            prompt,
            task_type=task_type,
            depth=depth,
            files=files or [],
            privacy=privacy,
            max_rounds=max_rounds,
        )
    except InsufficientFreeModelsError as exc:
        return {
            "error": "insufficient_free_models",
            "message": str(exc),
            "quota_failures": exc.reasons,
        }
    finally:
        await service.cleanup()
    return {"report_path": str(report_path), **result.compact()}


async def _models() -> dict[str, Any]:
    service = CouncilService.create(config=load_config())
    try:
        models = await service.list_free_models()
    finally:
        await service.cleanup()
    return {
        "models": [
            {
                "provider": m.provider,
                "model_id": m.model_id,
                "family": m.family,
                "cost_status": m.cost_status.value,
            }
            for m in models
        ]
    }


async def _validate() -> dict[str, Any]:
    service = CouncilService.create(config=load_config())
    try:
        rows = await service.validate_providers()
    finally:
        await service.cleanup()
    return {
        "providers": [
            {
                "provider": r.provider,
                "authenticated": r.authenticated,
                "free_status": r.free_status,
                "usable": r.usable,
            }
            for r in rows
        ]
    }


async def _usage(day: str | None) -> dict[str, Any]:
    service = CouncilService.create(config=load_config())
    try:
        summaries, rows = service.usage(day=day)
    finally:
        await service.cleanup()
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
                "requests": r.requests,
                "total_tokens": r.total_tokens,
            }
            for r in rows
        ],
    }


async def _status() -> dict[str, Any]:
    config = load_config()
    return {
        "allow_paid_models": config.allow_paid_models,
        "depth": config.depth.value,
        "max_rounds": config.max_rounds,
        "quality_threshold": config.quality_threshold,
        "privacy": config.privacy.value,
        "enabled_providers": config.enabled_providers,
        "minimum_models": config.minimum_models,
    }


def build_server() -> Any:
    """Construct the FastMCP server with all council tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "The 'mcp' package is required for the MCP server. Install the extra:"
            " uv pip install 'free-claude-code[council]'"
        ) from exc

    server = FastMCP("fcc-council")

    @server.tool()
    async def council_evaluate(
        prompt: str,
        task_type: str = "auto",
        depth: str | None = "deep",
        files: list[str] | None = None,
        privacy: str | None = "redacted",
        max_rounds: int | None = 3,
    ) -> dict[str, Any]:
        """Run a free-only multi-model deliberation and return a compact result."""
        return await _evaluate(prompt, task_type, depth, files, privacy, max_rounds)

    @server.tool()
    async def council_models() -> dict[str, Any]:
        """List the free-eligible models the council can currently use."""
        return await _models()

    @server.tool()
    async def council_validate_providers() -> dict[str, Any]:
        """Report provider authentication and free-access status (no keys)."""
        return await _validate()

    @server.tool()
    async def council_status() -> dict[str, Any]:
        """Return the current council configuration summary."""
        return await _status()

    @server.tool()
    async def council_usage(day: str | None = None) -> dict[str, Any]:
        """Approximate token/request usage per provider vs free limits (a day)."""
        return await _usage(day)

    return server


def run_stdio() -> None:
    """Start the MCP server over stdio."""
    build_server().run()
