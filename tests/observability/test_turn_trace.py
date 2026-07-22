"""Tests for per-turn trace summaries reconstructed from the server log."""

import json

from free_claude_code.observability import format_summary, summarize_turns
from free_claude_code.observability.turn_trace import iter_records


def _row(**fields: object) -> str:
    return json.dumps(fields)


def _slow_turn_lines() -> list[str]:
    rid = "req_slow"
    return [
        _row(
            time="2026-07-21 23:38:00.000000+02:00",
            level="INFO",
            message="TRACE free_claude_code.api.route.resolved",
            event="free_claude_code.api.route.resolved",
            stage="routing",
            request_id=rid,
            provider_id="cerebras",
            gateway_model="gpt-oss-120b",
        ),
        _row(
            time="2026-07-21 23:38:05.000000+02:00",
            level="WARNING",
            message="Provider rate limit set for 8.5s (reactive)",
            module="free_claude_code.providers.rate_limit",
            request_id=rid,
        ),
        _row(
            time="2026-07-21 23:38:20.000000+02:00",
            level="WARNING",
            message="Upstream provider returned HTTP 429 Too Many Requests",
            request_id=rid,
        ),
        _row(
            time="2026-07-21 23:38:30.000000+02:00",
            level="WARNING",
            message="Provider rate limit set for 16.8s (reactive)",
            module="free_claude_code.providers.rate_limit",
            request_id=rid,
        ),
        _row(
            time="2026-07-21 23:38:45.000000+02:00",
            level="INFO",
            message="TRACE free_claude_code.api.response.stream_completed",
            event="free_claude_code.api.response.stream_completed",
            stage="egress",
            outcome="ok",
            stream_chunks=7,
            request_id=rid,
            provider_id="cerebras",
            gateway_model="gpt-oss-120b",
            fallback_candidates=["cerebras/gpt-oss-120b", "groq/llama-3.3-70b"],
        ),
    ]


def _fast_turn_lines() -> list[str]:
    rid = "req_fast"
    return [
        _row(
            time="2026-07-21 23:40:00.000000+02:00",
            level="INFO",
            message="TRACE received",
            event="free_claude_code.api.request.received",
            request_id=rid,
            provider_id="groq",
            gateway_model="llama-3.3-70b",
        ),
        _row(
            time="2026-07-21 23:40:01.200000+02:00",
            level="INFO",
            message="TRACE free_claude_code.api.response.stream_completed",
            event="free_claude_code.api.response.stream_completed",
            outcome="ok",
            stream_chunks=3,
            request_id=rid,
            provider_id="groq",
            gateway_model="llama-3.3-70b",
        ),
    ]


def test_slow_turn_rolls_up_rate_limit_wait() -> None:
    (turn,) = summarize_turns(_slow_turn_lines())
    assert turn.request_id == "req_slow"
    assert turn.duration_s == 45.0
    assert turn.rate_limit_wait_s == 8.5 + 16.8
    assert turn.rate_limit_blocks == 2
    assert turn.http_429 == 1
    assert turn.provider_id == "cerebras"
    assert turn.gateway_model == "gpt-oss-120b"
    assert turn.outcome == "ok"
    assert turn.stream_chunks == 7
    assert turn.fallback_candidates == [
        "cerebras/gpt-oss-120b",
        "groq/llama-3.3-70b",
    ]
    # Most of the turn was rate-limit backoff, which is the whole point.
    assert turn.rate_limit_fraction > 0.5


def test_multiple_turns_preserve_order_and_isolation() -> None:
    turns = summarize_turns(_slow_turn_lines() + _fast_turn_lines())
    assert [t.request_id for t in turns] == ["req_slow", "req_fast"]
    fast = turns[1]
    assert fast.duration_s == 1.2
    assert fast.rate_limit_wait_s == 0.0
    assert fast.rate_limit_fraction == 0.0


def test_iter_records_skips_non_json_and_rows_without_request_id() -> None:
    lines = [
        "not json at all",
        "",
        json.dumps({"time": "t", "message": "no request id here"}),
        json.dumps({"request_id": "req_ok", "message": "kept"}),
    ]
    kept = list(iter_records(lines))
    assert len(kept) == 1
    assert kept[0]["request_id"] == "req_ok"


def test_format_summary_is_readable() -> None:
    (turn,) = summarize_turns(_slow_turn_lines())
    text = format_summary(turn)
    assert "req_slow" in text
    assert "rate-limit" in text
    assert "cerebras/gpt-oss-120b" in text


def test_served_model_from_fallback_overrides_routed_primary() -> None:
    lines = _slow_turn_lines()
    lines.insert(
        3,
        _row(
            time="2026-07-21 23:38:31.000000+02:00",
            level="INFO",
            message=(
                "precommit_fallback.serving request_id=req_slow "
                "model=gemini/gemini-flash-latest provider=gemini"
            ),
            request_id="req_slow",
        ),
    )
    (turn,) = summarize_turns(lines)
    assert turn.served_model == "gemini/gemini-flash-latest"
    text = format_summary(turn)
    assert "gemini/gemini-flash-latest" in text
    assert "routed: cerebras/gpt-oss-120b" in text
