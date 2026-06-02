# Architecture — Praxis-Kernel

## Goal

Praxis is a governed agentic OS kernel: a Python daemon that runs overnight, performs
tasks on behalf of the user, and stages all external writes (email, calendar, Notion,
Linear, Slack) for morning approval via `python -m praxis --approve` or the web UI.
The user never gets surprised by autonomous sends. The §5 hook makes this architectural,
not advisory.

## Technology stack

**Python 3.10+** — primary language. Chosen for AI/data ecosystem alignment.
TypeScript was rejected for the core (wrong ecosystem for LLM integrations).

**Anthropic claude_agent_sdk** — orchestrator runtime. OAuth subscription, never API key.
`ANTHROPIC_API_KEY` is explicitly rejected at startup — it silently overrides OAuth
and burns uncapped metered billing.

**Starlette + uvicorn** — MCP HTTP/SSE gateway and REST API server.
FastAPI was rejected because FastMCP auto-generates inputSchema from annotations,
but Praxis needs to pass its pre-built JSON schemas verbatim.

**React 18 + Vite** — web UI. SPA served as static files from uvicorn at /ui/.
Next.js was rejected (requires separate Node.js server process).
React Native rejected (responsive web + Tailscale covers mobile adequately).

**ChromaDB + sentence-transformers all-MiniLM-L6-v2** — semantic memory (optional).
LanceDB was considered but ChromaDB is more battle-tested in Python.
Pinecone rejected (cloud-only, adds external dependency for personal assistant).

**watchdog** — filesystem event watcher for hooks engine (optional dep).

## Directory structure

```
praxis/                  — main Python package
  orchestrator.py        — Orchestrator: tool dispatch + §5 hook
  queue_runner.py        — Queue processing loop with scheduler + ambient threads
  queue.py               — TaskQueue CRUD on .praxis/queue/tasks.jsonl
  event_bus.py           — (NEW) async pub/sub singleton for real-time events
  api.py                 — (NEW) REST route handlers
  trust.py               — (NEW) per-sender trust tiers for channels
  voice.py               — (NEW) Whisper STT transcriber
  hooks_engine.py        — (NEW) FileWatcher + webhook receiver
  memory_store.py        — (NEW) ChromaDB vector store for wiki
  mcp_server.py          — MCP HTTP/SSE gateway + REST API wiring
  wiki.py                — bitemporal personal wiki (ingest/query/lint)
  integrations/          — Slack, Telegram, WhatsApp, Email, Calendar, Notion, Linear...
  runtime/               — ClaudeCodeRuntime, OpenAIBaseRuntime, LocalRuntime
  ui/                    — (NEW) React + Vite SPA source
    src/
      api/client.ts      — fetch wrapper with auth
      store/ws.ts        — Zustand WebSocket store
      components/Layout.tsx
      screens/           — Chat, Approvals, TaskBoard, Wiki, Schedule, Settings
    dist/                — built output, served at /ui/ (gitignored)
.praxis/
  queue/tasks.jsonl      — task queue (gitignored)
  staging/               — all staged external writes (gitignored)
  memory/                — conversational memory + ChromaDB (gitignored)
  security/              — denials audit log, credential inventory (gitignored)
  SOUL.md                — persona layer (gitignored)
  HEARTBEAT.md           — proactive trigger config (gitignored)
convergence.yaml         — runtime routing + channel trust config
scripts/validate_setup.py — integration health checks
```

## Key decisions

**Write-escalate is structural, not a runtime check.**
There is no `send_email()`, `create_event()`, or `create_notion_page()` function.
The only external write paths are staging functions that write to `.praxis/staging/`.
Human runs `--approve` or uses the web UI to execute staged actions. This means
an agent that "tries hard" to bypass the restriction will find there's nothing to bypass.
Django-style "permissions framework" approach was rejected — structural impossibility
is better than permission checks.

**§5 hook fires before every tool call regardless of permission_mode.**
`.claude/hooks/escalation-boundary.py` is a PreToolUse hook. Even with
`--dangerously-skip-permissions`, the hook fires. Never modify this file.
If a change is needed, write the exact patch to STATUS.md under "NEEDS HUMAN".

**EventBus is in-process pub/sub, not a message broker.**
Redis, RabbitMQ rejected — overkill for a single-process daemon. The event bus
connects queue_runner, scheduler, ambient monitor → WebSocket clients → web UI.
Purely internal signal infrastructure. Events do not cause tool calls.

**Feature-scoped sessions, not one long orchestrator.**
V1 orchestrate.py held a persistent orchestrator session across all tasks.
V2 sessions are disposable: orient from files, implement one feature, commit, exit.
Rate limit hits don't destroy state because state lives on disk.

**Bitemporal wiki, not flat MEMORY.md files.**
NeuroClaw/OpenClaw uses simple MEMORY.md. Praxis uses valid_from/superseded_on,
entity resolution (Jaro-Winkler), typed links, multi-source merge, content hashing.
Do not replace this with flat files. The semantic memory layer (ChromaDB) sits
on top of it — it does not replace it.

**Trust model: env vars take precedence over convergence.yaml.**
PRAXIS_OWNER_IDS/PRAXIS_ADMIN_IDS env vars are checked first.
convergence.yaml channels.<ch>.trust_ids is fallback.
PRAXIS_DEFAULT_TRUST (default: "user") is the last resort.

## Integration points

- **EventBus** → subscribed by WebSocket `/ws` endpoint → pushes JSON to browser
- **TaskQueue** → read by queue_runner loop, api.py endpoints, dashboard
- **Staging files** → read by api.py /api/approvals, written by integration tools
- **wiki.py ingest()** → calls memory_store.embed_wiki_page() if PRAXIS_SEMANTIC_MEMORY=true
- **orchestrator context assembly** → calls memory_store.search() and prepends results
- **mcp_server.py start()** → imports api.py handlers, hooks_engine routes, and registers all

## Security and auth approach

- Auth: `CLAUDE_CODE_OAUTH_TOKEN` (subscription). `ANTHROPIC_API_KEY` rejected at startup.
- §5 boundary: PreToolUse hook at `.claude/hooks/escalation-boundary.py`. Fires on every tool call.
- Web UI auth: `PRAXIS_UI_TOKEN` Bearer token on all `/api/*` routes and `/ws`. Optional when binding to 127.0.0.1 only.
- Remote access: Tailscale (zero-config, private). Set `PRAXIS_MCP_BIND` to Tailscale IP. NEVER expose 0.0.0.0 without `PRAXIS_UI_TOKEN`.
- Trust tiers: OWNER (all commands) / ADMIN (queue + view) / USER (read-only) / BLOCKED (silent ignore). Enforced per-channel before tasks reach the queue.
- Credentials: `.env` file, gitignored. Never logged, never committed.
