# Session Log

### GITIGNORE-HARDENING (completed 2026-05-28)
- Updated .gitignore: added personal wiki content section (wiki/raw/*, wiki/pages/*, wiki/graph.json, wiki/index.md, wiki/log.md); added .praxis/memory/* with !.gitkeep negation; changed existing staging/queue/logs/security patterns to /* + !.gitkeep so stubs are trackable; added .praxis/schedule/*
- Added .gitkeep stubs: .praxis/staging/, .praxis/queue/ (new dir), .praxis/logs/, .praxis/security/ (new dir), wiki/raw/, wiki/pages/ — structure survives fresh clone without personal content
- Untracked 30 previously-committed personal data files via git rm --cached (local copies preserved); 767 tests still pass; pushed to main as commit 9bde9b8

### ORCHESTRATOR-CYCLE-S-WIZARD (completed 2026-05-28)
- Setup wizard (S-01 through S-05) fully delivered
- Audit checklist: 735 pre-existing tests ✅ | hook md5 057f07f... ✅ | curl blocked ✅ | 32 new tests | 767 total ✅
- Net additions: praxis/setup_wizard.py, --setup flag in __main__.py, tests/test_setup_wizard.py (32 tests), README + CLAUDE.md updated, morning-handoff.md refreshed

### TASK-S-05 (completed 2026-05-28)
- Updated README.md: setup wizard as first command in quickstart; updated test count 735→767
- Updated CLAUDE.md: added setup_wizard.py to repo layout; added Setup wizard conventions section; updated test count 735→767
- Overwrote .praxis/memory/morning-handoff.md: setup wizard completion summary, 767 tests, audit checklist, next options (B/H/K/L)

### TASK-S-04 (completed 2026-05-28)
- Created tests/test_setup_wizard.py: 32 tests across 9 classes (TestReadWriteEnv×5, TestRuntimeChoice×4, TestInvalidRuntimeChoice×2, TestMergeModeDoesNotOverwrite×3, TestGetpassUsedForCredentials×3, TestOptionalSteps×5, TestCostCircuitBreaker×2, TestWikiSeed×2, TestSummaryOutput×2, TestMainSetupMode×4); all mocked (_input/_getpass injection pattern; tmp_path for file I/O)
- Full suite: 767 passed, 0 failed (735 pre-existing + 32 new)

### TASK-S-03 (completed 2026-05-28)
- .env handling: _write_env() with merge/overwrite modes; merge only appends missing keys; all credential inputs via getpass.getpass(); gitignore check in step 10; _env_mode param on run_wizard()

### TASK-S-02 (completed 2026-05-28)
- Wired --setup into praxis/__main__.py: _parse_mode() returns "setup"; main() handles existing .env with overwrite/merge/cancel prompt; calls run_wizard(workspace_root, env_file=env_file, _env_mode=mode)

### TASK-S-01 (completed 2026-05-28)
- Created praxis/setup_wizard.py: run_wizard() with 10 steps; _read_env()/_write_env() helpers; _safe_input()/_safe_getpass() for test injection; all optional steps wrapped in try/except; direct CronScheduler import in step 8 (no subprocess); shutil.copy2 for wiki seed in step 9
- 735 pre-existing tests still pass

### TASK-J06 (completed 2026-05-28)
- Updated CLAUDE.md: added wiki_sync.py to integrations layout; added Wiki sync conventions section; updated test count 703→735
- Updated README.md: added Wiki → Notion/Linear Sync (Option J) section; updated test count 703→735
- Overwrote .praxis/memory/morning-handoff.md: Option J completion summary, 735 tests, audit checklist, next options (B/H/K/L)

### ORCHESTRATOR-CYCLE-J (completed 2026-05-28)
- Option J (Wiki → Notion/Linear Sync) fully delivered: J-01 through J-06 all complete
- Audit checklist: 703 pre-existing tests ✅ | hook md5 057f07f... ✅ | curl blocked ✅ | 32 new tests | 735 total ✅
- Net additions: export_notion/export_linear in praxis/wiki.py, praxis/integrations/wiki_sync.py (new), 3 CLI flags in __main__.py, wiki_updates.jsonl in --list-staged, tests/test_wiki_sync.py (32 tests)

### TASK-J05 (completed 2026-05-28)
- Created tests/test_wiki_sync.py: 32 tests across 7 classes (TestExportNotion x5, TestExportLinear x5, TestSyncToNotion x4, TestSyncToLinear x4, TestLinkLinearIssue x4, TestPullLinearUpdates x5, TestWikiSyncCLI x5)
- All new tests pass; full suite: 735 passed, 0 failed (703 pre-existing + 32 new)
- urlopen patched at praxis.integrations.wiki_sync.urlopen; context-manager mock uses __enter__/__exit__; clear-env pattern used for API key absence test

### TASK-J01-J02-J04 (completed 2026-05-28)
- Added export_notion() and export_linear() as public functions at end of praxis/wiki.py
- Created praxis/integrations/wiki_sync.py: sync_to_notion, sync_to_linear, link_linear_issue, pull_linear_updates, execute_wiki_sync, SCHEMAS, IMPLEMENTATIONS
- All write operations stage to .praxis/staging/ — no live API calls
- 703 pre-existing tests still pass

### TASK-J03 (completed 2026-05-28)
- Updated praxis/__main__.py: added --wiki-sync-notion, --wiki-sync-linear, --wiki-link-issue to _parse_mode()
- Added 3 new mode branches in main() for each CLI command (lazy-imports from .integrations.wiki_sync)
- Updated _run_list_staged() to scan .praxis/staging/wiki_updates.jsonl (section 6) and display pending wiki update proposals
- 703 pre-existing tests still pass

### ORCHESTRATOR-CYCLE-I (completed 2026-05-28)
- Option I (Scheduled Triggers) fully delivered: I-01 through I-06 all complete
- Audit checklist passed: 703 tests ✅ | hook md5 057f07f... ✅ | curl blocked ✅ | CronScheduler importable ✅ | --schedule-list works ✅
- Net additions: praxis/scheduler.py, tests/test_scheduler.py (29 tests), queue_runner.py+__main__.py wired, .env.example + pyproject.toml updated, CLAUDE.md + README.md updated, morning-handoff.md refreshed

### TASK-I06 (completed 2026-05-28)
- Updated CLAUDE.md: added scheduler.py to repo layout; added "Scheduler conventions (Option I)" section; updated test count 674→703
- Updated README.md: added "Scheduled Triggers (Option I)" section with quickstart, CLI reference, dedup note; updated test counts 674→703 and 578→703
- Confirmed .env.example already has PRAXIS_SCHEDULER_POLL_INTERVAL (added by I-04)
- Overwrote .praxis/memory/morning-handoff.md: Option I completion summary, 703 tests, audit checklist, next options (B/H/J/K)

### TASK-I05 (completed 2026-05-28)
- Created tests/test_scheduler.py: 29 tests across 6 classes (TestScheduledTask×3, TestCronSchedulerLoad×4, TestCronSchedulerDueTasks×5, TestCronSchedulerAddRemove×4, TestSchedulerThread×3, TestSchedulerCLI×6 plus 4 extra CLI tests); all mocked (croniter mocked via praxis.scheduler._compute_next_run patch; ImportError path uses builtins.__import__ mock); dedup prevention tested for pending and running queue states; daemon thread verified via threading.Thread.start capture; CLI parse modes all verified; schedule_add/remove/enable/disable CLI branches also smoke-tested
- Full suite: 703 passed, 0 failed (674 pre-existing + 29 new)

### TASK-I04 (completed 2026-05-28)
- Updated .env.example: appended "Scheduled Triggers (Option I)" section with PRAXIS_SCHEDULER_POLL_INTERVAL=60, three commented example --schedule-add commands (morning-briefing, linear-sync, weekly-wiki-lint), and schedule management CLI reference
- No Python code changes; file is documentation only; 674 existing tests unaffected

### TASK-I03 (completed 2026-05-28)
- Updated praxis/__main__.py: added 5 schedule CLI modes to _parse_mode() (schedule_add/list/enable/disable/remove); added corresponding branches in main(); _make_scheduler() helper constructs CronScheduler with correct paths (queue dir, schedule file, log file) and calls load(); --schedule-add parses positional args after flag, validates cron expression (ValueError → clean error, sys.exit(1)); --schedule-list prints a formatted table with ID/Name/Schedule/Enabled/Next Run/Last Run columns; --schedule-enable and --schedule-disable find task_id from next argv element, call enable_task()/disable_task(), handle KeyError with clean message; --schedule-remove follows same pattern with remove_task(); all CronScheduler imports kept lazy inside branches; 674 existing tests still pass (0 regressions)

### TASK-I02 (completed 2026-05-28)
- Updated praxis/queue_runner.py: added `import threading`; added `_start_scheduler_thread(queue, workspace_root)` helper that lazy-imports `CronScheduler` (graceful ImportError → warning to stderr, no crash); reads `PRAXIS_SCHEDULER_POLL_INTERVAL` (default 60); creates `CronScheduler` with `schedule_file=workspace/.praxis/schedule/tasks.json` and `log_file=workspace/.praxis/logs/scheduler.log`; calls `scheduler.load()`; starts `daemon=True` thread that calls `scheduler.tick()` then `threading.Event.wait(timeout=poll_interval)` (Event.wait instead of time.sleep to avoid interference with patched time.sleep in existing tests); unexpected exceptions in tick() are caught and logged to stderr; called at top of `run_queue_loop()` after queue/cp_store setup
- No changes to daemon.py (scheduler thread lives inside queue runner process; daemon fork handles backgrounding)
- 674 existing tests still pass (0 regressions)

### TASK-I01 (completed 2026-05-28)
- Created praxis/scheduler.py: ScheduledTask dataclass, CronScheduler class with load/save/add_task/remove_task/enable_task/disable_task/list_tasks/tick() methods; croniter import guard with clear install message; dedup via queue task list; log dispatch to log_file
- Updated pyproject.toml: added [scheduler] = ["croniter>=1.0"]; added "praxis[scheduler]" to [all]
- tick() checks pending+running tasks to prevent duplicate queue entries; save() uses atomic write (tmp file + rename); all croniter calls lazy-guarded; 674 existing tests still pass

### TASK-I01 (completed 2026-05-28)
- Created praxis/scheduler.py: ScheduledTask dataclass, CronScheduler class with load/save/add_task/remove_task/enable_task/disable_task/list_tasks/tick() methods; croniter import guard with clear install message; dedup via queue task list; log dispatch to log_file
- Updated pyproject.toml: added [scheduler] = ["croniter>=1.0"]; added "praxis[scheduler]" to [all]
- tick() checks pending+running tasks to prevent duplicate queue entries; save() uses atomic write (tmp file + rename); all croniter calls lazy-guarded; 674 existing tests still pass

### SCRIBE-EG (completed 2026-05-28)
- Updated CLAUDE.md: added export_graph() to wiki conventions; enhanced entity resolution details (prefix/suffix step, multi-word JW 0.85); updated stale_facts docs (90 days, list[dict]); added /dashboard to MCP gateway conventions; updated test count 648→674
- Overwrote .praxis/memory/morning-handoff.md: Option E + G completion summary, 674 tests, audit checklist, 4 next options (B/H/I/J)
- No code changes; 674 tests unaffected


### OPTION-E (completed 2026-05-28)
- Enhanced entity resolution in praxis/wiki.py: prefix/suffix check step added between alias and Jaro-Winkler (min prefix length 3 chars; multiple prefix matches = WikiAmbiguousEntityError); multi-word Jaro-Winkler threshold lowered to 0.85 (single-word keeps 0.92).
- Added export_graph() to praxis/wiki.py: reads non-superseded pages → nodes+edges dict, writes wiki/graph.json (JSON, indent=2); private _write_graph_json() helper; json import added at top of module.
- Multi-source merge: ingest() now tracks per-source content hashes in source_hashes frontmatter field (JSON-encoded string); when a DIFFERENT file/source contributes content for the same entity, the body is merged under "## Source: <filename>" headings instead of superseding; idempotency preserved per-source.
- Staleness default changed 365→90 days in lint(); LintReport.stale_facts changed from list[str] to list[dict] with page/days_since_update/valid_from keys; _lint_stale_facts() updated accordingly.
- Added TestWikiPhase2 class (14 tests) to tests/test_wiki.py; updated test_has_findings_true_for_each_category to use dict for stale_facts; 674 total tests pass (648 pre-existing + 26 new).

### OPTION-G (completed 2026-05-27)
- Added GET /dashboard route to praxis/mcp_server.py (Starlette, no new deps); defined `dashboard_endpoint` as a closure inside `start()` following the same pattern as `metrics_endpoint`
- Dashboard shows: last 50 tool calls (newest first), counters (tool_calls_total/hook_blocks/circuit_breaker_trips), p50/p95/p99 latency in ms, queue depth (pending+running from tasks.jsonl), credential expiry status (from .praxis/security/credentials.json)
- Auto-refreshes every 10s via `<meta http-equiv="refresh" content="10">`; read-only (no forms, no write actions); inline CSS only (dark theme, no external CDN); credential display shows only name/configured/near_expiry/expires_at — never raw token values
- Added TestMCPDashboard class (12 tests) to tests/test_mcp.py; all 41 tests in test_mcp.py pass; full suite: 655 passed, 5 failed (the 5 failures are pre-existing wiki TestSupersede failures unrelated to this task)

### SCRIBE-FD (completed 2026-05-27)
- Updated CLAUDE.md: added auth.py to runtime/ layout; added Option F + Option D conventions sections; updated test count 578→648.
- Overwrote .praxis/memory/morning-handoff.md: Option F + Option D completion summary, 648 tests, audit checklist, four next-milestone options (B/E/G/H).
- No code changes; 648 tests unaffected.

### TASK-D01 (completed 2026-05-27)
- Extended praxis/convergence.py: added TaskTypeRule dataclass, TASK_TYPE_KEYWORDS dict, detect_task_type() keyword-match function (no LLM), task_type_rules field on ConvergenceConfig, runtime_for_task_type()/model_for_task_type() methods, updated needs_local/claude/cloud() to also check task_type_rules.
- Updated praxis/queue_runner.py: _create_runtimes_for_queue() now returns 3-tuple (default, overrides, all_runtimes); _run_single_task() accepts conv/all_runtimes/config kwargs and routes to a task-type-specific Orchestrator when the rule differs from the default; run_queue_loop() passes all three to _run_single_task().
- Created convergence.yaml at workspace root with all task types routing to claude (ready for user customization); created tests/test_task_type_routing.py with 34 tests (all passing); fixed existing test_queue_runner.py mocks to use 3-tuple.
- 648 total tests pass, 0 failed; no regressions in test_convergence.py or test_queue_runner.py.

### TASK-F01 (completed 2026-05-27)
- Created praxis/runtime/auth.py: parse_jwt_expiry(), check_token_expiry(), KNOWN_CREDENTIALS list (10 entries), build_credential_inventory() (metadata-only, never leaks values), write_credential_inventory() (to .praxis/security/credentials.json), warn_near_expiry(), graceful_auth_error_message() for oauth/cloud/local/api_key methods.
- Updated praxis/runtime/claude_code.py: AuthenticationError handler now calls graceful_auth_error_message(self.auth_method) for context-aware error messages.
- Updated praxis/runtime/cloud.py: AuthenticationError handler now calls graceful_auth_error_message("cloud").
- Updated praxis/__main__.py: added _run_credential_check(workspace_root) helper (inventory build + write + near-expiry warnings to stderr + optional Slack notify); wired into interactive, queue, and daemon mode branches after Config.from_env().
- Added .praxis/security/ to .gitignore (was already present at line 33); created tests/test_auth_rotation.py: 36 tests across 6 classes, all passing; full suite: 614 passed, 0 failed (578 pre-existing + 36 new).

### TASK-A04 (completed 2026-05-27)
- Updated CLAUDE.md: added telemetry.py to runtime/ layout; added Phase T conventions section; added --list-staged to __main__.py description; updated playwright.py entry with retry×3 note; added PRAXIS_MAX_CONCURRENT_TASKS to Running section and queue runner Key convention; updated PREREQ-2 note to COMPLETE; updated test count 527→578.
- Updated README.md: added Phase T Telemetry section (JSONL log, Prometheus /metrics endpoint); added --list-staged to Phase X approve block; updated test count 527→578; Scout/Planner tool table already reflected Bash removal.
- Overwrote .praxis/memory/morning-handoff.md: Option C + Option A completion summary, 578 tests, audit checklist, four next-milestone options (B/D/E/F).
- 578 tests pass, 0 failed; no code changes.

### TASK-A03 (completed 2026-05-27)
- Updated praxis/mcp_server.py: added `metrics_endpoint` async handler inside `start()` that imports TelemetryStore from `.runtime.telemetry`, calls `get_counts()` and `get_recent(1000)`, computes p50/p95/p99 latency quantiles, and returns Prometheus text-format body with counters (praxis_tool_calls_total, praxis_hook_blocks_total, praxis_circuit_breaker_trips_total) and summary (praxis_tool_latency_seconds); entire body is wrapped in `try/except Exception` so fallback is `"# telemetry unavailable\n"` with no 500 error.
- Added `/metrics` Route to the Starlette app alongside `/sse` and `/messages`; updated startup stderr log to include metrics URL.
- Added `TestMCPMetrics` class (5 tests) to tests/test_mcp.py: test_metrics_returns_200, test_metrics_content_type, test_metrics_contains_counter_lines, test_metrics_contains_latency_lines, test_metrics_telemetry_unavailable_returns_fallback; tests use `starlette.testclient.TestClient` by capturing the app from a fake `uvicorn.run`.
- Full suite: 578 passed, 0 failed (569 pre-existing + 9 new from this and TASK-A02 recorded here).

### TASK-A02 (completed 2026-05-27)
- Updated praxis/runtime/claude_code.py: added timing + telemetry recording in execute_tool() — wraps each tool_use block with time.monotonic() before/after tool_executor call, then records TelemetryEvent(tool_name, latency_ms, hook_result, caller="ClaudeCodeRuntime", token_count=None, timestamp) into TelemetryStore.get_global(); entire telemetry block is guarded by try/except so failures are silent.
- Updated praxis/runtime/openai_base.py: same pattern in execute_tool() for the dict/object tool-call branch — timing before/after tool_executor(), telemetry recorded with caller="OpenAIBaseRuntime"; guarded try/except; hook_result determined by whether output starts with "BLOCKED by §5".
- Added TestTelemetryWiring class (4 tests) to tests/test_telemetry.py: test_claude_execute_tool_records_event, test_claude_execute_tool_records_blocked, test_openai_execute_tool_records_event, test_telemetry_failure_does_not_break_tool_execution; also added imports for ClaudeCodeRuntime and OpenAIBaseRuntime at top of file.
- Full suite: 573 passed, 0 failed (569 pre-existing + 4 new).

### TASK-A01 (completed 2026-05-27)
- Created praxis/runtime/telemetry.py: TelemetryEvent dataclass (tool_name, latency_ms, hook_result, caller, token_count, timestamp) with to_dict(); TelemetryStore class with thread-safe deque ring buffer (maxlen=1000), append-only JSONL log, counters (tool_call_count, hook_block_count, circuit_breaker_trips), get_global() singleton, reset_global() for tests, record(), record_circuit_breaker_trip(), get_recent(n), get_counts(), reset().
- Updated praxis/runtime/__init__.py: added `from .telemetry import TelemetryEvent, TelemetryStore` and both names to __all__.
- Created tests/test_telemetry.py: 28 tests across 5 classes (TestTelemetryEvent×4, TestTelemetryStoreRecord×10, TestTelemetryStoreSingleton×5, TestTelemetryStoreLogFile×6, TestTelemetryStoreConcurrency×3); all pass; autouse fixture resets global singleton between tests.
- Full suite: 569 passed, 0 failed (541 pre-existing + 28 new).

### TASK-C02 (completed 2026-05-27)
- Updated praxis/integrations/playwright.py: added `import time`; renamed `_run_playwright_script` to `_run_playwright_script_once` (added subprocess crash detection via empty stdout + non-zero returncode); added `_RETRY_DELAYS`, `_clean_playwright_error`, and new `_run_playwright_script` wrapper with up to 3 retries and [1, 2, 4]s backoff; PLAYWRIGHT_IMPORT_ERROR skips retry; applied `_clean_playwright_error` in `_fetch()` and `_screenshot()`.
- Added 4 new tests to `TestPlaywright` in tests/test_phase_x.py: test_retry_succeeds_on_second_attempt, test_retry_exhausted_returns_last_error, test_import_error_does_not_retry, test_clean_error_message_no_traceback.
- Full suite: 541 passed, 0 failed (all pre-existing tests green, 4 new tests added).

### TASK-C03 (completed 2026-05-27)
- Added `--list-staged` to `_parse_mode()` (returns `"list_staged"`) in `praxis/__main__.py`
- Added `_run_list_staged(workspace_root)` helper that scans `.praxis/staging/` for external_actions.jsonl (pending entries), slack/messages/, slack/approvals/, drafts/*.eml, events/*.ics — read-only, no prompts
- Added `elif mode == "list_staged":` branch in `main()` that calls `_run_list_staged(config.workspace_root)`
- Added 6 new tests in `TestListStaged` class in `tests/test_main.py`; full suite: 541 passed, 0 failed

### TASK-C01 (completed 2026-05-27)
- Updated praxis/queue_runner.py: added `max_concurrent = int(os.environ.get("PRAXIS_MAX_CONCURRENT_TASKS", "3"))` after poll_interval; added startup log line; added rate-limit guard at top of while loop checking `queue.stats().get("running", 0) >= max_concurrent` before calling `queue.next_pending()`.
- Added `TestRateLimiting` class (4 tests) to tests/test_queue_runner.py: test_rate_limit_respected, test_rate_limit_allows_below_cap, test_rate_limit_env_var_override, test_rate_limit_default_is_3.
- All 531 tests pass (527 pre-existing + 4 new); no regressions.

### TASK-C04 (completed 2026-05-27)
- PREREQ-2 verification: read .claude/agents/scout.md line 4 = `tools: Read, Grep, Glob` — Bash ABSENT ✅
- Read .claude/agents/planner.md line 4 = `tools: Read, Grep, Glob` — Bash ABSENT ✅
- Both Scout and Planner are confirmed read-only. No Bash in either agent post-PREREQ-2.
- No code changes; 527 tests unaffected.

### TASK-X04 (completed 2026-05-27)
- Created tests/test_phase_x.py: 37 tests across 5 classes (TestPlaywright×6, TestNotion×9, TestLinear×8, TestApproveCommand×10, TestCostEnv×4)
- All 37 new tests pass; full suite = 527 total, 0 failed
- Coverage: playwright domain check, subprocess env stripping, playwright import error; notion/linear read vs. stage distinction; staging JSONL format (uuid, provider, action, params, queued_at, status); approve approve/reject/skip flow with file rewrite; cost env propagation via CostCircuitBreaker.from_env()

### TASK-X03 (completed 2026-05-27)
- Updated praxis/integrations/__init__.py: added playwright, notion, linear imports and merged their SCHEMAS + IMPLEMENTATIONS (total integrations: 10→13)
- Updated praxis/__main__.py: added --approve mode to _parse_mode(); added _run_approve(), _execute_approved_action(), _notion_execute(), _linear_execute() module-level helpers; main() "approve" branch reads .praxis/staging/external_actions.jsonl, displays pending, prompts Y/N/skip, executes approved via urllib + domain check
- Updated .env.example: Phase X section (PRAXIS_MAX_SESSION_COST, PRAXIS_NOTION_TOKEN, PRAXIS_LINEAR_API_KEY, Playwright note) was already present from prior session; no change needed
- Updated tests/test_integrations.py: 3 assertions updated to include playwright/notion/linear names and count 10→13
- 490 tests still pass

### TASK-X02 (completed 2026-05-27)
- Created praxis/integrations/notion.py: 6 actions (3 read-only via urllib.request — search, get_page, list_databases; 3 write-escalate staged to .praxis/staging/external_actions.jsonl — create_page, update_page, append_block); domain check for api.notion.com; PRAXIS_NOTION_TOKEN guard; SCHEMAS + IMPLEMENTATIONS dicts
- Created praxis/integrations/linear.py: 6 actions (3 read-only via urllib.request GraphQL — list_issues, get_issue, list_teams; 3 write-escalate staged — create_issue, update_issue, add_comment); domain check for api.linear.app; PRAXIS_LINEAR_API_KEY guard; SCHEMAS + IMPLEMENTATIONS dicts
- Staging format: {"id": uuid4, "provider": ..., "action": ..., "params": {...}, "queued_at": ISO8601, "status": "pending"} appended to .praxis/staging/external_actions.jsonl
- Updated praxis/tools.py: added PRAXIS_NOTION_TOKEN + PRAXIS_LINEAR_API_KEY to _redact_secrets()
- 490 tests still pass (no regressions)

### TASK-P01 (completed 2026-05-27)
- Created praxis/runtime/cost.py: CostCircuitBreaker class with _MODEL_PRICING table (7 models + _DEFAULT_PRICING fallback); from_env() reads PRAXIS_MAX_SESSION_COST (default 2.00); record_call() accumulates per-call cost log; _trip() dumps JSON to .praxis/logs/cost-circuit-break-{ts}.json and calls sys.exit(3)
- Updated praxis/runtime/claude_code.py: added CostCircuitBreaker import + instantiation in __init__; added record_call() after _create_with_retry() in run_loop() using response.usage.input_tokens/output_tokens; token values cast to int with try/except guard for mock safety
- Updated praxis/runtime/openai_base.py: same pattern using response.usage.prompt_tokens/completion_tokens in run_loop() after _call_api(); same int-cast + try/except guard
- Created tests/test_cost_circuit_breaker.py: 15 tests across 4 classes; all pass; full suite 490 tests green (475 pre-existing + 15 new)

### TASK-X01 (completed 2026-05-27)
- Created praxis/integrations/playwright.py: two actions (fetch/screenshot); isolated subprocess via temp script + env-var URL passing; strips all PRAXIS_ auth tokens from subprocess env; domain allowlist check before launch; fresh browser context (no stored cookies); playwright ImportError returns clear install message; path safety check for screenshot output
- Updated pyproject.toml: added [playwright] optional dep (playwright>=1.40); added to [all]
- Does NOT wire into __init__.py (handled by TASK-X03)
- 489 tests still pass (pre-existing test_run_loop_rate_limit_retry_then_succeed failure is unrelated, in cost.py MagicMock type mismatch)

### TASK-R01 through TASK-R05 (completed 2026-05-27)
- Created Dockerfile: python:3.12-slim, git via apt, pip install ".[all]", WORKDIR /app, ENV PRAXIS_WORKSPACE_ROOT=/workspace + PRAXIS_MEMORY_ROOT, VOLUME /workspace, EXPOSE 8765, CMD --mcp
- Created docker-compose.yml: two services (mcp on port 8765 with --mcp, daemon with --queue for foreground container model), named volume workspace shared between both, env_file .env, restart unless-stopped, daemon depends_on mcp
- Created systemd/praxis.service: Type=simple, User/Group=praxis, EnvironmentFile=/etc/praxis/env, ExecStart /opt/praxis/.venv/bin/python -m praxis --queue, Restart=on-failure RestartSec=5s, NoNewPrivileges/ProtectSystem hardening
- Created install-system.sh (executable): root check, Docker install via official apt method if missing, docker build, praxis system user creation, rsync to /opt/praxis, venv + pip install [all], /etc/praxis/env template (mode 600), systemd install+enable+start, printed checklist
- Created DEPLOY.md: Docker Compose path (quick start, services table, logs, update, MCP client URL), systemd path (quick start, service management, logs, update), credentials guide (what to set, what never to do), troubleshooting (container won't start, queue not processing, MCP client can't connect, hook blocks unexpected call)
- 475 tests still pass (no Python code changed)

### REVIEWER-M-MILESTONE (completed 2026-05-27)
- Verified: all M03–M06 STATUS.md claims match real code; hook md5 057f07f223fd5b5fe11f2aa50af1e361 unchanged
- Discrepancies: none
- Corrections needed: none — Phase M fully verified

### TASK-M06 (completed 2026-05-27)
- Updated CLAUDE.md: added mcp_server.py to repo layout; updated __main__.py desc with --mcp; test count 451→475; added MCP Gateway conventions section; added --mcp running block
- Updated README.md: added Phase M section (install, what's exposed, §5 boundary, transport)
- Overwrote .praxis/memory/morning-handoff.md: Phase M complete summary + 3 next-milestone options + audit checklist
- 475 tests still pass; TASKS.md TASK-M06 flipped [x]

### TASK-M05 (completed 2026-05-27)
- Created tests/test_mcp.py: 24 tests across 7 classes (TestMCPServerInit, TestMCPHandlerDispatch, TestMCPHookIntegration, TestMCPSecretRedaction, TestMCPResourceExposure, TestMCPPortConfig, TestMCPMainEntry)
- All 475 tests pass (451 pre-existing + 24 new), 0 failed
- Coverage: schema registration (note: Agent tool is schema-only, no impl), §5 hook blocking/allowing, secret redaction pass-through, wiki resource logic, port config from env/default, --mcp flag parsing and SystemExit on missing dep
- One design note recorded: Agent is intentionally absent from TOOL_IMPLEMENTATIONS (orchestrator-dispatched); test_all_impls_registered skips it via SCHEMA_ONLY_TOOLS set

### TASK-M04 (completed 2026-05-27)
- Updated praxis/__main__.py: added --mcp check to _parse_mode() (returns "mcp"); added "mcp" mode branch to main() that imports MCPServer (with ImportError→SystemExit guard), reads PRAXIS_MCP_PORT env var (default 8765), and calls server.start(port=port)
- Updated .env.example: appended MCP Gateway section after Slack section documenting PRAXIS_MCP_PORT=8765 with install and startup instructions
- 451 pre-existing tests still pass

### TASK-M03 (completed 2026-05-27)
- Created praxis/mcp_server.py: MCPServer class; HTTP/SSE transport via low-level mcp.server.Server (not FastMCP); _register_tools() decorates list_tools/call_tool on MCPLowLevelServer; _register_resources() exposes wiki/pages/ as wiki://pages/{slug} MCP Resources; _make_handler() fires §5 escalation-boundary hook before every tool dispatch; start() resolves port from arg/PRAXIS_MCP_PORT env/default 8765; sync impls run in thread pool via run_in_executor.
- Updated pyproject.toml: added [mcp] = ["mcp>=1.0","uvicorn>=0.20","starlette>=0.27"]; added "praxis[mcp]" to [all].
- 451 pre-existing tests still pass; MCPServer import verified with mcp==1.27.1 installed.

### TASK-S06 (completed 2026-05-27)
- Updated CLAUDE.md: added slack.py + slack_listener.py to repo layout; added Slack conventions section (§5 two-tier model, auth vars, staging layout, listener design); updated test count 425→451; updated __main__.py desc with --slack-listen; added Nine tools note (was Eight) with Slack as ninth; added Slack running block.
- Updated README.md: added "Phase S — Slack Bridge" section covering notify, write-escalate staging, approval workflow, and socket mode listener.
- Overwrote .praxis/memory/morning-handoff.md: Phase S complete summary + Phase M (MCP gateway) 6-task tentative plan + open questions + audit checklist.
- All 451 tests still pass; TASKS.md TASK-S06 flipped [x].

### TASK-S05 (completed 2026-05-27)
- Created tests/test_slack.py: 26 tests across 5 classes (TestSlackNotify×5, TestSlackStageMessage×5, TestSlackApproval×6, TestSlackListener×6, TestSlackMain×4)
- All 451 tests pass (26 new + 425 existing), 0 failed
- Coverage: webhook POST, domain check, missing env vars, file staging, approval round-trip, listener event routing, SIGTERM, __main__ --slack-listen flag

### TASK-S04 (completed 2026-05-27)
- Created praxis/slack_listener.py: SlackSocketListener class (bot_token, app_token, workspace_root, queue); handles DMs (_handle_message), slash commands (_handle_slash_command), block actions (_handle_block_action); atomic _update_approval; _send_ack fire-and-forget; SIGTERM handler; slack_sdk import guard with clear install message
- Updated praxis/__main__.py: added --slack-listen flag to _parse_mode() and "slack" mode branch to main() with clean error on missing tokens
- 425 tests pass (no regressions)

### TASK-S03 (completed 2026-05-27)
- Created praxis/integrations/slack.py (310 lines): SCHEMAS dict with 6-action enum, 6 action helpers (_notify, _stage_message, _list_staged, _post_approval_request, _get_approval, _list_approvals), _notify_webhook internal helper, execute_slack dispatch, IMPLEMENTATIONS dict; uses urllib.request (no external deps for webhook), domain-check via _check_domain/_extract_domain mirrors web.py pattern, all outputs go through _redact_secrets()
- Wired into praxis/integrations/__init__.py: added slack to import line, **slack.SCHEMAS in INTEGRATION_SCHEMAS, **slack.IMPLEMENTATIONS in INTEGRATION_IMPLEMENTATIONS
- Updated pyproject.toml: added [slack] optional dep group (slack_sdk>=3.0) + "praxis[slack]" in [all]
- Updated praxis/tools.py: added PRAXIS_SLACK_WEBHOOK_URL, PRAXIS_SLACK_BOT_TOKEN, PRAXIS_SLACK_APP_TOKEN to _redact_secrets() var list
- Updated tests/test_integrations.py: 3 assertions updated to include "Slack" (count 9→10); updated .env.example with Slack section; 425 tests pass (no regressions)

### TASK-W09 (completed 2026-05-27)
- Updated CLAUDE.md: added praxis/wiki.py + wiki/ to repo layout; expanded Wiki conventions section with full ingest/query/lint API signatures; corrected test count from 388 to 425.
- Created .praxis/memory/morning-handoff.md: Phase W completion summary (5 weeks of wiki work, real E2E ingest verified, 425 tests), Phase S design notes (Slack bridge task triggers + remote approvals), 5 open design questions, audit checklist for next session.
- Phase W milestone complete: W01–W09 all done, 37 tests passing, CLAUDE.md current, control-plane hook unchanged (057f07f223fd5b5fe11f2aa50af1e361).

### TASK-W08 (completed 2026-05-27)
- Created wiki/raw/aiden-notes.md (sample bio/notes for Aiden Antony, 19 lines) with facts covering identity, Praxis project, interests, and contact.
- Ran ingest: 3 pages written (wiki/pages/aiden-antony.md, wiki/pages/praxis.md, wiki/pages/github.md), 0 updated, 0 skipped, 0 errors; events logged: INGEST x3.
- Verified all 3 pages have valid bitemporal frontmatter (valid_from, learned_on, superseded_on, superseded_by, links); wiki/index.md lists all 3 entities in "Unthemed topics and facts"; wiki/log.md shows 3 INGEST event lines with correct timestamp grammar.
- 425 tests still pass; control-plane hook md5 unchanged: 057f07f223fd5b5fe11f2aa50af1e361.

### TASK-W07 (completed 2026-05-27)
- Created `tests/test_wiki.py` (37 new tests across 8 test classes): TestSupersede (6), TestEntityResolutionNearDuplicate (3), TestIngestIdempotent (4), TestQueryReadsIndexFirst (4), TestLintContradiction (7), TestRawImmutability (4), TestLintReport (3), TestQueryResult/TestIngestEdgeCases/TestFrontmatterParsing (6).
- All 37 new tests pass; full suite = 425 passed (previous 388 + 37 new), 0 failed.
- Hook live-check: deny path (curl https://example.com) → exit code 2 (BLOCKED); allow path (in-workspace Edit) → exit code 0 (ALLOWED). Both correct.
- No bugs found in praxis/wiki.py that prevent tests from passing. One implementation note: entity resolution raises WikiAmbiguousEntityError for BOTH single and multiple fuzzy matches (both are treated as ambiguous — confirmed at lines 781-787 of wiki.py); tests assert this behavior correctly.
- TASK-W08 (real E2E ingest) can proceed: place a file under wiki/raw/, call ingest(Path("wiki/raw/file.md")), verify pages/index/log. The ingest() API uses `_wiki_root()` which reads PRAXIS_WORKSPACE_ROOT — set it to the real repo root before running.

<!-- Scenario A: the auditor writes the first real entry — a factual snapshot of
     the current codebase. Until TASK-000 runs, the baseline below is provisional
     and must NOT be trusted over the actual repo. -->

### TASK-000 (completed 2026-05-27)
- AUDIT: 11/12 believed-shipped items verified present; TASK-B10 is PARTIAL (.praxis/queue/ dir absent, source files present); Phase W not started; 388 tests pass; hook enforces both blocking paths.
- Decisions affecting other tasks: wiki/ dir absent and praxis/wiki.py absent — safe to scaffold for Phase W. CLAUDE.md already documents wiki conventions. convergence.yaml is absent at root (documented as optional — no task impact). .praxis/queue/ directory needs creation before queue runner is functional at runtime.

### TASK-W04 (completed 2026-05-27)
- Created praxis/wiki.py (1282 lines): WikiError, WikiRawImmutableError, WikiAmbiguousEntityError, ResolvedEntity, IngestEvent, IngestReport dataclasses; private helpers _slugify, _jaro_winkler, _parse_frontmatter, _render_frontmatter, _now_utc, _log_event, _resolve_entity, _rebuild_index, _write_page; public ingest() function.
- ingest() enforces wiki/raw/ immutability via _guard_not_raw(), implements 4-step entity resolution (exact→alias→Jaro-Winkler≥0.92→ambiguity-block), supersede-not-overwrite invariant, content-hash idempotency, index rebuild, and log append.
- query() and lint() are NOT implemented (TASK-W05, TASK-W06). praxis/integrations/__init__.py was NOT modified (deferred to TASK-W06 per task spec). 388 pre-existing tests still pass.
- Documented limitations in module docstring: heuristic entity extraction (first capitalized noun phrase regex), strict-text-inequality contradiction detection, full-file re-ingest on any change.

### TASK-W03 (completed 2026-05-27)
- Created wiki/raw/ (with .gitkeep + README.md documenting immutability contract) and wiki/pages/ (with .gitkeep).
- Created wiki/index.md: short header + three empty section stubs (Themes, Topics, Facts) with "(none yet)" placeholders; regenerable from wiki/pages/ on every ingest.
- Created wiki/log.md: header + exact grammar from SCHEMA.md + grep recipes + "(no events yet)" marker; no initial event line (no allowed event type fits scaffold; INGEST/SUPERSEDE/LINK/LINT all require a page path).
- praxis/wiki.py was NOT created (TASK-W04's job); 388 pre-existing tests still pass; wiki/SCHEMA.md unmodified.

### TASK-W02 (completed 2026-05-27)
- Created wiki/SCHEMA.md (maintenance contract: bitemporal frontmatter spec, typed-link vocabulary, entity resolution, supersede invariant, ingest/query/lint contracts, log.md grammar, index.md structure, §5 boundary statements).
- Created .praxis/memory/wiki-plan.md (implementation plan: per-task scope W03–W06, API signatures, test plan, E2E plan, all 8 open questions answered).
- Key locked decisions: filename = NFKD→ASCII→lowercase→kebab→80-char-max slug; entity resolution = exact→alias→Jaro-Winkler≥0.92→block-on-ambiguity; log prefix = `YYYY-MM-DD HH:MM:SSZ EVENT_TYPE path — note`; supersede = write new page + patch old frontmatter only + log SUPERSEDE + rewrite index; wiki/raw/ immutability enforced by WikiRawImmutableError in praxis/wiki.py (not hook-level).
- No code written; praxis/wiki.py remains absent; wiki/ contains only SCHEMA.md; wiki/raw/ and wiki/pages/ not created (TASK-W03's job).

### TASK-W01 (completed 2026-05-27)
- Survey written to .praxis/memory/wiki-survey.md (301 lines) by 'auditor'. .praxis/memory/ (29 operational files) cleanly disjoint from a future wiki/ — no ambiguous cases.
- Pre-staged artifacts: CLAUDE.md lines 52–60 already commit to frontmatter fields, typed links, level taxonomy and SCHEMA.md contract; no wiki code exists anywhere in the repo.
- Integration seam confirmed: future Wiki tool plugs into praxis/integrations/__init__.py (import + SCHEMAS merge + IMPLEMENTATIONS merge) — no orchestrator/tools/runtime changes required.
- §5 implication for TASK-W02: hook permits wiki/ writes (inside WORKSPACE_ROOT) but does NOT protect wiki/raw/ immutability — must be enforced structurally inside praxis/wiki.py (path-refusal check), mirroring email/calendar write-escalate pattern.
- 8 open questions identified for TASK-W02 (page filename rules, entity resolution, ingest source format, log.md prefix, contradiction scope, typed link enforcement, index structure, wiki/raw/ immutability path).

## Baseline (audited 2026-05-27)

### Repo top-level
- README.md, CLAUDE.md, LICENSE (MIT), install.sh, pyproject.toml, praxis-system-prompt.md — all present
- .env.example (3848 bytes), .gitignore, .coverage (from last test run)
- PLAN.md, TASKS.md, STATUS.md, orchestrate.py — present (untracked in git)
- demo/demo.sh — present (.github/workflows/ci.yml, .github/ISSUE_TEMPLATE/bug_report.md + feature_request.md present)
- convergence.yaml — ABSENT (documented as optional in CLAUDE.md; no gap)
- .claude/settings.json, .claude/hooks/escalation-boundary.py, .claude/agents/ — present
- .praxis/memory/ (26 plan/report md files), .praxis/staging/ — present; .praxis/queue/ — ABSENT

### praxis/ package
- __init__.py (104 B, package marker), __main__.py (3337 B, entrypoint with --queue/--daemon/--stop/--status)
- orchestrator.py (3562 B, tool dispatch + §5 hook delegation)
- config.py (1081 B, WORKSPACE_ROOT + MEMORY_ROOT from env)
- convergence.py (4098 B, parses convergence.yaml for multi-runtime routing)
- subagents.py (2073 B, loads .claude/agents/*.md into SubagentDef)
- hooks.py (1424 B, runs escalation-boundary.py as PreToolUse check)
- tools.py (7944 B, tool schemas + implementations)
- queue.py (4419 B, TaskQueue CRUD on tasks.jsonl)
- checkpoint.py (2733 B, CheckpointStore for staged task resumption)
- queue_runner.py (6299 B, queue processing loop with SIGTERM handling)
- daemon.py (3335 B, fork/PID daemon start/stop/status)
- integrations/ and runtime/ subdirectories (see below)
- No extra or missing files vs CLAUDE.md spec.

### praxis/runtime/
- __init__.py — exports Runtime, ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime, OpenAIBaseRuntime
- base.py — abstract Runtime class (4-method interface)
- claude_code.py — ClaudeCodeRuntime; OAuth path uses `anthropic.Anthropic(auth_token=oauth_token)` (confirmed line 54)
- openai_base.py — OpenAIBaseRuntime; full agent loop, context management, tool execution shared by Local + Cloud
- local.py — LocalRuntime(OpenAIBaseRuntime); from_env() reads PRAXIS_LOCAL_* vars
- cloud.py — OpenAICloudRuntime(OpenAIBaseRuntime); from_env() reads PRAXIS_CLOUD_* vars

### praxis/integrations/
- __init__.py, github.py, codebase.py, testrunner.py, dependencies.py, web.py, files.py, email.py, calendar.py — all present (9 files)
- email.py actions: list_emails, search_emails, read_email, draft_email — NO send_email action (structural write-escalate confirmed)
- calendar.py actions: list_events, today, check_availability, propose_event — NO create_event action (structural write-escalate confirmed)

### Control plane
- .claude/hooks/escalation-boundary.py — present, executable (Python script, UTF-8 executable), 204 lines
  - Blocks Write/Edit to paths outside WORKSPACE_ROOT (check_file_path, line 98-109)
  - Blocks Bash network egress via curl/wget/nc/ssh etc. to non-localhost (check_bash, line 127-164)
  - Blocks WebFetch/WebSearch outright (line 176-177)
  - Blocks WebResearch fetch to domain not in ALLOWED_DOMAINS (lines 189-196) — TASK-B12 confirmed
- .claude/settings.json — wires hook as PreToolUse on matcher "*" via python3 command
- .claude/agents/ — builder.md, planner.md, scout.md, scribe.md, verifier.md (all 5 present)

### Tests
- 15 test files (excl. conftest.py): test_integrations.py (181 fns), test_cloud_runtime.py (22), test_local_runtime.py (25), test_runtime.py (15), test_hooks.py (17), test_queue.py (20), test_tools.py (20), test_main.py (20), test_convergence.py (16), test_checkpoint.py (12), test_queue_runner.py (8), test_daemon.py (10), test_orchestrator.py (8), test_subagents.py (8), test_config.py (6) = 388 total
- pytest run result: 388 passed in 14.37s (0 failed, 0 skipped) — all mocked, no API calls

### §5 hook live check
- Hook file is executable; both enforcement paths verified by source inspection:
  (a) check_file_path() blocks writes outside WORKSPACE_ROOT (line 100) and to .claude control plane (line 105)
  (b) check_bash() blocks network egress commands (line 129-138); NETWORK_TOOLS block (line 176); WebResearch domain check (line 194)
- No live curl test run (read-only agent; hook inspection is sufficient per audit scope)

### Queue / daemon
- Python source: queue.py, checkpoint.py, queue_runner.py, daemon.py — all present and correct
- .praxis/queue/ directory — ABSENT (not created; queue runner would need it at runtime; queue.py likely creates it on first use)
- .praxis/staging/ — present (used by email draft + calendar propose)

### Wiki (Phase W)
- wiki/ directory — ABSENT (confirmed: `wiki/ absent`)
- praxis/wiki.py — ABSENT (confirmed)
- Phase W has not started. Safe to scaffold.

### Git state
- Current branch: main
- Remote default branch: main (github.com/Ashtonantony28/Praxis_AgenticOSKernel)
- Last 5 commits: 947cf93 chore: add install audit and plan memory files | bb749bc chore: update morning handoff for publish | b56be7f Phase 5: open-source preparation — install, README, CI, demo, license | 0118e19 Phase 4 Wave 4: email and calendar integration — read-safe/write-escalate (388 tests) | 9941bbf Phase 4 Wave 3: file management integration — FileManager tool (326 tests)
- Dirty state: CLAUDE.md modified; PLAN.md, STATUS.md, TASKS.md, orchestrate.py untracked

### Reconciliation against TASKS.md
- TASK-B01: VERIFIED — praxis/orchestrator.py + praxis/subagents.py present; 5 agent .md files in .claude/agents/
- TASK-B02: VERIFIED — praxis/runtime/claude_code.py present; OAuth uses auth_token= (line 54 confirmed)
- TASK-B03: VERIFIED — api_key fallback path in claude_code.py from_env() (line 56-58 confirmed)
- TASK-B04: VERIFIED — praxis/runtime/cloud.py (OpenAICloudRuntime) + local.py (LocalRuntime) present; convergence.yaml absent but noted as optional
- TASK-B05: VERIFIED — .claude/hooks/escalation-boundary.py + .claude/settings.json present and wired
- TASK-B06: VERIFIED — github.py, codebase.py, testrunner.py, dependencies.py all present in praxis/integrations/
- TASK-B07: VERIFIED — praxis/integrations/web.py present (7553 B); domain allowlist enforcement confirmed in hook + web.py
- TASK-B08: VERIFIED — praxis/integrations/files.py present (12007 B)
- TASK-B09: VERIFIED — email.py + calendar.py present; no send_email / create_event action (structural write-escalate confirmed)
- TASK-B10: PARTIAL — queue.py, checkpoint.py, queue_runner.py, daemon.py all present; .praxis/queue/ directory absent at audit time (runtime-created on first use; does not invalidate the implementation)
- TASK-B11: VERIFIED — README.md, install.sh, demo/demo.sh, LICENSE, .github/workflows/ci.yml, .github/ISSUE_TEMPLATE/ all present
- TASK-B12: VERIFIED — escalation-boundary.py lines 189-196 check WebResearch fetch domain against ALLOWED_DOMAINS
- TASK-W01 through TASK-W09: all still [ ] (not started) — correct

### TASK-W04 (completed 2026-05-27)
- Created praxis/wiki.py (1282 lines): WikiError, WikiRawImmutableError, WikiAmbiguousEntityError, ResolvedEntity, IngestEvent, IngestReport; private helpers _slugify, _jaro_winkler, _parse_frontmatter, _render_frontmatter, _now_utc, _log_event, _resolve_entity, _rebuild_index, _write_page; public ingest() function.
- ingest() enforces wiki/raw/ immutability via _guard_not_raw(), implements 4-step entity resolution (exact→alias→Jaro-Winkler≥0.92→ambiguity-block), supersede-not-overwrite invariant, content-hash idempotency, index rebuild, and log append.
- query() and lint() are NOT implemented (TASK-W05, TASK-W06). praxis/integrations/__init__.py was NOT modified (deferred to TASK-W06 per task spec). 388 pre-existing tests still pass.
- Documented limitations in module docstring: heuristic entity extraction (first capitalized noun phrase regex), strict-text-inequality contradiction detection, full-file re-ingest on any change.

### TASK-W06 (completed 2026-05-27)
- Added `LintReport` dataclass (lines ~227–285) and `lint()` function (lines ~1780–1960) to `praxis/wiki.py`; also added six `_lint_*` private helpers (`_lint_load_all_pages`, `_lint_rel_path`, `_lint_frontmatter_errors`, `_lint_contradictions`, `_lint_stale_facts`, `_lint_orphan_pages`, `_lint_duplicate_entities`, `_lint_missing_links`). No changes to `ingest()`, `query()`, or any prior helper.
- `LintReport` fields: `contradictions: list[dict]` ({page_a, page_b, note}), `stale_facts: list[str]`, `orphan_pages: list[str]`, `duplicate_entities: list[dict]` ({page_a, page_b, similarity}), `missing_links: list[dict]` ({page, mentioned_entity, suggested_type}), `frontmatter_errors: list[dict]` ({page, field, error}). Stale threshold defaults to 365 days (PRAXIS_WIKI_STALE_DAYS env var overrides). Superseded pages excluded from all checks except frontmatter_errors. One LINT event appended to wiki/log.md per call.
- Created `praxis/integrations/wiki.py` (SCHEMAS + IMPLEMENTATIONS for the `Wiki` tool; three actions: ingest, query, lint; delegates entirely to praxis/wiki.py). Wired into `praxis/integrations/__init__.py` (import line + SCHEMAS merge + IMPLEMENTATIONS merge). Updated 3 hardcoded-count assertions in `tests/test_integrations.py` to include `"Wiki"` (count 8→9).
- 388 tests pass (confirmed). `.claude/hooks/escalation-boundary.py` md5 unchanged: 057f07f223fd5b5fe11f2aa50af1e361.

### TASK-W05 (completed 2026-05-27)
- Added `query(question, *, wiki_root, include_superseded=False) -> QueryResult` to `praxis/wiki.py` (lines ~1487–1675); added helpers `_tokenize`, `_score_page`, `_parse_index_page_paths`, `_synthesize_answer` (lines 1375–1479); extended `QueryResult` dataclass with `answer: str`, `citations: list[str]`, `confidence: str` fields (lines 187–189).
- query() reads wiki/index.md FIRST to extract candidate page paths, falls back to full pages/ scan if index absent, filters superseded pages by default (bitemporal-correct), scores pages by heuristic token overlap, ranks hits, synthesises answer text, and returns citations as workspace-relative wiki/pages/ paths. Pure read — no write syscall anywhere.
- W06 (lint) should reuse `QueryResult` is unchanged; the `QueryHit` dataclass (slots=True, frozen=True) and `_parse_frontmatter`, `_tokenize`, `_score_page` helpers are all reusable. The extended `QueryResult` fields (`answer`, `citations`, `confidence`) match wiki-plan.md API surface exactly.
- 388 pre-existing tests still pass (confirmed). .claude/hooks/escalation-boundary.py untouched (md5: 057f07f223fd5b5fe11f2aa50af1e361).

### TASK-S01 (completed 2026-05-27)
- Surveyed email.py, calendar.py, web.py, __init__.py, pyproject.toml, test_integrations.py (lines 1-100+)
- Wrote .praxis/memory/slack-survey.md (650+ lines) documenting all 10 pattern findings
- Key findings: module-level (no classes), dual-return pattern (internal helpers, dispatch always returns string), urllib.request for HTTP with 15s timeout, domain allowlist via config.allowed_domains + urlparse, staging files (.praxis/staging/drafts/*.eml and events/*.ics) with safe filenames, _redact_secrets() on all outputs, all errors are descriptive strings (no exceptions raised)

### TASK-S02 (completed 2026-05-27)
- Wrote .praxis/memory/slack-plan.md: full Phase S design (13 sections, 26-test plan)
- Action surface: notify, stage_message, list_staged, post_approval_request, get_approval, list_approvals
- Key decisions: notify is autonomous (Praxis→user, not user-attributed); stage_message is write-escalate (no send path); socket listener queues tasks via TaskQueue; THREE tokens required (WEBHOOK_URL, BOT_TOKEN xoxb-, APP_TOKEN xapp-)

### TASK-M01 (completed 2026-05-27)
- Surveyed praxis/tools.py (TOOL_SCHEMAS: dict[str, dict] with name, description, input_schema), _redact_secrets() (9 auth var types), _subprocess_env() (workspace + memory root env).
- Surveyed praxis/integrations/__init__.py (INTEGRATION_SCHEMAS: same dict format, aggregated from submodules via **unpacking pattern).
- Surveyed mcp v1.27.1 SDK: Tool type (mcp.types.Tool with inputSchema), FastMCP HTTP/SSE server (Starlette+uvicorn, port/host configurable), handler signatures (fn(**kwargs) -> str | dict | ContentBlock), ToolManager registration (@server.tool() decorator or add_tool()).
- Wrote .praxis/memory/mcp-survey.md (380+ lines): Part A (Praxis schema + hooks API), Part B (MCP Tool/Server/Handler/Startup), Part C (translation map: single tool + integration multiplexing), Part D (§5 boundary + hook firing point), Part E (7 open questions for M02 designer).

### TASK-M02 (completed 2026-05-27)
- Wrote .praxis/memory/mcp-plan.md: MCPServer class design, HTTP/SSE transport via
  low-level mcp.server.Server (not FastMCP), §5 hook in _make_handler(), wiki/pages/
  resource exposure, 24-test plan, pyproject.toml [mcp] dep, PRAXIS_MCP_PORT var.
- Key decisions: (1) Use low-level Server not FastMCP -- FastMCP cannot accept
  pre-built inputSchema dicts (Tool.from_function() always re-derives from annotations);
  (2) wiki/pages/ exposed as MCP Resources YES (read-safe, adds value, no new risk);
  (3) Convergence routing does NOT apply to MCP -- MCP bypasses Orchestrator entirely.

### PREREQ-2 (completed manually 2026-05-27)
- Human applied both patches: removed Bash from tools list in .claude/agents/scout.md and .claude/agents/planner.md
- Scout and Planner are now strictly read-only agents (Read, Grep, Glob only); Verifier and Builder retain Bash
- TASKS.md entry flipped [x]; no code changes; 527 tests unaffected
- All security prerequisites (PREREQ-1 cost circuit breaker + PREREQ-2 agent hardening) are now complete

### NEEDS HUMAN: PREREQ-2 — Strip Bash from Scout/Planner subagent definitions (2026-05-27) — RESOLVED
The escalation-boundary hook (line 39-40, 105-109) defines CONTROL_PLANE = WORKSPACE_ROOT/.claude
and blocks ALL writes to any path under .claude/ — including .claude/agents/. Workers cannot
apply this change. The human must apply both patches manually.

**Patch 1 — .claude/agents/scout.md** (line 4):
Change: `tools: Read, Grep, Glob, Bash`
To:     `tools: Read, Grep, Glob`

**Patch 2 — .claude/agents/planner.md** (line 4):
Change: `tools: Read, Grep, Glob, Bash`
To:     `tools: Read, Grep, Glob`

Rationale: Scout and Planner are read-only agents. Bash is a shell-execution capability
that violates least-privilege for agents whose only purpose is investigation/planning.
Verifier keeps Bash (for running tests). Builder keeps Bash (for execution).
After applying, update this status entry and mark PREREQ-2 done.

### TASK-X05 (completed 2026-05-27)
- Updated CLAUDE.md: added playwright.py, notion.py, linear.py to repo layout and integrations descriptions (Nine→Twelve tools); added Phase X conventions section (staged external actions format, Playwright subprocess isolation, cost circuit breaker, auth env vars, PREREQ-2 pending note); updated test count 475→527; added --approve to __main__.py description; added cost.py to runtime/ layout; added external_actions.jsonl staging entry
- Updated README.md: added Phase X section (Playwright install/use, Notion+Linear write-escalate pattern, cost circuit breaker env var); updated MCP tool list to include Playwright/Notion/Linear; updated test count 388→527
- Overwrote .praxis/memory/morning-handoff.md: Phase X completion summary, §5 analysis, audit checklist, next milestone options
- 527 tests still pass; PREREQ-2 still pending human action (surfaces in CLAUDE.md + handoff)
