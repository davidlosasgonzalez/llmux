"""Convert between OpenAI Responses payloads and Anthropic-style payloads."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any


class ResponsesConversionError(ValueError):
    """Raised when a Responses request cannot be converted deterministically."""


def responses_request_to_anthropic_payload(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert an OpenAI Responses request into an Anthropic Messages payload."""

    system_parts: list[str] = []
    if instructions := _optional_str(request.get("instructions")):
        system_parts.append(instructions)

    messages: list[dict[str, Any]] = []
    for item in _iter_input_items(request.get("input")):
        _append_input_item(item, messages=messages, system_parts=system_parts)

    if not messages:
        raise ResponsesConversionError("Responses request input must contain a message")

    payload: dict[str, Any] = {
        "model": _required_str(request.get("model"), "model"),
        "messages": messages,
        "stream": True,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    _copy_if_present(request, payload, "temperature")
    _copy_if_present(request, payload, "top_p")
    if request.get("max_output_tokens") is not None:
        payload["max_tokens"] = request["max_output_tokens"]
    if isinstance(request.get("metadata"), dict):
        payload["metadata"] = request["metadata"]

    raw_tool_choice = request.get("tool_choice")
    tools = _convert_tools(request.get("tools"))
    if tools and raw_tool_choice != "none":
        payload["tools"] = tools
    tool_choice = _convert_tool_choice(raw_tool_choice)
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    return payload


def anthropic_message_response_to_openai_response(
    message: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    response_id: str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Convert a complete Anthropic message response into a Responses object."""

    response_id = response_id or _new_response_id()
    output: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in _message_content_blocks(message):
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            output.append(
                _function_call_item(
                    block_id=str(block.get("id", "") or _new_call_id()),
                    name=str(block.get("name", "")),
                    arguments=json.dumps(block.get("input") or {}),
                    status="completed",
                )
            )

    text = "".join(text_parts)
    if text:
        output.insert(0, _message_item(_new_message_item_id(), text, "completed"))

    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": str(request.get("model", message.get("model", ""))),
        "output": output,
        "parallel_tool_calls": bool(request.get("parallel_tool_calls", True)),
        "tool_choice": request.get("tool_choice", "auto"),
        "temperature": request.get("temperature"),
        "top_p": request.get("top_p"),
        "max_output_tokens": request.get("max_output_tokens"),
        "usage": _openai_usage(message.get("usage")),
        "error": None if status == "completed" else {},
    }


def openai_error_payload(*, message: str, error_type: str) -> dict[str, Any]:
    """Return an OpenAI-compatible error envelope."""

    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        }
    }


def _append_input_item(
    item: Any,
    *,
    messages: list[dict[str, Any]],
    system_parts: list[str],
) -> None:
    if isinstance(item, str):
        messages.append({"role": "user", "content": item})
        return
    if not isinstance(item, dict):
        raise ResponsesConversionError(
            f"Unsupported Responses input item: {type(item).__name__}"
        )

    item_type = item.get("type")
    if item_type in (None, "message") or "role" in item:
        role = _required_str(item.get("role", "user"), "input.role")
        _append_message_item(role, item.get("content", ""), messages, system_parts)
        return
    if item_type == "function_call":
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": _call_id_from_item(item),
                        "name": _required_str(item.get("name"), "function_call.name"),
                        "input": _parse_arguments(item.get("arguments")),
                    }
                ],
            }
        )
        return
    if item_type == "function_call_output":
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": _call_id_from_item(item),
                        "content": item.get("output", ""),
                    }
                ],
            }
        )
        return
    if item_type == "reasoning":
        return
    if item_type in {"input_text", "output_text", "text"}:
        messages.append({"role": "user", "content": _text_from_part(item)})
        return

    raise ResponsesConversionError(
        f"Unsupported Responses input item type: {item_type!r}"
    )


def _append_message_item(
    role: str,
    content: Any,
    messages: list[dict[str, Any]],
    system_parts: list[str],
) -> None:
    normalized_role = "system" if role == "developer" else role
    if normalized_role == "system":
        text = _content_as_text(content)
        if text:
            system_parts.append(text)
        return
    if normalized_role not in {"user", "assistant"}:
        raise ResponsesConversionError(f"Unsupported Responses message role: {role!r}")
    messages.append(
        {
            "role": normalized_role,
            "content": _convert_message_content(content),
        }
    )


def _iter_input_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _convert_message_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                blocks.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                raise ResponsesConversionError(
                    f"Unsupported Responses content part: {type(part).__name__}"
                )
            part_type = part.get("type")
            if part_type in {"input_text", "output_text", "text"} or "text" in part:
                blocks.append({"type": "text", "text": _text_from_part(part)})
                continue
            if part_type == "refusal":
                blocks.append({"type": "text", "text": str(part.get("refusal", ""))})
                continue
            raise ResponsesConversionError(
                f"Unsupported Responses content part type: {part_type!r}"
            )
        return blocks
    if isinstance(content, dict):
        return [{"type": "text", "text": _text_from_part(content)}]
    raise ResponsesConversionError(
        f"Unsupported Responses message content: {type(content).__name__}"
    )


def _content_as_text(content: Any) -> str:
    converted = _convert_message_content(content)
    if isinstance(converted, str):
        return converted
    return "\n".join(str(block.get("text", "")) for block in converted)


def _text_from_part(part: Mapping[str, Any]) -> str:
    if text := _optional_str(part.get("text")):
        return text
    if text := _optional_str(part.get("input_text")):
        return text
    if text := _optional_str(part.get("output_text")):
        return text
    return ""


def _convert_tools(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ResponsesConversionError("Responses tools must be a list")

    tools: list[dict[str, Any]] = []
    for tool in value:
        if not isinstance(tool, dict):
            raise ResponsesConversionError(
                f"Unsupported Responses tool: {type(tool).__name__}"
            )
        tool_type = tool.get("type")
        if tool_type != "function":
            raise ResponsesConversionError(
                f"Unsupported Responses tool type: {tool_type!r}"
            )

        function = tool.get("function")
        source = function if isinstance(function, dict) else tool
        name = _required_str(source.get("name"), "tool.name")
        schema = source.get("parameters")
        if schema is None:
            schema = {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            raise ResponsesConversionError(
                f"Responses tool {name!r} parameters must be an object"
            )
        converted: dict[str, Any] = {
            "name": name,
            "input_schema": schema,
        }
        if description := _optional_str(source.get("description")):
            converted["description"] = description
        tools.append(converted)
    return tools


def _convert_tool_choice(value: Any) -> dict[str, Any] | None:
    if value is None or value == "auto":
        return None
    if value == "none":
        return None
    if value == "required":
        return {"type": "any"}
    if isinstance(value, dict):
        choice_type = value.get("type")
        if choice_type == "function":
            return {
                "type": "tool",
                "name": _required_str(value.get("name"), "tool_choice.name"),
            }
        if choice_type in {"auto", "any", "tool"}:
            return dict(value)
    raise ResponsesConversionError(f"Unsupported Responses tool_choice: {value!r}")


def _parse_arguments(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ResponsesConversionError("Responses function_call arguments must be JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ResponsesConversionError(
            f"Responses function_call arguments are invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ResponsesConversionError(
            "Responses function_call arguments must decode to an object"
        )
    return parsed


def _call_id_from_item(item: Mapping[str, Any]) -> str:
    for key in ("call_id", "id"):
        if value := _optional_str(item.get(key)):
            return value
    return _new_call_id()


def _required_str(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value:
        return value
    raise ResponsesConversionError(
        f"Responses field {field_name} must be a non-empty string"
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _copy_if_present(
    source: Mapping[str, Any], target: dict[str, Any], field_name: str
) -> None:
    if source.get(field_name) is not None:
        target[field_name] = source[field_name]


def _message_content_blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _message_item(item_id: str, text: str, status: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call_item(
    *,
    block_id: str,
    name: str,
    arguments: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": block_id if block_id.startswith("fc_") else f"fc_{uuid.uuid4().hex[:24]}",
        "type": "function_call",
        "status": status,
        "call_id": block_id,
        "name": name,
        "arguments": arguments,
    }


def _openai_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    return {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else 0,
        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
        "total_tokens": (
            (input_tokens if isinstance(input_tokens, int) else 0)
            + (output_tokens if isinstance(output_tokens, int) else 0)
        ),
    }


def _new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def _new_message_item_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"
