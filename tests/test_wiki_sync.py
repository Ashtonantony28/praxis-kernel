"""Tests for Option J: Wiki -> Notion/Linear sync.

All tests are mocked — no real API calls, no real file I/O beyond tmp_path.
703 pre-existing tests must still pass.
"""

from __future__ import annotations

import io
import json
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_wiki_page(wiki_root: Path, slug: str = "alice") -> Path:
    """Write a minimal but valid wiki page. Returns the page path."""
    pages_dir = wiki_root / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        "entity: Alice\n"
        "level: fact\n"
        "valid_from: 2026-01-01\n"
        "learned_on: 2026-01-01\n"
        "superseded_on: null\n"
        "superseded_by: null\n"
        "links:\n"
        "  - type: relates\n"
        "    target: wiki/pages/praxis.md\n"
        "---\n"
        "\n"
        "Alice is a software engineer who built Praxis.\n"
    )
    page_path = pages_dir / f"{slug}.md"
    page_path.write_text(content, encoding="utf-8")
    return page_path


def _make_config(tmp_path: Path, allowed_domains: frozenset | None = None) -> Config:
    """Create a mock Config pointing at tmp_path."""
    config = MagicMock(spec=Config)
    config.workspace_root = tmp_path
    if allowed_domains is None:
        allowed_domains = frozenset({"api.linear.app", "api.notion.com"})
    config.allowed_domains = allowed_domains
    return config


# ---------------------------------------------------------------------------
# J-01: export_notion
# ---------------------------------------------------------------------------


