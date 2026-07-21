# Backlog — Own Agent Harness

> Origen: diseño en `docs/own-agent.md` (2026-07-21). Giro de rumbo del
> mismo día: la superficie interactiva pasa a **OpenCode + SSH (Termius)**;
> Telegram/Discord deja de ser el canal de órdenes.

## Fase 1 — Harness mínimo — ✅ (`4.8.0`)

A1–A5: bucle, tools, permisos, breakers, `fcc-agent`.

## Fase 2 — Fiabilidad tool calling — ✅ (`4.9.0`)

A6 schema repair · A7 `core/quota` · A8 `FallbackProxyClient`.

## Fase 3 — Contexto — ✅ (`4.10.0`)

- [x] **A9 — Compactación de historial** (`agent/context.py`), goal + recent
  turns preservados; tests de shrink.
- **A10 — Evaluar RAG (diferida).** No implementar sin evidencia en repos
  grandes.

## Fase 4 — Autonomía y remoto — ✅ (`4.11.0`)

- [x] **A11 — Harness en messaging** (opt-in `MESSAGING_SESSION_BACKEND=agent`).
- [x] **A12 — Aprobación remota** (`ApprovalBroker` + `RemotePermissionGate`;
  timeout → deny).
- [x] **A13 — Cola desatendida** (`AgentJobQueue` con timeout + concurrencia).

Nota (giro OpenCode): A11–A12 quedan implementadas y opt-in pero **sin uso
previsto** — la superficie de control es SSH, no messaging. No se extienden.

## Descartado (2026-07-21)

- **UI de botones de aprobación en Telegram/Discord.** Motivo: el control
  remoto real es Termius + SSH + tmux, y en terminal la aprobación ya la
  cubre el `console_confirm` existente. El `ApprovalBroker` se conserva por
  si algún día se retoma messaging.

## Fase 5 — Integración OpenCode — ✅ (`4.12.0`)

- [x] **B1 — Verificar config OpenCode vigente.** Validado contra OpenCode
  `1.18.4` (`opencode debug config`). Snippet en `docs/own-agent.md`.
  `baseURL` = `{proxy}/v1` + `@ai-sdk/anthropic`; MCP local
  `type: local` + `command: [fcc-council, serve-mcp]`.
- [x] **B2 — Launcher `fcc-opencode`.** Preflight proxy, escribe
  `~/.fcc/opencode.json`, exporta `OPENCODE_CONFIG`, lanza binario.
- [x] **B3 — MCP `free-llm-verdict` en OpenCode.** Registrado en la config
  que genera el launcher.
- [x] **B4 — Guía de despliegue en servidor.** `docs/deploy-server.md`.
- [x] **B5 — Smoke E2E OpenCode→proxy.** `smoke/prereq/test_opencode_prereq_live.py`
  (salta si no hay `opencode` / proxy).
- [x] **B6 — CLI para la cola desatendida.** `fcc-agent jobs
  enqueue|status|result` (+ `_run` worker) con persistencia en
  `~/.fcc/agent_jobs/`.

## Nota de versionado

Fase 5 → `4.12.0` (+ `uv lock`).
