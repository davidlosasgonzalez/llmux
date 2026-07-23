"""Application-owned provider execution contracts."""

from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import MagicMock

import pytest

from llmux.application.execution import ProviderExecutor
from llmux.application.routing import ModelRouter, ResolvedModel, RoutedMessagesRequest
from llmux.config.settings import Settings
from llmux.core.anthropic.models import Message, MessagesRequest
from llmux.core.async_iterators import AsyncCloseable


class FakeProvider:
    def __init__(self) -> None:
        self.preflight_calls: list[tuple[MessagesRequest, bool]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.stream_close_calls = 0

    def preflight_stream(
        self,
        request: MessagesRequest,
        *,
        thinking_enabled: bool,
    ) -> None:
        self.preflight_calls.append((request, thinking_enabled))

    async def stream_response(
        self,
        request: MessagesRequest,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        self.stream_calls.append(
            {
                "request": request,
                "input_tokens": input_tokens,
                "request_id": request_id,
                "thinking_enabled": thinking_enabled,
            }
        )
        try:
            yield "event: message_stop\ndata: {}\n\n"
        finally:
            self.stream_close_calls += 1


class FailingPreflightProvider(FakeProvider):
    def preflight_stream(
        self,
        request: MessagesRequest,
        *,
        thinking_enabled: bool,
    ) -> None:
        raise ValueError("invalid provider request")


class FailingStreamConstructionProvider(FakeProvider):
    def stream_response(
        self,
        request: MessagesRequest,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        raise RuntimeError("stream construction failed")


def _routed_request() -> RoutedMessagesRequest:
    request = MessagesRequest(
        model="provider-model",
        messages=[Message(role="user", content="hello")],
    )
    return RoutedMessagesRequest(
        request=request,
        resolved=ResolvedModel(
            original_model="gateway-model",
            provider_id="provider",
            provider_model="provider-model",
            provider_model_ref="provider/provider-model",
            thinking_enabled=True,
        ),
    )


@pytest.mark.asyncio
async def test_executor_uses_structural_provider_port_and_preflights_eagerly() -> None:
    provider = FakeProvider()
    routed = _routed_request()
    request = routed.request
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 17,
    )

    stream = executor.stream(
        routed,
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload=request.model_dump(),
        request_id="req_application",
    )

    assert provider.preflight_calls == [(request, True)]
    assert [chunk async for chunk in stream] == ["event: message_stop\ndata: {}\n\n"]
    assert provider.stream_calls == [
        {
            "request": request,
            "input_tokens": 17,
            "request_id": "req_application",
            "thinking_enabled": True,
        }
    ]
    assert provider.stream_close_calls == 1


@pytest.mark.asyncio
async def test_closing_executor_stream_closes_provider_stream_once() -> None:
    provider = FakeProvider()
    routed = _routed_request()
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 17,
    )
    stream = executor.stream(
        routed,
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload={},
        request_id="req_early_close",
    )

    assert await anext(stream) == "event: message_stop\ndata: {}\n\n"
    assert isinstance(stream, AsyncCloseable)
    await stream.aclose()

    assert provider.stream_close_calls == 1


@pytest.mark.asyncio
async def test_stream_construction_failure_remains_deferred_to_iteration() -> None:
    provider = FailingStreamConstructionProvider()
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 17,
    )

    stream = executor.stream(
        _routed_request(),
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload={},
        request_id="req_deferred_construction",
    )

    with pytest.raises(RuntimeError, match="stream construction failed"):
        await anext(stream)


def _routed_via_router(router: ModelRouter) -> RoutedMessagesRequest:
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="hi")],
    )
    return router.resolve_messages_request(request)


@pytest.mark.asyncio
async def test_long_context_model_rescues_oversized_prompt() -> None:
    settings = Settings(
        model="groq/llama-3.3-70b-versatile",
        model_fallbacks="cerebras/gpt-oss-120b",
    )
    router = ModelRouter(settings)
    provider = FakeProvider()
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 140_000,
        model_router=router,
        model_fallbacks=settings.model_fallbacks,
        long_context_model="gemini/gemini-flash-latest",
    )

    stream = executor.stream(
        _routed_via_router(router),
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload={},
        request_id="req_long_context",
    )

    assert [chunk async for chunk in stream] == ["event: message_stop\ndata: {}\n\n"]
    assert len(provider.stream_calls) == 1
    served = cast(MessagesRequest, provider.stream_calls[0]["request"])
    assert served.model == "gemini-flash-latest"


@pytest.mark.asyncio
async def test_long_context_model_deduplicated_when_already_a_fallback() -> None:
    settings = Settings(
        model="groq/llama-3.3-70b-versatile",
        model_fallbacks="gemini/gemini-flash-latest",
    )
    router = ModelRouter(settings)
    provider = FakeProvider()
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 140_000,
        model_router=router,
        model_fallbacks=settings.model_fallbacks,
        long_context_model="gemini/gemini-flash-latest",
    )

    stream = executor.stream(
        _routed_via_router(router),
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload={},
        request_id="req_dedup",
    )

    assert [chunk async for chunk in stream] == ["event: message_stop\ndata: {}\n\n"]
    # The duplicate ref must not be attempted twice.
    assert len(provider.stream_calls) == 1


@pytest.mark.asyncio
async def test_long_context_model_unused_when_prompt_fits_primary() -> None:
    settings = Settings(model="groq/llama-3.3-70b-versatile")
    router = ModelRouter(settings)
    provider = FakeProvider()
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=lambda _messages, _system, _tools: 10,
        model_router=router,
        long_context_model="gemini/gemini-flash-latest",
    )

    stream = executor.stream(
        _routed_via_router(router),
        wire_api="messages",
        raw_log_label="FULL_PAYLOAD",
        raw_log_payload={},
        request_id="req_small",
    )

    assert [chunk async for chunk in stream] == ["event: message_stop\ndata: {}\n\n"]
    assert len(provider.stream_calls) == 1
    served = cast(MessagesRequest, provider.stream_calls[0]["request"])
    assert served.model == "llama-3.3-70b-versatile"


def test_executor_preflight_failure_stays_before_token_count_and_stream() -> None:
    provider = FailingPreflightProvider()
    token_counter = MagicMock(return_value=17)
    executor = ProviderExecutor(
        lambda _provider_id: provider,
        token_counter=token_counter,
    )

    with pytest.raises(ValueError, match="invalid provider request"):
        executor.stream(
            _routed_request(),
            wire_api="messages",
            raw_log_label="FULL_PAYLOAD",
            raw_log_payload={},
            request_id="req_application",
        )

    token_counter.assert_not_called()
    assert provider.stream_calls == []
