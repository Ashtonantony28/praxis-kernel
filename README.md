# Praxis

A personal AI assistant that works in the background and **never acts on your behalf without approval.**

Run it free on local Ollama, free Gemini API, or a Claude OAuth subscription. Plan/build enforcement, security boundary, and all 941 tests work identically on all three.

## Why it's different

**Every external write is staged, not executed.** There is no `send_email` function. No `create_event` function. No `create_issue` function. When Praxis wants to act on your behalf — draft an email, create a Linear issue, update a Notion page — it writes the action to `.praxis/staging/` and stops. You run `python -m praxis --approve` to review and execute. The escalation is structural: it cannot be bypassed by a prompt injection or a confused model, because no execution path exists.

**Genuinely model-agnostic at every layer.** Not just "swap the inference endpoint" — plan mode, build mode, subagent definitions, mode enforcement, and the §5 security boundary all work identically on Claude OAuth, free Gemini 2.5 Flash, and local Ollama. The 941 tests are parametrized: the same test body runs three times, once per provider. If a feature works on Claude, it works on Ollama.

---

## Quickstart

```bash
git clone https://github.com/Ashtonantony28/Praxis_AgenticOSKernel.git
cd Praxis_AgenticOSKernel
bash install.sh
python -m praxis --setup   # guided wizard: runtime, credentials, optional integrations
```

**Path 1 — Free, offline (local Ollama, zero cost)**
```bash
pip install praxis[local]
export PRAXIS_RUNTIME=local
export PRAXIS_LOCAL_BASE_URL=http://localhost:11434
export PRAXIS_LOCAL_MODEL=llama3.1:8b
python -m praxis "hello"
```

**Path 2 — Free, cloud (Gemini API free tier — no credit card)**
```bash
pip install praxis[local]
export PRAXIS_RUNTIME=cloud
export PRAXIS_CLOUD_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
export PRAXIS_CLOUD_API_KEY=your-gemini-key
export PRAXIS_CLOUD_MODEL=gemini-2.5-flash
python -m praxis "hello"
```

**Path 3 — Claude OAuth subscription**
```bash
export CLAUDE_CODE_OAUTH_TOKEN=your-oauth-token
python -m praxis "hello"
```

---

## How it works

**Plan mode** surfaces what Praxis will do before it does anything. **Build mode** (the default) executes.

```bash
# Surface a plan — read-only, no writes, no side effects
python -m praxis --mode plan "review this codebase and suggest improvements"

# Review staged plans
python -m praxis --list-plans

# Approve — re-runs the task in build mode
python -m praxis --approve-plan <uuid>
```

**The morning approval loop** — a concrete example:

1. Start the daemon: `python -m praxis --daemon`
2. Praxis reads your email, checks Linear, reviews the codebase overnight. Read actions are autonomous.
3. When it wants to act externally — create a GitHub issue, draft a reply, add a Notion page — it writes to `.praxis/staging/` and stops.
4. Morning: `python -m praxis --approve`

```
Pending actions (3):
[1] linear: create_issue "Fix flaky Playwright tests" (team: backend)
    Approve? [y/n/s] y ✓

[2] email: draft to alice@example.com "Weekly status update"
    Approve? [y/n/s] n ✗

[3] notion: append_block "Meeting notes 2026-05-29"
    Approve? [y/n/s] s  (skipped)
```

---

## What it integrates with

All integrations: read autonomously, write to staging only.

| Integration | What it reads | What it stages |
|-------------|---------------|----------------|
| **GitHub** | PRs, issues, diffs | — |
| **Codebase** | Coverage, complexity, lint | — |
| **TestRunner** | pytest results | — |
| **Dependencies** | Outdated packages, vulnerabilities | — |
| **WebResearch** | Search + page fetch | — |
| **Files** | Search, git status, disk usage | — |
| **Email** | IMAP inbox | Draft `.eml` files |
| **Calendar** | iCal feed | Proposed `.ics` files |
| **Notion** | Pages, databases | Create/update actions |
| **Linear** | Issues, teams | Create/update actions |
| **Playwright** | Page fetch, screenshot | — |
| **Slack** | — | Staged messages; webhook notify (Praxis→you) is autonomous |
| **MCP Gateway** | Exposes all tools over HTTP/SSE to any MCP client | — |

Full configuration reference: `.env.example`.

---

## Security model

