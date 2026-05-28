"""Wiki integration — ingest, query, and lint actions for the bitemporal wiki."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from .. import wiki as _wiki

SCHEMAS: dict[str, dict[str, Any]] = {
    "Wiki": {
        "name": "Wiki",
        "description": (
            "Bitemporal personal wiki. "
            "Actions: ingest (add facts to wiki/pages/), "
            "query (answer questions from the wiki), "
            "lint (report schema violations, stale facts, orphans, duplicates, "
            "missing links — never auto-applies)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["ingest", "query", "lint"],
                    "description": "The wiki operation to perform",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "For ingest: path to a file under wiki/raw/ or literal "
                        "fact text to ingest."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": "For query: natural-language question about the user.",
                },
                "include_superseded": {
                    "type": "boolean",
                    "description": (
                        "For query: include superseded (historical) pages in results. "
                        "Default false."
                    ),
                },
                "provenance": {
                    "type": "string",
                    "description": (
                        "For ingest: free-text description of where the fact came from."
                    ),
                },
                "entity_hint": {
                    "type": "string",
                    "description": (
                        "For ingest: slug (with or without .md) to resolve entity "
                        "ambiguity explicitly."
                    ),
                },
                "stale_days": {
                    "type": "integer",
                    "description": (
                        "For lint: days after which a fact is considered stale. "
                        "Default 365 (or PRAXIS_WIKI_STALE_DAYS env var)."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


def execute_wiki(params: dict[str, Any], config: Config) -> str:
    """Dispatch a Wiki tool call to the appropriate wiki function.

    All three actions (ingest, query, lint) delegate entirely to praxis/wiki.py.
    No wiki logic is duplicated here.
    """
    action = params.get("action", "")
    wiki_root = config.workspace_root / "wiki"

    if action == "ingest":
        source_str = params.get("source", "")
        if not source_str:
            return "Error: 'source' is required for ingest action"

        # Determine if source is a path or literal text
        source_path = Path(source_str)
        if source_path.exists():
            source: str | Path = source_path
        else:
            source = source_str

        provenance = params.get("provenance")
        entity_hint = params.get("entity_hint")

        try:
            report = _wiki.ingest(
                source,
                provenance=provenance,
                entity_hint=entity_hint,
            )
        except _wiki.WikiRawImmutableError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error during ingest: {exc}"

        lines = [report.summary()]
        if report.created:
            lines.append(f"Created: {', '.join(report.created)}")
        if report.updated:
            lines.append(f"Updated: {', '.join(report.updated)}")
        if report.skipped:
            lines.append(f"Skipped (idempotent): {', '.join(report.skipped)}")
        if report.ambiguous_entities:
            lines.append(
                f"Blocked (ambiguous): {', '.join(report.ambiguous_entities)} "
                "— provide entity_hint to resolve"
            )
        if report.errors:
            lines.append(f"Errors: {'; '.join(report.errors)}")
        return "\n".join(lines)

    elif action == "query":
        question = params.get("question", "")
        if not question:
            return "Error: 'question' is required for query action"

        include_superseded = bool(params.get("include_superseded", False))

        try:
            result = _wiki.query(
                question,
                wiki_root=wiki_root,
                include_superseded=include_superseded,
            )
        except Exception as exc:
            return f"Error during query: {exc}"

        lines = [
            f"Answer ({result.confidence} confidence): {result.answer}",
        ]
        if result.citations:
            lines.append(f"Citations: {', '.join(result.citations)}")
        if result.notes:
            lines.extend(f"Note: {n}" for n in result.notes)
        return "\n".join(lines)

    elif action == "lint":
        stale_days = params.get("stale_days")
        if stale_days is not None:
            try:
                stale_days = int(stale_days)
            except (TypeError, ValueError):
                return "Error: stale_days must be an integer"

        try:
            report = _wiki.lint(wiki_root=wiki_root, stale_days=stale_days)
        except Exception as exc:
            return f"Error during lint: {exc}"

        lines = [report.summary()]

        if report.frontmatter_errors:
            lines.append(f"\nFrontmatter errors ({len(report.frontmatter_errors)}):")
            for e in report.frontmatter_errors:
                lines.append(f"  {e['page']}: {e['field']} — {e['error']}")

        if report.contradictions:
            lines.append(f"\nContradictions ({len(report.contradictions)}):")
            for c in report.contradictions:
                lines.append(f"  {c['page_a']} <-> {c['page_b']}: {c['note']}")

        if report.stale_facts:
            lines.append(f"\nStale facts ({len(report.stale_facts)}):")
            for s in report.stale_facts:
                lines.append(f"  {s}")

        if report.orphan_pages:
            lines.append(f"\nOrphan pages ({len(report.orphan_pages)}):")
            for o in report.orphan_pages:
                lines.append(f"  {o}")

        if report.duplicate_entities:
            lines.append(f"\nDuplicate entities ({len(report.duplicate_entities)}):")
            for d in report.duplicate_entities:
                lines.append(
                    f"  {d['page_a']} ~= {d['page_b']} (similarity={d['similarity']})"
                )

        if report.missing_links:
            lines.append(f"\nMissing typed links ({len(report.missing_links)}):")
            for m in report.missing_links:
                lines.append(
                    f"  {m['page']}: mentions '{m['mentioned_entity']}' "
                    f"(suggest: {m['suggested_type']})"
                )

        if not report.has_findings:
            lines.append("No issues found.")

        return "\n".join(lines)

    else:
        return f"Error: unknown Wiki action '{action}'. Must be ingest, query, or lint."


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "Wiki": execute_wiki,
}
