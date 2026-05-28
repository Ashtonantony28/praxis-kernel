# Deploying Praxis

## Overview

Praxis supports two production deployment paths:

- **Docker Compose** (recommended for MCP gateway use and multi-service setups)
- **systemd** (native Linux service for background queue processing on a dedicated host)

Both paths keep credentials outside the repo in a file you edit locally and never commit.

---

## Prerequisites

- Linux host (Ubuntu 22.04+ recommended for systemd path; any Docker host for Compose path)
- Python 3.10+ (only needed for systemd path — Docker bundles its own Python)
- Docker Engine 24+ and Docker Compose v2 (for Docker path)
- A Praxis auth credential: `CLAUDE_CODE_OAUTH_TOKEN` (preferred, flat-cost subscription) or `ANTHROPIC_API_KEY` (pay-per-token fallback)

---

## Path 1 — Docker Compose

### Quick start

```bash
# 1. Copy the env template and fill in credentials
cp .env.example .env
nano .env   # set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY

# 2. Start both services in the background
docker compose up -d

# 3. Connect an MCP client
#    URL: http://localhost:8765/sse
```

### Services

| Service | Command | Port | Purpose |
|---------|---------|------|---------|
| `mcp` | `python -m praxis --mcp` | 8765 | MCP HTTP/SSE gateway — connects to Claude Desktop and other MCP clients |
| `daemon` | `python -m praxis --queue` | — | Queue processor — picks up tasks from `.praxis/queue/tasks.jsonl` and runs them through the orchestrator |

Both services share a named Docker volume (`workspace`) so they operate on the same queue, wiki, and staging state.

Note: the `daemon` service uses `--queue` (foreground loop) rather than `--daemon` (which forks to background). Docker requires the process to stay in the foreground as PID 1.

### Passing credentials

Credentials are read from `.env` via `env_file: .env` in `docker-compose.yml`. The `.env` file is bind-mounted at container start — never baked into the image.

**Important: never commit `.env`.** It is listed in `.gitignore` by convention. Use `.env.example` (which contains only comments and placeholder values) as the version-controlled template.

### Checking logs

```bash
# Follow logs for the MCP gateway
docker compose logs -f mcp

# Follow logs for the queue processor
docker compose logs -f daemon

# Both at once
docker compose logs -f
```

### Updating

```bash
# Pull latest source
git pull

# Rebuild the image and restart both services
docker compose build
docker compose up -d
```

### Connecting an MCP client

Point your MCP client (Claude Desktop, a custom agent, etc.) to:

```
http://localhost:8765/sse
```

For remote access, replace `localhost` with your server's IP or hostname. Ensure port 8765 is open in your firewall.

All tool calls through the MCP gateway are checked by the `escalation-boundary.py` hook before execution — the same governance boundary that applies to direct orchestrator calls.

---

## Path 2 — systemd (native Linux)

### Quick start

```bash
# 1. Run the installer as root (installs Docker, builds image, installs service)
sudo bash install-system.sh

# 2. Edit the credential file that was created
sudo nano /etc/praxis/env
#    Uncomment and set: CLAUDE_CODE_OAUTH_TOKEN=your-token-here
#    (or ANTHROPIC_API_KEY=sk-ant-...)

# 3. Restart the service to pick up new credentials
sudo systemctl restart praxis

# 4. Optional: also start the MCP gateway via Docker Compose
docker compose up -d
```

The installer:
- Installs Docker via the official apt source (if not already present)
- Builds the `praxis` Docker image
- Creates a dedicated `praxis` system user (no login shell, no home directory)
- Installs the repo to `/opt/praxis` and creates a Python venv at `/opt/praxis/.venv`
- Writes a credential template to `/etc/praxis/env` (mode 600, owned root:praxis)
- Installs `systemd/praxis.service` to `/etc/systemd/system/`
- Enables and starts the service

### Service management

```bash
# Check service status
systemctl status praxis

# Start / stop / restart
sudo systemctl start praxis
sudo systemctl stop praxis
sudo systemctl restart praxis

# Disable autostart on boot
sudo systemctl disable praxis

# Re-enable autostart
sudo systemctl enable praxis
```

