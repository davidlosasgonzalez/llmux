# Council (free-llm-verdict) — Registro de fallos y mejoras

> Documento vivo. Objetivo: acercar el razonamiento del council a Opus.
> Metodología: mismo prompt a Opus (con web) y al MCP; anotar cada fallo del MCP.
> Fecha de inicio: 2026-07-15

## Leyenda de severidad
- 🔴 crítico — produce respuesta incorrecta o rompe la ejecución
- 🟠 alto — degrada calidad o fiabilidad de forma notable
- 🟡 medio — mejora de robustez / observabilidad
- 🔵 bajo — pulido

---

## A. Observabilidad / logging

### A1 🟡 No hay tiempo de pared por ejecución
- **Síntoma:** un run tardó >10 min; el report JSON no permite saberlo.
- **Detalle:** `council_reports/*.json` no tiene `started_at`/`ended_at`/`elapsed_s`.
- **Fix propuesto:** añadir timestamps de inicio/fin y `elapsed_s` por ronda y total al report.

### A2 🟠 No hay log de errores/excepciones persistente
- **Síntoma:** si un proveedor cuelga o lanza, solo se ve un contador `failures++` en `model_stats`.
- **Detalle:** no existe `~/.fcc/logs/`. No se guarda traza, causa, ni qué modelo/timeout falló.
- **Fix propuesto:** logger a fichero (`~/.fcc/logs/council-YYYYMMDD.log`) con nivel, modelo, fase, excepción y duración. Rotación diaria.

### A3 🟠→✅ Modelo lento sin guardia (raíz de los 10 min) — RESUELTO (T2, 2026-07-15)
- **Evidencia (council.db, model_stats):**
  - `cerebras/gpt-oss-120b` ≈ 1.2–1.9 s/req
  - `groq/llama-3.1-8b-instant` ≈ 7.1 s/req
  - `nvidia_nim/deepseek-ai/deepseek-v4-flash` ≈ **158.9 s/req** ← y actúa de sintetizador
- **Fix propuesto:** (a) timeout por llamada configurable; (b) penalizar latencia en el selector; (c) no usar modelos con latencia media alta como sintetizador salvo fallback.
- **Confirmado en código (2026-07-15):** no existe NINGÚN timeout por llamada
  (`invoker.py`/`provider_invoker.py`); `_speed_bonus` (`scoring.py:42-48`) capa el
  efecto de la velocidad en un 3% — un modelo 100× más lento apenas pierde puntos.
  Fix: ver T2 del backlog.

### A4 🔴→✅ `confidence: 0` — CAUSA RAÍZ CONFIRMADA y RESUELTA (T1, 2026-07-15)
- **Cadena:** el schema de ejemplo del critique (`prompts.py:175`) ancla `"score": 0.0`
  → los críticos copian el 0 → `_is_acceptable` nunca se cumple → **todas las
  ejecuciones queman max_rounds** → `compact()['confidence'] = critique.score = 0`.
- **Evidencia:** 6/6 critiques de los 2 runs = `{revise, score 0.0, 0 issues}` (incoherente).
- **Impacto real:** no es cosmético — triplica la duración y el gasto de cuota de cada run.
- **Fix:** ver T1 en `docs/backlog/2026-07-15-council-reliability.md`.

---

## B. Acceso a web / documentación oficial

### Prueba B — Cloudflare Workers CPU limits (2026-07-15)
Prompt: límites de CPU-time de un Worker (default/máx Paid, config, Free). Verdad oficial
confirmada en developers.cloudflare.com/workers/platform/limits/:
default Paid **30 s**, máx **5 min (300.000 ms)**, clave `cpu_ms`, Free 10 ms.

| dato | Oficial | MCP |
|---|---|---|
| Default Paid | 30 s | 50 ms ❌ |
| Máx Paid | 5 min | 30 s ❌ |
| Clave config | `cpu_ms` | `cpu_ms` ✅ |
| Free | 10 ms | 10 ms ✅ |

