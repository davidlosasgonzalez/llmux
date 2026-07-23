"""Budget-class semantics for provider limits, including paid model splits."""

from llmux.core.provider_limits import (
    HIGH_THROUGHPUT,
    PAID,
    SCARCE,
    UNKNOWN,
    daily_limit,
)


def test_open_router_free_slug_stays_scarce() -> None:
    limit = daily_limit("open_router", "deepseek/deepseek-v3.2:free")

    assert limit.budget_class == SCARCE
    assert limit.rpd == 50


def test_open_router_paid_slug_is_paid_with_no_daily_cap() -> None:
    limit = daily_limit("open_router", "deepseek/deepseek-v4-flash")

    assert limit.budget_class == PAID
    assert limit.rpm is None
    assert limit.rpd is None


def test_open_router_without_model_keeps_free_tier_default() -> None:
    assert daily_limit("open_router").budget_class == SCARCE


def test_other_providers_ignore_model_id() -> None:
    assert daily_limit("groq", "llama-3.3-70b-versatile").budget_class == (
        HIGH_THROUGHPUT
    )
    assert daily_limit("nvidia_nim").budget_class == HIGH_THROUGHPUT


def test_unknown_provider_reports_unknown() -> None:
    assert daily_limit("nonexistent").budget_class == UNKNOWN
