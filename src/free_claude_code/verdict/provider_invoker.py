"""Real :class:`ModelInvoker` backed by the existing provider stack.

Reuses ``create_provider`` -> ``stream_response`` -> the Anthropic SSE
aggregator, so no request translation is duplicated. Every outgoing payload is
passed through the configured privacy mode first, and provider instances are
cached and cleaned up together.
"""

import time
from typing import Any

from loguru import logger

from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic.models import Message, MessagesRequest
from free_claude_code.core.anthropic.sse_aggregation import (
    aggregate_anthropic_sse_to_message,
)
from free_claude_code.core.anthropic.tokens import get_token_count
from free_claude_code.core.quota import classify_failure
from free_claude_code.providers.base import BaseProvider
from free_claude_code.providers.runtime.factory import create_provider

from .invoker import InvocationResult
from .models import ModelRef, Privacy
from .redaction import apply_privacy


def _extract_text(message: dict[str, Any]) -> str:
    blocks = message.get("content")
    if not isinstance(blocks, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _usage(message: dict[str, Any]) -> tuple[int, int]:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return (
        int(input_tokens) if isinstance(input_tokens, int) else 0,
        int(output_tokens) if isinstance(output_tokens, int) else 0,
    )


class ProviderModelInvoker:
    """Bridges verdict model calls onto FCC providers, with a provider cache."""

    def __init__(self, settings: Settings, *, privacy: Privacy = Privacy.REDACTED):
        self._settings = settings
        # Public so the service can switch privacy mode per evaluate() call.
        self.privacy = privacy
        self._providers: dict[str, BaseProvider] = {}

    def _provider(self, provider_id: str) -> BaseProvider:
        cached = self._providers.get(provider_id)
        if cached is None:
            cached = create_provider(provider_id, self._settings)
            self._providers[provider_id] = cached
        return cached

    async def invoke(
        self,
        model: ModelRef,
        system: str,
        user: str,
        *,
        max_tokens: int,
        request_id: str,
    ) -> InvocationResult:
        safe_system = apply_privacy(system, self.privacy)
        safe_user = apply_privacy(user, self.privacy)
        request = MessagesRequest(
            model=model.model_id,
            max_tokens=max_tokens,
            system=safe_system,
            messages=[Message(role="user", content=safe_user)],
            stream=False,
        )
        provider = self._provider(model.provider)
        started = time.monotonic()
        try:
            provider.preflight_stream(request)
            input_tokens = get_token_count(
                request.messages, request.system, request.tools
            )
            stream = provider.stream_response(
                request, input_tokens=input_tokens, request_id=request_id
            )
            message, error = await aggregate_anthropic_sse_to_message(stream)
        except Exception as exc:
            elapsed = time.monotonic() - started
            logger.warning("verdict.provider.error model={} err={}", model.key, exc)
            return InvocationResult.failure(
                model.key, classify_failure(exc), detail=str(exc), latency_s=elapsed
            )

        elapsed = time.monotonic() - started
        if error is not None:
            return InvocationResult.failure(
                model.key,
                classify_failure(error),
                detail=str(error.get("message", "provider error")),
                latency_s=elapsed,
            )
        text = _extract_text(message)
        if not text.strip():
            return InvocationResult.failure(
                model.key,
                classify_failure({"type": "provider_failure", "message": "empty"}),
                detail="empty response",
                latency_s=elapsed,
            )
        in_tokens, out_tokens = _usage(message)
        return InvocationResult.success(
            model.key,
            text,
            latency_s=elapsed,
            input_tokens=in_tokens or input_tokens,
            output_tokens=out_tokens,
        )

    async def list_models(self, provider_id: str) -> list[str]:
        """List a provider's raw model ids (used by discovery)."""
        provider = self._provider(provider_id)
        return sorted(await provider.list_model_ids())

    async def cleanup(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.cleanup()
            except Exception as exc:
                logger.debug("verdict.provider.cleanup error: {}", exc)
        self._providers.clear()
