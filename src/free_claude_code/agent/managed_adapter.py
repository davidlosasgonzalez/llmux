"""Adapt AgentLoop to the messaging managed-session Protocols (A11)."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger

from free_claude_code.agent.loop import AgentLoop, AgentStopReason
from free_claude_code.agent.permissions import AllowlistPermissionGate, PermissionPort
from free_claude_code.agent.proxy_client import FallbackProxyClient, HttpProxyClient
from free_claude_code.agent.tools import ToolRegistry
from free_claude_code.agent.workspace import Workspace
from free_claude_code.core.quota import DailyExhaustionStore, QuotaTracker


class AgentManagedSession:
    """One harness session that yields CLI-compatible events for messaging."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        proxy_root_url: str,
        auth_token: str = "",
        model: str = "claude-sonnet-4-5",
        permissions: PermissionPort | None = None,
        fallback_models: list[str] | None = None,
        exhaustion: DailyExhaustionStore | None = None,
        max_turns: int = 40,
        job_timeout_s: float | None = None,
    ) -> None:
        self._workspace = workspace
        self._proxy_root_url = proxy_root_url
        self._auth_token = auth_token
        self._model = model
        self._permissions = permissions or AllowlistPermissionGate(auto_approve=True)
        self._fallback_models = list(fallback_models or [])
        self._exhaustion = exhaustion
        self._max_turns = max_turns
        self._job_timeout_s = job_timeout_s
        self._busy = False
        self._history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def is_busy(self) -> bool:
        return self._busy

    async def start_task(
        self,
        prompt: str,
        session_id: str | None = None,
        fork_session: bool = False,
    ) -> AsyncGenerator[dict, Any]:
        async with self._lock:
            if self._busy:
                yield {
                    "type": "error",
                    "error": {"message": "Agent session is busy"},
                }
                yield {"type": "exit", "code": 1}
                return
            self._busy = True

        harness_id = session_id or f"agent_{uuid.uuid4().hex[:12]}"
        try:
            yield {"type": "session_info", "session_id": harness_id}

            if fork_session:
                # Fork keeps a copy of history for the branched turn.
                self._history = list(self._history)

            client: HttpProxyClient | FallbackProxyClient = HttpProxyClient(
                self._proxy_root_url,
                auth_token=self._auth_token,
            )
            if self._fallback_models:
                client = FallbackProxyClient(
                    inner=client,
                    fallback_models=self._fallback_models,
                    quota=QuotaTracker(),
                    exhaustion=self._exhaustion,
                )
            tools = ToolRegistry(self._workspace, self._permissions)
            loop = AgentLoop(
                client=client,
                workspace=self._workspace,
                permissions=self._permissions,
                tools=tools,
                model=self._model,
                max_turns=self._max_turns,
            )

            prior = list(self._history) if self._history else None
            run = loop.run(prompt, prior_messages=prior)
            if self._job_timeout_s is not None and self._job_timeout_s > 0:
                result = await asyncio.wait_for(run, timeout=self._job_timeout_s)
            else:
                result = await run

            # Persist full transcript for later resume/fork within this process.
            self._history = list(result.messages)

            if result.final_text:
                yield {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": result.final_text}],
                    },
                }

            if result.stop_reason == AgentStopReason.COMPLETED:
                yield {"type": "exit", "code": 0}
            elif result.stop_reason == AgentStopReason.BREAKER:
                yield {
                    "type": "error",
                    "error": {"message": result.detail or "circuit breaker tripped"},
                }
                yield {"type": "exit", "code": 1, "stderr": result.detail}
            else:
                yield {
                    "type": "error",
                    "error": {"message": result.detail or result.stop_reason.value},
                }
                yield {
                    "type": "exit",
                    "code": 1,
                    "stderr": result.detail or result.stop_reason.value,
                }
        except TimeoutError:
            logger.warning("agent.managed.timeout session={}", harness_id)
            yield {
                "type": "error",
                "error": {"message": f"job exceeded {self._job_timeout_s:.0f}s"},
            }
            yield {"type": "exit", "code": 1, "stderr": "timeout"}
        except Exception as exc:
            logger.exception("agent.managed.failed session={}", harness_id)
            yield {"type": "error", "error": {"message": str(exc)}}
            yield {"type": "exit", "code": 1, "stderr": str(exc)}
        finally:
            self._busy = False

    async def stop(self) -> bool:
        self._busy = False
        return True


