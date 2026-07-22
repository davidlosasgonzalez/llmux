"""Reusable product E2E smoke drivers."""

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from free_claude_code.cli.claude_env import build_claude_proxy_env
from free_claude_code.config.provider_catalog import SUPPORTED_PROVIDER_IDS
from free_claude_code.core.anthropic.stream_contracts import (
    SSEEvent,
    assert_anthropic_stream_contract,
    event_index,
    has_tool_use,
    parse_sse_lines,
    text_content,
)
from smoke.lib.child_process import run_captured_text
from smoke.lib.config import ProviderModel, SmokeConfig, auth_headers
from smoke.lib.server import RunningServer, start_server
from smoke.lib.skips import fail_missing_env


@dataclass(slots=True)
class ConversationTurn:
    request: dict[str, Any]
    events: list[SSEEvent]

    @property
    def assistant_content(self) -> list[dict[str, Any]]:
        return assistant_content_from_events(self.events)

    @property
    def text(self) -> str:
        return text_content(self.events)


class SmokeServerDriver:
    """Start a local proxy server for a product scenario."""

    def __init__(
        self,
        config: SmokeConfig,
        *,
        name: str,
        env_overrides: dict[str, str] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.config = config
        self.name = name
        self.env_overrides = env_overrides
        self.command = command

    @contextmanager
    def run(self) -> Iterator[RunningServer]:
        with start_server(
            self.config,
            env_overrides=self.env_overrides,
            command=self.command,
            name=self.name,
        ) as server:
            yield server


class ConversationDriver:
    """Drive multi-turn Anthropic-compatible conversations through the server."""

    def __init__(self, server: RunningServer, config: SmokeConfig) -> None:
        self.server = server
        self.config = config
        self.messages: list[dict[str, Any]] = []
        self.turns: list[ConversationTurn] = []

    def ask(
        self,
        text: str,
        *,
        model: str = "fcc-smoke-default",
        max_tokens: int = 256,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        append_assistant: bool = True,
    ) -> ConversationTurn:
        self.messages.append({"role": "user", "content": text})
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": list(self.messages),
        }
        if extra:
            payload.update(extra)
        turn = self.stream(payload, headers=headers)
        if append_assistant:
            self.messages.append(
                {"role": "assistant", "content": turn.assistant_content or turn.text}
            )
        return turn

    def stream(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> ConversationTurn:
        request_headers = headers or auth_headers()
        with httpx.stream(
            "POST",
            f"{self.server.base_url}/v1/messages",
            headers=request_headers,
            json=payload,
            timeout=self.config.timeout_s,
        ) as response:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                raise AssertionError(
                    f"stream request failed: HTTP {response.status_code} {body[:1000]}"
                )
            events = parse_sse_lines(response.iter_lines())
        assert_anthropic_stream_contract(events)
        turn = ConversationTurn(payload, events)
        self.turns.append(turn)
        return turn

    def stream_expect_http_error(
        self,
        payload: dict[str, Any],
        *,
        expected_status: int,
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.server.base_url}/v1/messages",
            headers=auth_headers(),
            json=payload,
            timeout=self.config.timeout_s,
        )
        assert response.status_code == expected_status, response.text
        return response.json()


class ProviderMatrixDriver:
    """Resolve provider models and enforce matrix semantics for product smoke."""

    ALL_PROVIDERS: tuple[str, ...] = SUPPORTED_PROVIDER_IDS

    def __init__(self, config: SmokeConfig) -> None:
        self.config = config

    def configured_models(self) -> list[ProviderModel]:
        return self.config.provider_models()

    def provider_smoke_models(self) -> list[ProviderModel]:
        selected = self.config.provider_matrix
        missing_selected = [
            provider
            for provider in selected
            if provider in self.ALL_PROVIDERS
            and not self.config.has_provider_configuration(provider)
        ]
        if missing_selected:
            fail_missing_env(
                "selected providers are not configured: "
                + ", ".join(sorted(missing_selected))
            )

        models = self.config.provider_smoke_models()
        if not models and os.getenv("FCC_ALLOW_NO_PROVIDER_SMOKE") != "1":
            fail_missing_env(
                "no configured provider smoke models; set FCC_ALLOW_NO_PROVIDER_SMOKE=1 "
                "only for no-provider smoke collection"
            )
        return models

    def first_model(self) -> ProviderModel:
        models = self.provider_smoke_models()
        if not models:
            pytest.skip("missing_env: no configured provider model")
        return models[0]


