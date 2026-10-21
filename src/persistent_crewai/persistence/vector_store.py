"""
Persistent Vector Store and Deterministic Hash Embeddings for Persistent CrewAI.
Supports cosine similarity search, metadata filtering, and FTS5 hybrid retrieval.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.persistence.db import DatabaseManager, init_db


class EmbeddingProviderInterface(ABC):
    """Abstract base interface for embedding providers."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of text strings into float vectors."""
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string into a float vector."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimensionality."""
        pass


class DeterministicHashEmbeddingProvider(EmbeddingProviderInterface):
    """
    Zero-dependency, offline deterministic embedding provider using SHA256 token hashing.
    Produces unit L2-normalized float vectors for 100% reproducible execution.
    """

    def __init__(self, dimension: int = 64, seed_prefix: str = "crewai_emb_") -> None:
        if dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        self._dimension = dimension
        self._seed_prefix = seed_prefix

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_query(self, text: str) -> List[float]:
        vec = [0.0] * self._dimension
        if not text or not text.strip():
            return vec

        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            return vec

        for w in words:
            # Word-level feature hashing
            d = hashlib.sha256(f"{self._seed_prefix}{w}".encode("utf-8")).hexdigest()
            val = int(d, 16)
            idx = val % self._dimension
            sign = 1.0 if ((val >> 16) & 1) == 0 else -1.0
            vec[idx] += sign * 1.0

            # Subword 3-grams for morphological/character overlap
            if len(w) >= 3:
                for i in range(len(w) - 2):
                    ng = w[i : i + 3]
                    d_ng = hashlib.sha256(f"{self._seed_prefix}ng_{ng}".encode("utf-8")).hexdigest()
                    val_ng = int(d_ng, 16)
                    idx_ng = val_ng % self._dimension
                    sign_ng = 1.0 if ((val_ng >> 16) & 1) == 0 else -1.0
                    vec[idx_ng] += sign_ng * 0.4

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            return [round(x / norm, 6) for x in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_query(t) for t in texts]


@dataclass
class SearchResult:
    """Standardized semantic / hybrid search result."""
    document_id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    category: Optional[str] = None
    agent_role: Optional[str] = None


