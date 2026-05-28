# Tasks

<!-- Scenario A: items below marked [x] reflect work believed shipped per the
     build history. The AUDIT task runs FIRST and the orchestrator must reconcile
     these against the real repo, correcting any that the codebase contradicts.
     Do not trust this list over the actual code. -->

## Onboarding (run first)
- [x] TASK-000: AUDIT — dispatch the read-only 'auditor' to inventory the actual
      repo at the confirmed cwd. Write a factual baseline to STATUS.md: package
      layout, what exists in praxis/ and praxis/integrations/ and praxis/runtime/,
      the control-plane files, the real current test count and whether they pass,
      the default branch, and which of the [x] items below are actually present.
      Reconcile TASKS.md against findings. Do NOT implement. — deps: none

## Believed-shipped baseline (verify in audit; correct if wrong)
- [x] TASK-B01: Python orchestrator + five subagents — see praxis/orchestrator.py, praxis/subagents.py
- [x] TASK-B02: Runtime abstraction + ClaudeCodeRuntime (OAuth, auth_token=) — see praxis/runtime/
- [x] TASK-B03: API-key runtime path — see praxis/runtime/
- [x] TASK-B04: OpenAI-compatible cloud/local runtime (Gemini-verified) — see praxis/runtime/cloud.py, local.py
- [x] TASK-B05: Control-plane hook + settings wiring — see .claude/hooks/escalation-boundary.py, .claude/settings.json
- [x] TASK-B06: Integrations — github, codebase, testrunner, dependencies — see praxis/integrations/
- [x] TASK-B07: Web integration (Brave + domain-enforced fetch) — see praxis/integrations/web.py
- [x] TASK-B08: File-management integration — see praxis/integrations/files.py
- [x] TASK-B09: Email + calendar (read-safe / write-escalate) — see praxis/integrations/email.py, calendar.py
- [x] TASK-B10: Unattended operation (queue, checkpoint, queue_runner, daemon) — see praxis/queue.py etc. [AUDIT: source files verified present; .praxis/queue/ dir absent at audit time — created at runtime]
- [x] TASK-B11: Open-source prep (README, install.sh, demo/demo.sh, LICENSE, CI, templates)
- [x] TASK-B12: Defense-in-depth WebResearch egress check at hook level — verify in escalation-boundary.py

## Phase W — Bitemporal personal wiki (current milestone, all new work)
- [x] TASK-W01: Wiki survey — 'auditor' confirms how a wiki/ layer sits alongside
      existing .praxis/memory (operational state) vs wiki (knowledge about the
      user); write findings to .praxis/memory/wiki-survey.md — deps: TASK-000
- [x] TASK-W02: Wiki design + SCHEMA.md — Planner-equivalent design pass: bitemporal
      frontmatter spec (valid_from, learned_on, superseded_on+pointer); entity-
      resolution workflow; typed-link vocabulary (contradicts|supports|contains|
      supersedes|relates); level taxonomy (theme|topic|fact); ingest/query/lint
      contracts; how it stays within the §5 boundary. Write wiki/SCHEMA.md and
      .praxis/memory/wiki-plan.md. No implementation. — deps: TASK-W01
- [x] TASK-W03: Implement wiki scaffolding — wiki/raw/ (read-only to Praxis),
      wiki/pages/, wiki/index.md, wiki/log.md (grep-parseable prefix), per SCHEMA.md
      — deps: TASK-W02
- [x] TASK-W04: Implement praxis/wiki.py :: ingest(source) — entity resolution
      against existing pages, write/update pages with bitemporal frontmatter,
      update index + log, flag contradictions as supersedes — deps: TASK-W03
- [x] TASK-W05: Implement praxis/wiki.py :: query(question) — read index first then
      drill into pages, synthesize with citations, offer file-back — deps: TASK-W03
      # independent of W04's ingest internals → may parallelize ONLY if disjoint files
- [x] TASK-W06: Implement praxis/wiki.py :: lint() — find contradictions, stale
      facts, orphans, duplicate entities, missing typed links; report, do not
      auto-apply — deps: TASK-W04, TASK-W05
- [x] TASK-W07: Tests — bitemporal supersede-not-overwrite; entity-resolution
      catches near-duplicate; ingest idempotent; query reads index before pages;
      all pre-existing tests still green; live hook still fires — deps: TASK-W04, TASK-W05, TASK-W06
- [x] TASK-W08: Real end-to-end ingest — drop a short bio/notes file into wiki/raw,
      ingest it, confirm pages/index/log correct and human-readable — deps: TASK-W07
- [x] TASK-W09: Scribe pass — update CLAUDE.md with the wiki layer; write Phase S
      (Slack bridge) plan to morning-handoff — deps: TASK-W08

## Phase S — Slack bridge (current milestone)
<!-- Design decisions locked (2026-05-27): push approval model, socket mode receiver,
     .praxis/staging/slack/ for state, all subagents callable, queue rate-limiting. -->
