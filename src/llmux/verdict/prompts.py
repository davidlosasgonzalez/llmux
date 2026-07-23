"""Prompt construction for every deliberation phase.

Each builder returns ``(system, user)``. Prompts demand a single JSON object and
a short, verifiable rationale — never hidden chain-of-thought — so outputs stay
auditable and cheap to parse.
"""

from .models import Critique, Proposal, Review, Synthesis, TaskType

_JSON_RULES = (
    "Respond with EXACTLY ONE JSON object and nothing else. No markdown fences, "
    "no prose before or after. Use double quotes. Keep every list concise."
)

_CITATION_CONTRACT = (
    "Citation rule: you may only cite a URL if it appears in AUTHORISED CONTEXT "
    "under FUENTES VERIFICADAS. Never cite a URL from memory as if it were "
    "verified — if you mention one anyway, label it "
    "'(URL recordada, no verificada en esta ejecución)'."
)


def propose_prompt(
    question: str,
    task_type: TaskType,
    *,
    criteria: str = "",
    context: str = "",
) -> tuple[str, str]:
    """Phase 3 — an independent proposal from one model."""
    system = (
        "You are an expert contributor on a panel solving a "
        f"{task_type.value.replace('_', ' ')} task. Solve it INDEPENDENTLY. "
        "Do NOT assume other panelists agree with you, and do NOT assume any "
        "consensus exists. Base every claim on the given context or clearly "
        "label it an assumption. " + _CITATION_CONTRACT + " " + _JSON_RULES
    )
    schema = (
        "{\n"
        '  "conclusion": "string — your answer",\n'
        '  "reasoning_summary": ["short verifiable steps"],\n'
        '  "assumptions": ["assumptions you made"],\n'
        '  "evidence": ["facts from the context supporting you"],\n'
        '  "risks": ["ways your answer could be wrong or harmful"],\n'
        '  "unknowns": ["things you could not determine"],\n'
        '  "confidence": "<0.0-1.0>"\n'
        "}"
    )
    parts = [f"TASK:\n{question}"]
    if criteria:
        parts.append(f"EVALUATION CRITERIA:\n{criteria}")
    if context:
        parts.append(f"AUTHORISED CONTEXT (only this may be relied upon):\n{context}")
    parts.append(f"Return this JSON shape:\n{schema}")
    return system, "\n\n".join(parts)


def review_prompt(
    question: str,
    labeled_proposals: dict[str, str],
) -> tuple[str, str]:
    """Phase 4 — anonymous cross-review of all proposals.

    ``labeled_proposals`` maps an anonymous label (A, B, C, ...) to the proposal
    text. The author's provider/model is never revealed.
    """
    system = (
        "You are a rigorous, impartial reviewer. The proposals below are "
        "anonymous; judge only their content, never their style or origin. "
        "Be specific: cite the concrete claim you are flagging. " + _JSON_RULES
    )
    schema = (
        "{\n"
        '  "fatal_errors": ["errors that make a proposal unusable"],\n'
        '  "material_errors": ["errors that change the decision"],\n'
        '  "unsupported_claims": ["claims lacking evidence"],\n'
        '  "important_omissions": ["missing considerations"],\n'
        '  "best_elements": ["the strongest ideas across proposals"],\n'
        '  "ranking": ["A", "C", "B"],\n'
        '  "recommended_synthesis": ["elements a final answer should combine"]\n'
        "}"
    )
    blocks = [f"PROPOSAL {label}:\n{text}" for label, text in labeled_proposals.items()]
    user = (
        f"TASK:\n{question}\n\n"
        + "\n\n".join(blocks)
        + f"\n\nReview all proposals. Rank them best-first. Return this JSON:\n{schema}"
    )
    return system, user


def _format_proposal(label: str, proposal: Proposal) -> str:
    lines = [f"PROPOSAL {label}:", proposal.conclusion]
    if proposal.risks:
        lines.append("Risks: " + "; ".join(proposal.risks))
    if proposal.assumptions:
        lines.append("Assumptions: " + "; ".join(proposal.assumptions))
    return "\n".join(lines)


def _format_review(index: int, review: Review) -> str:
    lines = [f"REVIEW {index}:"]
    if review.ranking:
        lines.append("Ranking: " + " > ".join(review.ranking))
    if review.material_errors:
        lines.append("Material errors: " + "; ".join(review.material_errors))
    if review.best_elements:
        lines.append("Best elements: " + "; ".join(review.best_elements))
    if review.recommended_synthesis:
        lines.append(
            "Recommended synthesis: " + "; ".join(review.recommended_synthesis)
        )
    return "\n".join(lines)


