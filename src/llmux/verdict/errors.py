"""Verdict-specific exceptions."""


class VerdictError(Exception):
    """Base class for verdict failures."""


class InsufficientFreeModelsError(VerdictError):
    """Raised when fewer free models are available than the verdict requires.

    Carries the per-provider reasons so the caller can explain which providers
    were exhausted, unauthenticated or excluded.
    """

    def __init__(self, message: str, *, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []


class DeliberationFailedError(VerdictError):
    """Raised when no usable proposal or synthesis could be produced."""
