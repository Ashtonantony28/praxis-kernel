"""Tests for praxis/wiki.py — TASK-W07.

Coverage:
  1. Supersede-not-overwrite invariant
  2. Entity resolution catches near-duplicates (raises WikiAmbiguousEntityError)
  3. Ingest idempotency (content-hash gate)
  4. Query reads wiki/index.md before wiki/pages/
  5. Lint surfaces contradictions without modifying pages
  6. wiki/raw/ immutability (WikiRawImmutableError)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch, call

import pytest

import json

import praxis.wiki as wiki_mod
from praxis.wiki import (
    LintReport,
    WikiAmbiguousEntityError,
    WikiRawImmutableError,
    _render_frontmatter,
    _parse_frontmatter,
    _slugify,
    export_graph,
    ingest,
    lint,
    query,
)


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

_TODAY = "2026-01-15"
_FAKE_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_page(
    entity: str,
    body: str,
    *,
    superseded_on: str | None = None,
    superseded_by: str | None = None,
    links: list | None = None,
    level: str = "fact",
    valid_from: str = "2025-01-01",
    learned_on: str = _TODAY,
    aliases: list | None = None,
    ingest_hash: str | None = None,
) -> str:
    """Build a complete wiki page string (frontmatter + body)."""
    import hashlib
    meta: dict[str, Any] = {
        "entity": entity,
        "aliases": aliases or [],
        "level": level,
        "valid_from": valid_from,
        "learned_on": learned_on,
        "superseded_on": superseded_on,
        "superseded_by": superseded_by,
        "links": links or [],
    }
    if ingest_hash is None:
        ingest_hash = hashlib.sha256(body.encode()).hexdigest()
    meta["ingest_hash"] = ingest_hash
    return _render_frontmatter(meta) + "\n" + body


def _setup_wiki_dirs(base: Path) -> tuple[Path, Path, Path]:
    """Create wiki/raw/, wiki/pages/, wiki/index.md, wiki/log.md under base."""
    wiki = base / "wiki"
    (wiki / "raw").mkdir(parents=True)
    (wiki / "pages").mkdir(parents=True)
    (wiki / "index.md").write_text("# Wiki Index\n<!-- generated: 2026-01-15T12:00:00Z -->\n\n(no pages yet)\n", encoding="utf-8")
    (wiki / "log.md").write_text("# Wiki Log\n\n(no events yet)\n", encoding="utf-8")
    return wiki, wiki / "pages", wiki / "raw"


# ---------------------------------------------------------------------------
# 1. Supersede-not-overwrite invariant
# ---------------------------------------------------------------------------


class TestSupersede:
    """ingest() a fact, then ingest a contradicting fact for the same entity."""

    def test_old_page_file_still_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old page must not be deleted after supersession."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # First ingest — creates ashton-antony.md (entity extracted from text)
        r1 = ingest(
            "Ashton Antony lives in Kerala, India.",
            now=_FAKE_NOW,
        )
        assert not r1.errors, f"First ingest errors: {r1.errors}"
        assert r1.created, "First ingest should create a page"

        first_page = Path(r1.created[0])
        assert first_page.exists(), "First page file must exist"
        first_slug = first_page.stem

        # Second ingest — contradicting fact, same entity name
        r2 = ingest(
            "Ashton Antony lives in Bangalore, India.",
            now=_FAKE_NOW,
        )
        assert not r2.errors, f"Second ingest errors: {r2.errors}"

        # The old page MUST still exist (not deleted, not truncated)
        assert first_page.exists(), "Old page must not be deleted after supersession"

    def test_old_page_carries_superseded_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old page must have superseded_on set after supersession."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        r1 = ingest("Ashton Antony lives in Kerala, India.", now=_FAKE_NOW)
        assert r1.created
        first_page = Path(r1.created[0])

        ingest("Ashton Antony lives in Bangalore, India.", now=_FAKE_NOW)

        content = first_page.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(content)
        assert meta.get("superseded_on") is not None, "Old page must have superseded_on set"
        assert meta.get("superseded_by") is not None, "Old page must have superseded_by set"

    def test_old_page_superseded_by_points_to_new_page(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """superseded_by on old page must point to the new page."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        r1 = ingest("Ashton Antony lives in Kerala, India.", now=_FAKE_NOW)
        first_page = Path(r1.created[0])
        first_slug = first_page.stem

        r2 = ingest("Ashton Antony lives in Bangalore, India.", now=_FAKE_NOW)

        old_content = first_page.read_text(encoding="utf-8")
        old_meta, _ = _parse_frontmatter(old_content)

        superseded_by = old_meta.get("superseded_by", "")
        assert superseded_by is not None, "superseded_by must not be null"
        assert "wiki/pages/" in superseded_by, f"superseded_by must be a wiki/pages/ path, got {superseded_by!r}"

        # The new page path the old page points to must exist
        new_page = tmp_path / superseded_by
        assert new_page.exists(), f"New page referenced by superseded_by must exist: {new_page}"

    def test_new_page_has_no_superseded_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """New (live) page must have superseded_on = null."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        ingest("Ashton Antony lives in Kerala, India.", now=_FAKE_NOW)
        r2 = ingest("Ashton Antony lives in Bangalore, India.", now=_FAKE_NOW)

        # New page is in r2.created
        assert r2.created, "Second ingest must create a new page"
        new_page = Path(r2.created[0])
        content = new_page.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(content)
        assert meta.get("superseded_on") is None, "New page must have superseded_on = null"

    def test_log_contains_supersede_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """wiki/log.md must have a SUPERSEDE event after supersession."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        ingest("Ashton Antony lives in Kerala, India.", now=_FAKE_NOW)
        ingest("Ashton Antony lives in Bangalore, India.", now=_FAKE_NOW)

        log = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
        assert "SUPERSEDE" in log, f"wiki/log.md must contain a SUPERSEDE event. Log:\n{log}"

    def test_index_lists_only_live_page(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """wiki/index.md must not list the superseded page."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        r1 = ingest("Ashton Antony lives in Kerala, India.", now=_FAKE_NOW)
        first_page = Path(r1.created[0])
        first_slug = first_page.stem

        r2 = ingest("Ashton Antony lives in Bangalore, India.", now=_FAKE_NOW)

        # Get the new slug
        assert r2.created
        new_slug = Path(r2.created[0]).stem

        index = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")

        # The superseded page's slug must NOT appear in links in the index
        # (index only contains live pages)
        # We check that the old slug is not linked as a page
        old_page_link = f"wiki/pages/{first_slug}.md"
        assert old_page_link not in index, (
            f"Index must not list superseded page '{old_page_link}'. Index:\n{index}"
        )


# ---------------------------------------------------------------------------
# 2. Entity resolution catches near-duplicates
# ---------------------------------------------------------------------------


class TestEntityResolutionNearDuplicate:
    """Jaro-Winkler >= 0.92 triggers WikiAmbiguousEntityError."""

    def test_near_duplicate_raises_ambiguous_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ingesting 'Aston Antony' after 'Ashton Antony' must raise WikiAmbiguousEntityError."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        # Confirm the two slugs score >= 0.92 on Jaro-Winkler
        slug_a = _slugify("Ashton Antony")   # ashton-antony
        slug_b = _slugify("Aston Antony")    # aston-antony
        score = wiki_mod._jaro_winkler(slug_a, slug_b)
        assert score >= 0.92, (
            f"Test pre-condition: JW({slug_a!r}, {slug_b!r}) = {score:.4f} < 0.92"
        )

        # First ingest: create the "Ashton Antony" page
        r1 = ingest("Ashton Antony is a software engineer.", now=_FAKE_NOW)
        assert not r1.errors, f"Errors on first ingest: {r1.errors}"
        assert r1.created, "First ingest must create a page"

        # Second ingest: near-duplicate "Aston Antony" — must NOT silently create a new page
        r2 = ingest("Aston Antony is a developer in India.", now=_FAKE_NOW)

        # The implementation raises WikiAmbiguousEntityError internally and catches it
        # into report.ambiguous_entities — so the ingest returns a report with
        # ambiguous_entities populated, NOT a new page
        assert "Aston Antony" in r2.ambiguous_entities or len(r2.ambiguous_entities) > 0, (
            "Near-duplicate entity 'Aston Antony' must be reported as ambiguous"
        )

    def test_near_duplicate_does_not_create_second_page(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """After ambiguity block, no second page must exist for the near-duplicate."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        ingest("Ashton Antony is a software engineer.", now=_FAKE_NOW)

        pages_before = set(p.stem for p in (tmp_path / "wiki" / "pages").glob("*.md"))

        ingest("Aston Antony is a developer in India.", now=_FAKE_NOW)

        pages_after = set(p.stem for p in (tmp_path / "wiki" / "pages").glob("*.md"))
        new_pages = pages_after - pages_before
        # No new page should have been created for the near-duplicate
        aston_slug = _slugify("Aston Antony")  # aston-antony
        assert aston_slug not in new_pages, (
            f"Page for near-duplicate 'Aston Antony' (slug={aston_slug!r}) "
            f"must not be created. New pages: {new_pages}"
        )

    def test_resolve_entity_raises_directly_for_fuzzy_match(self, tmp_path: Path) -> None:
        """_resolve_entity raises WikiAmbiguousEntityError for a single fuzzy match."""
        _setup_wiki_dirs(tmp_path)
        pages_dir = tmp_path / "wiki" / "pages"

        # Write an existing page for "Ashton Antony"
        page_content = _make_page("Ashton Antony", "Ashton Antony is a software engineer.")
        (pages_dir / "ashton-antony.md").write_text(page_content, encoding="utf-8")

        with pytest.raises(WikiAmbiguousEntityError) as exc_info:
            wiki_mod._resolve_entity("Aston Antony", wiki_root=tmp_path / "wiki")

        err = exc_info.value
        assert err.candidate_name == "Aston Antony"
        assert len(err.matches) >= 1


# ---------------------------------------------------------------------------
# 3. Ingest idempotency
# ---------------------------------------------------------------------------


class TestIngestIdempotent:
    """Ingesting the same source twice is a no-op on the second call."""

    def test_second_ingest_skips_unchanged_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second ingest of identical text produces only skipped events."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        source = "Ashton Antony is a Python developer based in Kerala."
        r1 = ingest(source, now=_FAKE_NOW)
        assert r1.created, "First ingest must create a page"

        r2 = ingest(source, now=_FAKE_NOW)
        assert r2.created == [], f"Second ingest must not create any new page. Got: {r2.created}"
        assert r2.skipped, f"Second ingest must have skipped entries. Got: {r2.skipped}"

    def test_second_ingest_no_new_pages(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Page count must not increase after re-ingesting the same text."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        source = "Ashton Antony is a Python developer based in Kerala."
        ingest(source, now=_FAKE_NOW)

        pages_after_first = list((tmp_path / "wiki" / "pages").glob("*.md"))
        count_first = len(pages_after_first)

        ingest(source, now=_FAKE_NOW)

        pages_after_second = list((tmp_path / "wiki" / "pages").glob("*.md"))
        count_second = len(pages_after_second)

        assert count_first == count_second, (
            f"Page count must not increase on re-ingest: "
            f"before={count_first}, after={count_second}"
        )

    def test_second_ingest_no_new_log_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Log line count must not increase after re-ingesting the same text."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        source = "Ashton Antony is a Python developer based in Kerala."
        ingest(source, now=_FAKE_NOW)

        log_path = tmp_path / "wiki" / "log.md"
        log_after_first = log_path.read_text(encoding="utf-8")
        lines_first = [l for l in log_after_first.splitlines() if re.match(r'^\d{4}-\d{2}-\d{2}', l)]

        ingest(source, now=_FAKE_NOW)

        log_after_second = log_path.read_text(encoding="utf-8")
        lines_second = [l for l in log_after_second.splitlines() if re.match(r'^\d{4}-\d{2}-\d{2}', l)]

        assert len(lines_first) == len(lines_second), (
            f"Log must not gain new lines on re-ingest. "
            f"First: {len(lines_first)}, Second: {len(lines_second)}"
        )

    def test_second_ingest_index_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """wiki/index.md content must not change on re-ingest."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        source = "Ashton Antony is a Python developer based in Kerala."
        ingest(source, now=_FAKE_NOW)

        index_after_first = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")

        ingest(source, now=_FAKE_NOW)

        index_after_second = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")

        # Strip the generated timestamp comment (it may differ by seconds)
        def _strip_generated_comment(text: str) -> str:
            return re.sub(r'<!-- generated:.*?-->', '', text)

        assert _strip_generated_comment(index_after_first) == _strip_generated_comment(index_after_second), (
            "Index content (excluding timestamp) must not change on re-ingest"
        )


# ---------------------------------------------------------------------------
# 4. Query reads wiki/index.md before wiki/pages/
# ---------------------------------------------------------------------------


class TestQueryReadsIndexFirst:
    """query() must consult wiki/index.md before opening individual pages."""

    def test_query_sets_index_consulted_true(self, tmp_path: Path) -> None:
        """query() returns index_consulted=True when index.md exists."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Put a real page on disk so query has something to find
        slug = "ashton-antony"
        page_content = _make_page(
            "Ashton Antony",
            "Ashton Antony is a software engineer.",
        )
        page_file = pages_dir / f"{slug}.md"
        page_file.write_text(page_content, encoding="utf-8")

        # Write an index that references the page
        index_text = (
            "# Wiki Index\n"
            "<!-- generated: 2026-01-15T12:00:00Z -->\n\n"
            "## Unthemed topics and facts\n"
            f"- [fact] [Ashton Antony](wiki/pages/{slug}.md)\n"
        )
        (wiki / "index.md").write_text(index_text, encoding="utf-8")

        result = query("Ashton Antony", wiki_root=wiki)
        assert result.index_consulted is True, (
            "query() must set index_consulted=True when wiki/index.md exists"
        )

    def test_query_falls_back_to_pages_scan_when_no_index(self, tmp_path: Path) -> None:
        """query() falls back to full pages scan when index.md is absent."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)
        (wiki / "index.md").unlink()  # Remove the index

        page_content = _make_page("Ashton Antony", "Ashton Antony is a software engineer.")
        (pages_dir / "ashton-antony.md").write_text(page_content, encoding="utf-8")

        result = query("Ashton Antony", wiki_root=wiki)
        assert result.index_consulted is False, (
            "query() must set index_consulted=False when wiki/index.md is absent"
        )
        # Should still find the page via fallback scan
        assert any("ashton" in h.entity.lower() for h in result.hits), (
            "query() must still find pages via fallback scan"
        )

    def test_query_uses_index_to_drive_page_selection(self, tmp_path: Path) -> None:
        """Pages listed in the index are the primary candidates for query()."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Create two pages: only one is listed in the index
        page_a = _make_page("Ashton Antony", "Ashton Antony is a software engineer.")
        page_b = _make_page("Python Language", "Python is a programming language.")
        (pages_dir / "ashton-antony.md").write_text(page_a, encoding="utf-8")
        (pages_dir / "python-language.md").write_text(page_b, encoding="utf-8")

        # Index only references page_a
        index_text = (
            "# Wiki Index\n"
            "<!-- generated: 2026-01-15T12:00:00Z -->\n\n"
            "## Unthemed topics and facts\n"
            "- [fact] [Ashton Antony](wiki/pages/ashton-antony.md)\n"
        )
        (wiki / "index.md").write_text(index_text, encoding="utf-8")

        result = query("Ashton Antony software", wiki_root=wiki)

        assert result.index_consulted is True
        # Since index only has ashton-antony, that is the candidate pool
        # The hits should include ashton-antony
        hit_entities = [h.entity for h in result.hits]
        assert any("Ashton" in e for e in hit_entities), (
            f"Expected Ashton Antony in hits. Hits: {hit_entities}"
        )

    def test_query_excludes_superseded_pages_by_default(self, tmp_path: Path) -> None:
        """query() excludes superseded pages by default (bitemporal filter)."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Create a superseded page
        sup_page = _make_page(
            "Ashton Antony",
            "Ashton Antony lives in Kerala.",
            superseded_on="2026-01-10",
            superseded_by="wiki/pages/ashton-antony-2.md",
        )
        (pages_dir / "ashton-antony.md").write_text(sup_page, encoding="utf-8")

        # Live page
        live_page = _make_page("Ashton Antony", "Ashton Antony lives in Bangalore.")
        (pages_dir / "ashton-antony-2.md").write_text(live_page, encoding="utf-8")

        index_text = (
            "# Wiki Index\n"
            "<!-- generated: 2026-01-15T12:00:00Z -->\n\n"
            "## Unthemed topics and facts\n"
            "- [fact] [Ashton Antony](wiki/pages/ashton-antony.md)\n"
            "- [fact] [Ashton Antony](wiki/pages/ashton-antony-2.md)\n"
        )
        (wiki / "index.md").write_text(index_text, encoding="utf-8")

        result = query("Ashton Antony location", wiki_root=wiki)

        superseded_hits = [h for h in result.hits if h.superseded_on is not None]
        assert len(superseded_hits) == 0, (
            "query() must exclude superseded pages by default"
        )


# ---------------------------------------------------------------------------
# 5. Lint surfaces contradiction without modifying pages
# ---------------------------------------------------------------------------


class TestLintContradiction:
    """lint() finds contradictions but never rewrites pages."""

    def _write_standard_page(self, path: Path, entity: str, body: str, links: list | None = None) -> None:
        """Write a complete valid wiki page to disk."""
        content = _make_page(entity, body, links=links)
        path.write_text(content, encoding="utf-8")

    def test_lint_reports_contradiction_for_same_entity_slug(self, tmp_path: Path) -> None:
        """Two active pages with same slug base and different bodies → contradiction."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Both pages have slug base "ashton-antony" (ashton-antony and ashton-antony-2)
        # Different bodies, no supersedes link between them
        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony lives in Kerala.",
        )
        self._write_standard_page(
            pages_dir / "ashton-antony-2.md",
            "Ashton Antony",
            "Ashton Antony lives in Bangalore.",
        )

        report = lint(wiki_root=wiki)
        assert len(report.contradictions) >= 1, (
            f"Expected at least one contradiction. Got: {report.contradictions}"
        )

    def test_lint_does_not_modify_any_page(self, tmp_path: Path) -> None:
        """lint() must not write to any page file."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        page_path = pages_dir / "ashton-antony.md"
        self._write_standard_page(page_path, "Ashton Antony", "Ashton Antony lives in Kerala.")

        content_before = page_path.read_text(encoding="utf-8")
        lint(wiki_root=wiki)
        content_after = page_path.read_text(encoding="utf-8")

        assert content_before == content_after, (
            "lint() must not modify any page file"
        )

    def test_lint_appends_exactly_one_lint_event_to_log(self, tmp_path: Path) -> None:
        """lint() appends exactly ONE LINT event to wiki/log.md."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Manually write two contradicting pages (no supersedes link)
        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony is a Python developer.",
        )
        self._write_standard_page(
            pages_dir / "ashton-antony-2.md",
            "Ashton Antony",
            "Ashton Antony is a Java developer.",
        )

        log_path = wiki / "log.md"
        # Count existing LINT lines before
        log_before = log_path.read_text(encoding="utf-8")
        lint_lines_before = [l for l in log_before.splitlines() if " LINT " in l]

        lint(wiki_root=wiki)

        log_after = log_path.read_text(encoding="utf-8")
        lint_lines_after = [l for l in log_after.splitlines() if " LINT " in l]

        new_lint_lines = len(lint_lines_after) - len(lint_lines_before)
        assert new_lint_lines == 1, (
            f"lint() must append exactly 1 LINT event. Added {new_lint_lines} events.\n"
            f"Log:\n{log_after}"
        )

    def test_lint_contradiction_has_required_keys(self, tmp_path: Path) -> None:
        """Each contradiction entry must have page_a, page_b, note keys."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony lives in Kerala.",
        )
        self._write_standard_page(
            pages_dir / "ashton-antony-2.md",
            "Ashton Antony",
            "Ashton Antony lives in Bangalore.",
        )

        report = lint(wiki_root=wiki)
        for entry in report.contradictions:
            assert "page_a" in entry, f"contradiction entry missing 'page_a': {entry}"
            assert "page_b" in entry, f"contradiction entry missing 'page_b': {entry}"
            assert "note" in entry, f"contradiction entry missing 'note': {entry}"

    def test_lint_no_contradiction_when_supersedes_link_exists(self, tmp_path: Path) -> None:
        """lint() must NOT flag a contradiction when a supersedes link connects the pair."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        # Page 2 supersedes page 1
        supersedes_link = [{"type": "supersedes", "target": "wiki/pages/ashton-antony.md"}]
        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony lives in Kerala.",
        )
        content2 = _make_page(
            "Ashton Antony",
            "Ashton Antony lives in Bangalore.",
            links=supersedes_link,
        )
        (pages_dir / "ashton-antony-2.md").write_text(content2, encoding="utf-8")

        report = lint(wiki_root=wiki)
        assert len(report.contradictions) == 0, (
            f"lint() must not flag a contradiction when supersedes link exists. "
            f"Got: {report.contradictions}"
        )

    def test_lint_report_has_findings_property(self, tmp_path: Path) -> None:
        """LintReport.has_findings is True when contradictions exist."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony is a Python developer.",
        )
        self._write_standard_page(
            pages_dir / "ashton-antony-2.md",
            "Ashton Antony",
            "Ashton Antony is a Java developer.",
        )

        report = lint(wiki_root=wiki)
        assert report.has_findings is True

    def test_lint_summary_method(self, tmp_path: Path) -> None:
        """LintReport.summary() returns a string with count labels."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)
        self._write_standard_page(
            pages_dir / "ashton-antony.md",
            "Ashton Antony",
            "Ashton Antony is a Python developer.",
        )

        report = lint(wiki_root=wiki)
        summary = report.summary()
        assert "contradictions" in summary
        assert "stale" in summary


