"""Provider-level failure kinds for circuit breaking (SDK-free).

Distinct from :class:`free_claude_code.core.failures.FailureKind`, which maps
to Anthropic wire error types. This enum drives cool-off windows for free
providers in the council and the own-agent harness.
"""

from enum import StrEnum


class FailureKind(StrEnum):
    """Normalised classification of a provider failure."""

    AUTHENTICATION = "authentication_failure"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    PROVIDER_FAILURE = "provider_failure"
    UNKNOWN_COST = "unknown_cost"
