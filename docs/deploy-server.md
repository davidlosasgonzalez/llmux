# Deploy — servidor + Claude Code + SSH (v2)

Checklist para controlar un proyecto en un servidor vía Termius → SSH → tmux,
con LLMux como proxy y Claude Code como TUI diaria.

## 1. Base

1. Instalar [uv](https://docs.astral.sh/uv/) y Python 3.14:
   `uv python install 3.14.0`
2. Clonar este repo (o instalar el paquete) en el servidor.
3. `uv sync` / install script; comprobar `llmux-server --help`, `llmux-claude --help`.
4. Copiar `.env.example` → `~/.llmux/.env` y rellenar API keys.
5. Fijar política de modelos (C1):
   - `MODEL=open_router/moonshotai/kimi-k2.6` (o el ganador del último eval)
   - `MODEL_FALLBACKS=open_router/deepseek/deepseek-v3.2,cerebras/gpt-oss-120b`
6. Instalar Claude Code: `curl -fsSL https://claude.ai/install.sh | bash`

## 2. Proxy LLMux (persistente)

Usar la unit versionada `deploy/llmux-server.service`:

```bash
mkdir -p ~/.config/systemd/user ~/.llmux/logs ~/.local/bin
# Asegura que llmux-server esté en ~/.local/bin (uv tool / symlink)
cp deploy/llmux-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llmux-server.service
loginctl enable-linger "$USER"   # sobrevive logout SSH
curl -fsS http://127.0.0.1:8082/health
```

Alternativa rápida: `tmux new -s llmux` → `llmux-server`.

### 2b. Variante system-mode (root, clone en /opt)

Para un VPS donde `/opt/llmux` es un clone de
`github.com/davidlosasgonzalez/llmux` (deploy key de solo lectura) y el
servicio corre como root (verificada en producción, 2026-07-22):

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

Dos lecciones pagadas con horas de debugging — no las repitas:

- **`EnvironmentFile=` es obligatorio.** Settings no carga `~/.llmux/.env` en
  este contexto; sin él, el proxy arranca "healthy" pero responde
  `X_API_KEY is not set` a cada petición.
- **`ExecStart` directo al binario del venv, nunca `uv run`.** `uv run` como
  proceso principal deja un hijo huérfano escuchando el puerto que sobrevive
  a `systemctl restart` (síntoma: `MainPID=0` con el servicio "active" y
  fixes de config que "no surten efecto").
- El `.env` para systemd no debe llevar comillas en los valores
  (`EnvironmentFile` las pasa literales al proceso).

Actualización del servidor (tras cada push a `main`):

```bash
cd /opt/llmux
git pull --ff-only
export PATH="$HOME/.local/bin:$PATH"   # uv no está en PATH en shell no interactiva
uv sync
systemctl restart llmux-server.service
sleep 5 && curl -fsS http://127.0.0.1:8082/health
# Verificación real, nunca solo /health: una petición E2E + llmux-trace --last
```

## 3. Claude Code

```bash
cd /path/to/project
llmux-claude            # lanza Claude Code contra el proxy local
llmux-claude -p "responde solo: pong"
```

El launcher comprueba que el proxy está vivo e inyecta `ANTHROPIC_BASE_URL` y
`ANTHROPIC_AUTH_TOKEN` según la config del Admin UI. El picker nativo `/model`
de Claude Code lista los modelos que LLMux expone.

Para segundas opiniones multi-modelo, registrar el MCP de Verdict en Claude
Code (stdio):

```bash
claude mcp add llmux-verdict -- llmux-verdict serve-mcp
```

## 4. Flujo remoto (Termius)

1. SSH al servidor.
2. Trabajo: `cd proyecto && llmux-claude`.
3. Admin UI desde el portátil:
   `ssh -L 8082:127.0.0.1:8082 user@server` → abrir `http://127.0.0.1:8082/admin`
4. Cuotas por SSH:

```bash
llmux-verdict usage
```

5. Diagnóstico de una petición concreta:

```bash
llmux-trace --last
```

## 5. Ordenar fallbacks con stats del Verdict (C10)

Tras varias deliberaciones:

```bash
llmux-verdict usage --output json
# o inspeccionar ~/.llmux/verdict.db model_stats
```

Procedimiento manual:

1. Preferir modelos con alta tasa de éxito / baja latencia en `model_stats`.
2. `github_models/*` (tope ~4k tokens/request) ya no hace falta excluirlo a
   mano: el filtro de contexto lo salta solo cuando el request supera su cap
   de free tier (`daily_limit(...).max_request_tokens`). Incluirlo solo aporta
   como fallback de último recurso para requests pequeños.
3. Poner el mejor como `MODEL`, el resto (2–3) en `MODEL_FALLBACKS` separados
   por comas.
4. Si toda la cadena `MODEL`+`MODEL_FALLBACKS` tiene ventana de contexto
   parecida (p.ej. todos ~131k), fijar `MODEL_LONG_CONTEXT` a un modelo de
   ventana grande (gemini, minimax, kimi) — si no, una conversación larga de
   Claude Code puede agotar toda la cadena a la vez. `llmux-server` avisa de
   esto al arrancar si falta, y también imprime el
   `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` recomendado para el cliente según el
   techo real de la cadena.
5. Si la tabla estática de ventanas/caps se equivoca para tu cuenta concreta
   (p.ej. Cerebras con menos contexto libre del que la tabla asume), corregirlo
   con `CONTEXT_WINDOW_OVERRIDES` (`model_or_ref=tokens`) en vez de tocar
   código.
6. Contrastar con el último eval C1 (`docs/evals/…`).
7. Reiniciar `llmux-server` para aplicar la config.

## 6. Checklist v2 (reproducir de cero)

- [ ] `~/.llmux/.env` con keys + `MODEL` + `MODEL_FALLBACKS` + `MODEL_LONG_CONTEXT`
- [ ] `systemctl --user status llmux-server` active (o tmux)
- [ ] `curl /health` OK tras reboot / re-login (linger)
- [ ] `llmux-verdict usage` imprime tabla (aunque esté vacía)
- [ ] `llmux-claude -p "pong"` completa contra el proxy
- [ ] Drill fallback: forzar 429 en primario (key inválida temporal) →
      responde el secundario; logs `precommit_fallback.serving`
- [ ] Revisar en el log de arranque la línea `Client config:` con el
      `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` recomendado y aplicarlo si aplica
- [ ] Drill contexto: request con prompt oversize → responde 400
      `invalid_request_error` ("prompt is too long") si no hay
      `MODEL_LONG_CONTEXT`, o sirve desde ahí si lo hay
- [ ] `/verdict` o MCP `evaluate` en una pregunta de diseño
- [ ] `llmux-trace --last` resume el turno con el modelo servido
- [ ] Validación en `~/Documents/advisor` (local) antes del VPS
