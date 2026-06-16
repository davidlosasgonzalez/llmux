from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.anthropic.sse import format_sse_event
from core.anthropic.stream_contracts import parse_sse_text


class FakeProvider:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.preflight_stream = MagicMock()
        self.requests: list[Any] = []

    async def stream_response(self, request_data, **_kwargs):
        self.requests.append(request_data)
        for chunk in self.chunks:
            yield chunk


@pytest.fixture
def responses_client():
    provider = FakeProvider(_anthropic_text_stream("Hello from provider"))
    app = create_app(lifespan_enabled=False)
    with (
        patch("api.dependencies.resolve_provider", return_value=provider),
        TestClient(app) as client,
    ):
        yield client, provider


def test_responses_probe_endpoints_return_204(
    responses_client: tuple[TestClient, FakeProvider],
) -> None:
    client, _provider = responses_client

    assert client.head("/v1/responses").status_code == 204
    assert client.options("/v1/responses").status_code == 204


def test_create_response_stream_routes_through_provider(
    responses_client: tuple[TestClient, FakeProvider],
) -> None:
    client, provider = responses_client

    response = client.post(
        "/v1/responses",
        json={
            "model": "nvidia_nim/test-model",
            "input": "Hello",
            "stream": True,
            "max_output_tokens": 32,
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = parse_sse_text(response.text)
    assert events[0].event == "response.created"
    assert events[-1].event == "response.completed"
    assert events[-1].data["response"]["output"][0]["content"][0]["text"] == (
        "Hello from provider"
    )
    assert provider.preflight_stream.called
    routed = provider.requests[0]
    assert routed.model == "test-model"
    assert routed.messages[0].role == "user"
    assert routed.messages[0].content == "Hello"
    assert routed.max_tokens == 32


def test_create_response_non_stream_collects_response(
    responses_client: tuple[TestClient, FakeProvider],
) -> None:
    client, _provider = responses_client

    response = client.post(
        "/v1/responses",
        json={
            "model": "nvidia_nim/test-model",
            "input": "Hello",
            "stream": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["output"][0]["content"][0]["text"] == "Hello from provider"


def test_create_response_tool_stream_emits_function_call() -> None:
    provider = FakeProvider(_anthropic_tool_stream())
    app = create_app(lifespan_enabled=False)
    with (
        patch("api.dependencies.resolve_provider", return_value=provider),
        TestClient(app) as client,
    ):
        response = client.post(
            "/v1/responses",
            json={
                "model": "nvidia_nim/test-model",
                "input": "Use echo",
                "stream": True,
                "tools": [
                    {
                        "type": "function",
                        "name": "echo",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            },
        )

    assert response.status_code == 200
    events = parse_sse_text(response.text)
    completed = events[-1].data["response"]
    call = completed["output"][0]
    assert call["type"] == "function_call"
    assert call["call_id"] == "toolu_1"
    assert call["arguments"] == '{"value":"FCC"}'


def test_create_response_accepts_codex_namespace_tool_request() -> None:
    provider = FakeProvider(_anthropic_tool_stream(tool_name="mcp__node_repl__js"))
    app = create_app(lifespan_enabled=False)
    with (
        patch("api.dependencies.resolve_provider", return_value=provider),
        TestClient(app) as client,
    ):
        response = client.post(
            "/v1/responses",
            json={
                "model": "nvidia_nim/test-model",
                "input": "Use JS",
                "stream": True,
                "tools": [
                    {"type": "web_search", "external_web_access": True},
                    {"type": "image_generation", "output_format": "png"},
                    {
                        "type": "namespace",
                        "name": "mcp__node_repl",
                        "tools": [
                            {
                                "type": "function",
                                "name": "js",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"code": {"type": "string"}},
                                },
                            }
                        ],
                    },
                ],
            },
        )

    assert response.status_code == 200
    routed = provider.requests[0]
    assert [tool.name for tool in routed.tools] == ["mcp__node_repl__js"]
    completed = parse_sse_text(response.text)[-1].data["response"]
    call = completed["output"][0]
    assert call["namespace"] == "mcp__node_repl"
    assert call["name"] == "js"


def test_create_response_unsupported_tool_returns_openai_error(
    responses_client: tuple[TestClient, FakeProvider],
) -> None:
    client, _provider = responses_client

    response = client.post(
        "/v1/responses",
        json={
            "model": "nvidia_nim/test-model",
            "input": "Hello",
            "tools": [{"type": "web_search_preview"}],
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert "Unsupported Responses tool type" in payload["error"]["message"]


def _anthropic_text_stream(text: str) -> list[str]:
    return [
        format_sse_event("message_start", {"type": "message_start", "message": {}}),
        format_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        format_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        format_sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        format_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        ),
        format_sse_event("message_stop", {"type": "message_stop"}),
    ]


def _anthropic_tool_stream(tool_name: str = "echo") -> list[str]:
    return [
        format_sse_event("message_start", {"type": "message_start", "message": {}}),
        format_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": tool_name,
                    "input": {},
                },
            },
        ),
        format_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"value":"FCC"}',
                },
            },
        ),
        format_sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        format_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        ),
        format_sse_event("message_stop", {"type": "message_stop"}),
    ]