class TestExportNotion:

    def test_returns_dict_with_title_and_blocks(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_notion
        result = export_notion("alice", wiki_root=wiki_root)
        assert isinstance(result, dict)
        assert result["title"] == "Alice"
        assert isinstance(result["blocks"], list)
        assert len(result["blocks"]) > 0

    def test_first_block_is_heading_with_entity_name(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_notion
        result = export_notion("alice", wiki_root=wiki_root)
        first = result["blocks"][0]
        assert first["type"] == "heading_1"
        assert first["content"] == "Alice"

    def test_typed_links_rendered_as_callout_blocks(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_notion
        result = export_notion("alice", wiki_root=wiki_root)
        callouts = [b for b in result["blocks"] if b.get("type") == "callout"]
        assert len(callouts) >= 1
        assert "relates" in callouts[0]["content"]
        assert "wiki/pages/praxis.md" in callouts[0]["content"]

    def test_missing_page_raises_wiki_error(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
        from praxis.wiki import export_notion, WikiError
        with pytest.raises(WikiError, match="not found"):
            export_notion("nonexistent", wiki_root=wiki_root)

    def test_body_content_in_paragraph_blocks(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_notion
        result = export_notion("alice", wiki_root=wiki_root)
        paragraphs = [b for b in result["blocks"] if b.get("type") == "paragraph"]
        all_text = " ".join(p["content"] for p in paragraphs)
        assert "software engineer" in all_text or "Alice" in all_text


# ---------------------------------------------------------------------------
# J-01: export_linear
# ---------------------------------------------------------------------------


class TestExportLinear:

    def test_returns_markdown_string_with_heading(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_linear
        result = export_linear("alice", wiki_root=wiki_root)
        assert isinstance(result, str)
        assert "# Alice" in result

    def test_includes_valid_from_metadata(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_linear
        result = export_linear("alice", wiki_root=wiki_root)
        assert "valid_from" in result
        assert "2026-01-01" in result

    def test_includes_body_content(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_linear
        result = export_linear("alice", wiki_root=wiki_root)
        assert "software engineer" in result

    def test_includes_typed_links_section(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.wiki import export_linear
        result = export_linear("alice", wiki_root=wiki_root)
        assert "relates" in result
        assert "wiki/pages/praxis.md" in result

    def test_missing_page_raises_wiki_error(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
        from praxis.wiki import export_linear, WikiError
        with pytest.raises(WikiError, match="not found"):
            export_linear("nonexistent", wiki_root=wiki_root)


# ---------------------------------------------------------------------------
# J-02: sync_to_notion
# ---------------------------------------------------------------------------


class TestSyncToNotion:

    def test_stages_to_external_actions_jsonl(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_notion
        result = sync_to_notion("alice", "parent-abc", wiki_root=wiki_root, config=config)
        assert "Staged" in result
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        assert staging_file.exists()

    def test_staging_entry_has_correct_format(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_notion
        sync_to_notion("alice", "parent-abc", wiki_root=wiki_root, config=config)
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = json.loads(staging_file.read_text(encoding="utf-8").strip())
        assert entry["provider"] == "notion"
        assert entry["action"] == "create_page"
        assert entry["params"]["parent_id"] == "parent-abc"
        assert entry["params"]["wiki_page_slug"] == "alice"
        assert entry["status"] == "pending"
        assert "id" in entry
        assert "queued_at" in entry

    def test_never_calls_notion_api(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_notion
        with patch("praxis.integrations.wiki_sync.urlopen") as mock_urlopen:
            sync_to_notion("alice", "parent-abc", wiki_root=wiki_root, config=config)
            mock_urlopen.assert_not_called()

    def test_missing_page_returns_error_string_not_exception(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_notion
        result = sync_to_notion("nonexistent", "parent-abc", wiki_root=wiki_root, config=config)
        assert isinstance(result, str)
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# J-02: sync_to_linear
# ---------------------------------------------------------------------------


class TestSyncToLinear:

    def test_stages_to_external_actions_jsonl(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_linear
        result = sync_to_linear("alice", "team-xyz", wiki_root=wiki_root, config=config)
        assert "Staged" in result
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        assert staging_file.exists()

    def test_staging_entry_has_correct_format(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_linear
        sync_to_linear("alice", "team-xyz", wiki_root=wiki_root, config=config)
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = json.loads(staging_file.read_text(encoding="utf-8").strip())
        assert entry["provider"] == "linear"
        assert entry["action"] == "create_issue"
        assert entry["params"]["team_id"] == "team-xyz"
        assert entry["params"]["wiki_page_slug"] == "alice"
        assert entry["status"] == "pending"

    def test_never_calls_linear_api(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_linear
        with patch("praxis.integrations.wiki_sync.urlopen") as mock_urlopen:
            sync_to_linear("alice", "team-xyz", wiki_root=wiki_root, config=config)
            mock_urlopen.assert_not_called()

    def test_missing_page_returns_error_string_not_exception(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import sync_to_linear
        result = sync_to_linear("nonexistent", "team-xyz", wiki_root=wiki_root, config=config)
        assert isinstance(result, str)
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# J-02: link_linear_issue
# ---------------------------------------------------------------------------


class TestLinkLinearIssue:

    def test_adds_linear_issue_id_to_frontmatter(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.integrations.wiki_sync import link_linear_issue
        from praxis.wiki import _parse_frontmatter
        result = link_linear_issue("alice", "LIN-42", wiki_root=wiki_root)
        assert "LIN-42" in result
        content = (wiki_root / "pages" / "alice.md").read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(content)
        assert meta.get("linear_issue_id") == "LIN-42"

    def test_adds_relates_typed_link(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.integrations.wiki_sync import link_linear_issue
        from praxis.wiki import _parse_frontmatter
        link_linear_issue("alice", "LIN-42", wiki_root=wiki_root)
        content = (wiki_root / "pages" / "alice.md").read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(content)
        links = meta.get("links", [])
        linear_links = [
            lnk for lnk in links
            if isinstance(lnk, dict) and "linear.app" in lnk.get("target", "")
        ]
        assert len(linear_links) >= 1
        assert any(lnk.get("type") == "relates" for lnk in linear_links)

    def test_link_not_duplicated_on_second_call(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        from praxis.integrations.wiki_sync import link_linear_issue
        from praxis.wiki import _parse_frontmatter
        link_linear_issue("alice", "LIN-42", wiki_root=wiki_root)
        link_linear_issue("alice", "LIN-42", wiki_root=wiki_root)
        content = (wiki_root / "pages" / "alice.md").read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(content)
        links = meta.get("links", [])
        linear_links = [
            lnk for lnk in links
            if isinstance(lnk, dict) and "LIN-42" in lnk.get("target", "")
        ]
        assert len(linear_links) == 1

    def test_missing_page_returns_error_string(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
        from praxis.integrations.wiki_sync import link_linear_issue
        result = link_linear_issue("nonexistent", "LIN-42", wiki_root=wiki_root)
        assert isinstance(result, str)
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# J-04: pull_linear_updates
# ---------------------------------------------------------------------------


class TestPullLinearUpdates:

    def test_no_api_key_returns_error(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import pull_linear_updates
        with patch.dict("os.environ", {}, clear=True):
            result = pull_linear_updates(wiki_root=wiki_root, config=config)
        assert "PRAXIS_LINEAR_API_KEY not set" in result

    def test_domain_not_allowlisted_returns_error(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        # config with empty allowed_domains — linear.app not in it
        config = _make_config(tmp_path, allowed_domains=frozenset())
        from praxis.integrations.wiki_sync import pull_linear_updates
        with patch.dict("os.environ", {"PRAXIS_LINEAR_API_KEY": "test-key"}):
            result = pull_linear_updates(wiki_root=wiki_root, config=config)
        assert "PRAXIS_ALLOWED_DOMAINS" in result

    def test_no_linked_pages_returns_message(self, tmp_path):
        # Page exists but has no linear_issue_id
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)
        from praxis.integrations.wiki_sync import pull_linear_updates
        with patch.dict("os.environ", {"PRAXIS_LINEAR_API_KEY": "test-key"}):
            result = pull_linear_updates(wiki_root=wiki_root, config=config)
        assert "No wiki pages with linear_issue_id" in result

    def test_stages_update_when_linked_page_found(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)

        # First link the page to a Linear issue
        from praxis.integrations.wiki_sync import link_linear_issue, pull_linear_updates
        link_linear_issue("alice", "LIN-42", wiki_root=wiki_root)

        # Mock the Linear API response
        mock_response = {
            "data": {
                "issue": {
                    "id": "LIN-42",
                    "title": "Alice Entity",
                    "description": "A wiki fact",
                    "state": {"name": "In Progress"},
                    "priority": 2,
                    "updatedAt": "2026-05-28T00:00:00Z",
                    "comments": {
                        "nodes": [
                            {
                                "id": "c1",
                                "body": "New comment",
                                "createdAt": "2026-05-28T00:00:00Z",
                                "user": {"name": "Bob"},
                            }
                        ]
                    },
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.dict("os.environ", {"PRAXIS_LINEAR_API_KEY": "test-key"}):
            with patch("praxis.integrations.wiki_sync.urlopen", return_value=mock_resp):
                result = pull_linear_updates(wiki_root=wiki_root, config=config)

        assert "staged 1" in result
        wiki_updates_file = tmp_path / ".praxis" / "staging" / "wiki_updates.jsonl"
        assert wiki_updates_file.exists()
        entry = json.loads(wiki_updates_file.read_text(encoding="utf-8").strip())
        assert entry["page_slug"] == "alice"
        assert entry["linear_issue_id"] == "LIN-42"
        assert entry["current_state"] == "In Progress"
        assert entry["status"] == "pending"
        assert "id" in entry
        assert "queued_at" in entry

    def test_api_error_recorded_not_raised(self, tmp_path):
        wiki_root = tmp_path / "wiki"
        _make_wiki_page(wiki_root)
        config = _make_config(tmp_path)

        from praxis.integrations.wiki_sync import link_linear_issue, pull_linear_updates
        link_linear_issue("alice", "LIN-99", wiki_root=wiki_root)

        from urllib.error import URLError
        with patch.dict("os.environ", {"PRAXIS_LINEAR_API_KEY": "test-key"}):
            with patch("praxis.integrations.wiki_sync.urlopen", side_effect=URLError("timeout")):
                result = pull_linear_updates(wiki_root=wiki_root, config=config)

        # Should return a string (not raise), with error info and 0 staged
        assert isinstance(result, str)
        assert "staged 0" in result or "0 update" in result


# ---------------------------------------------------------------------------
# J-03: CLI modes
# ---------------------------------------------------------------------------


class TestWikiSyncCLI:

    def test_parse_mode_wiki_sync_notion(self):
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["praxis", "--wiki-sync-notion", "alice", "parent-abc"]) == "wiki_sync_notion"

    def test_parse_mode_wiki_sync_linear(self):
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["praxis", "--wiki-sync-linear", "alice", "team-xyz"]) == "wiki_sync_linear"

    def test_parse_mode_wiki_link_issue(self):
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["praxis", "--wiki-link-issue", "alice", "LIN-42"]) == "wiki_link_issue"

    def test_list_staged_shows_wiki_update_proposals(self, tmp_path):
        from praxis.__main__ import _run_list_staged
        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True)
        wiki_updates = staging / "wiki_updates.jsonl"
        entry = {
            "id": str(uuid.uuid4()),
            "page_slug": "alice",
            "linear_issue_id": "LIN-42",
            "current_state": "In Progress",
            "current_title": "Alice entity",
            "comment_count": 1,
            "latest_comments": [],
            "queued_at": "2026-05-28T10:00:00+00:00",
            "status": "pending",
        }
        wiki_updates.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        out = io.StringIO()
        with redirect_stdout(out):
            _run_list_staged(tmp_path)
        output = out.getvalue()
        # Should show alice or wiki somewhere in the output
        assert "alice" in output

    def test_list_staged_empty_wiki_updates_not_shown(self, tmp_path):
        """If wiki_updates.jsonl only has non-pending entries, section is not shown."""
        from praxis.__main__ import _run_list_staged
        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True)
        wiki_updates = staging / "wiki_updates.jsonl"
        entry = {
            "id": str(uuid.uuid4()),
            "page_slug": "alice",
            "linear_issue_id": "LIN-42",
            "current_state": "Done",
            "queued_at": "2026-05-28T10:00:00+00:00",
            "status": "approved",  # not pending
        }
        wiki_updates.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        out = io.StringIO()
        with redirect_stdout(out):
            _run_list_staged(tmp_path)
        output = out.getvalue()
        assert "No staged items" in output
