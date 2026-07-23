"""Approximate free-tier limits and budget classes per provider.

Free tiers split providers in two operational classes:

* **high_throughput** — generous RPM/RPD, safe to fan out.
* **scarce** — very low daily caps (e.g. OpenRouter :free 50 RPD, GitHub high-tier
  50 RPD) but host the strongest models; reserve for high-value calls.

Numbers are best-effort from provider docs (see docs/providers-and-models.md) and
change often — they drive *approximate* usage reporting and soft routing bias,
never a hard cap that could wrongly block a call.

Shared by :mod:`verdict.provider_limits` (which adds deliberation-role fan-out
bias) and :mod:`application.auto_router` (which cannot depend on ``verdict``,
see ``tests/contracts/test_architecture_contracts.py``), so the table itself
lives here once instead of being duplicated per consumer.
"""

from dataclasses import dataclass

HIGH_THROUGHPUT = "high_throughput"
SCARCE = "scarce"
PAID = "paid"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DailyLimit:
    """Approximate free-tier limits for one provider."""

    provider: str
    budget_class: str
    rpm: int | None = None
    rpd: int | None = None
    tokens_per_day: int | None = None
    # Hard cap on one request's total token count (prompt + completion) that
    # the free tier itself enforces — distinct from a model's real context
    # window, which can be far larger. Observed directly: GitHub Models free
    # tier rejects deepseek-r1-0528 with HTTP 413 ("Request body too large...
    # Max size: 4000 tokens") well inside that model's real window.
    max_request_tokens: int | None = None
    note: str = ""


_LIMITS: dict[str, DailyLimit] = {
    "groq": DailyLimit(
        "groq",
        HIGH_THROUGHPUT,
        rpm=30,
        rpd=1000,
        note="Varies a lot by model; some up to 14,400 RPD.",
    ),
    "cerebras": DailyLimit(
        "cerebras",
        HIGH_THROUGHPUT,
        rpm=30,
        tokens_per_day=1_000_000,
        note="Token-based, ~1M/day. Free context often capped ~8K.",
    ),
    "gemini": DailyLimit(
        "gemini",
        HIGH_THROUGHPUT,
        rpm=15,
        rpd=1000,
        note="Flash-lite generous; Pro much tighter (~100 RPD / 5 RPM).",
    ),
    "nvidia_nim": DailyLimit(
        "nvidia_nim",
        HIGH_THROUGHPUT,
        rpm=40,
        note="Credit-based (1,000-5,000 credits), not a fixed RPD.",
    ),
    "open_router": DailyLimit(
        "open_router",
        SCARCE,
        rpm=20,
        rpd=50,
        note="50 RPD unless lifetime purchases >= $10 (then 1,000). Failures count.",
    ),
    "github_models": DailyLimit(
        "github_models",
        SCARCE,
        rpm=10,
        rpd=50,
        max_request_tokens=4000,
        note="High-tier (e.g. deepseek-r1) 50 RPD; low-tier 150. 8K in / 4K out.",
    ),
    "mistral": DailyLimit("mistral", HIGH_THROUGHPUT, note="Free experiment tier."),
    "cohere": DailyLimit("cohere", SCARCE, note="Trial keys, monthly-limited."),
    "cloudflare": DailyLimit(
        "cloudflare", HIGH_THROUGHPUT, note="Daily free neuron allocation."
    ),
    # Local providers: effectively unlimited.
    "lmstudio": DailyLimit("lmstudio", HIGH_THROUGHPUT, note="Local, no limit."),
    "llamacpp": DailyLimit("llamacpp", HIGH_THROUGHPUT, note="Local, no limit."),
    "ollama": DailyLimit("ollama", HIGH_THROUGHPUT, note="Local, no limit."),
}


# OpenRouter's scarce free tier applies only to ``:free``-suffixed model slugs.
# Paid slugs on a credited key bill per token with no fixed daily cap, so the
# router must not treat them as scarce or it will under-route to them.
_OPEN_ROUTER_PAID = DailyLimit(
    "open_router",
    PAID,
    note="Pay-per-token on purchased credits; no fixed RPD. Very cheap per call.",
)


def daily_limit(provider: str, model_id: str | None = None) -> DailyLimit:
    if (
        provider == "open_router"
        and model_id is not None
        and not model_id.endswith(":free")
    ):
        return _OPEN_ROUTER_PAID
    known = _LIMITS.get(provider)
    if known is not None:
        return known
    return DailyLimit(provider, UNKNOWN, note="Unknown limits.")


def budget_class(provider: str) -> str:
    return daily_limit(provider).budget_class


def max_request_tokens(provider: str) -> int | None:
    return daily_limit(provider).max_request_tokens
