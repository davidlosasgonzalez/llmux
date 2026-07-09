# Messaging Stop And Provider Error Finalization Plan

## Summary

Fix the three valid defects found while testing PR #1027:

1. Standalone `/stop` can preserve the transcript but still render the wrong final footer because active runner-owned nodes do not receive stop intent.
2. `MessagingNodeRunner` suppresses successful completion after any earlier error event, instead of suppressing only duplicate process-exit errors.
3. Exhausted provider failures, especially upstream 429s, are returned to Claude Code as retryable HTTP statuses. Claude Code then retries the whole `/v1/messages` turn outside FCC's retry policy, leaving messaging stuck on `Continuing conversation...`.

Corrected error policy:

- Provider errors may be visible to Claude Code and the user.
- Provider error diagnostics should be preserved: error type, message, upstream detail, and request id.
- Provider execution failures must not be returned to Claude Code in a retryable HTTP shape after FCC has accepted a streaming `/v1/messages` turn.
- FCC owns upstream retry and recovery. Claude Code should receive only the final terminal outcome.

The customer-facing contract remains:

- Telegram and Discord `/stop` preserve already-rendered transcript content and append a stopped footer.
- Managed Claude Code turns do not remain indefinitely `in_progress` after FCC has already exhausted provider retries.
- Claude Code still sees useful provider failures.
- Provider retry counts, recovery prompts, transport semantics, and request bodies remain unchanged.
- Codex `/v1/responses` is not the triggering bug for this PR, but the plan documents the same retry-ownership principle so the API boundary does not drift.

Because this changes production messaging/API behavior on the PR branch, keep the existing PR version bump unless this work is split after `3.4.14` lands. If split onto a later `main`, bump patch and run `uv lock`.

## Explored Code Paths

### Streaming Messages Boundary

`MessagesHandler.create()` resolves and preflights the request, then returns an async provider SSE body through `anthropic_sse_streaming_response()`.

Important boundary:

- Synchronous failures before the body iterator is returned are ingress/preflight failures.
- Exceptions raised while pulling the first chunk from the body iterator are provider-execution or stream-conversion failures after FCC has started owning the turn.

Current behavior:

- `_first_chunk_streaming_response()` waits for the first chunk.
- If the provider body raises before the first chunk, it calls `MessagesHandler._pre_start_error_response()`.
- `_pre_start_error_response()` currently maps `ProviderError` to its HTTP status.
- For `RateLimitError`, Claude Code receives HTTP 429 and retries the whole request.

That is the live stuck-turn path seen in logs:

```text
POST /v1/messages?beta=true HTTP/1.1" 429 Too Many Requests
POST /v1/messages?beta=true HTTP/1.1" 429 Too Many Requests
...
```

### Provider Retry Boundary

OpenAI-chat and native Anthropic transports already retry internally:

- pre-response retry is owned by `providers.rate_limit.GlobalRateLimiter`;
- early/midstream recovery is owned by `core.anthropic.streaming.RecoveryController`;
- providers raise `map_stream_start_error(...)` only when no downstream-visible SSE has escaped.

So when the API layer sees a final `ProviderError` from first-chunk probing, FCC has already decided provider retry/recovery is exhausted.

Returning HTTP 429/5xx at that point leaks retry ownership back to Claude Code.

### Managed Claude Boundary

Managed messaging runs Claude Code as:

```text
claude --model opus -p ... --output-format stream-json --verbose
```

Messaging waits on Claude Code stdout. If Claude Code is retrying the HTTP request internally, messaging has no terminal stdout event to process, so the status remains `Continuing conversation...`.

The fix should not make messaging guess about retry state. The API should return a terminal client-visible result that makes Claude Code stop the turn.

### Codex Responses Boundary

`/v1/responses` already converts Anthropic `event: error` into `response.failed`.

However, pre-first-chunk exceptions still go through the generic `pre_start_error_response` JSON path. That is the same class of boundary issue, but it is not the observed stuck messaging bug. This PR should keep the implementation focused unless adding a shared helper makes the Responses behavior nearly free and tests prove no extra complexity.

