# Technical Analysis & Implementation Blueprint: VectorStore, Embeddings & State Export/Import

**Explorer**: Explorer M1-3  
**Milestone**: Milestone 1 (Foundation & State/Memory Persistence)  
**Date**: 2026-10-13  
**Target Modules**:
- `src/persistent_crewai/persistence/vector_store.py`
- `src/persistent_crewai/persistence/export_import.py` (and re-exported in `session.py` / `__init__.py`)
- `tests/test_persistence_vector.py`
- `tests/test_state_export_import.py`

---

## 1. Executive Summary

Milestone 1 requires robust long-term memory, vector similarity search, and full state backup/restoration capabilities for an 8-agent persistent CrewAI framework. To fulfill Acceptance Criteria with zero external network or API key dependencies:
1. **Embedding Layer**: Defines `EmbeddingProviderInterface` and provides `DeterministicHashEmbeddingProvider`, computing fixed-dimensional ($D = 64$ or $128$), $L_2$-normalized vector representations via SHA256 token and character $n$-gram hashing.
2. **Persistent Vector Store**: Implements `SQLiteVectorStore` backed by `memory_records` and `memory_fts` (SQLite FTS5 virtual table). Features cosine similarity search, flexible metadata filtering via `json_extract` and Python dictionaries, and hybrid Reciprocal Rank Fusion / combined ranking.
3. **State Export & Import Engine**: Exports the complete relational state of all 8 tables (`projects`, `decisions`, `workflow_runs`, `task_history`, `milestones`, `agent_status`, `execution_traces`, `memory_records`) plus `schema_migrations` into portable JSON. Imports back atomically with schema version validation (`1.0.0`), conflict protection, and foreign-key cascade integrity.

---

## 2. Embedding Provider Architecture

### 2.1 Interface Definition (`EmbeddingProviderInterface`)

The interface conforms to standard abstract protocols, enabling drop-in replacement with real embedding providers (e.g., OpenAI `text-embedding-3-small`, HuggingFace, or Ollama) when available.

```python
from abc import ABC, abstractmethod
from typing import List

class EmbeddingProviderInterface(ABC):
    """Abstract base interface for embedding providers."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of text strings into float vectors.
        
        Args:
            texts: List of strings to embed.
            
        Returns:
            List of float vectors of fixed dimension.
        """
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string into a float vector.
        
        Args:
            text: Query string.
            
        Returns:
            Float vector of fixed dimension.
        """
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimensionality (e.g., 64, 128, 1536)."""
        pass
```

### 2.2 `DeterministicHashEmbeddingProvider` Implementation Details

#### Algorithmic Design
To enable 100% offline testing without external model weights or internet access, `DeterministicHashEmbeddingProvider` leverages the feature hashing trick (Murmur/SHA256 hash kernels) over word tokens and character 3-grams:
1. **Normalization**: Input text is lowercased and stripped of leading/trailing whitespace.
2. **Tokenization**: Regex word extraction `re.findall(r'\b\w+\b', normalized_text)`.
3. **Feature Hashing**:
   - For each word $w$:
     - Computes $h = \text{int}(\text{SHA256}(seed\_prefix + w), 16)$.
     - Maps feature index: $idx = h \pmod D$.
     - Generates sign bit: $s = +1.0$ if $(h \gg 16) \pmod 2 == 0$ else $-1.0$.
     - Accumulates vector: $v[idx] += s \times 1.0$.
   - For subword character 3-grams ($n=3$) of words with $\text{len}(w) \ge 3$:
     - Computes subword hash: $h_{ng} = \text{int}(\text{SHA256}(seed\_prefix + \text{"ng_"} + w[i:i+3]), 16)$.
     - Maps index: $idx_{ng} = h_{ng} \pmod D$.
     - Generates sign bit: $s_{ng} = +1.0$ if $(h_{ng} \gg 16) \pmod 2 == 0$ else $-1.0$.
     - Accumulates vector with subword dampening: $v[idx_{ng}] += s_{ng} \times 0.4$.
4. **$L_2$ Normalization**:
   - Calculates Euclidean norm: $\|v\|_2 = \sqrt{\sum_{i=0}^{D-1} v_i^2}$.
   - If $\|v\|_2 > 10^{-9}$, each element is normalized: $v_i \leftarrow \frac{v_i}{\|v\|_2}$.
   - If $\|v\|_2 \le 10^{-9}$ (empty text or punctuation only), returns $[0.0] \times D$ without division-by-zero error.
