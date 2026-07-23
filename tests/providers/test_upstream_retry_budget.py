"""C12: the upstream retry budget is configurable so fallback can win over backoff."""

import httpx
import pytest

from llmux.providers.base import ProviderConfig
from llmux.providers.rate_limit import ProviderRateLimiter


def _rate_limited_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://upstream.example/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return httpx.HTTPStatusError("429", request=request, response=response)


def _limiter(upstream_max_retries: int) -> ProviderRateLimiter:
    return ProviderRateLimiter(
        rate_limit=100,
        rate_window=1.0,
        upstream_max_retries=upstream_max_retries,
    )


@pytest.mark.asyncio
async def test_zero_budget_fails_on_first_429_without_backoff() -> None:
    limiter = _limiter(0)
    calls = 0

    async def always_429() -> None:
        nonlocal calls
        calls += 1
        raise _rate_limited_error()

    with pytest.raises(httpx.HTTPStatusError):
        await limiter.execute_with_retry(always_429)
    assert calls == 1


@pytest.mark.asyncio
async def test_explicit_max_retries_still_overrides_configured_budget() -> None:
    limiter = _limiter(0)
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _rate_limited_error()
        return "ok"

    result = await limiter.execute_with_retry(
        flaky, max_retries=1, base_delay=0.01, max_delay=0.01, jitter=0.0
    )
    assert result == "ok"
    assert calls == 2


def test_provider_config_carries_retry_budget_default() -> None:
    config = ProviderConfig(api_key="k", base_url="https://upstream.example")
    assert config.upstream_max_retries == 4


def test_negative_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        _limiter(-1)
