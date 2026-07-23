"""Detection of upstream gateway error text served as a successful completion.

Some upstreams answer HTTP 200 with an error sentence as the assistant body
(observed 2026-07-23 via OpenRouter -> DeepSeek on ~100k-token prompts:
"Connect timeout, please try again later." with zero real output). Without
detection that text reaches the client as a normal assistant message, so no
retry or model fallback ever fires.

The guard holds leading text back while it can still be one of the known
error bodies. If the accumulated text diverges it is released unchanged; if
the finished completion equals a known error body the provider raises a
canonical failure instead of emitting it.
"""

from collections.abc import Sequence

# Full-completion matches only (whitespace-trimmed), never substrings, so a
# real answer that merely mentions one of these sentences is never affected.
UPSTREAM_ERROR_COMPLETIONS: tuple[str, ...] = (
    "Connect timeout, please try again later.",
)


class UpstreamErrorTextGuard:
    """Hold leading stream text while it can still be a known error body."""

    def __init__(self, patterns: Sequence[str] = UPSTREAM_ERROR_COMPLETIONS) -> None:
        self._patterns = tuple(patterns)
        self._held: list[str] = []
        self._armed = True

    def feed(self, text: str) -> str:
        """Return the text safe to emit now; hold it while a match is possible."""
        if not self._armed:
            return text
        self._held.append(text)
        candidate = "".join(self._held).strip()
        if any(pattern.startswith(candidate) for pattern in self._patterns):
            return ""
        return self.disarm()

    def disarm(self) -> str:
        """Stop matching and return any held text for normal emission."""
        self._armed = False
        held = "".join(self._held)
        self._held = []
        return held

    def matched(self) -> str | None:
        """Return the matched error body when the held text equals a pattern."""
        if not self._armed:
            return None
        candidate = "".join(self._held).strip()
        return candidate if candidate in self._patterns else None
