"""FCC Council — a free-only, multi-model deliberation layer for FCC.

Consults several free cloud models, has them cross-review each other, synthesises
a merged answer and adversarially critiques it, refining until the result is good
enough. Designed to save Claude Opus/Fable calls and context on tasks where a
longer deliberation among free models yields a good-enough result.

The default regime is strictly free-only (``ALLOW_PAID_MODELS=false``): no paid
model is ever called and no unknown-cost model is ever selected.
"""

from .config import CouncilConfig, load_config
from .errors import (
    CouncilError,
    DeliberationFailedError,
    InsufficientFreeModelsError,
)
from .models import (
    CouncilResult,
    Depth,
    ModelRef,
    Privacy,
    TaskType,
)
from .service import CouncilService, ProviderValidation

__all__ = [
    "CouncilConfig",
    "CouncilError",
    "CouncilResult",
    "CouncilService",
    "DeliberationFailedError",
    "Depth",
    "InsufficientFreeModelsError",
    "ModelRef",
    "Privacy",
    "ProviderValidation",
    "TaskType",
    "load_config",
]
