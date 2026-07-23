"""Safe default logging tests for the application runtime owner."""

import logging

import pytest

from llmux.runtime.application import best_effort


@pytest.mark.asyncio
async def test_best_effort_default_logs_exclude_exception_text(caplog):
    async def boom():
        raise ValueError("SECRET_SHUTDOWN")

    with caplog.at_level(logging.WARNING):
        await best_effort("test_step", boom(), log_verbose_errors=False)

    blob = " | ".join(record.getMessage() for record in caplog.records)
    assert "SECRET_SHUTDOWN" not in blob
    assert "exc_type=ValueError" in blob


@pytest.mark.asyncio
async def test_best_effort_verbose_includes_exception_text(caplog):
    async def boom():
        raise ValueError("VISIBLE_SHUTDOWN")

    with caplog.at_level(logging.WARNING):
        await best_effort("test_step", boom(), log_verbose_errors=True)

    blob = " | ".join(record.getMessage() for record in caplog.records)
    assert "VISIBLE_SHUTDOWN" in blob
