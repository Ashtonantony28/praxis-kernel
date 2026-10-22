# Praxis-Kernel — Completion Plan

Status: **APPROVED 2026-10-19 by the owner.** Agents may edit `.claude/hooks/` and `.claude/settings.json` for SG2/SG4, in reviewable PRs, re-pinning the md5 in `init.sh`. Web UI stays out of scope.
Previous plan (Phase H, done): `PLAN_phaseH_archive.md`.

## Goal
Make the shipped claims true: every runtime enforces §5 and modes, plan mode is actually
read-only, staged actions are tamper-evident from the moment they are staged, the daemon
survives a week unattended, and the docs describe the code that exists.

## Baseline (2026-10-19, HEAD ed94709)
- `pip install -e .` fails: `build-backend = "setuptools.backends._legacy:_Backend"` does not exist. CI has been red on every push since at least 2026-06-01 for this reason.
- `PYTHONPATH=. pytest tests -q`: **1312 passed, 85 failed** (50 `test_mcp.py`: `Server.list_tools` gone in current `mcp`; 21 `test_memory_store.py`: hard numpy import; 9 orchestrator/telemetry/runtime tests depend on `PRAXIS_WORKSPACE_ROOT` being set by the caller; 1 webhook wiring).
- Checker latency per tool call (5k calls, laptop-class container):
  `enforce()` p50 15–74 µs, p99 60–195 µs; subprocess hook p50 47 ms, p99 60 ms.

## Latency budget (set now, enforced by SG2 and AUDIT-2)
| Check | Budget |
|---|---|
| In-process `policy.evaluate()` per tool call | p99 ≤ 1 ms |
| Subprocess hook per tool call | p99 ≤ 80 ms |
| Queue `claim()` / `update()` with 10k tasks on disk | p99 ≤ 20 ms |

Measured by `python scripts/bench_policy.py` and `python scripts/bench_queue.py` (added in SG1/SG6); CI fails if a budget is exceeded.

## Coordination rules
- Work runs in this project's Praxis thread; each sub-goal is delegated to one subagent (owner below). Max 3 subagents at once.
- Every owner reads this file first and updates only its own row and section at the end.
- Subagents return only: files changed, tests added, verification command + result, open issues.
- Stop and report if verification fails 3 times or a fix needs a change to a contract in "Interface contracts". No silent workarounds.
- One PR per sub-goal, branch `claude/sg<N>-<slug>`. Concise code matching existing style, minimal comments, tests first.
- SG0 and SG1 are serial gates: nothing else starts until both are merged and green.
- `CLAUDE.md` still applies: owners also follow its finishing contract (`feature_list.json`, `claude-progress.txt`). The owner approved edits to `.claude/hooks/` and `.claude/settings.json` for SG2 and SG4 only; any other control-plane change goes to `STATUS.md` under "NEEDS HUMAN".

## Sub-goals

| ID | Sub-goal | Owner | Depends on | Status |
|---|---|---|---|---|
| SG0 | Green, hermetic baseline | builder-sg0 | — | done |
| SG1 | Shared interfaces + policy fixture corpus | builder-sg1 | SG0 | not started |
| SG2 | Unified policy engine (hook + enforce) | builder-sg2 | SG1 | not started |
| SG3 | Mode semantics and subagent inheritance | builder-sg3 | SG1, SG2 | not started |
| SG4 | Governed Claude Code CLI runtime | builder-sg4 | SG2, SG3 | not started |
| SG5 | Approval integrity | builder-sg5 | SG1 | not started |
| SG6 | Queue and daemon robustness | builder-sg6 | SG1 | not started |
| SG7 | Untrusted input channels and gateway auth | builder-sg7 | SG2, SG3 | not started |
| SG8 | Cross-process events and Telegram wiring | builder-sg8 | SG6 | not started |
| SG9 | Docs match code | scribe-sg9 | SG2–SG8 | not started |

Waves: SG0 → SG1 → {SG2, SG5, SG6} → {SG3, SG7, SG8} → SG4 → SG9 → audits.

### SG0 — Green, hermetic baseline
Fix packaging (`setuptools.build_meta`), port `mcp_server.py` to the current low-level `mcp` API and pin a tested range, make semantic-memory tests `importorskip` numpy/chromadb, add an autouse fixture that sets `PRAXIS_WORKSPACE_ROOT`/`PRAXIS_MEMORY_ROOT` to `tmp_path`.
- Accept: fresh venv `pip install -e .[dev,mcp,cloud,scheduler,slack,hooks]` succeeds; full suite 0 failures with no env vars set; CI green on 3.11 and 3.12.
- Verify: `python -m venv /tmp/v && /tmp/v/bin/pip install -e '.[dev,mcp,cloud,scheduler,slack,hooks]' && env -u PRAXIS_WORKSPACE_ROOT /tmp/v/bin/python -m pytest tests -q`

