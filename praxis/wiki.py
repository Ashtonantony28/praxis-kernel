"""Praxis bitemporal wiki — ingest, query, and lint (TASK-W04, TASK-W05, TASK-W06).

This module owns wiki/pages/, wiki/index.md, and wiki/log.md.
It NEVER writes under wiki/raw/ — any resolved write path under wiki/raw/ raises
WikiRawImmutableError before the syscall (structural immutability, mirroring the
email/calendar write-escalate pattern).

The §5 hook enforces WORKSPACE_ROOT boundary; this module enforces the additional
wiki/raw/ immutability invariant on top.

Limitations (v1, documented):
- Source parsing is heuristic: paragraphs are candidate facts; entity name is
  the first capitalized noun phrase found via a simple regex. This will mis-identify
  common words as entities and miss multi-word proper nouns that span sentences.
  Better extraction (LLM-assisted or structured) is out of scope for Phase W.
- Contradiction detection uses strict text inequality: same entity slug + a page
  already exists with a different body hash. It does NOT do semantic analysis.
  Two pages saying the same thing in different words are treated as contradictions.
  A lint() pass (TASK-W06) provides more nuanced contradiction detection.
- Idempotency is checked at the entity+body level: if the exact same text already
  exists in the most recent (non-superseded) page for an entity, the candidate is
  skipped. Full-file re-ingest occurs on any content change.
- query() entity extraction uses the same heuristic regex as ingest(): first
  capitalized noun phrase in the question text. It will miss entities phrased as
  lowercase keywords and may pick up sentence-initial words. No semantic
  understanding; purely structural/lexical matching. LLM-assisted extraction is
  out of scope for Phase W.
- lint() contradiction detection is heuristic (same entity slug, different body
  hash, no supersedes link). Semantic contradictions expressed in different words
  are not detected. Stale threshold defaults to 90 days (configurable via
  PRAXIS_WIKI_STALE_DAYS). Superseded pages are excluded from all checks except
  frontmatter validation. One LINT summary event is appended to wiki/log.md.
"""

from __future__ import annotations

import binascii
import dataclasses
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WikiError(Exception):
    """Base exception for all wiki errors."""


class WikiRawImmutableError(WikiError):
    """Raised when any write path resolves under wiki/raw/.

    wiki/raw/ is HUMAN-ONLY — Praxis reads it but must never write to it.
    """


class WikiAmbiguousEntityError(WikiError):
    """Raised when entity resolution finds multiple candidates and no entity_hint.

    Carries the candidate list so the caller can surface it to the user.
    """

    def __init__(self, candidate_name: str, matches: list[str]) -> None:
        self.candidate_name = candidate_name
        self.matches = matches
        super().__init__(
            f"Ambiguous entity {candidate_name!r}: matches {matches}. "
            "Provide entity_hint= to resolve."
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ResolvedEntity:
    """Result of entity resolution."""

    name: str
    slug: str
    page_path: Path | None  # None → new entity (no existing page)
    is_new: bool
    candidates: list[str]  # non-empty only when ambiguous


@dataclasses.dataclass(frozen=True)
class IngestEvent:
    """A single ingest event.

    kind is one of: created, updated, superseded, skipped-idempotent, skipped-ambiguous.
    """

    kind: str
    page_path: Path
    note: str


@dataclasses.dataclass
class IngestReport:
    """Report returned by ingest()."""

    events: list[IngestEvent] = dataclasses.field(default_factory=list)
    ambiguous_entities: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)

    # Convenience aliases matching wiki-plan.md API surface
    @property
    def created(self) -> list[str]:
        return [str(e.page_path) for e in self.events if e.kind == "created"]

    @property
    def updated(self) -> list[str]:
        return [str(e.page_path) for e in self.events if e.kind in ("updated", "superseded")]

    @property
    def skipped(self) -> list[str]:
        return [str(e.page_path) for e in self.events if e.kind.startswith("skipped")]

    @property
    def log_entries(self) -> list[str]:
        """Lines that were appended to wiki/log.md during this ingest."""
        return [e.note for e in self.events if not e.kind.startswith("skipped")]

    def summary(self) -> str:
        created = len(self.created)
        updated = len(self.updated)
        skipped = len(self.skipped)
        ambiguous = len(self.ambiguous_entities)
        errors = len(self.errors)
        return (
            f"ingest: {created} created, {updated} updated, {skipped} skipped, "
            f"{ambiguous} ambiguous, {errors} errors"
        )


@dataclasses.dataclass(slots=True, frozen=True)
class QueryHit:
    """A single result page returned by query()."""

    page_path: Path
    entity: str
    level: str
    valid_from: str
    learned_on: str
    superseded_on: str | None
    excerpt: str           # first ~400 chars of page body
    links: list[dict]      # each {type, target}
    score: float           # relevance score 0..1


@dataclasses.dataclass(slots=True, frozen=True)
class QueryResult:
    """Result returned by query().

    Fields
    ------
    question:
        The original question passed to query().
    hits:
        Ranked list of relevant pages (highest score first).
    index_consulted:
        True if wiki/index.md was read to narrow the search.
    notes:
        Advisory messages (superseded exclusions, empty-wiki notice, etc.).
    answer:
        Synthesised natural-language answer built from the top hits (no LLM
        call — heuristic combination of entity names and body excerpts).
    citations:
        Workspace-relative paths of every page that was read and scored > 0
        (in score-descending order).  These are the authoritative references;
        never invented.
    confidence:
        Heuristic quality signal: "high" if at least one page matched an
        entity-name token from the question; "medium" if pages were found via
        body-token overlap only; "low" if no relevant pages were found.
    """

    question: str
    hits: list[QueryHit]
    index_consulted: bool  # True if wiki/index.md was read; False if it was missing
    notes: list[str]       # advisory notes (ambiguity warnings, superseded exclusions, etc.)
    answer: str = ""       # synthesised answer text (set by query())
    citations: list[str] = dataclasses.field(default_factory=list)  # wiki/pages/ paths
    confidence: str = "low"  # "high" | "medium" | "low"

    def summary(self) -> str:
        """Return a short human-readable rendering of the answer and citations."""
        if not self.hits:
            note_block = ""
            if self.notes:
                note_block = "\n".join(f"  * {n}" for n in self.notes)
                return f"No results found for: {self.question!r}\n{note_block}"
            return f"No results found for: {self.question!r}"

        lines = [f"Query: {self.question!r}", ""]

        for i, hit in enumerate(self.hits, 1):
            sup_tag = " [SUPERSEDED]" if hit.superseded_on else ""
            lines.append(
                f"{i}. [{hit.level}] {hit.entity}{sup_tag}"
                f" (score={hit.score:.2f})"
            )
            lines.append(f"   Source: {hit.page_path}")
            if hit.excerpt:
                preview = hit.excerpt[:200].replace("\n", " ")
                lines.append(f"   Excerpt: {preview}...")
            lines.append("")

        if self.notes:
            lines.append("Notes:")
            for note in self.notes:
                lines.append(f"  * {note}")

        return "\n".join(lines)


@dataclasses.dataclass
class LintReport:
    """Report returned by lint().

    Fields
    ------
    contradictions:
        Pairs of current (non-superseded) pages whose entity slug is the same
        but whose body hashes differ AND that have no supersedes link between
        them.  Each entry is {page_a, page_b, note}.
    stale_facts:
        Dicts with page/days_since_update/valid_from for pages where valid_from
        is more than stale_days in the past and superseded_on is null.
        Each entry: {"page": "wiki/pages/foo.md", "days_since_update": 123, "valid_from": "2024-01-01"}.
    orphan_pages:
        Workspace-relative paths of pages that have no outbound typed links
        AND are not referenced by any other page's typed link AND are not
        listed in wiki/index.md.
    duplicate_entities:
        Pairs of current pages whose entity slugs score >= 0.92 on
        Jaro-Winkler but are NOT connected by a supersedes link.  Each entry
        is {page_a, page_b, similarity}.
    missing_links:
        Pages that mention a known entity (exact entity name or alias match)
        in their body text but carry no typed link to that entity's page.
        Each entry is {page, mentioned_entity, suggested_type}.
    frontmatter_errors:
        Hard errors: pages missing required fields, wrong types, or
        inconsistent supersession state.  Each entry is {page, field, error}.
    """

    contradictions: list[dict] = dataclasses.field(default_factory=list)
    stale_facts: list[dict] = dataclasses.field(default_factory=list)
    orphan_pages: list[str] = dataclasses.field(default_factory=list)
    duplicate_entities: list[dict] = dataclasses.field(default_factory=list)
    missing_links: list[dict] = dataclasses.field(default_factory=list)
    frontmatter_errors: list[dict] = dataclasses.field(default_factory=list)

    def summary(self) -> str:
        """Return a short summary of all finding counts."""
        return (
            f"lint: {len(self.contradictions)} contradictions, "
            f"{len(self.stale_facts)} stale facts, "
            f"{len(self.orphan_pages)} orphan pages, "
            f"{len(self.duplicate_entities)} duplicate entities, "
            f"{len(self.missing_links)} missing links, "
            f"{len(self.frontmatter_errors)} frontmatter errors"
        )

    @property
    def has_findings(self) -> bool:
        """True if any findings were reported."""
        return bool(
            self.contradictions
            or self.stale_facts
            or self.orphan_pages
            or self.duplicate_entities
            or self.missing_links
            or self.frontmatter_errors
        )


# ---------------------------------------------------------------------------
# Internal helpers — workspace / wiki root
# ---------------------------------------------------------------------------


def _workspace_root() -> Path:
    """Return the workspace root from praxis.config (monkeypatchable in tests)."""
    from praxis.config import Config  # local import so tests can monkeypatch before
    return Config.from_env().workspace_root


def _wiki_root() -> Path:
    """Return wiki/ under the workspace root."""
    return _workspace_root() / "wiki"


# ---------------------------------------------------------------------------
# Slugify (LOCKED algorithm from SCHEMA.md)
# ---------------------------------------------------------------------------


