"""Tests for Fireworks AI provider."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providers.base import ProviderConfig
from providers.fireworks import FIREWORKS_BASE_URL, FireworksProvider


class MockMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class MockBlock:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockRequest:
    def __init__(self, **kwargs):
        self.model = "accounts/fireworks/models/glm-5p1"
        self.messages = [MockMessage("user", "Hello")]
        self.max_tokens = 100
        self.temperature = 0.5
        self.top_p = 0.9
        self.system = "System prompt"
        self.stop_sequences = None
        self.tools = []
        self.extra_body = {}
        self.thinking = MagicMock()
        self.thinking.enabled = True
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def fireworks_config():
    return ProviderConfig(
        api_key="test_fireworks_key",
        base_url=FIREWORKS_BASE_URL,
        rate_limit=10,
        rate_window=60,
        enable_thinking=True,
    )


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Mock the global rate limiter to prevent waiting."""

    @asynccontextmanager
    async def _slot():
        yield

    with patch("providers.openai_compat.GlobalRateLimiter") as mock:
        instance = mock.get_scoped_instance.return_value

        async def _passthrough(fn, *args, **kwargs):
            return await fn(*args, **kwargs)

        instance.execute_with_retry = AsyncMock(side_effect=_passthrough)
        instance.concurrency_slot.side_effect = _slot
        yield instance


@pytest.fixture
def fireworks_provider(fireworks_config):
    return FireworksProvider(fireworks_config)


def test_init(fireworks_config):
    """Test provider initialization."""
    with patch("providers.openai_compat.AsyncOpenAI") as mock_openai:
        provider = FireworksProvider(fireworks_config)
        assert provider._api_key == "test_fireworks_key"
        assert provider._base_url == FIREWORKS_BASE_URL
        mock_openai.assert_called_once()


def test_base_url_constant():
    """FIREWORKS_BASE_URL points to the Fireworks AI inference endpoint."""
    assert FIREWORKS_BASE_URL == "https://api.fireworks.ai/inference/v1"


def test_build_request_body_basic(fireworks_provider):
    """Basic request body conversion works for Fireworks AI."""
    req = MockRequest()
    body = fireworks_provider._build_request_body(req)

    assert body["model"] == "accounts/fireworks/models/glm-5p1"
    assert body["messages"][0]["role"] == "system"


def test_build_request_body_global_disable_blocks_thinking():
    """Global disable suppresses provider-side thinking."""
    provider = FireworksProvider(
        ProviderConfig(
            api_key="test_fireworks_key",
            base_url=FIREWORKS_BASE_URL,
            rate_limit=10,
            rate_window=60,
            enable_thinking=False,
        )
    )
    req = MockRequest()
    body = provider._build_request_body(req)

    # When thinking is disabled, no thinking-related fields should appear
    assert "extra_body" not in body or "thinking" not in body.get("extra_body", {})


def test_build_request_body_request_disable_blocks_thinking(fireworks_provider):
    """Request-level disable suppresses provider-side thinking when global is enabled."""
    req = MockRequest()
    req.thinking.enabled = False
    body = fireworks_provider._build_request_body(req)

    assert "extra_body" not in body or "thinking" not in body.get("extra_body", {})


def test_build_request_body_preserves_caller_extra_body(fireworks_provider):
    """Caller-provided extra_body should be preserved."""
    req = MockRequest(
        extra_body={"custom_param": "value"},
    )
    body = fireworks_provider._build_request_body(req)

    assert body["extra_body"]["custom_param"] == "value"


@pytest.mark.asyncio
async def test_stream_response_text(fireworks_provider):
    """Text content deltas are emitted as text blocks."""
    req = MockRequest()

    mock_chunk = MagicMock()
    mock_chunk.choices = [
        MagicMock(
            delta=MagicMock(
                content="Hello back!",
                reasoning_content=None,
                tool_calls=None,
            ),
            finish_reason="stop",
        )
    ]
    mock_chunk.usage = MagicMock(completion_tokens=5, prompt_tokens=10)

    async def mock_stream():
        yield mock_chunk

    with patch.object(
        fireworks_provider._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_stream()

        events = [event async for event in fireworks_provider.stream_response(req)]

        assert any(
            '"text_delta"' in event and "Hello back!" in event for event in events
        )


@pytest.mark.asyncio
async def test_stream_response_reasoning_content(fireworks_provider):
    """reasoning_content deltas are emitted as thinking blocks."""
    req = MockRequest()

    mock_chunk = MagicMock()
    mock_chunk.choices = [
        MagicMock(
            delta=MagicMock(
                content=None,
                reasoning_content="Thinking...",
                tool_calls=None,
            ),
            finish_reason="stop",
        )
    ]
    mock_chunk.usage = MagicMock(completion_tokens=2, prompt_tokens=10)

    async def mock_stream():
        yield mock_chunk

    with patch.object(
        fireworks_provider._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_stream()

        events = [event async for event in fireworks_provider.stream_response(req)]

        assert any(
            '"thinking_delta"' in event and "Thinking..." in event for event in events
        )


@pytest.mark.asyncio
async def test_cleanup(fireworks_provider):
    """cleanup closes the OpenAI client."""
    fireworks_provider._client = AsyncMock()

    await fireworks_provider.cleanup()

    fireworks_provider._client.close.assert_called_once()
