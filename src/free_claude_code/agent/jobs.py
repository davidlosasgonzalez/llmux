"""Unattended agent job queue with budget/time limits (A13)."""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .loop import AgentLoop, AgentResult, AgentStopReason
from .permissions import PermissionPort
from .proxy_client import ProxyClient
from .tools import ToolRegistry
from .workspace import Workspace


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(slots=True)
class AgentJob:
    job_id: str
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: AgentResult | None = None
    detail: str = ""


@dataclass(slots=True)
class AgentJobQueue:
    """FIFO queue of AgentLoop runs with concurrency + timeout caps.

    Used by unit tests and as an in-process library. SSH/CLI unattended work
    goes through ``job_store`` + ``fcc-agent jobs`` (disk-backed).
    """

    workspace: Workspace
    client: ProxyClient
    permissions: PermissionPort
    model: str = "claude-sonnet-4-5"
    max_turns: int = 40
    job_timeout_s: float = 600.0
    max_concurrent: int = 1
    on_complete: Callable[[AgentJob], Any] | None = None

    _queue: asyncio.Queue[AgentJob] = field(default_factory=asyncio.Queue)
    _jobs: dict[str, AgentJob] = field(default_factory=dict)
    _workers: list[asyncio.Task[None]] = field(default_factory=list)
    _stopped: bool = False

    def enqueue(self, prompt: str) -> AgentJob:
        if self._stopped:
            raise RuntimeError("job queue is stopped")
        job = AgentJob(job_id=uuid.uuid4().hex[:12], prompt=prompt)
        self._jobs[job.job_id] = job
        self._queue.put_nowait(job)
        return job

    def get(self, job_id: str) -> AgentJob | None:
        return self._jobs.get(job_id)

    async def start(self) -> None:
        if self._workers:
            return
        for _ in range(max(1, self.max_concurrent)):
            self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        self._stopped = True
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _worker(self) -> None:
        while not self._stopped:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await self._run_job(job)

    async def _run_job(self, job: AgentJob) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        tools = ToolRegistry(self.workspace, self.permissions)
        loop = AgentLoop(
            client=self.client,
            workspace=self.workspace,
            permissions=self.permissions,
            tools=tools,
            model=self.model,
            max_turns=self.max_turns,
        )
        try:
            result = await asyncio.wait_for(
                loop.run(job.prompt), timeout=self.job_timeout_s
            )
            job.result = result
            if result.stop_reason == AgentStopReason.COMPLETED:
                job.status = JobStatus.COMPLETED
            else:
                job.status = JobStatus.FAILED
                job.detail = result.detail or result.stop_reason.value
        except TimeoutError:
            job.status = JobStatus.FAILED
            job.detail = f"exceeded job_timeout_s={self.job_timeout_s}"
        except asyncio.CancelledError:
            job.status = JobStatus.STOPPED
            job.detail = "cancelled"
            raise
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.detail = str(exc)
        finally:
            job.finished_at = time.time()
            if self.on_complete is not None:
                maybe = self.on_complete(job)
                if asyncio.iscoroutine(maybe):
                    await maybe