class AgentManagedSessionManager:
    """Pool of harness sessions for MessagingWorkflow (A11)."""

    def __init__(
        self,
        *,
        workspace_path: str,
        proxy_root_url: str,
        auth_token: str = "",
        model: str = "claude-sonnet-4-5",
        permissions: PermissionPort | None = None,
        fallback_models: list[str] | None = None,
        exhaustion_db: str | None = None,
        max_turns: int = 40,
        job_timeout_s: float | None = None,
    ) -> None:
        self._workspace = Workspace(workspace_path)
        self._proxy_root_url = proxy_root_url
        self._auth_token = auth_token
        self._model = model
        self._permissions = permissions
        self._fallback_models = list(fallback_models or [])
        self._exhaustion = (
            DailyExhaustionStore(exhaustion_db) if exhaustion_db else None
        )
        self._max_turns = max_turns
        self._job_timeout_s = job_timeout_s
        self._sessions: dict[str, AgentManagedSession] = {}
        self._pending: dict[str, AgentManagedSession] = {}
        self._temp_to_real: dict[str, str] = {}
        self._real_to_temp: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _make_session(self) -> AgentManagedSession:
        return AgentManagedSession(
            workspace=self._workspace,
            proxy_root_url=self._proxy_root_url,
            auth_token=self._auth_token,
            model=self._model,
            permissions=self._permissions,
            fallback_models=self._fallback_models,
            exhaustion=self._exhaustion,
            max_turns=self._max_turns,
            job_timeout_s=self._job_timeout_s,
        )

    async def get_or_create_session(
        self, session_id: str | None = None
    ) -> tuple[AgentManagedSession, str, bool]:
        async with self._lock:
            if session_id:
                lookup = self._temp_to_real.get(session_id, session_id)
                if lookup in self._sessions:
                    return self._sessions[lookup], lookup, False
                if lookup in self._pending:
                    return self._pending[lookup], lookup, False

            temp_id = session_id if session_id else f"pending_{uuid.uuid4().hex[:8]}"
            session = self._make_session()
            self._pending[temp_id] = session
            return session, temp_id, True

    async def register_real_session_id(
        self, temp_id: str, real_session_id: str
    ) -> bool:
        async with self._lock:
            session = self._pending.pop(temp_id, None)
            if session is None:
                session = self._sessions.get(temp_id)
            if session is None:
                return False
            self._sessions[real_session_id] = session
            self._temp_to_real[temp_id] = real_session_id
            self._real_to_temp[real_session_id] = temp_id
            return True

    async def remove_session(self, session_id: str) -> bool:
        async with self._lock:
            lookup = self._temp_to_real.get(session_id, session_id)
            session = self._sessions.pop(lookup, None) or self._pending.pop(
                lookup, None
            )
            if session is None:
                return False
            temp = self._real_to_temp.pop(lookup, None)
            if temp is not None:
                self._temp_to_real.pop(temp, None)
            await session.stop()
            return True

    async def stop_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values()) + list(self._pending.values())
            self._sessions.clear()
            self._pending.clear()
            self._temp_to_real.clear()
            self._real_to_temp.clear()
        for session in sessions:
            await session.stop()

    def get_stats(self) -> dict[str, Any]:
        return {
            "backend": "agent",
            "active_sessions": len(self._sessions),
            "pending_sessions": len(self._pending),
        }
