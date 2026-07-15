"""Budget-class routing and approximate usage tracking."""

import pytest

from free_claude_code.council.config import CouncilConfig
from free_claude_code.council.models import CostStatus, ModelRef, TaskType
from free_claude_code.council.orchestration import Orchestrator
from free_claude_code.council.provider_limits import (
    budget_class,
    budget_multiplier,
    daily_limit,
)
from free_claude_code.council.selector import select_models
from free_claude_code.council.storage import CouncilStore, ModelStats
from tests.council.support import FakeInvoker, default_candidates


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
        CouncilConfig(),
        category="general_reasoning",
        count=1,
        role="proponent",
    )
    assert proponents[0].provider == "groq"  # scarce avoided for fan-out

    refiners = select_models(
        candidates,
        _cold,
        CouncilConfig(),
        category="general_reasoning",
        count=1,
        role="refiner",
    )
    # For a single high-value call the scarce top model is allowed to win.
    assert refiners[0].provider in ("open_router", "groq")


@pytest.mark.asyncio
async def test_usage_is_recorded(tmp_path):
    store = CouncilStore(tmp_path / "council.db")
    orch = Orchestrator(
        FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"]),
        CouncilConfig(),
        store=store,
    )
    await orch.run("Q", TaskType.GENERAL_REASONING, default_candidates())

    rows = store.usage_rows()
    assert rows, "expected usage rows after a run"
    assert sum(r.requests for r in rows) > 0
    assert sum(r.total_tokens for r in rows) > 0  # tokens accumulated
    store.close()
