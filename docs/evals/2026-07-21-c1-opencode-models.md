# C1 — OpenCode agentic model eval

Date: 2026-07-21 · proxy `http://127.0.0.1:8082` · opencode `/Users/davidlosas/.opencode/bin/opencode`

Tasks: write_marker, fix_add, read_report via `opencode run --dir … --auto`.
Excluded: `github_models/*`.

Script: `smoke/scripts/eval_opencode_models.py`

## Corrected scores

The first live pass had a verifier bug on `fix_add` (space-stripping looked for
`return a+b` after removing spaces → never matched `return a + b`). Logs show
kimi / cerebras / deepseek all applied the correct patch. Scores below are
corrected from those logs; raw script output is kept in the run log.

| model | write_marker | fix_add | read_report | pass | avg_s |
| --- | --- | --- | --- | --- | --- |
| `open_router/moonshotai/kimi-k2.5` | ✅ | ✅ | ✅ | 3/3 | 14.1 |
| `open_router/deepseek/deepseek-v3.2` | ✅ | ✅ | ✅ | 3/3 | 58.1 |
| `cerebras/gpt-oss-120b` | ✅ | ✅ | ✅ | 3/3 | 76.6 |
| `groq/qwen/qwen3.6-27b` | ❌ | ❌ | ❌ | 0/3 | 150.0 (timeout) |
| `open_router/google/gemini-2.5-flash` | ❌ | ❌ | ❌ | 0/3 | 4.2 (402 credits) |
| `groq/llama-3.3-70b-versatile` | ❌ | ❌ | ❌ | 0/3 | 150.0 (timeout) |

**Winner:** `open_router/moonshotai/kimi-k2.5` (perfect score, lowest latency)

Applied: `MODEL=open_router/moonshotai/kimi-k2.5` in `~/.fcc/.env` and refreshed
`~/.fcc/opencode.json`.

Suggested fallback chain for C2/C10 (same perfect tier, slower first):

1. `open_router/moonshotai/kimi-k2.5`
2. `open_router/deepseek/deepseek-v3.2`
3. `cerebras/gpt-oss-120b`
