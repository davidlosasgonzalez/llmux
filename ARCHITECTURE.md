# Architecture

This document is a maintainer-oriented map of Free Claude Code. It explains the
runtime boundaries, request flows, provider abstraction, configuration model,
and verification strategy.

For installation, provider setup, and user-facing usage, see
[README.md](README.md). This file focuses on where behavior lives in the codebase
and how contributors should extend it.

## System Overview

Free Claude Code is a local proxy for agent clients. It accepts Anthropic
Messages traffic from Claude Code, routes the request to a configured upstream
provider, and preserves the wire protocol expected by the caller.

There are three runtime surfaces:

- HTTP proxy: FastAPI routes expose Anthropic-compatible, health, model-listing,
  and admin endpoints.
- CLI launcher: the `fcc-claude` wrapper entrypoint prepares Claude Code
  sessions so they target the local proxy.
- Standalone tools: `fcc-verdict` (multi-model evaluation CLI and MCP server)
  and `fcc-trace` (per-turn log summaries) run beside the proxy.

```mermaid
flowchart LR
    ClaudeCode[Claude Code CLI and Extensions] --> ProxyAPI[FastAPI Proxy]
    AdminUI[Local Admin UI] --> ProxyAPI
    ProxyAPI --> Handlers[API Product Handlers]
    Handlers --> Router[application ModelRouter]
    Handlers --> Executor[application ProviderExecutor]
    Executor --> Lease[Provider Generation Lease]
    Lease --> Providers[ProviderRuntime]
    Providers --> OpenAIChat[OpenAI Chat Provider Profiles And Specialized Adapters]
```

## Package Boundaries

The installable wheel packages are declared in [pyproject.toml](pyproject.toml):

- [src/free_claude_code/application/](src/free_claude_code/application/) is the dependency-leaf application boundary. It
  owns immutable routing/model-metadata values, model routing, optional
  classifier-driven auto-routing, shared provider execution, the consumer-facing
  `ProviderPort`, request-runtime lease ports, and deterministic
  request/readiness errors. It depends only on configuration and core
  protocol-neutral logic.
- [src/free_claude_code/api/](src/free_claude_code/api/) is the HTTP adapter. It owns the FastAPI app, routes, API product
  handlers, local optimizations, model-catalog responses, HTTP error mapping,
  response commit timing, and Admin-specific ports. It consumes application and
  protocol types instead of defining use cases or wire schemas.
- [src/free_claude_code/cli/](src/free_claude_code/cli/) owns console entrypoints, the client CLI launcher,
  the shared Claude proxy environment, and child-process registration.
- [src/free_claude_code/config/](src/free_claude_code/config/) owns settings, provider metadata, filesystem paths,
  logging setup, constants, and provider ID catalogs.
- [src/free_claude_code/core/](src/free_claude_code/core/) owns provider-neutral protocol logic: wire request and response
  models, Anthropic conversion, SSE construction,
  canonical execution-failure semantics, credential-safe diagnostics, token
  counting, and structured trace helpers. It never classifies provider SDK or
  HTTP client exceptions.
- [src/free_claude_code/observability/](src/free_claude_code/observability/) owns the `fcc-trace` CLI, which renders
  per-turn summaries from the managed server log.
- [src/free_claude_code/providers/](src/free_claude_code/providers/) owns provider construction, the shared OpenAI-chat
  provider, specialized adapters, SDK/HTTP failure classification, retry and
  recovery policy, rate limiting, model listing, and concrete provider adapters.
- [src/free_claude_code/runtime/](src/free_claude_code/runtime/) is the process composition root. It owns application
  startup and shutdown, provider generations, Admin runtime operations, and the
  concrete wiring between API and providers.
- [src/free_claude_code/verdict/](src/free_claude_code/verdict/) owns the `fcc-verdict` multi-model evaluation
  CLI and its MCP server: provider discovery, capability scoring, orchestration,
  and result storage.

[tests/](tests/) contains deterministic unit and contract coverage.
[smoke/](smoke/) contains local and live product smoke tests that can launch
subprocesses or touch real services.

Production package imports follow one least-privilege dependency policy. Every
listed edge is exercised by the current code; removing the last use of an edge
also removes that permission:

| Package | Exact allowed direct dependencies |
| --- | --- |
| `config` | none |
| `core` | none |
| `application` | `config`, `core` |
| `providers` | `application`, `config`, `core` |
| `api` | `application`, `config`, `core` |
| `cli` | `config`, `core` |
| `verdict` | `config`, `core`, `providers` |
| `observability` | `config` |
| `runtime` | `api`, `application`, `config`, `core`, `providers` |

There is one exact exception:
`free_claude_code.cli.entrypoints` imports
`free_claude_code.runtime.bootstrap` because the installed server executable
delegates construction to the process composition root. The exception does not
permit any broader dependency from `cli` to `runtime`. Every new top-level
package or cross-package edge must be added to the policy deliberately.

Internal modules do not import an ancestor package facade; package initializers
may import dependency leaves to publish supported exports. Code outside
`providers.openai_chat` consumes that owner through its package facade.
Deliberate provider factory loading is protected by the provider catalog,
supported-ID, and factory synchronization contract.

