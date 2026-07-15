"""Robust extraction of structured JSON from free-model text output.

Free models are inconsistent about JSON: they wrap it in ``` fences, prepend
prose, or emit trailing commentary. These helpers recover the first valid JSON
object and coerce it into the council's typed dataclasses without ever raising
on a missing or mistyped field — a malformed field degrades to a safe default so
one sloppy model cannot abort a deliberation.
"""

import json
import re
from typing import Any

from .models import (
    Critique,
    Proposal,
    RejectedArgument,
    Review,
    Synthesis,
    Verdict,
)

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first parseable top-level JSON object in ``text``.

    Tries fenced blocks first, then a brace-balanced scan. Returns ``None`` when
    nothing parses.
    """
    if not text:
        return None

    for match in _FENCE.finditer(text):
        parsed = _try_load(match.group(1))
        if parsed is not None:
            return parsed

    # Brace-balanced scan for the first complete object.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    parsed = _try_load(text[start : index + 1])
                    if parsed is not None:
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _try_load(blob: str) -> dict[str, Any] | None:
    try:
        value = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def parse_proposal(model_key: str, text: str) -> Proposal | None:
    data = extract_json_object(text)
    if data is None:
        # Fall back to using the whole text as the conclusion so a model that
        # answered well but ignored the JSON contract is not wholly discarded.
        stripped = text.strip()
        if not stripped:
            return None
        return Proposal(model_key=model_key, conclusion=stripped)
    return Proposal(
        model_key=model_key,
        conclusion=_text(data.get("conclusion")),
        reasoning_summary=_str_list(data.get("reasoning_summary")),
        assumptions=_str_list(data.get("assumptions")),
        evidence=_str_list(data.get("evidence")),
        risks=_str_list(data.get("risks")),
        unknowns=_str_list(data.get("unknowns")),
        confidence=_confidence(data.get("confidence")),
    )


def parse_review(reviewer_key: str, text: str) -> Review | None:
    data = extract_json_object(text)
    if data is None:
        return None
    return Review(
        reviewer_key=reviewer_key,
        fatal_errors=_str_list(data.get("fatal_errors")),
        material_errors=_str_list(data.get("material_errors")),
        unsupported_claims=_str_list(data.get("unsupported_claims")),
        important_omissions=_str_list(data.get("important_omissions")),
        best_elements=_str_list(data.get("best_elements")),
        ranking=[label.strip().upper()[:1] for label in _str_list(data.get("ranking"))],
        recommended_synthesis=_str_list(data.get("recommended_synthesis")),
    )


def _rejected_arguments(value: Any) -> list[RejectedArgument]:
    if not isinstance(value, list):
        return []
    return [
        RejectedArgument(
            argument=_text(item.get("argument")),
            reason=_text(item.get("reason")),
        )
        for item in value
        if isinstance(item, dict)
    ]


def parse_synthesis(model_key: str, text: str) -> Synthesis | None:
    data = extract_json_object(text)
    if data is None:
        stripped = text.strip()
        if not stripped:
            return None
        return Synthesis(model_key=model_key, final_answer=stripped)
    return Synthesis(
        model_key=model_key,
        final_answer=_text(data.get("final_answer")),
        consensus=_str_list(data.get("consensus")),
        material_disagreements=_str_list(data.get("material_disagreements")),
        uncertainties=_str_list(data.get("uncertainties")),
        rejected_arguments=_rejected_arguments(data.get("rejected_arguments")),
        recommended_action=_text(data.get("recommended_action")),
        quality_score=_confidence(data.get("quality_score")),
    )


def _verdict(value: Any) -> Verdict:
    text = _text(value).strip().lower()
    for candidate in Verdict:
        if candidate.value == text:
            return candidate
    return Verdict.REVISE


def parse_critique(model_key: str, text: str) -> Critique | None:
    data = extract_json_object(text)
    if data is None:
        return None
    return Critique(
        model_key=model_key,
        critical_issues=_str_list(data.get("critical_issues")),
        material_issues=_str_list(data.get("material_issues")),
        minor_issues=_str_list(data.get("minor_issues")),
        missing_evidence=_str_list(data.get("missing_evidence")),
        verdict=_verdict(data.get("verdict")),
        score=_confidence(data.get("score")),
    )
