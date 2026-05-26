"""Workstation integrations — aggregates tool schemas and implementations."""

from __future__ import annotations

from typing import Any, Callable

from ..config import Config
from . import codebase, dependencies, github, testrunner

# Aggregate all integration tool schemas and implementations
INTEGRATION_SCHEMAS: dict[str, dict[str, Any]] = {
    **github.SCHEMAS,
    **codebase.SCHEMAS,
    **testrunner.SCHEMAS,
    **dependencies.SCHEMAS,
}

INTEGRATION_IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    **github.IMPLEMENTATIONS,
    **codebase.IMPLEMENTATIONS,
    **testrunner.IMPLEMENTATIONS,
    **dependencies.IMPLEMENTATIONS,
}


def get_integration_schemas(
    tool_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return integration tool schemas, optionally filtered by name."""
    if tool_names is None:
        return list(INTEGRATION_SCHEMAS.values())
    return [INTEGRATION_SCHEMAS[n] for n in tool_names if n in INTEGRATION_SCHEMAS]
