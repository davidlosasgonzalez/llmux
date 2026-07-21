"""Workspace tools: read, edit, write, bash, grep, glob."""

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from free_claude_code.core.anthropic.streaming.recovery import (
    ToolSchema,
    validate_tool_input,
)

from .breakers import BreakerTrip, CircuitBreakers
from .permissions import PermissionDecision, PermissionPort
from .workspace import Workspace, WorkspaceError


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolExecution:
    content: str
    is_error: bool = False
    trip: BreakerTrip | None = None


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read",
        description="Read a UTF-8 text file under the workspace root.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to read."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="write",
        description="Create or overwrite a UTF-8 text file under the workspace root.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="edit",
        description=(
            "Replace the first occurrence of `old_string` with `new_string` in a file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="bash",
        description="Run a shell command with cwd=workspace root. Returns stdout+stderr.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="grep",
        description="Search file contents under the workspace for a regex pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Optional subdirectory or file to search.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="glob",
        description="List files under the workspace matching a glob pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob, e.g. **/*.py"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
)


def anthropic_tool_defs() -> list[dict[str, Any]]:
    """Serialize tool specs for a Messages API ``tools`` array."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in TOOL_SPECS
    ]


def tool_schemas() -> dict[str, ToolSchema]:
    """Schemas for happy-path ``validate_tool_input`` (A6)."""
    return {
        spec.name: ToolSchema(name=spec.name, input_schema=spec.input_schema)
        for spec in TOOL_SPECS
    }


class ToolRegistry:
    """Dispatch tool_use blocks against a confined workspace."""

    def __init__(
        self,
        workspace: Workspace,
        permissions: PermissionPort,
        breakers: CircuitBreakers | None = None,
        *,
        bash_timeout_s: float = 30.0,
    ) -> None:
        self._workspace = workspace
        self._permissions = permissions
        self._breakers = breakers or CircuitBreakers()
        self._bash_timeout_s = bash_timeout_s
        self._schemas = tool_schemas()

    @property
    def breakers(self) -> CircuitBreakers:
        return self._breakers

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        if not validate_tool_input(name, arguments, self._schemas):
            return ToolExecution(
                content=(
                    f"Invalid arguments for tool `{name}`: does not match input_schema. "
                    "Fix the JSON and retry."
                ),
                is_error=True,
            )
        decision: PermissionDecision = await self._permissions.check(name, arguments)
        if not decision.allowed:
            return ToolExecution(
                content=f"Permission denied: {decision.reason}",
                is_error=True,
            )
        try:
            if name == "read":
                return self._read(arguments)
            if name == "write":
                return self._write(arguments)
            if name == "edit":
                return self._edit(arguments)
            if name == "bash":
                return self._bash(arguments)
            if name == "grep":
                return self._grep(arguments)
            if name == "glob":
                return self._glob(arguments)
        except WorkspaceError as exc:
            return ToolExecution(content=str(exc), is_error=True)
        except (OSError, ValueError, re.error) as exc:
            return ToolExecution(content=f"{type(exc).__name__}: {exc}", is_error=True)
        return ToolExecution(content=f"Unknown tool: {name}", is_error=True)

    def _read(self, arguments: dict[str, Any]) -> ToolExecution:
        path = self._workspace.resolve(str(arguments.get("path", "")))
        if not path.is_file():
            return ToolExecution(content=f"not a file: {path}", is_error=True)
        text = path.read_text(encoding="utf-8")
        trip = self._breakers.note_read(str(path.relative_to(self._workspace.root)))
        return ToolExecution(content=text, trip=trip)

    def _write(self, arguments: dict[str, Any]) -> ToolExecution:
        path = self._workspace.resolve(str(arguments.get("path", "")))
        content = str(arguments.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = str(path.relative_to(self._workspace.root))
        trip = self._breakers.note_write(rel, content)
        return ToolExecution(content=f"wrote {len(content)} bytes to {rel}", trip=trip)

    def _edit(self, arguments: dict[str, Any]) -> ToolExecution:
        path = self._workspace.resolve(str(arguments.get("path", "")))
        old = str(arguments.get("old_string", ""))
        new = str(arguments.get("new_string", ""))
        if not path.is_file():
            return ToolExecution(content=f"not a file: {path}", is_error=True)
        before = path.read_text(encoding="utf-8")
        if old not in before:
            return ToolExecution(
                content="old_string not found in file",
                is_error=True,
            )
        after = before.replace(old, new, 1)
        path.write_text(after, encoding="utf-8")
        rel = str(path.relative_to(self._workspace.root))
        trip = self._breakers.note_edit(rel, after)
        return ToolExecution(content=f"edited {rel}", trip=trip)

    def _bash(self, arguments: dict[str, Any]) -> ToolExecution:
        command = str(arguments.get("command", ""))
        if not command.strip():
            return ToolExecution(content="empty command", is_error=True)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self._workspace.root,
                capture_output=True,
                text=True,
                timeout=self._bash_timeout_s,
            )
        except subprocess.TimeoutExpired:
            trip = self._breakers.note_bash(command, ok=False)
            return ToolExecution(
                content=f"timeout after {self._bash_timeout_s:.0f}s",
                is_error=True,
                trip=trip,
            )
        out_parts = []
        if completed.stdout:
            out_parts.append(completed.stdout)
        if completed.stderr:
            out_parts.append(completed.stderr)
        body = "".join(out_parts) or "(no output)"
        if completed.returncode != 0:
            body = f"exit {completed.returncode}\n{body}"
        trip = self._breakers.note_bash(command, ok=completed.returncode == 0)
        return ToolExecution(
            content=body,
            is_error=completed.returncode != 0,
            trip=trip,
        )

    def _grep(self, arguments: dict[str, Any]) -> ToolExecution:
        pattern = str(arguments.get("pattern", ""))
        compiled = re.compile(pattern)
        rel_path = str(arguments.get("path") or ".")
        target = self._workspace.resolve(rel_path)
        hits: list[str] = []
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for file_path in files:
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError, OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    rel = file_path.relative_to(self._workspace.root)
                    hits.append(f"{rel}:{line_no}:{line}")
                    if len(hits) >= 200:
                        break
            if len(hits) >= 200:
                break
        self._breakers.note_grep_or_glob()
        return ToolExecution(content="\n".join(hits) if hits else "(no matches)")

    def _glob(self, arguments: dict[str, Any]) -> ToolExecution:
        pattern = str(arguments.get("pattern", ""))
        # Path.glob does not allow leading **/ on some versions when absolute;
        # search from root with the given pattern.
        matches = sorted(
            str(p.relative_to(self._workspace.root))
            for p in self._workspace.root.glob(pattern)
            if p.is_file()
        )
        self._breakers.note_grep_or_glob()
        return ToolExecution(content="\n".join(matches) if matches else "(no matches)")