- [x] TASK-S01: Scout pass — read praxis/integrations/email.py, calendar.py, web.py
      (domain-check idiom), and pyproject.toml. Write .praxis/memory/slack-survey.md
      documenting: exact action signatures, staging file layout (.praxis/staging/),
      domain check idiom used in web.py + calendar.py, how optional deps are declared
      in pyproject.toml, test patterns in tests/test_integrations.py for email/calendar.
      Read-only, no code changes. — deps: TASK-W09
- [x] TASK-S02: Design pass — using slack-survey.md, write .praxis/memory/slack-plan.md:
      full action surface (notify, stage_message, list_staged, post_approval_request,
      get_approval, list_approvals), §5 analysis (notify = Praxis→user via own webhook,
      NOT user-attributed → OK to send; stage_message = user-attributed → write-escalate),
      .praxis/staging/slack/messages/ and approvals/ layout, SlackSocketListener class
      design for __main__.py --slack-listen, exact pyproject.toml [slack] dep group
      (slack_sdk>=3.0), test plan (20-30 tests). No code. — deps: TASK-S01
- [x] TASK-S03: Implement praxis/integrations/slack.py — SlackIntegration class;
      actions: notify (POST to webhook, domain-check on hooks.slack.com),
      stage_message (write .praxis/staging/slack/messages/{id}.json, never sends),
      list_staged (read-only), post_approval_request (stage + notify), get_approval
      (read-only), list_approvals (read-only); SCHEMAS + IMPLEMENTATIONS dicts;
      wire into praxis/integrations/__init__.py (import + merge); update pyproject.toml
      to add [slack] optional dep group (slack_sdk>=3.0). — deps: TASK-S02
- [x] TASK-S04: Implement praxis/slack_listener.py — SlackSocketListener class using
      slack_sdk socket mode (PRAXIS_SLACK_BOT_TOKEN); handles DM messages + slash
      commands (→ TaskQueue tasks in .praxis/queue/) + block_actions (approval button
      clicks → update .praxis/staging/slack/approvals/{id}.json); graceful SIGTERM.
      Update praxis/__main__.py: add --slack-listen flag that starts the listener loop.
      — deps: TASK-S03
- [x] TASK-S05: Tests — tests/test_slack.py (20-30 tests, all mocked):
      notify posts to webhook URL; domain-check blocks unlisted domain; stage_message
      writes file + never POSTs; list_staged returns correct items; approval round-trip;
      socket listener enqueues commands; --slack-listen wires listener in __main__;
      PRAXIS_SLACK_WEBHOOK_URL and PRAXIS_SLACK_BOT_TOKEN missing produce clear errors.
      All 425+ pre-existing tests still green. — deps: TASK-S03, TASK-S04
- [x] TASK-S06: Scribe pass — update CLAUDE.md (add slack.py + slack_listener.py to
      repo layout; add Slack integration to conventions with §5 analysis); update
      .env.example (PRAXIS_SLACK_WEBHOOK_URL, PRAXIS_SLACK_BOT_TOKEN, note to add
      hooks.slack.com+slack.com to PRAXIS_ALLOWED_DOMAINS); update README with Phase S
      section. Append STATUS.md entry. — deps: TASK-S05

## Phase M — MCP Gateway (HTTP/SSE transport; current milestone)
<!-- Design decision locked (2026-05-27): Transport = HTTP/SSE (not stdio).
     Reason: works locally AND remotely; compatible with Claude Desktop AND remote
     agents; fits existing daemon architecture. stdio is local-only and too limiting.
     §5 rule: every MCP tool call routes through escalation-boundary.py before execution.
     No new auth surface: server runs inside WORKSPACE_ROOT, same env vars, same hook. -->
- [x] TASK-M01: Scout pass — read praxis/tools.py (TOOL_SCHEMAS format, _redact_secrets,
      _subprocess_env) and praxis/integrations/__init__.py (INTEGRATION_SCHEMAS shape).
      Survey the `mcp` Python SDK (pip show mcp or pypi docs): ToolDef/ToolResult types,
      how HTTP/SSE server is started, how tool handlers are registered.
      Write .praxis/memory/mcp-survey.md: exact schema format used in tools.py, exact
      MCP SDK API surface for HTTP/SSE, how to translate one Praxis tool schema to a
      ToolDef, and any §5 considerations at the MCP boundary.
      Read-only; no code changes. — deps: TASK-S06
- [x] TASK-M02: Design pass — using mcp-survey.md, write .praxis/memory/mcp-plan.md:
      full MCPServer class design (HTTP/SSE transport, port via PRAXIS_MCP_PORT env var
      default 8765), how TOOL_SCHEMAS + INTEGRATION_SCHEMAS translate to ToolDef list,
      how each tool call routes through Orchestrator.run() (or direct tool dispatch),
      §5 boundary: every tool_call JSON passes through escalation-boundary.py hook
      before execution; resource exposure decision (wiki/pages/ as MCP resources: yes/no
      with rationale); convergence.yaml routing applies to MCP-dispatched calls;
      [mcp] optional dep group (mcp>=1.0); test plan (15-25 tests, all mocked).
      No code. — deps: TASK-M01
