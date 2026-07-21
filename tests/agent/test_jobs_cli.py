"""Tests for fcc-agent jobs CLI and disk-backed store."""

import json
from pathlib import Path
from unittest.mock import patch

from free_claude_code.agent.cli import main
from free_claude_code.agent.job_store import create_job, load_job
from free_claude_code.agent.jobs import JobStatus


def test_create_and_load_job(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FCC_AGENT_JOBS_DIR", str(tmp_path))
    job = create_job(
        prompt="say hi",
        workspace=str(tmp_path),
        model="claude-sonnet-4-5",
        jobs_dir=tmp_path,
    )
    loaded = load_job(job.job_id, jobs_dir=tmp_path)
    assert loaded is not None
    assert loaded.prompt == "say hi"
    assert loaded.status == JobStatus.QUEUED.value
    assert (tmp_path / f"{job.job_id}.json").is_file()


def test_jobs_enqueue_prints_id_and_spawns(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("FCC_AGENT_JOBS_DIR", str(tmp_path))
    with (
        patch("free_claude_code.agent.cli.get_settings") as settings_fn,
        patch(
            "free_claude_code.agent.cli.spawn_job_worker", return_value=4242
        ) as spawn,
    ):
        settings = settings_fn.return_value
        settings.allowed_dir = str(tmp_path)
        settings.model = "claude-sonnet-4-5"
        settings.messaging_agent_job_timeout_s = 60.0
        main(["jobs", "enqueue", "--workspace", str(tmp_path), "do the thing"])
    out = capsys.readouterr().out.strip()
    assert out
    job_id = out
    spawn.assert_called_once_with(job_id)
    record = json.loads((tmp_path / f"{job_id}.json").read_text(encoding="utf-8"))
    assert record["prompt"] == "do the thing"
    assert record["pid"] == 4242
    assert record["auto_approve"] is True


def test_jobs_status_and_result(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("FCC_AGENT_JOBS_DIR", str(tmp_path))
    job = create_job(
        prompt="x",
        workspace=str(tmp_path),
        model="m",
        jobs_dir=tmp_path,
    )
    job.status = JobStatus.COMPLETED.value
    job.final_text = "done"
    job.save(tmp_path)

    main(["jobs", "status", job.job_id])
    status_out = capsys.readouterr().out
    assert f"job_id={job.job_id}" in status_out
    assert "status=completed" in status_out

    main(["jobs", "result", job.job_id])
    assert capsys.readouterr().out.strip() == "done"


def test_jobs_result_fails_when_running(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FCC_AGENT_JOBS_DIR", str(tmp_path))
    job = create_job(
        prompt="x",
        workspace=str(tmp_path),
        model="m",
        jobs_dir=tmp_path,
    )
    job.status = JobStatus.RUNNING.value
    job.save(tmp_path)
    try:
        main(["jobs", "result", job.job_id])
        raised = False
    except SystemExit as exc:
        raised = True
        assert exc.code == 3
    assert raised
