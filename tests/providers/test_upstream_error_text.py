"""Tests for upstream error text served as a successful completion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from llmux.core.anthropic.stream_contracts import (
    parse_sse_text,
    text_content,
)
from llmux.core.failures import ExecutionFailure, FailureKind
from llmux.providers.base import ProviderConfig
from llmux.providers.open_router import OpenRouterProvider
from llmux.providers.upstream_error_text import (
    UPSTREAM_ERROR_COMPLETIONS,
    UpstreamErrorTextGuard,
)
from tests.providers.request_factory import make_messages_request
from tests.providers.support import passthrough_rate_limiter

_ERROR_TEXT = "Connect timeout, please try again later."


class AsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def _chunk(*, content: str | None = None, finish_reason: str | None = None):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def make_request(**overrides):
    return make_messages_request("deepseek/deepseek-v4-flash", **overrides)


@pytest.fixture
def provider():
    return OpenRouterProvider(
        ProviderConfig(
            api_key="test_openrouter_key",
            base_url="https://openrouter.ai/api/v1",
            rate_limit=10,
            rate_window=60,
        ),
        rate_limiter=passthrough_rate_limiter(),
    )


def test_guard_holds_error_prefix_and_matches_full_body():
    guard = UpstreamErrorTextGuard()

    assert guard.feed("Connect timeout, ") == ""
    assert guard.feed("please try again later.") == ""
    assert guard.matched() == _ERROR_TEXT


def test_guard_tolerates_surrounding_whitespace():
    guard = UpstreamErrorTextGuard()

    assert guard.feed(f"{_ERROR_TEXT}\n") == ""
    assert guard.matched() == _ERROR_TEXT


def test_guard_releases_diverging_text_unchanged():
    guard = UpstreamErrorTextGuard()

    assert guard.feed("Connect ") == ""
    assert guard.feed("me to the server.") == "Connect me to the server."
    assert guard.matched() is None
    assert guard.feed("More text.") == "More text."


def test_guard_passes_non_matching_text_through_immediately():
    guard = UpstreamErrorTextGuard()

    assert guard.feed("The upstream said: Connect timeout") == (
        "The upstream said: Connect timeout"
    )
    assert guard.matched() is None


def test_guard_disarm_returns_held_text_once():
    guard = UpstreamErrorTextGuard()

    assert guard.feed("Connect timeout") == ""
    assert guard.disarm() == "Connect timeout"
    assert guard.disarm() == ""
    assert guard.matched() is None


def test_known_error_completions_are_full_sentences():
    for pattern in UPSTREAM_ERROR_COMPLETIONS:
        assert pattern == pattern.strip()
        assert len(pattern) > 20


@pytest.mark.asyncio
async def test_stream_raises_on_upstream_error_completion(provider):
    stream = AsyncStream(
        [
            _chunk(content="Connect timeout, "),
            _chunk(content="please try again later.", finish_reason="stop"),
        ]
    )
    events: list[str] = []
    with (
        patch.object(
            provider._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=stream,
        ),
        pytest.raises(ExecutionFailure) as excinfo,
    ):
        stream_iter = provider.stream_response(make_request())
        while True:
            events.append(await anext(stream_iter))

    assert excinfo.value.kind == FailureKind.UPSTREAM
    assert excinfo.value.status_code == 502
    assert excinfo.value.retryable is False
    assert _ERROR_TEXT in excinfo.value.message
    assert _ERROR_TEXT not in "".join(events)


@pytest.mark.asyncio
async def test_stream_emits_text_that_diverges_from_error_body(provider):
    stream = AsyncStream(
        [
            _chunk(content="Connect "),
            _chunk(content="four data sources first.", finish_reason="stop"),
        ]
    )
    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=stream,
    ):
        events = [event async for event in provider.stream_response(make_request())]

    text = text_content(parse_sse_text("".join(events)))
    assert "Connect four data sources first." in text


@pytest.mark.asyncio
async def test_stream_emits_answer_that_mentions_the_error_sentence(provider):
    answer = f"The upstream said: {_ERROR_TEXT}"
    stream = AsyncStream([_chunk(content=answer, finish_reason="stop")])
    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=stream,
    ):
        events = [event async for event in provider.stream_response(make_request())]

    assert answer in text_content(parse_sse_text("".join(events)))


@pytest.mark.asyncio
async def test_stream_releases_held_error_prefix_at_finish(provider):
    stream = AsyncStream([_chunk(content="Connect timeout", finish_reason="stop")])
    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=stream,
    ):
        events = [event async for event in provider.stream_response(make_request())]

    assert "Connect timeout" in text_content(parse_sse_text("".join(events)))
