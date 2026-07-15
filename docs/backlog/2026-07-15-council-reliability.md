# Backlog — Fiabilidad del Council (free-llm-verdict)

> Origen: evaluación Opus vs MCP del 2026-07-15 (`docs/council-eval-findings.md`).
> Objetivo: acercar el razonamiento del council a Opus. Palancas: integridad del
> bucle de crítica, verificación externa de hechos, y observabilidad real.
> Toda referencia `fichero:línea` verificada contra el código el 2026-07-15.

## Estado

- ✅ **T1 — Integridad del score de crítica** (2026-07-15). De-anclaje de los
  placeholders `0.0` en los prompts, `Critique.is_informative`, guarda
  anti-degeneración con reintento de crítico y stop `critique unavailable`,
  confianza honesta (`null` + `confidence_source`) en `compact()`.
- ✅ **T2 — Timeout por llamada + latencia en la selección** (2026-07-15).
  `call_timeout_s` (default 90 s) con `asyncio.wait_for` en `_invoke`,
  `latency_penalty` multiplicativa sustituye al bonus del 3%, penalización doble
  para roles seriales (refiner/critic) en `_rank`.
- 🧹 **Extra (compatibilidad):** corregida sintaxis `except X, Y:` (Python-2, solo
  válida en 3.14) a `except (X, Y):` en los 4 ficheros del paquete `council/`
  (`parsing`, `quota`, `redaction`). Quedan 7 instancias fuera de `council/`
  (messaging, providers, core) — anotadas como follow-up en findings §E.
- 🐛 **Bug encontrado al implementar T1:** `_pick_one` no permitía excluir por
  `key`, así que en DEEP (4 proponentes = 4 candidatos, todas las familias
  prohibidas) el reintento devolvía el mismo crítico. Añadido `exclude_keys`.
  Cazado por el test `test_degenerate_critique_retries_...`.

Pendiente: T3, T4, T5, T6, T7 (ver abajo).

## Orden de ejecución recomendado

| # | Tarea | Sev | Esfuerzo | Modelo sugerido |
|---|-------|-----|----------|-----------------|
| T1 | Integridad del score de crítica | 🔴 | M | Opus/Fable (diseño fino de prompt + guardas) |
| T2 | Timeout por llamada + penalización de latencia | 🔴 | S | Sonnet |
| T3 | Logging persistente en modo MCP | 🟠 | S | Sonnet |
| T4 | Tiempos en report y respuesta compacta | 🟡 | XS | Sonnet |
| T5 | Fase de investigación web (research) | 🔴 | L | Opus/Fable |
| T6 | Desacuerdos factuales → evidencia, nunca mayoría | 🔴 | M | Opus/Fable (depende de T5) |
| T7 | Disciplina de citas: solo URLs realmente leídas | 🟠 | S | Sonnet (depende de T5) |

T1+T2 primero: arreglan el caso de uso actual (lento + confidence 0) sin añadir
nada nuevo. T5–T7 son el salto de calidad hacia Opus.

---

## T1 ✅ Integridad del score de crítica (raíz de `confidence: 0` y de las 3 rondas siempre)

### Evidencia (2 runs reales, 6/6 critiques degeneradas)
Reports `council-1784114111` y `council-1784115102`: todas las rondas tienen
`critique = {verdict: "revise", score: 0.0, critical_issues: [], material_issues: []}`
con crítico real asignado (p. ej. `open_router/qwen/qwen3-next-80b`). Un crítico
que no encuentra NINGÚN defecto pero puntúa 0.0 y pide revisar es incoherente.

### Cadena causal (verificada en código)
1. `prompts.py:175` — el schema de ejemplo del critique dice literalmente
   `"score": 0.0`. Los modelos free copian el valor del ejemplo (anclaje).
   Mismo patrón en `prompts.py:39` (propose, `"confidence": 0.0`).
2. `parsing.py:95-100` — `_confidence()` devuelve 0.0 tanto si el campo falta
   como si es inválido: "no contestó" y "puntuó 0" son indistinguibles.
3. `orchestration.py:512-518` — `_is_acceptable()` exige `score >= quality_threshold`
   (0.85): con score 0.0 nunca se cumple → **siempre se agotan las rondas**
   (lentitud + quema de cuota free ×3).
4. `orchestration.py:186-193` — con score constante 0.0, `improved` solo es true
   en la ronda 0 (`0.0 - (-1.0) >= epsilon`); rondas 1-2 → `stale_rounds = 2` →
   stop "two rounds without material improvement". Firma exacta de ambos runs.
