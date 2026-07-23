"""Static capability priors so cold-start selection prefers strong models.

With no empirical history every model scores ~0.5, which lets the selector pick a
weak 8B model over a 120B+ reasoning model. This module adapts the shared,
dependency-free heuristics in :mod:`llmux.core.model_capability` to
:class:`ModelRef`, plus a per-category role fit. :mod:`scoring` blends this prior
with empirical stats, weighting the prior heavily while the model is still
unproven and fading it out as real observations accumulate.

No external data is needed, so this is robust even to model names we have never
seen (it falls back to size and family heuristics).
"""

from llmux.core.model_capability import (
    capability_prior as _capability_prior,
)
from llmux.core.model_capability import (
    category_fit as _category_fit,
)
from llmux.core.model_capability import (
    size_billions as size_billions,
)

from .models import ModelRef

__all__ = ["capability_prior", "category_fit", "size_billions"]


def capability_prior(model: ModelRef) -> float:
    """Return a 0..1 static capability estimate for ``model``."""
    return _capability_prior(
        model.model_id, model.family, supports_reasoning=model.supports_reasoning
    )


def category_fit(model: ModelRef, category: str) -> float:
    """Multiplier (~0.9..1.1) boosting models suited to a task category."""
    return _category_fit(
        model.model_id, supports_reasoning=model.supports_reasoning, category=category
    )
