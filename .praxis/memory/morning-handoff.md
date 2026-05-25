# Morning handoff — Phase 0: build the minimal Python orchestrator

**Status:** Phase A halted (correctly — premise mismatch). Reconciliation
confirmed: **Phase A is actually Phase 0**. Build the orchestrator first,
then a runtime abstraction has something to wrap.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`
**Working branch:** `claude/blissful-franklin-VIMiH` (this branch)
**Local path:** `/home/user/Praxis_AgenticOSKernel`

Files (identical on `main`, `claude/blissful-franklin-VIMiH`, and
`claude/plan-execute-mode-switch-zCz38`):

```
praxis-system-prompt.md                     # the spec (§0–§11)
.claude/agents/builder.md
.claude/agents/planner.md
.claude/agents/scout.md
.claude/agents/scribe.md
.claude/agents/verifier.md
.claude/hooks/escalation-boundary.py        # the only Python, PreToolUse hook
.claude/settings.json                       # wires the hook
.praxis/memory/.gitkeep
.praxis/memory/morning-handoff.md           # this file (new on this branch)
.gitignore
```

Other branches (`fix-pre-tool-use-hook-tRzjd`, `jolly-clarke-Gpap8`,
`loving-mendel-7qxgd`, `remove-escalation-hook-kfZ5Q`,
`review-morning-handoff-JVdUE`, `wizardly-sagan-fSJLM`,
`zealous-turing-WkWhs`) are empty — no commits with content.

**No Python orchestrator. No test suite. No `runtime/` package. No
`CLAUDE.md`.**

---

## 2. Correct WORKSPACE_ROOT

`praxis-system-prompt.md` §0 currently says:

```
WORKSPACE_ROOT = /home/user/LinuxAgenticClaudeOS
MEMORY_ROOT    = /home/user/LinuxAgenticClaudeOS/.praxis/memory
```

`.claude/settings.json` also points its hook command at
`/home/user/LinuxAgenticClaudeOS/.claude/hooks/escalation-boundary.py`.

**Reality:** the repo lives at `/home/user/Praxis_AgenticOSKernel`. The
`LinuxAgenticClaudeOS` path is stale from an earlier rename and does not
exist on this machine. Either path needs to be made authoritative.

Recommended fix as part of Phase 0:

```
WORKSPACE_ROOT = /home/user/Praxis_AgenticOSKernel
MEMORY_ROOT    = /home/user/Praxis_AgenticOSKernel/.praxis/memory
```

…and update `.claude/settings.json` to reference the in-repo hook with a
repo-relative path (or `${CLAUDE_PROJECT_DIR}`) so it works in any
clone/container — the hardcoded absolute path is currently dead in this
container.

---

## 3. What Phase 0 needs to build

**Goal:** the smallest possible Python program that, when run, *is*
Praxis — i.e., the markdown spec (`praxis-system-prompt.md`) plus the
subagent prompts in `.claude/agents/` actually drive a Claude Agent SDK
session, with the §5 hook enforced. Once this exists, Phase A (runtime
abstraction) becomes a real refactor with real behavior to preserve.

### 3.1 Minimum components

```
praxis/
  __init__.py
  __main__.py            # `python -m praxis` entrypoint
  orchestrator.py        # builds & runs the top-level Agent SDK session
                         #   - loads praxis-system-prompt.md as system prompt
                         #   - registers tools (Bash, Read, Edit, Write, Grep,
                         #     Glob, Agent, ExitPlanMode, etc. — whatever the
                         #     spec actually needs)
                         #   - installs the PreToolUse hook
                         #   - runs the agent loop until completion
  subagents.py           # loads .claude/agents/*.md, exposes them as a
                         # spawn_subagent(name, prompt) primitive that the
                         # orchestrator's Agent tool dispatches to
  hooks.py               # thin wrapper that invokes
                         #   .claude/hooks/escalation-boundary.py
                         # as the PreToolUse hook (subprocess; respects
                         # stdin/stdout JSON protocol it already uses)
  config.py              # WORKSPACE_ROOT, MEMORY_ROOT, ALLOWED_DOMAINS
                         # resolved from env / settings.json with the
                         # "most restrictive interpretation" fallback
                         # from §0
  io.py                  # stdin/stdout JSON line protocol for sessions
                         # (or whatever transport you want — keep it small)