class ClientProtocolDriver:
    """Build recorded/representative client protocol requests."""

    @staticmethod
    def vscode_headers() -> dict[str, str]:
        headers = auth_headers()
        headers.update(
            {
                "anthropic-beta": "messages-2023-12-15",
                "user-agent": "Claude-Code-VSCode product smoke",
            }
        )
        return headers

    @staticmethod
    def jetbrains_headers() -> dict[str, str]:
        headers = auth_headers()
        headers["user-agent"] = "JetBrains-ACP product smoke"
        return headers

    @staticmethod
    def adaptive_thinking_payload() -> dict[str, Any]:
        return {
            "model": "claude-opus-4-7",
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "unsigned thought"},
                        {"type": "redacted_thinking", "data": "opaque"},
                        {"type": "text", "text": "Hello."},
                    ],
                },
                {"role": "user", "content": "Reply with exactly FCC_SMOKE_CLIENT"},
            ],
            "thinking": {"type": "adaptive", "budget_tokens": 1024},
        }

    @staticmethod
    def tool_result_payload() -> dict[str, Any]:
        return {
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "Use echo_smoke once."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_client_smoke",
                            "name": "echo_smoke",
                            "input": {"value": "FCC_SMOKE_CLIENT"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_client_smoke",
                            "content": "FCC_SMOKE_CLIENT",
                        }
                    ],
                },
            ],
            "tools": [echo_tool_schema()],
            "thinking": {"type": "adaptive"},
        }

    @staticmethod
    def run_claude_prompt(
        *,
        claude_bin: str,
        server: RunningServer,
        config: SmokeConfig,
        cwd: Path,
        prompt: str,
        model: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = build_claude_proxy_env(
            proxy_root_url=server.base_url,
            auth_token=config.settings.anthropic_auth_token,
            base_env=os.environ,
        )
        command = [
            claude_bin,
            "--bare",
            "--no-session-persistence",
            "--tools",
            "",
            "--system-prompt",
            "Reply with exactly the requested smoke token and no other text.",
        ]
        if model is not None:
            command.extend(["--model", model])
        command.extend(["-p", prompt])
        return run_captured_text(
            command,
            cwd=cwd,
            env=env,
            timeout=config.timeout_s,
            check=False,
        )


def echo_tool_schema() -> dict[str, Any]:
    return {
        "name": "echo_smoke",
        "description": "Echo a test value.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }


def assistant_content_from_events(events: list[SSEEvent]) -> list[dict[str, Any]]:
    blocks: dict[int, dict[str, Any]] = {}
    block_order: list[int] = []
    for event in events:
        if event.event == "content_block_start":
            index = event_index(event)
            block = event.data.get("content_block", {})
            if isinstance(block, dict):
                blocks[index] = dict(block)
                block_order.append(index)
            continue
        if event.event == "content_block_delta":
            index = event_index(event)
            block = blocks.get(index)
            delta = event.data.get("delta", {})
            if not isinstance(block, dict) or not isinstance(delta, dict):
                continue
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                block["text"] = str(block.get("text", "")) + str(delta.get("text", ""))
            elif delta_type == "thinking_delta":
                block["thinking"] = str(block.get("thinking", "")) + str(
                    delta.get("thinking", "")
                )
            elif delta_type == "input_json_delta":
                block["_partial_json"] = str(block.get("_partial_json", "")) + str(
                    delta.get("partial_json", "")
                )

    content: list[dict[str, Any]] = []
    for index in block_order:
        block = blocks[index]
        if block.get("type") == "tool_use":
            partial = str(block.pop("_partial_json", ""))
            if partial:
                try:
                    block["input"] = json.loads(partial)
                except json.JSONDecodeError:
                    block["input"] = {}
        content.append(block)
    return content


def tool_use_blocks(events: list[SSEEvent]) -> list[dict[str, Any]]:
    return [
        block
        for block in assistant_content_from_events(events)
        if block.get("type") == "tool_use"
    ]


def assert_product_stream(events: list[SSEEvent]) -> None:
    assert_anthropic_stream_contract(events)
    assert text_content(events).strip() or has_tool_use(events), (
        "product stream emitted neither text nor tool_use"
    )
