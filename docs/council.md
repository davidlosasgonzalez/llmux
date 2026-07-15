# FCC Council

FCC Council is an **optional, additive** deliberation layer for Free Claude Code.
It consults several **free** cloud models, has them cross-review each other,
synthesises a merged answer and adversarially critiques it, repeating until the
result is refined enough.

Its purpose is to **save Claude Opus/Fable calls and context** on tasks where a
longer deliberation among free models produces a good-enough result. Quality is
valued over speed — an evaluation may take several minutes.

> **Design & our refinement-flow intent:** see
> [`council-refinement-design.md`](council-refinement-design.md) for how we use
> Council (parallel solve → best-fit refiner → adversarial refine loop), the
> per-phase agent choices, the recommended amount of refinement, the fallback
> matrix, and the pending changes to apply.

Council does **not** replace `fcc-server`, `fcc-claude`, `fcc-codex`, `fcc-pi`,
the proxy, the model routing, streaming or tool calling. It is a separate layer
that reuses the existing provider stack (`create_provider`, `stream_response`,
the Anthropic SSE aggregator, `ProviderRegistry`/catalogue).

## What it does

1. **Classifies** the task (deterministic keyword rules).
2. **Selects** 3–5 diverse free models (provider/family diversity, empirical
   scores, health, quota).
3. **Proposes** — each model answers independently in structured JSON.
4. **Cross-reviews** — reviewers rank the proposals **anonymously** (labels
   A/B/C/D; provider and model identity are never revealed).
5. **Synthesises** — the best free model for the category merges the strongest
   elements.
6. **Critiques** — a different-family model tries to break the synthesis.
7. **Refines** — repeats synthesis+critique until the critique passes, the
   quality threshold is met, two rounds show no material improvement, or the
   round cap is hit (default 3, max 5).

## What it does NOT do

- It never calls a paid model in the default mode (`ALLOW_PAID_MODELS=false`).
- It never enables billing, never falls back to paid models, and never uses a
  model whose cost cannot be determined.
- It does not rotate keys or use multiple accounts to dodge quotas.
- It does not run another full Claude Code session inside Claude Code.
- It does not read files outside the allowed roots, and does not send secrets.

## The free-only guarantee

With `ALLOW_PAID_MODELS=false` (the default), a model is eligible **only** when:

- its provider is **enabled** in `council.yaml`, **and**
- its provider never requires a **card, deposit or billing** to obtain a genuine
  daily free allowance (`requires_card=false`), **and**
- the concrete model's cost status is `verified_free` or `free_tier`.

`unknown` and `paid` cost statuses are excluded. An API key only authenticates
you — it never authorises a provider. Enabling a card-required provider in
`council.yaml` does **not** let it be used in free-only mode; the cost gate still
excludes it. If fewer than the required number of free models/providers are
available, the council **fails explicitly** and explains which providers were
exhausted, unauthenticated or excluded.

Note on daily-limited providers: a provider is **not** excluded merely for
needing an API key or for having a daily free cap. Generous daily free tiers
without a card (e.g. Groq, Cerebras, Google AI Studio, NVIDIA NIM) are exactly
what Council is built on.

## Priority providers

| Tier | Providers | Default |
| --- | --- | --- |
| A (primary) | `groq`, `nvidia_nim`, `cerebras`, `gemini` | enabled |
| B (secondary) | `mistral`, `open_router`, `github_models`, `cloudflare`, `cohere` | opt-in |
| Disabled (unvalidated but plausibly free) | `huggingface`, `sambanova`, `ollama_cloud` | off |
| Paid (needs money/card) | `deepseek`, `fireworks`, `kimi`, `minimax`, `wafer`, `zai`, `vercel`, `opencode`, `opencode_go`, `mistral_codestral` | never in free mode |
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

None of the above require a payment card for their free tier at the time of
writing. Always confirm with `fcc-council providers validate`.

Set keys the same way as the rest of FCC (Admin UI, `~/.fcc/.env`, or your
shell). Council reads them through the shared `Settings`.

## Configuration

Council reads `~/.fcc/council.yaml` (optional). A versioned example lives at
[`../assets/council.example.yaml`](../assets/council.example.yaml). Copy it:

```bash
mkdir -p ~/.fcc && cp assets/council.example.yaml ~/.fcc/council.yaml
```