[core/version.py](src/free_claude_code/core/version.py) is the sole runtime owner
of the FCC release version. It reads installed distribution metadata for
FastAPI/OpenAPI, FCC-owned CLI `--version` output, and the outbound web-tools
user agent. A source-only checkout without installed metadata reports the
explicit `0+unknown` fallback; runtime code never parses `pyproject.toml` or
duplicates a release literal. Client launcher arguments remain transparent to
the wrapped client.

The main ownership rule is that Anthropic protocol schemas and
shared protocol behavior belong in [src/free_claude_code/core/](src/free_claude_code/core/), while request routing and
provider execution belong in [src/free_claude_code/application/](src/free_claude_code/application/). Routes use core schemas
directly for wire validation and call application use cases. Provider modules use
the same concrete request types and neutral helpers instead of importing the API
adapter or another provider.
Protocol consumers use the public `core.anthropic` facade. Low-level Anthropic
core and provider modules may import the dependency-leaf Anthropic `models.py`
module directly so their type dependency is explicit.
Package initialization and those leaves must remain import-order safe.
The model-list schema stays beside its API-owned construction policy in
`api/model_catalog.py`; there is no generic API model package.

## Customer-Facing Contract

FCC optimizes for installed user workflows, not internal compatibility. The
behavior that must be preserved is that these user-facing surfaces run correctly
for real prompts against supported providers:

- `fcc-server` and the local Admin UI for configuring supported providers,
  model routing, auth, server tools, and diagnostics.
- `fcc-claude`, Claude Code, and the Anthropic-compatible proxy behavior Claude
  Code relies on, including streaming text, native/interleaved thinking, tool
  use/results, model discovery, token counting, retries/recovery, and supported
  local server-tool behavior.
- `fcc-verdict`, its MCP server, and `fcc-trace` as installed companion tools.
- Installation, update, init, and uninstall scripts insofar as they make the
  above workflows available on a user's machine.

Internal modules, class designs, helper APIs, route implementations, and tests
are not stable contracts. Refactors may replace or remove them when doing so
simplifies the system, improves correctness, or better matches these
architecture boundaries. When tests primarily encode an obsolete internal shape,
update the tests to assert the customer-facing behavior instead. Features,
compatibility shims, endpoints, or helper paths that do not serve one of the
surfaces above are not product requirements and should be removed rather than
preserved.

## Design Pressure And Refactor Targets

The current package boundaries are intentional, but several modules still carry
large orchestration responsibilities. Treat these as refactor targets, not as
new places to add unrelated behavior:

- [api/handlers/](src/free_claude_code/api/handlers/) owns customer-facing API product flows:
  Claude Messages and token counting. Keep route handlers
  thin, keep Claude-only behavior in the Messages handler, and use
  [application/execution.py](src/free_claude_code/application/execution.py) only for shared
  provider resolution, preflight, tracing, token counting, and streaming.
- [providers/openai_chat/](src/free_claude_code/providers/openai_chat/) owns the common upstream provider
  behavior. It separates immutable vendor profiles from per-request stream
  execution, recovery, request policy, and tool-call assembly. Shared
  protocol rules belong in [src/free_claude_code/core/](src/free_claude_code/core/).
- [config/admin/](src/free_claude_code/config/admin/) owns Admin UI config behavior. Keep
  provider fields catalog-driven, and keep manifest, source loading, validation,
  env rendering, value presentation, and status metadata in their package owners.

## Runtime Startup And Lifecycle

Console scripts are registered in [pyproject.toml](pyproject.toml):

- `fcc-server` and `free-claude-code` call `free_claude_code.cli.entrypoints:serve`.
- `fcc-init` calls `free_claude_code.cli.entrypoints:init`.
- `fcc-claude` calls `free_claude_code.cli.launchers.claude:launch`.
- `fcc-verdict` calls `free_claude_code.verdict.cli:main`.
- `fcc-trace` calls `free_claude_code.observability.cli:main`.

[scripts/install.sh](scripts/install.sh) and [scripts/install.ps1](scripts/install.ps1)
install or update the uv tool. [scripts/uninstall.sh](scripts/uninstall.sh)
and [scripts/uninstall.ps1](scripts/uninstall.ps1) remove only the FCC uv tool and always
delete the managed `~/.fcc/` tree from [config/paths.py](src/free_claude_code/config/paths.py); they do not remove
uv, Claude Code, or uv-managed Python runtimes. [scripts/ci.sh](scripts/ci.sh) and
[scripts/ci.ps1](scripts/ci.ps1) mirror [.github/workflows/tests.yml](.github/workflows/tests.yml)
for local pre-push verification.

[cli/entrypoints.py](src/free_claude_code/cli/entrypoints.py) starts the FastAPI server with Uvicorn.
`serve()` migrates legacy env files when needed, loads cached settings, runs a
supervised server instance, and can restart the server after admin config changes.
An Admin restart constructs the next instance only when the prior
`ApplicationRuntime` reports that its complete ownership graph closed. An
incomplete ASGI shutdown therefore exits the supervisor instead of overlapping
old and replacement graphs. On final shutdown it best-effort kills registered
child processes.

