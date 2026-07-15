"""Empirical model scoring for selection and synthesiser choice.

Speed is included but weighted low. Quality, reliability, structured-output
compliance and remaining quota dominate, per the operator's priorities.
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


def _speed_bonus(stats: ModelStats) -> float:
    """Small reward for lower latency; capped so it cannot dominate quality."""
    latency = stats.avg_latency
    if latency <= 0.0:
        return 0.0
    # 0 at ~30s+, up to ~0.03 for sub-second replies.
    return max(0.0, min(0.03, 0.03 * (1.0 - min(latency, 30.0) / 30.0)))


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

    quality += _speed_bonus(stats)

    quality *= _QUOTA_MULTIPLIER.get(model.quota_status, 0.9)
    quality *= _HEALTH_MULTIPLIER.get(model.health, 1.0)

    return max(0.0, min(1.0, quality))
