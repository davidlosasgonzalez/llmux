# C8 — Council A/B vs best free model (kimi-k2.5)

Date: 2026-07-21

## Method (abbreviated)

Compare `fcc-council evaluate --depth quick --research on` against a single
`fcc-agent --yes` / OpenCode run with `MODEL=open_router/moonshotai/kimi-k2.5`
on the same prompt set. Full live matrix is expensive; this decision uses:

1. C1 agentic results (kimi 3/3 fastest among free candidates that completed).
2. Council design intent (parallel proposals + anonymous critique + synthesis
   fallback) vs OpenCode `@second-opinion` (one alternate model, cheap).
3. Operational cost: Council burns many free-provider calls per question;
   C1 already shows strong single-model agentic coding on kimi.

## Decision

**Council stays in niche, not default path.**

- Keep MCP + `/council` for non-trivial planning / sticky bugs / design review
  (C4 template).
- Do **not** block v1 on expanding Council features.
- Prefer `@second-opinion` for routine cross-checks (C9).
- Revisit only if a future A/B (5–8 real design questions, blind ranking)
  shows Council win rate ≥ +20% over kimi alone on the same rubric.

## Follow-up command (when you have quota)

```bash
# Example single pair — expand to 5–8 prompts and score manually
fcc-council evaluate --depth quick --research on "Should Advisor use X over Y?"
# vs
fcc-opencode run --dir ~/Documents/advisor --auto "Should Advisor use X over Y?"
```
