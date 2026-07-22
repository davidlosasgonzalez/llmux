"""Static capability priors so cold-start selection prefers strong models.

With no empirical history every model scores ~0.5, which lets the selector pick a
weak 8B model over a 120B+ reasoning model. This module derives a deterministic
prior from signals we can read off the model id alone — parameter size, whether
it is a reasoning/coder model, and coarse family reputation — plus a per-category
role fit. :mod:`scoring` blends this prior with empirical stats, weighting the
prior heavily while the model is still unproven and fading it out as real
observations accumulate.

No external data is needed, so this is robust even to model names we have never
seen (it falls back to size and family heuristics).
"""

import re

from .models import ModelRef

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b")

# Coarse reputation for models whose id carries no parameter count (e.g. gemini,
# glm). Keyed by family or a name token; higher = stronger default.
_FAMILY_BASE: dict[str, float] = {
    "deepseek": 0.85,
    "nemotron": 0.82,
    "gpt-oss": 0.82,
    "glm": 0.80,
    "qwen": 0.78,
    "mistral": 0.70,
    "llama": 0.68,
    "gemma": 0.60,
    "cohere-command": 0.66,
}

_SMALL_TOKENS = ("nano", "mini", "-lite", "lite-", "tiny", "-small", "small-", "guard")
_CODER_TOKENS = ("coder", "code", "devstral", "codestral", "codellama", "starcoder")


def size_billions(model_id: str) -> float | None:
    """Largest parameter count (in billions) parsed from a model id, if any.

    Uses the total-parameter figure for MoE names like ``550b-a55b`` (555 vs 55),
    which reflects capability better than active params.
    """
    matches = _SIZE_RE.findall(model_id.lower())
    if not matches:
        return None
    return max(float(m) for m in matches)


def _size_score(size: float) -> float:
    """Map a parameter count (billions) to a 0..1 capability base."""
    if size < 5:
        return 0.30
    if size < 10:
        return 0.40
    if size < 25:
        return 0.55
    if size < 50:
        return 0.65
    if size < 80:
        return 0.74
    if size < 130:
        return 0.83
    if size < 260:
        return 0.88
    if size < 420:
        return 0.92
    return 0.96


def _family_base(model: ModelRef) -> float:
    lowered = model.model_id.lower()
    # Gemini has no size token; grade by tier word.
    if "gemini" in lowered or model.family == "gemini":
        if "pro" in lowered:
            return 0.90
        if "flash-lite" in lowered or "flash-8b" in lowered:
            return 0.60
        if "flash" in lowered:
            return 0.78
        return 0.72
    return _FAMILY_BASE.get(model.family, 0.50)


def capability_prior(model: ModelRef) -> float:
    """Return a 0..1 static capability estimate for ``model``."""
    lowered = model.model_id.lower()
    size = size_billions(model.model_id)
    base = _size_score(size) if size is not None else _family_base(model)

    if model.supports_reasoning:
        base += 0.05
    if any(token in lowered for token in _SMALL_TOKENS):
        base -= 0.15

    return max(0.0, min(1.0, base))


def category_fit(model: ModelRef, category: str) -> float:
    """Multiplier (~0.9..1.1) boosting models suited to a task category."""
    lowered = model.model_id.lower()
    is_coder = any(token in lowered for token in _CODER_TOKENS)
    is_reasoner = model.supports_reasoning

    if category in ("software_engineering", "code_review", "debugging"):
        if is_coder:
            return 1.10
        if is_reasoner:
            return 1.03
    if is_reasoner and category in (
        "architecture",
        "planning",
        "research",
        "general_reasoning",
        "adversarial_review",
    ):
        return 1.05
    return 1.0
