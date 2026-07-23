"""Unit tests for pre-commit model fallback (C2)."""

from datetime import UTC, datetime

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
from free_claude_code.core.quota import DailyExhaustionStore, QuotaTracker


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


async def _run_fallback_with_failure(tmp_path, failure_message: str) -> set[str]:
    """Fail the primary with ``failure_message``; return today's exhausted keys."""
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
    exhaustion = DailyExhaustionStore(tmp_path / "quota.db")

    async def open_stream(routed: RoutedMessagesRequest):
        if "llama" in routed.resolved.provider_model_ref:
            raise RuntimeError(failure_message)
        yield "event: message_start\ndata: {}\n\n"

    async for _ in stream_with_precommit_fallback(
        template=template,
        candidates=["groq/llama-3.3-70b-versatile", "cerebras/gpt-oss-120b"],
        router=router,
        open_stream=open_stream,
        exhaustion=exhaustion,
        request_id="test",
    ):
        pass
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    exhausted = exhaustion.exhausted_keys(day)
    exhaustion.close()
    return exhausted


@pytest.mark.asyncio
async def test_transient_rate_limit_does_not_exhaust_model_for_the_day(tmp_path):
    exhausted = await _run_fallback_with_failure(tmp_path, "429 rate limit exceeded")
    assert exhausted == set()


@pytest.mark.asyncio
async def test_quota_exhaustion_is_recorded_for_the_day(tmp_path):
    exhausted = await _run_fallback_with_failure(
        tmp_path, "429 rate limit: daily quota exhausted"
    )
    assert exhausted == {"groq/llama-3.3-70b-versatile"}


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


@pytest.mark.asyncio
async def test_precommit_fallback_skips_candidate_too_small_for_context():
    settings = Settings(
        model="groq/llama-3.3-70b-versatile",
        model_fallbacks="gemini/gemini-flash-latest",
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
        calls.append(routed.resolved.provider_model_ref)
        yield "event: message_start\ndata: {}\n\n"

    chunks = [
        chunk
        async for chunk in stream_with_precommit_fallback(
            template=template,
            candidates=[
                "groq/llama-3.3-70b-versatile",
                "gemini/gemini-flash-latest",
            ],
            router=router,
            open_stream=open_stream,
            request_id="test",
            input_tokens=140_000,
        )
    ]

    assert chunks == ["event: message_start\ndata: {}\n\n"]
    assert calls == ["gemini/gemini-flash-latest"]


@pytest.mark.asyncio
async def test_precommit_fallback_keeps_candidate_when_input_tokens_unknown():
    settings = Settings(model="groq/llama-3.3-70b-versatile")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    calls: list[str] = []

    async def open_stream(routed: RoutedMessagesRequest):
        calls.append(routed.resolved.provider_model_ref)
        yield "event: message_start\ndata: {}\n\n"

    chunks = [
        chunk
        async for chunk in stream_with_precommit_fallback(
            template=template,
            candidates=["groq/llama-3.3-70b-versatile"],
            router=router,
            open_stream=open_stream,
            request_id="test",
        )
    ]

    assert chunks == ["event: message_start\ndata: {}\n\n"]
    assert calls == ["groq/llama-3.3-70b-versatile"]


@pytest.mark.asyncio
async def test_precommit_fallback_keeps_candidate_with_unknown_window():
    settings = Settings(model="groq/somebrand-newmodel-9000")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    calls: list[str] = []

    async def open_stream(routed: RoutedMessagesRequest):
        calls.append(routed.resolved.provider_model_ref)
        yield "event: message_start\ndata: {}\n\n"

    chunks = [
        chunk
        async for chunk in stream_with_precommit_fallback(
            template=template,
            candidates=["groq/somebrand-newmodel-9000"],
            router=router,
            open_stream=open_stream,
            request_id="test",
            input_tokens=140_000,
        )
    ]

    assert chunks == ["event: message_start\ndata: {}\n\n"]
    assert calls == ["groq/somebrand-newmodel-9000"]


@pytest.mark.asyncio
async def test_precommit_fallback_ignores_provider_prefix_for_context_window():
    """``gemini/gemma-...`` must not inherit gemini's 1M window from the prefix."""
    settings = Settings(model="gemini/gemma-3-27b-it")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    calls: list[str] = []

    async def open_stream(routed: RoutedMessagesRequest):
        calls.append(routed.resolved.provider_model_ref)
        yield "event: message_start\ndata: {}\n\n"

    chunks = [
        chunk
        async for chunk in stream_with_precommit_fallback(
            template=template,
            candidates=["gemini/gemma-3-27b-it"],
            router=router,
            open_stream=open_stream,
            request_id="test",
            input_tokens=140_000,
        )
    ]

    # Unknown window -> the candidate is attempted, never silently skipped.
    assert chunks == ["event: message_start\ndata: {}\n\n"]
    assert calls == ["gemini/gemma-3-27b-it"]


@pytest.mark.asyncio
async def test_precommit_fallback_errors_when_all_windows_too_small():
    settings = Settings(model="groq/llama-3.3-70b-versatile")
    router = ModelRouter(settings)
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[Message(role="user", content="hi")],
    )
    template = router.resolve_messages_request(request)

    async def open_stream(routed: RoutedMessagesRequest):
        raise AssertionError("no candidate should be opened")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="context window"):
        async for _ in stream_with_precommit_fallback(
            template=template,
            candidates=[
                "groq/llama-3.3-70b-versatile",
                "cerebras/gpt-oss-120b",
            ],
            router=router,
            open_stream=open_stream,
            request_id="test",
            input_tokens=140_000,
        ):
            pass
