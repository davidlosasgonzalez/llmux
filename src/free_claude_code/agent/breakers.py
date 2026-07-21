"""Circuit breakers that halt runaway agent loops."""

from dataclasses import dataclass, field
from enum import StrEnum


class BreakerKind(StrEnum):
    BASH_FAILURES = "bash_failures"
    EDIT_REVERT = "edit_revert"
    STALE_READ = "stale_read"
    SCHEMA_REPAIR = "schema_repair"


@dataclass(frozen=True, slots=True)
class BreakerTrip:
    kind: BreakerKind
    detail: str


@dataclass(slots=True)
class CircuitBreakers:
    """Track anti-loop signals across tool executions.

    Trips when:
    - the same bash command fails 3 times in a row;
    - the same path is edited then reverted twice (A→B→A→B→A);
    - the same path is read 3 times with no intervening mutating tool.
    """

    max_bash_failures: int = 3
    max_edit_reverts: int = 2
    max_stale_reads: int = 3

    _bash_fail_cmd: str | None = None
    _bash_fail_count: int = 0
    _edit_history: list[tuple[str, str]] = field(default_factory=list)
    _read_counts: dict[str, int] = field(default_factory=dict)

    def note_bash(self, command: str, *, ok: bool) -> BreakerTrip | None:
        if ok:
            self._bash_fail_cmd = None
            self._bash_fail_count = 0
            self._mark_progress()
            return None
        if command == self._bash_fail_cmd:
            self._bash_fail_count += 1
        else:
            self._bash_fail_cmd = command
            self._bash_fail_count = 1
        if self._bash_fail_count >= self.max_bash_failures:
            return BreakerTrip(
                BreakerKind.BASH_FAILURES,
                f"bash command failed {self._bash_fail_count}x: {command!r}",
            )
        return None

    def note_edit(self, path: str, after: str) -> BreakerTrip | None:
        self._mark_progress()
        self._edit_history.append((path, after))
        # Count A→B→A cycles on the same path (each revert is one cycle).
        path_edits = [content for p, content in self._edit_history if p == path]
        reverts = 0
        for index in range(2, len(path_edits)):
            if (
                path_edits[index] == path_edits[index - 2]
                and path_edits[index] != path_edits[index - 1]
            ):
                reverts += 1
        if reverts >= self.max_edit_reverts:
            return BreakerTrip(
                BreakerKind.EDIT_REVERT,
                f"edit/revert oscillated {reverts}x on {path}",
            )
        return None

    def note_write(self, path: str, content: str) -> BreakerTrip | None:
        return self.note_edit(path, after=content)

    def note_read(self, path: str) -> BreakerTrip | None:
        count = self._read_counts.get(path, 0) + 1
        self._read_counts[path] = count
        if count >= self.max_stale_reads:
            return BreakerTrip(
                BreakerKind.STALE_READ,
                f"read {path!r} {count}x without progress",
            )
        return None

    def note_grep_or_glob(self) -> None:
        """Search tools count as progress for the stale-read breaker."""
        self._mark_progress()

    def _mark_progress(self) -> None:
        self._read_counts.clear()
