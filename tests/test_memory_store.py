"""Tests for praxis/memory_store.py — SemanticMemoryStore with mocked deps.

All external deps (chromadb, sentence_transformers) are mocked so these tests
run without any optional packages installed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

import praxis.memory_store as _ms


# ---------------------------------------------------------------------------
# Helpers to build fully-mocked chromadb + sentence_transformers modules
# ---------------------------------------------------------------------------

def _mock_collection(query_result: dict | None = None) -> MagicMock:
    col = MagicMock()
    if query_result is None:
        query_result = {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
    col.query.return_value = query_result
    return col


def _mock_chromadb(collection: MagicMock | None = None) -> MagicMock:
    if collection is None:
        collection = _mock_collection()
    mod = MagicMock()
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    mod.PersistentClient.return_value = client
    return mod


def _mock_st(embedding: list[float] | None = None) -> MagicMock:
    if embedding is None:
        embedding = [0.1, 0.2, 0.3]
    mod = MagicMock()
    model = MagicMock()
    import numpy as np  # numpy is available as a transitive dep
    model.encode.return_value = np.array(embedding)
    mod.SentenceTransformer.return_value = model
    return mod


def _make_store(tmp_path: Path) -> tuple["_ms.SemanticMemoryStore", MagicMock, MagicMock, MagicMock]:
    """Return (store, mock_chromadb, mock_st, mock_collection)."""
    col = _mock_collection()
    chroma_mod = _mock_chromadb(col)
    st_mod = _mock_st()
    store = _ms.SemanticMemoryStore(tmp_path)
    with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
        store._ensure_initialized()
    return store, chroma_mod, st_mod, col


# ---------------------------------------------------------------------------
# get_memory_store
# ---------------------------------------------------------------------------

class TestGetMemoryStore:
    def test_returns_none_when_env_not_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRAXIS_SEMANTIC_MEMORY", raising=False)
        result = _ms.get_memory_store(tmp_path)
        assert result is None

    def test_returns_none_when_env_is_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_SEMANTIC_MEMORY", "false")
        result = _ms.get_memory_store(tmp_path)
        assert result is None

    def test_returns_none_when_deps_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_SEMANTIC_MEMORY", "true")
        with patch.dict(sys.modules, {"chromadb": None, "sentence_transformers": None}):
            result = _ms.get_memory_store(tmp_path)
        assert result is None

    def test_returns_store_when_enabled_and_deps_available(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_SEMANTIC_MEMORY", "true")
        chroma_mod = _mock_chromadb()
        st_mod = _mock_st()
        ws_key = str(tmp_path.resolve())
        # Clean singleton cache
        _ms._stores.pop(ws_key, None)
        try:
            with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
                result = _ms.get_memory_store(tmp_path)
            assert result is not None
            assert isinstance(result, _ms.SemanticMemoryStore)
        finally:
            _ms._stores.pop(ws_key, None)

    def test_returns_same_singleton_for_same_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_SEMANTIC_MEMORY", "true")
        chroma_mod = _mock_chromadb()
        st_mod = _mock_st()
        ws_key = str(tmp_path.resolve())
        _ms._stores.pop(ws_key, None)
        try:
            with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
                store1 = _ms.get_memory_store(tmp_path)
                store2 = _ms.get_memory_store(tmp_path)
            assert store1 is store2
        finally:
            _ms._stores.pop(ws_key, None)


# ---------------------------------------------------------------------------
# _ensure_initialized
# ---------------------------------------------------------------------------

class TestEnsureInitialized:
    def test_returns_false_when_chromadb_missing(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        with patch.dict(sys.modules, {"chromadb": None, "sentence_transformers": MagicMock()}):
            result = store._ensure_initialized()
        assert result is False

    def test_returns_false_when_st_missing(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        chroma_mod = _mock_chromadb()
        with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": None}):
            result = store._ensure_initialized()
        assert result is False

    def test_returns_true_when_both_available(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        chroma_mod = _mock_chromadb()
        st_mod = _mock_st()
        with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
            result = store._ensure_initialized()
        assert result is True

    def test_creates_chroma_path(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        chroma_mod = _mock_chromadb()
        st_mod = _mock_st()
        with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
            store._ensure_initialized()
        assert (tmp_path / ".praxis" / "memory" / "chroma").exists()


# ---------------------------------------------------------------------------
# embed_wiki_page
# ---------------------------------------------------------------------------

class TestEmbedWikiPage:
    def test_calls_collection_upsert(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.embed_wiki_page("my-slug", "content here")
        col.upsert.assert_called_once()
        kwargs = col.upsert.call_args[1]
        assert kwargs["ids"] == ["my-slug"]
        assert kwargs["documents"] == ["content here"]

    def test_active_true_when_no_superseded_on(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.embed_wiki_page("slug", "content", metadata={"author": "Alice"})
        meta = col.upsert.call_args[1]["metadatas"][0]
        assert meta["active"] is True

    def test_active_false_when_superseded_on_set(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.embed_wiki_page("slug", "content", metadata={"superseded_on": "2024-01-01"})
        meta = col.upsert.call_args[1]["metadatas"][0]
        assert meta["active"] is False

    def test_none_values_stripped_from_metadata(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.embed_wiki_page("slug", "content", metadata={"key": None, "keep": "val"})
        meta = col.upsert.call_args[1]["metadatas"][0]
        assert "key" not in meta
        assert meta["keep"] == "val"

    def test_non_primitive_metadata_converted_to_str(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.embed_wiki_page("slug", "content", metadata={"list_val": [1, 2, 3]})
        meta = col.upsert.call_args[1]["metadatas"][0]
        assert isinstance(meta["list_val"], str)

    def test_no_op_when_deps_missing(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        # Don't initialize — client is None
        # Should not raise
        store.embed_wiki_page("slug", "content")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_empty_list_when_not_initialized(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        result = store.search("hello")
        assert result == []

    def test_returns_empty_list_on_empty_results(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        col.query.return_value = {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        result = store.search("query")
        assert result == []

    def test_returns_formatted_results(self, tmp_path):
        col = _mock_collection({
            "ids": [["slug-1", "slug-2"]],
            "distances": [[0.1, 0.3]],
            "documents": [["content one", "content two"]],
            "metadatas": [[{"active": True}, {"active": True}]],
        })
        chroma_mod = _mock_chromadb(col)
        st_mod = _mock_st()
        store = _ms.SemanticMemoryStore(tmp_path)
        with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
            store._ensure_initialized()
        result = store.search("test query")
        assert len(result) == 2
        assert result[0]["slug"] == "slug-1"
        assert result[0]["score"] == pytest.approx(0.9)  # 1 - 0.1
        assert result[0]["content_preview"] == "content one"

    def test_content_preview_truncated_to_200(self, tmp_path):
        long_content = "x" * 500
        col = _mock_collection({
            "ids": [["slug"]],
            "distances": [[0.0]],
            "documents": [[long_content]],
            "metadatas": [[{"active": True}]],
        })
        chroma_mod = _mock_chromadb(col)
        st_mod = _mock_st()
        store = _ms.SemanticMemoryStore(tmp_path)
        with patch.dict(sys.modules, {"chromadb": chroma_mod, "sentence_transformers": st_mod}):
            store._ensure_initialized()
        result = store.search("query")
        assert len(result[0]["content_preview"]) == 200

    def test_passes_where_clause_when_not_include_superseded(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.search("q", include_superseded=False)
        call_kwargs = col.query.call_args[1]
        assert call_kwargs.get("where") == {"active": True}

    def test_no_where_clause_when_include_superseded(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.search("q", include_superseded=True)
        call_kwargs = col.query.call_args[1]
        assert "where" not in call_kwargs

    def test_top_k_respected(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.search("q", top_k=3)
        call_kwargs = col.query.call_args[1]
        assert call_kwargs["n_results"] == 3

    def test_returns_empty_list_on_exception(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        col.query.side_effect = RuntimeError("db error")
        result = store.search("query")
        assert result == []


# ---------------------------------------------------------------------------
# delete_page
# ---------------------------------------------------------------------------

class TestDeletePage:
    def test_calls_collection_delete(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        store.delete_page("my-slug")
        col.delete.assert_called_once_with(ids=["my-slug"])

    def test_no_op_when_not_initialized(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        # Should not raise
        store.delete_page("slug")

    def test_silences_exception(self, tmp_path):
        store, _, _, col = _make_store(tmp_path)
        col.delete.side_effect = Exception("not found")
        # Should not raise
        store.delete_page("slug")


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------

class TestRebuildIndex:
    def test_returns_zero_when_not_initialized(self, tmp_path):
        store = _ms.SemanticMemoryStore(tmp_path)
        result = store.rebuild_index(tmp_path)
        assert result == 0

    def test_counts_indexed_pages(self, tmp_path):
        # Create some .md files
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "page1.md").write_text("content 1")
        (wiki / "page2.md").write_text("content 2")
        (wiki / "page3.md").write_text("content 3")

        store, _, _, col = _make_store(tmp_path)
        count = store.rebuild_index(wiki)
        assert count == 3
        assert col.upsert.call_count == 3

    def test_uses_stem_as_slug(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "my-page.md").write_text("hello")

        store, _, _, col = _make_store(tmp_path)
        store.rebuild_index(wiki)

        call_kwargs = col.upsert.call_args[1]
        assert call_kwargs["ids"] == ["my-page"]

    def test_skips_failed_files(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "good.md").write_text("content")
        bad = wiki / "bad.md"
        bad.write_text("x")
        bad.chmod(0o000)  # make unreadable

        store, _, _, col = _make_store(tmp_path)
        try:
            count = store.rebuild_index(wiki)
            # Should not raise; bad file is skipped
            assert count >= 0
        finally:
            bad.chmod(0o644)  # restore


# ---------------------------------------------------------------------------
# _clean_metadata static method
# ---------------------------------------------------------------------------

class TestCleanMetadata:
    def test_strips_none_values(self):
        result = _ms.SemanticMemoryStore._clean_metadata({"k": None, "keep": "v"})
        assert "k" not in result
        assert result["keep"] == "v"

    def test_passes_str_int_float_bool(self):
        meta = {"s": "x", "i": 1, "f": 1.5, "b": True}
        result = _ms.SemanticMemoryStore._clean_metadata(meta)
        assert result == meta

    def test_converts_list_to_str(self):
        result = _ms.SemanticMemoryStore._clean_metadata({"lst": [1, 2]})
        assert result["lst"] == "[1, 2]"
