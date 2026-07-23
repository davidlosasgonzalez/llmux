"""Phase 1: deterministic task classification.

Keyword rules only — no LLM call is spent on classification unless a caller
explicitly opts in elsewhere. Ambiguous input falls back to general reasoning.
"""

import re

from .models import TaskType

# Ordered by specificity: the first category whose signals dominate wins ties by
# appearing earlier. Each signal is a whole-word pattern to avoid substring noise.
_SIGNALS: tuple[tuple[TaskType, tuple[str, ...]], ...] = (
    (
        TaskType.ADVERSARIAL_REVIEW,
        (
            "adversarial",
            "red team",
            "red-team",
            "poke holes",
            "prove.*wrong",
            "attack this",
            "steelman",
            "devil.s advocate",
        ),
    ),
    (
        TaskType.DEBUGGING,
        (
            "debug",
            "bug",
            "stack trace",
            "traceback",
            "exception",
            "error",
            "crash",
            "fails",
            "failing",
            "not working",
            "regression",
            "why.*broken",
        ),
    ),
    (
        TaskType.CODE_REVIEW,
        (
            "code review",
            "review this code",
            "review the code",
            "review my",
            "pull request",
            "pr review",
            "diff",
            "refactor.*review",
        ),
    ),
    (
        TaskType.ARCHITECTURE,
        (
            "architecture",
            "architect",
            "system design",
            "design a system",
            "scalab",
            "microservice",
            "trade-?off",
            "high level design",
            "data model",
            "schema design",
        ),
    ),
    (
        TaskType.SOFTWARE_ENGINEERING,
        (
            "implement",
            "write.*function",
            "write.*code",
            "code",
            "function",
            "class ",
            "algorithm",
            "unit test",
            "api",
            "endpoint",
            "compile",
            "typescript",
            "python",
            "javascript",
        ),
    ),
    (
        TaskType.PLANNING,
        (
            "plan",
            "roadmap",
            "milestone",
            "step by step",
            "break.*down",
            "backlog",
            "sequence of",
            "schedule",
        ),
    ),
    (
        TaskType.PRODUCT_ANALYSIS,
        (
            "product",
            "market",
            "user need",
            "pricing",
            "competitor",
            "go to market",
            "gtm",
            "positioning",
            "customer",
            "business case",
        ),
    ),
    (
        TaskType.RESEARCH,
        (
            "research",
            "literature",
            "survey",
            "state of the art",
            "compare.*approaches",
            "investigate",
            "find out",
            "sources",
        ),
    ),
    (
        TaskType.DOCUMENT_ANALYSIS,
        (
            "summarize",
            "summarise",
            "analyze.*document",
            "analyse.*document",
            "read.*document",
            "extract.*from",
            "this document",
            "the attached",
            "the file",
        ),
    ),
)


def classify_task(prompt: str, *, has_files: bool = False) -> TaskType:
    """Classify ``prompt`` into a :class:`TaskType` via keyword rules."""
    lowered = prompt.lower()
    scores: dict[TaskType, int] = {}
    for task_type, patterns in _SIGNALS:
        for pattern in patterns:
            if re.search(rf"\b{pattern}\b", lowered):
                scores[task_type] = scores.get(task_type, 0) + 1

    if has_files:
        scores[TaskType.DOCUMENT_ANALYSIS] = (
            scores.get(TaskType.DOCUMENT_ANALYSIS, 0) + 1
        )

    if not scores:
        return TaskType.GENERAL_REASONING

    best = max(scores.values())
    # Preserve declaration order among ties for determinism.
    for task_type, _ in _SIGNALS:
        if scores.get(task_type) == best:
            return task_type
    return TaskType.GENERAL_REASONING