def _slugify(name: str, *, existing_slugs: list[str] | None = None) -> str:
    """Derive a deterministic filename slug from a canonical entity name.

    Algorithm (per wiki/SCHEMA.md § Page Filename Rules):
      1. NFKD decompose, encode ASCII (drop non-ASCII — no transliteration).
      2. Lowercase.
      3. Collapse runs of whitespace, underscores, hyphens to a single hyphen.
      4. Strip leading/trailing hyphens.
      5. Remove any character not in [a-z0-9-].
      6. Truncate at 80 characters; if split mid-word, backtrack to last hyphen.
         Append 4-char hex CRC32 suffix if backtrack collides with another existing
         slug (i.e., two different full names produce the same 80-char prefix).
    """
    # Step 1: NFKD → ASCII
    normalized = unicodedata.normalize("NFKD", name)
    ascii_bytes = normalized.encode("ascii", errors="ignore")
    s = ascii_bytes.decode("ascii")

    # Step 2: lowercase
    s = s.lower()

    # Step 3: collapse whitespace/underscore/hyphen runs to single hyphen
    s = re.sub(r"[\s_\-]+", "-", s)

    # Step 4: strip boundary hyphens
    s = s.strip("-")

    # Step 5: remove non-[a-z0-9-]
    s = re.sub(r"[^a-z0-9\-]", "", s)

    # Strip again in case step 5 left boundary hyphens
    s = s.strip("-")

    if not s:
        # Fallback: hex CRC32 of original name
        crc = binascii.crc32(name.encode()) & 0xFFFFFFFF
        s = format(crc, "08x")

    # Step 6: truncate at 80 chars
    if len(s) > 80:
        prefix = s[:80]
        # Backtrack to last hyphen boundary
        last_hyphen = prefix.rfind("-")
        if last_hyphen > 0:
            candidate = prefix[:last_hyphen]
        else:
            candidate = prefix

        # Check for collision: if another existing slug has the same prefix
        if existing_slugs and candidate in existing_slugs:
            crc = binascii.crc32(s.encode()) & 0xFFFF
            s = candidate + "-" + format(crc, "04x")
        else:
            s = candidate

    return s


# ---------------------------------------------------------------------------
# Jaro-Winkler similarity (pure Python, no jellyfish dependency)
# ---------------------------------------------------------------------------


def _jaro_winkler(a: str, b: str) -> float:
    """Compute Jaro-Winkler similarity between strings a and b.

    Standard formula: prefix scaling factor 0.1, max prefix length 4.
    Returns 0.0 .. 1.0.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    len_a = len(a)
    len_b = len(b)
    match_dist = max(len_a, len_b) // 2 - 1
    if match_dist < 0:
        match_dist = 0

    a_matches = [False] * len_a
    b_matches = [False] * len_b

    matches = 0
    transpositions = 0

    # Find matching characters
    for i in range(len_a):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len_b)
        for j in range(start, end):
            if b_matches[j] or a[i] != b[j]:
                continue
            a_matches[i] = True
            b_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    # Count transpositions
    k = 0
    for i in range(len_a):
        if not a_matches[i]:
            continue
        while not b_matches[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len_a
        + matches / len_b
        + (matches - transpositions / 2) / matches
    ) / 3.0

    # Jaro-Winkler prefix bonus
    prefix = 0
    for i in range(min(4, len_a, len_b)):
        if a[i] == b[i]:
            prefix += 1
        else:
            break

    return jaro + prefix * 0.1 * (1.0 - jaro)


# ---------------------------------------------------------------------------
# Frontmatter parsing and rendering (stdlib-only, flat YAML subset)
# ---------------------------------------------------------------------------

# Valid link types
_LINK_TYPES = frozenset(["contradicts", "supports", "contains", "supersedes", "relates"])

# Required frontmatter fields
_REQUIRED_FIELDS = [
    "entity", "aliases", "level", "valid_from", "learned_on",
    "superseded_on", "superseded_by", "links",
]

# Stable field order for rendering
_FIELD_ORDER = _REQUIRED_FIELDS  # same as required fields


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into frontmatter dict + body.

    Handles the flat YAML subset used by wiki pages:
      - string scalars (with or without quotes)
      - null
      - ISO dates as strings
      - list of strings: `- "value"` or `- value`
      - list of dicts with two keys (type + target): `- type: foo\\n  target: bar`
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")

    meta: dict[str, Any] = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip blank lines and comments
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # Top-level key: value
        m = re.match(r'^(\w[\w_-]*):\s*(.*)', line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        val_str = m.group(2).strip()

        # Peek ahead for list items (next lines start with spaces + -)
        if val_str == "" or val_str == "[]":
            if val_str == "[]":
                meta[key] = []
                i += 1
                continue
            # May be a block sequence
            items = []
            i += 1
            while i < len(lines) and re.match(r'^\s+-', lines[i]):
                item_line = lines[i].strip().lstrip("- ").strip()
                # Check for dict item: key: value
                if re.match(r'^\w[\w_-]*:\s*', item_line):
                    # Collect the sub-dict
                    sub = {}
                    # Parse first key: val on this line
                    kv_m = re.match(r'^(\w[\w_-]*):\s*(.*)', item_line)
                    if kv_m:
                        sub[kv_m.group(1)] = _unquote(kv_m.group(2).strip())
                    # Peek at next lines for continuation of this dict item
                    i += 1
                    while i < len(lines) and re.match(r'^\s{2,}\w', lines[i]):
                        kv2 = re.match(r'^\s+(\w[\w_-]*):\s*(.*)', lines[i])
                        if kv2:
                            sub[kv2.group(1)] = _unquote(kv2.group(2).strip())
                        i += 1
                    items.append(sub)
                else:
                    items.append(_unquote(item_line))
                    i += 1
            meta[key] = items
        else:
            # Scalar
            meta[key] = _parse_scalar(val_str)
            i += 1

    return meta, body


def _unquote(s: str) -> str:
    """Strip surrounding single or double quotes from a YAML scalar."""
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _parse_scalar(val_str: str) -> Any:
    """Parse a YAML scalar value (null, bool, string, date)."""
    if val_str == "null":
        return None
    if val_str in ("true", "True"):
        return True
    if val_str in ("false", "False"):
        return False
    return _unquote(val_str)


def _render_frontmatter(meta: dict[str, Any]) -> str:
    """Render a frontmatter dict to a YAML string bracketed by --- lines.

    Fields are emitted in the stable order from _FIELD_ORDER. Extra fields
    (e.g., ingest_hash) are appended after the standard fields.
    """
    lines = ["---"]

    def _render_value(key: str, val: Any) -> None:
        if val is None:
            lines.append(f"{key}: null")
        elif isinstance(val, list):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in val:
                    if isinstance(item, dict):
                        first = True
                        for k2, v2 in item.items():
                            if first:
                                lines.append(f"  - {k2}: {_quote_str(v2)}")
                                first = False
                            else:
                                lines.append(f"    {k2}: {_quote_str(v2)}")
                    else:
                        lines.append(f"  - {_quote_str(item)}")
        else:
            lines.append(f"{key}: {_quote_str(val)}")

    # Standard fields first (in declared order)
    for field in _FIELD_ORDER:
        if field in meta:
            _render_value(field, meta[field])

    # Extra fields (not in standard order)
    for key, val in meta.items():
        if key not in _FIELD_ORDER:
            _render_value(key, val)

    lines.append("---")
    return "\n".join(lines) + "\n"


def _quote_str(val: Any) -> str:
    """Render a scalar as a quoted string (if string) or bare (dates, etc.)."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val)
    # Dates (YYYY-MM-DD) and path-like strings: quote them
    return f'"{s}"'


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Return current UTC time (monkeypatchable in tests)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Log event
# ---------------------------------------------------------------------------


_VALID_EVENT_TYPES = frozenset(["INGEST", "SUPERSEDE", "LINK", "LINT"])


def _log_event(
    event_type: str,
    page_path: Path,
    note: str,
    *,
    wiki_root: Path,
) -> None:
    """Append one line to wiki/log.md in the canonical grammar.

    Format: YYYY-MM-DD HH:MM:SSZ EVENT_TYPE path/to/page.md — note
    """
    if event_type not in _VALID_EVENT_TYPES:
        raise ValueError(
            f"Invalid event_type {event_type!r}. Must be one of {sorted(_VALID_EVENT_TYPES)}"
        )

    now = _now_utc()
    ts = now.strftime("%Y-%m-%d %H:%M:%S") + "Z"

    # Workspace-relative path
    workspace = wiki_root.parent
    try:
        rel_path = page_path.relative_to(workspace)
    except ValueError:
        rel_path = page_path

    # Truncate note to 120 chars
    note_truncated = note[:120]

    log_line = f"{ts} {event_type} {rel_path} — {note_truncated}\n"

    log_file = wiki_root / "log.md"
    _guard_not_raw(log_file, wiki_root)

    # Append to log.md (create if missing)
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(log_line)


# ---------------------------------------------------------------------------
# wiki/raw/ guard
# ---------------------------------------------------------------------------


