"""Core data types for the FCC Council deliberation layer.

These types are provider-agnostic and JSON-serialisable so the same structures
flow through the CLI, the MCP server and the tests without duplication.
"""

import time
from dataclasses import dataclass, field
from enum import StrEnum

from free_claude_code.core.quota import FailureKind as FailureKind


class TaskType(StrEnum):
    """Deliberation categories used to pick roles and models."""

    GENERAL_REASONING = "general_reasoning"
    SOFTWARE_ENGINEERING = "software_engineering"
    ARCHITECTURE = "architecture"
    DEBUGGING = "debugging"
    CODE_REVIEW = "code_review"
    PLANNING = "planning"
    PRODUCT_ANALYSIS = "product_analysis"
    RESEARCH = "research"
    DOCUMENT_ANALYSIS = "document_analysis"
    ADVERSARIAL_REVIEW = "adversarial_review"


class Depth(StrEnum):
    """Deliberation depth presets."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class Privacy(StrEnum):
    """How much of the caller-supplied context may leave the machine."""

    PUBLIC = "public"
    REDACTED = "redacted"
    LOCAL_ONLY = "local_only"


class CostStatus(StrEnum):
    """Whether a model can be used without ever incurring a charge.

    Only ``VERIFIED_FREE`` and ``FREE_TIER`` are eligible when
    ``ALLOW_PAID_MODELS`` is false. ``UNKNOWN`` and ``PAID`` are excluded.
    """

    VERIFIED_FREE = "verified_free"
    FREE_TIER = "free_tier"
    UNKNOWN = "unknown"
    PAID = "paid"


class QuotaStatus(StrEnum):
    """Best-effort view of remaining free quota for a provider/model."""

    AVAILABLE = "available"
    LOW = "low"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


class Health(StrEnum):
    """Circuit-breaker health of a provider/model."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Verdict(StrEnum):
    """Adversarial critique verdict."""

    PASS = "pass"
    REVISE = "revise"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A concrete, addressable free model within a provider.

    ``model_id`` is the *raw* provider model id (no ``provider/`` prefix), i.e.
    exactly what the provider's ``list_model_ids`` returns and what the reused
    ``stream_response`` path expects when the router is bypassed.
    """

    provider: str
    model_id: str
    family: str = "unknown"
    context_length: int | None = None
    supports_tools: bool = False
    supports_json: bool = False
    supports_reasoning: bool = False
    supports_images: bool = False
    cost_status: CostStatus = CostStatus.UNKNOWN
    quota_status: QuotaStatus = QuotaStatus.UNKNOWN
    health: Health = Health.HEALTHY
    last_verified: float | None = None

    @property
    def key(self) -> str:
        """Stable identifier used for stats, logs and de-duplication."""
        return f"{self.provider}/{self.model_id}"

    @property
    def eligible_free(self) -> bool:
        """True when the model is usable without any possible charge."""
        return self.cost_status in (CostStatus.VERIFIED_FREE, CostStatus.FREE_TIER)


@dataclass(frozen=True, slots=True)
class Proposal:
    """A single model's independent answer (Phase 3)."""

    model_key: str
    conclusion: str
    reasoning_summary: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class Review:
    """One reviewer's anonymous cross-review of all proposals (Phase 4)."""

    reviewer_key: str
    fatal_errors: list[str] = field(default_factory=list)
    material_errors: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    important_omissions: list[str] = field(default_factory=list)
    best_elements: list[str] = field(default_factory=list)
    ranking: list[str] = field(default_factory=list)
    recommended_synthesis: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RejectedArgument:
    argument: str
    reason: str


@dataclass(frozen=True, slots=True)
class Synthesis:
    """The synthesiser's merged answer (Phase 5)."""

    model_key: str
    final_answer: str
    consensus: list[str] = field(default_factory=list)
    material_disagreements: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    rejected_arguments: list[RejectedArgument] = field(default_factory=list)
    recommended_action: str = ""
    quality_score: float = 0.0


