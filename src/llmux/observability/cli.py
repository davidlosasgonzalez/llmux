"""``llmux-trace`` — explain what happened in a proxy turn from ``server.log``.

Examples
--------
    llmux-trace                 # slowest recent turns
    llmux-trace --last          # the most recent turn
    llmux-trace req_abc123      # one turn by id
    llmux-trace --slow 20       # turns longer than 20s
    llmux-trace --json          # machine-readable
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from llmux.config.paths import server_log_path
from llmux.observability.turn_trace import (
    TurnSummary,
    format_summary,
    summarize_turns,
)


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"log file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _select(turns: list[TurnSummary], args: argparse.Namespace) -> list[TurnSummary]:
    if args.request_id:
        matches = [t for t in turns if t.request_id == args.request_id]
        if not matches:
            raise SystemExit(f"no turn found for request_id {args.request_id}")
        return matches
    if args.last:
        return turns[-1:] if turns else []
    if args.slow is not None:
        picked = [t for t in turns if t.duration_s >= args.slow]
    else:
        picked = list(turns)
    picked.sort(key=lambda t: t.duration_s, reverse=True)
    return picked[: args.top]


def _to_json(turns: Sequence[TurnSummary]) -> str:
    rows = []
    for turn in turns:
        row = asdict(turn)
        row["start"] = turn.start.isoformat() if turn.start else None
        row["end"] = turn.end.isoformat() if turn.end else None
        row["duration_s"] = round(turn.duration_s, 3)
        row["rate_limit_fraction"] = round(turn.rate_limit_fraction, 3)
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llmux-trace",
        description="Summarise proxy turns from the structured server log.",
    )
    parser.add_argument(
        "request_id", nargs="?", help="show a single turn by request id"
    )
    parser.add_argument(
        "--last", action="store_true", help="show only the most recent turn"
    )
    parser.add_argument(
        "--slow",
        type=float,
        metavar="SECONDS",
        help="only turns at least SECONDS long",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        metavar="N",
        help="show the N slowest matching turns (default 5)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="path to server.log (default: ~/.llmux/logs/server.log)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    log_path = args.log if args.log is not None else server_log_path()
    turns = summarize_turns(_read_lines(log_path))
    selected = _select(turns, args)

    if args.json:
        print(_to_json(selected))
        return 0

    if not selected:
        print("no matching turns")
        return 0

    print("\n\n".join(format_summary(turn) for turn in selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
