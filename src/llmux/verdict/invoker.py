"""The seam between deliberation logic and concrete model calls.

Orchestration depends only on :class:`ModelInvoker`. The real implementation
(:mod:`provider_invoker`) reuses the existing provider stack; tests inject a
fake. This is what lets the CLI, the MCP server and the unit tests all drive the
exact same orchestration core.
"""

from dataclasses import dataclass
from typing import Protocol

from .models import FailureKind, ModelRef


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """Outcome of a single model call."""

    model_key: str
    ok: bool
    text: str = ""
    failure_kind: FailureKind | None = None
    detail: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def success(
        cls,
        model_key: str,
        text: str,
        *,
        latency_s: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> InvocationResult:
        return cls(
            model_key=model_key,
            ok=True,
            text=text,
            latency_s=latency_s,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @classmethod
    def failure(
        cls,
        model_key: str,
        kind: FailureKind,
        detail: str = "",
        *,
        latency_s: float = 0.0,
    ) -> InvocationResult:
        return cls(
            model_key=model_key,
            ok=False,
            failure_kind=kind,
            detail=detail,
            latency_s=latency_s,
        )


class ModelInvoker(Protocol):
    """Callable that turns a (system, user) prompt into text for one model."""

    async def invoke(
        self,
        model: ModelRef,
        system: str,
        user: str,
        *,
        max_tokens: int,
        request_id: str,
    ) -> InvocationResult: ...