## Root Causes

### 1. Global Stop Has No Runner Stop Intent

`MessagingWorkflow.stop_task()` mutates node context before cancelling one node:

```python
node.set_context({"cancel_reason": "stop"})
```

`MessagingWorkflow.stop_all_tasks()` does not do the same. It calls:

```python
cancelled_nodes = await self.tree_queue.cancel_all()
```

`TreeQueueManager.cancel_all()` delegates to `cancel_tree()`, which can classify an active node as runner-owned. The workflow then correctly skips direct status-only edits for runner-owned nodes.

But the runner checks `node.context.cancel_reason`; when it is missing, it renders the generic cancellation path:

```python
transcript.apply({"type": "error", "message": "Task was cancelled"})
await update_ui(self._format_status("Cancelled", ...), force=True)
```

So reply-scoped `/stop` can be correct while standalone `/stop` can still end with `Cancelled`.

### 2. Error Dedupe Is Too Broad

`MessagingNodeRunner` currently tracks `had_error_events` and drops every later `complete` event:

```python
elif ptype == "complete" and had_error_events:
    continue
```

The real duplicate is narrower:

- Claude Code emits a meaningful error event.
- The process exits non-zero.
- `parse_cli_event()` converts the exit into a generic error: `Process exited with code 1`.

The runner should suppress only that duplicate exit-sourced error. A later successful completion is the terminal fact and should win.

### 3. Retryable HTTP Status Leaks Retry Ownership To Claude Code

FCC already retries upstream transient errors. After those retries are exhausted, the provider transports raise a final `ProviderError`.

Current streaming `/v1/messages` behavior:

- final provider `RateLimitError` -> HTTP 429 JSON;
- final provider `OverloadedError` -> HTTP 529 JSON;
- final provider `APIError(status_code=500..599)` -> HTTP 5xx JSON.

Claude Code treats those as retryable HTTP failures. That creates a second retry loop outside FCC and outside messaging's observable event stream.

The root fix is not to hide provider errors. The root fix is to make final provider errors terminal in the Claude protocol instead of retryable in HTTP.

## Grill-Me Decisions

### Decision 1: Where should stop intent live?

Recommended answer: cancellation intent belongs at the tree cancellation boundary.

Why:

- `TreeQueueManager` knows which nodes are active, queued, stale, runner-owned, or workflow-owned.
- Runner-owned cleanup needs the stop reason before task cancellation reaches the runner.
- Workflow-side context mutation works only for single-node stop; global stop needs the same behavior atomically.

Decision:

- Add typed cancellation intent in `messaging/trees/cancellation.py`.
- Tree cancellation APIs accept optional `reason`.
- Stop commands pass `CancellationReason.STOP`.
- Tree manager attaches that reason before cancelling active tasks.

### Decision 2: Should workflow edit active runner-owned status messages as a fallback?

Recommended answer: no.

Why:

- The current PR exists because workflow-level direct edits can erase active transcript content.
- The runner owns `TranscriptBuffer`, render context, throttling, and final transcript-preserving edits.
- Workflow-owned nodes have no transcript buffer, so workflow may still render simple stopped status for queued/no-run nodes.

Decision:

- Keep runner-owned and workflow-owned cancellation UI separate.
- Fix missing stop intent rather than adding a second UI writer.

### Decision 3: How should `/clear` interact with stop intent?

Recommended answer: `/clear` may reuse stop intent for its first cancellation phase.

Why:

- `/clear` stops active work before deleting chat messages.
- If a stopped message survives long enough to render, `Stopped.` is more accurate than `Cancelled`.
- Clear/delete remains the owner of final chat cleanup.

Decision:

- `MessagingWorkflow.stop_all_tasks()` passes stop intent.
- Lower-level tree cancellation defaults to no intent for internal non-stop callers.

### Decision 4: Where should duplicate exit-code errors be suppressed?