- [x] TASK-M03: Implement praxis/mcp_server.py — MCPServer class; HTTP/SSE transport
      on configurable port (PRAXIS_MCP_PORT, default 8765); on startup load all
      TOOL_SCHEMAS + INTEGRATION_SCHEMAS and register as MCP ToolDef objects; each
      incoming tool_call: (1) run through escalation-boundary.py hook check, (2) if
      allowed, dispatch through existing tool/integration implementations, (3) return
      ToolResult. Expose wiki/pages/ as MCP Resources if mcp-plan.md recommends it.
      Add [mcp] optional dep to pyproject.toml (mcp>=1.0). This task edits ONLY
      praxis/mcp_server.py and pyproject.toml — NOT __main__.py (that is M04).
      — deps: TASK-M02
- [x] TASK-M04: Wire --mcp flag into praxis/__main__.py — add "mcp" mode to
      _parse_mode() and a "mcp" branch to main() that starts MCPServer on the
      configured port; add PRAXIS_MCP_PORT to .env.example with default and comment.
      This task edits ONLY praxis/__main__.py and .env.example. — deps: TASK-M03
- [x] TASK-M05: Tests — create tests/test_mcp.py (15-25 tests, all mocked):
      server starts and registers all TOOL_SCHEMAS + INTEGRATION_SCHEMAS as ToolDefs;
      tool_call dispatches to correct implementation; §5 hook blocks disallowed calls
      (mock hook returning exit 2); redacted secrets do not appear in ToolResult;
      missing [mcp] dep produces clear install message; --mcp flag in __main__.py
      starts MCPServer; wiki resources exposed if implemented.
      All 451 pre-existing tests still green. — deps: TASK-M03, TASK-M04
- [x] TASK-M06: Scribe pass — update CLAUDE.md: add praxis/mcp_server.py to repo layout;
      add MCP Gateway conventions section (HTTP/SSE transport, PRAXIS_MCP_PORT, §5
      routing, [mcp] install, resource exposure); update README with Phase M section
      (what it is, quickstart: pip install praxis[mcp] + --mcp flag, example usage);
      overwrite .praxis/memory/morning-handoff.md with Phase M completion summary +
      next-milestone notes. Append STATUS.md entry. — deps: TASK-M05

## Phase R — Docker + systemd deployment (current milestone)
<!-- Goal: make --daemon and --mcp production-grade on any Linux host or Docker host.
     All deliverables are new files; no existing Python source is modified.
     475 tests must still pass after this phase. -->
- [x] TASK-R01: Dockerfile — FROM python:3.12-slim; pip install praxis[all] from repo;
      COPY repo into /app; set PRAXIS_WORKSPACE_ROOT=/workspace as ENV; EXPOSE 8765;
      default CMD ["python", "-m", "praxis", "--mcp"] — deps: none
- [x] TASK-R02: docker-compose.yml — two services: mcp (runs --mcp, port 8765) and
      daemon (runs --daemon); shared volume /workspace mapped to ./workspace on host;
      env_file: .env; depends_on where needed; restart: unless-stopped — deps: TASK-R01
- [x] TASK-R03: systemd/praxis.service — unit file: Description, After=network.target,
      EnvironmentFile=/etc/praxis/env, ExecStart=python -m praxis --daemon,
      Restart=on-failure, RestartSec=5s, User=praxis, WorkingDirectory=/opt/praxis;
      Install WantedBy=multi-user.target — deps: none
- [x] TASK-R04: install-system.sh — checks for Docker (installs via apt if missing);
      docker build -t praxis .; copies systemd/praxis.service to /etc/systemd/system/;
      systemctl daemon-reload + enable + start; prints setup checklist (set env vars,
      point workspace volume, run docker compose up) — deps: TASK-R01, TASK-R02, TASK-R03