Key settings: `depth` (`quick`/`standard`/`deep`, default `deep`), `max_rounds`
(≤5), `quality_threshold` (0.85), `privacy` (`redacted` default),
`enabled_providers`, `minimum_models`/`minimum_distinct_providers`.

The `ALLOW_PAID_MODELS` environment variable always overrides the file and
defaults to `false`.

## Privacy

Assume free providers may log or reuse inputs. Modes:

- `public` — sent verbatim (you take responsibility).
- `redacted` — **default**; API keys, bearer tokens, passwords, cookies, private
  keys, credential URLs and `*_SECRET/_TOKEN/_PASSWORD/_API_KEY` assignments are
  masked before anything leaves the machine.
- `local_only` — only local providers (LM Studio / llama.cpp / Ollama) are used;
  if none are available the council **fails** rather than sending to the cloud.

File inputs must live under allowed roots (default: the current directory) and
respect a size cap. Prompts and responses are never logged verbatim; logs are
redacted and truncated.

## Install the MCP server and skill

```bash
# 1. Install the optional dependency for the MCP server
uv pip install 'free-claude-code[council]'

# 2. Register the MCP server with Claude Code
claude mcp add fcc-council -- fcc-council serve-mcp

# 3. Install the deep-council skill (explicit; backs up any existing one)
fcc-council install-claude-skill
```

`install-claude-skill` prints exactly which files it creates
(`~/.claude/skills/deep-council/SKILL.md`) and the MCP registration snippet. It
never edits your global Claude Code config automatically.

## CLI

```bash
fcc-council providers            # provider free-access status
fcc-council providers validate   # same, with live auth check (keys never shown)
fcc-council models --free-only   # discovered free-eligible models
fcc-council usage                # approximate requests/tokens per provider vs limits
fcc-council benchmark            # small local calibration
fcc-council evaluate "question"
fcc-council evaluate --depth deep --task-type architecture "question"
fcc-council evaluate --file path/to/file.py "review this"
fcc-council evaluate --output json "question"   # full structured payload
fcc-council serve-mcp            # start the MCP server (stdio)
fcc-council install-claude-skill
```

Default output is Markdown; `--output json` returns the full structured result.

## MCP tools

- `council_evaluate(prompt, task_type="auto", depth="deep", files=[], privacy="redacted", max_rounds=3)`
- `council_models()`
- `council_status()`
- `council_validate_providers()`
- `council_usage(day=None)` — approximate requests/tokens per provider vs free limits

`council_evaluate` returns a **compact** payload so Claude's context stays small:

```json
{
  "answer": "...",
  "recommended_action": "...",
  "material_disagreements": [],
  "uncertainties": [],
  "confidence": 0.0,
  "models_used": [],
  "providers_used": [],
  "rounds": 0,
  "quota_failures": [],
  "report_path": "~/.fcc/council_reports/council-....json"
}
```

The full deliberation (all proposals, reviews, rounds) is written to
`report_path`, not returned inline.

## Seeing quota and errors

- `fcc-council providers validate` shows auth, free status and usability.
- A failed evaluation lists every provider it could not use and why
  (`quota_failures`).
- Circuit breaking benches a provider after auth failures, 429s or exhausted
  quota, and recovers automatically once the window resets (respecting
  `Retry-After`).

## Empirical model selection

Council records per-model, per-category stats in `~/.fcc/council.db`: requests,
valid responses, failures, 429s, latency, JSON compliance, cross-review score,
times chosen best, times its synthesis was rejected, last used, estimated quota.
Speed has low weight; quality, reliability, structured-output compliance,
diversity and available quota dominate. Run `fcc-council benchmark` to seed the
stats with a small, configurable calibration.

## Keeping the fork updated from upstream

```bash
git remote add upstream https://github.com/Alishahryar1/free-claude-code.git
git fetch upstream
git checkout personal/fcc-council
git merge upstream/main         # Council lives in its own package; conflicts are rare
```

Council is confined to `src/free_claude_code/council/`, one line in
`[project.scripts]`, one optional-dependency group and the `ALLOW_PAID_MODELS`
note in `.env.example`, so upstream merges stay clean.

## What NOT to send to free providers

- Secrets, credentials, private keys, `.env` files, `~/.ssh`.
- Whole repositories or files outside the allowed roots.
- Anything you would not want logged or reused by a third party.

Use `redacted` (default) or `local_only` for sensitive work.
