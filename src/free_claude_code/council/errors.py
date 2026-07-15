"""Council-specific exceptions."""


class CouncilError(Exception):
    """Base class for council failures."""


class InsufficientFreeModelsError(CouncilError):
    """Raised when fewer free models are available than the council requires.

    Carries the per-provider reasons so the caller can explain which providers
    were exhausted, unauthenticated or excluded.
    """

    def __init__(self, message: str, *, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []


class DeliberationFailedError(CouncilError):
    """Raised when no usable proposal or synthesis could be produced."""
