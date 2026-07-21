"""Unit tests for the own-agent harness (Phase 1)."""

from pathlib import Path
from typing import Any

import pytest

from free_claude_code.agent.breakers import BreakerKind, CircuitBreakers
from free_claude_code.agent.loop import AgentLoop, AgentStopReason
from free_claude_code.agent.permissions import AllowlistPermissionGate
from free_claude_code.agent.proxy_client import AssistantTurn
from free_claude_code.agent.tools import ToolRegistry
from free_claude_code.agent.workspace import Workspace, WorkspaceError


def _gate(*, auto: bool = True) -> AllowlistPermissionGate:
    return AllowlistPermissionGate(auto_approve=auto)


# --------------------------------------------------------------------------- #
# Workspace confinement
# --------------------------------------------------------------------------- #
def test_workspace_resolves_relative(workspace: Workspace, tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert workspace.resolve("a.txt") == tmp_path / "a.txt"


def test_workspace_rejects_escape(workspace: Workspace):
    with pytest.raises(WorkspaceError):
        workspace.resolve("../outside.txt")


def test_workspace_rejects_absolute_outside(workspace: Workspace, tmp_path: Path):
    outsider = tmp_path.parent / "nope.txt"
    with pytest.raises(WorkspaceError):
        workspace.resolve(str(outsider))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_read_write_edit_roundtrip(workspace: Workspace, tmp_path: Path):
    tools = ToolRegistry(workspace, _gate())
    written = await tools.execute("write", {"path": "hi.txt", "content": "hello"})
    assert written.is_error is False
    read = await tools.execute("read", {"path": "hi.txt"})
    assert read.content == "hello"
    edited = await tools.execute(
        "edit", {"path": "hi.txt", "old_string": "hello", "new_string": "hola"}
    )
    assert edited.is_error is False
    assert (tmp_path / "hi.txt").read_text(encoding="utf-8") == "hola"


@pytest.mark.asyncio
async def test_read_rejects_path_outside_root(workspace: Workspace):
    tools = ToolRegistry(workspace, _gate())
    result = await tools.execute("read", {"path": "../secret"})
    assert result.is_error is True
    assert "escapes" in result.content


@pytest.mark.asyncio
async def test_bash_runs_in_workspace(workspace: Workspace, tmp_path: Path):
    (tmp_path / "note.txt").write_text("ok", encoding="utf-8")
    tools = ToolRegistry(workspace, _gate())
    result = await tools.execute("bash", {"command": "cat note.txt"})
    assert result.is_error is False
    assert "ok" in result.content


@pytest.mark.asyncio
async def test_grep_and_glob(workspace: Workspace, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "def foo():\n    return 1\n", encoding="utf-8"
    )
    tools = ToolRegistry(workspace, _gate())
    grepped = await tools.execute("grep", {"pattern": "def foo"})
    assert "a.py:1:" in grepped.content
    globbed = await tools.execute("glob", {"pattern": "**/*.py"})
    assert "src/a.py" in globbed.content.replace("\\", "/")


@pytest.mark.asyncio
async def test_permission_denial_returns_tool_result_not_exception(
    workspace: Workspace,
):
    async def deny(_name: str, _args: dict[str, object]) -> bool:
        return False

    tools = ToolRegistry(workspace, AllowlistPermissionGate(confirm=deny))
    result = await tools.execute("bash", {"command": "echo hi"})
    assert result.is_error is True
    assert "Permission denied" in result.content


@pytest.mark.asyncio
async def test_read_does_not_require_confirmation(workspace: Workspace, tmp_path: Path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    asked: list[str] = []

    async def confirm(name: str, _args: dict[str, object]) -> bool:
        asked.append(name)
        return False

    tools = ToolRegistry(workspace, AllowlistPermissionGate(confirm=confirm))
    result = await tools.execute("read", {"path": "f.txt"})
    assert result.is_error is False
    assert asked == []


# --------------------------------------------------------------------------- #
# Circuit breakers
# --------------------------------------------------------------------------- #
def test_breaker_trips_on_repeated_bash_failure():
    breakers = CircuitBreakers(max_bash_failures=3)
    assert breakers.note_bash("false", ok=False) is None
    assert breakers.note_bash("false", ok=False) is None
    trip = breakers.note_bash("false", ok=False)
    assert trip is not None
    assert trip.kind == BreakerKind.BASH_FAILURES


def test_breaker_trips_on_edit_revert_oscillation():
    breakers = CircuitBreakers(max_edit_reverts=2)
    assert breakers.note_edit("a.txt", "B") is None
    assert breakers.note_edit("a.txt", "A") is None  # revert 1
    assert breakers.note_edit("a.txt", "B") is None
    trip = breakers.note_edit("a.txt", "A")  # revert 2
    assert trip is not None
    assert trip.kind == BreakerKind.EDIT_REVERT


def test_breaker_trips_on_stale_reads():
    breakers = CircuitBreakers(max_stale_reads=3)
    assert breakers.note_read("a.txt") is None
    assert breakers.note_read("a.txt") is None
    trip = breakers.note_read("a.txt")
    assert trip is not None
    assert trip.kind == BreakerKind.STALE_READ


def test_breaker_stale_read_resets_after_write():
    breakers = CircuitBreakers(max_stale_reads=3)
    breakers.note_read("a.txt")
    breakers.note_read("a.txt")
    breakers.note_write("b.txt", "x")
    assert breakers.note_read("a.txt") is None  # counter reset


# --------------------------------------------------------------------------- #
# Agentic loop (fake proxy — no network)
# --------------------------------------------------------------------------- #
class _ScriptedClient:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls = 0

    async def complete(
        self,
        *,
        messages: Any,
        tools: Any,
        system: str,
        model: str,
        max_tokens: int,
    ) -> AssistantTurn:
        self.calls += 1
        if not self._turns:
            return AssistantTurn(content=[{"type": "text", "text": "done"}])
        return self._turns.pop(0)


@pytest.mark.asyncio
async def test_loop_runs_multi_step_task(workspace: Workspace, tmp_path: Path):
    """A1 acceptance: multi-step tool use against a real workspace (fake model)."""
    client = _ScriptedClient(
        [
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": "1",
                        "name": "write",
                        "input": {"path": "hello.txt", "content": "hi"},
                    }
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": "2",
                        "name": "read",
                        "input": {"path": "hello.txt"},
                    }
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                content=[{"type": "text", "text": "Created hello.txt with hi"}],
                stop_reason="end_turn",
            ),
        ]
    )
    permissions = _gate(auto=True)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=ToolRegistry(workspace, permissions),
    )
    result = await loop.run("create hello.txt saying hi, then read it back")
    assert result.stop_reason == AgentStopReason.COMPLETED
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert "hello.txt" in result.final_text
    assert client.calls == 3


@pytest.mark.asyncio
async def test_loop_stops_on_bash_breaker(workspace: Workspace):
    client = _ScriptedClient(
        [
            AssistantTurn(
                content=[
                    {
                        "type": "tool_use",
                        "id": str(i),
                        "name": "bash",
                        "input": {"command": "exit 1"},
                    }
                ],
                stop_reason="tool_use",
            )
            for i in range(3)
        ]
    )
    permissions = _gate(auto=True)
    loop = AgentLoop(
        client=client,
        workspace=workspace,
        permissions=permissions,
        tools=ToolRegistry(workspace, permissions),
    )
    result = await loop.run("keep failing")
    assert result.stop_reason == AgentStopReason.BREAKER
    assert result.trip is not None
    assert result.trip.kind == BreakerKind.BASH_FAILURES
