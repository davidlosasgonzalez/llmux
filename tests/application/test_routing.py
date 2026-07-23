import json
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest

from llmux.application.errors import UnknownProviderError
from llmux.application.routing import ModelRouter
from llmux.config.provider_catalog import PROVIDER_CATALOG
from llmux.config.settings import Settings
from llmux.core.anthropic.models import (
    Message,
    MessagesRequest,
    TokenCountRequest,
)


@pytest.fixture
def settings():
    settings = Settings()
    settings.model = "nvidia_nim/fallback-model"
    settings.model_fable = None
    settings.model_opus = None
    settings.model_sonnet = None
    settings.model_haiku = None
    settings.enable_model_thinking = True
    settings.enable_fable_thinking = None
    settings.enable_opus_thinking = None
    settings.enable_sonnet_thinking = None
    settings.enable_haiku_thinking = None
    return settings


def test_model_router_resolves_default_model(settings):
    resolved = ModelRouter(settings).resolve("claude-3-opus")

    assert resolved.original_model == "claude-3-opus"
    assert resolved.provider_id == "nvidia_nim"
    assert resolved.provider_model == "fallback-model"
    assert resolved.provider_model_ref == "nvidia_nim/fallback-model"
    assert resolved.thinking_enabled is True


def test_model_router_applies_opus_override(settings):
    settings.model_opus = "open_router/deepseek/deepseek-r1"

    request = MessagesRequest(
        model="claude-opus-4-20250514",
        max_tokens=100,
        messages=[Message(role="user", content="hello")],
    )
    routed = ModelRouter(settings).resolve_messages_request(request)

    assert routed.request.model == "deepseek/deepseek-r1"
    assert routed.resolved.provider_model_ref == "open_router/deepseek/deepseek-r1"
    assert routed.resolved.original_model == "claude-opus-4-20250514"
    assert routed.resolved.thinking_enabled is True
    assert request.model == "claude-opus-4-20250514"


def test_model_router_applies_fable_override(settings):
    settings.model_fable = "open_router/anthropic/claude-fable-5"

    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="claude-fable-5",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "anthropic/claude-fable-5"
    assert routed.resolved.provider_model_ref == "open_router/anthropic/claude-fable-5"
    assert routed.resolved.original_model == "claude-fable-5"


def test_model_router_resolves_per_model_thinking(settings):
    settings.enable_model_thinking = False
    settings.enable_fable_thinking = True
    settings.enable_opus_thinking = True
    settings.enable_haiku_thinking = False

    router = ModelRouter(settings)

    assert router.resolve("claude-fable-5").thinking_enabled is True
    assert router.resolve("claude-opus-4-20250514").thinking_enabled is True
    assert router.resolve("claude-sonnet-4-20250514").thinking_enabled is False
    assert router.resolve("claude-3-haiku-20240307").thinking_enabled is False
    assert router.resolve("claude-2.1").thinking_enabled is False


def test_model_router_applies_haiku_override(settings):
    settings.model_haiku = "lmstudio/qwen2.5-7b"

    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "qwen2.5-7b"
    assert routed.resolved.provider_model_ref == "lmstudio/qwen2.5-7b"


def test_model_router_applies_sonnet_override(settings):
    settings.model_sonnet = "nvidia_nim/meta/llama-3.3-70b-instruct"

    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "meta/llama-3.3-70b-instruct"
    assert (
        routed.resolved.provider_model_ref == "nvidia_nim/meta/llama-3.3-70b-instruct"
    )


def test_model_router_routes_prefixed_provider_model_directly(settings):
    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="deepseek/deepseek-chat",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "deepseek-chat"
    assert routed.resolved.original_model == "deepseek/deepseek-chat"
    assert routed.resolved.provider_id == "deepseek"
    assert routed.resolved.provider_model == "deepseek-chat"
    assert routed.resolved.provider_model_ref == "deepseek/deepseek-chat"


def test_model_router_routes_wafer_provider_model_directly(settings):
    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="wafer/DeepSeek-V4-Pro",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "DeepSeek-V4-Pro"
    assert routed.resolved.provider_id == "wafer"
    assert routed.resolved.provider_model == "DeepSeek-V4-Pro"
    assert routed.resolved.provider_model_ref == "wafer/DeepSeek-V4-Pro"


def test_model_router_routes_minimax_provider_model_directly(settings):
    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="minimax/MiniMax-M3",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "MiniMax-M3"
    assert routed.resolved.provider_id == "minimax"
    assert routed.resolved.provider_model == "MiniMax-M3"
    assert routed.resolved.provider_model_ref == "minimax/MiniMax-M3"


def test_model_router_routes_gateway_encoded_provider_model_directly(settings):
    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="anthropic/nvidia_nim/deepseek-ai/deepseek-v4-pro",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "deepseek-ai/deepseek-v4-pro"
    assert (
        routed.resolved.original_model
        == "anthropic/nvidia_nim/deepseek-ai/deepseek-v4-pro"
    )
    assert routed.resolved.provider_id == "nvidia_nim"
    assert routed.resolved.provider_model == "deepseek-ai/deepseek-v4-pro"
    assert (
        routed.resolved.provider_model_ref
        == "anthropic/nvidia_nim/deepseek-ai/deepseek-v4-pro"
    )


