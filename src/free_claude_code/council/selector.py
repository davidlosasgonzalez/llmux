"""Phase 2: diversity-aware model selection.

Given free-eligible candidates and their empirical scores, pick a small, diverse
panel. Diversity matters because near-identical models produce correlated errors
that cross-review cannot catch.
"""

import re
from collections.abc import Callable

from .config import CouncilConfig
from .models import ModelRef
from .provider_limits import budget_multiplier
from .scoring import score_model
from .storage import ModelStats

# How hard to punish reusing a provider or a model family already on the panel.
_PROVIDER_PENALTY = 0.15
_FAMILY_PENALTY = 0.25
# Same base model id from a different provider (e.g. gpt-oss-120b on two hosts).
_SAME_MODEL_PENALTY = 0.40

StatsLookup = Callable[[str, str], ModelStats]

_VERSION_NOISE = re.compile(r"[-_:.]?(v?\d+(?:\.\d+)*|latest|preview|instruct|it)\b")


def _normalise_model_base(model_id: str) -> str:
    """Collapse a model id to a coarse identity for near-duplicate detection."""
    tail = model_id.split("/")[-1].lower()
    tail = _VERSION_NOISE.sub("", tail)
    return re.sub(r"[-_:.]+", "", tail).strip()


def select_models(
    candidates: list[ModelRef],
    stats_lookup: StatsLookup,
    config: CouncilConfig,
    *,
    category: str,
    count: int,
    role: str = "proponent",
) -> list[ModelRef]:
    """Return up to ``count`` diverse, high-scoring models.

    Greedy: repeatedly pick the candidate whose base score, minus penalties for
    provider/family/base-model overlap with the already-chosen panel, is
    highest. ``role`` applies a budget bias so fan-out phases avoid scarce
    providers. Ties break by provider_priority order then model key for
    determinism.
    """
    if count <= 0 or not candidates:
        return []

    priority_index = {p: i for i, p in enumerate(config.provider_priority)}

    base_scores: dict[str, float] = {}
    for model in candidates:
        stats = stats_lookup(model.key, category)
        base_scores[model.key] = score_model(model, stats) * budget_multiplier(
            model.provider, role
        )

    remaining = list(candidates)
    chosen: list[ModelRef] = []
    chosen_providers: dict[str, int] = {}
    chosen_families: dict[str, int] = {}
    chosen_bases: dict[str, int] = {}

    limit = min(count, config.maximum_models, len(candidates))
    while remaining and len(chosen) < limit:
        best: ModelRef | None = None
        best_adjusted = float("-inf")
        best_tiebreak: tuple[int, str] = (10_000, "")
        for model in remaining:
            adjusted = base_scores[model.key]
            adjusted -= _PROVIDER_PENALTY * chosen_providers.get(model.provider, 0)
            adjusted -= _FAMILY_PENALTY * chosen_families.get(model.family, 0)
            adjusted -= _SAME_MODEL_PENALTY * chosen_bases.get(
                _normalise_model_base(model.model_id), 0
            )
            tiebreak = (
                priority_index.get(model.provider, 9_999),
                model.key,
            )
            if adjusted > best_adjusted or (
                adjusted == best_adjusted and tiebreak < best_tiebreak
            ):
                best = model
                best_adjusted = adjusted
                best_tiebreak = tiebreak

        if best is None:
            break
        chosen.append(best)
        remaining.remove(best)
        chosen_providers[best.provider] = chosen_providers.get(best.provider, 0) + 1
        chosen_families[best.family] = chosen_families.get(best.family, 0) + 1
        base = _normalise_model_base(best.model_id)
        chosen_bases[base] = chosen_bases.get(base, 0) + 1

    return chosen
