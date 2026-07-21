"""Own-agent harness: local coding agent that talks to the FCC proxy."""

from .loop import AgentLoop, AgentResult, AgentStopReason
from .workspace import Workspace

__all__ = ["AgentLoop", "AgentResult", "AgentStopReason", "Workspace"]
