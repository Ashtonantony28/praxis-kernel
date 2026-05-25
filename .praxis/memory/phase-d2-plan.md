# Phase D-2 Plan: convergence.yaml multi-runtime routing

**Date:** 2026-05-25

---

## Goal

Move runtime selection from pure env vars to a config file (`convergence.yaml`),
with env var as override. Enable routing different subagent roles to different
runtimes (e.g., scout → local, builder → claude).

## Config file

`convergence.yaml` lives at workspace root (next to `pyproject.toml`):

```yaml
runtimes:
  default: claude         # "claude" or "local"
  overrides:              # per-subagent routing (optional)
    scout: local
    scribe: local

local:                    # local runtime settings (only needed if used)
  base_url: http://localhost:11434
  model: llama3.1:8b
```

If `convergence.yaml` is absent, behavior is identical to today (env vars only).

## Override precedence

1. `PRAXIS_RUNTIME` env var — if set, overrides `runtimes.default` from file
2. `convergence.yaml` `runtimes.default` — if file exists
3. Falls back to `"claude"` if neither is set

Per-subagent overrides from convergence.yaml apply regardless of env var
(the env var only overrides the *default* runtime, not individual routes).

## Implementation

### New file: `praxis/convergence.py`

```python
@dataclass
class ConvergenceConfig:
    default_runtime: str                    # "claude" or "local"
    overrides: dict[str, str]               # subagent_name → runtime_name
    local_base_url: str
    local_model: str

    @classmethod
    def load(cls, workspace_root: Path) -> "ConvergenceConfig": ...
```

- Parses `convergence.yaml` with PyYAML (already available via anthropic deps)
- Falls back to env vars / defaults if file is absent
- Validates: unknown runtime names → clean error

### Changes to `__main__.py`

- Load `ConvergenceConfig`
- Create all needed runtimes (deduplicate — don't create LocalRuntime twice)
- Pass `runtime_overrides` dict to Orchestrator

### Changes to `orchestrator.py`

- `__init__` gains optional `runtime_overrides: dict[str, Runtime] | None`
- `run_subagent()` looks up override, falls back to default runtime
- `run()` always uses default runtime (no change)

### New tests: `tests/test_convergence.py`

- Parse valid YAML → correct ConvergenceConfig
- Missing file → defaults
- Env var overrides file default
- Invalid runtime name → clean error
- Subagent routing picks correct runtime

## Files changed

1. `praxis/convergence.py` — NEW: config parser
2. `praxis/__main__.py` — load convergence, create runtimes, pass overrides
3. `praxis/orchestrator.py` — accept + use runtime_overrides
4. `tests/test_convergence.py` — NEW: convergence config tests
5. `tests/test_orchestrator.py` — verify subagent routing with overrides

## What stays the same

- `runtime/` — unchanged (runtimes are still created the same way)
- `config.py` — unchanged
- All existing tests — unchanged behavior (no convergence.yaml = same defaults)

## Dependency

PyYAML — check if already available. If not, add to pyproject.toml.
