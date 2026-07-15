"""Failure classification and per-provider circuit breaking.

The underlying provider stack already does per-request backoff and retry. This
layer adds council-level accounting: it normalises whatever a provider raised
into a :class:`FailureKind`, and it temporarily benches a provider that is
authenticating badly, rate limited or out of quota so the panel fails over to
other free providers instead of hammering a dead one. No attempt is made to
dodge quotas via multiple keys or accounts.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .models import FailureKind

# Base cool-off per failure kind (seconds); rate-limit blocks extend on repeat.
_BLOCK_SECONDS: dict[FailureKind, float] = {
    FailureKind.AUTHENTICATION: 3600.0,
    FailureKind.QUOTA_EXHAUSTED: 1800.0,
    FailureKind.RATE_LIMITED: 60.0,
    FailureKind.MODEL_UNAVAILABLE: 300.0,
    FailureKind.PROVIDER_FAILURE: 30.0,
    FailureKind.UNSUPPORTED_CAPABILITY: 3600.0,
    FailureKind.UNKNOWN_COST: 86_400.0,
    FailureKind.INVALID_STRUCTURED_OUTPUT: 0.0,
}


def _status_code(error: object) -> int | None:
    code = getattr(error, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(error, "response", None)
    if response is not None:
        rc = getattr(response, "status_code", None)
        if isinstance(rc, int):
            return rc
    return None


def classify_failure(error: object) -> FailureKind:
    """Normalise an exception or error payload into a :class:`FailureKind`."""
    if isinstance(error, dict):
        message = str(error.get("message", "")).lower()
        etype = str(error.get("type", "")).lower()
        text = f"{etype} {message}"
    else:
        text = f"{type(error).__name__} {error}".lower()

    status = _status_code(error)

    if status == 401 or "authenticat" in text or "invalid api key" in text:
        return FailureKind.AUTHENTICATION
    if status == 429 or "rate limit" in text or "too many requests" in text:
        if "quota" in text or "insufficient" in text or "exhaust" in text:
            return FailureKind.QUOTA_EXHAUSTED
        return FailureKind.RATE_LIMITED
    if status == 402 or "quota" in text or "billing" in text or "insufficient" in text:
        return FailureKind.QUOTA_EXHAUSTED
    if status == 404 or "not found" in text or "does not exist" in text:
        return FailureKind.MODEL_UNAVAILABLE
    if "unsupported" in text or "not supported" in text or "capability" in text:
        return FailureKind.UNSUPPORTED_CAPABILITY
    return FailureKind.PROVIDER_FAILURE


def retry_after_seconds(error: object) -> float | None:
    """Extract a Retry-After hint (seconds) from an error, if present."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


@dataclass(slots=True)
class _ProviderState:
    blocked_until: float = 0.0
    last_kind: FailureKind | None = None
    consecutive_rate_limits: int = 0


@dataclass(slots=True)
class QuotaTracker:
    """In-memory circuit breaker keyed by provider id.

    ``now`` is injectable so tests can advance time without sleeping.
    """

    _states: dict[str, _ProviderState] = field(default_factory=dict)
    now: Callable[[], float] = time.time

    def _clock(self) -> float:
        return self.now()

    def _state(self, provider: str) -> _ProviderState:
        state = self._states.get(provider)
        if state is None:
            state = _ProviderState()
            self._states[provider] = state
        return state

    def is_blocked(self, provider: str) -> bool:
        state = self._states.get(provider)
        if state is None:
            return False
        return self._clock() < state.blocked_until

    def block_reason(self, provider: str) -> str:
        state = self._states.get(provider)
        if state is None or state.last_kind is None:
            return ""
        remaining = max(0.0, state.blocked_until - self._clock())
        return f"{state.last_kind.value} (retry in ~{int(remaining)}s)"

    def note_success(self, provider: str) -> None:
        state = self._states.get(provider)
        if state is not None:
            state.consecutive_rate_limits = 0

    def note_failure(
        self,
        provider: str,
        kind: FailureKind,
        *,
        retry_after: float | None = None,
    ) -> None:
        state = self._state(provider)
        state.last_kind = kind
        base = _BLOCK_SECONDS.get(kind, 30.0)
        if kind is FailureKind.RATE_LIMITED:
            state.consecutive_rate_limits += 1
            # Exponential-ish extension on repeated 429s, capped at 15 min.
            base = min(900.0, base * (2 ** (state.consecutive_rate_limits - 1)))
        if retry_after is not None:
            base = max(base, retry_after)
        if base <= 0.0:
            return
        state.blocked_until = self._clock() + base
