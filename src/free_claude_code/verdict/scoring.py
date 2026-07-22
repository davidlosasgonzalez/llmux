"""Empirical model scoring for selection and synthesiser choice.

Quality, reliability, structured-output compliance and remaining quota dominate,
per the operator's priorities. Latency applies as a multiplicative penalty (not a
token bonus) so a pathologically slow model actually loses selection weight.
"""

from .capability import capability_prior, category_fit
from .models import Health, ModelRef, QuotaStatus
from .storage import ModelStats

# Weights sum to 1.0 for the quality component; speed is a small separate bonus.
_W_RELIABILITY = 0.35
_W_CROSS_REVIEW = 0.35
_W_JSON = 0.20
_W_SELECTED = 0.10

# How many observations before empirical quality fully outweighs the static
# capability prior (Bayesian shrinkage). Low so a handful of runs starts to
# matter, but the prior governs the cold start.
_PRIOR_STRENGTH = 8.0

_QUOTA_MULTIPLIER: dict[QuotaStatus, float] = {
    QuotaStatus.AVAILABLE: 1.0,
    QuotaStatus.UNKNOWN: 0.9,
    QuotaStatus.LOW: 0.5,
    QuotaStatus.EXHAUSTED: 0.0,
}

_HEALTH_MULTIPLIER: dict[Health, float] = {
    Health.HEALTHY: 1.0,
    Health.DEGRADED: 0.6,
    Health.UNAVAILABLE: 0.0,
}


def _selected_best_rate(stats: ModelStats) -> float:
    if stats.requests == 0:
        return 0.5
    return min(1.0, stats.selected_best / stats.requests)


def latency_penalty(stats: ModelStats) -> float:
    """Multiplicative penalty for slow models (1.0 = no penalty).

    No penalty up to ~20s average latency, then a linear ramp down to a 0.35
    floor at ~118s. A tiny additive speed *bonus* never mattered: a model that
    is 100x slower than its peers must actually lose selection weight, or it
    keeps winning refiner/critic roles and dragging deep runs into minutes.
    """
    latency = stats.avg_latency
    if latency <= 0.0:
        return 1.0
    return max(0.35, 1.0 - max(0.0, latency - 20.0) / 120.0)


def score_model(model: ModelRef, stats: ModelStats) -> float:
    """Return a 0..1 selection score, blending prior and empirical history.

    Cold (no requests) the score is the static capability prior, so strong models
    are chosen from the start. As observations accumulate, empirical quality takes
    over (Bayesian shrinkage on ``_PRIOR_STRENGTH``).
    """
    empirical = (
        _W_RELIABILITY * stats.reliability
        + _W_CROSS_REVIEW * stats.avg_cross_review
        + _W_JSON * stats.json_compliance
        + _W_SELECTED * _selected_best_rate(stats)
    )

    prior = capability_prior(model) * category_fit(model, stats.category)
    confidence = stats.requests / (stats.requests + _PRIOR_STRENGTH)
    quality = confidence * empirical + (1.0 - confidence) * prior

    # Penalise a model whose syntheses keep getting rejected.
    if stats.requests > 0:
        rejection_rate = min(1.0, stats.synthesis_rejected / stats.requests)
        quality *= 1.0 - 0.3 * rejection_rate

    quality *= latency_penalty(stats)

    quality *= _QUOTA_MULTIPLIER.get(model.quota_status, 0.9)
    quality *= _HEALTH_MULTIPLIER.get(model.health, 1.0)

    return max(0.0, min(1.0, quality))
