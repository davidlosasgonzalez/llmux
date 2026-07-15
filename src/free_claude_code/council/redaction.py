"""Secret redaction, privacy modes and filesystem-root allowlisting.

Free providers may log or reuse whatever we send them, so by default every
piece of caller-supplied context passes through :func:`redact` before it leaves
the machine. Path access is constrained to an explicit allowlist so the MCP
tool cannot be tricked into exfiltrating arbitrary files.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import Privacy

_PLACEHOLDER = "[REDACTED]"

# Ordered most-specific-first so broad patterns do not shadow precise ones.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "url_credentials",
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"),
    ),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9._\-]{16,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z._\-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    (
        "assignment_secret",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API[_-]?KEY|COOKIE)[A-Z0-9_]*)"
            r"\s*[:=]\s*['\"]?([^\s'\"]{6,})",
        ),
    ),
    (
        "cookie_header",
        re.compile(r"(?i)\bCookie\s*:\s*[^\r\n]{8,}"),
    ),
)


def redact(text: str) -> str:
    """Return ``text`` with any recognised secret material masked."""
    if not text:
        return text
    result = text
    for name, pattern in _PATTERNS:
        if name == "url_credentials":
            result = pattern.sub(lambda m: f"{m.group(1)}{_PLACEHOLDER}@", result)
        elif name == "assignment_secret":
            result = pattern.sub(lambda m: f"{m.group(1)}={_PLACEHOLDER}", result)
        else:
            result = pattern.sub(_PLACEHOLDER, result)
    return result


def contains_secret(text: str) -> bool:
    """True when ``text`` still holds a secret (ignoring redaction markers)."""
    scrubbed = text.replace(_PLACEHOLDER, "")
    return any(pattern.search(scrubbed) for _, pattern in _PATTERNS)


def apply_privacy(text: str, privacy: Privacy) -> str:
    """Apply the requested privacy mode to a payload bound for a provider.

    ``PUBLIC`` sends verbatim (the caller takes responsibility), ``REDACTED``
    (the default for cloud) masks secrets, and ``LOCAL_ONLY`` is enforced at the
    selection layer, not here.
    """
    if privacy is Privacy.PUBLIC:
        return text
    return redact(text)


def safe_for_log(text: str, *, limit: int = 240) -> str:
    """Redact and truncate a string so it is safe to write to logs."""
    cleaned = redact(text).replace("\n", " ")
    if len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


@dataclass(frozen=True, slots=True)
class PathPolicy:
    """Allowlist gate for reading caller-referenced files."""

    roots: tuple[Path, ...]
    max_bytes: int = 200_000

    @classmethod
    def from_paths(
        cls, roots: Iterable[str | Path], *, max_bytes: int = 200_000
    ) -> PathPolicy:
        resolved = tuple(Path(r).expanduser().resolve() for r in roots)
        return cls(roots=resolved, max_bytes=max_bytes)

    def is_allowed(self, path: Path) -> bool:
        """True when ``path`` resolves inside one of the allowed roots."""
        try:
            candidate = path.expanduser().resolve()
        except OSError, RuntimeError:
            return False
        for root in self.roots:
            if candidate == root or root in candidate.parents:
                return True
        return False

    def read_text(self, path: str | Path) -> str:
        """Read a file only if it lives under an allowed root and fits the cap.

        Raises :class:`PermissionError` for out-of-root paths and
        :class:`ValueError` when the file exceeds ``max_bytes``.
        """
        target = Path(path)
        if not self.is_allowed(target):
            raise PermissionError(f"Path is outside the allowed roots: {target}")
        resolved = target.expanduser().resolve()
        size = resolved.stat().st_size
        if size > self.max_bytes:
            raise ValueError(
                f"File {resolved} is {size} bytes, exceeds cap {self.max_bytes}"
            )
        return resolved.read_text(encoding="utf-8", errors="replace")