[runtime/bootstrap.py](src/free_claude_code/runtime/bootstrap.py) is the single production composition function. The CLI
supervisor supplies one settings snapshot and its restart callback; bootstrap
configures logging, constructs the runtime owners, constructs the explicit
`ApiServices` composition value, and returns the ASGI application. Provider
request leases satisfy the consumer-owned ports in
[application/ports.py](src/free_claude_code/application/ports.py); Admin operations retain
their inbound-adapter port in [api/ports.py](src/free_claude_code/api/ports.py).

[api/app.py](src/free_claude_code/api/app.py) registers routers and exception
handlers around an explicit `ApiServices` value, then wraps the application in a
pure ASGI correlation boundary. The boundary surrounds the complete wire send;
it does not proxy streaming responses through `BaseHTTPMiddleware`. The API does
not read global settings or construct runtime resources.
`app.state.services` is the only runtime state published to FastAPI.

[runtime/application.py](src/free_claude_code/runtime/application.py) owns process startup and shutdown, Admin pending
state, and the injected restart callback. Shutdown is serialized: it closes the
provider manager and releases an owner reference only after its cleanup
succeeds; cancellation or failure leaves the incomplete graph retryable, and the
ASGI adapter reports that incomplete graph as lifespan shutdown failure. Cleanup
is completion-driven: generic timeouts do not cancel half-closed external
resources; the process supervisor owns any force-termination deadline.
[runtime/asgi.py](src/free_claude_code/runtime/asgi.py) drives that owner from ASGI lifespan messages and preserves
the concise startup-failure contract.

[runtime/provider_manager.py](src/free_claude_code/runtime/provider_manager.py) is the only owner that constructs, publishes,
retires, and closes provider generations. Each request acquires a generation
lease before routing. Non-streaming responses release it after aggregation;
streaming responses bind it to FCC's response owner, which first closes the
entire body chain and then releases the lease on completion, failure,
cancellation, disconnect, or a response-start send failure. A provider-only
Admin Apply prepares a candidate and commits configuration before publication.
New requests then use the candidate while old streams finish on the retired
generation; its last lease closes it exactly once. Final shutdown rejects new
acquisition and replacement, waits every lease, and awaits the same
manager-owned cleanup task even if the initiating request or lease release is
cancelled. Failed generation or unpublished-candidate cleanup remains owned and
retryable; the manager does not become terminal or clear its model catalog until
every owned runtime closes.

The manager also owns one application-lifetime provider model catalog and its
single best-effort discovery task. The catalog survives provider replacement.
This keeps the server model inventory stable without extra synchronization;
Claude clients may independently retain the list they fetched at startup.

## Configuration Model

[config/settings.py](src/free_claude_code/config/settings.py) owns the flat Pydantic Settings schema:
raw env fields, validation, and `get_settings()`. It should not own routing,
model-ref parsing, launcher defaults, or web-tool policy. Dotenv discovery lives
in [config/env_files.py](src/free_claude_code/config/env_files.py) and uses this order:

1. repo-local `.env`;
2. managed `~/.fcc/.env`;
3. optional `FCC_ENV_FILE`, appended when present.

Later dotenv files override earlier dotenv files. Process environment variables
also participate through Pydantic settings resolution. `ANTHROPIC_AUTH_TOKEN`
has an extra guard after settings are built: if any configured dotenv file
defines it, that dotenv value replaces a stale inherited shell token. Auth-token
source detection for startup warnings also belongs to `src/free_claude_code/config/env_files.py`.

[config/paths.py](src/free_claude_code/config/paths.py) defines managed paths:

- config directory: `~/.fcc`;
- managed env file: `~/.fcc/.env`;
- server log: `~/.fcc/logs/server.log`;
- Verdict MCP server log: `~/.fcc/logs/verdict-mcp.log`.

Model routing configuration is tiered:

- `MODEL` is the fallback provider-prefixed model ref.
- `MODEL_FABLE`, `MODEL_OPUS`, `MODEL_SONNET`, and `MODEL_HAIKU` override Claude model tiers.
- `ENABLE_MODEL_THINKING` is the global thinking switch.
- `ENABLE_FABLE_THINKING`, `ENABLE_OPUS_THINKING`, `ENABLE_SONNET_THINKING`, and
  `ENABLE_HAIKU_THINKING` optionally override thinking by tier.

[config/model_refs.py](src/free_claude_code/config/model_refs.py) owns provider-prefixed model ref
parsing and configured `MODEL*` inventory. API routing and provider validation
depend on those helpers instead of adding behavior methods to Settings.

[config/admin/](src/free_claude_code/config/admin/) owns the Admin UI config manifest and
managed env writes. Provider credential, local URL, proxy, and display-name
metadata is generated from [config/provider_catalog.py](src/free_claude_code/config/provider_catalog.py);
admin-only help text stays beside the admin manifest. The package splits source
loading, value presentation, validation, persistence, and provider status into
separate modules. [api/admin_routes.py](src/free_claude_code/api/admin_routes.py) exposes local-only
admin endpoints that load and validate config, then delegate runtime operations
through `AdminRuntimePort`. Provider-only Apply prepares prospective settings,
atomically commits the managed env, and publishes a new provider generation.
Restart-required changes preserve the existing supervisor restart flow and do
not publish an in-process generation first.

[.env.example](.env.example) is the single install/init/admin template source.
It is packaged as a [src/free_claude_code/config/](src/free_claude_code/config/) resource for `fcc-init` and Admin UI
template defaults; runtime settings do not read it as a live config file.

