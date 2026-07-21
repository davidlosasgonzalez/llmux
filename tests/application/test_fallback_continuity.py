"""Smoke / unit drill for pre-commit fallback continuity (C7)."""

import pytest

from free_claude_code.application.fallback import (
    fallback_candidates,
    stream_with_precommit_fallback,
)
from free_claude_code.application.routing import ModelRouter, RoutedMessagesRequest
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic import Message, MessagesRequest


@pytest.mark.asyncio
async def test_continuity_primary_429_secondary_serves():
    settings = Settings(
        model="groq/llama-3.3-70b-versatile",
        model_fallbacks="cerebras/gpt-oss-120b",
    )
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=32,
        messages=[Message(role="user", content="ping")],
    )
    template = router.resolve_messages_request(request)
    served: list[str] = []

    async def open_stream(routed: RoutedMessagesRequest):
        ref = routed.resolved.provider_model_ref
        if "llama" in ref:

            async def fail():
                raise RuntimeError("HTTP 429 rate limit")
                yield  # pragma: no cover

            async for item in fail():
                yield item
            return

        served.append(ref)

        async def ok():
            yield "data: secondary\n\n"

        async for item in ok():
            yield item

    out = [
        c
        async for c in stream_with_precommit_fallback(
            template=template,
            candidates=fallback_candidates(
                template.resolved.provider_model_ref,
                ["cerebras/gpt-oss-120b"],
            ),
            router=router,
            open_stream=open_stream,
            request_id="c7",
        )
    ]
    assert out == ["data: secondary\n\n"]
    assert served and "gpt-oss" in served[0]