The `§5 escalation boundary` is a `PreToolUse` hook that intercepts every tool call before execution — blocking writes outside `WORKSPACE_ROOT`, network egress to non-allowlisted domains, and any attempt to modify the control plane (`.claude/hooks/`, `.claude/settings.json`). Enforcement is two-layer: tools denied by the active mode are removed from the tool list before the model sees them, and `enforcement.py` blocks any denied call as defense-in-depth. What it doesn't claim: this is not container isolation — the boundary is designed for confused-model scenarios and prompt injection, not adversarial code execution.

---

## Deployment

- **Direct:** `python -m praxis "task"` — single interactive run, exits when done
- **Daemon:** `python -m praxis --daemon` — background queue processor, SIGTERM-safe, resumes staged tasks after restart
- **Docker:** `docker run -e PRAXIS_WORKSPACE_ROOT=/workspace ...` — see [DEPLOY.md](DEPLOY.md)
- **systemd:** unit file in [DEPLOY.md](DEPLOY.md) for always-on operation

---

## The wiki

Praxis maintains a bitemporal personal wiki in `wiki/pages/` — every fact carries a `valid_from` date and is superseded (never silently overwritten) when new information contradicts it, following the Karpathy LLM-Wiki pattern for temporal correctness. Seed it by placing `.md` or `.txt` files in `wiki/raw/`, then query: `python -m praxis "wiki query: what are my priorities this week?"`. The wiki is distinct from `.praxis/memory/` (operational state) — it's knowledge about you, structured for recall.

---

## Phase H — Persona, Proactive Triggers, and Telegram

### SOUL.md persona layer

Praxis can load a personal identity document at session start, prepending it to the orchestrator context (after the §5 governance block). This shapes voice, values, and working style without weakening the security boundary.

**Setup:**
```bash
cp wiki/SOUL.md .praxis/SOUL.md
# Edit .praxis/SOUL.md — add your name, preferred tone, values, recurring context
```

`.praxis/SOUL.md` is gitignored and never logged.

### HEARTBEAT.md proactive triggers

The scheduler reads `.praxis/HEARTBEAT.md` and fires matching sections as low-priority Tasks based on day-of-week and time-of-day — no cron required. Each H2 section with a `when:` line becomes a recurring trigger.

**Setup:**
```bash
cp wiki/HEARTBEAT.md .praxis/HEARTBEAT.md
# Edit .praxis/HEARTBEAT.md — adjust the when: lines and prompt bodies
```

Example `when:` syntax inside `.praxis/HEARTBEAT.md`:

```markdown
## Morning standup
when: weekdays 07:00-09:00

What's on my plate today? Check Linear for assigned issues.
```

Configure the check interval (default 30 min):
```bash
# In .env:
PRAXIS_HEARTBEAT_INTERVAL_MINUTES=30
```

### Telegram adapter

Inbound Telegram messages are enqueued as Tasks. Replies are **staged by default** (written to `.praxis/staging/telegram/replies/`) — never sent autonomously unless you configure the autonomy gate in `convergence.yaml`.

**Setup:**
```bash
pip install -e ".[telegram]"
```

Add to `.env`:
```bash
TELEGRAM_BOT_TOKEN=your-bot-token-here   # from BotFather: https://t.me/BotFather
```

Configure `convergence.yaml` (already scaffolded by H03):
```yaml
channels:
  telegram:
    autonomy: staged          # or "autonomous" to allow direct replies
    trusted_contacts: []      # add your Telegram user_id(s) here
    max_autonomous_reply_words: 50
```

Add `api.telegram.org` to `PRAXIS_ALLOWED_DOMAINS` in `.env`.

The autonomy gate only sends directly when: `autonomy=autonomous` AND sender is in `trusted_contacts` AND reply is within the word limit. Otherwise the reply is staged for your approval.

---

## Tests

```bash
pip install praxis[dev]
python -m pytest tests/ -q   # 941 tests, all mocked — no credentials needed
```

941 tests, parametrized over all three runtimes: the same test body runs once for Claude, once for cloud (OpenAI-compatible), once for local (Ollama). The §5 hook md5 is verified on every session startup to detect tampering.

---

## Contributing

1. Fork and create a feature branch
2. Write tests using `FakeClient` from `tests/conftest.py` — no real API calls
3. Run `python -m pytest tests/ -v`
4. The §5 hook is the one thing never to bypass, weaken, or route around

---

## License

MIT
