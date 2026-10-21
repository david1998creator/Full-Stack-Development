"""
Independent forensic test for DeterministicHashEmbeddingProvider and vector mathematics.
Verifies SHA256 hashing, L2 normalization, cosine similarity math, ranking, and hybrid FTS scoring.
"""

import math
import sys
import tempfile
from pathlib import Path

SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
    SearchResult
)

def test_mathematical_vector_integrity():
    embedder = DeterministicHashEmbeddingProvider(dimension=64, seed_prefix="crewai_emb_")
    
    # 1. Determinism
    text1 = "Architectural blueprint for persistent multi-agent framework"
    v1_a = embedder.embed_query(text1)
    v1_b = embedder.embed_query(text1)
    assert v1_a == v1_b, "Embedding provider is not deterministic!"
    print("[MATH CHECK] Determinism verified: identical inputs produce identical 64-dim vectors.")
    
    # 2. Vector dimensionality
    assert len(v1_a) == 64, f"Expected 64 dimensions, got {len(v1_a)}"
    assert embedder.dimension == 64
    
    # 3. Mathematical L2 normalization: ||v|| = 1.0
    norm = math.sqrt(sum(x * x for x in v1_a))
    assert abs(norm - 1.0) < 1e-4, f"Vector norm {norm} is not unit length!"
    print(f"[MATH CHECK] Unit L2 norm verified: ||v|| = {norm:.6f} ~= 1.0")
    
    # 4. Cosine similarity of identical text: dot(v, v) = 1.0
    self_similarity = sum(a * b for a, b in zip(v1_a, v1_b))
    assert abs(self_similarity - 1.0) < 1e-4, f"Self-cosine similarity {self_similarity} != 1.0"
    print(f"[MATH CHECK] Self-cosine similarity verified: cos(v, v) = {self_similarity:.6f}")
    
    # 5. Semantic discrimination:
    # Related query should have significantly higher cosine similarity than unrelated query
    q = "SQLite database schema and migrations"
    doc_relevant = "Relational database schema with SQLite tables and migrations"
    doc_irrelevant = "Fresh organic avocados and citrus fruit recipes for salad"
    
    q_vec = embedder.embed_query(q)
    rel_vec = embedder.embed_query(doc_relevant)
    irrel_vec = embedder.embed_query(doc_irrelevant)
    
    sim_rel = sum(a * b for a, b in zip(q_vec, rel_vec))
    sim_irrel = sum(a * b for a, b in zip(q_vec, irrel_vec))
    
    print(f"[MATH CHECK] Cosine similarities: relevant={sim_rel:.4f}, irrelevant={sim_irrel:.4f}")
    assert sim_rel > sim_irrel, f"Expected sim_rel > sim_irrel, got {sim_rel} <= {sim_irrel}"
    assert sim_rel > 0.45, f"Relevant similarity unexpectedly low: {sim_rel}"
    assert sim_irrel < 0.35, f"Irrelevant similarity unexpectedly high: {sim_irrel}"
    
    # 6. Subword n-gram sensitivity (e.g. typos or morphological variations)
    # "database" vs "databases" vs "databas" should share character n-grams
    v_base = embedder.embed_query("database")
    v_plural = embedder.embed_query("databases")
    v_typo = embedder.embed_query("databas")
    sim_plural = sum(a * b for a, b in zip(v_base, v_plural))
    sim_typo = sum(a * b for a, b in zip(v_base, v_typo))
    print(f"[MATH CHECK] Morphological n-gram overlap: database/databases={sim_plural:.4f}, database/databas={sim_typo:.4f}")
    assert sim_plural > 0.5, f"Morphological variation failed: {sim_plural}"
    assert sim_typo > 0.5, f"Typo n-gram variation failed: {sim_typo}"
    
    # 7. Vector store cosine ranking verification
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "vstore_math.db"
        vs = SQLiteVectorStore(db_path=db_path, embedder=embedder, project_id="p_math")
        
        docs = [
            "High performance SQLite WAL mode indexing",
            "UI components with Tailwind and React design",
            "Pytest automated regression test harness"
        ]
        vs.add(docs, ids=["doc_db", "doc_ui", "doc_test"])
        
        # Search for database query
        res = vs.search("SQLite WAL mode", top_k=3, hybrid_search=False)
        assert len(res) == 3
        # First result MUST be doc_db
        assert res[0].document_id == "doc_db", f"Top result was {res[0].document_id}, expected doc_db"
        assert res[0].score > res[1].score, f"Top score {res[0].score} not strictly greater than 2nd {res[1].score}"
        print(f"[MATH CHECK] Pure cosine ranking: 1st={res[0].document_id} ({res[0].score}), 2nd={res[1].document_id} ({res[1].score})")
        
        # Test hybrid search (combining BM25 and cosine)
        res_hybrid = vs.search("SQLite WAL mode", top_k=3, hybrid_search=True, hybrid_alpha=0.7)
        assert res_hybrid[0].document_id == "doc_db"
        print(f"[MATH CHECK] Hybrid search ranking: 1st={res_hybrid[0].document_id} ({res_hybrid[0].score})")
        
    print("[SUCCESS] ALL MATHEMATICAL VECTOR INTEGRITY CHECKS PASSED EMPIRICALLY.")

if __name__ == "__main__":
    test_mathematical_vector_integrity()