Admin routes call `require_loopback_admin()`, which rejects non-loopback clients
and non-local origins.

## HTTP Request Flow

[api/routes.py](src/free_claude_code/api/routes.py) exposes the public proxy routes:

- `POST /v1/messages`: Anthropic Messages-compatible streaming requests.
- `POST /v1/messages/count_tokens`: Anthropic token counting.
- `GET /v1/models`: gateway and Claude-compatible model listing.
- `GET /health`: health check.
- `HEAD` and `OPTIONS` probes for compatibility on supported endpoints.

Admin routes live beside these in [api/admin_routes.py](src/free_claude_code/api/admin_routes.py).

Authentication is handled by `require_proxy_auth()` in
[api/dependencies.py](src/free_claude_code/api/dependencies.py). If `ANTHROPIC_AUTH_TOKEN` is blank,
proxy auth is disabled. Otherwise FCC accepts exactly `Authorization: Bearer
<token>`. Other credential headers are ignored, so a stale provider API key
cannot mask valid proxy authorization. The complete bearer token is compared
in constant time; no model suffix or other token mutation is accepted.

HTTP request correlation is owned at ingress. A pure ASGI boundary creates one
opaque FCC request ID before routing, places it in log context and request state,
and adds `request-id` while forwarding the actual `http.response.start` message.
The shared model catalog also exposes the same
value as `x-request-id`. Provider execution and trace events receive that
existing ID; they do not create a second identifier. Keeping the context around
the complete inner ASGI call preserves correlation during streaming and leaves
response lifetime finalization under the concrete response owner. Starlette's
outer server-error boundary bypasses user middleware for its catch-all 500, so
that one handler explicitly attaches the same ingress-owned headers.

[api/handlers/](src/free_claude_code/api/handlers/) owns the public API product flows.
`MessagesHandler` validates non-empty messages, resolves models, applies
Claude-only safety-classifier and local optimization policy, handles local web
server tools, then streams Anthropic SSE. `TokenCountHandler`
owns Anthropic token counting. Shared provider execution lives in
[application/execution.py](src/free_claude_code/application/execution.py). `ProviderExecutor` resolves the narrow
consumer-owned `ProviderPort`, synchronously preflights the upstream request,
emits trace events, counts input tokens, and returns an Anthropic SSE iterator.
It receives only a provider resolver and the few scalar collaborators it needs;
it does not depend on FastAPI, provider implementations, or the full settings
object.
[api/response_streams.py](src/free_claude_code/api/response_streams.py) owns public streaming egress
commit timing. It waits for the first protocol chunk before returning a
successful FCC-owned `StreamingResponse`. Its explicit replay iterator owns the
prefetched stream even before replay begins. The response itself owns one
idempotent finalization task: close the body transitively, then release the
provider-generation lease. This finalizer surrounds the real ASGI send and runs
to completion even when sending headers or the first body frame fails. A provider
execution failure before that commit boundary remains a real typed non-2xx JSON
response. Once FCC has finalized the failure, the response includes
`x-should-retry: false` so FCC retains ownership of upstream retry/recovery
without causing a second client retry loop. After the first chunk has escaped,
HTTP status is committed; Messages emits an Anthropic `event: error` and closes
without a synthetic `message_stop`. Messages are non-streaming unless the client explicitly
sets `stream: true`. Non-streaming Messages aggregate internally and return
non-2xx JSON for any terminal stream error, discarding incomplete content rather
than presenting a partial success.

The public response chain follows a transitive close-ownership rule. A response
owns its replay iterator; replay owns the active protocol adapter; each protocol
adapter owns its direct input; tracing owns the executor body; the executor body
owns the provider iterator; and the provider runner owns its upstream stream.
Each of these response-chain owners closes its direct input on normal completion,
failure, cancellation, and early consumer close. Failures from those explicit
cleanup calls are trace metadata and cannot replace an established wire outcome;
a generation lease is released only after the body chain has finished closing.

Ingress authentication, request validation, model routing, and deterministic
preflight failures remain ordinary HTTP errors and do not receive the terminal
provider-execution retry header. Missing provider configuration and a shutting
down request runtime are application-readiness errors: Messages serializes them
as Anthropic JSON, and neither is
misclassified as an already-finalized provider execution failure.

```mermaid
sequenceDiagram
    participant Client
    participant Route as FastAPIRoute
    participant Handler as ProductHandler
    participant Router as ModelRouter
    participant Exec as ProviderExecutor
    participant Manager as ProviderRuntimeManager
    participant Lease as ProviderGenerationLease
    participant Runtime as ProviderRuntimeGeneration
    participant Provider

    Client->>Route: POST /v1/messages
    Route->>Route: require_proxy_auth
    Route->>Manager: acquire current generation
    Manager-->>Route: Lease(settings, provider resolver)
    Route->>Handler: create message
    Handler->>Router: resolve model and thinking
    Handler->>Handler: server tools or optimizations
    Handler->>Exec: stream routed request
    Exec->>Lease: resolve provider
    Lease->>Runtime: cached or new provider
    Runtime->>Provider: cached or new provider
    Exec->>Provider: preflight_stream
    Exec->>Provider: stream_response
    Provider-->>Client: Anthropic SSE events
    Route->>Lease: release after complete body
```

