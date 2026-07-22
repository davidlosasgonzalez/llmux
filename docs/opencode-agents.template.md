# OpenCode AGENTS.md — Free Claude Code (auto effort mode)

Copy this file to a project root as `AGENTS.md`, or to
`~/.config/opencode/AGENTS.md` for a global default.

## Effort ladder — pick the cheapest step that fits

| Step | Tool | Latency | Use when |
| --- | --- | --- | --- |
| 1 | Answer directly | seconds | Default. Reading code, edits, explanations, known APIs |
| 2 | `@second-opinion` subagent | +1 call | Medium confidence on a non-trivial diff or plan |
| 3 | `/verdict` (MCP `evaluate`) | minutes | See strict triggers below |

**Default is step 1.** Escalate only on an explicit trigger — never "just in
case". One escalation per task unless the user asks for more.

## Step 3 triggers (Verdict MCP `free-llm-verdict`, tool `evaluate`)

Call `evaluate` (depth `quick`) ONLY when at least one holds:

1. **Facts you cannot verify locally** (versions, limits, pricing, external
   APIs) and being wrong has consequences → use `research on`. This is where
   Verdict measurably beats a single model.
2. **A bug resisted two real fix attempts** in this session.
3. **Non-trivial design decision** (new architecture, migration, public API,
   auth/data-loss risk) where the user will build on the answer.

## Never escalate for

- Questions answerable by reading the repo (grep/read first — always).
- Formatting, naming, small refactors, test fixes, explanations.
- Anything the user needs fast; if unsure, answer directly and offer
  `/verdict` as a follow-up instead of blocking the turn.

## Models

- Default chat model comes from FCC `MODEL`; provider failover is handled by
  the FCC proxy (`MODEL_FALLBACKS`) before the first SSE byte. Do not hop
  models manually mid-turn.
- `@second-opinion` uses a different FCC model family and is read-only.

## Workspace discipline

- Prefer small diffs; run project checks after edits when available.
- Never force-push `main` / `master`.
- Keep secrets out of commits (`.env`, tokens, keys).
