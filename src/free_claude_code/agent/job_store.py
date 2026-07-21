"""Disk-backed agent job records for SSH / CLI unattended runs (B6)."""

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from free_claude_code.config.paths import agent_jobs_dir_path

from .jobs import JobStatus


@dataclass(slots=True)
class PersistedAgentJob:
    """Serializable job record written under ``~/.fcc/agent_jobs/``."""

    job_id: str
    prompt: str
    workspace: str
    model: str
    status: str = JobStatus.QUEUED.value
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    final_text: str = ""
    detail: str = ""
    stop_reason: str = ""
    max_turns: int = 40
    job_timeout_s: float = 600.0
    auto_approve: bool = True
    pid: int | None = None

    def path(self, jobs_dir: Path | None = None) -> Path:
        root = jobs_dir if jobs_dir is not None else agent_jobs_dir_path()
        return root / f"{self.job_id}.json"

    def save(self, jobs_dir: Path | None = None) -> Path:
        path = self.path(jobs_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path


def load_job(job_id: str, jobs_dir: Path | None = None) -> PersistedAgentJob | None:
    root = jobs_dir if jobs_dir is not None else agent_jobs_dir_path()
    path = root / f"{job_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PersistedAgentJob(**raw)


def create_job(
    *,
    prompt: str,
    workspace: str,
    model: str,
    max_turns: int = 40,
    job_timeout_s: float = 600.0,
    auto_approve: bool = True,
    jobs_dir: Path | None = None,
) -> PersistedAgentJob:
    job = PersistedAgentJob(
        job_id=uuid.uuid4().hex[:12],
        prompt=prompt,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        job_timeout_s=job_timeout_s,
        auto_approve=auto_approve,
    )
    job.save(jobs_dir)
    return job


def spawn_job_worker(job_id: str, *, jobs_dir: Path | None = None) -> int:
    """Start a detached worker that runs ``fcc-agent jobs _run <job_id>``."""

    env = dict(os.environ)
    if jobs_dir is not None:
        env["FCC_AGENT_JOBS_DIR"] = str(jobs_dir)
    command = [
        sys.executable,
        "-m",
        "free_claude_code.agent.cli",
        "jobs",
        "_run",
        job_id,
    ]
    log_root = jobs_dir if jobs_dir is not None else agent_jobs_dir_path()
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"{job_id}.log"
    log_file = log_path.open("a", encoding="utf-8")
    if sys.platform == "win32":
        create_new = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            close_fds=True,
            creationflags=create_new,
        )
    else:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
    log_file.close()
    return int(process.pid)
