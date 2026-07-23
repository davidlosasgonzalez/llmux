"""Unit tests for optional dynamic model routing (MODEL_ROUTING_MODE=auto)."""

import json
from collections.abc import AsyncIterator

import pytest

from llmux.application.auto_router import (
    choose_auto_model,
    extract_prompt_context,
)
from llmux.config.settings import Settings
from llmux.core.anthropic.models import Message, MessagesRequest


def _sse_text_response(text: str) -> str:
    events = (
        ("message_start", {"type": "message_start", "message": {"role": "assistant"}}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_stop", {"type": "message_stop"}),
    )
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    )


def _sse_error_response(message: str) -> str:
    payload = {"type": "error", "error": {"type": "api_error", "message": message}}
    return f"event: error\ndata: {json.dumps(payload)}\n\n"


class FakeProvider:
    """Structural ``ProviderPort`` double that returns a canned SSE reply."""

    def __init__(self, *, reply: str = "", raise_on_preflight: bool = False) -> None:
        self._reply = reply
        self._raise_on_preflight = raise_on_preflight
        self.preflight_calls: list[MessagesRequest] = []
        self.stream_calls: list[MessagesRequest] = []

    def preflight_stream(
        self, request: MessagesRequest, *, thinking_enabled: bool
    ) -> None:
        if self._raise_on_preflight:
            raise RuntimeError("preflight rejected")
        self.preflight_calls.append(request)

    def stream_response(
        self,
        request: MessagesRequest,
        *,
        input_tokens: int,
        request_id: str,
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        self.stream_calls.append(request)
        reply = self._reply

        async def _stream() -> AsyncIterator[str]:
            yield reply

        return _stream()


def _settings(**overrides: object) -> Settings:
    base = Settings()
    base.model = "lmstudio/model-base"
    base.model_haiku = "lmstudio/model-fast"
    base.model_sonnet = "llamacpp/model-coder"
    base.model_opus = "ollama/model-strong"
    base.model_classifier = "ollama/classifier-model"
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _request(text: str = "please refactor this function") -> MessagesRequest:
    return MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[Message(role="user", content=text)],
    )


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_without_classifier():
    settings = _settings(model_classifier=None)

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: FakeProvider(),
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_invalid_classifier_ref():
    settings = _settings(model_classifier="not-a-ref")

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: FakeProvider(),
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_unsupported_classifier_provider():
    settings = _settings(model_classifier="nonexistent_provider/model")

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: FakeProvider(),
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_without_any_configured_candidate():
    # open_router requires OPENROUTER_API_KEY; leaving it unset means the only
    # configured chat models are unreachable, so there is nothing to map to.
    settings = _settings(
        model="open_router/some-model",
        model_haiku=None,
        model_sonnet=None,
        model_opus=None,
        model_fable=None,
    )
    settings.open_router_api_key = ""

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: FakeProvider(),
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier_reply", "expected_ref"),
    [
        ("trivial", "lmstudio/model-fast"),
        ("standard", "llamacpp/model-coder"),
        ("complex", "ollama/model-strong"),
    ],
)
async def test_choose_auto_model_maps_each_tier_to_its_configured_model(
    tier_reply: str, expected_ref: str
):
    settings = _settings()
    provider = FakeProvider(reply=_sse_text_response(tier_reply))

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="please write a python function",
        request_id="req-1",
    )

    assert chosen == expected_ref
    assert len(provider.preflight_calls) == 1
    assert len(provider.stream_calls) == 1


@pytest.mark.asyncio
async def test_choose_auto_model_ignores_surrounding_text_and_backticks():
    settings = _settings()
    reply = _sse_text_response("Sure — I grade this as:\n`standard`\n")
    provider = FakeProvider(reply=reply)

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="fix this bug",
        request_id="req-1",
    )

    assert chosen == "llamacpp/model-coder"


@pytest.mark.asyncio
async def test_unconfigured_tiers_fall_back_along_the_tier_chain():
    settings = _settings(model_haiku=None, model_opus=None, model_fable=None)
    trivial_provider = FakeProvider(reply=_sse_text_response("trivial"))
    complex_provider = FakeProvider(reply=_sse_text_response("complex"))

    # trivial: no MODEL_HAIKU -> falls back to MODEL_SONNET
    assert (
        await choose_auto_model(
            settings,
            lambda provider_id: trivial_provider,
            prompt_context="hi",
            request_id="req-1",
        )
        == "llamacpp/model-coder"
    )
    # complex: no MODEL_OPUS/MODEL_FABLE -> falls back to MODEL
    assert (
        await choose_auto_model(
            settings,
            lambda provider_id: complex_provider,
            prompt_context="design a system",
            request_id="req-2",
        )
        == "lmstudio/model-base"
    )


@pytest.mark.asyncio
async def test_tier_mapping_skips_models_without_credentials():
    settings = _settings(model_opus="open_router/moonshotai/kimi-k2.5")
    settings.open_router_api_key = ""
    provider = FakeProvider(reply=_sse_text_response("complex"))

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="design a system",
        request_id="req-1",
    )

    # MODEL_OPUS is unreachable without a key -> complex falls back to MODEL.
    assert chosen == "lmstudio/model-base"


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_unparsable_answer():
    settings = _settings()
    provider = FakeProvider(reply=_sse_text_response("medium-ish, maybe?"))

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_on_provider_error_event():
    settings = _settings()
    provider = FakeProvider(reply=_sse_error_response("rate limited"))

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_when_preflight_raises():
    settings = _settings()
    provider = FakeProvider(raise_on_preflight=True)

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="hello",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_classifier_prompt_contains_tier_definitions_and_examples():
    settings = _settings()
    provider = FakeProvider(reply=_sse_text_response("standard"))

    await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="fix this bug",
        request_id="req-1",
    )

    system_prompt = provider.stream_calls[0].system
    assert isinstance(system_prompt, str)
    assert "trivial — greetings" in system_prompt
    assert "complex — deep multi-step reasoning" in system_prompt
    assert "-> trivial" in system_prompt
    assert "ONLY one of: trivial, standard, complex" in system_prompt


def test_extract_prompt_context_uses_latest_user_text():
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[
            Message(role="user", content="first question"),
            Message(role="assistant", content="answer"),
            Message(role="user", content="second question"),
        ],
    )

    assert extract_prompt_context(request) == "second question"


def test_extract_prompt_context_handles_text_blocks():
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[
            Message(
                role="user",
                content=[
                    {"type": "text", "text": "block one"},
                    {"type": "text", "text": "block two"},
                ],
            ),
        ],
    )

    context = extract_prompt_context(request)
    assert "block one" in context
    assert "block two" in context


def test_extract_prompt_context_empty_without_user_text():
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[Message(role="assistant", content="only assistant")],
    )

    assert extract_prompt_context(request) == ""