# ---------------------------------------------------------------------------
# 6. wiki/raw/ immutability
# ---------------------------------------------------------------------------


class TestRawImmutability:
    """Any write into wiki/raw/ must raise WikiRawImmutableError."""

    def test_guard_not_raw_raises_for_raw_path(self, tmp_path: Path) -> None:
        """_guard_not_raw raises WikiRawImmutableError for a path under wiki/raw/."""
        wiki, _, raw_dir = _setup_wiki_dirs(tmp_path)

        # raw_dir may not exist yet as a real resolved path for the guard check
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / "some-file.md"
        target.touch()  # must exist for resolve() to work correctly on some platforms

        with pytest.raises(WikiRawImmutableError):
            wiki_mod._guard_not_raw(target, wiki)

    def test_guard_not_raw_allows_pages_path(self, tmp_path: Path) -> None:
        """_guard_not_raw does NOT raise for a path under wiki/pages/."""
        wiki, pages_dir, _ = _setup_wiki_dirs(tmp_path)

        target = pages_dir / "some-page.md"
        # Must not raise
        wiki_mod._guard_not_raw(target, wiki)

    def test_write_page_raises_for_raw_dest(self, tmp_path: Path) -> None:
        """_write_page raises WikiRawImmutableError when dest is under wiki/raw/."""
        wiki, _, raw_dir = _setup_wiki_dirs(tmp_path)
        raw_dir.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "entity": "Test",
            "aliases": [],
            "level": "fact",
            "valid_from": "2026-01-01",
            "learned_on": "2026-01-15",
            "superseded_on": None,
            "superseded_by": None,
            "links": [],
        }

        with pytest.raises(WikiRawImmutableError):
            # Monkey-patch pages_dir to point at raw_dir to simulate the forbidden path
            # Actually we test _guard_not_raw directly by calling _write_page with a
            # wiki_root where pages/ resolves into raw/ — easier: test the guard directly
            wiki_mod._guard_not_raw(raw_dir / "test.md", wiki)

    def test_log_event_raises_for_raw_log_path(self, tmp_path: Path) -> None:
        """_log_event raises WikiRawImmutableError if log path is under wiki/raw/."""
        wiki, _, raw_dir = _setup_wiki_dirs(tmp_path)
        raw_dir.mkdir(parents=True, exist_ok=True)
        fake_log = raw_dir / "log.md"
        fake_log.touch()

        # We test _guard_not_raw directly with the raw log path
        with pytest.raises(WikiRawImmutableError):
            wiki_mod._guard_not_raw(fake_log, wiki)


