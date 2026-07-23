# LLMux Verdict

LLMux Verdict is an **optional, additive** deliberation layer for LLMux.
It consults several **free** cloud models, has them cross-review each other,
synthesises a merged answer and adversarially critiques it, repeating until the
result is refined enough.

Its purpose is to **save Claude Opus/Fable calls and context** on tasks where a
longer deliberation among free models produces a good-enough result. Quality is
valued over speed — an evaluation may take several minutes.

Verdict does **not** replace `llmux-server` or `llmux-claude`,
the proxy, the model routing, streaming or tool calling. It is a separate layer
that reuses the existing provider stack (`create_provider`, `stream_response`,
the Anthropic SSE aggregator, `ProviderRegistry`/catalogue).

## What it does

1. **Classifies** the task (deterministic keyword rules).
2. **Selects** 3–5 diverse free models (provider/family diversity, empirical
   scores, health, quota).
3. **Researches** (optional, Phase 2.5) — when the prompt hinges on current
   facts (versions, limits, prices, docs), the local process searches and
   fetches sources itself (no model can browse) and injects them as verified
   context. See [Web research](#web-research).
4. **Proposes** — each model answers independently in structured JSON.
5. **Cross-reviews** — reviewers rank the proposals **anonymously** (labels
   A/B/C/D; provider and model identity are never revealed).
6. **Synthesises** — the best free model for the category merges the strongest
   elements; a factual disagreement is settled on verified evidence, never by
   majority (unresolved conflicts are escalated to one directed research round).
7. **Critiques** — a different-family model tries to break the synthesis.
8. **Refines** — repeats synthesis+critique until the critique passes, the
   quality threshold is met, two rounds show no material improvement, or the
   round cap is hit (default 3, max 5).

## Model selection per phase

Every candidate model gets a 0–1 score (`verdict/scoring.py`) blending:

- A static **capability prior** (`verdict/capability.py`) from parameter size,
  family reputation, and coder/reasoner hints parsed from the model id —
  governs selection cold, before any history exists.
- A **category fit** multiplier that boosts coder-tuned models for
  `software_engineering`/`code_review`/`debugging` and reasoning-tuned models
  for `architecture`/`planning`/`research`/`general_reasoning`/`adversarial_review`.
- **Empirical stats** (reliability, cross-review score, JSON compliance, times
  picked as the best proposal) once a model has run enough requests —
  Bayesian shrinkage blends prior and empirical history so a handful of runs
  already starts to matter.
- Penalties for high latency, a history of rejected syntheses, low quota, and
  degraded health.

Selection itself is greedy and diversity-aware
(`verdict/selector.py`): it repeatedly picks the highest-scoring remaining
candidate after penalizing overlap with providers, families, and base models
already on the panel, so proposals stay decorrelated instead of echoing each
other. The `role` passed to selection (`proponent`/`refiner`/`critic`) applies
a budget bias: fan-out phases (propose, review) penalize scarce-quota
providers more heavily so their budget survives for the one or two high-value
refine/critique calls, which use scarce providers at full weight.

## Fallback behavior

One failing agent never sinks a run:

| Failure | Behavior |
| --- | --- |
| A proponent errors (429 / 404 / timeout / bad JSON) | Dropped; the run continues with the remaining proposals (at least one required). |
| A reviewer fails | That review is dropped; synthesis proceeds from the proposals plus whatever reviews survived (zero reviews is allowed). |
| The refiner fails | The next best-fit refiner from a different provider is picked; if none is available, the top-ranked proposal's model is promoted to refiner. |
| The critic fails | The verdict is forced to `revise` rather than an unchecked `pass`; on repeated failure the run stops with an honest low score — a missing adversary never lets a weak answer through silently. |
| A provider is circuit-broken | Excluded from every later phase until its cool-off elapses (respecting `Retry-After`); it recovers automatically. |
| Fewer than `minimum_models` / `minimum_distinct_providers` free models remain | The run fails explicitly, listing per-provider reasons (exhausted / unauthenticated / paid-excluded) — it never silently downgrades. |

## What it does NOT do

- It never calls a paid model in the default mode (`ALLOW_PAID_MODELS=false`).
- It never enables billing, never falls back to paid models, and never uses a
  model whose cost cannot be determined.
- It does not rotate keys or use multiple accounts to dodge quotas.
- It does not run another full Claude Code session inside Claude Code.
- It does not read files outside the allowed roots, and does not send secrets.

## The free-only guarantee

With `ALLOW_PAID_MODELS=false` (the default), a model is eligible **only** when:

- its provider is **enabled** in `verdict.yaml`, **and**
- its provider never requires a **card, deposit or billing** to obtain a genuine
  daily free allowance (`requires_card=false`), **and**
- the concrete model's cost status is `verified_free` or `free_tier`.

`unknown` and `paid` cost statuses are excluded. An API key only authenticates
you — it never authorises a provider. Enabling a card-required provider in
`verdict.yaml` does **not** let it be used in free-only mode; the cost gate still
excludes it. If fewer than the required number of free models/providers are
available, the verdict **fails explicitly** and explains which providers were
exhausted, unauthenticated or excluded.

Note on daily-limited providers: a provider is **not** excluded merely for
needing an API key or for having a daily free cap. Generous daily free tiers
without a card (e.g. Groq, Cerebras, Google AI Studio, NVIDIA NIM) are exactly
what Verdict is built on.

## Priority providers

| Tier | Providers | Default |
| --- | --- | --- |
| A (primary) | `groq`, `nvidia_nim`, `cerebras`, `gemini` | enabled |
| B (secondary) | `mistral`, `open_router`, `github_models`, `cloudflare`, `cohere` | opt-in |
| Disabled (unvalidated but plausibly free) | `huggingface`, `sambanova`, `ollama_cloud` | off |
| Paid (needs money/card) | `deepseek`, `fireworks`, `kimi`, `minimax`, `wafer`, `zai`, `vercel`, `mistral_codestral` | never in free mode |
| Local | `lmstudio`, `llamacpp`, `ollama` | when running |

A DeepSeek model **is** allowed when served by a validated-free provider such as
NVIDIA NIM or OpenRouter `:free` — the exclusion is of the *direct paid* DeepSeek
endpoint, not the model family.

## Getting API keys (all free, no card required)

| Provider | Key env var | Where |
| --- | --- | --- |
| Groq | `GROQ_API_KEY` | <https://console.groq.com/keys> |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | <https://build.nvidia.com/settings/api-keys> |
| Cerebras | `CEREBRAS_API_KEY` | <https://cloud.cerebras.ai/> |
| Google AI Studio (Gemini) | `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> |
| OpenRouter (`:free` models) | `OPENROUTER_API_KEY` | <https://openrouter.ai/keys> |
| Mistral La Plateforme | `MISTRAL_API_KEY` | <https://console.mistral.ai/> |
| GitHub Models | `GITHUB_MODELS_TOKEN` | <https://github.com/marketplace?type=models> |
| Cloudflare Workers AI | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | <https://developers.cloudflare.com/workers-ai/> |
| Cohere | `COHERE_API_KEY` | <https://dashboard.cohere.com/api-keys> |
| Brave Search (web research) | `BRAVE_SEARCH_API_KEY` | <https://brave.com/search/api/> |

None of the above require a payment card for their free tier at the time of
writing. Always confirm with `llmux-verdict providers validate`.

Set keys the same way as the rest of LLMux (Admin UI, `~/.llmux/.env`, or your
shell). Verdict reads them through the shared `Settings`.

## Configuration

Verdict reads `~/.llmux/verdict.yaml` (optional). A versioned example lives at
[`../assets/verdict.example.yaml`](../assets/verdict.example.yaml). Copy it:

```bash
mkdir -p ~/.llmux && cp assets/verdict.example.yaml ~/.llmux/verdict.yaml
```

### Refinement depth

| Depth | Proponents · Reviewers · Rounds | Use for |
| --- | --- | --- |
| `quick` | 2 · 1 · 1 | Cheap sanity check, low stakes |
| `standard` | 3 · 2 · 2 | Default for most questions |
| `deep` | 4 · 3 · 3 | Architecture, high-stakes, ambiguous problems |

The sweet spot is 2–3 refine rounds; the hard cap is 5, never unbounded.
Self-refinement plateaus after ~2–3 iterations — extra rounds rarely change the
decision and mostly reword, while over-refinement tends to make answers
blander and drop the concrete specifics that made a proposal useful. A round
stops early as soon as the critique passes and clears the quality threshold,
or when two consecutive rounds show no material improvement.

Key settings: `depth` (`quick`/`standard`/`deep`, default `deep`), `max_rounds`
(≤5), `quality_threshold` (0.85), `convergence_threshold` (0.98 — stop once a
synthesis reproduces the previous answer this closely), `privacy` (`redacted`
default), `enabled_providers`, `minimum_models`/`minimum_distinct_providers`,
`call_timeout_s` (per-call ceiling, default 90 s), and the research knobs
`research_enabled`, `research_max_sources`, `research_tokens_per_source`,
`research_tokens_total`, `research_fetch_timeout_s`.

The `ALLOW_PAID_MODELS` environment variable always overrides the file and
defaults to `false`.

## Privacy

Assume free providers may log or reuse inputs. Modes:

- `public` — sent verbatim (you take responsibility).
- `redacted` — **default**; API keys, bearer tokens, passwords, cookies, private
  keys, credential URLs and `*_SECRET/_TOKEN/_PASSWORD/_API_KEY` assignments are
  masked before anything leaves the machine.
- `local_only` — only local providers (LM Studio / llama.cpp / Ollama) are used;
  if none are available the verdict **fails** rather than sending to the cloud.
  Web research is disabled in this mode (it would reach an external engine).

File inputs must live under allowed roots (default: the current directory) and
respect a size cap. Prompts and responses are never logged verbatim; logs are
redacted and truncated.

## Install the MCP server and skill

```bash
# Register the MCP server with Claude Code (mcp ships with the base package)
claude mcp add llmux-verdict -- llmux-verdict serve-mcp

# 3. Install the deep-verdict skill (explicit; backs up any existing one)
llmux-verdict install-claude-skill
```

`install-claude-skill` prints exactly which files it creates
(`~/.claude/skills/deep-verdict/SKILL.md`) and the MCP registration snippet. It
never edits your global Claude Code config automatically.

## CLI

```bash
llmux-verdict providers            # provider free-access status
llmux-verdict providers validate   # same, with live auth check (keys never shown)
llmux-verdict models --free-only   # discovered free-eligible models
llmux-verdict usage                # approximate requests/tokens per provider vs limits
llmux-verdict benchmark            # small local calibration
llmux-verdict evaluate "question"
llmux-verdict evaluate --depth deep --task-type architecture "question"
llmux-verdict evaluate --file path/to/file.py "review this"
llmux-verdict evaluate --research on "current Cloudflare Workers CPU limits"
llmux-verdict evaluate --output json "question"   # full structured payload
llmux-verdict serve-mcp            # start the MCP server (stdio)
llmux-verdict install-claude-skill
```

`--research` accepts `auto` (default — fires only on currency-sensitive
prompts), `on`, or `off`.

Default output is Markdown; `--output json` returns the full structured result.

## MCP tools

- `evaluate(prompt, task_type="auto", depth="deep", files=[], privacy="redacted", max_rounds=3, research="auto")`
- `list_models()`
- `get_config()`
- `check_providers()`
- `get_usage(day=None)` — approximate requests/tokens per provider vs free limits

`evaluate` returns a **compact** payload so Claude's context stays small:

```json
{
  "answer": "...",
  "recommended_action": "...",
  "material_disagreements": [],
  "uncertainties": [],
  "confidence": null,
  "confidence_source": "critic",
  "models_used": [],
  "providers_used": [],
  "rounds": 0,
  "quota_failures": [],
  "research": null,
  "elapsed_s": 0.0,
  "report_path": "~/.llmux/verdict_reports/verdict-....json"
}
```

`confidence` is the critic's score, or `null` when no trustworthy critique was
produced (`confidence_source` is `"critic"` or `"unavailable"` — never a
fabricated `0.0`). `research` is `null` unless a research pass ran, in which case
it carries `{backend, queries, sources_fetched, note}`. Any URL in the answer
that research did **not** fetch is marked `(URL recalled from memory, not
verified in this run)` — a citation is never trusted on the model's word alone.

The full deliberation (all proposals, reviews, rounds) is written to
`report_path`, not returned inline.

## Web research

When `research` is `auto` (default), the local process runs a search-and-fetch
pass **before** the models propose, but only if the prompt hinges on current
facts — versions, limits, prices, dates, or official docs. Set `research="on"`
to force it, or `"off"` to skip it. It is always skipped under `local_only`
privacy.

- **Search** uses Brave Search API when `BRAVE_SEARCH_API_KEY` is set; otherwise
  the keyless DuckDuckGo HTML endpoint.
- **Fetch + extract** downloads the top results and strips them to text, capped
  by `research_tokens_per_source` / `research_tokens_total`.
- **Injection** adds the sources as verified context the panel is told to trust
  over its training memory, citing `[S#]`.
- **Offline** degrades gracefully: the run continues and surfaces a
  `research unavailable` note in `uncertainties`.

The sources fetched are recorded under `research.sources_fetched`.

## Seeing quota and errors

- `llmux-verdict providers validate` shows auth, free status and usability.
- A failed evaluation lists every provider it could not use and why
  (`quota_failures`).
- Circuit breaking benches a provider after auth failures, 429s or exhausted
  quota, and recovers automatically once the window resets (respecting
  `Retry-After`).
- A hard quota exhaustion is remembered per model for the rest of the day, so a
  later run skips that model instead of spending a call to rediscover the 429.
- `llmux-verdict serve-mcp` logs to `~/.llmux/logs/verdict-mcp.log` (JSON lines,
  appended across restarts — unlike other LLMux logs it is not truncated on
  start, since the server is long-lived).

## Empirical model selection

Verdict records per-model, per-category stats in `~/.llmux/verdict.db`: requests,
valid responses, failures, 429s, latency, JSON compliance, cross-review score,
times chosen best, times its synthesis was rejected, last used, estimated quota.
Speed has low weight; quality, reliability, structured-output compliance,
diversity and available quota dominate. Run `llmux-verdict benchmark` to seed the
stats with a small, configurable calibration.

## What NOT to send to free providers

- Secrets, credentials, private keys, `.env` files, `~/.ssh`.
- Whole repositories or files outside the allowed roots.
- Anything you would not want logged or reused by a third party.

Use `redacted` (default) or `local_only` for sensitive work.
