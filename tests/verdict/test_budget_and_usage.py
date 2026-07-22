"""Budget-class routing and approximate usage tracking."""

import pytest

from free_claude_code.verdict.config import VerdictConfig
from free_claude_code.verdict.models import CostStatus, ModelRef, TaskType
from free_claude_code.verdict.orchestration import Orchestrator
from free_claude_code.verdict.provider_limits import (
    budget_class,
    budget_multiplier,
    daily_limit,
)
from free_claude_code.verdict.selector import select_models
from free_claude_code.verdict.storage import ModelStats, VerdictStore
from tests.verdict.support import FakeInvoker, default_candidates


def _cold(key: str, category: str) -> ModelStats:
    return ModelStats(model_key=key, category=category)


def _m(provider: str, model_id: str, family: str, *, reasoning: bool = False):
    return ModelRef(
        provider=provider,
        model_id=model_id,
        family=family,
        supports_reasoning=reasoning,
        cost_status=CostStatus.FREE_TIER,
    )


def test_budget_class_and_multiplier():
    assert budget_class("groq") == "high_throughput"
    assert budget_class("open_router") == "scarce"
    assert budget_class("github_models") == "scarce"
    # Fan-out roles penalise scarce providers; single-call roles do not.
    assert budget_multiplier("open_router", "proponent") < 1.0
    assert budget_multiplier("open_router", "refiner") == 1.0
    assert budget_multiplier("groq", "proponent") == 1.0


def test_daily_limits_present():
    assert daily_limit("open_router").rpd == 50
    assert daily_limit("groq").budget_class == "high_throughput"


def test_fanout_prefers_high_throughput_over_scarce():
    # Equal-capability strong models on a scarce vs a high-throughput provider.
    candidates = [
        _m("open_router", "nvidia/nemotron-3-super-120b-a12b:free", "nemotron"),
        _m("groq", "openai/gpt-oss-120b", "gpt-oss"),
    ]
    proponents = select_models(
        candidates,
        _cold,
        VerdictConfig(),
        category="general_reasoning",
        count=1,
        role="proponent",
    )
    assert proponents[0].provider == "groq"  # scarce avoided for fan-out

    refiners = select_models(
        candidates,
        _cold,
        VerdictConfig(),
        category="general_reasoning",
        count=1,
        role="refiner",
    )
    # For a single high-value call the scarce top model is allowed to win.
    assert refiners[0].provider in ("open_router", "groq")


@pytest.mark.asyncio
async def test_usage_is_recorded(tmp_path):
    store = VerdictStore(tmp_path / "verdict.db")
    orch = Orchestrator(
        FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"]),
        VerdictConfig(),
        store=store,
    )
    await orch.run("Q", TaskType.GENERAL_REASONING, default_candidates())

    rows = store.usage_rows()
    assert rows, "expected usage rows after a run"
    assert sum(r.requests for r in rows) > 0
    assert sum(r.total_tokens for r in rows) > 0  # tokens accumulated
    store.close()


def test_exhaustion_round_trip(tmp_path):
    store = VerdictStore(tmp_path / "verdict.db")
    store.record_exhaustion("groq/llama", "groq", "2026-07-15")
    store.record_exhaustion("groq/llama", "groq", "2026-07-15")  # idempotent
    assert store.exhausted_keys("2026-07-15") == {"groq/llama"}
    assert store.exhausted_keys("2026-07-16") == set()
    store.close()


class _QuotaError(RuntimeError):
    """Message triggers classify_failure -> QUOTA_EXHAUSTED."""


@pytest.mark.asyncio
async def test_quota_exhaustion_is_recorded_and_skipped_next_run(tmp_path):
    store = VerdictStore(tmp_path / "verdict.db")
    candidates = default_candidates()
    victim = candidates[-1].key  # nvidia_nim/nvidia/nemotron

    # Run 1: the victim hits a hard quota exhaustion → it is remembered for today.
    orch1 = Orchestrator(
        FakeInvoker(
            raise_for={victim: _QuotaError("quota exhausted")},
            critique_scores=[0.95],
            critique_verdicts=["pass"],
        ),
        VerdictConfig(),
        store=store,
    )
    await orch1.run("Q", TaskType.GENERAL_REASONING, candidates)

    from free_claude_code.verdict.orchestration import _today

    assert victim in store.exhausted_keys(_today())

    # Run 2 (fresh orchestrator, same store): the victim is skipped up front.
    invoker2 = FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"])
    orch2 = Orchestrator(invoker2, VerdictConfig(), store=store)
    result = await orch2.run("Q", TaskType.GENERAL_REASONING, candidates)

    assert result.final_synthesis is not None
    called_keys = {key for (key, _phase, _s, _u) in invoker2.calls}
    assert victim not in called_keys  # never invoked in the second run
    store.close()


# Regression: _gather_reviews / _synthesise / _critique used to return/raise on
# a failed invocation *before* calling self._record(...), so a model that only
# ever failed in one of those three roles left no trace in the store — neither
# the cross-run quota-exhaustion memory above, nor the stats score_model reads.
# Only _gather_proposals recorded failures. These three mirror the round-trip
# test above for each of the other phases.
@pytest.mark.asyncio
async def test_review_failure_is_recorded(tmp_path):
    from free_claude_code.verdict.orchestration import _today

    store = VerdictStore(tmp_path / "verdict.db")
    candidates = default_candidates()
    invoker = FakeInvoker(
        raise_for_review={c.key: _QuotaError("quota exhausted") for c in candidates},
        critique_scores=[0.95],
        critique_verdicts=["pass"],
    )
    orch = Orchestrator(invoker, VerdictConfig(), store=store)
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)

    assert result.final_synthesis is not None  # a failed review is non-fatal
    exhausted = store.exhausted_keys(_today())
    assert exhausted, "a reviewer that only ever fails must still be recorded"
    assert exhausted <= {c.key for c in candidates}
    store.close()


@pytest.mark.asyncio
async def test_synthesis_failure_is_recorded_even_when_fallback_recovers(tmp_path):
    from free_claude_code.verdict.orchestration import _today

    store = VerdictStore(tmp_path / "verdict.db")
    candidates = default_candidates()
    victim = candidates[-1].key
    invoker = FakeInvoker(
        raise_for_synthesis={victim: _QuotaError("quota exhausted")},
        critique_scores=[0.95],
        critique_verdicts=["pass"],
    )
    orch = Orchestrator(invoker, VerdictConfig(), store=store)
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)

    # The synthesiser fallback recovers the run...
    assert result.final_synthesis is not None
    # ...but the failed attempt must still have reached the store, or a future
    # run (fresh process) would retry the same exhausted synthesiser.
    assert victim in store.exhausted_keys(_today())
    store.close()


@pytest.mark.asyncio
async def test_critique_failure_is_recorded(tmp_path):
    from free_claude_code.verdict.orchestration import _today

    store = VerdictStore(tmp_path / "verdict.db")
    candidates = default_candidates()
    invoker = FakeInvoker(
        raise_for_critique={c.key: _QuotaError("quota exhausted") for c in candidates},
    )
    orch = Orchestrator(invoker, VerdictConfig(), store=store)
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)

    assert result.stop_reason == "critique unavailable"  # forced to REVISE/0.0
    exhausted = store.exhausted_keys(_today())
    assert exhausted, "a critic that only ever fails must still be recorded"
    assert exhausted <= {c.key for c in candidates}
    store.close()
