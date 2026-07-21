# Deploy — servidor + OpenCode + SSH

Checklist para controlar un proyecto en un servidor vía Termius → SSH → tmux,
con FCC como proxy y OpenCode como TUI diaria.

## 1. Base

1. Instalar [uv](https://docs.astral.sh/uv/) y Python 3.14:
   `uv python install 3.14.0`
2. Clonar este repo (o instalar el paquete) en el servidor.
3. `uv sync` (o el install script del proyecto) y comprobar:
   `fcc-server --help`, `fcc-agent --version`, `fcc-opencode` (tras instalar
   OpenCode).
4. Copiar `.env.example` → `~/.fcc/.env` y rellenar API keys + `MODEL`.

## 2. Proxy FCC

Opción A — tmux (rápido):

```bash
tmux new -s fcc
fcc-server
# Detach: Ctrl-b d
```

Opción B — systemd user unit (esqueleto):

```ini
[Unit]
Description=Free Claude Code proxy
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/fcc-server
Restart=on-failure
WorkingDirectory=%h/projects/your-repo

[Install]
WantedBy=default.target
```

Comprobar: `curl -fsS http://127.0.0.1:8082/health` (ajusta puerto/`PORT`).

## 3. OpenCode

```bash
curl -fsSL https://opencode.ai/install | bash
# o: brew install opencode
fcc-opencode          # TUI; genera ~/.fcc/opencode.json y apunta al proxy
fcc-opencode run "…"  # no interactivo (smoke / scripts)
```

El launcher registra el MCP `free-llm-verdict` (`fcc-council serve-mcp`).

## 4. Flujo remoto

1. Termius → SSH al servidor.
2. `tmux attach -t fcc` (proxy) o una sesión de trabajo del repo.
3. En el directorio del proyecto: `fcc-opencode`.
4. Jobs desatendidos sin TUI:

```bash
JOB=$(fcc-agent jobs enqueue "refactor X y corre tests")
fcc-agent jobs status "$JOB"
fcc-agent jobs result "$JOB"
```

(`enqueue` auto-aprueba tools por defecto; usa `--no-auto-approve` solo si
el worker no está detachado.)

## 5. Verificación

- [ ] `fcc-server` responde en `/health`
- [ ] `fcc-opencode models fcc` lista el modelo de `MODEL`
- [ ] `fcc-opencode run "responde solo: pong"` completa vía proxy
- [ ] `fcc-agent jobs enqueue|status|result` redondea un job trivial
