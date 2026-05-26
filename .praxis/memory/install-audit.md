# Install Audit — Phase 5 P-1

**Date:** 2026-05-26

## Python Version
- **Required:** >= 3.10 (per pyproject.toml)
- Uses `|` type unions (3.10+), PEP 563 `from __future__ import annotations`
- No match/case. Supports 3.10, 3.11, 3.12, 3.13.

## Python Package Dependencies
| Package | Version | Required | Extra | Purpose |
|---------|---------|----------|-------|---------|
| anthropic | >= 0.39.0 | Yes (core) | — | Anthropic API client |
| pyyaml | >= 6.0 | Yes (core) | — | convergence.yaml parsing |
| openai | >= 1.0 | No | `[local]` | LocalRuntime + OpenAICloudRuntime |
| pytest | >= 8.0 | No | `[dev]` | Test suite |

All other imports are stdlib (imaplib, email, urllib, ssl, subprocess, etc.).

## System CLI Tools
**Always available (POSIX standard):** grep, git, du, coreutils
**Optional (integration-specific, fail gracefully):**
- `gh` — GitHub integration (https://cli.github.com)
- `coverage` — Analyze integration (`pip install coverage`)
- `radon` — Analyze integration (`pip install radon`)
- `pylint` — Analyze integration (`pip install pylint`)
- `pip-audit` — Dependencies integration (`pip install pip-audit`)

## Environment Variables
**Auth (1 required):**
- `CLAUDE_CODE_OAUTH_TOKEN` — subscription OAuth (preferred)
- `ANTHROPIC_API_KEY` — pay-per-token fallback

**Workspace:**
- `PRAXIS_WORKSPACE_ROOT` — defaults to cwd
- `PRAXIS_MEMORY_ROOT` — defaults to $WORKSPACE_ROOT/.praxis/memory

**Runtime selection:**
- `PRAXIS_RUNTIME` — claude (default), local, cloud
- `PRAXIS_MODEL` — override default model

**Local runtime:**
- `PRAXIS_LOCAL_BASE_URL` — default http://localhost:11434
- `PRAXIS_LOCAL_MODEL` — default llama3.1:8b

**Cloud runtime:**
- `PRAXIS_CLOUD_API_KEY` — required for cloud
- `PRAXIS_CLOUD_BASE_URL` — default https://api.openai.com/v1
- `PRAXIS_CLOUD_MODEL` — default gpt-4o

**Integrations (all optional):**
- `PRAXIS_WEB_SEARCH_API_KEY` — Brave Search API
- `PRAXIS_ALLOWED_DOMAINS` — domain allowlist (CSV)
- `PRAXIS_EMAIL_IMAP_HOST` — IMAP server
- `PRAXIS_EMAIL_USER` — email address
- `PRAXIS_EMAIL_PASSWORD` — app password
- `PRAXIS_CALENDAR_URL` — private iCal feed URL
- `GITHUB_TOKEN` — GitHub API token
- `PRAXIS_QUEUE_POLL_INTERVAL` — queue poll interval (default 2s)

## Redacted in output
CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY, GITHUB_TOKEN, PRAXIS_WEB_SEARCH_API_KEY, PRAXIS_EMAIL_PASSWORD, PRAXIS_CALENDAR_URL
