"""Shared test doubles for the verdict suite — no real APIs are touched."""

import json
from dataclasses import dataclass, field

from free_claude_code.verdict.invoker import InvocationResult
from free_claude_code.verdict.models import (
    CostStatus,
    Health,
    ModelRef,
    QuotaStatus,
)


def make_model(
    provider: str,
    model_id: str,
    *,
    family: str = "fam",
    cost: CostStatus = CostStatus.FREE_TIER,
    context_length: int | None = None,
) -> ModelRef:
    return ModelRef(
        provider=provider,
        model_id=model_id,
        family=family,
        cost_status=cost,
        quota_status=QuotaStatus.AVAILABLE,
        health=Health.HEALTHY,
        supports_tools=True,
        supports_json=True,
        context_length=context_length,
    )


def default_candidates() -> list[ModelRef]:
    return [
        make_model("groq", "llama-3.3-70b-versatile", family="llama"),
        make_model("cerebras", "qwen-3-32b", family="qwen"),
        make_model("gemini", "models/gemini-flash", family="gemini"),
        make_model("nvidia_nim", "nvidia/nemotron", family="nemotron"),
    ]


def _phase(system: str) -> str:
    if "Solve it INDEPENDENTLY" in system:
        return "propose"
    if "impartial reviewer" in system:
        return "review"
    if "the synthesiser" in system:
        return "synthesis"
    if "adversarial critic" in system:
        return "critique"
    return "unknown"


@dataclass
class FakeInvoker:
    """Deterministic invoker that answers each phase with valid JSON.

    Knobs let tests force failures, bad JSON, or a rising/falling critique score.
    """

    # model_key -> exception to raise on invoke (simulate 429 etc.)
    raise_for: dict[str, Exception] = field(default_factory=dict)
    # model_key -> exception, but only when invoked for one specific phase
    # (lets a test fail a model in exactly one role without also killing its
    # calls in the other phases).
    raise_for_synthesis: dict[str, Exception] = field(default_factory=dict)
    raise_for_review: dict[str, Exception] = field(default_factory=dict)
    raise_for_critique: dict[str, Exception] = field(default_factory=dict)
    # model_keys that should return unparseable text on propose.
    bad_json_for: set[str] = field(default_factory=set)
    # model_keys whose review should be unparseable garbage (discarded).
    bad_review_for: set[str] = field(default_factory=set)
    # Sequence of critique scores returned per synthesis round.
    critique_scores: list[float] = field(default_factory=lambda: [0.95])
    critique_verdicts: list[str] = field(default_factory=lambda: ["pass"])
    # Material disagreements every synthesis should report (drives T6 escalation).
    synthesis_disagreements: list[str] = field(default_factory=list)
    # URLs appended to every synthesis' final_answer (drives T7 citation checks).
    synthesis_urls: list[str] = field(default_factory=list)
    # When True, every synthesis returns identical text (drives convergence stop).
    stable_synthesis: bool = False
    # Records every (model_key, phase, system, user) call for assertions.
    calls: list[tuple[str, str, str, str]] = field(default_factory=list)
    _round: int = 0

    async def invoke(
        self,
        model: ModelRef,
        system: str,
        user: str,
        *,
        max_tokens: int,
        request_id: str,
    ) -> InvocationResult:
        phase = _phase(system)
        self.calls.append((model.key, phase, system, user))

        if model.key in self.raise_for:
            raise self.raise_for[model.key]
        if phase == "synthesis" and model.key in self.raise_for_synthesis:
            raise self.raise_for_synthesis[model.key]
        if phase == "review" and model.key in self.raise_for_review:
            raise self.raise_for_review[model.key]
        if phase == "critique" and model.key in self.raise_for_critique:
            raise self.raise_for_critique[model.key]

        if phase == "propose":
            if model.key in self.bad_json_for:
                return InvocationResult.success(model.key, "not json at all")
            # Neutral content: never leak provider/model identity into the text
            # that reviewers and the synthesiser will see.
            payload = {
                "conclusion": "The recommended approach is to proceed carefully.",
                "reasoning_summary": ["step"],
                "assumptions": [],
                "evidence": [],
                "risks": [],
                "unknowns": [],
                "confidence": 0.8,
            }
        elif phase == "review":
            if model.key in self.bad_review_for:
                return InvocationResult.success(model.key, "totally not json")
            payload = {
                "fatal_errors": [],
                "material_errors": [],
                "unsupported_claims": [],
                "important_omissions": [],
                "best_elements": ["clarity"],
                "ranking": ["A", "B", "C", "D"],
                "recommended_synthesis": ["combine"],
            }
        elif phase == "synthesis":
            answer = (
                "Synthesised answer"
                if self.stable_synthesis
                else f"Synthesised answer (round {self._round})"
            )
            if self.synthesis_urls:
                answer += " See " + " and ".join(self.synthesis_urls) + "."
            payload = {
                "final_answer": answer,
                "consensus": ["c1"],
                "material_disagreements": list(self.synthesis_disagreements),
                "uncertainties": [],
                "rejected_arguments": [],
                "recommended_action": "do it",
                "quality_score": 0.8,
            }
        elif phase == "critique":
            idx = min(self._round, len(self.critique_scores) - 1)
            vidx = min(self._round, len(self.critique_verdicts) - 1)
            payload = {
                "critical_issues": [],
                "material_issues": [],
                "minor_issues": [],
                "missing_evidence": [],
                "verdict": self.critique_verdicts[vidx],
                "score": self.critique_scores[idx],
            }
            self._round += 1
        else:
            payload = {"note": "unknown phase"}

        return InvocationResult.success(
            model.key, json.dumps(payload), input_tokens=100, output_tokens=50
        )