5. **Properties**:
   - **Unit Norm**: Guaranteed $\|v\|_2 \approx 1.0$ for non-empty text, allowing cosine similarity to reduce to a fast dot product: $\cos(u, v) = \sum u_i v_i$.
   - **Deterministic & Reversible in Testing**: Identical text on any platform (Windows, Linux, macOS) produces bit-identical float vectors.
   - **High Discrimination**: High cosine similarity ($\sim 0.60 - 0.90$) for topical/token overlap; near-zero ($\sim 0.00 - 0.10$) for unrelated topics.

```python
import hashlib
import math
import re
from typing import List

class DeterministicHashEmbeddingProvider(EmbeddingProviderInterface):
    """Zero-dependency, offline deterministic embedding provider using SHA256 token hashing."""

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
        if not text:
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
```

---

## 3. Persistent Vector Store Architecture (`SQLiteVectorStore`)

### 3.1 Data Structures

```python
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

@dataclass
class SearchResult:
    """Standardized semantic / hybrid search result."""
    document_id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0  # Cosine similarity [0.0, 1.0] or hybrid fused score
    category: Optional[str] = None
    agent_role: Optional[str] = None
```

### 3.2 Underlying Database Tables & FTS5 Synchronization

The store persists memory records to `memory_records` and synchronizes with `memory_fts`:
```sql
CREATE TABLE IF NOT EXISTS memory_records (
    record_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64),
    category VARCHAR(64) DEFAULT 'general',
    content TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    embedding_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_memory_insert AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_delete AFTER DELETE ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_update AFTER UPDATE ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;
```

### 3.3 Core Operations & Algorithmic Logic

#### `add` Operation
```python
def add(
    self,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
    ids: Optional[List[str]] = None,
    category: str = "general",
    agent_role: Optional[str] = None
) -> List[str]:
```
- Validates documents list; returns `[]` if empty.
- Automatically generates IDs (`f"mem_{uuid.uuid4().hex[:12]}"`) if `ids` is None.
- Computes embedding vectors in batch via `self.embedding_provider.embed_documents(documents)`.
- Inserts records atomically:
  ```python
  records = []
  for doc_id, doc, meta, emb in zip(doc_ids, documents, metadatas, embeddings):
      records.append((
          doc_id,
          self.project_id,
          agent_role or meta.get("agent_role"),
          category or meta.get("category", "general"),
          doc,
          json.dumps(meta, ensure_ascii=False),
          json.dumps(emb)
      ))
  self.conn.executemany("""
      INSERT INTO memory_records 
      (record_id, project_id, agent_role, category, content, metadata_json, embedding_json)
      VALUES (?, ?, ?, ?, ?, ?, ?)
  """, records)
  self.conn.commit()
  ```
- Because of `trg_memory_insert`, `memory_fts` is populated in the same transaction.

#### `search` Operation with Cosine & Hybrid FTS5
```python
def search(
    self,
    query: str,
    top_k: int = 5,
    filter_metadata: Optional[Dict[str, Any]] = None,
    category: Optional[str] = None,
    agent_role: Optional[str] = None,
    hybrid_search: bool = True,
    hybrid_alpha: float = 0.7
) -> List[SearchResult]:
```
- **Edge cases guarded**:
  - Empty or whitespace query: returns `[]`.
  - `top_k <= 0`: returns `[]`.
  - Database contains 0 records for `project_id`: returns `[]`.
- **Vector Cosine Calculation**:
  - Compute query vector $q = \text{embed\_query}(query)$.
  - Filter candidates in SQL by `project_id`, `category` (if specified), `agent_role` (if specified).
  - For each candidate:
    - If `filter_metadata` provided: parse `metadata_json` and verify candidate contains all key-values.
    - Parse `embedding_json`: $c = \text{json.loads}(embedding\_json)$.
    - Compute dot product: $\text{cos\_sim} = \sum_{i=0}^{D-1} q_i \cdot c_i$.
- **FTS5 Full-Text Retrieval & Query Sanitization**:
  - To prevent SQLite `fts5: syntax error` on special characters like `*`, `(`, `"`, `AND`, `OR`:
    - Extract words via regex `re.findall(r'\w+', query)`.
    - Format query by enclosing each token in double quotes: `' OR '.join(f'"{w}"' for w in words)`.
  - Execute FTS5 query:
    ```sql
    SELECT record_id, rank FROM memory_fts WHERE memory_fts MATCH ?
    ```
  - Map FTS5 BM25 rank ($r \le 0$) into normalized lexical score:
    $\text{norm\_bm25} = \frac{1.0}{1.0 - r} \in (0.0, 1.0]$.
- **Hybrid Fusion**:
  - For candidate document $d$:
    - $\text{score} = \alpha \cdot \max(0.0, \text{cos\_sim}) + (1.0 - \alpha) \cdot \text{norm\_bm25}$.
    - If document was not matched in FTS5: $\text{norm\_bm25} = 0.0$.
  - Rank candidates descending by `score` and slice `[:top_k]`.

