#!/usr/bin/env bash
# install-system.sh — one-command system installer for Praxis (Linux, requires root)
#
# What this does:
#   1. Checks for root / sudo
#   2. Installs Docker via official apt method if missing
#   3. Builds the Praxis Docker image (tag: praxis)
#   4. Creates a dedicated 'praxis' system user
#   5. Installs Praxis to /opt/praxis with a Python venv
#   6. Writes a credential template to /etc/praxis/env
#   7. Installs + enables + starts the systemd service
#   8. Prints a setup checklist
#
# After running: edit /etc/praxis/env with real credentials, then restart.

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Root check
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use: sudo bash install-system.sh)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Praxis system installer"
echo "    Working directory: ${SCRIPT_DIR}"
echo ""

# ---------------------------------------------------------------------------
# 2. Docker check / install
# ---------------------------------------------------------------------------
if command -v docker &>/dev/null; then
    echo "[1/7] Docker already installed: $(docker --version)"
else
    echo "[1/7] Docker not found — installing via official apt method..."
    apt-get update
    apt-get install -y ca-certificates curl gnupg

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    # shellcheck source=/dev/null
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

    echo "    Docker installed: $(docker --version)"
fi

# ---------------------------------------------------------------------------
# 3. Build Docker image
# ---------------------------------------------------------------------------
echo "[2/7] Building Docker image (tag: praxis)..."
docker build -t praxis "${SCRIPT_DIR}"
echo "    Image built."

# ---------------------------------------------------------------------------
# 4. Create system user
# ---------------------------------------------------------------------------
echo "[3/7] Creating 'praxis' system user (if not exists)..."
if id praxis &>/dev/null; then
    echo "    User 'praxis' already exists — skipping."
else
    useradd --system --no-create-home --shell /usr/sbin/nologin praxis
    echo "    User 'praxis' created."
fi

# ---------------------------------------------------------------------------
# 5. Install to /opt/praxis
# ---------------------------------------------------------------------------
echo "[4/7] Installing Praxis to /opt/praxis..."

# Copy repo
if [[ -d /opt/praxis ]]; then
    echo "    /opt/praxis already exists — updating..."
    rsync -a --delete \
        --exclude='.git/' \
        --exclude='.praxis/' \
        --exclude='*.pyc' \
        --exclude='__pycache__/' \
        "${SCRIPT_DIR}/" /opt/praxis/
else
    rsync -a \
        --exclude='.git/' \
        --exclude='.praxis/' \
        --exclude='*.pyc' \
        --exclude='__pycache__/' \
        "${SCRIPT_DIR}/" /opt/praxis/
fi

# Create venv
if [[ ! -d /opt/praxis/.venv ]]; then
    python3 -m venv /opt/praxis/.venv
    echo "    venv created at /opt/praxis/.venv"
else
    echo "    venv already exists — skipping creation."
fi

# Install package (all extras)
echo "    Installing praxis[all] into venv..."
/opt/praxis/.venv/bin/pip install --no-cache-dir --quiet ".[all]" \
    --find-links /opt/praxis \
    -e /opt/praxis

# Create required runtime directories
mkdir -p \
    /opt/praxis/.praxis/memory \
    /opt/praxis/.praxis/queue \
    /opt/praxis/.praxis/staging/drafts \
    /opt/praxis/.praxis/staging/events \
    /opt/praxis/.praxis/staging/slack/messages \
    /opt/praxis/.praxis/staging/slack/approvals \
    /etc/praxis \
    /var/log/praxis

# Set ownership of mutable directories
chown -R praxis:praxis /opt/praxis/.praxis /var/log/praxis

echo "    Installation complete."

# ---------------------------------------------------------------------------
# 6. Credential template
# ---------------------------------------------------------------------------
echo "[5/7] Writing credential template to /etc/praxis/env..."
if [[ -f /etc/praxis/env ]]; then
    echo "    /etc/praxis/env already exists — not overwriting."
    echo "    (Edit it manually to update credentials.)"
else
    cat > /etc/praxis/env <<'EOF'
# Praxis credentials — set real values, never commit this file
# After editing, restart the service: systemctl restart praxis

PRAXIS_WORKSPACE_ROOT=/opt/praxis/.praxis
PRAXIS_MEMORY_ROOT=/opt/praxis/.praxis/memory

# Auth: use ONE of the following (OAuth preferred — flat cost, no per-token billing)
# CLAUDE_CODE_OAUTH_TOKEN=your-token-here
# ANTHROPIC_API_KEY=sk-ant-...

# MCP Gateway port (default 8765)
# PRAXIS_MCP_PORT=8765

# Slack bridge (optional — only needed if using --slack-listen)
# PRAXIS_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
# PRAXIS_SLACK_BOT_TOKEN=xoxb-...
# PRAXIS_SLACK_APP_TOKEN=xapp-...

# Web / calendar egress (comma-separated allowlist)
# PRAXIS_ALLOWED_DOMAINS=api.search.brave.com,hooks.slack.com,slack.com

# Runtime (claude | local | cloud) — default: claude
# PRAXIS_RUNTIME=claude
EOF

    chmod 600 /etc/praxis/env
    chown root:praxis /etc/praxis/env
    echo "    Template written. EDIT /etc/praxis/env before starting the service."
fi

# ---------------------------------------------------------------------------
# 7. systemd service
# ---------------------------------------------------------------------------
echo "[6/7] Installing systemd service..."
cp "${SCRIPT_DIR}/systemd/praxis.service" /etc/systemd/system/praxis.service
systemctl daemon-reload
systemctl enable praxis
systemctl start praxis
echo "    Service installed, enabled, and started."

# ---------------------------------------------------------------------------
# 8. Checklist
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Praxis installation complete"
echo "============================================================"
echo ""
echo "  Docker image built (tag: praxis)"
echo "  System service installed at /etc/systemd/system/praxis.service"
echo "  Service enabled and started"
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Edit /etc/praxis/env — add your CLAUDE_CODE_OAUTH_TOKEN"
echo "     (or ANTHROPIC_API_KEY)"
echo ""
echo "  2. Restart service:    systemctl restart praxis"
echo ""
echo "  3. For MCP gateway:    docker compose up -d"
echo "     (from ${SCRIPT_DIR})"
echo ""
echo "  4. Connect MCP client: http://localhost:8765/sse"
echo ""
echo "  5. Check status:       python -m praxis --status"
echo "     (or)                systemctl status praxis"
echo ""
echo "  6. View logs:          journalctl -u praxis -f"
echo ""