def _guard_not_raw(dest: Path, wiki_root: Path) -> None:
    """Raise WikiRawImmutableError if dest resolves under wiki/raw/."""
    raw_root = (wiki_root / "raw").resolve()
    dest_resolved = dest.resolve()
    try:
        dest_resolved.relative_to(raw_root)
        # If we get here, dest is under raw/ — that's forbidden
        raise WikiRawImmutableError(
            f"Attempted write to {dest} which is under wiki/raw/ — "
            "wiki/raw/ is HUMAN-ONLY. Praxis must not write there."
        )
    except ValueError:
        # Not under raw/ — allowed
        pass


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def _resolve_entity(
    candidate: str,
    *,
    wiki_root: Path,
    entity_hint: str | None = None,
) -> ResolvedEntity:
    """Resolve a candidate entity name to an existing page or declare it new.

    4-step algorithm per SCHEMA.md:
      1. Exact filename match (slug → wiki/pages/<slug>.md).
      2. Alias scan: check aliases: field of all pages.
      3. Jaro-Winkler ≥ 0.92 fuzzy match against all page entity slugs.
      4. Ambiguity check: if multiple fuzzy candidates and no entity_hint,
         raise WikiAmbiguousEntityError.

    If entity_hint is provided it short-circuits ambiguity by pinning to a
    specific existing filename (without .md extension, or with).
    """
    pages_dir = wiki_root / "pages"
    slug = _slugify(candidate)

    # entity_hint: short-circuit to a specific page
    if entity_hint is not None:
        hint_slug = entity_hint.replace(".md", "")
        hint_path = pages_dir / f"{hint_slug}.md"
        if hint_path.exists():
            return ResolvedEntity(
                name=candidate,
                slug=hint_slug,
                page_path=hint_path,
                is_new=False,
                candidates=[],
            )
        # entity_hint points to a non-existent page — treat as explicit new
        return ResolvedEntity(
            name=candidate,
            slug=hint_slug,
            page_path=None,
            is_new=True,
            candidates=[],
        )

    # Step 1: exact filename match
    exact_path = pages_dir / f"{slug}.md"
    if exact_path.exists():
        return ResolvedEntity(
            name=candidate,
            slug=slug,
            page_path=exact_path,
            is_new=False,
            candidates=[],
        )

    # Load all existing pages for steps 2 and 3
    existing_pages = list(pages_dir.glob("*.md")) if pages_dir.exists() else []

    # Step 2: alias scan
    candidate_slug = _slugify(candidate)
    for page_path in existing_pages:
        try:
            content = page_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        aliases = meta.get("aliases", [])
        for alias in aliases:
            if isinstance(alias, str) and _slugify(alias) == candidate_slug:
                page_slug = page_path.stem
                return ResolvedEntity(
                    name=candidate,
                    slug=page_slug,
                    page_path=page_path,
                    is_new=False,
                    candidates=[],
                )

    # Step 3 (new): prefix/suffix check — runs AFTER alias check, BEFORE Jaro-Winkler
    # If candidate is a prefix of an existing entity name, or vice versa, AND the
    # prefix is at least 3 characters, treat as a match (longer name is canonical).
    # If multiple pages match by prefix, treat as ambiguous.
    _MIN_PREFIX_LEN = 3
    prefix_matches: list[tuple[Path, str]] = []  # (page_path, page_entity_name)
    for page_path in existing_pages:
        try:
            content = page_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        entity_name_existing = meta.get("entity", "")
        if not entity_name_existing:
            continue
        # Normalise both to lowercase for comparison
        cand_lower = candidate.lower().strip()
        exist_lower = entity_name_existing.lower().strip()
        # Check prefix: candidate is prefix of existing, OR existing is prefix of candidate
        if len(cand_lower) >= _MIN_PREFIX_LEN and len(exist_lower) >= _MIN_PREFIX_LEN:
            if exist_lower.startswith(cand_lower) or cand_lower.startswith(exist_lower):
                prefix_matches.append((page_path, entity_name_existing))

    if len(prefix_matches) == 1:
        fpath, fname = prefix_matches[0]
        page_slug = fpath.stem
        return ResolvedEntity(
            name=candidate,
            slug=page_slug,
            page_path=fpath,
            is_new=False,
            candidates=[],
        )
    if len(prefix_matches) > 1:
        raise WikiAmbiguousEntityError(
            candidate_name=candidate,
            matches=[fp.stem for fp, _ in prefix_matches],
        )

    # Step 4: Jaro-Winkler fuzzy match
    # Use threshold 0.85 for multi-word entities (has a space in candidate), 0.92 for single-word
    _has_space = " " in candidate.strip()
    _jw_threshold = 0.85 if _has_space else 0.92
    fuzzy_matches: list[tuple[float, Path, str]] = []
    for page_path in existing_pages:
        try:
            content = page_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        entity_name = meta.get("entity", "")
        if entity_name:
            page_entity_slug = _slugify(entity_name)
            score = _jaro_winkler(slug, page_entity_slug)
            if score >= _jw_threshold:
                fuzzy_matches.append((score, page_path, page_entity_slug))

    if len(fuzzy_matches) == 1:
        # Single fuzzy match — treat as ambiguous in non-interactive mode
        score, fpath, fslug = fuzzy_matches[0]
        raise WikiAmbiguousEntityError(
            candidate_name=candidate,
            matches=[fpath.stem],
        )

    if len(fuzzy_matches) > 1:
        candidates = [fp.stem for _, fp, _ in fuzzy_matches]
        raise WikiAmbiguousEntityError(
            candidate_name=candidate,
            matches=candidates,
        )

    # Step 5: no match → new entity
    return ResolvedEntity(
        name=candidate,
        slug=slug,
        page_path=None,
        is_new=True,
        candidates=[],
    )


# ---------------------------------------------------------------------------
# Page writer
# ---------------------------------------------------------------------------


