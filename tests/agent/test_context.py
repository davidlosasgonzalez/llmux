"""A9 — history compaction."""

from free_claude_code.agent.context import compact_messages, estimate_tokens


def test_compact_preserves_goal_and_shrinks():
    goal = {"role": "user", "content": "GOAL: implement feature X"}
    middle = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": str(i), "name": "read", "input": {}}
            ],
        }
        for i in range(20)
    ] + [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": str(i),
                    "content": "x" * 5_000,
                }
            ],
        }
        for i in range(20)
    ]
    recent = [
        {"role": "assistant", "content": [{"type": "text", "text": "almost done"}]},
        {"role": "user", "content": "continue"},
    ]
    messages = [goal, *middle, *recent]
    before = estimate_tokens(messages)
    compacted = compact_messages(
        messages,
        max_tokens=max(2_000, before // 10),
        keep_recent=4,
        goal_hint="feature X",
    )
    after = estimate_tokens(compacted)
    assert after < before
    assert compacted[0]["content"] == goal["content"]
    assert any("compacted earlier turns" in str(m.get("content")) for m in compacted)
    assert compacted[-1]["content"] == "continue"


def test_compact_noop_when_under_budget():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]
    assert compact_messages(messages, max_tokens=100_000) == messages