### Logs

The service logs to the system journal (`StandardOutput=journal`):

```bash
# Follow live logs
journalctl -u praxis -f

# Show last 100 lines
journalctl -u praxis -n 100

# Show logs since last boot
journalctl -u praxis -b
```

### Updating

```bash
# Pull latest source into the repo directory
git pull

# Re-run installer (it syncs /opt/praxis and rebuilds the Docker image)
sudo bash install-system.sh

# Or manually:
sudo rsync -a --delete \
    --exclude='.git/' --exclude='.praxis/' \
    ./ /opt/praxis/
sudo /opt/praxis/.venv/bin/pip install --no-cache-dir ".[all]" -e /opt/praxis
sudo systemctl restart praxis
```

---

## Credentials

### What to set

| Variable | Required | Description |
|----------|----------|-------------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Yes (or API key) | Subscription OAuth token — flat cost, preferred |
| `ANTHROPIC_API_KEY` | Yes (or OAuth) | Pay-per-token API key — fallback |
| `PRAXIS_WORKSPACE_ROOT` | Yes | Directory for tasks, wiki, staging, memory |
| `PRAXIS_MEMORY_ROOT` | Yes | Typically `$PRAXIS_WORKSPACE_ROOT/.praxis/memory` |
| `PRAXIS_MCP_PORT` | No | MCP gateway port (default 8765) |
| `PRAXIS_SLACK_WEBHOOK_URL` | No | Outbound Slack webhook |
| `PRAXIS_SLACK_BOT_TOKEN` | No | Slack bot token (for socket listener) |
| `PRAXIS_ALLOWED_DOMAINS` | No | Comma-separated domain allowlist for network egress |

For Docker Compose, set these in `.env`.
For systemd, set them in `/etc/praxis/env`.

### What never to do

- **Never commit `.env` or `/etc/praxis/env`** — these files contain live secrets.
- **Never hardcode tokens in source files, Dockerfiles, or docker-compose.yml** — use `env_file` or `EnvironmentFile` references instead.
- **Never log or echo credential values** — Praxis redacts them via `_redact_secrets()`, but do not work around this.
- **Never push a Docker image built with credentials baked in** — the Dockerfile does not bake credentials; keep it that way.

---

## Troubleshooting

### Container won't start

```bash
# Check container logs
docker compose logs mcp
docker compose logs daemon

# Common causes:
# - .env missing or empty — copy from .env.example and fill in
# - CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY not set
# - Port 8765 already in use (change PRAXIS_MCP_PORT in .env)
```

### Queue not processing

```bash
# Check the daemon service
docker compose logs daemon

# Verify the workspace volume is mounted correctly
docker compose exec daemon ls /workspace/.praxis/queue/

# Check queue stats from host (if Python is installed)
python -m praxis --status

# Tasks may be stuck in 'running' state after a crash — they are automatically
# reset to 'failed' when the queue runner starts (crash recovery).
```

### MCP client can't connect

```bash
# Verify the MCP gateway is running
docker compose ps
docker compose logs mcp

# Test the SSE endpoint
curl -N http://localhost:8765/sse

# If running remotely, check your firewall allows port 8765
# Verify PRAXIS_MCP_PORT matches the port in your MCP client config
```

### systemd service fails to start

```bash
# View the full error
journalctl -u praxis -n 50 --no-pager

# Verify the venv and package are installed
/opt/praxis/.venv/bin/python -m praxis --status

# Check credential file permissions (must be readable by praxis user)
ls -la /etc/praxis/env
# Should show: -rw------- root praxis

# Verify the 'praxis' user exists
id praxis
```

### Hook blocks a tool call unexpectedly

The `escalation-boundary.py` hook enforces the §5 boundary. If a legitimate tool call is blocked:

1. Check `journalctl -u praxis -n 20` for the blocked reason.
2. Review `.claude/hooks/escalation-boundary.py` — the enforcement logic is readable.
3. For network egress, add the domain to `PRAXIS_ALLOWED_DOMAINS` in your env file.
4. Control-plane changes (to the hook itself) must be applied by a human — never auto-modified.