## Model Routing

[application/routing.py](src/free_claude_code/application/routing.py) resolves incoming client model names.
It supports two forms:

- Direct provider model refs such as `nvidia_nim/nvidia/model-name`.
- Gateway model IDs decoded by [core/gateway_model_ids.py](src/free_claude_code/core/gateway_model_ids.py).

If the incoming model is not direct, `ModelRouter` maps it by Claude tier. Names
containing `fable`, `opus`, `sonnet`, or `haiku` use the matching tier override when set,
otherwise they fall back to `MODEL`.

[application/auto_router.py](src/free_claude_code/application/auto_router.py) owns
optional dynamic routing. It is off by default; when `MODEL_ROUTING_MODE=auto`,
a fast operator-configured classifier model (`MODEL_CLASSIFIER`) picks among the
operator's already-configured chat models before static resolution. The
candidate menu is built entirely from configured refs plus credential presence,
and any auto-routing failure falls back to the exact static resolution, so
auto-routing can never break a request.

The router also resolves thinking. Gateway model IDs can force thinking on or
off; otherwise `ModelRouter` applies tier-specific thinking overrides or the
global setting. `ResolvedModel` carries only the selected route and thinking
decision; provider catalog metadata does not cross the application boundary.

`GET /v1/models` advertises:

- configured provider model refs;
- cached provider-discovered models;
- no-thinking variants when appropriate;
- built-in Claude model IDs for compatibility with Claude clients.

Provider model discovery and optional thinking metadata live in the
application-level catalog owned by `ProviderRuntimeManager`.
`ProviderModelInfo.supports_thinking` alone owns discovered per-model thinking
support; provider-wide capabilities do not model thinking. The catalog is not
part of an individual provider generation, so a hot replacement does not erase
the last useful model list. Discovery failures retain prior entries.

## Provider Architecture

Provider metadata is neutral and centralized in
[config/provider_catalog.py](src/free_claude_code/config/provider_catalog.py). Each
`ProviderDescriptor` declares provider ID, display name, locality, credential env
var, default base URL, settings attribute names, and proxy support. It does not
select a concrete adapter.

[providers/runtime/](src/free_claude_code/providers/runtime/) owns construction details for one
closable provider generation: construction policy, resolved provider
configuration, lazy provider instances, provider-owned rate limiters, and
cleanup. [providers/runtime/factory.py](src/free_claude_code/providers/runtime/factory.py)
constructs ordinary provider IDs from `OPENAI_CHAT_PROFILES` and keeps a sparse
factory mapping only for adapters with real state or algorithms. The union of
those two construction owners must exactly equal the neutral provider catalog.
`ProviderRuntime` directly guarantees one provider and limiter per provider ID
within a generation; there is no pass-through cache object, process singleton,
or second limiter registry. Provider admission combines a strict proactive window with
one reactive backoff deadline. Positive backoffs can only extend that deadline,
and admission loops until proactive capacity and the final reactive check are
simultaneously available. The proactive timestamp is recorded only when that
check succeeds, so a concurrent 429/5xx cannot be missed, shortened, consume
unused quota, or release queued requests as an expiry burst. Retired generations
retain their own synchronization state until request leases drain, while new
generations and separate server instances never reuse it. Hot replacement
therefore begins with fresh quota state; an old and new generation enforce
independent budgets while old request leases drain. Application-level generation
publication, request leases, model metadata, discovery orchestration, and
configured-model validation belong to `ProviderRuntimeManager` in the runtime
package. This separates a single generation's resources from process-lifetime
state.

[application/model_metadata.py](src/free_claude_code/application/model_metadata.py) owns the immutable
`ProviderModelInfo` value consumed by the application catalog. Provider-specific
model-list modules retain response parsing and construct that value directly;
there is no provider-layer alias for the former owner.

[application/ports.py](src/free_claude_code/application/ports.py) defines the two provider operations consumed by request
execution: synchronous `preflight_stream()` and lazy `stream_response()`. API
handlers and application execution depend on that structural port, never on a
provider base class. Provider adapters implement it without registration or a
compatibility layer.

[providers/base.py](src/free_claude_code/providers/base.py) defines provider-internal construction and lifecycle contracts:

- `ProviderConfig`: shared provider settings such as API key, base URL, rate
  limits, timeouts, proxy, thinking, and logging flags. It is a frozen internal
  value whose base URL has already been resolved from the catalog.
- `BaseProvider`: the abstract implementation base for cleanup, model listing,
  explicit preflight, and `stream_response()`.

There is one upstream provider family:
[providers/openai_chat/](src/free_claude_code/providers/openai_chat/) implements the concrete
`OpenAIChatProvider` used by every OpenAI-compatible `/chat/completions`
upstream. `OpenAIChatProfile` contains immutable request policy, its standard
streamed-reasoning field, postprocessors, and base-URL normalization for
ordinary vendors. Configuration differences therefore remain data rather than
empty subclasses. The package also
owns the exactly typed private per-request runner, recovery operations, tool-call
assembly, and streamed usage handling. No obsolete generic transport namespace
or untyped provider backchannel remains.

