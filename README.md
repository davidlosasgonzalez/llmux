<div align="center">

# 🤖 LLMux

Use Claude Code or its IDE extensions through your own provider-backed proxy.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776ab.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=for-the-badge)](https://github.com/astral-sh/uv)
[![Tested with Pytest](https://img.shields.io/badge/testing-Pytest-00c0ff.svg?style=for-the-badge)](https://github.com/davidlosasgonzalez/llmux/actions/workflows/tests.yml)
[![Type checking: Ty](https://img.shields.io/badge/type%20checking-ty-ffcc00.svg?style=for-the-badge)](https://pypi.org/project/ty/)
[![Code style: Ruff](https://img.shields.io/badge/code%20formatting-ruff-f5a623.svg?style=for-the-badge)](https://github.com/astral-sh/ruff)
[![Logging: Loguru](https://img.shields.io/badge/logging-loguru-4ecdc4.svg?style=for-the-badge)](https://github.com/Delgan/loguru)

Run Claude Code with free, paid, or local models. Choose and validate providers from one local Admin UI. Get free multi-model second opinions inside Claude Code with the bundled Verdict MCP server.

[Quick Start](#quick-start) · [Providers](#choose-a-provider) · [Clients](#connect-your-client) · [Verdict](#verdict-free-multi-model-second-opinions) · [Manage](#manage-your-installation)

</div>

<div align="center">
  <img src="assets/pic.png" alt="LLMux in action" width="700">
  <p><em>Claude Code running through the LLMux proxy.</em></p>
</div>

## What You Get

- Launch Claude Code against the proxy with `llmux-claude` — no subscription required.
- Switch among 23 cloud and local providers from the Admin UI.
- Use Claude Code's native model picker with the models LLMux exposes.
- Route Fable, Opus, Sonnet, Haiku, and fallback traffic to different models.
- Keep streaming, tool use, reasoning, and image input across compatible models.
- Fail over automatically when an upstream answers with an error body (e.g. "Connect timeout, please try again later.") instead of a completion.
- Skip fallback candidates whose context window is too small for the prompt, so oversized requests route to large-window models instead of failing.
- Report upstream prompt-cache hits as Anthropic `cache_read_input_tokens`, with cached tokens excluded from `input_tokens`.
- Fail over after a single same-provider retry when `MODEL_FALLBACKS` is configured (adaptive `PROVIDER_UPSTREAM_MAX_RETRIES` default).
- Connect Claude Code in VS Code or through JetBrains ACP.
- Ask a free multi-model deliberation for second opinions with `llmux-verdict` (CLI and MCP server).
- Inspect per-request routing decisions with `llmux-trace`.
- Serve Claude Code's web search and fetch tools locally via Brave (`ENABLE_WEB_SERVER_TOOLS=true` plus `BRAVE_SEARCH_API_KEY`).
- Protect the local proxy with optional token authentication.
- Get advisory warnings at startup and on Admin UI **Validate** when a model
  combination looks off: a small model in the Opus/Fable slot, a fallback
  chain that never leaves one provider, duplicate fallback entries, or an
  oversized classifier model. Warnings never block your choice.

## Quick Start

<a id="install"></a>

### 1. Install Or Update

macOS/Linux:

```bash
curl -fsSL "https://raw.githubusercontent.com/davidlosasgonzalez/llmux/main/scripts/install.sh" | sh
```

Windows PowerShell:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/davidlosasgonzalez/llmux/main/scripts/install.ps1")))
```

Re-run the same command whenever you want to update. You can review the installers before running them: [install.sh](scripts/install.sh) and [install.ps1](scripts/install.ps1).

### 2. Start The Server

```bash
llmux-server
```

To print the installed LLMux version without starting the server,
run `llmux-server --version`.

Keep this process running. By default, the Admin UI opens in your browser once
the server is healthy. Its address is always shown in the startup log:

```text
INFO:     Admin UI: http://127.0.0.1:8082/admin (local-only)
```

Use the port shown in your terminal if it differs from `8082`.

<a id="nvidia-nim-provider"></a>

### 3. Configure NVIDIA NIM

1. Create an API key at [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys).
2. Open the Admin UI URL from the server log.
3. Paste the key into `NVIDIA_NIM_API_KEY`.
4. Leave `MODEL` on the default `nvidia_nim/nvidia/nemotron-3-super-120b-a12b`, or select another model.
5. Click **Validate**, then **Apply**.

### 4. Run Claude Code

```bash
llmux-claude
```

The launcher uses the current Admin UI settings. Use Claude Code's model picker to choose from the models LLMux exposes. Normal CLI arguments still work, for example:

```bash
llmux-claude -p "hello"
```

## Choose A Provider

Enter the listed setting in the Admin UI, set `MODEL` to a provider-prefixed model ID, then click **Validate** and **Apply**. Provider names link to their key, model, or setup pages.

| Provider | Admin UI setting | Example `MODEL` |
| --- | --- | --- |
| [NVIDIA NIM](https://build.nvidia.com/settings/api-keys) | `NVIDIA_NIM_API_KEY` | `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` |
| [OpenRouter](https://openrouter.ai/keys) | `OPENROUTER_API_KEY` | `open_router/openrouter/free` |
| [Google AI Studio (Gemini)](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` | `gemini/models/gemini-3.5-flash` |
| [DeepSeek](https://platform.deepseek.com/api_keys) | `DEEPSEEK_API_KEY` | `deepseek/deepseek-v4-flash` |
| [Mistral La Plateforme](https://console.mistral.ai/) | `MISTRAL_API_KEY` | `mistral/devstral-small-latest` |
| [Mistral Codestral](https://console.mistral.ai/) | `CODESTRAL_API_KEY` | `mistral_codestral/codestral-latest` |
| [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/models-and-providers) | `AI_GATEWAY_API_KEY` | `vercel/openai/gpt-5.6-sol` |
| [Hugging Face Inference Providers](https://huggingface.co/settings/tokens) | `HUGGINGFACE_API_KEY` | `huggingface/Qwen/Qwen3-Coder-480B-A35B-Instruct:fastest` |
| [Cohere](https://dashboard.cohere.com/api-keys) | `COHERE_API_KEY` | `cohere/command-a-plus-05-2026` |
| [GitHub Models](https://github.com/marketplace?type=models) (legacy) | `GITHUB_MODELS_TOKEN` | `github_models/deepseek/deepseek-r1` |
| [Wafer](https://wafer.ai/) (paid flat-rate) | `WAFER_API_KEY` | `wafer/DeepSeek-V4-Pro` |
| [Kimi](https://platform.moonshot.ai/console/api-keys) | `KIMI_API_KEY` | `kimi/kimi-k2.6` |
| [MiniMax](https://platform.minimax.io/user-center/basic-information/interface-key) | `MINIMAX_API_KEY` | `minimax/MiniMax-M3` |
| [Cerebras Inference](https://cloud.cerebras.ai/) | `CEREBRAS_API_KEY` | `cerebras/gpt-oss-120b` |
| [Groq](https://console.groq.com/keys) | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` |
| [SambaNova](https://cloud.sambanova.ai/apis) | `SAMBANOVA_API_KEY` | `sambanova/DeepSeek-V3.2` |
| [Fireworks AI](https://fireworks.ai/account/api-keys) | `FIREWORKS_API_KEY` | `fireworks/accounts/fireworks/models/kimi-k2p6` |
| [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/) | `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` | `cloudflare/@cf/moonshotai/kimi-k2.6` |
| [Z.ai](https://z.ai/manage-apikey/apikey-list) | `ZAI_API_KEY` | `zai/glm-5.2` |
| [Ollama Cloud](https://ollama.com/settings/keys) | `OLLAMA_API_KEY` | `ollama_cloud/qwen3-coder:480b` |
| [LM Studio](https://lmstudio.ai/) | `LM_STUDIO_BASE_URL` | `lmstudio/<model-id>` |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `LLAMACPP_BASE_URL` | `llamacpp/<model-id>` |
| [Ollama](https://ollama.com/) | `OLLAMA_BASE_URL` | `ollama/<model-tag>` |

Important provider notes:

- Mistral Codestral uses a separate key from Mistral La Plateforme.
- Cloudflare requires both its API token and account ID.
- Ollama Cloud connects directly to `ollama.com`; use the exact model IDs shown
  by LLMux's model picker. Local Ollama remains available through the separate
  `ollama/` prefix.
- GitHub Models was retired for new customers in June 2026; the provider only
  works if your account was grandfathered in.
- Wafer is a flat-rate paid subscription (not a free tier), served from
  `pass.wafer.ai`.
- Prefer tool-capable models for coding agents. Local models also need enough context for the agent's system prompt and tool definitions.

<details>
<summary><strong>Local provider setup</strong></summary>

### LM Studio

Start LM Studio's local server, load a tool-capable model, and use the model identifier shown by LM Studio with the `lmstudio/` prefix. The default URL is `http://localhost:1234/v1`.

### llama.cpp

Start `llama-server` with its OpenAI-compatible Chat Completions API and enough context for the model. Use the local model ID with the `llamacpp/` prefix. `LLAMACPP_BASE_URL` defaults to `http://localhost:8080/v1`; LLMux accepts either the server root or an explicit `/v1` suffix.

### Ollama

```bash
ollama pull llama3.1
ollama serve
```

Use the tag shown by `ollama list` with the `ollama/` prefix. `OLLAMA_BASE_URL` defaults to `http://localhost:11434`; LLMux accepts either the root URL or an explicit `/v1` suffix.

</details>

### Which Model For What

Model quality shifts monthly. This guide was last verified against provider
docs and independent benchmarks (SWE-bench Verified/Pro, LiveBench agentic
coding, Artificial Analysis) on **2026-07-23**. Any tool-capable model works
through LLMux — these are the combinations that made sense at that date.

**Agentic coding — daily driver (`MODEL`, `MODEL_SONNET`):**

| Model | Providers | Why |
| --- | --- | --- |
| `kimi-k2.6` | kimi, open_router (`:free`), fireworks, cloudflare | Best verified all-round coding agent (SWE-bench Verified 80.2, Terminal-Bench 66.7); `kimi-k2.7-code` is its coding-tuned variant |
| `deepseek-v4-pro` / `deepseek-v4-flash` | deepseek, nvidia_nim, open_router | ~80 SWE-bench Verified, 1M context, very cheap; Flash is the budget pick |
| `glm-5.2` | zai, nvidia_nim | #1 open model on LiveBench agentic coding; 1M context |
| `devstral-2` (123B) / `devstral-small-latest` (24B) | mistral | Purpose-built coding agents; Devstral Small 2 also leads the local class |

**Hard reasoning and planning (`MODEL_OPUS`):**

| Model | Providers | Why |
| --- | --- | --- |
| `kimi-k2.6` (thinking) | kimi, open_router (`:free`) | Top open model on SWE-bench Pro (58.6) |
| `deepseek-v4-pro` (thinking) | deepseek, nvidia_nim | Highest raw reasoning among open models; 1M context |
| `nemotron-3-ultra-550b-a55b` | nvidia_nim, open_router (`:free`) | 1M-context planner/orchestrator, free on two providers |

**Fast and cheap (`MODEL_HAIKU`, `MODEL_CLASSIFIER`):**

| Model | Providers | Why |
| --- | --- | --- |
| `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` | gemini | Best free request headroom, 1M context |
| `openai/gpt-oss-20b` | groq | ~1,000 tok/s; Groq's official successor to `llama-3.1-8b-instant` |
| `openai/gpt-oss-120b` | groq, cerebras | Fast and noticeably smarter; Cerebras free tier caps context around 8K |

**Local (LM Studio, Ollama, llama.cpp):** `qwen3-coder:30b` (fast MoE, strong
tool calling), Devstral Small 2 (24B, agent-tuned), or `qwen3.6-27b` (best
local all-rounder on 24 GB VRAM).

**Poor fits for coding agents** (still callable, just not recommended):

- Llama 3.3 70B and Llama 4 — a generation behind on repo-level work (Llama 4
  Maverick scores ~24 on SWE-bench Verified); Groq retires its Llama chat
  models on 2026-08-16.
- `codestral-latest` — a completion/FIM specialist for IDE autocomplete, not an
  agent model; use Devstral for agents.
- `deepseek-chat` / `deepseek-reasoner` — legacy aliases retired by DeepSeek on
  2026-07-24; use explicit `deepseek-v4-*` IDs.
- `gpt-oss-120b` as the main coder — excellent fast tier, but well behind the
  open leaders on SWE-bench.
- Gemini Pro (`gemini-3.1-pro-preview`) — Pro models left the AI Studio free
  tier in April 2026; paid only.

### Optional Model-Tier Routing

`MODEL` is the fallback for every request. Set `MODEL_FABLE`, `MODEL_OPUS`, `MODEL_SONNET`, or `MODEL_HAIKU` to override individual Claude Code tiers; leave a tier blank to inherit `MODEL`.

For example, route Opus to `nvidia_nim/moonshotai/kimi-k2.6`, Sonnet to `open_router/openrouter/free`, Haiku to `ollama/qwen3-coder:30b`, and keep `MODEL` on `zai/glm-5.2`.

### Optional Auto-Routing (`MODEL_ROUTING_MODE=auto`)

By default (`MODEL_ROUTING_MODE=static`), the tier overrides above are the only
routing logic — every request for a given tier always goes to the same
configured model.

Set `MODEL_ROUTING_MODE=auto` and `MODEL_CLASSIFIER=<provider/model>` (a fast,
cheap model, e.g. `groq/openai/gpt-oss-20b`) to instead have a classifier
model grade each request's complexity — trivial, standard, or complex — and
map that grade deterministically to `MODEL_HAIKU`, `MODEL_SONNET`, or
`MODEL_OPUS`. This only affects the alias-based tiers — a request that already
names an explicit `provider/model` always bypasses the classifier.

Any classifier failure (unset/invalid `MODEL_CLASSIFIER`, no reachable
candidate, an unparsable answer) falls back to the static routing above; the
existing `MODEL_FALLBACKS` chain still applies on top of whichever model was
chosen. Inspect a request's classifier decision and outcome with
`llmux-trace <request_id>` (or `llmux-trace --last`), which summarises turns from
`~/.llmux/logs/server.log`.

<a id="connect-your-client"></a>

## Connect Your Client

For terminal use, start `llmux-server`, then run `llmux-claude`. Use the guides below for editor integrations.

<details>
<summary><strong>Claude Code in VS Code</strong></summary>

Install the [Claude Code extension](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code). Open VS Code's user settings as JSON and add:

```json
"claudeCode.disableLoginPrompt": true,
"claudeCode.environmentVariables": [
  { "name": "ANTHROPIC_BASE_URL", "value": "http://localhost:8082" },
  { "name": "ANTHROPIC_AUTH_TOKEN", "value": "llmux" },
  { "name": "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "value": "1" },
  { "name": "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "value": "190000" },
  { "name": "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "value": "1" }
]
```

Match the port and authentication token to the Admin UI, then reload the extension.

</details>

<details>
<summary><strong>Claude Code in JetBrains ACP</strong></summary>

Edit the installed Claude ACP configuration:

- Windows: `C:\Users\%USERNAME%\AppData\Roaming\JetBrains\acp-agents\installed.json`
- Linux/macOS: `~/.jetbrains/acp.json`

Set the environment for `acp.registry.claude-acp`:

```json
"env": {
  "ANTHROPIC_BASE_URL": "http://localhost:8082",
  "ANTHROPIC_AUTH_TOKEN": "llmux",
  "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
  "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "190000",
  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
}
```

Match the port and token to the Admin UI, then restart the IDE.

</details>

<details>
<summary><strong>Claude Code still asks you to log in</strong></summary>

If Claude Code asks you to log in after you configure the LLMux URL and token, open its state file:

- Windows: `%USERPROFILE%\.claude.json`
- macOS/Linux/WSL: `~/.claude.json`

Merge this property into the existing JSON without removing its other fields:

```json
"hasCompletedOnboarding": true
```

If the file does not exist, create it with a complete JSON object:

```json
{
  "hasCompletedOnboarding": true
}
```

Restart Claude Code or the IDE after saving the file.

</details>

## Verdict: Free Multi-Model Second Opinions

Verdict is an optional deliberation layer that asks several free models the
same question, runs a debate, and returns one consolidated answer. It reuses
the provider keys you already configured — it never calls paid models unless
you explicitly allow it.

- CLI: `llmux-verdict evaluate "Should I use X over Y?"`
- MCP server for Claude Code: `llmux-verdict serve-mcp` (register it as a stdio
  MCP server named `llmux-verdict`)
- Status and models: `llmux-verdict providers`, `llmux-verdict models`

See [docs/verdict.md](docs/verdict.md) for configuration and depth options.

## Manage Your Installation

### Update

Re-run the matching command from [Install Or Update](#install).

### Uninstall

Stop every running LLMux command first. The uninstaller removes the LLMux uv tool, verifies every LLMux command is gone, and then deletes `~/.llmux/`. It leaves uv, Python, Claude Code, and shared PATH entries intact.

macOS/Linux:

```bash
curl -fsSL "https://raw.githubusercontent.com/davidlosasgonzalez/llmux/main/scripts/uninstall.sh" | sh
```

Windows PowerShell:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/davidlosasgonzalez/llmux/main/scripts/uninstall.ps1")))
```

## Project Links

- [Report bugs or request features](https://github.com/davidlosasgonzalez/llmux/issues)
- [Architecture and extension guide](ARCHITECTURE.md)
- [Contributing guide](CONTRIBUTING.md)

## License

MIT License. See [LICENSE](LICENSE) for details.
