"""HTTP client for the local FCC Anthropic Messages proxy."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from free_claude_code.cli.proxy_auth import PROXY_NO_AUTH_SENTINEL, proxy_auth_token
from free_claude_code.core.quota import (
    DailyExhaustionStore,
    FailureKind,
    QuotaTracker,
    classify_failure,
    retry_after_seconds,
)


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """One assistant message from the proxy (text and/or tool_use blocks)."""

    content: list[dict[str, Any]]
    stop_reason: str | None = None
    model: str | None = None

    @property
    def text(self) -> str:
        parts = [
            str(block.get("text", ""))
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [
            block
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]


class ProxyError(RuntimeError):
    """Proxy HTTP failure carrying a status code for quota classification."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProxyClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AssistantTurn: ...


def provider_from_model(model: str) -> str:
    """Best-effort provider id from ``provider/model`` or bare Claude aliases."""
    text = model.strip()
    if "/" in text:
        return text.split("/", 1)[0]
    return "default"


class HttpProxyClient:
    """POST ``/v1/messages`` (non-streaming) against the local FCC proxy."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str = "",
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = proxy_auth_token(auth_token)
        self._timeout_s = timeout_s
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._auth_token and self._auth_token != PROXY_NO_AUTH_SENTINEL:
            headers["authorization"] = f"Bearer {self._auth_token}"
        else:
            headers["authorization"] = f"Bearer {PROXY_NO_AUTH_SENTINEL}"
        return headers

    async def complete(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AssistantTurn:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "stream": False,
            "system": system,
            "messages": list(messages),
            "tools": list(tools),
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
        if response.status_code >= 400:
            raise ProxyError(
                f"proxy returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        body = response.json()
        content = body.get("content")
        if not isinstance(content, list):
            content = []
        return AssistantTurn(
            content=content,
            stop_reason=body.get("stop_reason")
            if isinstance(body.get("stop_reason"), str)
            else None,
            model=model,
        )


@dataclass(slots=True)
class FallbackProxyClient:
    """Try primary then fallback models; skip blocked/exhausted providers (A8)."""

    inner: ProxyClient
    fallback_models: list[str] = field(default_factory=list)
    quota: QuotaTracker = field(default_factory=QuotaTracker)
    exhaustion: DailyExhaustionStore | None = None

    async def complete(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AssistantTurn:
        candidates = self._candidates(model)
        errors: list[str] = []
        for candidate in candidates:
            provider = provider_from_model(candidate)
            if self.quota.is_blocked(provider):
                errors.append(
                    f"{candidate}: blocked ({self.quota.block_reason(provider)})"
                )
                continue
            if self.exhaustion is not None:
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                if candidate in self.exhaustion.exhausted_keys(day):
                    errors.append(f"{candidate}: exhausted today")
                    continue
            try:
                turn = await self.inner.complete(
                    messages=messages,
                    tools=tools,
                    system=system,
                    model=candidate,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                kind = classify_failure(exc)
                self.quota.note_failure(
                    provider, kind, retry_after=retry_after_seconds(exc)
                )
                if (
                    kind in {FailureKind.QUOTA_EXHAUSTED, FailureKind.RATE_LIMITED}
                    and self.exhaustion is not None
                ):
                    day = datetime.now(UTC).strftime("%Y-%m-%d")
                    self.exhaustion.record_exhaustion(candidate, provider, day)
                errors.append(f"{candidate}: {exc}")
                continue
            self.quota.note_success(provider)
            return turn
        raise RuntimeError(
            "all candidate models failed: "
            + ("; ".join(errors) if errors else "no candidates")
        )

    def _candidates(self, primary: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in [primary, *self.fallback_models]:
            text = name.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
