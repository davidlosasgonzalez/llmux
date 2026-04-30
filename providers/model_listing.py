"""Provider model-list response parsing helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from providers.exceptions import ModelListResponseError


def extract_openai_model_ids(payload: Any, *, provider_name: str) -> frozenset[str]:
    """Extract model ids from an OpenAI-compatible ``/models`` response."""
    data = _field(payload, "data")
    if not _is_sequence(data):
        raise _malformed(provider_name, "expected top-level data array")

    model_ids: set[str] = set()
    for item in data:
        model_id = _field(item, "id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise _malformed(provider_name, "expected every data item to include id")
        model_ids.add(model_id)

    if not model_ids:
        raise _malformed(provider_name, "response did not include any model ids")
    return frozenset(model_ids)


def extract_ollama_model_ids(payload: Any, *, provider_name: str) -> frozenset[str]:
    """Extract model ids from Ollama's native ``/api/tags`` response."""
    models = _field(payload, "models")
    if not _is_sequence(models):
        raise _malformed(provider_name, "expected top-level models array")

    model_ids: set[str] = set()
    for item in models:
        item_ids: list[str] = []
        for key in ("model", "name"):
            value = _field(item, key)
            if isinstance(value, str) and value.strip():
                item_ids.append(value)
        if not item_ids:
            raise _malformed(
                provider_name,
                "expected every models item to include model or name",
            )
        model_ids.update(item_ids)

    if not model_ids:
        raise _malformed(provider_name, "response did not include any model ids")
    return frozenset(model_ids)


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, str | bytes | bytearray
    )


def _malformed(provider_name: str, reason: str) -> ModelListResponseError:
    return ModelListResponseError(
        f"{provider_name} model-list response is malformed: {reason}"
    )
