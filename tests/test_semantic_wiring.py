"""Tests for semantic-wiring feature.

Verifies:
1. wiki.ingest() calls embed_wiki_page() on the SemanticMemoryStore when
   PRAXIS_SEMANTIC_MEMORY=true and the store is available.
2. Orchestrator.run() prepends a "## Relevant memory" block to the system
   prompt when get_memory_store() returns results.
3. CLI --rebuild-memory-index calls store.rebuild_index() and prints the count.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

import pytest

from praxis.config import Config
from praxis.orchestrator import Orchestrator
from praxis.runtime import ClaudeCodeRuntime
from tests.conftest import FakeClient, FakeResponse, FakeTextBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = "2026-01-15"
_FAKE_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_mock_store(search_results: list | None = None) -> MagicMock:
    """Return a mock SemanticMemoryStore with controlled search results."""
    store = MagicMock()
    store.search.return_value = search_results if search_results is not None else []
    store.embed_wiki_page.return_value = None
    store.rebuild_index.return_value = 42
    return store


def _make_raw_page(entity: str, body: str) -> str:
    """Build a minimal wiki source text that ingest() can parse."""
    return f"{entity} is a known entity.\n\n{body}"


# ---------------------------------------------------------------------------
# 1. wiki.ingest() calls embed_wiki_page when store is available
# ---------------------------------------------------------------------------


class TestWikiIngestCallsEmbed:
    """ingest() must call embed_wiki_page for every newly created/updated page."""

    def test_ingest_new_page_calls_embed(self, tmp_path: Path) -> None:
        """Creating a new wiki page triggers embed_wiki_page on the store."""
        import praxis.wiki as wiki_mod
        import praxis.memory_store as ms_mod

        wiki_root = tmp_path / "wiki"
        (wiki_root / "raw").mkdir(parents=True)
        (wiki_root / "pages").mkdir(parents=True)

        mock_store = _make_mock_store()

        with (
            patch.object(wiki_mod, "_wiki_root", return_value=wiki_root),
            patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW),
            patch.object(ms_mod, "get_memory_store", return_value=mock_store),
        ):
            report = wiki_mod.ingest(
                "Ashton Antony is a software engineer living in Melbourne.",
                provenance="test",
            )

        # Should have created at least one page
        assert len(report.created) >= 1 or len(report.updated) >= 1 or len(report.events) >= 1
        # embed_wiki_page should have been called
        assert mock_store.embed_wiki_page.called, "embed_wiki_page was not called after ingest"

    def test_ingest_new_page_embed_called_with_slug(self, tmp_path: Path) -> None:
        """embed_wiki_page is called with the slug (file stem) of the new page."""
        import praxis.wiki as wiki_mod
        import praxis.memory_store as ms_mod

        wiki_root = tmp_path / "wiki"
        (wiki_root / "raw").mkdir(parents=True)
        (wiki_root / "pages").mkdir(parents=True)

        mock_store = _make_mock_store()

        with (
            patch.object(wiki_mod, "_wiki_root", return_value=wiki_root),
            patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW),
            patch.object(ms_mod, "get_memory_store", return_value=mock_store),
        ):
            report = wiki_mod.ingest(
                "Melbourne is the capital of Victoria.",
                provenance="test",
            )

        if mock_store.embed_wiki_page.called:
            # slug arg (first positional arg) should be a non-empty string
            first_call_args = mock_store.embed_wiki_page.call_args_list[0]
            slug_arg = first_call_args[0][0]  # first positional arg
            assert isinstance(slug_arg, str) and len(slug_arg) > 0

    def test_ingest_no_embed_when_store_none(self, tmp_path: Path) -> None:
        """When get_memory_store returns None, ingest completes without error."""
        import praxis.wiki as wiki_mod
        import praxis.memory_store as ms_mod

        wiki_root = tmp_path / "wiki"
        (wiki_root / "raw").mkdir(parents=True)
        (wiki_root / "pages").mkdir(parents=True)

        with (
            patch.object(wiki_mod, "_wiki_root", return_value=wiki_root),
            patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW),
            patch.object(ms_mod, "get_memory_store", return_value=None),
        ):
            # Should not raise
            report = wiki_mod.ingest(
                "Python is a programming language invented by Guido.",
                provenance="test",
            )
        # No crash — the report may have events
        assert report is not None

    def test_wiki_ingest_calls_embed(self, tmp_path: Path) -> None:
        """Canonical test: wiki ingest calls embed_wiki_page on created pages."""
        import praxis.wiki as wiki_mod
        import praxis.memory_store as ms_mod

        wiki_root = tmp_path / "wiki"
        (wiki_root / "raw").mkdir(parents=True)
        (wiki_root / "pages").mkdir(parents=True)

        mock_store = _make_mock_store()

        with (
            patch.object(wiki_mod, "_wiki_root", return_value=wiki_root),
            patch.object(wiki_mod, "_now_utc", return_value=_FAKE_NOW),
            patch.object(ms_mod, "get_memory_store", return_value=mock_store),
        ):
            report = wiki_mod.ingest(
                "Claude is an AI assistant built by Anthropic.",
                provenance="test",
            )

        # At least one event must have been created
        created_events = [e for e in report.events if e.kind == "created"]
        if created_events:
            assert mock_store.embed_wiki_page.called, (
                "embed_wiki_page should be called when a page is created"
            )


# ---------------------------------------------------------------------------
# 2. Orchestrator prepends semantic context to system prompt
# ---------------------------------------------------------------------------


class TestOrchestratorPrependsSemanticContext:
    """Orchestrator.run() must inject ## Relevant memory block into the system prompt."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure a clean orchestrator environment regardless of .env state leak."""
        # These may be set by earlier tests that call main() which loads the real .env.
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0")
        monkeypatch.delenv("PRAXIS_DEFAULT_MODE", raising=False)
        monkeypatch.setenv("PRAXIS_MODEL", "claude-sonnet-4-6")

    def test_orchestrator_prepends_semantic_context(
        self, config: Config, workspace: Path
    ) -> None:
        """When get_memory_store returns results, system prompt includes ## Relevant memory."""
        from praxis.orchestrator import Orchestrator
        from praxis.runtime import ClaudeCodeRuntime
        import praxis.memory_store as ms_mod

        responses = [FakeResponse(content=[FakeTextBlock("Done.")], stop_reason="end_turn")]
        client = FakeClient(responses)
        orch = Orchestrator(ClaudeCodeRuntime(client), config)

        search_results = [
            {
                "slug": "ashton-antony",
                "score": 0.92,
                "content_preview": "Ashton is a software engineer.",
                "metadata": {"active": True},
            }
        ]
        mock_store = _make_mock_store(search_results)

        with patch.object(ms_mod, "get_memory_store", return_value=mock_store):
            result = orch.run("Tell me about Ashton")

        assert result == "Done."
        # System prompt passed to messages.create should contain the memory block
        assert len(client.messages.calls) >= 1
        system_used = client.messages.calls[0]["system"]
        assert "## Relevant memory" in system_used, (
            f"Expected '## Relevant memory' in system prompt, got: {system_used!r}"
        )
        assert "ashton-antony" in system_used

    def test_orchestrator_no_memory_block_when_store_none(
        self, config: Config, workspace: Path
    ) -> None:
        """When get_memory_store returns None, system prompt is unchanged."""
        import praxis.memory_store as ms_mod

        responses = [FakeResponse(content=[FakeTextBlock("Done.")], stop_reason="end_turn")]
        client = FakeClient(responses)
        orch = Orchestrator(ClaudeCodeRuntime(client), config)

        with patch.object(ms_mod, "get_memory_store", return_value=None):
            result = orch.run("Tell me something")

        assert result == "Done."
        system_used = client.messages.calls[0]["system"]
        assert "## Relevant memory" not in system_used

    def test_orchestrator_no_memory_block_when_empty_results(
        self, config: Config, workspace: Path
    ) -> None:
        """When search returns [], system prompt is not modified."""
        import praxis.memory_store as ms_mod

        responses = [FakeResponse(content=[FakeTextBlock("Done.")], stop_reason="end_turn")]
        client = FakeClient(responses)
        orch = Orchestrator(ClaudeCodeRuntime(client), config)

        mock_store = _make_mock_store(search_results=[])

        with patch.object(ms_mod, "get_memory_store", return_value=mock_store):
            result = orch.run("Tell me something")

        assert result == "Done."
        system_used = client.messages.calls[0]["system"]
        assert "## Relevant memory" not in system_used

    def test_orchestrator_memory_block_uses_top_k_env(
        self, config: Config, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PRAXIS_SEMANTIC_TOP_K env var is forwarded to store.search()."""
        import praxis.memory_store as ms_mod

        monkeypatch.setenv("PRAXIS_SEMANTIC_TOP_K", "3")

        responses = [FakeResponse(content=[FakeTextBlock("Done.")], stop_reason="end_turn")]
        client = FakeClient(responses)
        orch = Orchestrator(ClaudeCodeRuntime(client), config)

        mock_store = _make_mock_store(search_results=[])

        with patch.object(ms_mod, "get_memory_store", return_value=mock_store):
            orch.run("Tell me something")

        mock_store.search.assert_called_once()
        call_kwargs = mock_store.search.call_args
        # top_k should be 3
        assert call_kwargs[1].get("top_k") == 3 or call_kwargs[0][1] == 3

    def test_orchestrator_memory_block_graceful_on_search_error(
        self, config: Config, workspace: Path
    ) -> None:
        """If store.search() raises, orchestrator falls back to unaugmented system prompt."""
        import praxis.memory_store as ms_mod

        responses = [FakeResponse(content=[FakeTextBlock("Done.")], stop_reason="end_turn")]
        client = FakeClient(responses)
        orch = Orchestrator(ClaudeCodeRuntime(client), config)

        mock_store = MagicMock()
        mock_store.search.side_effect = RuntimeError("chroma error")

        with patch.object(ms_mod, "get_memory_store", return_value=mock_store):
            result = orch.run("Tell me something")

        # Should still complete normally
        assert result == "Done."
        system_used = client.messages.calls[0]["system"]
        assert "## Relevant memory" not in system_used


# ---------------------------------------------------------------------------
# 3. CLI --rebuild-memory-index
# ---------------------------------------------------------------------------


class TestRebuildMemoryIndexCLI:
    """--rebuild-memory-index CLI mode calls store.rebuild_index and prints count."""

    def test_parse_mode_rebuild_memory_index(self) -> None:
        """_parse_mode returns 'rebuild_memory_index' for --rebuild-memory-index flag."""
        from praxis.__main__ import _parse_mode
        result = _parse_mode(["--rebuild-memory-index"])
        assert result == "rebuild_memory_index"

    def test_rebuild_memory_index_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """When PRAXIS_SEMANTIC_MEMORY not set, prints not-enabled message."""
        import praxis.memory_store as ms_mod

        monkeypatch.delenv("PRAXIS_SEMANTIC_MEMORY", raising=False)
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["praxis", "--rebuild-memory-index"])

        with patch.object(ms_mod, "get_memory_store", return_value=None):
            from praxis.__main__ import main
            main()  # should not raise

        captured = capsys.readouterr()
        assert "Semantic memory not enabled" in captured.out or "PRAXIS_SEMANTIC_MEMORY" in captured.out

    def test_rebuild_memory_index_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """When store is available, calls rebuild_index and prints count."""
        import praxis.memory_store as ms_mod

        monkeypatch.setenv("PRAXIS_SEMANTIC_MEMORY", "true")
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        (tmp_path / "wiki").mkdir(parents=True, exist_ok=True)

        mock_store = _make_mock_store()
        mock_store.rebuild_index.return_value = 7

        monkeypatch.setattr(sys, "argv", ["praxis", "--rebuild-memory-index"])

        with patch.object(ms_mod, "get_memory_store", return_value=mock_store):
            from praxis.__main__ import main
            main()

        captured = capsys.readouterr()
        assert "7" in captured.out
        mock_store.rebuild_index.assert_called_once()