def test_model_router_routes_no_thinking_gateway_model_directly(settings):
    settings.enable_model_thinking = True

    routed = ModelRouter(settings).resolve_messages_request(
        MessagesRequest(
            model="claude-3-freecc-no-thinking/nvidia_nim/deepseek-ai/deepseek-v4-pro",
            max_tokens=100,
            messages=[Message(role="user", content="hello")],
        )
    )

    assert routed.request.model == "deepseek-ai/deepseek-v4-pro"
    assert (
        routed.resolved.original_model
        == "claude-3-freecc-no-thinking/nvidia_nim/deepseek-ai/deepseek-v4-pro"
    )
    assert routed.resolved.provider_id == "nvidia_nim"
    assert routed.resolved.provider_model == "deepseek-ai/deepseek-v4-pro"
    assert routed.resolved.thinking_enabled is False


def test_model_router_direct_prefixed_model_uses_provider_model_for_thinking(settings):
    settings.enable_model_thinking = False
    settings.enable_opus_thinking = True

    resolved = ModelRouter(settings).resolve("open_router/anthropic/claude-opus-4")

    assert resolved.provider_id == "open_router"
    assert resolved.provider_model == "anthropic/claude-opus-4"
    assert resolved.thinking_enabled is True


def test_model_router_routes_token_count_request(settings):
    settings.model_haiku = "lmstudio/qwen2.5-7b"

    request = TokenCountRequest(
        model="claude-3-haiku-20240307",
        messages=[Message(role="user", content="hello")],
    )
    routed = ModelRouter(settings).resolve_token_count_request(request)

    assert routed.request.model == "qwen2.5-7b"
    assert request.model == "claude-3-haiku-20240307"


def test_model_router_logs_mapping(settings):
    with patch("llmux.application.routing.logger.debug") as mock_log:
        ModelRouter(settings).resolve("claude-2.1")

    mock_log.assert_called()
    args = mock_log.call_args[0]
    assert "MODEL MAPPING" in args[0]
    assert args[1] == "claude-2.1"
    assert args[2] == "fallback-model"


def test_model_router_preserves_typed_error_for_unknown_mapped_provider(settings):
    settings.model = "unknown/model"

    with pytest.raises(UnknownProviderError) as exc_info:
        ModelRouter(settings).resolve("claude-2.1")

    supported = "', '".join(PROVIDER_CATALOG)
    assert str(exc_info.value) == (
        f"Unknown provider_type: 'unknown'. Supported: '{supported}'"
    )


def _classifier_sse(text: str) -> str:
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
        ("message_stop", {"type": "message_stop"}),
    )
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    )


class _FakeClassifierProvider:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls = 0

    def preflight_stream(
        self, request: MessagesRequest, *, thinking_enabled: bool
    ) -> None:
        pass

    async def stream_response(
        self,
        request: MessagesRequest,
        *,
        input_tokens: int,
        request_id: str,
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        self.calls += 1
        yield self._reply


@pytest.mark.asyncio
async def test_aresolve_messages_request_uses_static_path_by_default(settings):
    settings.model_sonnet = "lmstudio/qwen2.5-7b"
    provider = _FakeClassifierProvider(_classifier_sse("lmstudio/qwen2.5-7b"))
    router = ModelRouter(settings, provider_resolver=lambda _pid: provider)
    request = MessagesRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[Message(role="user", content="hello")],
    )

    routed = await router.aresolve_messages_request(request, request_id="req-1")

    assert routed.resolved.provider_model_ref == "lmstudio/qwen2.5-7b"
    # model_routing_mode defaults to "static": the classifier must never be called.
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_aresolve_messages_request_routes_dynamically_in_auto_mode(settings):
    settings.model = "lmstudio/model-a"
    settings.model_sonnet = "llamacpp/model-b"
    settings.model_routing_mode = "auto"
    settings.model_classifier = "ollama/classifier-model"
    provider = _FakeClassifierProvider(_classifier_sse("llamacpp/model-b"))
    router = ModelRouter(settings, provider_resolver=lambda _pid: provider)
    request = MessagesRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[Message(role="user", content="please write a python function")],
    )

    routed = await router.aresolve_messages_request(request, request_id="req-1")

    assert routed.resolved.provider_model_ref == "llamacpp/model-b"
    assert routed.request.model == "model-b"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_aresolve_messages_request_falls_back_when_classifier_unset(settings):
    settings.model = "lmstudio/model-a"
    settings.model_routing_mode = "auto"
    settings.model_classifier = None
    provider = _FakeClassifierProvider(_classifier_sse("lmstudio/model-a"))
    router = ModelRouter(settings, provider_resolver=lambda _pid: provider)
    request = MessagesRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[Message(role="user", content="hello")],
    )

    routed = await router.aresolve_messages_request(request, request_id="req-1")

    assert routed.resolved.provider_model_ref == "lmstudio/model-a"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_aresolve_messages_request_skips_auto_routing_for_direct_model(settings):
    settings.model_routing_mode = "auto"
    settings.model_classifier = "ollama/classifier-model"
    provider = _FakeClassifierProvider(_classifier_sse("deepseek/deepseek-chat"))
    router = ModelRouter(settings, provider_resolver=lambda _pid: provider)
    request = MessagesRequest(
        model="deepseek/deepseek-chat",
        max_tokens=100,
        messages=[Message(role="user", content="hello")],
    )

    routed = await router.aresolve_messages_request(request, request_id="req-1")

    assert routed.resolved.provider_model_ref == "deepseek/deepseek-chat"
    # A direct provider/model request already names its target explicitly.
    assert provider.calls == 0