### B1 🔴 Sin acceso a web → datos obsoletos presentados con seguridad
- **Síntoma:** falló los 2 datos sensibles a versión (los 50 ms / 30 s son límites de Cloudflare de ~2023).
- **Causa raíz:** ningún modelo del council navega; responde de memoria (corte de entrenamiento).
- **Fix propuesto:** dar al council una herramienta de búsqueda/fetch (DuckDuckGo/Brave/Google + fetch de URL) inyectada como contexto ANTES de deliberar, al menos para task_type que huelan a "doc oficial / versión / precio / release".

### B2 🔴 La deliberación SUPRIME al modelo minoritario correcto
- **Síntoma (grave):** `material_disagreements` registró que *"Proposal C afirma que el default es 30 s"* — **Proposal C tenía razón en el default** — y el council lo descartó "por contradecir la documentación oficial".
- **Implicación:** sin verdad-terreno, el voto por mayoría afianza el conocimiento obsoleto **compartido** y anula al único modelo que acertó. La deliberación empeoró el resultado en vez de mejorarlo.
- **Fix propuesto:** cuando haya desacuerdo material en un dato factual/versión, en lugar de resolver por mayoría, **disparar una verificación externa** (búsqueda web) y dejar que la evidencia decida. Nunca "corregir" a un modelo contra la mayoría en hechos verificables sin fuente.

### B3 🟠 Cita URLs oficiales que NO ha leído (falsa confianza)
- **Síntoma:** citó `.../workers/platform/limits/` (URL real y correcta) pero con contenido incorrecto.
- **Implicación:** la cita da falsa sensación de fundamento; el usuario podría fiarse.
- **Fix propuesto:** solo permitir citar una URL si se ha hecho fetch real de ella en esta ejecución; si no, marcarla como "URL recordada, no verificada".

### B4 🟡 `confidence: 0` de nuevo
- Segunda ejecución seguida con `confidence: 0`. Refuerza A4: el cálculo de confianza parece siempre 0. Prioridad media→alta para depurar.

---

## C. Razonamiento / calidad de respuesta

### C0 ✅ Baseline (no es fallo) — IEEE 754 / punto flotante
- Prompt de razonamiento puro; el council acertó lo esencial **incluido** el matiz de los 53 bits.
- La deliberación corrigió a los modelos que omitían el matiz (registrado en `material_disagreements`).
- Conclusión: en razonamiento cerrado sin web, el council rinde bien.

---

## D. Rendimiento / coste
*(pendiente)*

---

## E. Compatibilidad / portabilidad

### E1 🧹 Sintaxis `except X, Y:` (Python 2) por todo el repo
- **Hallazgo (2026-07-15):** `parsing.py` no compilaba en Python 3.11-3.13
  (`SyntaxError: multiple exception types must be parenthesized`). Solo funciona
  porque el proyecto está clavado a 3.14 (`.python-version`), que la acepta con
  semántica de tupla (verificado empíricamente: captura ambos tipos).
- **Resuelto en `council/`:** `parsing.py` (2), `quota.py` (1), `redaction.py` (1).
- **Follow-up (fuera de `council/`, 7 instancias):**
  - `core/anthropic/tokens.py:104`
  - `providers/openai_chat/provider.py:448` (`except asyncio.CancelledError, GeneratorExit:`)
  - `messaging/voice.py:310` (`except asyncio.CancelledError, Exception:` — además, capturar `CancelledError` en un `except` amplio es sospechoso; revisar aparte)
  - `messaging/rendering/telegram_markdown.py:179,186`
  - `messaging/rendering/discord_markdown.py:171,178`
- **Recomendación:** un barrido `except (X, Y):` en todo el repo + regla ruff
  (E722/pyupgrade) para que no reaparezca. No incluido en el commit de T1/T2 por
  acotar el radio a módulos verificados.
