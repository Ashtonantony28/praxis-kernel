"""Semantic memory store — ChromaDB + sentence-transformers all-MiniLM-L6-v2.

Optional dependency; gracefully returns [] / None when deps are not installed.
Enable by setting PRAXIS_SEMANTIC_MEMORY=true and installing:
    pip install praxis[semantic]

Architecture notes:
- Lazy imports: chromadb and sentence_transformers are never imported at
  module level so the rest of Praxis loads cleanly without these deps.
- Singleton per workspace_root via get_memory_store().
- ChromaDB persistent store lives in .praxis/memory/chroma/ (gitignored by
  the existing .praxis/memory/* rule in .gitignore).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_COLLECTION_NAME = "praxis_wiki"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Process-level singleton — one store per resolved workspace root.
_stores: dict[str, "SemanticMemoryStore"] = {}


def get_memory_store(workspace_root: "Path | str") -> "SemanticMemoryStore | None":
    """Return a SemanticMemoryStore if PRAXIS_SEMANTIC_MEMORY=true and deps are available.

    Returns None (silently) when:
      - PRAXIS_SEMANTIC_MEMORY is not set to 'true'
      - chromadb or sentence_transformers are not installed

    The returned store is a singleton per resolved workspace_root path.
    """
    if os.environ.get("PRAXIS_SEMANTIC_MEMORY", "").lower() != "true":
        return None

    # Fast-fail if optional deps are absent
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        return None

    ws = str(Path(workspace_root).resolve())
    if ws not in _stores:
        _stores[ws] = SemanticMemoryStore(Path(ws))
    return _stores[ws]


class SemanticMemoryStore:
    """Semantic memory using ChromaDB with sentence-transformer embeddings.

    All public methods silently return [] / None when the optional deps are
    not installed or when initialization fails — callers never need to guard.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = Path(workspace_root)
        self._chroma_path = self._workspace_root / ".praxis" / "memory" / "chroma"
        self._client: Any = None
        self._collection: Any = None
        self._model: Any = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> bool:
        """Lazily create/open the ChromaDB client and sentence-transformer model.

        Returns True when fully ready; False when deps are absent.
        """
        if self._client is not None:
            return True

        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return False

        self._chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_path))
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._model = SentenceTransformer(_EMBEDDING_MODEL)
        return True

    def _embed(self, text: str) -> list[float]:
        """Encode *text* into a dense embedding vector."""
        embedding = self._model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    @staticmethod
    def _clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Strip values that ChromaDB cannot store (None and non-primitive types)."""
        cleaned: dict[str, Any] = {}
        for k, v in meta.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                cleaned[k] = v
            else:
                cleaned[k] = str(v)
        return cleaned

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_wiki_page(
        self,
        slug: str,
        content: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> None:
        """Upsert a wiki page identified by *slug*.

        Sets ``metadata["active"] = False`` when ``superseded_on`` is set,
        otherwise ``True``.  The embedding is stored alongside the raw content
        so ChromaDB can be used as a document store as well.
        """
        if not self._ensure_initialized():
            return

        meta = dict(metadata or {})
        if meta.get("superseded_on"):
            meta["active"] = False
        else:
            meta["active"] = True

        cleaned = self._clean_metadata(meta)
        embedding = self._embed(content)

        self._collection.upsert(
            ids=[slug],
            embeddings=[embedding],
            documents=[content],
            metadatas=[cleaned],
        )

    def search(
        self,
        query: str,
        top_k: int = 6,
        include_superseded: bool = False,
    ) -> "list[dict[str, Any]]":
        """Semantic search over wiki pages.

        Args:
            query: Free-text query.
            top_k: Maximum number of results to return (default 6).
            include_superseded: When False (default), excludes pages where
                ``active == False`` (i.e. superseded pages).

        Returns:
            List of ``{slug, score, content_preview, metadata}`` dicts.
            *score* is cosine similarity in [0, 1] (higher = more similar).
            Returns ``[]`` silently when deps are missing, on any error, or
            when the collection is empty.
        """
        if not self._ensure_initialized():
            return []

        try:
            embedding = self._embed(query)

            where: "dict[str, Any] | None" = (
                None if include_superseded else {"active": True}
            )

            kwargs: dict[str, Any] = {
                "query_embeddings": [embedding],
                "n_results": top_k,
            }
            if where is not None:
                kwargs["where"] = where

            results = self._collection.query(**kwargs)

            if not results or not results.get("ids"):
                return []

            ids = results["ids"][0]
            distances = results.get("distances", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas_list = results.get("metadatas", [[]])[0]

            output: list[dict[str, Any]] = []
            for i, slug in enumerate(ids):
                distance = distances[i] if i < len(distances) else 1.0
                score = max(0.0, 1.0 - float(distance))  # cosine distance → similarity
                doc = documents[i] if i < len(documents) else ""
                meta = metadatas_list[i] if i < len(metadatas_list) else {}
                output.append(
                    {
                        "slug": slug,
                        "score": score,
                        "content_preview": doc[:200],
                        "metadata": meta,
                    }
                )
            return output
        except Exception:
            return []

    def delete_page(self, slug: str) -> None:
        """Remove the page identified by *slug* from the index.

        Silent no-op when deps are missing or the slug is not found.
        """
        if not self._ensure_initialized():
            return
        try:
            self._collection.delete(ids=[slug])
        except Exception:
            pass

    def rebuild_index(self, wiki_root: "Path | str") -> int:
        """Re-embed all Markdown files under *wiki_root*.

        Iterates ``wiki_root/**/*.md``, reads each file, and calls
        :meth:`embed_wiki_page` with the file stem as slug.

        Returns:
            Number of pages successfully indexed.
            Returns 0 silently when deps are missing.
        """
        if not self._ensure_initialized():
            return 0

        wiki_root = Path(wiki_root)
        count = 0
        for md_file in wiki_root.glob("**/*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                slug = md_file.stem
                self.embed_wiki_page(slug, content)
                count += 1
            except Exception:
                pass
        return count
