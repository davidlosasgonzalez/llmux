# Providers, models, limits & routing (data reference)

Ground truth for **which free models each key gives us**, **their limits**, and
**how that dictates which model plays which role**. Feeds the capability priors
in `verdict/capability.py` and the routing rules in
[`verdict-refinement-design.md`](verdict-refinement-design.md).

_Model catalogues are live-discovered (`fcc-verdict models --free-only`); limits
are researched from provider docs and **change often — re-verify periodically.**
Last researched: 2026-07-15._

---

## 1. What each key gives us (live, validated)

All six authenticate OK and are free without a card. Free-eligible **chat** models
discovered (non-chat like embeddings/whisper/guard are filtered out):

| Provider | Env var | Free chat models | Standout models |
| --- | --- | --- | --- |
| Groq | `GROQ_API_KEY` | ~10 | gpt-oss-120b, llama-3.3-70b, qwen3-32b |
| Cerebras | `CEREBRAS_API_KEY` | ~3 | gpt-oss-120b, zai-glm-4.7 |
| Gemini | `GEMINI_API_KEY` | ~49 | gemini-2.5/3-pro, gemini-flash (1M ctx) |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | ~94 | nemotron-3-super-120b, nemotron-ultra-550b, qwen3.5-397b, deepseek-v4, glm-5.2 |
| OpenRouter `:free` | `OPENROUTER_API_KEY` | ~16 | nemotron-super/ultra `:free`, qwen3-coder `:free`, llama-3.3-70b `:free` |
| GitHub Models | `GITHUB_MODELS_TOKEN` | ~20 | deepseek-r1, deepseek-v3, llama-4-maverick, mistral-medium |

**192 free chat models total.** The pool covers every role we need.

---

## 2. Free-tier limits (researched, with sources)

| Provider | RPM | RPD | Tokens | Card? | Notes |
| --- | --- | --- | --- | --- | --- |
| **Groq** | 30 (default) | 1,000 (default) | 6K–12K TPM, ~100K–500K TPD by model | No | Varies **a lot** by model: `llama-3.1-8b` up to 14,400 RPD; Llama-4 down to 15 RPM/500 RPD. Org-level. [1] |
| **Cerebras** | ~30 (some models ~5) | token-based, no RPD | **1M tokens/day**, ~60K TPM, ~1 req/s | No | ⚠️ Free context historically capped ~**8K** — avoid for long/large-file tasks. Verify in dashboard. [2] |
| **Gemini** | Flash-Lite 15 · Flash 10 · **Pro 5** | Flash-Lite 1,000 · Flash 250 · **Pro 100** | 250K TPM shared | No | Pro is free but very tight. 1M context. Cut 50-80% on 2025-12-07. Per-project, resets midnight PT. [3] |
| **NVIDIA NIM** | 40/model (→200 on request) | credit-based (1,000→5,000) | per-request credit burn | No | Email-only key. Hosted models free until credits run out; prototyping-grade. [4] |
| **OpenRouter `:free`** | **20 (fixed)** | **50** (→1,000 if ever bought ≥$10) | $0/token | No | Tightest daily. Failures count against quota. 429 in peak hours. [5] |
| **GitHub Models** | Low 15 · High 10 | Low 150 · **High 50** | **8K in / 4K out per request** | No (PAT only) | deepseek-r1 is "high tier" → 50 RPD. Hard 8K input cap. [6] |

---

## 3. Model capability & context (researched)

| Model | Context | Reasoning | Note |
| --- | --- | --- | --- |
| deepseek-r1 | 128K | **Yes** (explicit CoT) | top free reasoner |
| deepseek-v3 | 128K | No | strong generalist/coder |
| nemotron-3-super/ultra | 256K–1M | Yes (reasoning variants) | large MoE; gen-prev ref = 128K |
| gpt-oss-120b | 131K | Yes (adjustable effort) | strong, high-throughput on Groq/Cerebras |
| qwen3-coder | 256K | No | best free coder |
| qwen3 | 128K | Yes (thinking hybrid) | — |
| llama-3.3-70b | 128K | No | reliable generalist |
| gemini flash / pro (2.5) | **1M** | Yes | best long-context |
| glm (4.6+) | 200K | Yes (hybrid) | — |
| mistral-medium | 128K | No | — |

`qwen3.5`, `deepseek-v4`, and `nemotron-3` families were verified against the
live OpenRouter catalog on 2026-07-23 (`nemotron-3-ultra-550b-a55b:free`: 1M
ctx, NVIDIA first-party hosting, tools + reasoning). Remaining unverified names
are handled by **size/family heuristics** in `capability.py`, never by
hardcoded claims. Sources: [1]–[11] in the research log.

---

## 4. Routing implications — *this drives selection*

The limits split providers into two budget classes. **This is the key operational
insight:**

### High-throughput (many parallel calls) → PROPOSE + REVIEW
`groq`, `cerebras`, `nvidia_nim`, `gemini` (flash/flash-lite).
Generous RPM/RPD, cheap to fan out 3–4 proponents + 2–3 reviewers.

### Scarce / high-quality (few calls, high value) → REFINE + CRITIQUE
`open_router:free` (20 RPM / **50 RPD**), `github_models` high-tier
(deepseek-r1, **50 RPD**).
Reserve these for the 1–2 synthesis/critique calls per run where their stronger
models (nemotron-ultra, deepseek-r1) pay off — do **not** spend them on the
parallel propose phase or they exhaust in a few runs.

### Hard constraints to respect
- **Cerebras ≤ ~8K context** → exclude from long-context / large-file roles.
- **GitHub Models ≤ 8K in / 4K out** → keep refiner prompts compact; cap output ≤4K.
- **OpenRouter `:free` 50 RPD** (keys with $10+ lifetime credit purchases: 1,000 RPD) → budget it on uncredited keys; on credited keys it can join the fan-out.
- **Gemini Pro 100 RPD / 5 RPM** → use for high-value refine, not fan-out.

### Recommended default role→provider map
| Role | Providers (priority) | Models |
| --- | --- | --- |
| Propose (×3-4) | groq, cerebras, nvidia_nim, gemini-flash | gpt-oss-120b, llama-3.3-70b, qwen3, nemotron-super |
| Review (×2-3) | groq, nvidia_nim, gemini-flash-lite | qwen3, gpt-oss-120b, nemotron |
| **Refine** | github_models, open_router:free, nvidia_nim | **deepseek-r1**, nemotron-ultra-550b, qwen3-coder (code) |
| **Critique** | open_router:free, groq, nvidia_nim | nemotron-super, gpt-oss-120b (≠ refiner family) |

---

## 5. Sources

Groq [1] console.groq.com/docs/rate-limits · Cerebras [2]
inference-docs.cerebras.ai/support/rate-limits · Gemini [3]
ai.google.dev/gemini-api/docs/rate-limits · NVIDIA NIM [4] developer forums ·
OpenRouter [5] openrouter.ai/docs/api_reference/limits · GitHub Models [6]
github.com/orgs/community/discussions/137298 · capability [7]–[11] DeepSeek-R1
arXiv 2501.12948, build.nvidia.com model cards, llm-stats.com, artificialanalysis.ai.