#### `delete`, `count`, and `persist`
- `delete(ids: List[str]) -> bool`: Deletes rows from `memory_records`; trigger cascades deletion to `memory_fts`. Returns `True` if any row deleted.
- `count(category: Optional[str] = None) -> int`: Fast `SELECT COUNT(*) FROM memory_records WHERE project_id = ?`.
- `persist() -> None`: Commits active transaction.

---

## 4. State Export and Import Architecture

### 4.1 Schema Versioning & Portable JSON Specification

The export file format is portable, human-readable JSON supporting schema version `1.0.0`:

```json
{
  "schema_version": "1.0.0",
  "exported_at": "2026-10-13T20:15:00.000000+00:00",
  "project_id": "crewai_core_01",
  "projects": [
    {
      "project_id": "crewai_core_01",
      "name": "Persistent CrewAI Team",
      "description": "8-agent persistent framework",
      "root_path": "C:\\Users\\User\\.gemini\\antigravity\\scratch\\persistent_crewai_team",
      "integrity_mode": "development",
      "status": "active",
      "created_at": "2026-10-13T20:00:00Z",
      "updated_at": "2026-10-13T20:00:00Z",
      "metadata": {}
    }
  ],
  "decisions": [
    {
      "decision_id": "dec_001",
      "project_id": "crewai_core_01",
      "agent_role": "Architect",
      "title": "Use SQLite with WAL mode for persistence",
      "rationale": "Single-file, zero-dependency, transactional reliability",
      "implications": "Requires local file access; concurrency handled via WAL and timeout",
      "status": "accepted",
      "tags": ["database", "storage"],
      "superseded_by": null,
      "created_at": "2026-10-13T20:05:00Z",
      "updated_at": "2026-10-13T20:05:00Z"
    }
  ],
  "workflow_runs": [ ... ],
  "task_history": [ ... ],
  "milestones": [ ... ],
  "agent_status": [ ... ],
  "execution_traces": [ ... ],
  "memory_records": [ ... ],
  "schema_migrations": [ ... ]
}
```

### 4.2 Error Classes & Invariants

```python
class PersistenceError(Exception):
    """Base exception for persistent CrewAI storage."""

class SchemaVersionMismatchError(PersistenceError):
    """Raised when import JSON has an incompatible schema version."""

class StateImportConflictError(PersistenceError):
    """Raised when importing into an existing project without overwrite=True."""

class StateIntegrityError(PersistenceError):
    """Raised when export/import JSON is corrupted or violates schema constraints."""
```

### 4.3 Export Logic (`export_state_to_json`)

1. Connects to SQLite database with `sqlite3.Row` factory.
2. Selects all rows from:
   - `projects`
   - `decisions`
   - `workflow_runs`
   - `task_history`
   - `milestones`
   - `agent_status`
   - `execution_traces`
   - `memory_records`
   - `schema_migrations`
3. Filters by `project_id` if specified, or exports all if `project_id=None`.
4. Decodes nested JSON columns (`metadata_json`, `criteria_json`, `tags`, `embedding_json`) into structured Python objects for clean JSON serialization.
5. Writes to destination file atomically with indentation `indent=2`.

### 4.4 Import Logic (`import_state_from_json`)

1. **Validation Phase**:
   - Parses JSON content; catches `json.JSONDecodeError` and raises `StateIntegrityError`.
   - Checks `schema_version`: Must equal `"1.0.0"`. If not, raises `SchemaVersionMismatchError`.
   - Checks required structure: Must contain `"projects"` list with at least 1 record.
2. **Conflict Checking**:
   - Inspects target database: queries `SELECT project_id FROM projects WHERE project_id IN (...)`.
   - If project already exists and `overwrite=False`:
     Raises `StateImportConflictError(f"Project '{pid}' already exists in database. Pass overwrite=True to replace.")`.
3. **Atomic Transactional Insertion**:
   - Executes inside `with conn:` block (automatic rollback on exception).
   - If `overwrite=True` and project exists:
     `conn.execute("DELETE FROM projects WHERE project_id = ?", (pid,))`.
     *Note*: Because all child tables define `ON DELETE CASCADE`, deleting the parent `projects` row automatically and cleanly removes all matching records across `decisions`, `workflow_runs`, `task_history`, `milestones`, `agent_status`, `execution_traces`, and `memory_records`!
   - Inserter executes in topological dependency order:
     1. `schema_migrations` (`INSERT OR IGNORE`)
     2. `projects`
     3. `workflow_runs`
     4. `task_history`
     5. `agent_status`
     6. `decisions`
     7. `milestones`
     8. `execution_traces`
     9. `memory_records` (automatically synchronizing `memory_fts` via trigger)
