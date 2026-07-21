"""Shared fixtures for agent harness tests."""

from pathlib import Path

import pytest

from free_claude_code.agent.workspace import Workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)
