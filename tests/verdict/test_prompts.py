"""Prompt-construction contracts for the factual-disagreement rules (T6)."""

from llmux.verdict.models import Proposal, Synthesis
from llmux.verdict.prompts import critique_prompt, synthesis_prompt


def _synthesis() -> Synthesis:
    return Synthesis(
        model_key="p/m",
        final_answer="The default is 30 seconds.",
        material_disagreements=["30s vs 50ms default"],
        recommended_action="verify",
    )


def test_synthesis_prompt_carries_context_and_evidence_rule():
    proposals = {"A": Proposal(model_key="p/a", conclusion="use 30s")}
    system, user = synthesis_prompt(
        "What is the default?",
        proposals,
        [],
        context="VERIFIED SOURCES (fetched 2026-07-15):\n[S1] https://x/docs",
    )
    # The hard rule against resolving factual conflicts by majority is present...
    assert "majority" in system.lower()
    assert "VERIFIED SOURCES" in system
    # ...and the sources reach the synthesiser.
    assert "AUTHORISED CONTEXT" in user
    assert "[S1] https://x/docs" in user


def test_synthesis_prompt_without_context_omits_block():
    proposals = {"A": Proposal(model_key="p/a", conclusion="x")}
    _system, user = synthesis_prompt("Q", proposals, [])
    assert "AUTHORISED CONTEXT" not in user


def test_critique_prompt_carries_context_and_factual_lens():
    system, user = critique_prompt(
        "What is the default?",
        _synthesis(),
        context="[S1] https://x/docs — default 30s",
    )
    # The critic is told to hunt unsourced version/limit/price claims.
    assert "versions/limits/prices" in system
    assert "AUTHORISED CONTEXT" in user
    assert "[S1] https://x/docs" in user


def test_critique_prompt_without_context_omits_block():
    _system, user = critique_prompt("Q", _synthesis())
    assert "AUTHORISED CONTEXT" not in user
