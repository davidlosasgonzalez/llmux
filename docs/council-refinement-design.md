# FCC Council — Refinement-Flow Design (our fork)

This document specifies **how we use FCC Council** and the **changes we want to
apply** to make it a first-class *refinement flow*. It is the design spec; the
user-facing manual is [`council.md`](council.md).

---

## 1. How our use differs from upstream

**Upstream `free-claude-code`** is a *proxy*: it lets **one** coding agent (Claude
Code / Codex / Pi) run on free or local models, with routing, streaming and tool
calling. One model answers each request.

**Our use is a refinement flow.** One query is:

1. solved **in parallel by several agents** (different free models),
2. then **the most adequate agent refines/merges everything**,
3. then **adversarially checked**, and
4. **refined again** until it is good enough.

The goal: **save Claude Opus/Fable calls and context**, and get an answer
*better than any single free model* by combining de-correlated attempts. Same
infrastructure is reused; this is an **additive layer** (`council/` package),
never a rewrite of the proxy.

Mapping your words to the pipeline:

| Your description | Pipeline phase |
| --- | --- |
| "distintos agentes lo resuelven en paralelo" | **Propose** (proponents) |
| "otro agente, el más adecuado, refina todo" | **Synthesize / Refine** (refiner) |
| "otro flujo de refinación" | **Critique → Refine loop** (+ optional 2nd pass) |
| "con fallbacks por si uno falla" | **Fallback matrix** (§5) |

---

## 2. The refinement pipeline

```
Query
  │
  ├─▶ Classify           (deterministic: task category)
  ├─▶ Select             (capability-aware, diverse, free-only)
  │
  ├─▶ Propose  ──▶ A  B  C  D        (N proponents, in parallel, independent)
  │
  ├─▶ Cross-review (anonymous)       (M reviewers rank A/B/C/D; no identities)
  │
  ├─▶ ┌───────────── refine loop (×R rounds) ─────────────┐
  │   │  Synthesize  (best-fit refiner merges + reviews)  │
  │   │  Critique    (different-family adversary)         │
  │   └──────────────────────────────────────────────────┘
  │
  └─▶ Compact result  (+ full report saved to disk)
```

Stop the loop when the critique **passes and clears the quality threshold**, when
**two rounds show no material improvement**, or at the **round cap**. Never loop
unbounded.

---

## 3. Agent assignment per phase — *our decisions*

We do **not** leave role assignment to chance. Each phase gets the model families
best suited to it, chosen from what we validated as free (Groq, Cerebras, NVIDIA
NIM, Gemini, OpenRouter `:free`, GitHub Models).

### 3.1 Roles and preferred families

| Phase | Role goal | Preferred families | Concrete free models (validated) |
| --- | --- | --- | --- |
| **Propose** | Breadth, diverse strong generalists | gpt-oss, llama-70b, nemotron-super, deepseek-v3, qwen3-large | `gpt-oss-120b` (groq/cerebras), `llama-3.3-70b` (groq), `nemotron-3-super-120b:free` (or), `deepseek-v3` (github), `qwen3.5-122b` (nim) |
| **Cross-review** | Sharp reasoning, error-finding | deepseek-r1, nemotron-reasoning, qwen3, glm | `deepseek-r1` (github), `nemotron-3-nano-...-reasoning:free` (or), `qwen3-32b` (groq), `glm-5.2` (nim) |
| **Synthesize / Refine** | The "most adequate": strong reasoning + long context, category-fit | deepseek-r1, nemotron-super/ultra, gpt-oss-120b, qwen3-coder | see §3.2 per category |
| **Critique (adversary)** | Break the synthesis; must differ in family+provider from the refiner | any strong family ≠ refiner's | `gpt-oss-120b`, `nemotron-super-120b`, `deepseek-r1`, `glm-5.2` |

### 3.2 Refiner choice per task category

The refiner ("most adequate agent") is picked **by category**, preferring a strong
model of a **different family** from the top proponents (so the merge is not an
echo):

| Category | Refiner capability | First-choice families |
| --- | --- | --- |
| `software_engineering` | coding + reasoning | qwen3-coder, deepseek-v3, gpt-oss-120b |
| `architecture` | reasoning + long context | deepseek-r1, nemotron-super/ultra |
| `debugging` | reasoning | deepseek-r1, nemotron-reasoning |
| `code_review` / `adversarial_review` | reasoning, skeptical | deepseek-r1, gpt-oss-120b |
| `research` / `document_analysis` | long context | gemini-flash, nemotron-super, qwen3-large |
| `planning` / `product_analysis` | reasoning + structure | deepseek-r1, nemotron-super |
| `general_reasoning` | reasoning | deepseek-r1, gpt-oss-120b |

---

## 4. How much refinement — *our recommendation*

You asked how much refinement is advisable. Recommendation, with rationale:

| Depth | Proponents · Reviewers · Rounds | Use for |
| --- | --- | --- |
| `quick` | 2 · 1 · 1 | cheap sanity check, low stakes |
| `standard` | 3 · 2 · 2 | **default for most questions** |
| `deep` | 4 · 3 · 3 | architecture, high-stakes, ambiguous problems |

**Recommended sweet spot: 2–3 refine rounds.** Hard cap 5, never unbounded.

Why not more:

- **Self-refinement plateaus after ~2–3 iterations.** Extra rounds rarely change
  the decision and mostly reword.
- **Over-refinement degrades**: answers get blander and lose the concrete
  specifics that made a proposal useful.
- **Cost/quota**: each round is a synthesis + critique call; free tiers rate-limit.