5. `models.py:244` — `compact()['confidence'] = critique.score` → 0 hacia el usuario.

### Fix (4 cambios pequeños, mismo commit)
1. **Prompt sin anclaje** (`prompts.py:168-177`): sustituir el valor de ejemplo
   por un placeholder no copiable e instrucción explícita:
   ```
   "score": "<número 0.0-1.0 — tu evaluación honesta; 1.0 = impecable.
              NO copies este texto: emite un número>"
   ```
   Añadir al system: "verdict y score deben ser coherentes: pass ⇒ score alto;
   revise/reject ⇒ lista al menos un issue concreto que lo justifique."
   Aplicar el mismo criterio al `"confidence": 0.0` de `prompts.py:39`.
2. **Distinguir ausente de cero** (`parsing.py`): `_confidence()` → nueva
   variante `_score_or_none()` que devuelve `None` si falta/no numérico.
   `parse_critique()` con score `None` devuelve `None` (= crítica inválida),
   activando el fallback existente de `orchestration.py:322-323`.
3. **Guarda anti-degeneración** (`orchestration.py`, tras `parse_critique`):
   una critique con `verdict != pass`, `score == 0.0` y cero issues es
   degenerada → reintentar UNA vez con otro crítico (`_pick_one` excluyendo al
   anterior); si también degenera, romper el bucle con
   `stop_reason = "critique unavailable"` en vez de quemar más rondas.
4. **Confianza honesta** (`models.py:233-255`): si la última critique es
   fallback/degenerada, `compact()['confidence']` debe ser `null` (no 0.0) y
   añadir `"confidence_source": "critic" | "unavailable"`. Un 0 inventado es
   desinformación para el consumidor (Claude Code decide con ese número).

### Anti-humo
- Test: critique JSON sin campo `score` → `parse_critique` devuelve `None`.
- Test: critique `{"verdict":"revise","score":0.0}` sin issues → se reintenta con
  otro crítico y, si repite, `stop_reason == "critique unavailable"`.
- Test de regresión del bucle: con crítico sano (score 0.9, pass) el run para en
  la ronda 0 con `stop_reason == "quality threshold met"`.
- Métrica de éxito: en runs reales, `confidence` deja de ser 0.0 constante y las
  ejecuciones dejan de consumir siempre `max_rounds`.

---

## T2 ✅ Timeout por llamada + latencia en la selección (raíz de los runs de 10 min)

### Evidencia
`council.db › model_stats`: `nvidia_nim/deepseek-ai/deepseek-v4-flash` media
**158.9 s/req** (vs 1.2-1.9 s de cerebras) y actuó de **sintetizador** → 3
síntesis ≈ 8 min él solo. `grep timeout invoker.py provider_invoker.py` → no
existe ningún timeout por llamada.

### Fix
1. **Timeout duro por invocación** (`orchestration.py:329-368 _invoke`):
   envolver `self._invoker.invoke(...)` en `asyncio.wait_for(..., timeout=self._config.call_timeout_s)`.
   Nuevo campo en `CouncilConfig` (`config.py`): `call_timeout_s` (default 90;
   override por env/`council.yaml`). `TimeoutError` → `classify_failure` como
   fallo de disponibilidad → cuenta en `failures` y dispara el circuito de
   `QuotaTracker` como cualquier otro error.
2. **La latencia debe PENALIZAR, no solo bonificar** (`scoring.py:42-48`):
   `_speed_bonus` cap 0.03 es irrelevante (un modelo 100× más lento pierde un 3%).
   Añadir multiplicador: `quality *= max(0.35, 1.0 - max(0.0, avg_latency - 20.0) / 120.0)`
   → 20 s: ×1.0 · 60 s: ×0.67 · ≥98 s: ×0.35. Deepseek (159 s) cae al suelo 0.35
   y deja de ganar selecciones a modelos rápidos de calidad similar.
3. **Rol sintetizador intolerante a latencia** (`orchestration.py:502-510 _rank`):
   el sintetizador se invoca hasta `max_rounds` veces en serie — para
   `role in ("refiner", "critic")` aplicar la penalización de latencia al
   cuadrado (o excluir modelos con `avg_latency > call_timeout_s / 2`).

### Anti-humo
- Test: invoker fake que tarda `timeout+1` → `InvocationResult.failure`, el run
  continúa con los demás modelos (no cuelga).
- Test de scoring: dos modelos con prior idéntico, latencias 2 s vs 150 s → el
  lento queda por debajo para rol refiner.
