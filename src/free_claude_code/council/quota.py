"""Failure classification and per-provider circuit breaking.

Implementation lives in :mod:`free_claude_code.core.quota` so the own-agent
harness and council share one tracker. This module re-exports the public API
for existing council imports.
"""

from free_claude_code.core.quota import (
    FailureKind,
    QuotaTracker,
    classify_failure,
    retry_after_seconds,
)

# Re-export FailureKind from core for callers that historically imported it
# alongside QuotaTracker from this module. Prefer ``council.models.FailureKind``
# (alias) or ``core.quota.FailureKind`` in new code.
__all__ = [
    "FailureKind",
    "QuotaTracker",
    "classify_failure",
    "retry_after_seconds",
]
