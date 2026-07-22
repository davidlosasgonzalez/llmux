"""Approximate free-tier limits and budget classes per provider.

Free tiers split providers in two operational classes:

* **high_throughput** — generous RPM/RPD, safe to fan out (propose/review).
* **scarce** — very low daily caps (e.g. OpenRouter :free 50 RPD, GitHub high-tier
  50 RPD) but host the strongest models; reserve for the 1-2 refine/critique calls.

Numbers are best-effort from provider docs (see docs/providers-and-models.md) and
change often — they drive *approximate* usage reporting and soft routing bias,
never a hard cap that could wrongly block a call.
"""

from dataclasses import dataclass

HIGH_THROUGHPUT = "high_throughput"
SCARCE = "scarce"
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
    # window (``ModelRef.context_length``), which can be far larger. Observed
    # directly: GitHub Models free tier rejects deepseek-r1-0528 with HTTP 413
    # ("Request body too large... Max size: 4000 tokens") well inside that
    # model's real window, so ``_fit_context_window`` must check both.
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


def daily_limit(provider: str) -> DailyLimit:
    known = _LIMITS.get(provider)
    if known is not None:
        return known
    return DailyLimit(provider, UNKNOWN, note="Unknown limits.")


def budget_class(provider: str) -> str:
    return daily_limit(provider).budget_class


def max_request_tokens(provider: str) -> int | None:
    return daily_limit(provider).max_request_tokens


# Soft multiplier applied to a provider's selection score, by role. Fan-out
# phases (propose/review) avoid scarce providers so their tiny daily quota is
# saved for the high-value single calls (refine/critique), where they are
# allowed at full weight.
_FANOUT_ROLES = frozenset({"proponent", "reviewer"})
_SCARCE_FANOUT_PENALTY = 0.55


def budget_multiplier(provider: str, role: str) -> float:
    """Return a 0..1 score multiplier for using ``provider`` in ``role``."""
    if role in _FANOUT_ROLES and budget_class(provider) == SCARCE:
        return _SCARCE_FANOUT_PENALTY
    return 1.0