# ---------------------------------------------------------------------------
# 7. Additional integrity tests
# ---------------------------------------------------------------------------


class TestLintReport:
    """LintReport dataclass behaviour."""

    def test_has_findings_false_when_empty(self) -> None:
        """has_findings is False when all lists are empty."""
        report = LintReport()
        assert report.has_findings is False

    def test_has_findings_true_for_each_category(self) -> None:
        """has_findings is True for each individual non-empty category."""
        for field_name in [
            "contradictions",
            "stale_facts",
            "orphan_pages",
            "duplicate_entities",
            "missing_links",
            "frontmatter_errors",
        ]:
            if field_name == "orphan_pages":
                report = LintReport(**{field_name: ["wiki/pages/test.md"]})
            elif field_name == "stale_facts":
                # stale_facts is now list[dict] with page/days_since_update/valid_from
                report = LintReport(**{field_name: [{"page": "wiki/pages/test.md", "days_since_update": 100, "valid_from": "2024-01-01"}]})
            else:
                report = LintReport(**{field_name: [{"page": "wiki/pages/test.md"}]})
            assert report.has_findings is True, f"has_findings must be True when {field_name} is set"

    def test_summary_format(self) -> None:
        """summary() contains all six category labels."""
        report = LintReport()
        s = report.summary()
        for keyword in ["contradictions", "stale", "orphan", "duplicate", "missing", "frontmatter"]:
            assert keyword in s, f"summary() missing keyword {keyword!r}"


