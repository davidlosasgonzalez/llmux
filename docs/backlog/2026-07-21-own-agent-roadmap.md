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

- [x] **B1–B6** — config OpenCode, `fcc-opencode`, MCP, deploy guide, smoke,
  `fcc-agent jobs`.

## Fase 6 — v1 servidor: continuidad + calidad — ✅ (`4.13.0`)

- [x] **C1 — Política de modelos.** Eval + ganador
  `open_router/moonshotai/kimi-k2.5` (`docs/evals/2026-07-21-c1-opencode-models.md`).
- [x] **C2 — Fallback pre-commit.** `MODEL_FALLBACKS` +
  `application/fallback.py` + `ProviderExecutor` (solo antes del primer
  byte SSE). Admin field + `.env.example`.
- [x] **C3 — Launcher multi-modelo.** `fcc-opencode` lista `MODEL` +
  fallbacks.
- [x] **C4 — Council sistemático.** `docs/opencode-agents.template.md` +
  comando `/council` en config generada.
- [x] **C5 — Arranque persistente.** `deploy/fcc-server.service` +
  `docs/deploy-server.md`.
- [x] **C6 — Cuotas SSH.** `fcc-council usage` + port-forward documentados.
- [x] **C7 — Smoke continuidad.** `tests/application/test_fallback_continuity.py`
  + checklist v1 en deploy guide.
- [x] **C8 — A/B Council.** Decisión: Council en nicho, no default
  (`docs/evals/2026-07-21-c8-council-ab.md`).
- [x] **C9 — Subagente second-opinion.** Generado por launcher (otro modelo
  de la cadena; docs OpenCode `agent` + `mode: subagent`).
- [x] **C10 — Stats → fallbacks.** Procedimiento en `docs/deploy-server.md` §5.

Fuera de v1 (consciente): RAG (A10), validación happy-path de tool calls en
el proxy (v1.5), cambio de modelo mid-stream, messaging como canal.

## Nota de versionado

Fase 6 → `4.13.0` (+ `uv lock`) por C2/C3/C9 (producción).
