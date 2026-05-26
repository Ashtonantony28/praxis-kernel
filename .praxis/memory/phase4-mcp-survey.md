# Phase 4 Wave 1 — MCP Server Survey

**Date:** 2026-05-25

## Verdict: Subprocess for all four integrations

No MCP servers are needed. All four targets have mature CLI tools with JSON output that fit Praxis's existing subprocess pattern.

### GitHub
An official MCP server exists (`@modelcontextprotocol/server-github`) covering PRs, issues, search. However, the `gh` CLI is simpler: already installed, outputs JSON via `--json` flag, no protocol overhead, no extra dependencies. Auth via `GITHUB_TOKEN` env var. **Use: `gh` CLI subprocess.**

### Local Codebase Analysis
No mature MCP server for unified static analysis. `pylint --output-format=json`, `radon cc --json`, `coverage json` all produce structured output natively. Subprocess stays within §5 sandbox. **Use: direct subprocess to pylint/radon/coverage.**

### Test Runner
No MCP server for pytest. `pytest --tb=short -q` gives concise output; `pytest-json-report` plugin available for structured data but adds a dependency. Plain pytest subprocess output is sufficient for MVP. **Use: `pytest` subprocess.**

### Dependency Management
No MCP server exists. `pip list --outdated --format=json` and `pip-audit --format=json` both produce structured output. Local-only, sandbox-friendly. **Use: subprocess to pip/pip-audit.**

## Design Implication
Extend `praxis/tools.py` pattern — each integration is a new tool module with subprocess.run, env propagation, secret redaction, and clean error messages. No new protocol layers.