tests/
  test_config.py         # restrictive-fallback semantics
  test_subagents.py      # markdown agent loader: name, description, tools
  test_hooks.py          # PreToolUse hook invocation, deny/allow/rewrite
  test_orchestrator.py   # end-to-end with a stubbed SDK transport:
                         # - boots, loads system prompt, registers tools
                         # - subagent spawn is dispatched correctly
                         # - hook denial blocks the offending tool call
                         # - in-workspace edit succeeds
  conftest.py            # fakes/fixtures: FakeAgentSDK that records calls

pyproject.toml           # claude-agent-sdk, pytest, ruff
README.md                # one screen: how to run + how to test
```

Keep it under ~500 lines of orchestrator code. The point is to make the
markdown spec executable, not to add features.

### 3.2 Tool list (what the orchestrator needs to register with the SDK)

From `praxis-system-prompt.md` and the subagent files, the minimum tool
surface is:

- `Bash`, `Read`, `Edit`, `Write` — workspace mutation
- `Grep`, `Glob` — workspace search
- `Agent` — dispatches to the subagents declared in `.claude/agents/*.md`
- `ExitPlanMode` — §4.5 plan/execute switch
- `WebFetch` *(optional, gated by ALLOWED_DOMAINS — currently empty,
  so this is wired but always denied by §5 hook)*

No MCP tools in Phase 0 — add later if needed.

### 3.3 §5 hook integration (do not change behavior)

`.claude/hooks/escalation-boundary.py` already implements the §5 boundary
check via the standard Claude Code PreToolUse JSON protocol (read JSON
from stdin, exit 0 / nonzero, emit decision JSON on stdout). The
orchestrator must call it **byte-for-byte the same way** Claude Code
does:

- subprocess with the hook script as `argv[0]`
- send the standard `{tool_name, tool_input, ...}` JSON on stdin
- honor `permissionDecision` and `permissionDecisionReason` from stdout

A test must cover: `curl https://example.com` → blocked (egress closed);
edit inside `WORKSPACE_ROOT` → allowed.

### 3.4 Subagent loader contract

Each `.claude/agents/*.md` has YAML frontmatter (name, description,
tools, model). The loader parses the frontmatter, exposes
`spawn_subagent(name, prompt, *, isolation=None)` that runs a child
Agent SDK session with:

- system prompt = the file's markdown body
- tools = the frontmatter `tools` list (restricted from orchestrator's)
- model = the frontmatter `model` (Haiku for scout, etc.)
- inherits §5 hook unconditionally

This is the spawn primitive the Phase A `Runtime.spawn_subagent` will
later wrap.

### 3.5 Test suite target

The Phase A task referenced "62 cases" — that number was aspirational,
not from this repo. A realistic Phase 0 test count is ~25–40 covering:

- config resolution + restrictive fallback (4–6)
- subagent markdown parsing edge cases (6–8)
- hook subprocess protocol incl. deny/allow/rewrite (6–8)
- orchestrator boot + tool registration with a FakeAgentSDK (4–6)
- subagent dispatch routing (3–5)
- end-to-end happy path + §5 boundary path (2–4)

Use `pytest`. Stub the SDK; don't burn real API calls in CI.

### 3.6 Out of scope for Phase 0 (do not let it expand)

- The Runtime abstraction itself — that's Phase A, only meaningful once
  the orchestrator exists.
- OAuth / API key plumbing — Phase B.
- Memory management beyond reading/writing files under `MEMORY_ROOT`.
- Anything that changes the §5 hook's behavior.
- Renaming Praxis or touching the spec's content (only fix the stale
  `WORKSPACE_ROOT` path).

---

## 4. Recommended next-session prompt

> Build Phase 0: the minimal Python orchestrator described in
> `.praxis/memory/morning-handoff.md` §3. Plan → Build → Verify → Scribe.
> Fix the stale `WORKSPACE_ROOT` to `/home/user/Praxis_AgenticOSKernel`
> in `praxis-system-prompt.md` §0 and in `.claude/settings.json`'s hook
> path. Keep it ≤500 lines orchestrator + ≤300 lines tests. Do not start
> Phase A (runtime abstraction) until Phase 0 is green.

---

## 5. What this session changed

- `.praxis/memory/morning-handoff.md` — this file (created).

Nothing else. No code, no spec edits, no settings changes.