Recommended answer: in `MessagingNodeRunner`.

Why:

- `parse_cli_event()` should parse raw facts.
- The runner owns turn-level event sequencing.
- The duplicate condition depends on prior rendered events in the same turn.

Decision:

- Replace `had_error_events` with a narrower state, such as `had_non_exit_error`.
- If parsed event is `source == "exit"` and a prior non-exit error was rendered, skip it.
- Do not skip successful completion just because an earlier error existed.

### Decision 5: If an error is followed by successful completion, what wins?

Recommended answer: successful completion wins.

Why:

- It is the actual terminal process result.
- A prior warning/error-like event can be recoverable or superseded.
- Keeping node state `ERROR` after successful terminal completion is less correct.

Decision:

- Let `complete(status="success")` pass through.
- Completion updates node state to `COMPLETED`.
- Completion path clears stale node error state if needed.
- Do not redesign child-error propagation in this PR unless a test proves it is necessary.

### Decision 6: Should provider errors be hidden from Claude Code?

Recommended answer: no.

Why:

- Users need actionable error messages.
- Current provider mapping includes useful upstream status/body/cause/request-id detail.
- Hiding errors would make debugging worse.

Decision:

- Preserve provider error text and error type.
- Change only the retryability of the downstream shape.

### Decision 7: What downstream shape should streaming `/v1/messages` use for final provider errors?

Recommended answer: HTTP 200 with terminal Anthropic SSE `event: error`.

Why:

- The request is a streaming Claude Messages turn.
- Claude protocol already has an error event shape.
- HTTP 200 commits the stream and prevents Claude Code from treating the request itself as retryable.
- The error remains visible as protocol data.

Decision:

- For streaming `/v1/messages`, first-chunk provider-execution failures become terminal SSE error frames.
- The terminal frame preserves `ProviderError.error_type` and `ProviderError.message`.
- This applies to final provider auth/rate-limit/overload/API errors from execution, not just 429/5xx, because the API boundary should be uniform once provider execution owns the turn.
- FCC auth, request validation, unsupported tools, model routing/preflight request-shape failures, and other ingress failures remain HTTP errors.

### Decision 8: Should unexpected stream-body exceptions also be terminalized?

Recommended answer: yes for streaming `/v1/messages`.

Why:

- Once the body iterator is being probed, the API has accepted a streaming turn.
- Retrying an internal stream conversion failure externally is unlikely to help.
- Messaging should receive a terminal result instead of waiting while Claude Code retries.

Decision:

- For streaming `/v1/messages`, any exception raised while pulling the first stream chunk becomes a terminal SSE error response.
- `ProviderError` keeps its mapped type/message.
- Unknown exceptions are logged and emitted as `api_error` with the same sanitized user-facing message policy used today.
- `asyncio.CancelledError` and `GeneratorExit` are still re-raised by the response wrapper; cancellation is not an error result.

### Decision 9: Should non-stream `/v1/messages` change now?

Recommended answer: no.

Why:

- The observed stuck bug is the streaming Claude turn path.
- Non-stream aggregation has no SSE protocol to commit.
- Changing non-stream HTTP status semantics would be a separate client contract decision.

Decision:

- Keep `stream=false` behavior unchanged in this PR.
- Add an explicit residual risk: if Claude Code is later shown to retry non-stream provider execution errors in a harmful loop, add a separate non-stream policy, likely a non-retryable dependency-failure status with the same Anthropic error body.

### Decision 10: Should `/v1/responses` be changed in this PR?

Recommended answer: only if the shared helper makes it trivial and tests remain simple; otherwise no.

Why:

- The current defect is managed Claude messaging.
- Responses already converts Anthropic `event: error` into `response.failed`.
- Pre-start Responses provider exceptions are the analogous boundary, but there is no current evidence of a Codex stuck loop.

Decision:

- Document the product-wide principle in `ARCHITECTURE.md`: streaming product APIs should surface final provider execution failures as terminal protocol events, not retryable HTTP statuses.
- Implement Messages now.
- Do not add broad Responses changes unless the implementation naturally exposes a reusable terminalization helper without additional architecture spread.

