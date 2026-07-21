"""Phase 2: schema repair (A6), shared quota (A7), model fallback (A8)."""

from typing import Any

import pytest

from free_claude_code.agent.breakers import BreakerKind
from free_claude_code.agent.loop import AgentLoop, AgentStopReason
from free_claude_code.agent.permissions import AllowlistPermissionGate
from free_claude_code.agent.proxy_client import (
    AssistantTurn,
    FallbackProxyClient,
    ProxyError,
    provider_from_model,
)
from free_claude_code.agent.tools import ToolRegistry
from free_claude_code.agent.workspace import Workspace
from free_claude_code.core.quota import (
    DailyExhaustionStore,
    FailureKind,
    QuotaTracker,
    classify_failure,
)


class _ScriptedClient:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)

    async def complete(self, **_kwargs: Any) -> AssistantTurn:
        if not self._turns:
            return AssistantTurn(content=[{"type": "text", "text": "done"}])
        return self._turns.pop(0)


class _FlakyByModel:
    """Fails permanently for primary; succeeds for secondary."""

    def __init__(self, *, primary: str, secondary: str) -> None:
        self.primary = primary
        self.secondary = secondary
        self.calls: list[str] = []

    async def complete(self, *, model: str, **_kwargs: Any) -> AssistantTurn:
        self.calls.append(model)
        if model == self.primary:
            raise ProxyError("rate limited", status_code=429)
        return AssistantTurn(
            content=[{"type": "text", "text": f"ok via {model}"}],
            stop_reason="end_turn",
            model=model,
        )


@pytest.mark.asyncio
async def test_invalid_tool_args_return_error_for_model_repair(workspace: Workspace):
    tools = ToolRegistry(workspace, AllowlistPermissionGate(auto_approve=True))
    result = await tools.execute("read", {})  # missing required `path`
    assert result.is_error is True
    assert "input_schema" in result.content


@pytest.mark.asyncio
async def test_loop_lets_model_repair_invalid_args_then_succeed(workspace, tmp_path):
    client = _ScriptedClient(
        [
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": "1",
                        "name": "write",
                        "input": {"path": "x.txt"},  # missing content
                    }
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": "2",
                        "name": "write",
                        "input": {"path": "x.txt", "content": "fixed"},
                    }
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                content=[{"type": "text", "text": "wrote x.txt"}],
                stop_reason="end_turn",
            ),
        ]
    )
    permissions = AllowlistPermissionGate(auto_approve=True)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=ToolRegistry(workspace, permissions),
    )
    result = await loop.run("write x.txt")
    assert result.stop_reason == AgentStopReason.COMPLETED
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "fixed"
    # First tool_result was the schema error.
    tool_msgs = [
        m
        for m in result.messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert any(
        "input_schema" in str(block.get("content", ""))
        for msg in tool_msgs
        for block in msg["content"]
        if isinstance(block, dict)
    )


@pytest.mark.asyncio
async def test_schema_repair_breaker_after_max(workspace):
    client = _ScriptedClient(
        [
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": str(i),
                        "name": "read",
                        "input": {},
                    }
                ],
                stop_reason="tool_use",
            )
            for i in range(3)
        ]
    )
    permissions = AllowlistPermissionGate(auto_approve=True)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=ToolRegistry(workspace, permissions),
        max_schema_repairs=3,
    )
    result = await loop.run("keep failing schema")
    assert result.stop_reason == AgentStopReason.BREAKER
    assert result.trip is not None
    assert result.trip.kind == BreakerKind.SCHEMA_REPAIR


def test_classify_failure_shared_from_core():
    err = ProxyError("too many requests", status_code=429)
    assert classify_failure(err) is FailureKind.RATE_LIMITED


def test_daily_exhaustion_store(tmp_path):
    store = DailyExhaustionStore(tmp_path / "q.db")
    store.record_exhaustion("groq/llama", "groq", "2026-07-21")
    assert "groq/llama" in store.exhausted_keys("2026-07-21")
    assert store.exhausted_keys("2026-07-22") == set()
    store.close()


@pytest.mark.asyncio
async def test_fallback_client_uses_secondary_after_429(tmp_path):
    inner = _FlakyByModel(
        primary="groq/bad",
        secondary="cerebras/good",
    )
    client = FallbackProxyClient(
        inner=inner,
        fallback_models=["cerebras/good"],
        quota=QuotaTracker(),
        exhaustion=DailyExhaustionStore(tmp_path / "ex.db"),
    )
    turn = await client.complete(
        messages=[],
        tools=[],
        system="s",
        model="groq/bad",
        max_tokens=16,
    )
    assert turn.text == "ok via cerebras/good"
    assert inner.calls == ["groq/bad", "cerebras/good"]
    assert provider_from_model("groq/bad") == "groq"