`OpenAIChatProvider` explicitly implements preflight by constructing the same
upstream request body it will later stream. `BaseProvider` makes that operation
abstract, so a new provider cannot silently omit the commit-boundary validation.
LM Studio composes the OpenAI-chat conversion first and its context-budget probe
second; conversion failure therefore cannot open a stream or run the probe.

Providers call the OpenAI request policy for Anthropic-to-OpenAI conversion,
thinking replay selection, `extra_body`, and chat-completion field normalization.
Specialized provider packages remain only for true upstream quirks such as
Gemini thought signatures, NIM tool-schema aliases, retry downgrades, and NVCF
deployment-failure classification, or DeepSeek attachment/tool/thinking
compatibility. Local Ollama, Ollama Cloud, llama.cpp, and LM Studio all use the
same OpenAI-compatible Chat Completions provider family;
Ollama's standard `reasoning` delta and history field are profile data rather
than a specialized adapter. DeepSeek intentionally uses its
OpenAI-compatible Chat Completions endpoint because that is the endpoint that
reports prompt-cache hit/miss counters; the provider maps those counters back
into Anthropic usage fields for Claude-compatible clients. Cloudflare uses its
account-scoped Workers AI OpenAI-compatible Chat Completions endpoint for
`@cf/...` model IDs, while account ID composition, model search, and
Cloudflare-specific reasoning deltas stay in the Cloudflare provider client.
OpenRouter remains specialized for model filtering and reasoning-detail stream
events. Wafer, Kimi, MiniMax, Fireworks, and Z.ai use ordinary declarative
profiles for their thinking, token, and `extra_body` policy. Z.ai is treated as
the GLM Coding Plan provider and uses Z.ai's Coding Plan OpenAI base.
Mistral La Plateforme keeps its native `reasoning_effort` and thinking-chunk
request/stream mapping inside
[providers/mistral/reasoning.py](src/free_claude_code/providers/mistral/reasoning.py), including its
fallback retry when a selected Mistral model rejects reasoning fields.
NIM reasoning budget control is also treated as a provider-owned best-effort
downgrade: if an upstream NIM deployment rejects explicit budget control, FCC
retries without the budget while preserving thinking enablement.

Shared provider responsibilities include upstream rate limiting, model listing,
SDK/HTTP failure classification, safe diagnostic construction, HTTP resource
cleanup, thinking/tool handling, retry or recovery where supported, and
returning successful Anthropic SSE strings to the service layer. Final failures
cross that boundary as `ExecutionFailure`, not as provider-authored wire events.
Every provider receives the same concrete
`MessagesRequest` owned by the Anthropic protocol package. Known wire fields are
accessed through that model; `Any` and dynamic attribute lookup are reserved for
SDK response objects and genuinely open-ended nested extension payloads.
Provider-specific inputs that do not apply to other upstreams, such as
Cloudflare's account ID, stay in that provider's factory/client instead of being
added to shared `ProviderConfig`.
Gateway providers such as Vercel AI Gateway, Hugging Face, and Cohere are
profiles because their documented behavior is expressible as request policy.
GitHub Models remains specialized because it owns API headers, a separate model
catalog client, and capability filtering. The OpenAI-chat provider owns standard
streamed usage handling: it requests
`stream_options.include_usage`, consumes provider `prompt_tokens` and
`completion_tokens` when present, and falls back to local estimates when
providers omit or reject optional usage metadata. Provider modules only own true
usage quirks such as DeepSeek prompt-cache counters.

### Adding A Provider

1. Add provider metadata to [config/provider_catalog.py](src/free_claude_code/config/provider_catalog.py).
2. Add credentials and related settings to [config/settings.py](src/free_claude_code/config/settings.py)
   and [.env.example](.env.example) when user configurable.
3. Let Admin UI provider credential, local URL, and proxy fields come from the
   catalog. Add admin-only help text or provider-specific fields under
   [config/admin/](src/free_claude_code/config/admin/) only when the generated manifest is
   insufficient.
4. Add an `OpenAIChatProfile` under [providers/openai_chat/](src/free_claude_code/providers/openai_chat/) when
   request policy fully describes the upstream.
5. Add a specialized provider package and sparse factory entry only when the
   upstream owns state, model-list behavior, stream events, or retry algorithms
   that a profile cannot express.
6. Add deterministic tests under [tests/providers/](tests/providers/) and any
   relevant contract tests.
7. Add smoke coverage or smoke config in [smoke/](smoke/) when the provider can
   be exercised live.
8. Update user-facing provider docs in [README.md](README.md) when users need new
   setup instructions.

## Protocol Conversion And Streaming Contracts

[src/free_claude_code/core/anthropic/](src/free_claude_code/core/anthropic/) owns Anthropic-side protocol behavior:

- `models.py` defines the permissive Messages and token-count wire requests,
  content/tool/thinking blocks, and Anthropic response envelopes;
- trace-safe request snapshots stay beside those models so the generic trace
  module remains protocol-independent and import-order safe;
- text, image, and message conversion for OpenAI-compatible upstreams;
- request serialization primitives shared by provider request policies;
- tool schema and tool-result handling;
- thinking block handling;
- stream lifecycle through `src/free_claude_code/core/anthropic/streaming`, including the neutral
  stream ledger, Anthropic SSE emitter, continuation-body construction, and tool repair;
- token counting and Anthropic-owned failure-kind-to-wire mapping.

