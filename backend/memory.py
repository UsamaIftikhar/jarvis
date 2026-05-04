"""ChromaDB-backed long-term memory for JARVIS (Phase 5).

Optional dependency: install with ``uv sync --extra memory``.

If ChromaDB / sentence-transformers are missing, ``JarvisMemory`` degrades
gracefully (empty recall, no-op store).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.memory")


def _default_chroma_dir() -> str:
    return str(Path(__file__).resolve().parent / "data" / "chroma")


class JarvisMemory:
    """Persistent semantic memory using Chroma + sentence-transformers."""

    def __init__(self) -> None:
        self._collection: Any = None
        self._disabled_reason: str | None = None

        try:
            import chromadb  # noqa: PLC0415
            from chromadb.utils import embedding_functions  # noqa: PLC0415
        except ImportError:
            self._disabled_reason = "chromadb not installed (run: uv sync --extra memory)"
            logger.warning("%s — memory disabled", self._disabled_reason)
            return

        persist = os.environ.get("JARVIS_CHROMA_PATH", _default_chroma_dir())
        Path(persist).mkdir(parents=True, exist_ok=True)

        model = os.environ.get(
            "JARVIS_EMBED_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model,
        )
        client = chromadb.PersistentClient(path=persist)
        self._collection = client.get_or_create_collection(
            name="jarvis_memory",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB memory ready at %s", persist)

    @property
    def enabled(self) -> bool:
        return self._collection is not None

    async def warmup(self) -> None:
        """Pre-load the sentence-transformer and touch Chroma so the first user
        utterance is not blocked by a large one-time model download."""
        if not self._collection:
            return
        logger.info("Memory warmup: loading embedding model (first run may download ~90MB)…")
        await self.recall("__jarvis_warmup__", k=1)
        logger.info("Memory warmup complete.")

    async def recall(self, query_text: str, *, k: int = 4) -> str:
        """Return a short block of text to inject into the system prompt."""
        if not self._collection or not query_text.strip():
            return ""
        q = query_text.strip()

        def _run() -> str:
            assert self._collection is not None
            res = self._collection.query(query_texts=[q], n_results=k)
            docs = (res.get("documents") or [[]])[0]
            lines: list[str] = []
            for i, doc in enumerate(docs):
                if not doc:
                    continue
                lines.append(f"- {doc.strip()}")
            if not lines:
                return ""
            return "Relevant prior context:\n" + "\n".join(lines)

        try:
            return await asyncio.to_thread(_run)
        except Exception:
            logger.exception("memory recall failed")
            return ""

    async def remember(self, text: str, *, kind: str = "turn") -> None:
        """Store a new memory document (e.g. one turn summary)."""
        if not self._collection or not text.strip():
            return

        doc_id = f"{kind}-{uuid.uuid4().hex}"

        def _run() -> None:
            assert self._collection is not None
            self._collection.add(
                ids=[doc_id],
                documents=[text.strip()],
                metadatas=[{"kind": kind}],
            )

        try:
            await asyncio.to_thread(_run)
        except Exception:
            logger.exception("memory store failed")


_memory_singleton: JarvisMemory | None = None


def get_memory() -> JarvisMemory:
    """Lazily construct a single ``JarvisMemory`` per process."""
    global _memory_singleton
    if _memory_singleton is None:
        _memory_singleton = JarvisMemory()
    return _memory_singleton


def describe_memory_status() -> str:
    """Human-readable status for /health (no secrets)."""
    m = get_memory()
    if m.enabled:
        return "enabled"
    return f"disabled ({m._disabled_reason or 'unknown'})"
