"""Small local calibration harness (`fcc-verdict benchmark`).

Runs a handful of tiny predefined tasks against discovered free models to seed
the empirical selection stats: JSON compliance, reliability and rough
correctness. Deliberately small and configurable — it must not burn hundreds of
calls.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .invoker import ModelInvoker
from .models import ModelRef
from .parsing import extract_json_object
from .storage import VerdictStore

Checker = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class BenchTask:
    category: str
    system: str
    user: str
    checker: Checker


def _contains(*needles: str) -> Checker:
    lowered = [n.lower() for n in needles]
    return lambda text: any(n in text.lower() for n in lowered)


def _json_has(key: str) -> Checker:
    def check(text: str) -> bool:
        obj = extract_json_object(text)
        return obj is not None and key in obj

    return check


# One tiny task per required competency. Answers are checkable deterministically.
BENCH_TASKS: tuple[BenchTask, ...] = (
    BenchTask(
        category="general_reasoning",
        system="Answer with just the final number.",
        user="A bat and a ball cost 1.10 in total. The bat costs 1.00 more than "
        "the ball. How much does the ball cost? Answer in dollars.",
        checker=_contains("0.05", ".05", "5 cent"),
    ),
    BenchTask(
        category="software_engineering",
        system="Return only a Python one-liner.",
        user="Write a Python expression that reverses the string s.",
        checker=_contains("[::-1]", "reversed("),
    ),
    BenchTask(
        category="debugging",
        system="Answer in one short sentence.",
        user="This Python raises 'IndexError: list index out of range' on "
        "`xs[len(xs)]`. What is the fix?",
        checker=_contains("len(xs) - 1", "len(xs)-1", "index", "-1", "off-by-one"),
    ),
    BenchTask(
        category="architecture",
        system="Answer with one word: the pattern name.",
        user="Which pattern decouples a producer from consumers via an "
        "intermediate buffer they poll? One word.",
        checker=_contains("queue", "message", "broker", "pub", "buffer"),
    ),
    BenchTask(
        category="general_reasoning",
        system="Follow the format exactly.",
        user="Reply with EXACTLY the text: OK-123 and nothing else.",
        checker=lambda text: text.strip() == "OK-123" or "OK-123" in text,
    ),
    BenchTask(
        category="document_analysis",
        system="Respond with one JSON object only.",
        user='Return a JSON object with a key "answer" whose value is the '
        "capital of France.",
        checker=lambda text: _json_has("answer")(text) and _contains("paris")(text),
    ),
)


@dataclass(frozen=True, slots=True)
class BenchResult:
    model_key: str
    task_category: str
    passed: bool
    json_ok: bool
    latency_s: float


async def run_benchmark(
    invoker: ModelInvoker,
    models: list[ModelRef],
    *,
    store: VerdictStore | None = None,
    max_models: int = 6,
    max_tasks: int | None = None,
    max_tokens: int = 512,
) -> list[BenchResult]:
    """Benchmark up to ``max_models`` models on the calibration tasks."""
    tasks = BENCH_TASKS if max_tasks is None else BENCH_TASKS[:max_tasks]
    results: list[BenchResult] = []
    for model in models[:max_models]:
        for task in tasks:
            invocation = await invoker.invoke(
                model,
                task.system,
                task.user,
                max_tokens=max_tokens,
                request_id=f"bench:{task.category}",
            )
            passed = bool(invocation.ok and task.checker(invocation.text))
            json_ok = bool(
                invocation.ok and extract_json_object(invocation.text) is not None
            )
            results.append(
                BenchResult(
                    model_key=model.key,
                    task_category=task.category,
                    passed=passed,
                    json_ok=json_ok,
                    latency_s=invocation.latency_s,
                )
            )
            if store is not None:
                store.record_invocation(
                    model.key,
                    task.category,
                    ok=passed,
                    json_ok=json_ok,
                    rate_limited=False,
                    latency_s=invocation.latency_s,
                )
                # Treat a correct answer as a strong cross-review-equivalent signal.
                store.record_cross_review_score(
                    model.key, task.category, 1.0 if passed else 0.0
                )
    return results
