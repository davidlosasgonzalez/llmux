# Deploy — servidor + OpenCode + SSH (v1)

Checklist para controlar un proyecto en un servidor vía Termius → SSH → tmux,
con FCC como proxy y OpenCode como TUI diaria.

## 1. Base

1. Instalar [uv](https://docs.astral.sh/uv/) y Python 3.14:
   `uv python install 3.14.0`
2. Clonar este repo (o instalar el paquete) en el servidor.
3. `uv sync` / install script; comprobar `fcc-server --help`, `fcc-agent --version`.
4. Copiar `.env.example` → `~/.fcc/.env` y rellenar API keys.
5. Fijar política de modelos (C1):
   - `MODEL=open_router/moonshotai/kimi-k2.5` (o el ganador del último eval)
   - `MODEL_FALLBACKS=open_router/deepseek/deepseek-v3.2,cerebras/gpt-oss-120b`
6. Instalar OpenCode: `curl -fsSL https://opencode.ai/install | bash`
7. (Opcional) Copiar `docs/opencode-agents.template.md` → `AGENTS.md` del
   proyecto o `~/.config/opencode/AGENTS.md`.

## 2. Proxy FCC (persistente)

Usar la unit versionada `deploy/fcc-server.service`:

```bash
mkdir -p ~/.config/systemd/user ~/.fcc/logs ~/.local/bin
# Asegura que fcc-server esté en ~/.local/bin (uv tool / symlink)
cp deploy/fcc-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fcc-server.service
loginctl enable-linger "$USER"   # sobrevive logout SSH
curl -fsS http://127.0.0.1:8082/health
```

Alternativa rápida: `tmux new -s fcc` → `fcc-server`.

## 3. OpenCode

```bash
cd /path/to/project
fcc-opencode          # escribe ~/.fcc/opencode.json (modelos + MCP + /council)
fcc-opencode run --dir . --auto "responde solo: pong"
```

El launcher incluye:

- modelos `MODEL` + `MODEL_FALLBACKS` para cambio manual en la TUI
- MCP `free-llm-verdict`
- comando `/council`
- subagente `@second-opinion` (otro modelo de la cadena)

## 4. Flujo remoto (Termius)

1. SSH al servidor.
2. Trabajo: `cd proyecto && fcc-opencode`.
3. Admin UI desde el portátil:
   `ssh -L 8082:127.0.0.1:8082 user@server` → abrir `http://127.0.0.1:8082/admin`
4. Cuotas por SSH:

```bash
fcc-council usage
```

5. Jobs desatendidos:

```bash
JOB=$(fcc-agent jobs enqueue "refactor X y corre tests")
fcc-agent jobs status "$JOB"
fcc-agent jobs result "$JOB"
```

## 5. Ordenar fallbacks con stats del Council (C10)

Tras varias deliberaciones:

```bash
fcc-council usage --output json
# o inspeccionar ~/.fcc/council.db model_stats
```

Procedimiento manual:

1. Preferir modelos con alta tasa de éxito / baja latencia en `model_stats`.
2. Excluir `github_models/*` (tope ~4k tokens/request).
3. Poner el mejor como `MODEL`, el resto (2–3) en `MODEL_FALLBACKS` separados
   por comas.
4. Contrastar con el último eval C1 (`docs/evals/…` o
   `uv run python smoke/scripts/eval_opencode_models.py`).
5. Reiniciar `fcc-server` / relanzar `fcc-opencode` para regenerar config.

## 6. Checklist v1 (reproducir de cero)

- [ ] `~/.fcc/.env` con keys + `MODEL` + `MODEL_FALLBACKS`
- [ ] `systemctl --user status fcc-server` active (o tmux)
- [ ] `curl /health` OK tras reboot / re-login (linger)
- [ ] `fcc-council usage` imprime tabla (aunque esté vacía)
- [ ] `fcc-opencode models fcc` lista default + fallbacks
- [ ] `fcc-opencode run --dir . --auto "pong"` completa
- [ ] Drill fallback: forzar 429 en primario (key inválida temporal) →
      responde el secundario; logs `precommit_fallback.serving`
- [ ] `/council` o MCP `evaluate` en una pregunta de diseño
- [ ] `@second-opinion` produce crítica con otro modelo
- [ ] `fcc-agent jobs enqueue|status|result` en un job trivial
- [ ] Validación en `~/Documents/advisor` (local) antes del VPS
