"""Conversation compaction so long agent sessions stay inside a token budget."""

import json
from typing import Any

# Same heuristic as verdict research budgets (~4 chars/token).
_CHARS_PER_TOKEN = 4

# Soft default: leave room for system + tools + model output.
DEFAULT_HISTORY_TOKEN_BUDGET = 24_000


def estimate_tokens(messages: list[dict[str, Any]], *, system: str = "") -> int:
    """Rough token estimate for the conversation payload."""
    payload = json.dumps(messages, ensure_ascii=False, default=str)
    return (len(payload) + len(system)) // _CHARS_PER_TOKEN


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = DEFAULT_HISTORY_TOKEN_BUDGET,
    keep_recent: int = 6,
    goal_hint: str = "",
) -> list[dict[str, Any]]:
    """Shrink ``messages`` while preserving the original goal and recent turns.

    Strategy (deterministic, no extra model call):
    1. Always keep the first user message (the task).
    2. Keep the last ``keep_recent`` messages intact.
    3. Collapse everything in between into one synthetic user note that
       records roles + truncated excerpts so the model still sees continuity.
    """
    if estimate_tokens(messages) <= max_tokens:
        return messages

    head = messages[0]
    tail = messages[-keep_recent:] if keep_recent > 0 else []
    middle = (
        messages[1 : len(messages) - keep_recent] if keep_recent > 0 else messages[1:]
    )

    # Avoid duplicating head if it is already in the tail window.
    if tail and tail[0] is head:
        middle = []
        compacted_head = head
    else:
        compacted_head = head

    summary_lines = [
        "[fcc-agent compacted earlier turns to fit the context window]",
    ]
    if goal_hint:
        summary_lines.append(f"Original goal: {goal_hint[:500]}")
    for msg in middle:
        role = str(msg.get("role", "?"))
        excerpt = _excerpt(msg.get("content"))
        summary_lines.append(f"- {role}: {excerpt}")

    summary_msg = {
        "role": "user",
        "content": "\n".join(summary_lines),
    }

    # Drop the head from tail if it would duplicate after rebuild.
    rebuilt = [compacted_head, summary_msg]
    for msg in tail:
        if msg is compacted_head:
            continue
        rebuilt.append(msg)

    # If still too large, aggressively trim tool_result bodies in the tail.
    if estimate_tokens(rebuilt) > max_tokens:
        rebuilt = [_trim_message(m, max_chars=2_000) for m in rebuilt]
    return rebuilt


def _excerpt(content: object, *, limit: int = 240) -> str:
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _trim_message(message: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, str) and len(content) > max_chars:
        return {**message, "content": content[:max_chars] + "…[truncated]"}
    if isinstance(content, list):
        trimmed: list[Any] = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_chars
            ):
                trimmed.append(
                    {
                        **block,
                        "content": block["content"][:max_chars] + "…[truncated]",
                    }
                )
            else:
                trimmed.append(block)
        return {**message, "content": trimmed}
    return message
