# Backlog — Fiabilidad del Verdict (free-llmux)

> **Histórico / cerrado.** Origen: evaluación Opus vs MCP del 2026-07-15
> (`docs/verdict-eval-findings.md`). T1–T7 y follow-ups están hechos; no hay
> pendientes accionables aquí. Detalle vivo en `docs/verdict.md` y el git log.

## Hecho — backends de research

- **Brave Search API** (`BraveSearchBackend`): se activa con `BRAVE_SEARCH_API_KEY`
  en `~/.llmux/.env`; si falta, cae a DuckDuckGo HTML (sin key).
- **Google CSE descartado**: Programmable Search ya no permite activar
  “Buscar en toda la Web” en motores nuevos, así que no sirve como buscador
  general. No se implementa.

## Hecho — verificación en vivo (2026-07-21)

### Logging MCP + proveedor inválido

- Con logging apuntando a `~/.llmux/logs/verdict-mcp.log` (mismo sink que
  `llmux-verdict serve-mcp`) y `GROQ_API_KEY` inválida, un invoke real escribió:
  - `ERROR: GROQ_ERROR: ... AuthenticationError http_status=401`
  - `WARNING: verdict.provider.error model=groq/... Invalid API Key`

### Prueba B — Cloudflare Workers CPU limits

- `llmux-verdict evaluate --depth quick --research on` con Brave.
- `research.backend=brave`; fuentes incluyen
  `https://developers.cloudflare.com/workers/platform/limits/`.
- Respuesta compacta (`answer`) con citas `[S#]`:
  - Free **10 ms** ✅
  - Máx Paid **5 min** ✅
  - Clave **`cpu_ms` / `limits.cpu_ms`** ✅
  - Default Paid **30 s** citado vía docs de Workflows (la tabla Workers en el
    HTML estático no separa default vs máx con claridad) — mejora frente al
    fallo histórico (50 ms / 30 s de memoria).
- **Nota (2026-07-21, re-verificación):** esta corrida no era reproducible en
  un shell sin `BRAVE_SEARCH_API_KEY` exportada al proceso — ver
  "Hecho — Brave no se activaba en ejecución real" más abajo para la causa
  raíz y el fix.

## Hecho — Brave no se activaba en ejecución real (2026-07-21, re-verificación)

Al reproducir la Prueba B en un shell limpio (sin `BRAVE_SEARCH_API_KEY`
exportada, solo presente en `~/.llmux/.env`), el research cayó a `ddg` y
devolvió 0 fuentes — el mismo fallo B1 que este backlog daba por resuelto.

- **Causa raíz:** `Settings` (config/settings.py) no tenía un campo
  `brave_search_api_key`; `VerdictService._research_service()` llamaba a
  `build_research_service()` sin `brave_api_key`, así que
  `resolve_search_backend()` dependía de `os.getenv("BRAVE_SEARCH_API_KEY")`
  directo — la única credencial del proyecto que no pasaba por el pipeline de
  `Settings`/dotenv que carga `~/.llmux/.env`. La verificación previa del
  2026-07-21 debió correr en un shell donde la key sí estaba exportada al
  proceso, no solo en el `.env` gestionado.
- **Fix:** añadido `Settings.brave_search_api_key` (mismo patrón que
  `groq_api_key`, etc.) y cableado explícito en `_research_service()`.
  Test de regresión:
  `test_research_service_uses_brave_key_from_settings_not_process_env`.

## Hecho — fallo de sintetizador abortaba toda la deliberación (2026-07-21)

Al volver a probar con Brave ya activo, dos ejecuciones consecutivas del
mismo prompt fallaron por completo (sin report, sin respuesta) por un
**fallo del sintetizador**, no de research:

- Intento 1: `nvidia_nim/deepseek-ai/deepseek-v4-pro` superó el timeout de 90s.
- Intento 2: `github_models/deepseek/deepseek-r1-0528` devolvió HTTP 413
  (tope de 4000 tokens de la capa gratuita de GitHub Models — el contexto
  con fuentes de research lo supera). `_fit_context_window` no lo detecta
  porque filtra por `context_length` del modelo, no por el tope artificial
  de la capa gratuita del proveedor.

`_synthesise()` no tenía fallback: cualquier fallo del sintetizador
(timeout, 413, lo que sea) lanzaba `DeliberationFailedError` y tiraba toda
la corrida, descartando propuestas y reviews ya pagadas — la misma
asimetría que T1 ya había corregido para el crítico (`_critique()` degrada
en vez de lanzar) pero que nunca se replicó en la fase de síntesis.

- **Fix reactivo:** `_synthesise_with_fallback()` en `orchestration.py` —
  reintenta con un sintetizador alternativo (excluyendo el que falló),
  acotado a `min(3, len(candidates))` intentos, antes de propagar el error
  si no queda ningún modelo disponible. Usado tanto en `run()` como en
  `resynthesise_with_context()`.
  Tests: `test_synthesiser_failure_falls_back_to_alternate_and_completes`,
  `test_synthesiser_failure_raises_when_every_model_fails`.
- **Fix proactivo (mismo día):** `provider_limits.DailyLimit` gana un campo
  `max_request_tokens` (4000 para `github_models`, el valor observado en el
  413) y `_fit_context_window` (service.py) ahora descarta candidatos por
  ese tope de proveedor además del `context_length` del modelo — antes solo
  miraba la ventana real del modelo, que para deepseek-r1-0528 es mucho
  mayor que lo que la capa gratuita realmente admite por request. Se evita
  el 413 en vez de solo tolerarlo vía el fallback reactivo.
  Test: `test_fit_context_window_drops_provider_request_cap_despite_big_window`.
  (Se descartó deliberadamente comprimir el prompt con otro modelo gratuito
  para encajarlo en 4000 tokens: añade una llamada extra con su propio modo
  de fallo, y arriesga perder precisión justo en el bloque de fuentes
  verificadas que research.py existe para proteger. Con 6+ proveedores
  gratuitos sin ese tope, no compensa.)

**Reproducción final (2026-07-21, con los tres fixes):** mismo prompt de
límites de Cloudflare Workers → `backend=brave`, 3 fuentes oficiales
fetched (`developers.cloudflare.com/{workers,queues,workflows}/...`),
confidence 0.95, "quality threshold met", valores correctos (Free 10 ms,
máximo 5 min / 300000 ms, clave `cpu_ms`).

## Pendiente

Ninguno en este backlog. Si aparece ruido de extracción HTML en SPAs de docs
(Cloudflare), sería un follow-up de research fetch, no de fiabilidad T1–T7.

## Nota de versionado

`pyproject.toml` en `4.7.0` (+ `uv lock`) cubre el backend Brave +
`BRAVE_SEARCH_API_KEY` en `.env.example`. Al mergear a `main`, ese bump viaja
en el mismo commit.