_FACTUAL_DISAGREEMENT_RULE = (
    "If panelists disagree on a verifiable fact (a version, limit, price, date, "
    "or API behaviour), do NOT resolve it by majority vote nor by appealing to "
    "'the documentation says' unless the AUTHORISED CONTEXT contains VERIFIED "
    "SOURCES that settle it. Absent such evidence, keep the conflict in "
    "material_disagreements and present BOTH values in the answer, marking which "
    "one lacks verification. A shared but unsourced belief is not evidence."
)


def synthesis_prompt(
    question: str,
    proposals: dict[str, Proposal],
    reviews: list[Review],
    *,
    prior_critique: Critique | None = None,
    context: str = "",
) -> tuple[str, str]:
    """Phase 5 / 7 — merge proposals and reviews into a final answer.

    When ``prior_critique`` is present this is a refinement round: the critique's
    issues must be addressed rather than repeated. ``context`` carries any
    verified research sources so the synthesiser can settle factual conflicts on
    evidence instead of consensus.
    """
    system = (
        "You are the synthesiser. Produce the best possible answer by combining "
        "the strongest, best-supported elements and discarding weak ones. Do not "
        "invent facts. Attribute nothing to specific panelists. "
        + _FACTUAL_DISAGREEMENT_RULE
        + " "
        + _CITATION_CONTRACT
        + " "
        + _JSON_RULES
    )
    schema = (
        "{\n"
        '  "final_answer": "string",\n'
        '  "consensus": ["points all sources agree on"],\n'
        '  "material_disagreements": ["unresolved substantive conflicts"],\n'
        '  "uncertainties": ["what remains unknown"],\n'
        '  "rejected_arguments": [{"argument": "string", "reason": "string"}],\n'
        '  "recommended_action": "string",\n'
        '  "quality_score": "<0.0-1.0>"\n'
        "}"
    )
    proposal_blocks = [
        _format_proposal(label, proposal) for label, proposal in proposals.items()
    ]
    review_blocks = [
        _format_review(index + 1, review) for index, review in enumerate(reviews)
    ]
    parts = [f"TASK:\n{question}"]
    if context:
        parts.append(f"AUTHORISED CONTEXT (only this may be relied upon):\n{context}")
    parts.extend([*proposal_blocks, *review_blocks])
    if prior_critique is not None:
        issues = (
            prior_critique.critical_issues
            + prior_critique.material_issues
            + prior_critique.missing_evidence
        )
        if issues:
            parts.append(
                "A prior critique raised these issues. Resolve each one; do not "
                "merely restate the previous answer:\n- " + "\n- ".join(issues)
            )
    parts.append(f"Return this JSON:\n{schema}")
    return system, "\n\n".join(parts)


def critique_prompt(
    question: str, synthesis: Synthesis, *, context: str = ""
) -> tuple[str, str]:
    """Phase 6 — adversarial critique that tries to break the synthesis.

    ``context`` carries any verified research sources so the critic can flag
    factual claims (versions, limits, prices) that the sources contradict — or
    that lack any source at all.
    """
    system = (
        "You are an adversarial critic. Try to prove the synthesis below is "
        "wrong or incomplete. Do NOT propose cosmetic changes and do NOT restate "
        "the answer. Only report issues that could change the decision: false "
        "facts, unproven assumptions, contradictions, ignored risks, superior "
        "alternatives, conclusions not supported by the evidence, or claims about "
        "versions/limits/prices/dates that have no verified source in the "
        "AUTHORISED CONTEXT (or that contradict one that is present).\n"
        "Your verdict and score MUST be consistent and honestly computed: "
        "'pass' requires a high score (>= 0.85) and an empty issue list; "
        "'revise' or 'reject' requires at least one concrete critical or material "
        "issue that justifies it. Never return an empty critique — if you truly "
        "find no defects, that is a 'pass' with a high score, not a 0.0. "
        "The values in the shape below are placeholders; replace every one, "
        "including the score, with your own judgement. " + _JSON_RULES
    )
    schema = (
        "{\n"
        '  "critical_issues": ["decision-changing defects"],\n'
        '  "material_issues": ["significant but non-fatal defects"],\n'
        '  "minor_issues": ["small issues"],\n'
        '  "missing_evidence": ["claims that need support"],\n'
        '  "verdict": "pass | revise | reject",\n'
        '  "score": "<0.0-1.0>"\n'
        "}"
    )
    disagreements = (
        "\nKnown disagreements: " + "; ".join(synthesis.material_disagreements)
        if synthesis.material_disagreements
        else ""
    )
    context_block = (
        f"AUTHORISED CONTEXT (only this may be relied upon):\n{context}\n\n"
        if context
        else ""
    )
    user = (
        f"TASK:\n{question}\n\n"
        f"{context_block}"
        f"SYNTHESIS TO ATTACK:\n{synthesis.final_answer}\n"
        f"Recommended action: {synthesis.recommended_action}{disagreements}\n\n"
        f"Return this JSON:\n{schema}"
    )
    return system, user