## Target Architecture

### Ownership Boundaries

- Provider transports own upstream request construction, provider-specific retries/fallbacks, stream parsing, and recovery.
- `providers.error_mapping` owns mapping raw SDK/HTTP errors into `ProviderError` with user-facing diagnostics.
- `api.response_streams` owns first-chunk stream commit behavior.
- `api.handlers.messages` owns Claude Messages product policy: what Claude Code receives for accepted streaming turns.
- Messaging owns rendering CLI events and terminal node state, not provider retry semantics.

### Streaming Error Policy

Add an explicit policy at the public API boundary:

```text
Ingress/preflight failure before accepted stream
  -> HTTP error response

Accepted streaming Messages turn, provider/stream body fails before first chunk
  -> HTTP 200 text/event-stream
  -> terminal Anthropic event: error

Accepted streaming Messages turn, provider/stream body fails after first chunk
  -> existing terminal stream error behavior
```

This keeps the distinction precise:

- HTTP status communicates whether FCC accepted the client request.
- SSE terminal event communicates the provider execution result.

### Error Payload Shape

Anthropic terminal stream error:

```text
event: error
data: {
  "type": "error",
  "error": {
    "type": "<provider error_type or api_error>",
    "message": "<sanitized provider/user-facing message>"
  }
}
```

The message should preserve current provider diagnostics, including copied upstream details and request id when present.

## Implementation Plan

### Step 1: Add Typed Cancellation Intent

Files:

- `messaging/trees/cancellation.py`
- `messaging/trees/manager.py`
- `messaging/workflow.py`
- `messaging/node_runner.py`

Changes:

- Add `CancellationReason` enum with at least `STOP = "stop"`.
- Add helper functions:
  - `set_cancel_reason(node, reason)`
  - `get_cancel_reason(node)`
- Preserve existing node context when setting cancellation reason.
- Update tree cancellation APIs:
  - `cancel_tree(root_id, *, reason=None)`
  - `cancel_node(node_id, *, reason=None)`
  - `cancel_all(*, reason=None)`
  - `cancel_branch(branch_root_id, *, reason=None)`
- Apply cancellation reason before active task cancellation is delivered.
- `MessagingWorkflow.stop_all_tasks()` and `stop_task()` pass `CancellationReason.STOP`.
- `MessagingNodeRunner` reads cancel reason through the helper.

Expected behavior:

- Standalone `/stop` active task keeps transcript and final footer is `Stopped.`.
- Reply `/stop` active task keeps existing correct behavior.
- Queued/no-run stop remains workflow-owned and status-only.
- Generic internal cancellation can still render generic cancellation.

### Step 2: Narrow Exit Error Dedupe

Files:

- `messaging/node_runner.py`
- `messaging/event_parser.py`
- `messaging/node_event_pipeline.py`

Changes:

- Keep `parse_cli_event()` returning exit-sourced error facts for non-zero exits.
- Replace `had_error_events` with narrower state:
  - `had_non_exit_error`
  - optionally `had_terminal_success`
- Runner logic:
  - non-exit error: process and set `had_non_exit_error = True`;
  - exit-sourced error after non-exit error: skip;
  - exit-sourced error without prior non-exit error: process;
  - successful complete: process normally;
  - non-success complete: keep existing ignored behavior.
- Ensure successful completion leaves node state `COMPLETED` and does not leave a stale `error_message`.

Expected behavior:

- Provider error + exit code 1 renders only the useful provider error.
- Exit code 1 alone still renders process-exit error.
- Error-like event followed by success can complete.

### Step 3: Add Anthropic Terminal Error Serialization For Provider Execution Failures

Files:

- `core/anthropic/streaming/emitter.py`
- `api/response_streams.py`
- `api/handlers/messages.py`

Changes:

