"""End-to-end orchestration behaviour with a fake invoker (no network)."""

import pytest

from free_claude_code.council.config import CouncilConfig
from free_claude_code.council.models import Depth, TaskType
from free_claude_code.council.orchestration import Orchestrator
from tests.council.support import FakeInvoker, default_candidates


class _Err429(Exception):
    status_code = 429


def _config() -> CouncilConfig:
    return CouncilConfig()


@pytest.mark.asyncio
async def test_stops_when_quality_threshold_met():
    invoker = FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"])
    orch = Orchestrator(invoker, _config())
    result = await orch.run(
        "What is the best approach?", TaskType.GENERAL_REASONING, default_candidates()
    )
    assert len(result.rounds) == 1
    assert result.stop_reason == "quality threshold met"
    assert result.final_synthesis is not None
    assert result.final_synthesis.final_answer


@pytest.mark.asyncio
async def test_stops_at_max_rounds_when_never_passing():
    invoker = FakeInvoker(
        critique_scores=[0.5, 0.6, 0.7],
        critique_verdicts=["revise", "revise", "revise"],
    )
    orch = Orchestrator(invoker, _config())
    result = await orch.run(
        "Question", TaskType.GENERAL_REASONING, default_candidates(), depth=Depth.DEEP
    )
    assert len(result.rounds) == 3
    assert result.stop_reason == "max rounds reached"


@pytest.mark.asyncio
async def test_stops_on_two_rounds_without_improvement():
    invoker = FakeInvoker(
        critique_scores=[0.5, 0.5, 0.5],
        critique_verdicts=["revise", "revise", "revise"],
    )
    orch = Orchestrator(invoker, _config())
    result = await orch.run(
        "Question", TaskType.GENERAL_REASONING, default_candidates(), depth=Depth.DEEP
    )
    assert result.stop_reason == "two rounds without material improvement"


@pytest.mark.asyncio
async def test_reviews_are_anonymous():
    invoker = FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"])
    orch = Orchestrator(invoker, _config())
    await orch.run("Q", TaskType.GENERAL_REASONING, default_candidates())

    provider_names = {m.provider for m in default_candidates()}
    model_ids = {m.model_id for m in default_candidates()}
    review_prompts = [
        user for (_k, phase, _s, user) in invoker.calls if phase == "review"
    ]
    assert review_prompts, "expected at least one review call"
    for prompt in review_prompts:
        assert "PROPOSAL A" in prompt
        for name in provider_names | model_ids:
            assert name not in prompt


@pytest.mark.asyncio
async def test_429_triggers_fallback_and_completes():
    candidates = default_candidates()
    victim = candidates[-1].key  # nvidia_nim/nvidia/nemotron
    invoker = FakeInvoker(
        raise_for={victim: _Err429()},
        critique_scores=[0.95],
        critique_verdicts=["pass"],
    )
    orch = Orchestrator(invoker, _config())
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)

    assert result.final_synthesis is not None  # deliberation still completed
    proposal_models = {p.model_key for p in result.proposals}
    assert victim not in proposal_models  # the 429 model was dropped


@pytest.mark.asyncio
async def test_garbage_review_is_discarded():
    candidates = default_candidates()
    invoker = FakeInvoker(
        bad_review_for={m.key for m in candidates},
        critique_scores=[0.95],
        critique_verdicts=["pass"],
    )
    orch = Orchestrator(invoker, _config())
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)
    # Every reviewer emitted unparseable JSON, so no review survived.
    assert result.reviews == []
    # ...yet synthesis still produced a usable answer.
    assert result.final_synthesis is not None
