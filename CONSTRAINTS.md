# Constraints — Non-negotiable rules for all sessions

Read this before writing a single line of code.

## §5 Governance — INVIOLABLE

- NEVER write outside WORKSPACE_ROOT. NEVER egress to a non-allowlisted domain. NEVER send or publish as the user. NEVER modify .claude/hooks/ or .claude/settings.json. If a task requires it, write the exact patch to STATUS.md under "NEEDS HUMAN" and stop.
- NEVER create send_email(), create_event(), or any autonomous external write function. Write-escalate only: staging functions that save to .praxis/staging/ for human --approve.
- NEVER run `unset` or reassign the §5 hook. It fires on every tool call regardless of permission_mode.

## Code style

- ALWAYS use `from __future__ import annotations` at the top of every new Python file.
- NEVER use bare `except:` — always `except Exception as exc:` or a specific exception type.
- ALWAYS use `pathlib.Path` for file paths, never string concatenation.
- NEVER hardcode credentials. All secrets from `os.environ.get()` only.
- ALWAYS add new env vars to `.env.example` with a comment explaining where to get the value.

## Testing

- NEVER mark a feature as passing without running `python -m pytest tests/ -v` and seeing it pass.
- ALWAYS write tests for new public functions. Minimum one test per new feature.
- NEVER make real API calls in tests. Use `FakeClient` from `tests/conftest.py` for Anthropic; mock external services with `unittest.mock`.
- Tests are SYNCHRONOUS. Do not add pytest-asyncio unless it is already in the project.

## Imports and dependencies

- NEVER add a dependency without checking if stdlib or existing deps can do the job.
- ALWAYS add new optional dependencies to a new `[group]` in `pyproject.toml`, not to `dependencies`.
- NEVER import optional dependencies at module level. Use lazy imports inside functions with `try/except ImportError`.

## Git

- ALWAYS commit after completing a feature. Never leave uncommitted changes.
- Commit format: `feat: [feature-id] brief description`
- NEVER commit `.env`, `wiki/raw/*`, `wiki/pages/*`, `.praxis/memory/*`, `.praxis/staging/*`.

## Feature list

- NEVER modify `feature_list.json` except to set `"passes": true` on your completed feature.
- NEVER mark a feature passing if `python -m pytest tests/ -v` fails.
- ALWAYS update `claude-progress.txt` before committing.

## Auth

- NEVER run with `ANTHROPIC_API_KEY` set. OAuth only (`CLAUDE_CODE_OAUTH_TOKEN`).
- NEVER print, log, echo, or commit any token or credential.
