"""Phase 4: managed adapter, remote approvals, job queue."""

import asyncio

import pytest

from free_claude_code.agent.jobs import AgentJobQueue, JobStatus
from free_claude_code.agent.loop import AgentStopReason
from free_claude_code.agent.managed_adapter import AgentManagedSession
from free_claude_code.agent.permissions import AllowlistPermissionGate
from free_claude_code.agent.proxy_client import AssistantTurn
from free_claude_code.agent.remote_permissions import (
    ApprovalBroker,
    RemotePermissionGate,
)
from free_claude_code.agent.workspace import Workspace
from free_claude_code.messaging.event_parser import parse_cli_event


@pytest.mark.asyncio
async def test_managed_adapter_emits_session_info_assistant_exit(tmp_path, monkeypatch):
    scripted = AgentManagedSession(
        workspace=Workspace(tmp_path),
        proxy_root_url="http://127.0.0.1:9",
        permissions=AllowlistPermissionGate(auto_approve=True),
    )

    import free_claude_code.agent.managed_adapter as mod

    class _Loop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, prompt, prior_messages=None):
            from free_claude_code.agent.loop import AgentResult

            return AgentResult(
                final_text="hello from agent",
                stop_reason=AgentStopReason.COMPLETED,
                turns=1,
                messages=[{"role": "user", "content": prompt}],
            )

    monkeypatch.setattr(mod, "AgentLoop", _Loop)
    events = [event async for event in scripted.start_task("do the thing")]

    types = [e.get("type") for e in events]
    assert types == ["session_info", "assistant", "exit"]
    assert events[-1]["code"] == 0

    parsed = []
    for event in events:
        parsed.extend(parse_cli_event(event))
    assert any(p.get("type") == "text_chunk" for p in parsed)
    assert any(p.get("type") == "complete" for p in parsed)


@pytest.mark.asyncio
async def test_remote_permission_timeout_denies():
    broker = ApprovalBroker(timeout_s=0.05)
    gate = RemotePermissionGate(broker)
    decision = await gate.check("bash", {"command": "echo hi"})
    assert decision.allowed is False
    assert "timed out" in decision.reason or "denied" in decision.reason


@pytest.mark.asyncio
async def test_remote_permission_approve_via_resolve():
    broker = ApprovalBroker(timeout_s=2.0)
    gate = RemotePermissionGate(broker)

    async def approve_soon(req):
        await asyncio.sleep(0.01)
        assert broker.resolve(req.request_id, approved=True)

    broker.on_request = approve_soon
    decision = await gate.check("write", {"path": "a.txt", "content": "x"})
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_remote_permission_deny_via_resolve():
    broker = ApprovalBroker(timeout_s=2.0)
    gate = RemotePermissionGate(broker)

    async def deny_soon(req):
        await asyncio.sleep(0.01)
        assert broker.resolve(req.request_id, approved=False)

    broker.on_request = deny_soon
    decision = await gate.check(
        "edit", {"path": "a.txt", "old_string": "x", "new_string": "y"}
    )
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_job_queue_runs_and_reports(tmp_path):
    workspace = Workspace(tmp_path)
    permissions = AllowlistPermissionGate(auto_approve=True)

    class _Client:
        async def complete(self, **_kwargs):
            return AssistantTurn(
                content=[{"type": "text", "text": "job ok"}],
                stop_reason="end_turn",
            )

    queue = AgentJobQueue(
        workspace=workspace,
        client=_Client(),
        permissions=permissions,
        job_timeout_s=30.0,
        max_concurrent=1,
    )
    await queue.start()
    job = queue.enqueue("say hi")
    for _ in range(50):
        current = queue.get(job.job_id)
        assert current is not None
        if current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED}:
            break
        await asyncio.sleep(0.05)
    done = queue.get(job.job_id)
    assert done is not None
    assert done.status == JobStatus.COMPLETED
    assert done.result is not None
    assert done.result.final_text == "job ok"
    await queue.stop()


@pytest.mark.asyncio
async def test_job_queue_timeout_fails(tmp_path):
    workspace = Workspace(tmp_path)
    permissions = AllowlistPermissionGate(auto_approve=True)

    class _SlowClient:
        async def complete(self, **_kwargs):
            await asyncio.sleep(1.0)
            return AssistantTurn(
                content=[{"type": "text", "text": "late"}],
                stop_reason="end_turn",
            )

    queue = AgentJobQueue(
        workspace=workspace,
        client=_SlowClient(),
        permissions=permissions,
        job_timeout_s=0.05,
        max_concurrent=1,
    )
    await queue.start()
    job = queue.enqueue("slow")
    for _ in range(50):
        current = queue.get(job.job_id)
        assert current is not None
        if current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED}:
            break
        await asyncio.sleep(0.05)
    done = queue.get(job.job_id)
    assert done is not None
    assert done.status == JobStatus.FAILED
    assert "timeout" in done.detail
    await queue.stop()