- Métrica: p95 de duración total de `evaluate` en depth=deep < 3 min.

---

## T3 🟠 Logging persistente en modo MCP

### Evidencia
`config/logging_config.py:configure_logging()` existe (sink JSON loguru,
rotación 50 MB) pero `mcp_server.py` **no lo llama nunca** (grep sin resultados).
Los `logger.warning` de `orchestration.py:356-361` se pierden. No existe
`~/.fcc/logs/`. Único rastro de un fallo: contador `failures++` sin causa.

### Fix
1. En el arranque del MCP (`mcp_server.py`): `configure_logging(fcc_dir() / "logs" / "council-mcp.log")`.
2. **Quitar el truncado** para este uso: `logging_config.py` hace
   `log_path.write_text("")` en cada arranque — cada reinicio del MCP borraría
   el historial. Parametrizar: `configure_logging(..., truncate=False)`.
3. Enriquecer el warning de `orchestration.py:356` a nivel ERROR con `detail=str(exc)`
   truncado a 500 chars, y añadir log INFO por fase con `elapsed` (propose/review/
   synthesis/critique, modelo y latencia) — es lo que permitirá diagnosticar el
   próximo "run de 10 min" sin reproducirlo.

### Anti-humo
- Arrancar el MCP, lanzar un `evaluate`, matar un proveedor (API key inválida en
  env) → `~/.fcc/logs/council-mcp.log` contiene línea ERROR con modelo, fase y causa.
- Reiniciar el MCP → el log anterior sigue ahí (append, no truncate).

---

## T4 🟡 Tiempos en report y respuesta compacta

### Evidencia
`CouncilResult` ya tiene `started_at`/`finished_at` (`models.py:222-223`) pero ni
`_full_report()` (`service.py:330`) ni `compact()` (`models.py:233`) los serializan.
Imposible saber cuánto tardó un run sin cronometrarlo desde fuera.

### Fix
1. `compact()`: añadir `"elapsed_s": round(finished_at - started_at, 1)` (null si
   `finished_at` es None).
2. `_full_report()`: añadir `started_at`, `finished_at` (ISO-8601) y `elapsed_s`.
3. Por ronda: `Round` gana `elapsed_s` (medido en el bucle de `run()`), serializado
   en `_full_report`. Con T2 esto delata al modelo lento en un vistazo.

### Anti-humo
- Test: `compact()` incluye `elapsed_s` numérico tras un run fake.
- Los reports nuevos en `~/.fcc/council_reports/` llevan los 3 campos.

---

## T5 🔴 Fase de investigación web (la palanca principal hacia Opus)

### Evidencia (prueba B, findings §B1)
Prompt sobre límites de CPU de Cloudflare Workers: el council respondió con los
límites de ~2023 (50 ms default / 30 s máx) como si fueran actuales. Oficial
vigente: 30 s default / 5 min máx. Ningún modelo del council navega; Opus acertó
4/4 únicamente porque buscó y leyó la doc antes de afirmar.

### Diseño
Nuevo módulo `council/research.py` + fase opcional previa a propose ("Phase 2.5"):

1. **Detección de necesidad** (sin LLM, barato): heurística regex sobre el prompt
   — términos de versión/actualidad (`versión|version|latest|current|vigente|202[4-9]`),
   precios/límites (`precio|pricing|límite|limit|quota|plan`), doc oficial
   (`documentación oficial|docs|changelog|release`). Si matchea → research ON.
   Además: parámetro explícito `research: bool | "auto"` en el tool `evaluate`
   (default `"auto"`).
2. **Búsqueda sin coste y sin API key** como base: DuckDuckGo HTML
   (`html.duckduckgo.com/html/?q=...`, parseo de resultados, sin JS). Backends
   opcionales por API key en `.env`: Brave Search API (2.000 queries/mes free),
   Google CSE. Interfaz común `SearchBackend.search(query) -> list[SearchHit]`.
3. **Fetch + extracción**: descargar top 3-5 URLs (httpx ya es dependencia),
   convertir a texto (strip HTML), truncar por presupuesto (~2K tokens/fuente,
   cap global ~6K — recordar que Cerebras free capa ~8K contexto; si research ON,
   excluir modelos de contexto corto o recortar más).