User image conversion is a pure protocol operation. Core maps Anthropic base64
and URL image sources to ordered OpenAI `image_url` content parts without
fetching remote content. Provider adapters do not gate that conversion behind a
provider-wide vision flag; the selected upstream model owns image capability,
while any deliberate provider-specific attachment removal remains explicit
compatibility policy.

Shared stream behavior lives under
[src/free_claude_code/core/anthropic/streaming/](src/free_claude_code/core/anthropic/streaming/). The shared layer owns the
Anthropic content-block ledger, SSE serialization, continuation request
transformations, and tool JSON repair. It does not import `httpx` or the OpenAI
SDK and does not decide whether an upstream failure is retryable.

[core/failures.py](src/free_claude_code/core/failures.py) defines the immutable,
protocol-neutral `FailureKind` and `ExecutionFailure`. The exception is the
value propagated through async iterators; its semantic fields are immutable,
while Python remains free to attach traceback/cause metadata during unwinding.
[core/diagnostics.py](src/free_claude_code/core/diagnostics.py) owns bounded error
body/cause extraction, credential redaction, safe traceback formatting, and
copyable request-ID diagnostics. The Anthropic package maps the canonical kind
and status to its wire error types.

[providers/failure_policy.py](src/free_claude_code/providers/failure_policy.py)
owns generic raw OpenAI SDK and `httpx` exception classification,
transient status/body inference, stable provider wording, and final diagnostic
construction for those failures.
Concrete adapters may supply one narrow semantic override for an upstream quirk
that the shared SDK cannot express correctly. The concrete adapter owns the
exact upstream marker, while the shared failure policy owns its canonical
meaning and wording. The limiter uses that meaning for retry qualification and
its existing provider-wide reactive backoff while retaining the raw exception,
so exhausted retries still receive the original HTTP status/body through the
shared redaction and diagnostic path. For NVCF's function-scoped failure this
deliberately keeps the simple one-limiter-per-provider policy; a degraded NIM
function can therefore briefly delay other NIM models during backoff. No
provider-specific marker enters `core/`, another provider, or an API adapter.
[providers/stream_recovery.py](src/free_claude_code/providers/stream_recovery.py)
owns the 0.75-second/65,536-byte holdback, four transparent early retries after
the first attempt, and five midstream recovery attempts. Provider opening keeps
its existing five-attempt exponential-backoff budget. `ExecutionFailure.retryable`
records provider-policy eligibility; it never tells the client to retry after FCC
has finalized the failure.

The OpenAI-chat provider remains an upstream adapter: it converts OpenAI chat
chunks into ledger operations. After retry, continuation, and tool salvage are
exhausted, it discards uncommitted output or flushes committed output, closes
open content blocks, and raises `ExecutionFailure`. It never synthesizes a
terminal Anthropic error event.

The public HTTP commit boundary solely decides whether a final failure can use
non-2xx JSON or must use a terminal protocol event; the protocol packages own
envelope and event serialization. Before the first public frame the boundary
returns typed non-2xx JSON with `x-should-retry: false`; after the first frame
Messages appends one Anthropic `event: error`. Non-streaming Messages catches
the same failure and discards its partial aggregate. Unexpected failures use the
same commit-state split but do not acquire provider retry semantics.

Provider code should delegate protocol details to these modules. Avoid copying
conversion code into individual providers, and avoid provider-to-provider imports
for shared Anthropic behavior.

## Local Optimizations And Server Tools

[api/optimization_handlers.py](src/free_claude_code/api/optimization_handlers.py) short-circuits
common low-value client requests before they reach a provider:

- quota probes;
- command prefix detection;
- title generation;
- suggestion mode;
- filepath extraction.

The Messages handler runs these only after model routing and after local server-tool
handling. Each optimization is controlled by settings flags.

Claude Code auto-mode safety-classifier requests are a message-only routing
policy, not a short-circuit response. After routing, the Messages handler detects the
narrow classifier prompt shape and forces thinking off before provider execution
so Claude Code receives a parser-readable `<block>yes</block>` or
`<block>no</block>` verdict.

Local `web_search` and `web_fetch` handling lives under
[api/web_tools/](src/free_claude_code/api/web_tools/). When `ENABLE_WEB_SERVER_TOOLS` is true, the
Messages handler can stream local Anthropic server-tool responses without sending the
request upstream. [api/web_tools/egress.py](src/free_claude_code/api/web_tools/egress.py) enforces URL
scheme and private-network restrictions for `web_fetch`.

Anthropic server-tool definitions are never passed to upstream OpenAI Chat
providers because that conversion would be lossy. Forced `web_search` or
`web_fetch` requests are handled locally when `ENABLE_WEB_SERVER_TOOLS` is true;
otherwise the Messages handler rejects them before provider execution.

## CLI Launcher

[cli/proxy_auth.py](src/free_claude_code/cli/proxy_auth.py) owns the neutral
proxy-auth token policy shared by client launchers. A blank configured token
becomes the local-only `fcc-no-auth` sentinel so clients cross their login gates
while FCC continues to run without API authentication.