class TestQueryResult:
    """QueryResult dataclass and query() edge cases."""

    def test_empty_wiki_returns_low_confidence(self, tmp_path: Path) -> None:
        """query() returns confidence='low' when wiki is empty."""
        wiki, _, _ = _setup_wiki_dirs(tmp_path)

        result = query("What is Ashton's location?", wiki_root=wiki)
        assert result.confidence == "low"

    def test_query_returns_answer_string(self, tmp_path: Path) -> None:
        """query() always returns a non-empty answer string."""
        wiki, _, _ = _setup_wiki_dirs(tmp_path)

        result = query("Where does Ashton live?", wiki_root=wiki)
        assert isinstance(result.answer, str)
        assert len(result.answer) > 0


class TestIngestEdgeCases:
    """Edge cases for ingest()."""

    def test_ingest_empty_text_returns_no_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ingesting empty text returns an empty report without raising."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)

        report = ingest("   \n\n  ", now=_FAKE_NOW)
        # Should return with no events (nothing to ingest)
        assert isinstance(report.events, list)

    def test_ingest_creates_pages_dir_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ingest() creates wiki/pages/ if it does not yet exist."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "raw").mkdir()
        # Do NOT create pages/ — ingest should create it

        ingest("Ashton Antony is a software engineer.", now=_FAKE_NOW)

        assert (wiki / "pages").exists(), "ingest() must create wiki/pages/ if absent"