### SG1 — Shared interfaces + policy fixture corpus
Write the contracts below as code with no behaviour change, plus `tests/fixtures/policy/*.jsonl`: ≥ 200 labelled tool calls `{call, ctx, expect, rule}` covering every bypass in the analysis (relative paths, `cd` chains, `$HOME`/`~`, interpreter network calls, `localhost` comment spoofing, reads of `.env`/`~/.ssh`, integration writes in plan mode, nested `Agent` calls) and ≥ 50 must-allow calls. Prove the policy encoding on the fixture traces before touching the hook, runtimes or channels.
- Accept: contracts importable and unit-tested; schema files validate the fixtures; `scripts/bench_policy.py` runs the corpus and prints p50/p99; corpus cases the current code gets wrong are marked `xfail(strict=True)`.
- Verify: `pytest tests/test_contracts.py tests/test_policy_corpus.py -q && python scripts/bench_policy.py`

### SG2 — Unified policy engine
Implement `praxis/policy.py:evaluate`; `enforce()` and `.claude/hooks/escalation-boundary.py` both become thin callers. Fail closed on missing hook, malformed hook input, unset workspace root. Confine Read/Grep/Glob/Wiki-ingest to `WORKSPACE_ROOT` with a secrets denylist. Hook edit and new md5 pin in `init.sh` are owner-approved; land them in this sub-goal's PR.
- Accept: corpus 100% — **zero false negatives** on `expect: block`, every false positive listed in this file with a reason; latency within budget; hook and `enforce()` give identical verdicts on the whole corpus (parity test).
- Verify: `pytest tests/test_policy_corpus.py tests/test_policy_parity.py tests/test_enforcement.py -q && python scripts/bench_policy.py --assert-budget`

### SG3 — Mode semantics and subagent inheritance
Deny entries support `tool` and `tool:action`; `allowed_tools` enforced; mode passed per call, not stored on the runtime; child mode = `parent.restrict(child)`; plan mode denies `Agent` for any agent whose effective tools include a write; fix `scribe`/`verifier` definitions so their mode and tools agree.
- Accept: plan-mode session cannot stage any integration write, cannot spawn `builder`, and a parent's later calls are checked against the parent's mode after a subagent returns — each proven on all API runtimes (Claude, cloud, local).
- Verify: `pytest tests/test_mode_enforcement.py tests/test_subagent_mode_routing.py tests/test_mode_inheritance.py -q`

### SG4 — Governed Claude Code CLI runtime
`ClaudeCodeCLIRuntime` passes the governance prompt (`--append-system-prompt`), maps the mode to `--disallowedTools` / `--permission-mode plan`, refuses to start unless the workspace `.claude/settings.json` installs the §5 hook with the pinned md5, and records cost from the JSON result. Remove the hard-coded personal path from `.claude/settings.json`.
- Accept: with a fake `claude` binary on `PATH`, argv assertions pass for build and plan; a plan run on the real-format fixture produces zero file writes; auto-selection only happens when the hook check passes; the CLI runtime joins the cross-runtime parametrised tests.
- Verify: `pytest tests/test_cli_runtime.py tests/test_enforcement.py tests/test_mode_enforcement.py -q`

### SG5 — Approval integrity
Sign at stage time when `PRAXIS_STAGING_HMAC_KEY` is set and reject unsigned or mismatched entries on every execute path (CLI and `POST /api/approvals`); constant-time compare; idempotency key `(task_id, provider, action, sha256(params))` dedupes re-staging; `--approve-plan` requires `status == pending` and replays the original task text (stored before history is prepended). Email/calendar stay manual-send; `--approve` lists them as such instead of implying it sends them.
- Accept: tamper tests on both paths, duplicate staging test, approve-rejected-plan test, history-not-duplicated test.
- Verify: `pytest tests/test_plan_approval.py tests/test_api.py tests/test_staging_integrity.py -q`

### SG6 — Queue and daemon robustness
`TaskStore` with `fcntl` lock + atomic rename; `BaseException` (incl. `SystemExit`) at the task boundary becomes a task failure, never a dead runner; cost breaker is per task; retries skip already-staged actions via the SG5 idempotency key; `PRAXIS_MAX_CONCURRENT_TASKS` either drives a real worker pool or is removed (owner picks the smaller change); task-type routing keeps overrides, agent modes and `model_for_task_type`.
- Accept: 4 writer processes × 2,500 appends + running loop → 0 lost or duplicated tasks; injected `SystemExit`/cost trip fails one task and the loop continues; `bench_queue.py` within budget.
- Verify: `pytest tests/test_queue.py tests/test_queue_runner.py tests/test_queue_concurrency.py -q && python scripts/bench_queue.py --assert-budget`

### SG7 — Untrusted input channels and gateway auth
Implement the documented trust tiers (`praxis/trust.py`: OWNER/ADMIN/USER/BLOCKED, env first, then `convergence.yaml`); Slack, email ambient, webhook and WhatsApp senders fail closed when unconfigured; tasks from non-OWNER origins run in plan mode; `/sse` and `/messages` require `PRAXIS_UI_TOKEN`; MCP tool calls go through `evaluate()`; constant-time token compare.
- Accept: unknown Slack user and unknown email sender enqueue nothing; webhook task runs in plan mode; unauthenticated `/sse` returns 401.
- Verify: `pytest tests/test_trust.py tests/test_slack.py tests/test_ambient.py tests/test_mcp.py tests/test_webhook.py -q`