**Early-stop is a feature, not a shortcut**: stop as soon as the adversary passes
and the score clears the threshold, or two rounds add nothing material.

**Optional second-pass (meta-refinement)** — for exceptional stakes only: feed the
first council's answer back as the query of a *fresh* council run. Opt-in, roughly
doubles cost. Off by default.

---

## 5. Fallback matrix — *always on*

Every phase degrades gracefully; one failing agent never sinks the run.

| Failure | Action |
| --- | --- |
| Proponent: 429 / 404 / timeout / bad JSON | Drop it; keep the other proponents; circuit-break that provider; deliberate with what remains (≥1 proposal). |
| Reviewer fails | Drop that review; synthesize from proposals + surviving reviews (0 reviews is allowed). |
| **Refiner fails** | Re-pick the next best-fit refiner from a **different provider**; if none, **promote the top-ranked proposal's model** as refiner. |
| **Critic fails** | Force verdict `revise` (never auto-`pass`); if repeated, stop with an honest low score. Safety: a missing adversary must never let a weak answer through. |
| Provider circuit-broken | Excluded from all later phases until its cool-off (respects `Retry-After`); recovers automatically. |
| Fewer than `minimum_models` / `minimum_distinct_providers` free models | **Fail explicitly** with per-provider reasons (exhausted / unauthenticated / paid-excluded). Never silently downgrade. |

Selection order for any re-pick: **capability fit → health (not circuit-broken) →
quota → family/provider diversity → empirical score**.

---

## 6. Changes to apply

Status of each design point against the current implementation:

- [x] Parallel propose → anonymous cross-review → best-fit synthesize → adversarial
      critique → bounded refine loop. *(implemented)*
- [x] Per-category refiner selection preferring a different family. *(implemented in `orchestration._pick_one` + `config.roles`)*
- [x] Full fallback matrix, circuit breaking, explicit-fail on too few models. *(implemented)*
- [x] Free-only cost gate; paid/unknown never selected. *(implemented)*
- [x] **Capability priors (cold-start fix).** `council/capability.py` derives a
      static prior from parameter size + reasoning/coder flags + family
      reputation, plus a per-category fit. `scoring.py` blends it with empirical
      stats via Bayesian shrinkage (prior governs cold, empirical takes over after
      ~8 observations). Fixes the observed bug where a `deep` run picked 8B/30B
      models while 120B+ models sat unused. *(implemented + tested)*
- [x] **Budget-class routing.** `council/provider_limits.py` classifies each
      provider *high-throughput* (Groq, Cerebras, NVIDIA NIM, Gemini) or *scarce*
      (OpenRouter `:free` 50 RPD, GitHub high-tier 50 RPD). Fan-out phases
      (propose/review) apply a penalty to scarce providers via
      `budget_multiplier`, while refine/critique use them at full weight — so the
      strong-but-scarce models (deepseek-r1, nemotron-ultra) are spent on the 1–2
      high-value calls, not the parallel fan-out. *(implemented + tested)*
- [x] **Usage tracking (`/usage`-style).** Every call records requests + tokens
      per model/provider/day in SQLite (`usage_log`). `fcc-council usage` and the
      `get_usage` MCP tool show requests vs the approximate free RPD limit and
      tokens spent. Approximate by design (provider-reported tokens where
      available). *(implemented + tested)*
- [ ] **Role-capability config per category** (`roles:` in `council.yaml`) wired
      fully into selection so §3.2 preferences hold without relying on stats.
- [ ] **Optional second-pass meta-refinement** flag (`--second-pass`).
- [ ] Seed stats with `fcc-council benchmark` on first setup so selection is warm.

## 6b. MCP + Claude Code integration architecture

The intended usage is **from Claude Code, via MCP**, so a deliberation runs on
free models without spending Opus/Fable context.

```
Claude Code (Opus/Fable)
  │  calls MCP tool  evaluate(prompt, depth, files, privacy)
  ▼
fcc-council serve-mcp   (stdio, local-only)         ← already implemented
  │  CouncilService.create()  (the shared core)
  ▼
Discovery → free-only gate → capability+budget selection
  → propose(∥) → review(∥) → refine loop → compact result
  │
  ▼  returns a COMPACT JSON payload (answer, action, disagreements,
     confidence, models_used, rounds) + a report_path on disk
```

Setup (one-time):

```bash
uv pip install 'free-claude-code[council]'      # brings the mcp dependency
claude mcp add fcc-council -- fcc-council serve-mcp
fcc-council install-claude-skill                # optional: the deep-council skill
```

Design choices that make this safe and cheap for Claude Code:
- **Compact return** keeps Claude's context small; the full deliberation is on disk.
- **stdio, local-only** — never a network socket.
- **Same core** as the CLI (`CouncilService`), so behaviour is identical whether
  invoked by MCP, CLI or tests.
- The `deep-council` skill tells Claude *when* to reach for it (second opinion,
  compare proposals, evaluate architecture/plan, save context) and to send only
  the needed context, never secrets.

The first pending item (budget-class routing) is the most impactful next step: it
turns "we have the models" into "we spend the right model on the right role
without exhausting the scarce free quotas."

---

## 7. Guardrails (unchanged)

- **Free-only by default** (`ALLOW_PAID_MODELS=false`); no charge is ever possible.
- **Privacy**: `redacted` by default; `local_only` fails rather than sending to
  the cloud; file inputs restricted to allowed roots.
- **Anonymity**: reviewers and the refiner never see model/provider identities.
- **Additive**: nothing in the upstream proxy, routing, streaming or tool calling
  is changed.