- Add a serializer that accepts an error type and message, not only a hardcoded `api_error`.
- Keep existing `anthropic_terminal_error_frame(message)` for generic egress interruption or replace it with a small wrapper around the typed serializer.
- Add a helper for turning an exception into a terminal Anthropic SSE error:
  - `ProviderError` -> use `exc.error_type` and `exc.message`;
  - unknown `Exception` -> log through existing unexpected-error path and use sanitized `get_user_facing_error_message(exc)`;
  - `EmptyStreamError` -> `api_error`, existing empty-stream message.
- Do not include raw tracebacks or secrets in the SSE error.

Expected behavior:

- Existing post-start egress interruption still emits generic `api_error`.
- Pre-start provider errors can preserve their specific type, e.g. `rate_limit_error`.

### Step 4: Terminalize First-Chunk Streaming Messages Failures

Files:

- `api/handlers/messages.py`
- `api/response_streams.py`
- tests under `tests/api/`

Changes:

- Allow `pre_start_error_response` to return any `Response`, not just JSON.
- In `MessagesHandler._pre_start_error_response()`:
  - return `StreamingResponse` with `text/event-stream` for exceptions raised while probing the stream body;
  - emit exactly one terminal Anthropic `event: error`;
  - use HTTP 200;
  - include `ANTHROPIC_SSE_RESPONSE_HEADERS`;
  - trace `api.response.provider_error_terminalized` or `api.response.stream_start_error_terminalized`.
- Keep synchronous `ProviderError` raised before `_to_public_response()` as HTTP. Those are preflight/ingress failures.
- Keep `stream=false` aggregation behavior unchanged.

Expected behavior:

- Final provider 429 after FCC retries: Claude receives terminal SSE error, not HTTP 429.
- Final provider 5xx/529 after FCC retries: Claude receives terminal SSE error, not retryable HTTP.
- Final provider auth error during provider execution: Claude receives terminal SSE `authentication_error`.
- FCC auth failure before handler: still HTTP 401.
- Request validation/unsupported tool/preflight request-shape failure: still HTTP 400.

### Step 5: Keep Provider Retry/Recovery Unchanged

Files:

- `providers/rate_limit.py`
- `providers/transports/openai_chat/stream.py`
- `providers/transports/anthropic_messages/stream.py`

Changes:

- No retry count changes.
- No NIM-specific fallback.
- No request trimming.
- No prompt/history modification.
- No recovery prompt changes.

Expected behavior:

- Provider retry ownership stays where it is.
- API only changes the final shape returned to Claude after provider retry/recovery is exhausted.

### Step 6: Document The Boundary

Files:

- `ARCHITECTURE.md`

Changes:

- Add a short note to the API/product boundary section:
  - provider transports own upstream retry/recovery;
  - public streaming product handlers own downstream terminal error shape;
  - final provider execution failures must be surfaced as protocol terminal events for streaming clients, not retryable HTTP statuses.

## Test Plan

### Messaging Stop Tests

Add/update:

- `tests/messaging/test_handler.py`
- `tests/messaging/test_tree_queue.py`
- `tests/messaging/test_handler_markdown_and_status_edges.py`

Cases:

- Active standalone `/stop`:
  - node emits partial transcript;
  - `stop_all_tasks()` is called;
  - final message contains prior transcript and `Stopped.`;
  - final message does not contain generic `Cancelled`.
- Reply-scoped active `/stop` remains correct.
- Queued standalone `/stop` still gets workflow-owned `Stopped.`.
- `cancel_all(reason=STOP)` marks active runner-owned nodes before cancellation cleanup.
- Cancellation context helper preserves unrelated node context keys.

### CLI Event Dedupe Tests

Add/update:

- `tests/messaging/test_event_parser.py`
- `tests/messaging/test_handler.py`
- `tests/cli/test_cli.py`

Cases:

- Provider error followed by exit code 1:
  - provider error renders once;
  - `Process exited with code 1` is suppressed;
  - no complete footer;
  - node state is `ERROR`.
- Exit code 1 without prior non-exit error:
  - process-exit error renders.