def _write_page(
    slug: str,
    frontmatter: dict[str, Any],
    body: str,
    *,
    wiki_root: Path,
) -> Path:
    """Write wiki/pages/{slug}.md. Raises WikiRawImmutableError if path is under wiki/raw/."""
    pages_dir = wiki_root / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    dest = pages_dir / f"{slug}.md"
    _guard_not_raw(dest, wiki_root)

    content = _render_frontmatter(frontmatter) + "\n" + body
    dest.write_text(content, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def _rebuild_index(wiki_root: Path) -> None:
    """Regenerate wiki/index.md from all live (non-superseded) wiki/pages/.

    Format per SCHEMA.md § wiki/index.md Structure:
      H2 headings for each theme (## theme: <name>)
      Under each theme: bullet topics/facts
      Unthemed topics and facts in ## Unthemed topics and facts
      Superseded pages excluded
      Generated timestamp on line 2
    """
    index_path = wiki_root / "index.md"
    _guard_not_raw(index_path, wiki_root)

    pages_dir = wiki_root / "pages"
    page_files = sorted(pages_dir.glob("*.md")) if pages_dir.exists() else []

    now = _now_utc()
    ts = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    # Collect live pages grouped by level then theme
    themes: dict[str, list[dict[str, Any]]] = {}  # theme_entity_slug → list of topic/fact pages
    unthemed: list[dict[str, Any]] = []

    for pf in page_files:
        try:
            content = pf.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        if not meta:
            continue

        # Skip superseded pages
        if meta.get("superseded_on") is not None:
            continue

        level = meta.get("level", "fact")
        entity = meta.get("entity", pf.stem)
        rel_path = f"wiki/pages/{pf.name}"

        page_info = {"entity": entity, "level": level, "path": rel_path, "slug": pf.stem}

        # Determine theme from links (contains-link from a theme page)
        # For now, all pages go to unthemed unless we detect theme parent
        # (Theme assignment requires bidirectional link traversal — deferred)
        if level == "theme":
            # Theme pages are listed as H2 headings, not bullets
            continue  # themes are headings, handled separately below

        unthemed.append(page_info)

    # Also collect theme pages separately
    theme_pages: list[dict[str, Any]] = []
    for pf in page_files:
        try:
            content = pf.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        if not meta:
            continue
        if meta.get("superseded_on") is not None:
            continue
        if meta.get("level") == "theme":
            entity = meta.get("entity", pf.stem)
            rel_path = f"wiki/pages/{pf.name}"
            theme_pages.append({"entity": entity, "path": rel_path, "slug": pf.stem})

    # Build index content
    lines = [
        "# Wiki Index",
        f"<!-- generated: {ts} — do not edit by hand -->",
        "",
    ]

    # Themed sections
    for theme in theme_pages:
        lines.append(f"## theme: {theme['entity']}")
        # Find topic/fact pages that link to this theme page (contains links)
        themed_items = []
        for page_info in unthemed:
            # Check if this page has a link that points to the theme
            pf = pages_dir / f"{page_info['slug']}.md"
            try:
                content = pf.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_frontmatter(content)
            for lnk in meta.get("links", []):
                if (
                    isinstance(lnk, dict)
                    and lnk.get("type") == "contains"
                    and theme["slug"] in str(lnk.get("target", ""))
                ):
                    themed_items.append(page_info)
                    break
        for item in themed_items:
            lines.append(f"- [{item['level']}] [{item['entity']}]({item['path']})")
        lines.append("")

    # Unthemed section (pages not linked to a theme)
    themed_slugs: set[str] = set()
    for theme in theme_pages:
        pf = pages_dir / f"{theme['slug']}.md"
        try:
            content = pf.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(content)
        for lnk in meta.get("links", []):
            if isinstance(lnk, dict) and lnk.get("type") == "contains":
                target = str(lnk.get("target", ""))
                # Extract slug from path
                m = re.search(r"wiki/pages/(.+)\.md", target)
                if m:
                    themed_slugs.add(m.group(1))

    unthemed_items = [p for p in unthemed if p["slug"] not in themed_slugs]

    if unthemed_items:
        lines.append("## Unthemed topics and facts")
        for item in unthemed_items:
            lines.append(f"- [{item['level']}] [{item['entity']}]({item['path']})")
        lines.append("")

    if not theme_pages and not unthemed_items:
        lines.append("(no pages yet)")
        lines.append("")

    index_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Source parsing helpers
# ---------------------------------------------------------------------------


def _extract_entity_name(text: str) -> str | None:
    """Extract the most likely entity name from a paragraph of text.

    Heuristic: find the first capitalized noun phrase (sequence of title-case words
    or proper noun patterns). Returns None if no candidate found.

    Limitation: this is a simple regex heuristic. It will miss complex proper nouns
    and may misidentify common capitalized words (e.g., sentence-initial words).
    """
    # Try: sequence of 1–4 title-case words (optionally separated by spaces/hyphens)
    m = re.search(
        r'\b([A-Z][a-z]+(?:[\s\-][A-Z][a-z]+){0,3})\b',
        text,
    )
    if m:
        candidate = m.group(1).strip()
        # Reject very short single-word candidates (likely sentence starters)
        if " " in candidate or len(candidate) > 5:
            return candidate
    # Fallback: any word ≥ 6 chars starting with capital
    m2 = re.search(r'\b([A-Z][a-zA-Z]{5,})\b', text)
    if m2:
        return m2.group(1)
    return None


def _hash_content(text: str) -> str:
    """Return a SHA-256 hex digest of the given text (for idempotency checks)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_source_into_paragraphs(text: str) -> list[str]:
    """Split source text into paragraphs (blank-line separated).

    Strips leading/trailing whitespace from each paragraph and discards
    markdown headings and very short fragments.
    """
    raw_paragraphs = re.split(r'\n\s*\n', text.strip())
    result = []
    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue
        # Skip markdown headings (lines starting with #)
        if para.startswith("#"):
            continue
        # Skip very short fragments (likely horizontal rules, YAML fences, etc.)
        if len(para) < 10:
            continue
        result.append(para)
    return result


# ---------------------------------------------------------------------------
# Frontmatter validation
# ---------------------------------------------------------------------------


def _validate_frontmatter(meta: dict[str, Any]) -> list[str]:
    """Return a list of validation errors for a frontmatter dict.

    Returns empty list if valid.
    """
    errors = []
    for field in _REQUIRED_FIELDS:
        if field not in meta:
            errors.append(f"missing required field: {field!r}")

    if "level" in meta and meta["level"] not in ("theme", "topic", "fact"):
        errors.append(f"level must be theme/topic/fact, got {meta['level']!r}")

    if "links" in meta and not isinstance(meta["links"], list):
        errors.append("links must be a list")

    if "aliases" in meta and not isinstance(meta["aliases"], list):
        errors.append("aliases must be a list")

    return errors


# ---------------------------------------------------------------------------
# Public function: ingest()
# ---------------------------------------------------------------------------


def ingest(
    source: str | Path,
    *,
    provenance: str | None = None,
    entity_hint: str | None = None,
    now: datetime | None = None,
) -> IngestReport:
    """Ingest a source file (under wiki/raw/) or literal text into wiki/pages/.

    Parameters
    ----------
    source:
        Either a filesystem Path (typically wiki/raw/<file>.md) or a literal
        text string.  Distinguishing rule: if the value is already a Path
        object, or if it looks like a path and exists on disk, treat it as a
        file; otherwise treat it as a text blob.
    provenance:
        Free-text description of the fact source.  Stored in the page body
        header comment, NOT in frontmatter.
    entity_hint:
        Filename slug (with or without .md) that pins entity resolution when
        the automatic algorithm would be ambiguous.  The caller passes this
        when the user has manually resolved an ambiguity.
    now:
        Override the "current UTC datetime" for testing.  Production code
        leaves this as None so _now_utc() is called internally.

    Returns
    -------
    IngestReport
        Summary of all events.  Never raises on ambiguity — ambiguous names
        are placed in report.ambiguous_entities.

    Raises
    ------
    WikiRawImmutableError
        If any resolved write path falls under wiki/raw/ (should be
        impossible given correct inputs, but is checked defensively).
    """
    wiki_root = _wiki_root()
    raw_root = (wiki_root / "raw").resolve()
    report = IngestReport()

    # Resolve "current time" (allows test monkeypatching via the now= parameter)
    if now is None:
        today = _now_utc().date()
    else:
        today = now.date()
    today_str = today.isoformat()

    # ------------------------------------------------------------------
    # Determine source text and provenance
    # ------------------------------------------------------------------
    source_text: str
    source_path: Path | None = None

    if isinstance(source, Path):
        source_path = source.resolve()
        # Guard: ingest READS wiki/raw/ — that is fine.
        # It must NOT write there. We only write to wiki/pages/ below.
        if not source_path.exists():
            report.errors.append(f"source path does not exist: {source_path}")
            return report
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            report.errors.append(f"could not read source: {exc}")
            return report
        if provenance is None:
            try:
                provenance = str(source_path.relative_to(wiki_root.parent))
            except ValueError:
                provenance = str(source_path)
    else:
        # Might be a path-like string that exists on disk
        as_path = Path(str(source))
        if as_path.exists() and not source.startswith("\n") and "\n" not in str(source)[:80]:
            source_path = as_path.resolve()
            try:
                source_text = source_path.read_text(encoding="utf-8")
            except OSError as exc:
                report.errors.append(f"could not read source: {exc}")
                return report
            if provenance is None:
                try:
                    provenance = str(source_path.relative_to(wiki_root.parent))
                except ValueError:
                    provenance = str(source_path)
        else:
            source_text = str(source)
            if provenance is None:
                provenance = "inline text"

    # ------------------------------------------------------------------
    # Parse source into candidate (entity_name, paragraph) pairs
    # ------------------------------------------------------------------
    paragraphs = _split_source_into_paragraphs(source_text)
    if not paragraphs:
        # Nothing to ingest
        return report

    # Group paragraphs into entities: each paragraph may contribute one fact
    candidates: list[tuple[str, str]] = []  # (entity_name, paragraph_body)
    for para in paragraphs:
        entity_name = _extract_entity_name(para)
        if entity_name:
            candidates.append((entity_name, para))

    if not candidates:
        report.errors.append("no entity candidates found in source text")
        return report

    # ------------------------------------------------------------------
    # For each candidate: resolve entity, check idempotency, write/supersede
    # ------------------------------------------------------------------
    pages_dir = wiki_root / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # Build a list of existing slugs for collision detection
    existing_slugs = [p.stem for p in pages_dir.glob("*.md")]

    for entity_name, para_body in candidates:
        content_hash = _hash_content(para_body)

        # ------ Entity resolution ------
        try:
            resolved = _resolve_entity(
                entity_name,
                wiki_root=wiki_root,
                entity_hint=entity_hint,
            )
        except WikiAmbiguousEntityError as exc:
            report.ambiguous_entities.append(exc.candidate_name)
            report.events.append(
                IngestEvent(
                    kind="skipped-ambiguous",
                    page_path=pages_dir / f"{_slugify(entity_name)}.md",
                    note=f"ambiguous: {exc.matches}",
                )
            )
            continue
        except WikiRawImmutableError:
            raise  # propagate — should never happen in entity resolution
        except Exception as exc:
            report.errors.append(f"entity resolution error for {entity_name!r}: {exc}")
            continue

        slug = resolved.slug
        page_path = pages_dir / f"{slug}.md"

        # ------ Check for existing page ------
        if resolved.page_path is not None and resolved.page_path.exists():
            # Page exists — check idempotency + possible supersede
            try:
                existing_content = resolved.page_path.read_text(encoding="utf-8")
            except OSError as exc:
                report.errors.append(f"could not read existing page {resolved.page_path}: {exc}")
                continue

            existing_meta, existing_body = _parse_frontmatter(existing_content)

            # Check if existing page is already superseded
            already_superseded = existing_meta.get("superseded_on") is not None

            # Determine the source key for this ingest call.
            # For file-based ingests: use the basename of the source file.
            # For inline text ingests: use "inline".
            if source_path is not None:
                source_key = source_path.name
            else:
                source_key = "inline"

            # Multi-source merge: check per-source content hashes (source_hashes dict)
            # source_hashes is stored as a JSON-encoded string in frontmatter.
            # Maps source_filename → content_hash for merged sources.
            existing_source_hashes_raw = existing_meta.get("source_hashes")
            existing_source_hashes: dict[str, str] = {}
            if isinstance(existing_source_hashes_raw, str):
                try:
                    parsed = json.loads(existing_source_hashes_raw)
                    if isinstance(parsed, dict):
                        existing_source_hashes = parsed
                except (ValueError, json.JSONDecodeError):
                    pass
            elif isinstance(existing_source_hashes_raw, dict):
                existing_source_hashes = existing_source_hashes_raw

            # Idempotency: if this exact source has already contributed this exact content, skip
            if existing_source_hashes.get(source_key) == content_hash:
                report.events.append(
                    IngestEvent(
                        kind="skipped-idempotent",
                        page_path=resolved.page_path,
                        note="content unchanged (multi-source merge idempotency)",
                    )
                )
                continue

            # Legacy single-hash idempotency: compare ingest_hash stored in page
            # (for pages ingested before source_hashes was introduced)
            stored_hash = existing_meta.get("ingest_hash")
            if not existing_source_hashes and stored_hash == content_hash:
                # Identical content (legacy path, same single source) — skip
                report.events.append(
                    IngestEvent(
                        kind="skipped-idempotent",
                        page_path=resolved.page_path,
                        note="content unchanged",
                    )
                )
                continue

            # Determine if this is a multi-source merge situation:
            # MERGE when: not superseded AND a DIFFERENT source is providing content
            # for the same entity (i.e., source_key is not already the only known source).
            # SUPERSEDE when: same source provides UPDATED content (or no source_hashes yet
            # meaning the page was created by the same source).
            #
            # Logic:
            # - If existing_source_hashes is non-empty AND source_key is NOT in it
            #   → different source → MERGE
            # - If existing_source_hashes is non-empty AND source_key IS in it (but
            #   different hash, which was checked above) → same source updated → SUPERSEDE
            # - If existing_source_hashes is empty (legacy single-ingest_hash page)
            #   → treat as SUPERSEDE (original behavior preserved)
            _is_new_source_for_merge = (
                not already_superseded
                and bool(existing_source_hashes)
                and source_key not in existing_source_hashes
            )

            if _is_new_source_for_merge:
                # MERGE: append new content under "## Source: <source_key>" heading
                source_section_marker = f"## Source: {source_key}"
                if source_section_marker in existing_body:
                    # Source section exists — check if content already there (body idempotency)
                    section_start = existing_body.index(source_section_marker)
                    next_heading = existing_body.find("\n## ", section_start + 1)
                    if next_heading == -1:
                        section_content = existing_body[section_start:]
                    else:
                        section_content = existing_body[section_start:next_heading]
                    if para_body.strip() in section_content:
                        report.events.append(
                            IngestEvent(
                                kind="skipped-idempotent",
                                page_path=resolved.page_path,
                                note="content unchanged (source section already merged)",
                            )
                        )
                        continue

                # Append new source section to body
                new_source_section = f"\n\n{source_section_marker}\n\n{para_body}"
                merged_body = existing_body.rstrip() + new_source_section

                # Update source_hashes: add entry for this new source
                updated_source_hashes = dict(existing_source_hashes)
                updated_source_hashes[source_key] = content_hash

                # Update frontmatter: set source_hashes (JSON-encoded string), update learned_on
                merged_meta = dict(existing_meta)
                merged_meta["learned_on"] = today_str
                merged_meta["source_hashes"] = json.dumps(updated_source_hashes)

                try:
                    written_path = _write_page(
                        resolved.page_path.stem, merged_meta, merged_body, wiki_root=wiki_root
                    )
                except WikiRawImmutableError:
                    report.errors.append(
                        f"BUG: attempted write to wiki/raw/ for slug {resolved.page_path.stem}"
                    )
                    continue

                event_note = (
                    f"merged source {source_key!r} into existing page"
                )[:120]
                report.events.append(
                    IngestEvent(kind="updated", page_path=written_path, note=event_note)
                )
                try:
                    _log_event("INGEST", written_path, event_note, wiki_root=wiki_root)
                except Exception as exc:
                    report.errors.append(f"log write failed: {exc}")
                continue

            if already_superseded:
                # Create a fresh page for the new fact (not a supersede — old was already done)
                # Use the same slug — the existing page is already archived
                new_slug = slug
                # But wait — the file exists and is superseded. The new fact gets same slug
                # meaning we write the same filename. That would be an overwrite.
                # Per SCHEMA.md: same-entity updates use the same slug; ingest checks
                # if existing page is superseded. If it is, we can overwrite with new content.
                new_meta: dict[str, Any] = {
                    "entity": entity_name,
                    "aliases": existing_meta.get("aliases", []),
                    "level": existing_meta.get("level", "fact"),
                    "valid_from": today_str,
                    "learned_on": today_str,
                    "superseded_on": None,
                    "superseded_by": None,
                    "links": [],
                    "ingest_hash": content_hash,
                }
                validation_errors = _validate_frontmatter(new_meta)
                if validation_errors:
                    report.errors.append(
                        f"frontmatter validation failed for {slug}: {validation_errors}"
                    )
                    continue

                try:
                    written_path = _write_page(new_slug, new_meta, para_body, wiki_root=wiki_root)
                except WikiRawImmutableError:
                    report.errors.append(
                        f"BUG: attempted write to wiki/raw/ for slug {new_slug}"
                    )
                    continue

                if new_slug not in existing_slugs:
                    existing_slugs.append(new_slug)

                event_note = (
                    f"updated (replaced superseded page) from {provenance or 'inline'}"
                )[:120]
                report.events.append(
                    IngestEvent(kind="updated", page_path=written_path, note=event_note)
                )
                try:
                    _log_event("INGEST", written_path, event_note, wiki_root=wiki_root)
                except Exception as exc:
                    report.errors.append(f"log write failed: {exc}")
                continue

            # Existing current (non-superseded) page with different content → SUPERSEDE
            # Step 1: determine new page path
            # Same entity → same slug. We need a new file. Use slug with a suffix.
            # Find an available slug by incrementing
            new_slug = slug
            counter = 2
            while (pages_dir / f"{new_slug}.md").exists():
                new_slug = f"{slug}-{counter}"
                counter += 1

            # Step 2: create new page
            new_meta = {
                "entity": entity_name,
                "aliases": existing_meta.get("aliases", []),
                "level": existing_meta.get("level", "fact"),
                "valid_from": today_str,
                "learned_on": today_str,
                "superseded_on": None,
                "superseded_by": None,
                "links": [
                    {"type": "supersedes", "target": f"wiki/pages/{slug}.md"}
                ],
                "ingest_hash": content_hash,
            }
            validation_errors = _validate_frontmatter(new_meta)
            if validation_errors:
                report.errors.append(
                    f"frontmatter validation failed for {new_slug}: {validation_errors}"
                )
                continue

            prov_note = provenance or "inline"
            body_with_prov = f"<!-- provenance: {prov_note} -->\n\n{para_body}"

            try:
                new_page_path = _write_page(
                    new_slug, new_meta, body_with_prov, wiki_root=wiki_root
                )
            except WikiRawImmutableError:
                report.errors.append(
                    f"BUG: attempted write to wiki/raw/ for slug {new_slug}"
                )
                continue

            if new_slug not in existing_slugs:
                existing_slugs.append(new_slug)

            # Step 3: patch OLD page frontmatter only
            existing_meta["superseded_on"] = today_str
            existing_meta["superseded_by"] = f"wiki/pages/{new_slug}.md"
            # Do NOT touch valid_from or body
            try:
                old_page_content = _render_frontmatter(existing_meta) + "\n" + existing_body
                resolved.page_path.write_text(old_page_content, encoding="utf-8")
            except OSError as exc:
                report.errors.append(f"failed to patch old page {resolved.page_path}: {exc}")
                continue

            # Step 4: append SUPERSEDE log entry
            supersede_note = (
                f"superseded by wiki/pages/{new_slug}.md"
            )[:120]
            try:
                _log_event("SUPERSEDE", resolved.page_path, supersede_note, wiki_root=wiki_root)
            except Exception as exc:
                report.errors.append(f"log write failed: {exc}")

            report.events.append(
                IngestEvent(
                    kind="superseded",
                    page_path=resolved.page_path,
                    note=supersede_note,
                )
            )

            # INGEST event for new page
            ingest_note = (
                f"created (supersedes {slug}.md) from {provenance or 'inline'}"
            )[:120]
            report.events.append(
                IngestEvent(kind="created", page_path=new_page_path, note=ingest_note)
            )
            try:
                _log_event("INGEST", new_page_path, ingest_note, wiki_root=wiki_root)
            except Exception as exc:
                report.errors.append(f"log write failed: {exc}")

            # Step 5: rebuild index (done once at end)
            continue

        # ------ New entity: create page ------
        # Slug collision: check if slug is already used
        final_slug = slug
        if final_slug in existing_slugs:
            counter = 2
            while f"{slug}-{counter}" in existing_slugs:
                counter += 1
            final_slug = f"{slug}-{counter}"

        prov_note = provenance or "inline"
        body_with_prov = f"<!-- provenance: {prov_note} -->\n\n{para_body}"

        # Determine source key for new pages too
        if source_path is not None:
            _new_entity_source_key = source_path.name
        else:
            _new_entity_source_key = "inline"

        new_meta = {
            "entity": entity_name,
            "aliases": [],
            "level": "fact",
            "valid_from": today_str,
            "learned_on": today_str,
            "superseded_on": None,
            "superseded_by": None,
            "links": [],
            "ingest_hash": content_hash,
            "source_hashes": json.dumps({_new_entity_source_key: content_hash}),
        }

        validation_errors = _validate_frontmatter(new_meta)
        if validation_errors:
            report.errors.append(
                f"frontmatter validation failed for {final_slug}: {validation_errors}"
            )
            continue

        try:
            written_path = _write_page(final_slug, new_meta, body_with_prov, wiki_root=wiki_root)
        except WikiRawImmutableError:
            report.errors.append(
                f"BUG: attempted write to wiki/raw/ for slug {final_slug}"
            )
            continue

        existing_slugs.append(final_slug)

        event_note = (f"created from {provenance or 'inline'} (new entity)")[:120]
        report.events.append(
            IngestEvent(kind="created", page_path=written_path, note=event_note)
        )
        try:
            _log_event("INGEST", written_path, event_note, wiki_root=wiki_root)
        except Exception as exc:
            report.errors.append(f"log write failed: {exc}")

    # ------------------------------------------------------------------
    # Rebuild index if any pages were created or superseded
    # ------------------------------------------------------------------
    changed = any(e.kind in ("created", "updated", "superseded") for e in report.events)
    if changed:
        try:
            _rebuild_index(wiki_root)
        except Exception as exc:
            report.errors.append(f"index rebuild failed: {exc}")

    return report


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Return a set of lowercase alphanum tokens from text (for relevance scoring)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _score_page(
    question_tokens: set[str],
    meta: dict[str, Any],
    body: str,
    page_path: Path,
) -> float:
    """Compute a relevance score [0..1] for a wiki page against a question.

    Strategy (heuristic, no LLM):
      - Entity name tokens in question: strong signal (weight 0.5 per match).
      - Alias tokens in question: moderate signal (weight 0.3 per match).
      - Body tokens in question: weak signal (weight 0.05 per token hit, capped at 0.2).
      - Score is normalised to [0..1] by clamping.
    """
    if not question_tokens:
        return 0.0

    score = 0.0

    # Entity name tokens
    entity = meta.get("entity", "")
    entity_tokens = _tokenize(entity)
    matched_entity = question_tokens & entity_tokens
    score += 0.5 * len(matched_entity)

    # Alias tokens
    aliases = meta.get("aliases", [])
    for alias in aliases:
        if isinstance(alias, str):
            alias_tokens = _tokenize(alias)
            if question_tokens & alias_tokens:
                score += 0.3
                break  # count at most once

    # Body tokens (cheap bag-of-words overlap)
    body_tokens = _tokenize(body)
    body_hits = len(question_tokens & body_tokens)
    score += min(0.2, 0.05 * body_hits)

    return min(1.0, score)


def _parse_index_page_paths(index_text: str, wiki_root: Path) -> list[Path]:
    """Extract wiki/pages/ paths from index.md link syntax.

    Matches both:
      - [label](wiki/pages/some-entity.md)  — markdown links
      - wiki/pages/some-entity.md           — bare paths
    Returns resolved Path objects that exist on disk.
    """
    pages_dir = wiki_root / "pages"
    found: list[Path] = []
    seen: set[str] = set()

    # Markdown links: [text](wiki/pages/slug.md)
    for m in re.finditer(r'\[([^\]]*)\]\((wiki/pages/[^\)]+\.md)\)', index_text):
        rel = m.group(2)
        if rel not in seen:
            seen.add(rel)
            # rel is workspace-relative: wiki/pages/<slug>.md
            candidate = wiki_root.parent / rel
            if candidate.exists():
                found.append(candidate)

    # Bare paths (backup, in case index uses plain paths)
    for m in re.finditer(r'\bwiki/pages/([\w\-]+\.md)\b', index_text):
        rel = f"wiki/pages/{m.group(1)}"
        if rel not in seen:
            seen.add(rel)
            candidate = wiki_root.parent / rel
            if candidate.exists():
                found.append(candidate)

    return found


def _synthesize_answer(
    question: str,
    hits: list[QueryHit],
    include_superseded: bool,
) -> str:
    """Produce a natural-language answer from the ranked hit list.

    Pure text synthesis — no LLM call. Combines entity names and excerpts from
    the top-scoring pages into a readable paragraph. Returns a fallback string
    if no hits are found.
    """
    if not hits:
        return f"No information found in the wiki for: {question!r}"

    # Collect the top 3 hits only (avoid an oversized answer)
    top = hits[:3]

    parts: list[str] = []
    for hit in top:
        sup_tag = " (superseded)" if hit.superseded_on else ""
        excerpt = hit.excerpt.strip().replace("\n", " ")
        parts.append(f"[{hit.entity}{sup_tag}] {excerpt}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public function: query()
# ---------------------------------------------------------------------------


def query(
    question: str,
    *,
    wiki_root: Path | None = None,
    include_superseded: bool = False,
) -> QueryResult:
    """Answer a natural-language question about the user using wiki/pages/.

    Parameters
    ----------
    question:
        A natural-language question, e.g. "Where does Ashton live?"
        or "What is Ashton's primary programming language?".
    wiki_root:
        Path to the wiki/ directory.  Defaults to wiki/ under
        PRAXIS_WORKSPACE_ROOT if not provided.
    include_superseded:
        If True, include pages whose ``superseded_on`` field is set.
        Default False — only current (non-superseded) pages are considered.

    Returns
    -------
    QueryResult
        ``answer``      — synthesised text response (heuristic, no LLM call).
        ``citations``   — workspace-relative paths of the pages that were read
                          to produce the answer (never invented; only pages
                          actually loaded from disk).
        ``confidence``  — "high" if at least one direct page matched an entity
                          name token from the question; "medium" if pages were
                          found via body overlap only; "low" if no relevant
                          pages were found.

    Bitemporal handling
    -------------------
    Pages with ``superseded_on`` set are **excluded** by default.  Pass
    ``include_superseded=True`` to retrieve historical facts as well.
    Superseded hits are annotated in the ``QueryHit.superseded_on`` field and
    tagged "[superseded]" in ``QueryHit.excerpt`` — callers should surface this
    to users.

    Read-safety guarantee
    ---------------------
    ``query()`` is **pure read**.  It MUST NOT write to disk, MUST NOT append
    to ``wiki/log.md``, and MUST NOT modify any frontmatter.  This guarantee
    is structural: no write syscall appears in this function or any helper it
    calls.
    """
    if wiki_root is None:
        wiki_root = _wiki_root()

    notes: list[str] = []
    index_consulted = False

    # ------------------------------------------------------------------
    # Step 1: Read wiki/index.md FIRST to identify candidate page paths
    # ------------------------------------------------------------------
    index_path = wiki_root / "index.md"
    candidate_paths: list[Path] = []

    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding="utf-8")
            index_consulted = True
            candidate_paths = _parse_index_page_paths(index_text, wiki_root)
        except OSError as exc:
            notes.append(f"could not read wiki/index.md: {exc}")
    else:
        notes.append("wiki/index.md not found; falling back to full pages scan")
        # Fallback: scan all pages (index may not have been built yet)
        pages_dir = wiki_root / "pages"
        if pages_dir.exists():
            candidate_paths = list(pages_dir.glob("*.md"))

    if not candidate_paths:
        # Index exists but is empty, or no pages at all
        notes.append("wiki is empty — no pages to search")
        return QueryResult(
            question=question,
            hits=[],
            index_consulted=index_consulted,
            notes=notes,
            answer=f"No information found in the wiki for: {question!r}",
            citations=[],
            confidence="low",
        )

    # ------------------------------------------------------------------
    # Step 2: Load and filter candidate pages
    # ------------------------------------------------------------------
    question_tokens = _tokenize(question)
    hits: list[QueryHit] = []
    superseded_count = 0

    for page_path in candidate_paths:
        try:
            content = page_path.read_text(encoding="utf-8")
        except OSError as exc:
            notes.append(f"could not read {page_path}: {exc}")
            continue

        meta, body = _parse_frontmatter(content)
        if not meta:
            continue

        # Step 3: Bitemporal filter — skip superseded unless requested
        is_superseded = meta.get("superseded_on") is not None
        if is_superseded and not include_superseded:
            superseded_count += 1
            continue

        # Score this page against the question
        score = _score_page(question_tokens, meta, body, page_path)
        if score <= 0.0:
            continue

        # Build excerpt: first ~400 chars of body (strip markdown noise)
        excerpt_raw = body.strip()
        # Remove provenance comment if present
        excerpt_raw = re.sub(r"^<!--[^>]*-->\s*", "", excerpt_raw, flags=re.DOTALL)
        excerpt = excerpt_raw[:400]

        # Workspace-relative citation path
        workspace = wiki_root.parent
        try:
            rel = str(page_path.relative_to(workspace))
        except ValueError:
            rel = str(page_path)

        hits.append(
            QueryHit(
                page_path=Path(rel),
                entity=meta.get("entity", page_path.stem),
                level=meta.get("level", "fact"),
                valid_from=meta.get("valid_from", ""),
                learned_on=meta.get("learned_on", ""),
                superseded_on=meta.get("superseded_on"),
                excerpt=excerpt,
                links=meta.get("links", []),
                score=score,
            )
        )

    if superseded_count > 0:
        notes.append(
            f"{superseded_count} superseded page(s) excluded "
            "(pass include_superseded=True to include history)"
        )

    # ------------------------------------------------------------------
    # Step 4: Rank by score (descending); synthesise answer
    # ------------------------------------------------------------------
    hits.sort(key=lambda h: h.score, reverse=True)

    # ------------------------------------------------------------------
    # Step 5: Determine confidence and build citations list
    # ------------------------------------------------------------------
    # "high"   — at least one page has entity-name token overlap with question
    # "medium" — pages found but only via body-token overlap
    # "low"    — no pages found
    citations: list[str] = [str(h.page_path) for h in hits]

    if not hits:
        confidence = "low"
    else:
        top_score = hits[0].score
        if top_score >= 0.5:
            confidence = "high"
        elif top_score >= 0.05:
            confidence = "medium"
        else:
            confidence = "low"

    # ------------------------------------------------------------------
    # Step 6: Synthesise answer text
    # ------------------------------------------------------------------
    answer = _synthesize_answer(question, hits, include_superseded)

    return QueryResult(
        question=question,
        hits=hits,
        index_consulted=index_consulted,
        notes=notes,
        answer=answer,
        citations=citations,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Lint helpers
# ---------------------------------------------------------------------------


def _lint_load_all_pages(
    wiki_root: Path,
) -> list[tuple[Path, dict[str, Any], str]]:
    """Load all pages from wiki/pages/ and return (path, meta, body) triples.

    Pages that cannot be read or parsed are silently skipped — the caller
    handles frontmatter errors separately.
    """
    pages_dir = wiki_root / "pages"
    result: list[tuple[Path, dict[str, Any], str]] = []
    if not pages_dir.exists():
        return result
    for page_file in sorted(pages_dir.glob("*.md")):
        try:
            content = page_file.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(content)
        result.append((page_file, meta, body))
    return result


def _lint_rel_path(page_path: Path, wiki_root: Path) -> str:
    """Return workspace-relative path string for a page."""
    workspace = wiki_root.parent
    try:
        return str(page_path.relative_to(workspace))
    except ValueError:
        return str(page_path)


def _lint_frontmatter_errors(
    all_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
) -> list[dict]:
    """Check every page for required frontmatter fields and type validity.

    Also checks that supersessed_on/superseded_by are consistent (both null
    or both set).
    """
    errors: list[dict] = []
    for page_path, meta, _body in all_pages:
        rel = _lint_rel_path(page_path, wiki_root)
        for field in _REQUIRED_FIELDS:
            if field not in meta:
                errors.append({
                    "page": rel,
                    "field": field,
                    "error": f"missing required field: {field!r}",
                })

        # Type checks (only if fields present)
        if "level" in meta and meta["level"] not in ("theme", "topic", "fact"):
            errors.append({
                "page": rel,
                "field": "level",
                "error": f"must be theme/topic/fact, got {meta['level']!r}",
            })
        if "links" in meta and not isinstance(meta["links"], list):
            errors.append({
                "page": rel,
                "field": "links",
                "error": "must be a list",
            })
        if "aliases" in meta and not isinstance(meta["aliases"], list):
            errors.append({
                "page": rel,
                "field": "aliases",
                "error": "must be a list",
            })

        # Consistency: superseded_on and superseded_by must both be null or both set
        sup_on = meta.get("superseded_on")
        sup_by = meta.get("superseded_by")
        if (sup_on is None) != (sup_by is None):
            errors.append({
                "page": rel,
                "field": "superseded_on/superseded_by",
                "error": (
                    "superseded_on and superseded_by must both be null or both set; "
                    f"got superseded_on={sup_on!r}, superseded_by={sup_by!r}"
                ),
            })

    return errors


def _lint_contradictions(
    active_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
) -> list[dict]:
    """Find pairs of active pages for the same entity slug with no supersedes link.

    Heuristic: two pages share an entity slug (or one page's slug is a prefix
    variant of the other, e.g. 'ashton-antony' and 'ashton-antony-2') AND their
    body hashes differ AND neither page carries a supersedes link to the other.

    Bitemporal note: superseded pages are excluded from active_pages by the caller.
    """
    contradictions: list[dict] = []
    # Group by base slug (strip trailing -N collision suffix)
    slug_groups: dict[str, list[tuple[Path, dict[str, Any], str]]] = {}
    for page_path, meta, body in active_pages:
        slug = page_path.stem
        # Normalise: strip trailing -<number> collision suffix for grouping
        base = re.sub(r'-\d+$', '', slug)
        slug_groups.setdefault(base, []).append((page_path, meta, body))

    for base_slug, group in slug_groups.items():
        if len(group) < 2:
            continue
        # Check every pair in the group
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                pa_path, pa_meta, pa_body = group[i]
                pb_path, pb_meta, pb_body = group[j]

                # Different body hashes?
                if _hash_content(pa_body) == _hash_content(pb_body):
                    continue  # identical bodies — not a contradiction

                # Do they already carry a supersedes link between them?
                rel_a = _lint_rel_path(pa_path, wiki_root)
                rel_b = _lint_rel_path(pb_path, wiki_root)

                def _has_supersedes_link(meta: dict, target_rel: str) -> bool:
                    for lnk in meta.get("links", []):
                        if (
                            isinstance(lnk, dict)
                            and lnk.get("type") == "supersedes"
                            and target_rel in str(lnk.get("target", ""))
                        ):
                            return True
                    return False

                if _has_supersedes_link(pa_meta, rel_b):
                    continue
                if _has_supersedes_link(pb_meta, rel_a):
                    continue

                contradictions.append({
                    "page_a": rel_a,
                    "page_b": rel_b,
                    "note": (
                        f"same entity slug base '{base_slug}' with different body content "
                        "and no supersedes link — potential contradiction or unresolved update"
                    ),
                })

    return contradictions


def _lint_stale_facts(
    active_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
    stale_days: int,
) -> list[dict]:
    """Find active pages whose valid_from is older than stale_days and not superseded.

    Bitemporal note: superseded pages are excluded (they are retired, not stale).
    Only current pages that have not been updated are flagged.

    Returns list of dicts with keys: page, days_since_update, valid_from.
    """
    stale: list[dict] = []
    now = _now_utc()
    threshold_date = now.date()

    for page_path, meta, _body in active_pages:
        valid_from_str = meta.get("valid_from")
        if not valid_from_str or not isinstance(valid_from_str, str):
            continue
        try:
            # Parse ISO date (may be "YYYY-MM-DD" or "1900-01-01" sentinel)
            vf = datetime.strptime(valid_from_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        delta_days = (threshold_date - vf).days
        if delta_days > stale_days:
            stale.append({
                "page": _lint_rel_path(page_path, wiki_root),
                "days_since_update": delta_days,
                "valid_from": valid_from_str[:10],
            })

    return stale


def _lint_orphan_pages(
    active_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
) -> list[str]:
    """Find active pages not referenced by wiki/index.md and with no typed links.

    A page is an orphan if ALL of the following hold:
    1. It has no outbound typed links (links: []).
    2. No other active page carries a typed link pointing to it.
    3. It is not referenced in wiki/index.md.

    Bitemporal note: superseded pages are excluded from the active corpus.
    """
    # Build set of active page relative paths
    active_rels: set[str] = {
        _lint_rel_path(p, wiki_root) for p, _, _ in active_pages
    }

    # Collect all targets referenced by any active page's typed links
    referenced_targets: set[str] = set()
    pages_with_outbound: set[str] = set()
    for page_path, meta, _body in active_pages:
        links = meta.get("links", [])
        if not isinstance(links, list):
            continue
        for lnk in links:
            if isinstance(lnk, dict):
                target = str(lnk.get("target", ""))
                if target:
                    # Normalise to workspace-relative path
                    # target may be "wiki/pages/foo.md" (workspace-relative) already
                    referenced_targets.add(target)
                    pages_with_outbound.add(_lint_rel_path(page_path, wiki_root))

    # Read wiki/index.md for referenced paths
    index_referenced: set[str] = set()
    index_path = wiki_root / "index.md"
    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding="utf-8")
        except OSError:
            index_text = ""
        for m in re.finditer(r'\(wiki/pages/([\w\-]+\.md)\)', index_text):
            index_referenced.add(f"wiki/pages/{m.group(1)}")
        for m in re.finditer(r'\bwiki/pages/([\w\-]+\.md)\b', index_text):
            index_referenced.add(f"wiki/pages/{m.group(1)}")

    orphans: list[str] = []
    for page_path, _meta, _body in active_pages:
        rel = _lint_rel_path(page_path, wiki_root)
        has_outbound = rel in pages_with_outbound
        is_targeted = rel in referenced_targets
        in_index = rel in index_referenced
        if not has_outbound and not is_targeted and not in_index:
            orphans.append(rel)

    return orphans


def _lint_duplicate_entities(
    active_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
) -> list[dict]:
    """Find pairs of active pages whose entity slugs have Jaro-Winkler >= 0.92.

    Excludes pairs already connected by a supersedes link (they are intentionally
    related). Reuses _jaro_winkler from the entity resolution logic.

    Bitemporal note: superseded pages are excluded from the active corpus.
    """
    duplicates: list[dict] = []
    page_list = list(active_pages)

    for i in range(len(page_list)):
        for j in range(i + 1, len(page_list)):
            pa_path, pa_meta, _pa_body = page_list[i]
            pb_path, pb_meta, _pb_body = page_list[j]

            slug_a = _slugify(pa_meta.get("entity", pa_path.stem))
            slug_b = _slugify(pb_meta.get("entity", pb_path.stem))

            if slug_a == slug_b:
                # Identical slug — already caught by contradictions or same entity
                continue

            sim = _jaro_winkler(slug_a, slug_b)
            if sim < 0.92:
                continue

            # Skip if already connected by a supersedes link
            rel_a = _lint_rel_path(pa_path, wiki_root)
            rel_b = _lint_rel_path(pb_path, wiki_root)

            def _has_supersedes(meta: dict, other_rel: str) -> bool:
                for lnk in meta.get("links", []):
                    if (
                        isinstance(lnk, dict)
                        and lnk.get("type") == "supersedes"
                        and other_rel in str(lnk.get("target", ""))
                    ):
                        return True
                return False

            if _has_supersedes(pa_meta, rel_b) or _has_supersedes(pb_meta, rel_a):
                continue

            duplicates.append({
                "page_a": rel_a,
                "page_b": rel_b,
                "similarity": round(sim, 4),
            })

    return duplicates


def _lint_missing_links(
    active_pages: list[tuple[Path, dict[str, Any], str]],
    wiki_root: Path,
) -> list[dict]:
    """Find pages that mention a known entity in their body but carry no typed link.

    Detection is conservative: exact entity name (or alias) must appear as a
    whole word in the body text (case-insensitive). This avoids false positives
    from partial word matches. The suggested link type defaults to "relates"
    unless the entity is a known parent (which requires theme/contains inference
    beyond this heuristic scope).

    Bitemporal note: only active pages are scanned for mentions; the known-entity
    registry is also built from active pages only.
    """
    # Build entity registry: {entity_name_lower: rel_path}
    entity_registry: dict[str, str] = {}
    alias_registry: dict[str, str] = {}  # alias_lower → rel_path of the page

    for page_path, meta, _body in active_pages:
        rel = _lint_rel_path(page_path, wiki_root)
        entity_name = meta.get("entity", "")
        if entity_name:
            entity_registry[entity_name.lower()] = rel
            # Also register the slug form
            entity_registry[_slugify(entity_name).replace("-", " ")] = rel
        for alias in meta.get("aliases", []):
            if isinstance(alias, str) and alias.strip():
                alias_registry[alias.lower().strip()] = rel

    missing: list[dict] = []

    for page_path, meta, body in active_pages:
        rel = _lint_rel_path(page_path, wiki_root)

        # Collect targets of existing typed links on this page
        existing_link_targets: set[str] = set()
        for lnk in meta.get("links", []):
            if isinstance(lnk, dict):
                existing_link_targets.add(str(lnk.get("target", "")))

        body_lower = body.lower()

        # Check each known entity
        already_found: set[str] = set()  # entity names already flagged for this page
        for entity_lower, target_rel in list(entity_registry.items()) + list(alias_registry.items()):
            if target_rel == rel:
                continue  # skip self-references
            if entity_lower in already_found:
                continue
            if len(entity_lower) < 3:
                continue  # too short — too many false positives

            # Whole-word match in body (word boundary)
            pattern = r'\b' + re.escape(entity_lower) + r'\b'
            if not re.search(pattern, body_lower):
                continue

            # Already has a typed link to this target?
            # Check by target path containment (partial match for robustness)
            already_linked = any(
                target_rel in tgt or tgt in target_rel
                for tgt in existing_link_targets
            )
            if already_linked:
                continue

            already_found.add(entity_lower)
            missing.append({
                "page": rel,
                "mentioned_entity": entity_lower,
                "suggested_type": "relates",
            })

    return missing


# ---------------------------------------------------------------------------
# Public function: lint()
# ---------------------------------------------------------------------------


def lint(
    *,
    wiki_root: Path | None = None,
    stale_days: int | None = None,
) -> LintReport:
    """Scan wiki/pages/ for schema violations, contradictions, and stale facts.

    Parameters
    ----------
    wiki_root:
        Path to the wiki/ directory.  Defaults to wiki/ under
        PRAXIS_WORKSPACE_ROOT if not provided.
    stale_days:
        Number of days after which an active page is considered stale.
        Defaults to 365 (or the value of PRAXIS_WIKI_STALE_DAYS env var).

    Returns
    -------
    LintReport
        A dataclass with six finding categories (see below).  NEVER auto-applied.

    Finding categories
    ------------------
    contradictions:
        Pairs of current (non-superseded) pages that share the same entity slug
        base but have different body content and no supersedes link between them.
        Heuristic — may produce false positives.  Humans decide the resolution.

    stale_facts:
        Active pages where valid_from is more than stale_days in the past and
        superseded_on is null.  Configurable via PRAXIS_WIKI_STALE_DAYS env var
        (default 90).  Each entry is a dict: {"page", "days_since_update", "valid_from"}.

    orphan_pages:
        Active pages with no outbound typed links, not targeted by any other
        page's typed link, and not referenced in wiki/index.md.

    duplicate_entities:
        Pairs of active pages whose entity slugs score >= 0.92 on Jaro-Winkler
        but are not connected by a supersedes link.

    missing_links:
        Active pages that mention a known entity by name (exact whole-word match
        in body text) but carry no typed link to that entity's page.  Suggested
        link type is "relates" (conservative default).

    frontmatter_errors:
        Pages missing required frontmatter fields, or fields of the wrong type,
        or inconsistent supersession state (superseded_on/superseded_by mismatch).

    Bitemporal handling
    -------------------
    Pages with superseded_on set are EXCLUDED from the active corpus for all
    checks EXCEPT frontmatter_errors (frontmatter violations are hard errors
    regardless of supersession state).  The rationale: a superseded page is
    retired — it should not be flagged as an orphan or duplicate, and its stale
    date is no longer relevant.

    Side effects
    ------------
    lint() appends exactly ONE LINT event to wiki/log.md with a summary of
    finding counts.  This is the only write it performs.  It does NOT rewrite
    any page, does NOT modify any frontmatter, and does NOT touch wiki/index.md.
    The LINT event is appended even if no findings are found (summary: "0
    findings").

    Report, do not auto-apply
    --------------------------
    lint() is purely advisory.  No finding is auto-corrected.  The returned
    LintReport is for human review.
    """
    if wiki_root is None:
        wiki_root = _wiki_root()

    # Resolve stale_days: parameter > env var > default 90
    if stale_days is None:
        env_val = os.environ.get("PRAXIS_WIKI_STALE_DAYS", "")
        try:
            stale_days = int(env_val)
        except (ValueError, TypeError):
            stale_days = 90

    # ------------------------------------------------------------------
    # Load all pages (including superseded)
    # ------------------------------------------------------------------
    all_pages = _lint_load_all_pages(wiki_root)

    # Separate active (non-superseded) and superseded pages
    active_pages: list[tuple[Path, dict[str, Any], str]] = []
    for page_path, meta, body in all_pages:
        if meta.get("superseded_on") is None:
            active_pages.append((page_path, meta, body))

    # ------------------------------------------------------------------
    # Frontmatter errors — check ALL pages (active + superseded)
    # ------------------------------------------------------------------
    frontmatter_errors = _lint_frontmatter_errors(all_pages, wiki_root)

    # ------------------------------------------------------------------
    # Contradictions — active pages only
    # ------------------------------------------------------------------
    contradictions = _lint_contradictions(active_pages, wiki_root)

    # ------------------------------------------------------------------
    # Stale facts — active pages only
    # ------------------------------------------------------------------
    stale_facts = _lint_stale_facts(active_pages, wiki_root, stale_days)

    # ------------------------------------------------------------------
    # Orphan pages — active pages only
    # ------------------------------------------------------------------
    orphan_pages = _lint_orphan_pages(active_pages, wiki_root)

    # ------------------------------------------------------------------
    # Duplicate entities — active pages only
    # ------------------------------------------------------------------
    duplicate_entities = _lint_duplicate_entities(active_pages, wiki_root)

    # ------------------------------------------------------------------
    # Missing typed links — active pages only
    # ------------------------------------------------------------------
    missing_links = _lint_missing_links(active_pages, wiki_root)

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------
    report = LintReport(
        contradictions=contradictions,
        stale_facts=stale_facts,
        orphan_pages=orphan_pages,
        duplicate_entities=duplicate_entities,
        missing_links=missing_links,
        frontmatter_errors=frontmatter_errors,
    )

    # ------------------------------------------------------------------
    # Append ONE LINT summary event to wiki/log.md (the only write)
    # ------------------------------------------------------------------
    summary_note = (
        f"{report.summary()} — stale_days={stale_days}"
    )[:120]

    # Use wiki/index.md as the page path for the LINT event (summary event
    # has no single page target; index.md is the best canonical anchor)
    lint_target = wiki_root / "index.md"
    # If index doesn't exist yet, fall back to the wiki root itself
    if not lint_target.exists():
        lint_target = wiki_root / "log.md"

    try:
        _log_event("LINT", lint_target, summary_note, wiki_root=wiki_root)
    except Exception:
        # Log failures must not prevent the report from being returned
        pass

    return report


# ---------------------------------------------------------------------------
# Public function: export_graph()
# ---------------------------------------------------------------------------


def export_graph(*, wiki_root: Path | None = None) -> dict:
    """Export the wiki relationship graph as a JSON dict of nodes and edges.

    Parameters
    ----------
    wiki_root:
        Path to the wiki/ directory.  Defaults to wiki/ under
        PRAXIS_WORKSPACE_ROOT if not provided.

    Returns
    -------
    dict
        {"nodes": [...], "edges": [...], "generated_at": ISO8601_timestamp}

        Nodes: one per non-superseded page.
            {"id": slug, "label": entity_name, "level": level, "valid_from": valid_from}

        Edges: one per typed link in a page's frontmatter links: section.
            {"source": this_slug, "target": linked_slug, "type": link_type}
            linked_slug is extracted from the target path (wiki/pages/<slug>.md).

        Writes the result as indented JSON to wiki/graph.json inside wiki_root.
        If no pages/ directory or no .md files, returns empty graph.

    Side effects
    ------------
    Writes wiki/graph.json (creates if absent, overwrites if present).
    This is a Praxis-owned file inside wiki/, inside WORKSPACE_ROOT — allowed.
    """
    if wiki_root is None:
        wiki_root = _wiki_root()

    now = _now_utc()
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    pages_dir = wiki_root / "pages"
    if not pages_dir.exists():
        result: dict = {"nodes": [], "edges": [], "generated_at": generated_at}
        _write_graph_json(wiki_root, result)
        return result

    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        result = {"nodes": [], "edges": [], "generated_at": generated_at}
        _write_graph_json(wiki_root, result)
        return result

    nodes: list[dict] = []
    edges: list[dict] = []

    for page_file in page_files:
        try:
            content = page_file.read_text(encoding="utf-8")
        except OSError:
            continue

        meta, _ = _parse_frontmatter(content)
        if not meta:
            continue

        # Exclude superseded pages
        if meta.get("superseded_on") is not None:
            continue

        slug = page_file.stem
        entity_name = meta.get("entity", slug)
        level = meta.get("level", "fact")
        valid_from = meta.get("valid_from", "")

        nodes.append({
            "id": slug,
            "label": entity_name,
            "level": level,
            "valid_from": valid_from,
        })

        # Extract edges from typed links
        links = meta.get("links", [])
        if not isinstance(links, list):
            continue
        for lnk in links:
            if not isinstance(lnk, dict):
                continue
            link_type = lnk.get("type", "")
            target = lnk.get("target", "")
            if not link_type or not target:
                continue
            # Extract slug from target path (e.g. "wiki/pages/foo-bar.md" → "foo-bar")
            m = re.search(r"wiki/pages/([\w\-]+)\.md", str(target))
            if not m:
                continue
            target_slug = m.group(1)
            edges.append({
                "source": slug,
                "target": target_slug,
                "type": link_type,
            })

    result = {"nodes": nodes, "edges": edges, "generated_at": generated_at}
    _write_graph_json(wiki_root, result)
    return result


def _write_graph_json(wiki_root: Path, data: dict) -> None:
    """Write the graph dict as indented JSON to wiki/graph.json."""
    graph_path = wiki_root / "graph.json"
    _guard_not_raw(graph_path, wiki_root)
    graph_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public function: export_notion()
# ---------------------------------------------------------------------------


def export_notion(page_slug: str, *, wiki_root: Path | None = None) -> dict:
    """Export a wiki page as Notion-compatible block format.

    Parameters
    ----------
    page_slug:
        The slug of the page (without .md extension) in wiki/pages/.
    wiki_root:
        Path to the wiki/ directory. Defaults to wiki/ under PRAXIS_WORKSPACE_ROOT.

    Returns
    -------
    dict
        {"title": entity_name, "blocks": [block, ...]}

        Block types used:
          {"type": "heading_1", "content": str}
          {"type": "heading_2", "content": str}
          {"type": "paragraph", "content": str}
          {"type": "callout", "content": str, "emoji": "🔗"}

        Typed links are rendered as callout blocks: "relates → wiki/pages/foo.md"

    Raises
    ------
    WikiError
        If the page file does not exist.
    """
    if wiki_root is None:
        wiki_root = _wiki_root()

    page_path = wiki_root / "pages" / f"{page_slug}.md"
    if not page_path.exists():
        raise WikiError(f"export_notion: page not found: {page_slug}")

    content = page_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(content)

    entity_name = meta.get("entity", page_slug)
    blocks: list[dict] = []

    # H1 heading = entity name
    blocks.append({"type": "heading_1", "content": entity_name})

    # Metadata paragraph
    valid_from = str(meta.get("valid_from", ""))
    level = str(meta.get("level", "fact"))
    if valid_from:
        blocks.append({"type": "paragraph", "content": f"valid_from: {valid_from} | level: {level}"})

    # Body lines → paragraph / heading blocks
    for line in body.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            blocks.append({"type": "heading_2", "content": stripped[3:]})
        elif stripped.startswith("# "):
            blocks.append({"type": "heading_1", "content": stripped[2:]})
        else:
            blocks.append({"type": "paragraph", "content": stripped})

    # Typed links → callout blocks
    links = meta.get("links", [])
    if isinstance(links, list) and links:
        valid_links = [lnk for lnk in links if isinstance(lnk, dict) and lnk.get("type") and lnk.get("target")]
        if valid_links:
            blocks.append({"type": "heading_2", "content": "Linked entities"})
            for lnk in valid_links:
                link_type = lnk.get("type", "relates")
                target = lnk.get("target", "")
                blocks.append({
                    "type": "callout",
                    "content": f"{link_type} → {target}",
                    "emoji": "\U0001f517",
                })

    return {"title": entity_name, "blocks": blocks}


# ---------------------------------------------------------------------------
# Public function: export_linear()
# ---------------------------------------------------------------------------


def export_linear(page_slug: str, *, wiki_root: Path | None = None) -> str:
    """Export a wiki page as a Linear issue description (Markdown).

    Parameters
    ----------
    page_slug:
        The slug of the page (without .md extension) in wiki/pages/.
    wiki_root:
        Path to the wiki/ directory. Defaults to wiki/ under PRAXIS_WORKSPACE_ROOT.

    Returns
    -------
    str
        Markdown string suitable for a Linear issue description body.
        Includes entity name as H1, metadata, body content, and typed links.

    Raises
    ------
    WikiError
        If the page file does not exist.
    """
    if wiki_root is None:
        wiki_root = _wiki_root()

    page_path = wiki_root / "pages" / f"{page_slug}.md"
    if not page_path.exists():
        raise WikiError(f"export_linear: page not found: {page_slug}")

    content = page_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(content)

    entity_name = meta.get("entity", page_slug)
    valid_from = str(meta.get("valid_from", ""))
    level = str(meta.get("level", "fact"))

    lines: list[str] = [f"# {entity_name}", ""]

    if valid_from:
        lines.append(f"*valid_from: {valid_from} | level: {level}*")
        lines.append("")

    body_stripped = body.strip()
    if body_stripped:
        lines.append(body_stripped)
        lines.append("")

    # Typed links section
    links = meta.get("links", [])
    if isinstance(links, list):
        valid_links = [lnk for lnk in links if isinstance(lnk, dict) and lnk.get("type") and lnk.get("target")]
        if valid_links:
            lines.append("---")
            lines.append("**Linked entities:**")
            for lnk in valid_links:
                link_type = lnk.get("type", "relates")
                target = lnk.get("target", "")
                lines.append(f"- {link_type}: {target}")
            lines.append("")

    return "\n".join(lines)