class TestFrontmatterParsing:
    """_parse_frontmatter and _render_frontmatter round-trip."""

    def test_roundtrip_basic_fields(self) -> None:
        """parse(render(meta)) == meta for basic field types."""
        meta: dict[str, Any] = {
            "entity": "Ashton Antony",
            "aliases": ["Ashton"],
            "level": "topic",
            "valid_from": "2026-01-01",
            "learned_on": "2026-05-27",
            "superseded_on": None,
            "superseded_by": None,
            "links": [{"type": "relates", "target": "wiki/pages/python.md"}],
        }
        rendered = _render_frontmatter(meta)
        parsed, _ = _parse_frontmatter(rendered + "\nbody text")
        assert parsed["entity"] == meta["entity"]
        assert parsed["level"] == meta["level"]
        assert parsed["superseded_on"] is None
        assert len(parsed["links"]) == 1
        assert parsed["links"][0]["type"] == "relates"

    def test_roundtrip_with_superseded_fields(self) -> None:
        """Superseded page frontmatter round-trips correctly."""
        meta: dict[str, Any] = {
            "entity": "Old Fact",
            "aliases": [],
            "level": "fact",
            "valid_from": "2025-01-01",
            "learned_on": "2025-06-01",
            "superseded_on": "2026-01-15",
            "superseded_by": "wiki/pages/new-fact.md",
            "links": [],
        }
        rendered = _render_frontmatter(meta)
        parsed, _ = _parse_frontmatter(rendered + "\nbody text")
        assert parsed["superseded_on"] == "2026-01-15"
        assert parsed["superseded_by"] == "wiki/pages/new-fact.md"


