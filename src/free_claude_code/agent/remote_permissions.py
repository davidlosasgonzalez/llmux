"""Remote permission approval with timeout → deny (A12).

Messaging platforms resolve pending requests via ``ApprovalBroker.resolve``
(e.g. Telegram callback / Discord button). Until UI buttons ship, tests and
text adapters call ``resolve`` directly.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .permissions import AUTO_ALLOW, CONFIRM_REQUIRED, PermissionDecision


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    tool_name: str
    arguments: dict[str, object]
    created_at: float
    scope_key: str = ""


NotifyFn = Callable[[ApprovalRequest], Awaitable[None] | None]


@dataclass(slots=True)
class ApprovalBroker:
    """In-process broker: request → wait → resolve/timeout."""

    timeout_s: float = 120.0
    on_request: NotifyFn | None = None
    _pending: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    _meta: dict[str, ApprovalRequest] = field(default_factory=dict)

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        scope_key: str = "",
    ) -> bool:
        request_id = uuid.uuid4().hex
        req = ApprovalRequest(
            request_id=request_id,
            tool_name=tool_name,
            arguments=arguments,
            created_at=time.time(),
            scope_key=scope_key,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future
        self._meta[request_id] = req
        try:
            if self.on_request is not None:
                maybe = self.on_request(req)
                if asyncio.iscoroutine(maybe):
                    await maybe
            return await asyncio.wait_for(future, timeout=self.timeout_s)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)
            self._meta.pop(request_id, None)

    def resolve(self, request_id: str, *, approved: bool) -> bool:
        """Resolve a pending approval. Returns False if unknown/already settled."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def pending(self) -> list[ApprovalRequest]:
        return list(self._meta.values())


class RemotePermissionGate:
    """PermissionPort that asks a remote broker for mutating tools."""

    def __init__(
        self,
        broker: ApprovalBroker,
        *,
        scope_key: str = "",
        auto_approve: bool = False,
    ) -> None:
        self._broker = broker
        self._scope_key = scope_key
        self._auto_approve = auto_approve

    async def check(
        self, tool_name: str, arguments: dict[str, object]
    ) -> PermissionDecision:
        if tool_name in AUTO_ALLOW:
            return PermissionDecision(allowed=True, reason="auto-allow")
        if tool_name not in CONFIRM_REQUIRED:
            return PermissionDecision(
                allowed=False, reason=f"unknown tool denied: {tool_name}"
            )
        if self._auto_approve:
            return PermissionDecision(allowed=True, reason="auto-approve")
        allowed = await self._broker.request_approval(
            tool_name, arguments, scope_key=self._scope_key
        )
        if allowed:
            return PermissionDecision(allowed=True, reason="remote approved")
        return PermissionDecision(allowed=False, reason="remote denied or timed out")