4. **Inyección**: el resultado entra por el parámetro `context` que
   `Orchestrator.run()` **ya acepta** (`orchestration.py:87`) y que
   `propose_prompt` ya sabe renderizar — no hay que tocar el motor. Formato:
   ```
   FUENTES VERIFICADAS (fetched 2026-07-15):
   [S1] <url> — <extracto>
   [S2] ...
   Instrucción: para hechos de versión/límites/precios, las FUENTES mandan
   sobre tu memoria de entrenamiento.
   ```
5. **Registro**: el report gana `"research": {"queries": [...], "sources_fetched": [urls], "backend": "ddg"}`.

### Decisión de alcance
El fetch lo hace el proceso MCP local (Python), NO los modelos free — ninguno
tiene tools. Es determinista, testeable y no depende del proveedor.

### Anti-humo
- Repetir la prueba B (Cloudflare CPU limits) → el council responde 30 s / 5 min
  / `cpu_ms` / 10 ms (4/4) citando `[S1]`.
- Prompt sin señal de actualidad ("explica IEEE 754") → research no se dispara
  (0 requests HTTP).
- Sin red (offline) → `evaluate` degrada con aviso en `uncertainties`
  ("research no disponible"), nunca revienta.

---

## T6 🔴 Desacuerdos factuales se resuelven con evidencia, nunca por mayoría

### Evidencia (prueba B, findings §B2 — el hallazgo más grave)
En el run de Cloudflare, la Proposal C dio el default correcto (30 s). El council
la descartó "por contradecir la documentación oficial" (que nadie había leído) y
consolidó el dato obsoleto compartido por la mayoría. **La deliberación suprimió
al único modelo que acertaba.** Sin verdad-terreno, mayoría = consenso en el error.

### Fix (depende de T5)
1. **Prompt de síntesis** (`prompts.py:synthesis_prompt`): añadir regla dura:
   "Si existe desacuerdo material sobre un hecho verificable (versión, límite,
   precio, fecha, API), NO lo resuelvas por mayoría ni por 'la documentación
   dice' salvo que haya FUENTES VERIFICADAS en el contexto. Sin evidencia,
   decláralo en material_disagreements y refleja ambos valores en la respuesta
   marcando cuál carece de verificación."
2. **Escalada automática** (`service.py`, tras la ronda 0): si
   `synthesis.material_disagreements` no está vacío Y el run no llevó research →
   ejecutar research dirigido (queries construidas a partir del texto de cada
   desacuerdo), inyectar las fuentes y relanzar UNA ronda de síntesis con el
   contexto ampliado. Coste acotado: 1 síntesis extra solo cuando hay conflicto.
3. **Critique con lupa factual** (`prompts.py:critique_prompt`): añadir a la
   lista de defectos que buscar: "afirmaciones sobre versiones/límites/precios
   sin fuente verificada en el contexto".

### Anti-humo
- Reproducir la prueba B sin research inicial: el desacuerdo sobre "30 s vs 50 ms"
  debe disparar la escalada y la respuesta final debe dar 30 s con cita.
- Caso sin evidencia disponible (offline): la respuesta presenta ambos valores
  como no resueltos en vez de elegir por mayoría.

---

## T7 🟠 Disciplina de citas: solo URLs realmente leídas

### Evidencia (findings §B3)
El council citó `developers.cloudflare.com/workers/platform/limits/` (URL real,
correcta) avalando datos incorrectos. La cita de memoria fabrica autoridad.

### Fix (depende de T5)
1. **Contrato en prompts** (propose + synthesis): "Solo puedes citar URLs listadas
   en FUENTES VERIFICADAS. Cualquier otra URL debe ir marcada como
   `(URL recordada, no verificada en esta ejecución)`."
2. **Post-proceso determinista** (`service.py`, antes de `compact()`): regex de
   URLs sobre `final_answer` y `recommended_action`; toda URL ∉
   `research.sources_fetched` se reescribe añadiendo el marcador. Sin research,
   todas las URLs se marcan. No se confía en que el modelo obedezca: se verifica.

### Anti-humo
- Test: respuesta con URL no fetcheada → sale marcada.
- Test: URL presente en sources_fetched → sale limpia.

---

## Fuera de alcance (anotado, no urgente)

- `_full_report` no serializa `minor_issues`/`missing_evidence` de las critiques
  ni `evidence`/`unknowns` de proposals — añadir cuando se toque T4.
- `usage_log` no registra el día en que un modelo agotó cuota (solo tokens);
  útil para el selector pero de bajo impacto ahora.
- Evaluar sustituir el stop por `stale_rounds` (dependiente del score del
  crítico) por detección de convergencia textual entre síntesis consecutivas —
  solo si tras T1 se siguen viendo rondas inútiles.