- Non-exit error followed by successful exit:
  - completion is processed;
  - final footer is `Complete`;
  - node state is `COMPLETED`.
- Non-success complete remains ignored.

### Streaming Messages API Tests

Add/update:

- `tests/api/test_response_streams.py`
- `tests/api/test_api_handlers.py`
- `tests/api/test_api.py`

Cases:

- Streaming `/v1/messages`, body raises `RateLimitError` before first chunk:
  - HTTP 200;
  - content type `text/event-stream`;
  - single terminal `event: error`;
  - error type `rate_limit_error`;
  - message preserves provider diagnostic text.
- Streaming `/v1/messages`, body raises `OverloadedError` before first chunk:
  - HTTP 200 terminal SSE error;
  - no retryable HTTP 529.
- Streaming `/v1/messages`, body raises `APIError(status_code=503)` before first chunk:
  - HTTP 200 terminal SSE error;
  - error type `api_error`.
- Streaming `/v1/messages`, body raises provider `AuthenticationError` during provider execution:
  - HTTP 200 terminal SSE error;
  - error type `authentication_error`.
- FCC/API ingress auth failure remains HTTP 401.
- Invalid request/preflight `InvalidRequestError` before stream body remains HTTP 400.
- Unexpected exception from stream body before first chunk:
  - HTTP 200 terminal SSE `api_error`;
  - unexpected exception is logged.
- Empty stream before first chunk:
  - HTTP 200 terminal SSE `api_error`.
- Post-start exception behavior remains existing terminal stream error frame.
- `stream=false` behavior remains unchanged.

### Managed Messaging Regression Tests

Add/update:

- `tests/messaging/test_handler.py`
- `tests/cli/test_cli.py`

Cases:

- Managed Claude emits a provider error event caused by terminal SSE, then exits non-zero:
  - messaging renders the provider error;
  - node leaves `IN_PROGRESS`;
  - no duplicate process-exit error;
  - no false `Complete`.
- Managed Claude completes successfully after a non-exit warning/error-like event:
  - success completion wins.

### Optional Responses Guardrail

Add only if implementation naturally touches shared streaming policy:

- pre-start `/v1/responses` provider execution failure emits `response.created` then `response.failed`;
- Codex does not receive retryable HTTP for final provider execution errors.

If this adds meaningful complexity, leave it out and document as a follow-up.

## Verification Commands

Run targeted checks first:

```powershell
uv run pytest tests/messaging/test_handler.py tests/messaging/test_handler_markdown_and_status_edges.py tests/messaging/test_tree_queue.py tests/messaging/test_event_parser.py tests/cli/test_cli.py
uv run pytest tests/api/test_response_streams.py tests/api/test_api_handlers.py tests/api/test_api.py
```

Then run the final local gate:

```powershell
.\scripts\ci.ps1
```

If the local server is running, stop it before full CI because installer/uninstaller tests require no active `fcc-server` process.

## Out Of Scope

- Changing provider retry counts, backoff, or rate-limit blocking.
- Adding NIM-specific request fallbacks.
- Trimming prompts, tools, or conversation history.
- Changing `stream=false` response policy.
- Redesigning `/v1/responses` unless the shared helper makes the fix trivial.
- Changing Claude Code invocation flags.
- Changing Telegram/Discord platform adapters beyond tests expecting final UI text.

## Residual Risks

- Claude Code could theoretically retry terminal SSE `event: error`. If observed, the next escalation is a managed-Claude-specific request marker plus a known non-retryable terminal shape. That should not be implemented until evidence proves SSE terminal errors are retried.
- `stream=false` provider errors may still use retryable HTTP statuses. This is acceptable for this PR because the live stuck path is streaming; if a non-stream retry loop is observed, add a separate non-stream provider-error policy.
- Allowing success completion after a prior non-exit error may expose a stale propagated child cancellation if a child was already marked failed. The first implementation should test the known event order; if a real recoverable-error-success stream exists, delay child propagation until terminal process state in a later focused design.
