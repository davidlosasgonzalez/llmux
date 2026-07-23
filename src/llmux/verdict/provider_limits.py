"""Deliberation-role fan-out bias on top of the shared free-tier limit table.

The provider limit table itself (``DailyLimit``, ``daily_limit``, ``budget_class``)
lives in :mod:`llmux.core.provider_limits` so it can be shared with
:mod:`application.auto_router` without ``application`` depending on ``verdict``
(see ``tests/contracts/test_architecture_contracts.py``). This module adds the
role-aware fan-out penalty, which is deliberation-specific and has no meaning
outside the verdict orchestrator.
"""

from llmux.core.provider_limits import (
    HIGH_THROUGHPUT as HIGH_THROUGHPUT,
)
from llmux.core.provider_limits import (
    SCARCE as SCARCE,
)
from llmux.core.provider_limits import (
    UNKNOWN as UNKNOWN,
)
from llmux.core.provider_limits import (
    DailyLimit as DailyLimit,
)
from llmux.core.provider_limits import (
    budget_class as budget_class,
)
from llmux.core.provider_limits import (
    daily_limit as daily_limit,
)
from llmux.core.provider_limits import (
    max_request_tokens as max_request_tokens,
)

# Soft multiplier applied to a provider's selection score, by role. Fan-out
# phases (propose/review) avoid scarce providers so their tiny daily quota is
# saved for the high-value single calls (refine/critique), where they are
# allowed at full weight.
_FANOUT_ROLES = frozenset({"proponent", "reviewer"})
_SCARCE_FANOUT_PENALTY = 0.55


def budget_multiplier(provider: str, role: str) -> float:
    """Return a 0..1 score multiplier for using ``provider`` in ``role``."""
    if role in _FANOUT_ROLES and budget_class(provider) == SCARCE:
        return _SCARCE_FANOUT_PENALTY
    return 1.0
