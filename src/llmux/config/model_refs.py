"""Provider-prefixed model reference helpers."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ConfiguredChatModelRef:
    """A unique configured chat model reference and the env keys that set it."""

    model_ref: str
    provider_id: str
    model_id: str
    sources: tuple[str, ...]


class ChatModelConfig(Protocol):
    model: str
    model_fable: str | None
    model_opus: str | None
    model_sonnet: str | None
    model_haiku: str | None


def parse_provider_type(model_ref: str) -> str:
    """Extract provider type from any 'provider/model' string."""

    return model_ref.split("/", 1)[0]


def parse_model_name(model_ref: str) -> str:
    """Extract model name from any 'provider/model' string."""

    return model_ref.split("/", 1)[1]


def parse_model_fallbacks(raw: str) -> list[str]:
    """Split a comma-separated ``MODEL_FALLBACKS`` setting into model refs."""

    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_context_window_overrides(raw: str) -> dict[str, int]:
    """Parse ``CONTEXT_WINDOW_OVERRIDES``: comma-separated ``key=tokens`` pairs.

    Each key is either a full ``provider/model`` ref or a bare model name,
    matched against both forms when filtering fallback candidates by context
    window. Raises ``ValueError`` on a malformed pair or a non-positive token
    count.
    """

    overrides: dict[str, int] = {}
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        key, separator, value = entry.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(
                f"Invalid CONTEXT_WINDOW_OVERRIDES entry {entry!r}; "
                "expected 'model_or_ref=tokens'"
            )
        try:
            tokens = int(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"Invalid CONTEXT_WINDOW_OVERRIDES token count in {entry!r}: "
                "must be an integer"
            ) from exc
        if tokens <= 0:
            raise ValueError(
                f"Invalid CONTEXT_WINDOW_OVERRIDES token count in {entry!r}: "
                "must be positive"
            )
        overrides[key.strip()] = tokens
    return overrides


def configured_chat_model_refs(
    settings: ChatModelConfig,
) -> tuple[ConfiguredChatModelRef, ...]:
    """Return unique configured chat provider/model refs with source env keys."""

    candidates = (
        ("MODEL", settings.model),
        ("MODEL_FABLE", settings.model_fable),
        ("MODEL_OPUS", settings.model_opus),
        ("MODEL_SONNET", settings.model_sonnet),
        ("MODEL_HAIKU", settings.model_haiku),
    )
    sources_by_ref: dict[str, list[str]] = {}
    for source, model_ref in candidates:
        if model_ref is None:
            continue
        sources_by_ref.setdefault(model_ref, []).append(source)

    return tuple(
        ConfiguredChatModelRef(
            model_ref=model_ref,
            provider_id=parse_provider_type(model_ref),
            model_id=parse_model_name(model_ref),
            sources=tuple(sources),
        )
        for model_ref, sources in sources_by_ref.items()
    )