- [x] TASK-R05: DEPLOY.md — deployment guide: Docker path (compose up), systemd path
      (install-system.sh), how to pass credentials (env file format, never commit),
      how to update (docker pull/rebuild + systemctl restart), MCP client connection
      (http://host:8765/sse), troubleshooting tips — deps: TASK-R01, TASK-R02, TASK-R03, TASK-R04

## Security Prerequisites (before Phase X)

- [x] TASK-P01: Financial circuit breaker — create praxis/runtime/cost.py with
      CostCircuitBreaker class (reads PRAXIS_MAX_SESSION_COST env var, default 2.00;
      pricing table for known Claude/OpenAI/Gemini models; _DEFAULT_PRICING=(1.00,3.00)
      for unknown models; on breach: dump JSON to .praxis/logs/cost-circuit-break-{ts}.json
      then sys.exit(3)). Wire into ClaudeCodeRuntime.run_loop() (uses response.usage.input_tokens
      / output_tokens after _create_with_retry). Wire into OpenAIBaseRuntime.run_loop() (uses
      response.usage.prompt_tokens / completion_tokens after _call_api). Add tests/test_cost_circuit_breaker.py
      (~14 tests, all mocked — breaker fires at cap, dumps correct JSON, exit code 3, per-call
      accumulation, model pricing lookup, unknown model uses default, env var override,
      ClaudeCode + OpenAIBase integration). Does NOT touch .env.example or __init__.py.
      — deps: none

- [x] PREREQ-2: Strip Bash from Scout and Planner subagent definitions.
      .claude/agents/scout.md and .claude/agents/planner.md currently have
      "tools: Read, Grep, Glob, Bash" — Bash must be removed (they are read-only agents).
      Verifier and Builder KEEP Bash. The hook blocks all writes to .claude/ — this change
      MUST be applied by the human. Exact patches are in STATUS.md under
      "NEEDS HUMAN: PREREQ-2". — deps: none — APPLIED MANUALLY 2026-05-27

## Phase X — External integrations (requires P01 complete before X03)

- [x] TASK-X01: Playwright subprocess integration — create praxis/integrations/playwright.py
      (module-level, no class, following existing integration pattern). Actions: fetch (url →
      text content, max 4000 chars), screenshot (url → save PNG to workspace path → return path).
      Browser runs in an isolated subprocess: write a small inline Python script to a temp file,
      pass URL via env var (not shell args), subprocess env strips all PRAXIS_ auth tokens.
      Use playwright.async_api for chromium headless, fresh context (no stored cookies, no
      --user-data-dir pointing to real profile). Domain allowlist: check URL against
      config.allowed_domains before launching. If playwright not installed: return clear
      install message. Output path for screenshot must be inside WORKSPACE_ROOT.
      _redact_secrets() on all outputs. SCHEMAS + IMPLEMENTATIONS dicts.
      Update pyproject.toml: add [playwright] optional dep (playwright>=1.40); add to [all].
      Does NOT touch praxis/integrations/__init__.py — wired in X03. — deps: none

- [x] TASK-X02: Notion + Linear integrations (write-escalate) — create two files:
      praxis/integrations/notion.py: actions: search (query→results), get_page (page_id→content),
      list_databases; write-escalate actions (staged, never live): create_page, update_page,
      append_block. Auth: PRAXIS_NOTION_TOKEN env var. Domain: api.notion.com checked against
      config.allowed_domains for reads. Uses urllib.request (stdlib). Add PRAXIS_NOTION_TOKEN
      to _redact_secrets() in praxis/tools.py. SCHEMAS + IMPLEMENTATIONS dicts.
      praxis/integrations/linear.py: actions: list_issues, get_issue, list_teams;
      write-escalate actions (staged): create_issue, update_issue, add_comment.
      Auth: PRAXIS_LINEAR_API_KEY env var. Domain: api.linear.app. Linear API is GraphQL;
      use urllib.request with JSON body. Add PRAXIS_LINEAR_API_KEY to _redact_secrets() in
      praxis/tools.py. SCHEMAS + IMPLEMENTATIONS dicts.
      Staging format for ALL write actions (both providers): append one JSON line to
      .praxis/staging/external_actions.jsonl per action:
        {"id": "<uuid4>", "provider": "notion"|"linear", "action": "<action_name>",
         "params": {...}, "queued_at": "<ISO8601>", "status": "pending"}
      Create staging dir if absent. Return confirmation string (never execute the write).
      Does NOT touch praxis/integrations/__init__.py — wired in X03. — deps: none

- [x] TASK-X03: Wire integrations + --approve command — runs after P01, X01, X02 complete.
      (1) Update praxis/integrations/__init__.py: import playwright, notion, linear;
          merge their SCHEMAS + IMPLEMENTATIONS into aggregates. Update the hardcoded
          integration-count assertions in tests/test_integrations.py (was 10, now 13).
      (2) Add --approve mode to praxis/__main__.py: _parse_mode checks "--approve";
          new branch in main() reads .praxis/staging/external_actions.jsonl, filters
          status=="pending", displays each action (provider, action name, params summary),
          prompts Y/N/s(kip) via input(), on Y: executes action by calling the real API
          (urllib.request with domain check + auth from env), on N: marks rejected,
          on s: skips (leaves pending). Updates JSONL entry status in-place (rewrite file).
          If staging file absent or empty: print "No pending actions." and exit 0.
      (3) Update .env.example: add PRAXIS_MAX_SESSION_COST section (after MCP section);
          add PRAXIS_NOTION_TOKEN, PRAXIS_LINEAR_API_KEY sections; add note about
          api.notion.com and api.linear.app in PRAXIS_ALLOWED_DOMAINS.
      — deps: TASK-P01, TASK-X01, TASK-X02

- [x] TASK-X04: Phase X tests — create tests/test_phase_x.py (~45 tests, all mocked):
      TestPlaywright (5 tests): fetch returns content, domain check blocks unlisted,
        missing playwright dep gives clear message, subprocess env strips auth tokens,
        screenshot validates path is inside workspace.
      TestNotion (8 tests): search/get_page/list_databases call API, create_page stages
        to JSONL (never POSTs), update_page stages, append_block stages, missing token
        gives clear message, domain check blocks unlisted domain for reads,
        JSONL entry has correct format (id/provider/action/params/queued_at/status).
      TestLinear (8 tests): same structure as Notion.
      TestApproveCommand (10 tests): reads JSONL, displays pending actions, approves
        updates status to "approved", rejects updates to "rejected", skip leaves "pending",
        empty staging file prints "No pending actions.", non-pending entries skipped in display,
        executes approved Notion action via urllib, executes approved Linear action,
        bad domain in staging blocked by domain check.
      TestCostEnvironment (4 tests): PRAXIS_MAX_SESSION_COST read correctly across runtimes.
      475+ pre-existing tests still green. — deps: TASK-X03

- [x] TASK-X05: Scribe pass — update CLAUDE.md (add playwright.py, notion.py, linear.py
      to integrations list; add Phase X conventions section: staged external actions pattern,
      external_actions.jsonl format, --approve command, §5 analysis); update README.md
      (Phase X section); overwrite .praxis/memory/morning-handoff.md with Phase X
      completion summary. Append STATUS.md entry. — deps: TASK-X04

## Option C — Hardening Sprint

- [x] TASK-C04: Verification sweep — confirm Scout (.claude/agents/scout.md) and Planner
      (.claude/agents/planner.md) no longer list Bash in their tools after PREREQ-2 was
      applied. Document result in STATUS.md. (Read-only; verified by orchestrator inline.)
      — deps: PREREQ-2

- [x] TASK-C01: Rate limiting on task queue — add PRAXIS_MAX_CONCURRENT_TASKS env var
      (default 3) to praxis/queue_runner.py. In run_queue_loop(), read the var and, before
      picking the next pending task, check queue.stats()["running"] >= max_concurrent;
      if so, sleep poll_interval and continue. Log the setting at startup.
      Add/update tests in tests/test_queue_runner.py (≥4 new tests: cap respected when
      running==max, task picked when slot available, env var override, default=3 used when
      unset). All 527 pre-existing tests still pass. Flip TASK-C01 [x] in TASKS.md and
      append STATUS.md entry. — deps: none

- [x] TASK-C02: Playwright retry and error handling — in praxis/integrations/playwright.py,
      add retry-with-backoff to _run_playwright_script(): on a PLAYWRIGHT_ERROR: output,
      retry up to MAX_RETRIES=3 times with delays [1s, 2s, 4s] between attempts (use
      time.sleep). After exhausting retries, return a clean user-facing error string
      (not a raw Python traceback). Also add friendly messages for common errors:
      TimeoutExpired → "Browser navigation timed out", subprocess crash → "Browser process
      crashed". The PLAYWRIGHT_IMPORT_ERROR path does NOT retry (not transient).
      Add/update tests in tests/test_phase_x.py (≥4 new tests covering retry succeeds on
      second attempt, all retries exhausted returns clean message, timeout returns clean
      message, import error does not retry). All 527 pre-existing tests still pass.
      Flip TASK-C02 [x] in TASKS.md and append STATUS.md entry. — deps: none

- [x] TASK-C03: list_staged subcommand — add --list-staged flag to praxis/__main__.py.
      In _parse_mode(), add: if "--list-staged" in argv: return "list_staged".
      In main(), add a "list_staged" branch that calls a new _run_list_staged(workspace_root)
      helper. _run_list_staged() scans these locations inside .praxis/staging/:
        1. external_actions.jsonl — count and list pending entries (provider, action, queued_at)
        2. slack/messages/ — count staged message files
        3. slack/approvals/ — count staged approval files
        4. drafts/ — count staged email drafts (*.eml)
        5. events/ — count staged calendar events (*.ics)
      Print a summary table (no interactive prompt, no file modification, just display).
      Add/update tests in tests/test_main.py (≥4 new tests: --list-staged parses correctly,
      empty staging prints "No staged items", external_actions entries shown, slack messages
      shown). All 527 pre-existing tests still pass.
      Flip TASK-C03 [x] in TASKS.md and append STATUS.md entry. — deps: none

## Option A — Telemetry & Observability

- [x] TASK-A01: Create praxis/runtime/telemetry.py — TelemetryEvent dataclass (tool_name,
      latency_ms: float, hook_result: str, caller: str, token_count: int|None,
      timestamp: str ISO8601). TelemetryStore class: thread-safe (threading.Lock);
      get_global() classmethod returns a module-level singleton; record(event) appends to
      in-memory deque (maxlen=1000) and appends JSON line to .praxis/logs/telemetry.jsonl
      (creates dir if absent); counters: tool_call_count, hook_block_count,
      circuit_breaker_trips (all int, increment via record() based on hook_result=="blocked"
      and a separate record_circuit_breaker_trip() method); get_recent(n=100) returns last
      n events; get_counts() returns dict with the three counters; reset() for testing.
      Also update praxis/runtime/__init__.py to export TelemetryStore and TelemetryEvent.
      Create tests/test_telemetry.py (≥15 tests, all mocked — no real file I/O required;
      use tmp_path fixture for log file tests): record appends event, hook_block_count
      increments on "blocked", tool_call_count increments on every record, get_recent
      returns last n, get_global returns same instance, log file written correctly,
      circuit_breaker_trips increments via record_circuit_breaker_trip, reset clears state,
      thread-safe concurrent writes don't corrupt counters, TelemetryEvent timestamp is ISO8601.
      All 527 pre-existing tests still pass. Flip TASK-A01 [x] in TASKS.md and append
      STATUS.md entry. — deps: none

- [x] TASK-A02: Wire telemetry into run_loop() — in praxis/runtime/claude_code.py and
      praxis/runtime/openai_base.py, import TelemetryStore from .telemetry. In run_loop(),
      for every tool call: (a) record start_time = time.monotonic() before the tool
      executes, (b) after the result is available compute latency_ms = (time.monotonic()
      - start_time) * 1000, (c) call TelemetryStore.get_global().record(TelemetryEvent(
        tool_name=<tool name from call>, latency_ms=latency_ms,
        hook_result="allowed" (tool ran) or "blocked" (hook returned non-zero),
        caller="ClaudeCodeRuntime" or "OpenAIBaseRuntime",
        token_count=<from response.usage if available else None>,
        timestamp=datetime.now(timezone.utc).isoformat())).
      Guard with try/except so a telemetry failure never breaks the main loop.
      Add tests to tests/test_telemetry.py or tests/test_runtime.py (≥4 new tests):
      ClaudeCodeRuntime.run_loop records event after tool call; OpenAIBaseRuntime records
      event; hook_result="blocked" when hook rejects; token_count populated from usage.
      All 527 pre-existing tests still pass. Flip TASK-A02 [x] in TASKS.md and append
      STATUS.md entry. — deps: TASK-A01

- [x] TASK-A03: /metrics endpoint on MCP server (Prometheus format) — add a /metrics
      GET route to the Starlette app in praxis/mcp_server.py. The handler calls
      TelemetryStore.get_global().get_counts() and get_recent() to build a Prometheus
      text-format response. Expose these metrics:
        praxis_tool_calls_total (counter)
        praxis_hook_blocks_total (counter)
        praxis_circuit_breaker_trips_total (counter)
        praxis_tool_latency_seconds (summary: p50/p95/p99 from recent latencies)
      Return Content-Type: text/plain; version=0.0.4. Import TelemetryStore from
      praxis.runtime.telemetry. Guard import with try/except ImportError (same pattern
      as other optional imports in mcp_server.py). Add tests to tests/test_mcp.py
      (≥4 new tests): /metrics returns 200 with correct Content-Type; counter lines
      present in response; p50 latency line present; TelemetryStore import error
      returns graceful fallback (empty metrics, not 500). All 527 pre-existing tests
      still pass. Flip TASK-A03 [x] in TASKS.md and append STATUS.md entry.
      — deps: TASK-A01

- [x] TASK-A04: Scribe pass — after TASK-A01, A02, A03, C01, C02, C03 complete:
      (1) Update CLAUDE.md: add telemetry.py to praxis/runtime/ layout entry; add
          Phase T / Option A conventions section (TelemetryStore singleton, log path,
          /metrics endpoint, wired into runtimes); update test count (527→new total);
          add PRAXIS_MAX_CONCURRENT_TASKS and --list-staged to relevant sections.
      (2) Update README.md: add Telemetry section (what's logged, /metrics endpoint,
          how to query); add --list-staged to CLI reference.
      (3) Overwrite .praxis/memory/morning-handoff.md with Option C + Option A
          completion summary, verified test count, next milestone options.
      Flip TASK-A04 [x] in TASKS.md and append STATUS.md entry. — deps: TASK-A01,
      TASK-A02, TASK-A03, TASK-C01, TASK-C02, TASK-C03

## Option D — Convergence routing for queue tasks

- [x] TASK-D01: Task-type-based convergence routing — extend praxis/convergence.py
      (TaskTypeRule dataclass, TASK_TYPE_KEYWORDS dict, detect_task_type() keyword
      matcher, task_type_rules field on ConvergenceConfig, runtime_for_task_type(),
      model_for_task_type(), updated needs_local/claude/cloud()); update
      praxis/queue_runner.py (_create_runtimes_for_queue returns 3-tuple,
      _run_single_task accepts conv/all_runtimes/config kwargs and routes accordingly);
      create convergence.yaml at workspace root; create
      tests/test_task_type_routing.py (34 tests). 648 total tests pass.
      — completed 2026-05-27

## Option F — Auth rotation hardening

- [x] TASK-F01: Auth rotation hardening — create praxis/runtime/auth.py with
      parse_jwt_expiry(), check_token_expiry(), KNOWN_CREDENTIALS, build_credential_inventory()
      (metadata-only, never leaks credential values), write_credential_inventory()
      (to .praxis/security/credentials.json), warn_near_expiry(),
      graceful_auth_error_message(auth_method). Update claude_code.py and cloud.py
      to use graceful_auth_error_message(). Add _run_credential_check() to __main__.py
      wired into interactive/queue/daemon modes. Add .praxis/security/ to .gitignore.
      Tests: tests/test_auth_rotation.py (36 tests, all passing). 614 total tests pass.
      — completed 2026-05-27

## Option E — Wiki Phase 2

- [x] OPTION-E: Wiki Phase 2 enhancements — (1) prefix/suffix entity resolution step added
      between alias and Jaro-Winkler; multi-word JW threshold 0.85, single-word 0.92;
      (2) export_graph() public function writes wiki/graph.json (nodes+edges for non-superseded
      pages); (3) multi-source merge in ingest(): different raw files for same entity are merged
      under ## Source: headings with source_hashes frontmatter tracking;
      (4) stale_facts default changed 365→90 days; LintReport.stale_facts now list[dict]
      with page/days_since_update/valid_from; (5) TestWikiPhase2 class (14 tests) in
      tests/test_wiki.py; 674 total tests pass.
      — completed 2026-05-28

## Option G — Observability Dashboard

- [x] OPTION-G: GET /dashboard endpoint on MCP server — HTML observability dashboard
      showing last 50 tool calls, counters (tool_calls_total/hook_blocks/circuit_breaker_trips),
      p50/p95/p99 latency, queue depth (pending+running), credential expiry status.
      Auto-refreshes every 10s via meta refresh. Inline CSS only (no external CDN).
      Read-only — no forms, no write actions. Credential display: name/configured/near_expiry/expires_at
      only (never raw token values). Added /dashboard Route to Starlette app in start().
      Added TestMCPDashboard class (12 tests) to tests/test_mcp.py; 41 total mcp tests pass.
      — completed 2026-05-27

## Option I — Scheduled Triggers

- [x] TASK-I01: Scheduler core — create praxis/scheduler.py.
      ScheduledTask dataclass: id (str), name (str), prompt (str), schedule (cron expression str),
      enabled (bool, default True), last_run (str|None ISO8601), next_run (str|None ISO8601),
      created_at (str ISO8601).
      CronScheduler class: __init__(queue: TaskQueue, schedule_file: Path, log_file: Path);
      load() reads .praxis/schedule/tasks.json (creates empty [] if absent); save() writes back;
      add_task(name, schedule, prompt) → ScheduledTask with uuid4 id + next_run computed via croniter;
      remove_task(id); enable_task(id); disable_task(id); list_tasks() → list[ScheduledTask];
      tick() — evaluate which enabled tasks are due (next_run <= now()), for each: if task not
      already pending/running in queue (dedup check), append to TaskQueue, update last_run+next_run,
      save(); croniter import guard: if croniter not installed raise ImportError with clear install
      message ("pip install praxis[scheduler]").
      Update pyproject.toml: add [scheduler] = ["croniter>=1.0"]; add "praxis[scheduler]" to [all].
      — deps: none

- [x] TASK-I02: Scheduler daemon integration — wire CronScheduler into daemon.py and queue_runner.py.
      In praxis/queue_runner.py: import CronScheduler; add _start_scheduler_thread(queue, workspace_root)
      helper that creates CronScheduler (schedule_file=.praxis/schedule/tasks.json,
      log_file=.praxis/logs/scheduler.log), calls scheduler.load(), starts a daemon thread that
      loops: scheduler.tick(), sleep(poll_interval); poll_interval from PRAXIS_SCHEDULER_POLL_INTERVAL
      env var (default 60); thread is a threading.Thread(daemon=True) so it stops with the process;
      log scheduler dispatches to log_file (append "YYYY-MM-DD HH:MM:SSZ DISPATCH task_name" lines);
      wrap croniter import error: if scheduler unavailable, log warning and skip (don't crash queue).
      Call _start_scheduler_thread() at top of run_queue_loop() before the main while-loop.
      In praxis/daemon.py: no changes needed (daemon forks to background, scheduler thread runs
      inside the queue_runner process).
      — deps: TASK-I01

- [x] TASK-I03: CLI commands — wire schedule management into praxis/__main__.py.
      Add to _parse_mode(): --schedule-add → "schedule_add"; --schedule-list → "schedule_list";
      --schedule-enable → "schedule_enable"; --schedule-disable → "schedule_disable";
      --schedule-remove → "schedule_remove".
      Add to main(): five new branches:
        "schedule_add": argv must have 3 positional args after flag (name, cron, prompt);
          validate cron via croniter (clear error if invalid); create CronScheduler, load(),
          add_task(), save(); print confirmation with id + next_run.
        "schedule_list": create CronScheduler, load(), print table of all tasks
          (id, name, schedule, enabled, next_run, last_run).
        "schedule_enable" / "schedule_disable": next arg is task id; load(), toggle, save();
          print confirmation.
        "schedule_remove": next arg is task id; load(), remove, save(); print confirmation.
      Import guard: if croniter not installed, print install message and sys.exit(1).
      — deps: TASK-I01

- [x] TASK-I04: Built-in useful schedules — document in .env.example.
      Append a "Scheduled Triggers (Option I)" section to .env.example documenting:
        PRAXIS_SCHEDULER_POLL_INTERVAL=60   # seconds between scheduler ticks (default 60)
      And three commented example --schedule-add commands (NOT hardcoded defaults that run
      automatically — these are suggestions the human can copy-paste):
        # Morning briefing (7am daily): runs wiki query for today's priorities
        # python -m praxis --schedule-add 'morning-briefing' '0 7 * * *' 'wiki query: what are my priorities for today?'
        # Linear sync (hourly, 9am-6pm weekdays): check new issues
        # python -m praxis --schedule-add 'linear-sync' '0 9-18 * * 1-5' 'linear list_issues: assigned to me, status todo'
        # Weekly wiki lint (Sunday 9am): surface stale pages via Slack
        # python -m praxis --schedule-add 'weekly-lint' '0 9 * * 0' 'wiki lint: report stale facts and orphan pages'
      — deps: none (documentation only)

- [x] TASK-I05: Tests — create tests/test_scheduler.py.
      ≥25 tests, all mocked (no real croniter calls needed for most; use real croniter for cron
      expression parsing tests — it's a pure computation, no I/O).
      TestScheduledTask (3 tests): dataclass fields, defaults, serialization to dict.
      TestCronSchedulerLoad (4 tests): empty file creates [], valid JSON loads tasks, corrupt JSON
        raises informative error, creates .praxis/schedule/ dir if absent.
      TestCronSchedulerDueTasks (5 tests): task with next_run in past is due; task with next_run
        in future is not; disabled task never dispatched; task already pending in queue is skipped
        (duplicate prevention); task already running in queue is skipped.
      TestCronSchedulerAddRemove (4 tests): add_task writes correct ScheduledTask with valid next_run;
        remove_task removes by id; enable/disable toggle enabled field; remove unknown id raises KeyError.
      TestCronSchedulerThread (3 tests): _start_scheduler_thread starts a daemon thread; thread calls
        tick() on interval; missing croniter logs warning instead of crashing queue.
      TestSchedulerCLI (6 tests): --schedule-add parses args and calls add_task; --schedule-list prints
        all tasks; --schedule-enable toggles enabled=True; --schedule-disable toggles enabled=False;
        --schedule-remove calls remove_task; invalid cron expression prints error and exits nonzero.
      674 existing tests must still pass.
      — deps: TASK-I01, TASK-I02, TASK-I03, TASK-I04

- [x] TASK-I06: Scribe pass — update documentation for Option I completion.
      (1) Update CLAUDE.md: add praxis/scheduler.py to repo layout entry; add scheduler
          conventions section (ScheduledTask dataclass, CronScheduler class, tick() dedup logic,
          thread model, log format, [scheduler] optional dep, PRAXIS_SCHEDULER_POLL_INTERVAL).
      (2) Update README.md: add "Scheduled Triggers" section (what it does, quickstart:
          pip install praxis[scheduler], --schedule-add examples, daemon auto-starts scheduler).
      (3) Confirm .env.example has PRAXIS_SCHEDULER_POLL_INTERVAL (added in I-04).
      (4) Overwrite .praxis/memory/morning-handoff.md with Option I completion summary:
          what was built, final test count, audit checklist, next options.
      Append STATUS.md entry. Flip all TASK-I01 through TASK-I06 [x] in TASKS.md.
      — deps: TASK-I05

## Option J — Wiki to Notion and Linear sync
- [x] TASK-J01: export_notion/export_linear added to praxis/wiki.py — deps: none
- [x] TASK-J02: praxis/integrations/wiki_sync.py created (sync_to_notion, sync_to_linear, link_linear_issue, execute_wiki_sync) — deps: J01
- [x] TASK-J04: pull_linear_updates added to wiki_sync.py — deps: J02
- [x] TASK-J03: CLI commands wired into __main__.py — deps: J02
- [x] TASK-J05: tests/test_wiki_sync.py — deps: J01, J02, J03, J04
- [x] TASK-J06: Scribe pass — deps: J05

## Setup Wizard (S-01 through S-05) — completed 2026-05-28

- [x] TASK-S-01: praxis/setup_wizard.py — 10-step interactive wizard (runtime, workspace, Slack, GitHub, web, email, cost, schedule, wiki, summary); all credential inputs via getpass; merge/overwrite .env modes — deps: none
- [x] TASK-S-02: Wire --setup into praxis/__main__.py — _parse_mode + main() setup branch with existing-.env handling — deps: TASK-S-01
- [x] TASK-S-03: .env handling — merge mode, getpass for credentials, gitignore warning, _env_mode param — deps: TASK-S-01
- [x] TASK-S-04: tests/test_setup_wizard.py — 32 tests (9 classes); all mocked; 767 total — deps: TASK-S-01, TASK-S-02, TASK-S-03
- [x] TASK-S-05: Scribe pass — README + CLAUDE.md + morning-handoff.md updated — deps: TASK-S-04