@dataclass(frozen=True, slots=True)
class Critique:
    """The adversarial critic's verdict on a synthesis (Phase 6)."""

    model_key: str
    critical_issues: list[str] = field(default_factory=list)
    material_issues: list[str] = field(default_factory=list)
    minor_issues: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    verdict: Verdict = Verdict.REVISE
    score: float = 0.0

    @property
    def is_informative(self) -> bool:
        """True when the critic delivered a usable, trustworthy verdict.

        Free models frequently emit the degenerate critique ``revise`` with a
        0.0 score and no issues at all — usually by copying the schema example.
        That carries no signal, so it must never drive the quality gate nor be
        reported as a real confidence.
        """
        if self.verdict is Verdict.PASS:
            return True
        return self.score > 0.0 or bool(
            self.critical_issues
            or self.material_issues
            or self.minor_issues
            or self.missing_evidence
        )


@dataclass(frozen=True, slots=True)
class Round:
    """A synthesis + critique pair produced during refinement (Phase 7)."""

    index: int
    synthesis: Synthesis
    critique: Critique
    elapsed_s: float = 0.0


@dataclass(frozen=True, slots=True)
class QuotaFailure:
    """A provider that could not be used, with a human-readable reason."""

    provider: str
    reason: str


@dataclass(frozen=True, slots=True)
class ResearchSummary:
    """SDK-free record of a web-research pass, serialised in the result.

    Kept in ``models`` (not ``research``) so the result stays dependency-free and
    both ``compact()`` and the on-disk report can serialise it without importing
    the httpx-backed research module.
    """

    backend: str
    queries: list[str] = field(default_factory=list)
    sources_fetched: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def unavailable(self) -> bool:
        """True when research ran but produced no usable sources."""
        return not self.sources_fetched


@dataclass(slots=True)
class CouncilResult:
    """The full deliberation outcome. The MCP/CLI expose a compact view of it."""

    task_type: TaskType
    depth: Depth
    proposals: list[Proposal] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    quota_failures: list[QuotaFailure] = field(default_factory=list)
    stop_reason: str = ""
    research: ResearchSummary | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def final_synthesis(self) -> Synthesis | None:
        return self.rounds[-1].synthesis if self.rounds else None

    @property
    def final_critique(self) -> Critique | None:
        return self.rounds[-1].critique if self.rounds else None

    def compact(self) -> dict[str, object]:
        """The small payload Claude Code receives, to avoid context bloat."""
        synthesis = self.final_synthesis
        critique = self.final_critique
        # Only surface a confidence the critic actually earned. A degenerate or
        # missing critique reports null, never a fabricated 0.0 the consumer
        # (Claude Code) might act on.
        if critique is not None and critique.is_informative:
            confidence: float | None = critique.score
            confidence_source = "critic"
        else:
            confidence = None
            confidence_source = "unavailable"
        elapsed_s = (
            round(self.finished_at - self.started_at, 1)
            if self.finished_at is not None
            else None
        )
        # Surface any research degradation to the consumer as an explicit
        # uncertainty — silently answering from stale memory is the failure mode
        # research exists to prevent.
        uncertainties = list(synthesis.uncertainties) if synthesis else []
        research: dict[str, object] | None = None
        if self.research is not None:
            research = {
                "backend": self.research.backend,
                "queries": list(self.research.queries),
                "sources_fetched": list(self.research.sources_fetched),
                "note": self.research.note,
            }
            if self.research.note:
                uncertainties.append(self.research.note)
        return {
            "answer": synthesis.final_answer if synthesis else "",
            "recommended_action": synthesis.recommended_action if synthesis else "",
            "material_disagreements": (
                list(synthesis.material_disagreements) if synthesis else []
            ),
            "uncertainties": uncertainties,
            "confidence": confidence,
            "confidence_source": confidence_source,
            "models_used": list(self.models_used),
            "providers_used": list(self.providers_used),
            "rounds": len(self.rounds),
            "quota_failures": [
                {"provider": qf.provider, "reason": qf.reason}
                for qf in self.quota_failures
            ],
            "task_type": self.task_type.value,
            "depth": self.depth.value,
            "stop_reason": self.stop_reason,
            "research": research,
            "elapsed_s": elapsed_s,
        }