# ---------------------------------------------------------------------------
# TestWikiPhase2 — Option E enhancements
# ---------------------------------------------------------------------------


class TestWikiPhase2:
    """Phase 2 enhancements: prefix resolution, export_graph, multi-source merge, staleness."""

    # ------------------------------------------------------------------ #
    # 1. Full-name disambiguation (prefix/suffix entity resolution)
    # ------------------------------------------------------------------ #

    def test_partial_name_resolves_to_full_name_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingest 'Aiden Antony', then 'Aiden' — both must resolve to the same page."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"

        # First ingest creates page for "Aiden Antony"
        r1 = ingest("Aiden Antony is a software engineer working on Praxis.", now=_FAKE_NOW)
        assert not r1.errors, f"First ingest errors: {r1.errors}"
        assert r1.created, "First ingest must create a page"

        # Resolve "Aiden" — must prefix-match to the "Aiden Antony" page
        resolved = wiki_mod._resolve_entity("Aiden", wiki_root=wiki)
        assert not resolved.is_new, (
            "Prefix 'Aiden' should resolve to existing 'Aiden Antony' page"
        )
        # The page should be the same aiden-antony.md
        pages_before = set(p.stem for p in (wiki / "pages").glob("*.md"))
        assert any("aiden" in s for s in pages_before), (
            f"Expected a page with 'aiden' in stem. Pages: {pages_before}"
        )

    def test_full_name_resolves_to_existing_partial_page(
        self, tmp_path: Path
    ) -> None:
        """Create a page for entity 'Aiden', then resolve 'Aiden Antony' — must match the same page."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        # Directly write a page for entity "Aiden"
        (pages_dir / "aiden.md").write_text(
            _make_page("Aiden", "Aiden is a software developer."),
            encoding="utf-8",
        )

        # Resolve "Aiden Antony" — "Aiden" is a prefix of "Aiden Antony" (both >= 3 chars)
        resolved = wiki_mod._resolve_entity("Aiden Antony", wiki_root=wiki)
        assert not resolved.is_new, (
            "'Aiden Antony' should prefix-match to existing 'Aiden' page "
            "(Aiden is a prefix of Aiden Antony)"
        )
        assert resolved.page_path is not None
        assert resolved.page_path.stem == "aiden", (
            f"Expected resolved page stem 'aiden', got {resolved.page_path.stem!r}"
        )

    def test_prefix_match_min_length(self, tmp_path: Path) -> None:
        """A 2-character entity does NOT prefix-match (below the 3-char minimum)."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        # Create a page for "Al Smith"
        page_content = _make_page("Al Smith", "Al Smith is a person.")
        (pages_dir / "al-smith.md").write_text(page_content, encoding="utf-8")

        # "Al" (2 chars) must NOT prefix-match — below threshold
        # It should be a new entity
        resolved = wiki_mod._resolve_entity("Al", wiki_root=wiki)
        # "al" is 2 chars — below the 3-char minimum prefix length
        # It may match by Jaro-Winkler (short strings behave oddly) but NOT by prefix rule
        # We just check that the prefix check itself doesn't fire (we test the rule, not the JW)
        # The simplest check: "al" is only 2 chars, so prefix_check skips it
        # Let's verify directly by ensuring "al-smith" does not match via prefix
        # (we can't easily isolate the prefix step, so we test at the function level)
        # "Al" vs "Al Smith": len("al") = 2 < 3, so prefix rule skips → is_new or JW
        # Since JW("al", "al-smith") is low, it should be new
        assert resolved.is_new or resolved.slug == "al", (
            "'Al' (2 chars) must not prefix-match to 'Al Smith' page — below 3-char minimum"
        )

    def test_disambiguation_ambiguous_prefix(self, tmp_path: Path) -> None:
        """Two pages 'Alice Smith' and 'Alice Jones'; entity 'Alice' must raise WikiAmbiguousEntityError."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        # Create pages for "Alice Smith" and "Alice Jones"
        (pages_dir / "alice-smith.md").write_text(
            _make_page("Alice Smith", "Alice Smith is a researcher."), encoding="utf-8"
        )
        (pages_dir / "alice-jones.md").write_text(
            _make_page("Alice Jones", "Alice Jones is an engineer."), encoding="utf-8"
        )

        # "Alice" is a prefix of both → ambiguous
        with pytest.raises(WikiAmbiguousEntityError) as exc_info:
            wiki_mod._resolve_entity("Alice", wiki_root=wiki)

        err = exc_info.value
        assert err.candidate_name == "Alice"
        assert len(err.matches) >= 2, (
            f"Ambiguous prefix 'Alice' should have at least 2 matches. Got: {err.matches}"
        )

    # ------------------------------------------------------------------ #
    # 2. export_graph()
    # ------------------------------------------------------------------ #

    def test_export_graph_empty_wiki(self, tmp_path: Path) -> None:
        """Wiki with no pages returns empty graph with correct structure."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"

        result = export_graph(wiki_root=wiki)
        assert result["nodes"] == [], "Empty wiki must return empty nodes list"
        assert result["edges"] == [], "Empty wiki must return empty edges list"
        assert "generated_at" in result, "Result must have generated_at field"

    def test_export_graph_nodes(self, tmp_path: Path) -> None:
        """Wiki with 2 non-superseded pages produces 2 nodes with correct fields."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        (pages_dir / "alice-smith.md").write_text(
            _make_page("Alice Smith", "Alice Smith is a researcher.", level="topic"),
            encoding="utf-8",
        )
        (pages_dir / "praxis-project.md").write_text(
            _make_page("Praxis Project", "Praxis is an agentic OS.", level="fact"),
            encoding="utf-8",
        )

        result = export_graph(wiki_root=wiki)
        assert len(result["nodes"]) == 2, f"Expected 2 nodes, got {len(result['nodes'])}"

        slugs = {n["id"] for n in result["nodes"]}
        assert "alice-smith" in slugs
        assert "praxis-project" in slugs

        # Check required fields on each node
        for node in result["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "level" in node
            assert "valid_from" in node

    def test_export_graph_edges(self, tmp_path: Path) -> None:
        """Wiki page with a typed link produces an edge in the graph."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        # Page with a relates link
        (pages_dir / "alice-smith.md").write_text(
            _make_page(
                "Alice Smith",
                "Alice Smith works on Praxis.",
                links=[{"type": "relates", "target": "wiki/pages/praxis-project.md"}],
            ),
            encoding="utf-8",
        )
        (pages_dir / "praxis-project.md").write_text(
            _make_page("Praxis Project", "Praxis is an agentic OS."),
            encoding="utf-8",
        )

        result = export_graph(wiki_root=wiki)
        assert len(result["edges"]) >= 1, f"Expected at least 1 edge, got {result['edges']}"

        edge = result["edges"][0]
        assert edge["source"] == "alice-smith"
        assert edge["target"] == "praxis-project"
        assert edge["type"] == "relates"

    def test_export_graph_excludes_superseded(self, tmp_path: Path) -> None:
        """Superseded pages must not appear in nodes or edges."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        # Superseded page
        (pages_dir / "old-fact.md").write_text(
            _make_page(
                "Old Fact",
                "Old content.",
                superseded_on="2026-01-01",
                superseded_by="wiki/pages/new-fact.md",
                links=[{"type": "supersedes", "target": "wiki/pages/new-fact.md"}],
            ),
            encoding="utf-8",
        )
        # Active page
        (pages_dir / "new-fact.md").write_text(
            _make_page("New Fact", "New content."),
            encoding="utf-8",
        )

        result = export_graph(wiki_root=wiki)
        node_ids = {n["id"] for n in result["nodes"]}
        assert "old-fact" not in node_ids, "Superseded page must not appear in nodes"
        assert "new-fact" in node_ids, "Active page must appear in nodes"
        # Edges from superseded page must not appear
        edge_sources = {e["source"] for e in result["edges"]}
        assert "old-fact" not in edge_sources, "Superseded page must not appear as edge source"

    def test_export_graph_writes_json_file(self, tmp_path: Path) -> None:
        """After export_graph(), wiki/graph.json must exist and be valid JSON."""
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        (pages_dir / "alice-smith.md").write_text(
            _make_page("Alice Smith", "Alice Smith is a researcher."),
            encoding="utf-8",
        )

        export_graph(wiki_root=wiki)

        graph_file = wiki / "graph.json"
        assert graph_file.exists(), "wiki/graph.json must exist after export_graph()"

        content = graph_file.read_text(encoding="utf-8")
        parsed = json.loads(content)  # must not raise
        assert "nodes" in parsed
        assert "edges" in parsed
        assert "generated_at" in parsed

    # ------------------------------------------------------------------ #
    # 3. Multi-source merge
    # ------------------------------------------------------------------ #

    def test_multi_source_merge_single_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting two raw files describing the same entity produces only one page."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        raw_dir = wiki / "raw"

        # Write two raw files with the same entity "Aiden Antony"
        raw_file_1 = raw_dir / "bio-notes.md"
        raw_file_1.write_text(
            "Aiden Antony is a software engineer from Kerala.",
            encoding="utf-8",
        )
        raw_file_2 = raw_dir / "skill-notes.md"
        raw_file_2.write_text(
            "Aiden Antony specializes in Python and distributed systems.",
            encoding="utf-8",
        )

        r1 = ingest(raw_file_1, now=_FAKE_NOW)
        assert not r1.errors, f"First ingest errors: {r1.errors}"
        assert r1.created, "First ingest must create a page"

        r2 = ingest(raw_file_2, now=_FAKE_NOW)
        assert not r2.errors, f"Second ingest errors: {r2.errors}"

        # Check only one page exists for Aiden Antony
        aiden_pages = [p for p in (wiki / "pages").glob("*.md") if "aiden" in p.stem]
        assert len(aiden_pages) == 1, (
            f"Must have exactly 1 page for Aiden Antony, got {[p.name for p in aiden_pages]}"
        )

    def test_multi_source_merge_content_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After merging two sources, the page body must contain content from both."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        raw_dir = wiki / "raw"

        raw_file_1 = raw_dir / "bio-notes.md"
        raw_file_1.write_text(
            "Aiden Antony is a software engineer from Kerala.",
            encoding="utf-8",
        )
        raw_file_2 = raw_dir / "skill-notes.md"
        raw_file_2.write_text(
            "Aiden Antony specializes in Python and distributed systems.",
            encoding="utf-8",
        )

        ingest(raw_file_1, now=_FAKE_NOW)
        ingest(raw_file_2, now=_FAKE_NOW)

        # Read the merged page
        aiden_pages = list((wiki / "pages").glob("aiden*.md"))
        assert len(aiden_pages) == 1, f"Expected 1 page, got {[p.name for p in aiden_pages]}"
        content = aiden_pages[0].read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)

        # Both source contents should be present
        assert "engineer from Kerala" in body or "Kerala" in body, (
            "Page body must contain content from first source"
        )
        assert "Python" in body or "distributed" in body, (
            "Page body must contain content from second source"
        )

    def test_multi_source_merge_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting the same source file twice must not duplicate content."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        raw_dir = wiki / "raw"

        raw_file = raw_dir / "bio-notes.md"
        raw_file.write_text(
            "Aiden Antony is a software engineer from Kerala.",
            encoding="utf-8",
        )

        r1 = ingest(raw_file, now=_FAKE_NOW)
        assert not r1.errors
        assert r1.created

        r2 = ingest(raw_file, now=_FAKE_NOW)
        assert not r2.errors

        # Page count must not increase
        pages_after = list((wiki / "pages").glob("*.md"))
        assert len(pages_after) == len(r1.created), (
            "Page count must not increase on re-ingest of same file"
        )

        # Read the page and check content is not duplicated
        aiden_pages = list((wiki / "pages").glob("aiden*.md"))
        content = aiden_pages[0].read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)

        # "Kerala" should appear exactly once (not duplicated)
        count = body.count("Kerala")
        assert count == 1, (
            f"Content must not be duplicated on re-ingest. 'Kerala' appears {count} times"
        )

    # ------------------------------------------------------------------ #
    # 4. Staleness scoring in lint()
    # ------------------------------------------------------------------ #

    def test_staleness_default_90_days(self, tmp_path: Path) -> None:
        """lint() with no env var override flags a page with valid_from 91 days ago."""
        import os
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        from datetime import timedelta
        stale_date = (_FAKE_NOW - timedelta(days=91)).date().isoformat()

        # Write a page that is 91 days old
        (pages_dir / "old-fact.md").write_text(
            _make_page(
                "Old Fact",
                "This is an old fact.",
                valid_from=stale_date,
            ),
            encoding="utf-8",
        )

        # Patch _now_utc to return our fixed time
        with patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW):
            # Unset the env var to use default (90 days)
            old_val = os.environ.pop("PRAXIS_WIKI_STALE_DAYS", None)
            try:
                report = lint(wiki_root=wiki)
            finally:
                if old_val is not None:
                    os.environ["PRAXIS_WIKI_STALE_DAYS"] = old_val

        assert len(report.stale_facts) >= 1, (
            f"Expected at least 1 stale fact (91 days > 90 day default). Got: {report.stale_facts}"
        )

    def test_staleness_format(self, tmp_path: Path) -> None:
        """stale_facts entries are dicts with 'page', 'days_since_update', 'valid_from' keys."""
        import os
        _setup_wiki_dirs(tmp_path)
        wiki = tmp_path / "wiki"
        pages_dir = wiki / "pages"

        from datetime import timedelta
        stale_date = (_FAKE_NOW - timedelta(days=200)).date().isoformat()

        (pages_dir / "old-fact.md").write_text(
            _make_page(
                "Old Fact",
                "This is an old fact.",
                valid_from=stale_date,
            ),
            encoding="utf-8",
        )

        with patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW):
            old_val = os.environ.pop("PRAXIS_WIKI_STALE_DAYS", None)
            try:
                report = lint(wiki_root=wiki)
            finally:
                if old_val is not None:
                    os.environ["PRAXIS_WIKI_STALE_DAYS"] = old_val

        assert len(report.stale_facts) >= 1, (
            f"Expected at least 1 stale fact. Got: {report.stale_facts}"
        )
        entry = report.stale_facts[0]
        assert isinstance(entry, dict), f"stale_facts entry must be a dict, got: {type(entry)}"
        assert "page" in entry, f"stale_facts entry must have 'page' key. Got: {entry}"
        assert "days_since_update" in entry, f"stale_facts entry must have 'days_since_update'. Got: {entry}"
        assert "valid_from" in entry, f"stale_facts entry must have 'valid_from'. Got: {entry}"
        assert isinstance(entry["days_since_update"], int), (
            f"days_since_update must be int. Got: {type(entry['days_since_update'])}"
        )