4. **Result Summary**:
   - Returns a structured dictionary confirming imported entity counts.

---

## 5. File Layout & Integration Plan

```
src/persistent_crewai/persistence/
├── __init__.py           # Re-exports SQLiteVectorStore, DeterministicHashEmbeddingProvider, export/import
├── db.py                 # SQLite connection management & DDL (from Explorer M1-1)
├── state_manager.py      # State tracking (from Explorer M1-2)
├── vector_store.py       # VectorStoreInterface, SearchResult, DeterministicHashEmbeddingProvider, SQLiteVectorStore
├── export_import.py      # export_state_to_json, import_state_from_json, error classes
└── session.py            # Session resumption (Explorer M1-2) re-exporting export/import
```

---

## 6. Comprehensive Unit Test Plan

### Test File 1: `tests/test_persistence_vector.py`

| Test Case ID | Test Name | Purpose | Expected Behavior |
|--------------|-----------|---------|-------------------|
| `TC-VEC-01` | `test_hash_embedder_determinism` | Verify deterministic output | Same text called twice produces exact identical float vector |
| `TC-VEC-02` | `test_hash_embedder_dimension_and_norm` | Verify vector dimension and $L_2$ norm | Vector length matches dimension (64, 128); norm is $1.0 \pm 10^{-4}$ |
| `TC-VEC-03` | `test_hash_embedder_empty_and_whitespace` | Verify edge cases with empty input | `""`, `"   "`, `"!@#$%"` return zero vectors without exceptions |
| `TC-VEC-04` | `test_hash_embedder_semantic_discrimination` | Verify cosine discrimination | Query "SQLite WAL database" has higher cosine with "SQLite persistence" than "baking apple pie" |
| `TC-VEC-05` | `test_vector_store_add_and_count` | Verify adding records | Adding 5 records increments `count()` by 5; records store embeddings & metadata |
| `TC-VEC-06` | `test_vector_store_cosine_search` | Verify top-k cosine similarity retrieval | Search returns most relevant document at rank 1 with high similarity score |
| `TC-VEC-07` | `test_vector_store_metadata_filtering` | Verify metadata filtering | Search with `filter_metadata={"role": "Architect"}` returns only Architect records |
| `TC-VEC-08` | `test_vector_store_hybrid_fts5_search` | Verify FTS5 keyword hybrid retrieval | Exact keyword match boosts document score in hybrid mode |
| `TC-VEC-09` | `test_vector_store_fts5_syntax_safety` | Verify FTS5 query sanitization | Query containing `"WAL AND (OR * NOT \""` does not raise SQLite syntax error |
| `TC-VEC-10` | `test_vector_store_delete` | Verify document deletion | Calling `delete([id])` removes record from `memory_records` and `memory_fts` |
| `TC-VEC-11` | `test_vector_store_persistence_across_connections` | Verify disk persistence | Records added in connection 1 are searchable in independent connection 2 |
| `TC-VEC-12` | `test_vector_store_empty_search` | Verify empty search behavior | Empty query `""` or search on empty collection returns `[]` without error |

### Test File 2: `tests/test_state_export_import.py`

| Test Case ID | Test Name | Purpose | Expected Behavior |
|--------------|-----------|---------|-------------------|
| `TC-EXP-01` | `test_export_full_state_to_json` | Test export of all 8 tables | Exports complete valid JSON with metadata, decisions, tasks, traces, memories |
| `TC-EXP-02` | `test_export_single_project` | Test filtered export by project_id | Only records matching specified `project_id` are exported |
| `TC-IMP-01` | `test_import_into_fresh_database` | Test clean rehydration | Fresh SQLite database rehydrated; record counts match original database |
| `TC-IMP-02` | `test_import_schema_version_mismatch` | Test schema version validation | Importing JSON with `schema_version="2.0.0"` raises `SchemaVersionMismatchError` |
| `TC-IMP-03` | `test_import_conflict_detection` | Test existing project conflict check | Importing existing project with `overwrite=False` raises `StateImportConflictError` |
| `TC-IMP-04` | `test_import_overwrite_cascade` | Test overwrite replacement | Importing with `overwrite=True` cascades deletes existing state and replaces cleanly |
| `TC-IMP-05` | `test_import_corrupted_json` | Test malformed JSON handling | Corrupted JSON or missing projects raises `StateIntegrityError` |
| `TC-IMP-06` | `test_export_import_roundtrip_verification` | Test end-to-end memory retrieval after import | Documents searchable in original DB remain searchable with identical scores in imported DB |
| `TC-IMP-07` | `test_import_atomic_rollback_on_failure` | Test transaction rollback | Failure mid-import rolls back completely, leaving destination database untouched |
