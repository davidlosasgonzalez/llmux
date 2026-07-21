"""Unit tests for pre-commit model fallback (C2)."""

import pytest

from free_claude_code.application.fallback import (
    fallback_candidates,
    route_for_model,
    stream_with_precommit_fallback,
)
from free_claude_code.application.routing import ModelRouter, RoutedMessagesRequest
from free_claude_code.config.model_refs import parse_model_fallbacks
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic import Message, MessagesRequest
from free_claude_code.core.quota import QuotaTracker


def test_parse_and_candidates():
    assert parse_model_fallbacks(" a , b, ,c ") == ["a", "b", "c"]
    assert fallback_candidates("a", ["b", "a", "c"]) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_precommit_fallback_skips_failing_primary():
    settings = Settings(
        model="groq/llama-3.3-70b-versatile",
        model_fallbacks="cerebras/gpt-oss-120b",
    )
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    calls: list[str] = []

    async def open_stream(routed: RoutedMessagesRequest):
        ref = routed.resolved.provider_model_ref
        calls.append(ref)
        if "llama" in ref:

            async def fail():
                raise RuntimeError("429 rate limit exceeded")
                yield  # pragma: no cover

            async for item in fail():
                yield item
            return

        async def ok():
            yield "event: message_start\ndata: {}\n\n"

        async for item in ok():
            yield item

    chunks = [
        chunk
        async for chunk in stream_with_precommit_fallback(
            template=template,
            candidates=fallback_candidates(
                template.resolved.provider_model_ref,
                parse_model_fallbacks(settings.model_fallbacks),
            ),
            router=router,
            open_stream=open_stream,
            quota=QuotaTracker(),
            request_id="test",
        )
    ]
    assert chunks == ["event: message_start\ndata: {}\n\n"]
    assert any("llama" in c for c in calls)
    assert any("gpt-oss" in c for c in calls)


@pytest.mark.asyncio
async def test_precommit_fallback_never_switches_after_commit():
    settings = Settings(model="groq/llama-3.3-70b-versatile")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    async def open_stream(routed: RoutedMessagesRequest):
        async def gen():
            yield "event: message_start\ndata: {}\n\n"
            raise RuntimeError("upstream mid-stream boom")

        async for item in gen():
            yield item

    with pytest.raises(RuntimeError, match="mid-stream"):
        async for _ in stream_with_precommit_fallback(
            template=template,
            candidates=["groq/llama-3.3-70b-versatile", "cerebras/gpt-oss-120b"],
            router=router,
            open_stream=open_stream,
            request_id="test",
        ):
            pass


def test_route_for_model_rewrites_provider():
    settings = Settings(model="groq/llama-3.3-70b-versatile")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=16,
        messages=[Message(role="user", content="x")],
    )
    template = router.resolve_messages_request(request)
    routed = route_for_model(router, template, "cerebras/gpt-oss-120b")
    assert routed.resolved.provider_id == "cerebras"
    assert routed.request.model == "gpt-oss-120b"
