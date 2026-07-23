"""Static, dependency-free model capability heuristics.

Derives a deterministic capability signal from a raw ``provider_model_id``
alone (parameter size, family reputation, coder/reasoner hints) with no
network access and no empirical history. Shared by :mod:`verdict.capability`
(which adapts these to its ``ModelRef`` type for empirical scoring) and
:mod:`application.auto_router` (which cannot depend on ``verdict``, see
``tests/contracts/test_architecture_contracts.py``), so the heuristics live
here once instead of being duplicated per consumer.
"""

import re

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b")

# Coarse reputation for models whose id carries no parameter count (e.g. gemini,
# glm). Keyed by family or a name token; higher = stronger default.
_FAMILY_BASE: dict[str, float] = {
    "kimi": 0.86,
    "deepseek": 0.85,
    "nemotron": 0.82,
    "gpt-oss": 0.82,
    "glm": 0.80,
    "qwen": 0.78,
    "minimax": 0.78,
    "mistral": 0.70,
    "llama": 0.68,
    "gemma": 0.60,
    "cohere-command": 0.66,
}

_SMALL_TOKENS = ("nano", "mini", "-lite", "lite-", "tiny", "-small", "small-", "guard")
_CODER_TOKENS = ("coder", "code", "devstral", "codestral", "codellama", "starcoder")
_REASONING_TOKENS = ("reason", "think", "r1", "o1", "deepseek", "nemotron")

# Coarse family inference so callers can enforce diversity or grade reputation.
# Ordered so more specific tokens win (gpt-oss before gpt, gemma before gemini
# prefix clashes).
_FAMILY_TOKENS: tuple[tuple[str, str], ...] = (
    ("gpt-oss", "gpt-oss"),
    ("nemotron", "nemotron"),
    ("deepseek", "deepseek"),
    ("qwen", "qwen"),
    ("llama", "llama"),
    ("gemma", "gemma"),
    ("gemini", "gemini"),
    ("mistral", "mistral"),
    ("mixtral", "mistral"),
    ("devstral", "mistral"),
    ("codestral", "mistral"),
    ("glm", "glm"),
    ("kimi", "kimi"),
    ("minimax", "minimax"),
    ("command", "cohere-command"),
    ("phi", "phi"),
    ("yi", "yi"),
)


# Conservative context-window floors by name token, ordered so more specific
# tokens win. Static on purpose: catalog data is not available to every caller,
# and a conservative floor only shifts oversized prompts toward larger-window
# candidates. Unknown models return None and are never filtered on context.
_CONTEXT_WINDOW_TOKENS: tuple[tuple[str, int], ...] = (
    ("gemini", 1_048_576),
    ("minimax", 1_000_000),
    ("nemotron-3-ultra", 1_000_000),
    ("kimi", 262_144),
    ("command-a", 262_144),
    ("deepseek", 131_072),
    ("nemotron", 131_072),
    ("gpt-oss", 131_072),
    ("llama", 131_072),
    ("qwen", 131_072),
    ("glm", 131_072),
    ("mistral", 131_072),
    ("command", 131_072),
)


def known_context_window(model_id: str) -> int | None:
    """Approximate context window (tokens) for a model id, or None if unknown.

    Pass the model name without its provider prefix: a prefix such as
    ``gemini/`` would otherwise match the ``gemini`` family token and claim
    a 1M window for any model hosted there.
    """
    lowered = model_id.lower()
    for token, window in _CONTEXT_WINDOW_TOKENS:
        if token in lowered:
            return window
    return None


def size_billions(model_id: str) -> float | None:
    """Largest parameter count (in billions) parsed from a model id, if any.

    Uses the total-parameter figure for MoE names like ``550b-a55b`` (555 vs 55),
    which reflects capability better than active params.
    """
    matches = _SIZE_RE.findall(model_id.lower())
    if not matches:
        return None
    return max(float(m) for m in matches)


def family_of(model_id: str) -> str:
    """Coarse model family inferred from a raw model id."""
    lowered = model_id.lower()
    for token, family in _FAMILY_TOKENS:
        if token in lowered:
            return family
    return "unknown"


def is_coder_model(model_id: str) -> bool:
    """True when the model id carries a coder/code-tuned hint."""
    lowered = model_id.lower()
    return any(token in lowered for token in _CODER_TOKENS)


def is_reasoning_model(model_id: str) -> bool:
    """True when the model id carries a reasoning-tuned hint."""
    lowered = model_id.lower()
    return any(token in lowered for token in _REASONING_TOKENS)


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


def _family_base(model_id: str, family: str) -> float:
    lowered = model_id.lower()
    # Gemini has no size token; grade by tier word.
    if "gemini" in lowered or family == "gemini":
        if "pro" in lowered:
            return 0.90
        if "flash-lite" in lowered or "flash-8b" in lowered:
            return 0.60
        if "flash" in lowered:
            return 0.78
        return 0.72
    return _FAMILY_BASE.get(family, 0.50)


def capability_prior(model_id: str, family: str, *, supports_reasoning: bool) -> float:
    """Return a 0..1 static capability estimate for a model."""
    lowered = model_id.lower()
    size = size_billions(model_id)
    base = _size_score(size) if size is not None else _family_base(model_id, family)

    if supports_reasoning:
        base += 0.05
    if any(token in lowered for token in _SMALL_TOKENS):
        base -= 0.15

    return max(0.0, min(1.0, base))


def category_fit(model_id: str, *, supports_reasoning: bool, category: str) -> float:
    """Multiplier (~0.9..1.1) boosting models suited to a task category."""
    is_coder = is_coder_model(model_id)
    is_reasoner = supports_reasoning

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
