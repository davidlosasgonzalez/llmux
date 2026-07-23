"""LLMux Verdict — a free-only, multi-model deliberation layer for LLMux.

Consults several free cloud models, has them cross-review each other, synthesises
a merged answer and adversarially critiques it, refining until the result is good
enough. Designed to save Claude Opus/Fable calls and context on tasks where a
longer deliberation among free models yields a good-enough result.

The default regime is strictly free-only (``ALLOW_PAID_MODELS=false``): no paid
model is ever called and no unknown-cost model is ever selected.
"""

from .config import VerdictConfig, load_config
from .errors import (
    DeliberationFailedError,
    InsufficientFreeModelsError,
    VerdictError,
)
from .models import (
    Depth,
    ModelRef,
    Privacy,
    TaskType,
    VerdictResult,
)
from .service import ProviderValidation, VerdictService

__all__ = [
    "DeliberationFailedError",
    "Depth",
    "InsufficientFreeModelsError",
    "ModelRef",
    "Privacy",
    "ProviderValidation",
    "TaskType",
    "VerdictConfig",
    "VerdictError",
    "VerdictResult",
    "VerdictService",
    "load_config",
]
