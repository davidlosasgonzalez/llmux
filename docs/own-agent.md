# Own Agent Harness — Diseño

> Origen: análisis del 2026-07-21 (evolución de FCC hacia una alternativa
> completa a Claude Code/Cursor). Tareas accionables en
> `docs/backlog/2026-07-21-own-agent-roadmap.md`.

## Visión

Hoy FCC es un proxy multi-proveedor + Verdict MCP que otros agentes (Claude
Code, Codex, Pi) consumen. El objetivo es un agente de código autónomo
completo sobre modelos gratuitos, sin depender de herramientas comerciales
(Claude Code, Cursor).

**Giro 2026-07-21 — reparto de roles.** El harness interactivo del día a
día es **OpenCode** (open-source, TUI de terminal) apuntando al proxy FCC y
con el MCP `free-llm-verdict` registrado; el control remoto es **SSH
(Termius) + tmux** en el servidor donde vive el proyecto a controlar, no
Telegram/Discord. El harness propio `fcc-agent` (fases 1–4) queda como
motor de trabajos desatendidos (`fcc-agent jobs` / `job_store`) y como plan B
totalmente propio si OpenCode dejara de servir.

Principio rector: **no se reescribe nada**. El harness nuevo es un cliente
más del propio proxy (`POST /v1/messages`), igual que hoy lo es Claude Code.

## Qué se reutiliza tal cual

- **Capa de modelos**: proxy Anthropic Messages + OpenAI Responses, 25
  proveedores, streaming, tool use, vision, thinking, routing por tiers
  (`api/`, `core/`, `providers/`, `application/`).
- **Deliberación**: FCC Verdict (`verdict/`) — descubrimiento y scoring de
  modelos, memoria de cuotas, web research, refinamiento adversarial.
- **Superficie remota**: puente Telegram/Discord con voz, árboles de sesión
  y colas FIFO por conversación (`messaging/`). Tras el giro a SSH queda en
  mantenimiento: opt-in, sin nuevas features.

## Estado actual — hallazgos que condicionan el diseño

1. **Sin validación de tool calls en el camino feliz.** La validación
   `jsonschema` existe solo en la ruta de recuperación de streams cortados
   (`core/anthropic/streaming/recovery.py`); un stream completado con args
   que violan el schema se reenvía verbatim (`ledger.py::emit_tool_delta`).
2. **Sin fallback entre proveedores en el proxy.** `application/routing.py`
   es determinista de un salto; los reintentos golpean el mismo proveedor
   (`providers/rate_limit.py`). La inteligencia de cuotas (SQLite de
   agotamiento diario, circuit breaker, scoring) existe pero solo dentro
   del Verdict.
3. **Las sesiones remotas gestionadas corren `--dangerously-skip-permissions`**
   (`cli/managed/claude.py`); no existe flujo de aprobación remota de
   permisos.
4. **No hay RAG/embeddings/índice de código** en ninguna parte (barrido
   confirmado); la gestión de contexto se delega hoy al CLI cliente.

## Arquitectura del harness (`src/free_claude_code/agent/`)

- **Bucle agéntico**: system prompt + tools núcleo (read, edit, write,
  bash, grep, glob) + bucle que ejecuta tool calls contra el workspace
  hasta que el modelo deja de pedir tools o cumple el objetivo.
- **Cliente del proxy**: el harness habla con el proxy local vía
  `POST /v1/messages`; hereda streaming, proveedores y traducción de
  protocolo sin tocar `core/` ni `providers/`.
- **Permisos por puertos**: allowlist + callback de confirmación definidos
  como puerto (Protocol), con adaptadores distintos para consola y para
  messaging (botones inline).
- **Circuit breakers anti-bucle**: mismo comando fallando 3 veces → parar;
  ciclo edit+revert 2 veces → parar; misma lectura 3 veces sin progreso →
  parar con diagnóstico.
- **Reparación de tool calls en el harness**: al completarse un `tool_use`,
  validar contra `input_schema`; si falla, devolver un `tool_result` de
  error para que el modelo se autocorrija. La reparación vive en el bucle,
  no en la ruta de streaming del proxy.

## Gestión de contexto (RAG diferido)

- Recuperación primaria: **búsqueda agéntica** (grep/glob/read como tools),
  el mismo enfoque de Claude Code/Cursor actuales.
- Compactación de historial al acercarse a la ventana del modelo;
  reutilizar la lógica de context-fit del Verdict para elegir modelos donde
  quepa la conversación.