class SQLiteVectorStore:
    """
    Persistent vector store backed by SQLite relational tables and FTS5.
    Combines vector cosine similarity with full-text keyword retrieval.
    """

    def __init__(
        self,
        db_path: str | Path,
        embedder: Optional[EmbeddingProviderInterface] = None,
        project_id: Optional[str] = None,
    ) -> None:
        self.db_path = Path(db_path).resolve() if str(db_path) != ":memory:" else Path(":memory:")
        self.project_id = project_id or "default_project"
        self.embedder = embedder or DeterministicHashEmbeddingProvider(dimension=64)
        self._db_mgr = DatabaseManager(db_path=self.db_path)
        self._db_mgr.initialize_schema()
        self._ensure_project()

    def _ensure_project(self) -> None:
        """Ensure project row exists in projects table for referential integrity."""
        with self._db_mgr.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, name, description, root_path)
                VALUES (?, ?, 'Auto-initialized vector store project', '.')
                """,
                (self.project_id, self.project_id),
            )

    def add(
        self,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
        category: str = "general",
        agent_role: Optional[str] = None,
    ) -> List[str]:
        """Index documents into memory records and FTS5 table."""
        if not documents:
            return []

        doc_ids = ids if ids else [f"mem_{uuid.uuid4().hex[:12]}" for _ in documents]
        embeddings = self.embedder.embed_documents(documents)

        records = []
        for i, (doc_id, doc, emb) in enumerate(zip(doc_ids, documents, embeddings)):
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            doc_category = meta.get("category", category)
            doc_agent = (
                meta.get("agent_role")
                or meta.get("agent")
                or meta.get("role")
                or agent_role
            )
            records.append((
                doc_id,
                self.project_id,
                doc_agent,
                doc_category,
                doc,
                json.dumps(meta, ensure_ascii=False),
                json.dumps(emb),
            ))

        with self._db_mgr.connection() as conn:
            conn.executemany(
                """
                INSERT INTO memory_records (
                    record_id, project_id, agent_role, category, content, metadata_json, embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    agent_role = excluded.agent_role,
                    category = excluded.category,
                    content = excluded.content,
                    metadata_json = excluded.metadata_json,
                    embedding_json = excluded.embedding_json
                """,
                records,
            )

        return doc_ids

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
        category: Optional[str] = None,
        agent_role: Optional[str] = None,
        hybrid_search: bool = True,
        hybrid_alpha: float = 0.7,
    ) -> List[SearchResult]:
        """Search for relevant memories using cosine similarity and optional FTS5 hybrid fusion."""
        if not query or not query.strip() or top_k <= 0:
            return []

        q_vec = self.embedder.embed_query(query)
        q_norm = sum(x * x for x in q_vec)

        # FTS5 lexical retrieval
        fts_ranks: Dict[str, float] = {}
        if hybrid_search:
            words = re.findall(r"\w+", query)
            if words:
                fts_query = " OR ".join(f'"{w}"' for w in words)
                try:
                    with self._db_mgr.connection() as conn:
                        cur = conn.execute(
                            "SELECT record_id, rank FROM memory_fts WHERE memory_fts MATCH ?",
                            (fts_query,),
                        )
                        for row in cur.fetchall():
                            rec_id = row["record_id"]
                            r = float(row["rank"])
                            norm_bm25 = 1.0 / (1.0 - r) if r <= 0 else 0.5
                            fts_ranks[rec_id] = norm_bm25
                except Exception:
                    # Ignore FTS syntax or matching errors and fall back gracefully
                    pass

        # Query candidates from SQLite
        conditions = ["project_id = ?"]
        params: list[Any] = [self.project_id]

        if category:
            conditions.append("category = ?")
            params.append(category)
        if agent_role:
            conditions.append("agent_role = ?")
            params.append(agent_role)

        sql = f"SELECT record_id, agent_role, category, content, metadata_json, embedding_json FROM memory_records WHERE {' AND '.join(conditions)}"

        candidates = []
        with self._db_mgr.connection() as conn:
            cur = conn.execute(sql, params)
            candidates = cur.fetchall()

        if not candidates:
            return []

        results: List[SearchResult] = []
        for cand in candidates:
            rec_id = cand["record_id"]
            meta = json.loads(cand["metadata_json"] or "{}")

            # Apply metadata filtering
            if filter_metadata:
                matched = True
                for fk, fv in filter_metadata.items():
                    # Handle common alias keys
                    val = meta.get(fk)
                    if val is None and fk == "role":
                        val = meta.get("agent") or meta.get("agent_role") or cand["agent_role"]
                    elif val is None and fk in ("agent", "agent_role"):
                        val = meta.get("role") or cand["agent_role"]

                    if val != fv:
                        matched = False
                        break
                if not matched:
                    continue

            # Calculate cosine similarity
            emb_str = cand["embedding_json"]
            cos_sim = 0.0
            if emb_str and q_norm > 1e-9:
                try:
                    c_vec = json.loads(emb_str)
                    cos_sim = sum(q * c for q, c in zip(q_vec, c_vec))
                except Exception:
                    cos_sim = 0.0

            # Compute hybrid score
            bm25_score = fts_ranks.get(rec_id, 0.0)
            if hybrid_search and fts_ranks:
                score = (hybrid_alpha * max(0.0, cos_sim)) + ((1.0 - hybrid_alpha) * bm25_score)
            else:
                score = max(0.0, cos_sim)

            results.append(
                SearchResult(
                    document_id=rec_id,
                    content=cand["content"],
                    metadata=meta,
                    score=round(score, 6),
                    category=cand["category"],
                    agent_role=cand["agent_role"],
                )
            )

        # Sort descending by score
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def count(self, category: Optional[str] = None) -> int:
        """Count records stored in the vector store."""
        query = "SELECT count(*) as cnt FROM memory_records WHERE project_id = ?"
        params: list[Any] = [self.project_id]
        if category:
            query += " AND category = ?"
            params.append(category)

        with self._db_mgr.connection() as conn:
            cur = conn.execute(query, params)
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0

    def delete(self, ids: List[str]) -> bool:
        """Delete memories by record_id. Triggers cascade deletion to memory_fts."""
        if not ids:
            return False

        placeholders = ", ".join("?" for _ in ids)
        sql = f"DELETE FROM memory_records WHERE project_id = ? AND record_id IN ({placeholders})"
        params = [self.project_id, *ids]

        with self._db_mgr.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0

    def persist(self) -> None:
        """Explicit sync / commit."""
        pass
