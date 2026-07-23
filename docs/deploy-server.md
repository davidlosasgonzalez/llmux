# Deploying On A Server

How to run LLMux as a persistent background service on a remote server (VPS,
homelab box, etc.) alongside Claude Code over SSH.

## 1. Base setup

1. Install [uv](https://docs.astral.sh/uv/) and Python 3.14: `uv python install 3.14.0`.
2. Get LLMux onto the server — either the [one-line installer](../README.md#install)
   or a `git clone` (see [Install From A Local Checkout](../README.md#install-from-a-local-checkout)).
3. Copy `.env.example` to `~/.llmux/.env` and fill in provider API keys.
4. Set `MODEL` and `MODEL_FALLBACKS` (see [Choose A Provider](../README.md#choose-a-provider)).
5. Install Claude Code: `curl -fsSL https://claude.ai/install.sh | bash`.

## 2. Run LLMux as a systemd service

### User-mode (recommended, no root required)

Use the unit shipped at [`deploy/llmux-server.service`](../deploy/llmux-server.service):

```bash
mkdir -p ~/.config/systemd/user ~/.llmux/logs ~/.local/bin
cp deploy/llmux-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llmux-server.service
loginctl enable-linger "$USER"   # keep it running after SSH logout
curl -fsS http://127.0.0.1:8082/health
```

Quick alternative without systemd: `tmux new -s llmux` then `llmux-server`.

### Root/system-mode (e.g. a dedicated VPS with the checkout under `/opt`)

```ini
# /etc/systemd/system/llmux-server.service
[Unit]
Description=LLMux proxy (llmux-server)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/llmux
EnvironmentFile=/root/.llmux/.env
ExecStart=/opt/llmux/.venv/bin/llmux-server
Restart=on-failure
RestartSec=3
Environment=HOME=/root
StandardOutput=append:/root/.llmux/logs/systemd-server.log
StandardError=append:/root/.llmux/logs/systemd-server.log

[Install]
WantedBy=multi-user.target
```

Two gotchas worth knowing before you hit them:

- **`EnvironmentFile=` is required.** `Settings` does not load `~/.llmux/.env`
  in this context; without it, the proxy starts "healthy" but every request
  answers with `X_API_KEY is not set`.
- **Point `ExecStart` directly at the venv binary, never at `uv run`.** `uv run`
  as the main process can leave an orphaned child listening on the port that
  survives `systemctl restart` (symptom: `MainPID=0` while the unit reports
  "active", and config changes appear to have no effect).
- The `.env` file consumed by `EnvironmentFile=` must not quote its values —
  they're passed to the process literally, quotes included.

Updating after a `git pull`:

```bash
cd /opt/llmux
git pull --ff-only
export PATH="$HOME/.local/bin:$PATH"   # uv isn't on PATH in a non-interactive shell
uv sync
systemctl restart llmux-server.service
sleep 5 && curl -fsS http://127.0.0.1:8082/health
# Always confirm with a real end-to-end request afterward, not just /health.
```

## 3. Run Claude Code against it

```bash
cd /path/to/project
llmux-claude
llmux-claude -p "reply with just: pong"
```

The launcher checks that the proxy is up and injects `ANTHROPIC_BASE_URL` and
`ANTHROPIC_AUTH_TOKEN` from the current Admin UI settings. Claude Code's native
`/model` picker lists whatever models LLMux exposes.

Register the Verdict MCP server for multi-model second opinions:

```bash
claude mcp add llmux-verdict -- llmux-verdict serve-mcp
```

## 4. Remote workflow over SSH

1. SSH into the server.
2. `cd project && llmux-claude` to work.
3. Reach the Admin UI from your laptop via an SSH tunnel:
   `ssh -L 8082:127.0.0.1:8082 user@server`, then open `http://127.0.0.1:8082/admin`.
4. Check usage over SSH: `llmux-verdict usage`.
5. Diagnose a specific request: `llmux-trace --last`.

## 5. Ranking fallbacks

1. Prefer models with a high success rate / low latency — inspect
   `llmux-verdict usage --output json` or query `model_stats` directly in
   `~/.llmux/verdict.db`.
2. Put the best model as `MODEL`, the next 2–3 as `MODEL_FALLBACKS`.
3. If the whole `MODEL`/`MODEL_FALLBACKS` chain sits around a similar context
   window (e.g. all ~131k), set `MODEL_LONG_CONTEXT` to a large-window model
   (gemini, minimax, kimi) — otherwise a long Claude Code conversation can
   exhaust the entire chain at once. `llmux-server` warns about this at
   startup when it's missing, and also prints the recommended
   `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` for the client based on the chain's real
   ceiling.
4. If the static context-window/cap tables are wrong for your account (e.g. a
   provider's free tier caps context lower than the table assumes), fix it
   with `CONTEXT_WINDOW_OVERRIDES` (`model_or_ref=tokens`) instead of touching
   code.
5. Restart `llmux-server` to apply config changes.

## 6. Checklist (setting up from scratch)

- [ ] `~/.llmux/.env` has provider keys plus `MODEL`, `MODEL_FALLBACKS`, and
      `MODEL_LONG_CONTEXT`
- [ ] `systemctl --user status llmux-server` (or root-mode equivalent) is active
- [ ] `curl /health` succeeds after a reboot / re-login (with linger enabled)
- [ ] `llmux-verdict usage` prints a table (even if empty)
- [ ] `llmux-claude -p "pong"` completes through the proxy
- [ ] Fallback drill: force a failure on the primary (e.g. a temporarily
      invalid key) and confirm the secondary answers; check for
      `precommit_fallback.serving` in the logs
- [ ] Check the startup log's `Client config:` line for the recommended
      `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and apply it if present
- [ ] Context drill: an oversized prompt gets a 400 `invalid_request_error`
      ("prompt is too long") if no `MODEL_LONG_CONTEXT` is set, or is served
      from that rescue tier if it is
- [ ] `llmux-trace --last` summarizes the turn with the model that served it