- RAG vectorial (Ollama + ChromaDB): **decisión diferida** — solo si la
  búsqueda agéntica se queda corta en repos grandes. No es fundacional;
  añadirlo de entrada son 4 dependencias pesadas para un problema
  probablemente inexistente.

## Papel del Verdict

Fuera del camino por-tool-call. El harness / OpenCode puede invocarlo como
revisor opcional en pasos difíciles (planificación, bugs que resisten 2
intentos, revisión de diseño) vía MCP `free-llm-verdict` o `/verdict`.

**Decisión C8 (2026-07-21):** Verdict permanece en ese nicho; no es el path
por defecto. La segunda opinión barata la cubre `@second-opinion` (C9). Las
`model_stats` de `~/.fcc/verdict.db` + el eval C1 ordenan `MODEL_FALLBACKS`
(C10). Detalle: `docs/evals/2026-07-21-c8-verdict-ab.md`.

## Fases

1. **Harness mínimo** — ✅ (2026-07-21, `4.8.0`): bucle + tools + permisos
   consola + breakers + `fcc-agent`. Ver [Implementación](#implementación).
2. **Fiabilidad de tool calling** — ✅ (2026-07-21, `4.9.0`): validación
   schema + repair, `core/quota` compartido, fallback de modelos en el harness.
3. **Contexto** — ✅ (2026-07-21, `4.10.0`): compactación determinista de
   historial (`agent/context.py`). **A10 RAG diferida** (sin implementar).
4. **Autonomía y remoto** — ✅ (2026-07-21, `4.11.0`): harness detrás de
   `messaging/` (opt-in), aprobación remota con timeout→deny, cola de
   trabajos desatendidos. Botones Telegram/Discord: **descartados** (giro a
   SSH).
5. **Integración OpenCode** — ✅ (2026-07-21, `4.12.0`): B1–B6.
6. **v1 servidor: continuidad + calidad** — ✅ (2026-07-21, `4.13.0`): C1–C10
   (eval kimi, `MODEL_FALLBACKS` pre-commit, launcher multi-modelo, Verdict
   nicho, systemd, cuotas SSH, second-opinion, checklist).

## Implementación

### Fase 1 — paquete `src/free_claude_code/agent/`

| Módulo | Rol |
| --- | --- |
| `workspace.py` | Raíz confinada (`ALLOWED_DIR` / cwd / `--workspace`) |
| `tools.py` | read, edit, write, bash, grep, glob + validación schema (A6) |
| `permissions.py` | `PermissionPort` + allowlist + `console_confirm` |
| `breakers.py` | bash 3× · edit/revert 2× · stale read 3× · schema repair 3× |
| `proxy_client.py` | HTTP + `FallbackProxyClient` (A8) |
| `loop.py` | Bucle tool-use |
| `cli.py` | `fcc-agent` + `--fallback-model` + `jobs` |
| `job_store.py` | Persistencia `~/.fcc/agent_jobs/` para SSH (B6) |
| `jobs.py` | Cola in-memory (messaging A13) |

```bash
fcc-server
fcc-agent --yes "crea hello.txt con hola y léelo"
fcc-agent --yes --fallback-model cerebras/gpt-oss-120b "…"
```

### Fase 2 — `src/free_claude_code/core/quota/`

| Pieza | Rol |
| --- | --- |
| `FailureKind` / `QuotaTracker` / `classify_failure` | Circuit breaker compartido (antes solo en `verdict/quota.py`) |
| `DailyExhaustionStore` | SQLite de modelos agotados hoy; VerdictStore delega en las mismas helpers |
| `FallbackProxyClient` | Ante 429/cuota prueba el siguiente `--fallback-model` |

`verdict/quota.py` queda como re-export. No hay código muerto del tracker antiguo.

### Fase 3 — compactación

`agent/context.py`: si el historial supera el presupuesto (~24k tokens
heurísticos), conserva el mensaje-goal, resume el medio y deja los turnos
recientes. Sin llamada extra al modelo (determinista). A10 (RAG) sigue
diferida a propósito.

### Fase 4 — autonomía y remoto

| Pieza | Rol |
| --- | --- |
| `managed_adapter.py` | `AgentManagedSession(Manager)` cumple `ManagedClaudeSession*Protocol` |
| `remote_permissions.py` | `ApprovalBroker` + `RemotePermissionGate` (timeout → deny) |
| `jobs.py` | `AgentJobQueue` in-process (tests / lib); SSH usa `job_store` |

Opt-in: `MESSAGING_SESSION_BACKEND=agent` (default sigue `claude`).
Timeouts: `MESSAGING_AGENT_JOB_TIMEOUT_S`, `MESSAGING_AGENT_APPROVAL_TIMEOUT_S`.
`MESSAGING_AGENT_AUTO_APPROVE=true` salta confirmaciones (solo lab).

El runtime expone `approval_broker` (`resolve(request_id, approved=…)`).
Los botones inline de Telegram/Discord se descartaron con el giro a SSH; el
broker queda para tests / adaptadores de texto, o por si messaging se
retoma algún día.

### Fase 5 — OpenCode (B1 validado 2026-07-21, OpenCode `1.18.4`)

Formato vigente contrastado con `opencode debug config` en una instalación
real (`curl -fsSL https://opencode.ai/install | bash`).

- Provider custom Anthropic-compatible: `npm: "@ai-sdk/anthropic"`,
  `options.baseURL` = `{proxy_root}/v1` (el SDK hace `POST {baseURL}/messages`;
  FCC sirve `/v1/messages`). No usar el root sin `/v1` (eso es la convención
  de Claude Code vía `ANTHROPIC_BASE_URL`).
- API key: token del proxy (`ANTHROPIC_AUTH_TOKEN` o sentinel `fcc-no-auth`).
- MCP local stdio: `type: "local"`, `command: ["fcc-verdict", "serve-mcp"]`.
- Override de config: env `OPENCODE_CONFIG` (el launcher escribe
  `~/.fcc/opencode.json` y la exporta).
- Smoke no interactivo: `opencode run "…"`.

Snippet de referencia (el launcher genera el equivalente):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "fcc/claude-sonnet-4-5",
  "provider": {
    "fcc": {
      "npm": "@ai-sdk/anthropic",
      "name": "Free Claude Code",
      "options": {
        "baseURL": "http://127.0.0.1:8082/v1",
        "apiKey": "fcc-no-auth"
      },
      "models": {
        "claude-sonnet-4-5": {
          "name": "FCC (claude-sonnet-4-5)"
        }
      }
    }
  },
  "mcp": {
    "free-llm-verdict": {
      "type": "local",
      "command": ["fcc-verdict", "serve-mcp"],
      "enabled": true
    }
  }
}
```

```bash
fcc-server
fcc-opencode
fcc-opencode run "lista los archivos del directorio"
fcc-agent jobs enqueue "crea hello.txt con hola"
fcc-agent jobs status <job_id>
fcc-agent jobs result <job_id>
```

Cola desatendida (B6): `~/.fcc/agent_jobs/{id}.json` + worker detachado
(`fcc-agent jobs _run`). Sustituye la superficie messaging para jobs vía SSH.

### Fase 6 — v1 servidor (diseño, pendiente)

Decisiones clave (detalle de tareas C1–C7 en el backlog):

- **Fallback solo pre-commit.** La cadena `MODEL_FALLBACKS` actúa cuando el
  fallo (429/cuota/5xx persistente) ocurre **antes del primer byte SSE
  emitido** al cliente; un stream ya comenzado nunca cambia de modelo
  (semántica de commit-boundary del proxy). Mid-stream sigue la
  recuperación existente contra el mismo proveedor. Reutiliza `core/quota`
  (tracker + agotamiento diario) — misma lógica que `FallbackProxyClient`
  del harness, elevada al camino del proxy para que OpenCode/Claude
  Code/Codex la hereden sin cambios.
- **La config OpenCode la genera el launcher en cada arranque** — cualquier
  mejora (lista multi-modelo) va en `build_opencode_config_dict`, nunca en
  ediciones manuales de `~/.fcc/opencode.json`.
- **Política de modelos basada en eval, no en opinión.** Los IDs free
  cambian cada pocas semanas: C1 deja un script repetible que puntúa
  candidatos reales de `/v1/models` en tareas agénticas. `github_models/*`
  queda excluido de la cadena de chat (tope ~4k tokens/request en free).
- **Verdict fuera del per-turno.** Se sistematiza vía `AGENTS.md`
  (planificación, bugs resistentes, revisión de diseño), no en cada tool
  call.
- **Honestidad sobre "tipo Sonnet":** ningún free alcanza Sonnet 4.5
  sostenido en agentic coding; default fuerte + cadena + Verdict acorta la
  distancia, no la cierra.

