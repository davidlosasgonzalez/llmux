"""Agentic loop: talk to the proxy, execute tools, repeat until done."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .breakers import BreakerKind, BreakerTrip
from .context import DEFAULT_HISTORY_TOKEN_BUDGET, compact_messages
from .permissions import PermissionPort
from .prompts import SYSTEM_PROMPT
from .proxy_client import ProxyClient
from .tools import ToolRegistry, anthropic_tool_defs
from .workspace import Workspace


class AgentStopReason(StrEnum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BREAKER = "breaker"
    ERROR = "error"


@dataclass(slots=True)
class AgentResult:
    final_text: str
    stop_reason: AgentStopReason
    turns: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    trip: BreakerTrip | None = None
    detail: str = ""


class AgentLoop:
    """Run a multi-turn tool-using session against the FCC proxy."""

    def __init__(
        self,
        *,
        client: ProxyClient,
        workspace: Workspace,
        permissions: PermissionPort,
        tools: ToolRegistry | None = None,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 4096,
        max_turns: int = 40,
        max_schema_repairs: int = 3,
        history_token_budget: int = DEFAULT_HISTORY_TOKEN_BUDGET,
        system: str = SYSTEM_PROMPT,
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._permissions = permissions
        self._tools = tools or ToolRegistry(workspace, permissions)
        self._model = model
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._max_schema_repairs = max_schema_repairs
        self._schema_repairs = 0
        self._history_token_budget = history_token_budget
        self._system = system
        self._tool_defs = anthropic_tool_defs()

    async def run(
        self, prompt: str, *, prior_messages: list[dict[str, Any]] | None = None
    ) -> AgentResult:
        messages: list[dict[str, Any]] = list(prior_messages or [])
        messages.append({"role": "user", "content": prompt})
        final_text = ""
        goal = prompt
        if prior_messages:
            for msg in prior_messages:
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    goal = str(msg["content"])
                    break
        for turn in range(1, self._max_turns + 1):
            outbound = compact_messages(
                messages,
                max_tokens=self._history_token_budget,
                goal_hint=goal,
            )
            try:
                assistant = await self._client.complete(
                    messages=outbound,
                    tools=self._tool_defs,
                    system=self._system,
                    model=self._model,
                    max_tokens=self._max_tokens,
                )
            except Exception as exc:
                return AgentResult(
                    final_text=final_text,
                    stop_reason=AgentStopReason.ERROR,
                    turns=turn,
                    messages=messages,
                    detail=str(exc),
                )

            messages.append({"role": "assistant", "content": assistant.content})
            if assistant.text.strip():
                final_text = assistant.text.strip()

            tool_uses = assistant.tool_uses
            if not tool_uses:
                return AgentResult(
                    final_text=final_text,
                    stop_reason=AgentStopReason.COMPLETED,
                    turns=turn,
                    messages=messages,
                )

            tool_results, trip = await self._run_tools(tool_uses)
            messages.append({"role": "user", "content": tool_results})
            if trip is not None:
                return AgentResult(
                    final_text=final_text,
                    stop_reason=AgentStopReason.BREAKER,
                    turns=turn,
                    messages=messages,
                    trip=trip,
                    detail=trip.detail,
                )

        return AgentResult(
            final_text=final_text,
            stop_reason=AgentStopReason.MAX_TURNS,
            turns=self._max_turns,
            messages=messages,
            detail=f"exceeded max_turns={self._max_turns}",
        )

    async def _run_tools(
        self, tool_uses: Sequence[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], BreakerTrip | None]:
        results: list[dict[str, Any]] = []
        trip: BreakerTrip | None = None
        for block in tool_uses:
            tool_id = str(block.get("id", ""))
            name = str(block.get("name", ""))
            raw_input = block.get("input")
            arguments = raw_input if isinstance(raw_input, dict) else {}
            execution = await self._tools.execute(name, arguments)
            # A6: invalid schema → error tool_result (model may repair next turn).
            # Count consecutive schema repairs; trip after max_schema_repairs.
            if (
                execution.is_error
                and "does not match input_schema" in execution.content
            ):
                self._schema_repairs += 1
                if self._schema_repairs >= self._max_schema_repairs:
                    trip = BreakerTrip(
                        BreakerKind.SCHEMA_REPAIR,
                        f"tool schema repair exceeded {self._max_schema_repairs}",
                    )
            else:
                self._schema_repairs = 0
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": execution.content,
                    "is_error": execution.is_error,
                }
            )
            if execution.trip is not None:
                trip = execution.trip
                break
            if trip is not None:
                break
        return results, trip