[cli/claude_env.py](src/free_claude_code/cli/claude_env.py) owns the canonical
Claude Code proxy environment used by every FCC-launched Claude process. It
strips inherited `ANTHROPIC_*` variables, sets `ANTHROPIC_BASE_URL`, enables
gateway model discovery, configures the auto-compact window, disables
nonessential Anthropic traffic, and always sets `ANTHROPIC_AUTH_TOKEN`. Blank
proxy auth uses the shared local-only sentinel so Claude Code reaches the proxy
instead of stopping at its login gate.

[cli/launchers/claude.py](src/free_claude_code/cli/launchers/claude.py) owns the installed
`fcc-claude` launcher:

- `fcc-claude` applies the shared proxy environment without changing the user's
  Claude command arguments.

[cli/launchers/common.py](src/free_claude_code/cli/launchers/common.py) owns the
shared launcher process helpers: the proxy `/health` preflight, client binary
resolution, and running the wrapped client with child-PID registration through
[cli/process_registry.py](src/free_claude_code/cli/process_registry.py).

## Observability, Diagnostics, And Safety

[core/trace.py](src/free_claude_code/core/trace.py) emits structured trace events across stages such
as ingress, routing, provider, and egress. Trace
payloads are intended to connect API and provider activity
without requiring raw transport logs by default.
[observability/turn_trace.py](src/free_claude_code/observability/turn_trace.py)
consumes the managed server log to render `fcc-trace` per-turn summaries,
including the model that actually served each turn.

Logging defaults are conservative:

- API payloads and SSE events are not logged raw unless explicitly enabled.
- Provider and application errors log metadata by default; verbose traceback and
  message logging are opt-in.
- Process logging and server authentication policy
  are captured by their lifecycle owners at construction. Admin marks those
  settings restart-required so an Apply cannot report success while an existing
  runtime continues using stale security or privacy policy.
- Values under keys that look like API keys, authorization, tokens, or secrets
  are redacted by trace helpers where structured traces are emitted.

Important safety boundaries:

- Admin UI and admin APIs are loopback-only.
- Proxy API auth is controlled by `ANTHROPIC_AUTH_TOKEN`.
- `web_fetch` egress defaults to configured URL schemes and blocks private
  network targets unless explicitly allowed.
- Local provider URLs are user-configurable, but local-provider status checks are
  exposed only through the local admin API.

## Testing And CI Strategy

Deterministic tests live under [tests/](tests/). They cover API routes, config,
provider conversion, upstream adapters, streaming contracts, CLI
adapters, import boundaries, provider catalog contracts, and other invariants.
The import-boundary contract derives every static production edge with one AST
scanner and checks the package matrix, exact exceptions, and facade
ownership. The resulting first-party module graph must remain
acyclic. The same contract rejects untyped provider collaborators and private
provider access from helper modules. These tests protect current architectural
properties rather than preserving deleted modules or an exact internal file
layout.

Live and local product tests live under [smoke/](smoke/). See
[smoke/README.md](smoke/README.md) for target taxonomy, environment variables,
failure classes, and examples. Smoke tests can launch subprocesses, call real
providers, and touch local model servers.

CI is defined in [.github/workflows/tests.yml](.github/workflows/tests.yml). It
enforces:

- `Ban type ignore suppressions`;
- `ruff-format`;
- `ruff-check`;
- `ty`;
- `pytest`.

Contributor verification commands:

```powershell
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest
```

For docs-only architecture changes, a source-link and accuracy review is usually
sufficient. Full CI can still be run when the doc accompanies runtime changes or
when maintainers want branch-level assurance.

## Extension Checklists

### Add An Admin Setting

1. Add or expose the setting in [config/settings.py](src/free_claude_code/config/settings.py).
2. Add the template key to [.env.example](.env.example) if users configure it.
3. Add a `ConfigFieldSpec` under [config/admin/](src/free_claude_code/config/admin/), or add
   provider catalog metadata when the setting is provider credential, local URL,
   proxy, or display-name metadata.
4. Mark `restart_required` or `session_sensitive` when runtime state cannot be
   updated in place.
5. Add tests under [tests/api/](tests/api/) or [tests/config/](tests/config/).

### Add Or Change A Client Surface

1. For an installed wrapper, add or update a launcher under
   [cli/launchers/](src/free_claude_code/cli/launchers/) and keep credential stripping local to that
   client.
2. Add launcher and customer-flow tests under [tests/cli/](tests/cli/).

### Add Protocol Behavior

1. Put shared Anthropic behavior under [src/free_claude_code/core/anthropic/](src/free_claude_code/core/anthropic/).
2. Keep provider-specific request quirks inside the provider profile or specialized
   provider subclass.
3. Add stream contract tests under [tests/contracts/](tests/contracts/) or
   [tests/core/](tests/core/) when event shape or ordering changes.
4. Add provider tests when the behavior changes upstream request or response
   handling.

## Maintenance Rules For This Document

Update this file when a change adds or meaningfully changes:

- a top-level package or installable runtime boundary;
- a public route or wire protocol;
- startup, shutdown, or resource ownership;
- configuration precedence or managed config behavior;
- provider runtime, catalog, or upstream-adapter architecture;
- model routing or thinking behavior;
- CLI adapter behavior;
- protocol conversion or streaming contracts;
- CI, smoke, or verification strategy.

Docs-only changes to this file do not require a semver bump. Production code
changes still follow the versioning rules in [AGENTS.md](AGENTS.md) and
[CLAUDE.md](CLAUDE.md).

