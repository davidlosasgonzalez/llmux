"""Request builder for Google Gemini API (AI Studio OpenAI-compatible chat completions)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from loguru import logger

from core.anthropic import ReasoningReplayMode, build_base_request_body
from core.anthropic.conversion import OpenAIConversionError
from providers.exceptions import InvalidRequestError


def _ensure_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    nested: dict[str, Any] = {}
    container[key] = nested
    return nested


def _apply_thinking_config(extra_body: dict[str, Any]) -> None:
    # OpenAI's SDK merges its ``extra_body`` argument into the request JSON.
    # Google expects its extension fields under a literal JSON ``extra_body`` key.
    literal_extra_body = _ensure_dict(extra_body, "extra_body")
    google_section = _ensure_dict(literal_extra_body, "google")
    thinking_cfg = _ensure_dict(google_section, "thinking_config")
    thinking_cfg.setdefault("include_thoughts", True)


def build_request_body(request_data: Any, *, thinking_enabled: bool) -> dict:
    """Build OpenAI-format request body from an Anthropic request for Gemini."""
    logger.debug(
        "GEMINI_REQUEST: conversion start model={} msgs={}",
        getattr(request_data, "model", "?"),
        len(getattr(request_data, "messages", [])),
    )
    try:
        body = build_base_request_body(
            request_data,
            reasoning_replay=ReasoningReplayMode.REASONING_CONTENT
            if thinking_enabled
            else ReasoningReplayMode.DISABLED,
        )
    except OpenAIConversionError as exc:
        raise InvalidRequestError(str(exc)) from exc

    extra_body: dict[str, Any] = {}
    request_extra = getattr(request_data, "extra_body", None)
    if isinstance(request_extra, dict):
        extra_body.update(deepcopy(request_extra))

    if thinking_enabled:
        _apply_thinking_config(extra_body)
    else:
        body["reasoning_effort"] = "none"

    if extra_body:
        body["extra_body"] = extra_body

    logger.debug(
        "GEMINI_REQUEST: conversion done model={} msgs={} tools={}",
        body.get("model"),
        len(body.get("messages", [])),
        len(body.get("tools", [])),
    )
    return body
