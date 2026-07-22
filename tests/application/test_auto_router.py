"""Unit tests for optional dynamic model routing (MODEL_ROUTING_MODE=auto)."""

import json
from collections.abc import AsyncIterator

import pytest

from free_claude_code.application.auto_router import (
    choose_auto_model,
    extract_prompt_context,
)
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic.models import Message, MessagesRequest


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
            raise ValueError("classifier provider misconfigured")
        self.preflight_calls.append(request)

    async def stream_response(
        self,
        request: MessagesRequest,
        *,
        input_tokens: int,
        request_id: str,
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        self.stream_calls.append(request)
        yield self._reply


def _settings(**overrides: object) -> Settings:
    # Aliased fields (validation_alias=MODEL_SONNET, etc.) only bind to their
    # alias at construction time; set them by attribute after, matching the
    # convention already used across tests/application/test_routing.py.
    base = Settings()
    base.model = "lmstudio/model-a"
    base.model_sonnet = "llamacpp/model-b"
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
        lambda _provider_id: FakeProvider(),
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_invalid_classifier_ref():
    settings = _settings(model_classifier="not-a-valid-ref")

    chosen = await choose_auto_model(
        settings,
        lambda _provider_id: FakeProvider(),
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_unsupported_classifier_provider():
    settings = _settings(model_classifier="not_a_real_provider/some-model")

    chosen = await choose_auto_model(
        settings,
        lambda _provider_id: FakeProvider(),
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_without_any_configured_candidate():
    settings = Settings()
    # open_router requires OPENROUTER_API_KEY; leaving it unset means the only
    # configured candidate has no usable credential.
    settings.model = "open_router/some-model"
    settings.model_classifier = "ollama/classifier-model"
    settings.open_router_api_key = ""

    chosen = await choose_auto_model(
        settings,
        lambda _provider_id: FakeProvider(),
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_picks_the_classifiers_answer():
    settings = _settings()
    providers: dict[str, FakeProvider] = {
        "ollama": FakeProvider(reply=_sse_text_response("llamacpp/model-b")),
    }

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="please write a python function",
        request_id="req-1",
    )

    assert chosen == "llamacpp/model-b"
    assert len(providers["ollama"].preflight_calls) == 1
    assert len(providers["ollama"].stream_calls) == 1


@pytest.mark.asyncio
async def test_choose_auto_model_ignores_surrounding_text_and_backticks():
    settings = _settings()
    reply = _sse_text_response("Sure, I'll pick:\n`lmstudio/model-a`\n")
    providers = {"ollama": FakeProvider(reply=reply)}

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen == "lmstudio/model-a"


@pytest.mark.asyncio
async def test_choose_auto_model_accepts_ref_echoed_with_menu_formatting():
    settings = _settings()
    reply = _sse_text_response(
        "lmstudio/model-a | budget_class=high_throughput (30 rpm) "
        "| capability~=0.83 | reasoning=no | coder=no | note=n/a"
    )
    providers = {"ollama": FakeProvider(reply=reply)}

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen == "lmstudio/model-a"


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_for_unparsable_answer():
    settings = _settings()
    providers = {"ollama": FakeProvider(reply=_sse_text_response("groq/not-in-menu"))}

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_on_provider_error_event():
    settings = _settings()
    providers = {"ollama": FakeProvider(reply=_sse_error_response("rate limited"))}

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


@pytest.mark.asyncio
async def test_choose_auto_model_returns_none_when_preflight_raises():
    settings = _settings()
    providers = {"ollama": FakeProvider(raise_on_preflight=True)}

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: providers[provider_id],
        prompt_context="hi",
        request_id="req-1",
    )

    assert chosen is None


def test_extract_prompt_context_uses_latest_user_text():
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=10,
        messages=[
            Message(role="user", content="first question"),
            Message(role="assistant", content="an answer"),
            Message(role="user", content="second, more relevant question"),
        ],
    )

    assert extract_prompt_context(request) == "second, more relevant question"


def test_extract_prompt_context_handles_text_blocks():
    from free_claude_code.core.anthropic.models import ContentBlockText

    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=10,
        messages=[
            Message(
                role="user",
                content=[ContentBlockText(type="text", text="hello there")],
            )
        ],
    )

    assert extract_prompt_context(request) == "hello there"


def test_extract_prompt_context_empty_without_user_text():
    request = MessagesRequest(
        model="claude-sonnet-4-5",
        max_tokens=10,
        messages=[Message(role="assistant", content="an answer")],
    )

    assert extract_prompt_context(request) == ""


@pytest.mark.asyncio
async def test_menu_marks_open_router_paid_slugs_as_paid():
    settings = _settings()
    settings.model_opus = "open_router/moonshotai/kimi-k2.5"
    settings.open_router_api_key = "sk-or-test"
    provider = FakeProvider(
        reply=_sse_text_response("open_router/moonshotai/kimi-k2.5")
    )

    chosen = await choose_auto_model(
        settings,
        lambda provider_id: provider,
        prompt_context="design a database schema",
        request_id="req-menu",
    )

    assert chosen == "open_router/moonshotai/kimi-k2.5"
    system_prompt = provider.stream_calls[0].system
    assert isinstance(system_prompt, str)
    assert "open_router/moonshotai/kimi-k2.5 | budget_class=paid" in system_prompt
    assert "budget_class=paid is cheap pay-per-token" in system_prompt