### SG8 — Cross-process events and Telegram wiring
Queue events reach the gateway's `/ws` when `--queue` and `--mcp` run as separate processes (gateway tails an append-only `events.jsonl`; no broker); thread-safe `publish_sync`; `--telegram-listen` entrypoint calling `send_or_stage`; `convergence.yaml` `channels:` and `allowed_domains:` actually read.
- Accept: two-process integration test sees `TASK_COMPLETED` on a WebSocket client; Telegram inbound → queue → staged reply round trip with a fake bot.
- Verify: `pytest tests/test_event_bus.py tests/test_events_cross_process.py tests/test_telegram.py -q`

### SG9 — Docs match code
README/ARCHITECTURE/DEPLOY reflect the code: real test count, how API keys are handled, where the md5 check runs, the web UI is a scaffold (roadmap), `voice.py` roadmap, which staged items `--approve` executes.
- Accept: every claim in README "Why it's different", "Security model" and "Tests" maps to a test name listed in `docs/claims.md`.
- Verify: `python scripts/check_claims.py` (each referenced test exists and passes)

## Out of scope (roadmap, stays documented as such)
- React web UI screens (Chat, Approvals, TaskBoard, Wiki, Schedule, Settings). REST API stays.
- `voice.py` (Whisper STT). Container/namespace isolation (the README's own non-goal).
- Executors that actually send email or calendar invites.

## Interface contracts (SG1 owns; changing any of these after SG1 merges = stop and report)

```python
# praxis/policy.py
@dataclass(frozen=True)
class ToolCall:
    name: str
    input: Mapping[str, Any]
    @property
    def action(self) -> str | None: ...        # input.get("action") for integration tools

@dataclass(frozen=True)
class PolicyContext:
    workspace_root: Path
    allowed_domains: frozenset[str]
    mode: Mode | None
    origin: str = "cli"                         # cli | queue | slack | email | webhook | mcp | telegram | whatsapp

@dataclass(frozen=True)
class Verdict:
    allowed: bool
    rule: str                                   # stable id, e.g. "write.outside_workspace"
    reason: str

def evaluate(call: ToolCall, ctx: PolicyContext) -> Verdict: ...
def context_from_env(mode: Mode | None = None, origin: str = "cli") -> PolicyContext: ...

# praxis/modes/base.py (additions)
class Mode:
    def permits(self, call: ToolCall) -> bool: ...           # handles "tool" and "tool:action"
    def restrict(self, child: "Mode | None") -> "Mode": ...   # never wider than self

# praxis/runtime/base.py (signature change, all runtimes)
ToolExecutor = Callable[[ToolCall, PolicyContext], str]

# praxis/queue.py
class TaskStore(Protocol):
    def append(self, task: Task) -> None: ...
    def claim(self) -> Task | None: ...                       # atomically pending -> running
    def update(self, task_id: str, **fields: Any) -> None: ...

# praxis/trust.py
class Tier(IntEnum): BLOCKED = 0; USER = 1; ADMIN = 2; OWNER = 3
def tier_for(channel: str, sender_id: str) -> Tier: ...
```

Data formats (JSON Schema in `praxis/schemas/`):
- `staged_action.json`: `id, provider, action, params, queued_at, status (pending|approved|rejected|executed|failed), task_id, idempotency_key, _hmac`.
- `task.json`: existing `Task` fields + `origin`, `mode`, `cost_budget_usd`.
- `policy_case.json`: `{call: {name, input}, ctx: {mode, origin, allowed_domains}, expect: allow|block, rule}`.
- `event.json`: `{ts, type, data}` one per line in `.praxis/events.jsonl`.

## Audits (after SG9; auditors are fresh subagents that wrote none of the code)
| Audit | Focus | Output |
|---|---|---|
| AUDIT-1 | Policy soundness: no false negatives. Fuzz and hand-craft bypasses against `evaluate()` and the hook. | `AUDIT-1.md` |
| AUDIT-2 | Performance under load: checker latency vs budget, queue at 10k tasks, daemon with 4 producers for 1 h simulated. | `AUDIT-2.md` |
| AUDIT-3 | API/SDK consistency: runtime parity (4 runtimes), CLI vs REST vs Slack approval parity, MCP vs orchestrator tool behaviour. | `AUDIT-3.md` |
| AUDIT-4 | Test coverage: branch coverage on `policy.py`, `modes/`, `runtime/`, `queue*.py`, `__main__` approval code ≥ 90%; tests that assert nothing. | `AUDIT-4.md` |

Findings are ranked by severity, each with a repro or failing test. Critical/high findings become new SG rows here and go through build-and-verify. Audits repeat until a full round finds no critical or high issues.

## Open issues
_(owners append here)_
