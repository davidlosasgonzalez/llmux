"""Permission port: allowlist + confirmation callback."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

# Tools that never need interactive confirmation.
AUTO_ALLOW = frozenset({"read", "grep", "glob"})
# Tools that mutate the workspace or run shell — always ask (unless auto-approved).
CONFIRM_REQUIRED = frozenset({"write", "edit", "bash"})


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""


class PermissionPort(Protocol):
    """Decide whether a tool call may run."""

    async def check(
        self, tool_name: str, arguments: dict[str, object]
    ) -> PermissionDecision: ...


ConfirmCallback = Callable[[str, dict[str, object]], Awaitable[bool] | bool]


class AllowlistPermissionGate:
    """Allowlist auto-tools; confirm mutating tools via an injected callback."""

    def __init__(
        self,
        *,
        confirm: ConfirmCallback | None = None,
        auto_approve: bool = False,
    ) -> None:
        self._confirm = confirm
        self._auto_approve = auto_approve

    async def check(
        self, tool_name: str, arguments: dict[str, object]
    ) -> PermissionDecision:
        if tool_name in AUTO_ALLOW:
            return PermissionDecision(allowed=True, reason="auto-allow")
        if tool_name not in CONFIRM_REQUIRED:
            # Unknown tools are denied by default — safer than silent allow.
            return PermissionDecision(
                allowed=False, reason=f"unknown tool denied: {tool_name}"
            )
        if self._auto_approve:
            return PermissionDecision(allowed=True, reason="auto-approve")
        if self._confirm is None:
            return PermissionDecision(
                allowed=False, reason="no confirmation callback configured"
            )
        decided = self._confirm(tool_name, arguments)
        if isinstance(decided, bool):
            allowed = decided
        else:
            allowed = await decided
        if allowed:
            return PermissionDecision(allowed=True, reason="user approved")
        return PermissionDecision(allowed=False, reason="user denied")


async def console_confirm(tool_name: str, arguments: dict[str, object]) -> bool:
    """Prompt on stdin for mutating tool approval."""
    preview = _preview_args(arguments)
    print(
        f"\n[fcc-agent] Allow `{tool_name}`?\n  {preview}\n[y/N] ", end="", flush=True
    )
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _preview_args(arguments: dict[str, object], *, limit: int = 200) -> str:
    parts = [f"{k}={v!r}" for k, v in arguments.items()]
    text = ", ".join(parts)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
