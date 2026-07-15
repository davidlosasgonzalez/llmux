"""Unit coverage for the T1/T2 fixes: latency penalty and critique integrity."""

import pytest

from free_claude_code.council.models import Critique, Verdict
from free_claude_code.council.scoring import latency_penalty
from free_claude_code.council.storage import ModelStats


def _stats(avg_latency: float) -> ModelStats:
    # avg_latency == latency_sum / valid, so one valid call carries the average.
    valid = 0 if avg_latency <= 0.0 else 1
    return ModelStats(
        model_key="m",
        category="c",
        requests=valid,
        valid=valid,
        latency_sum=avg_latency,
    )


def test_latency_penalty_no_penalty_below_threshold():
    assert latency_penalty(_stats(0.0)) == 1.0  # no data => neutral
    assert latency_penalty(_stats(5.0)) == 1.0
    assert latency_penalty(_stats(20.0)) == 1.0


def test_latency_penalty_ramps_down_then_floors():
    # 60s: 1 - (60-20)/120 = 0.667
    assert latency_penalty(_stats(60.0)) == pytest.approx(2.0 / 3.0)
    # A 159s/call model (the real synthesiser that caused 10-min runs) hits the
    # floor and can no longer win selection against fast peers.
    assert latency_penalty(_stats(159.0)) == 0.35
    assert latency_penalty(_stats(10_000.0)) == 0.35


def _critique(verdict: Verdict, score: float, **issues) -> Critique:
    return Critique(model_key="c", verdict=verdict, score=score, **issues)


def test_pass_is_always_informative():
    assert _critique(Verdict.PASS, 0.9).is_informative


def test_degenerate_revise_is_not_informative():
    # verdict != pass, score 0, no issues => the exact garbage free models emit.
    assert not _critique(Verdict.REVISE, 0.0).is_informative
    assert not _critique(Verdict.REJECT, 0.0).is_informative


def test_revise_with_signal_is_informative():
    assert _critique(Verdict.REVISE, 0.5).is_informative  # non-zero score
    assert _critique(
        Verdict.REVISE, 0.0, material_issues=["real defect"]
    ).is_informative  # zero score but a concrete issue
