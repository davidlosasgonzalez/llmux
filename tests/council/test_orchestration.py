"""End-to-end orchestration behaviour with a fake invoker (no network)."""

import asyncio

import pytest

from free_claude_code.council.config import CouncilConfig
from free_claude_code.council.invoker import InvocationResult
from free_claude_code.council.models import Depth, ModelRef, TaskType
from free_claude_code.council.orchestration import Orchestrator
from tests.council.support import FakeInvoker, default_candidates


class _Err429(Exception):
    status_code = 429


def _config() -> CouncilConfig:
    return CouncilConfig()


class _SlowInvoker:
    """Wraps FakeInvoker but sleeps forever for one model, to trip the timeout."""

    def __init__(self, slow_key: str) -> None:
        self.slow_key = slow_key
        self._inner = FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"])

    async def invoke(
        self,
        model: ModelRef,
        system: str,
        user: str,
        *,
        max_tokens: int,
        request_id: str,
    ) -> InvocationResult:
        if model.key == self.slow_key:
            await asyncio.sleep(60)  # far beyond the tiny test timeout
        return await self._inner.invoke(
            model, system, user, max_tokens=max_tokens, request_id=request_id
        )


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
async def test_slow_model_times_out_and_deliberation_completes():
    candidates = default_candidates()
    victim = candidates[0].key  # groq/llama
    invoker = _SlowInvoker(slow_key=victim)
    config = _config().model_copy(update={"call_timeout_s": 0.05})
    orch = Orchestrator(invoker, config)
    result = await orch.run("Q", TaskType.GENERAL_REASONING, candidates)

    # The deliberation still produced an answer despite one model hanging...
    assert result.final_synthesis is not None
    # ...and the timed-out model contributed no proposal (it was treated as failed).
    assert victim not in {p.model_key for p in result.proposals}


@pytest.mark.asyncio
async def test_degenerate_critique_retries_then_stops_without_burning_rounds():
    # A critic that returns "revise" with score 0 and no issues is degenerate.
    invoker = FakeInvoker(critique_scores=[0.0], critique_verdicts=["revise"])
    orch = Orchestrator(invoker, _config())
    result = await orch.run(
        "Q", TaskType.GENERAL_REASONING, default_candidates(), depth=Depth.DEEP
    )

    # Stopped immediately instead of grinding through all 3 rounds.
    assert result.stop_reason == "critique unavailable"
    assert len(result.rounds) == 1
    # The guard retried once with a different critic before giving up.
    critique_calls = [c for c in invoker.calls if c[1] == "critique"]
    assert len(critique_calls) == 2
    # A degenerate critique must never be reported as real confidence.
    compact = result.compact()
    assert compact["confidence"] is None
    assert compact["confidence_source"] == "unavailable"


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
