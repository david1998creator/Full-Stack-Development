"""
Adversarial Stress Test Suite for Milestone 1 Persistence Architecture.
Tests edge cases, foreign key integrity, SQL injection resistance, multithreaded concurrency,
corrupted export/import handling, and role normalization boundaries.
"""

import concurrent.futures
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import normalize_role, CANONICAL_ROLES
from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import SQLiteVectorStore, DeterministicHashEmbeddingProvider
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    SchemaVersionMismatchError,
    StateIntegrityError,
    StateImportConflictError
)

def test_adversarial_suite():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "adversarial.db"
        sm = StateManager(db_path=db_path, project_id="adv_proj")
        sm.create_project("adv_proj", "Adversarial Project", "Stress testing")
        
        # 1. SQL Injection / Escaping Stress Test
        evil_strings = [
            "'; DROP TABLE projects; --",
            "\" OR \"1\"=\"1",
            "Robert'); DROP TABLE Students;--",
            "None",
            "\x00nullbyte",
            "🔥 Unicode emoji and symbols 💥 🚀",
            "Text with 'single' and \"double\" quotes and \\backslashes\\",
        ]
        
        for idx, evil in enumerate(evil_strings):
            dec_id = sm.record_decision(
                agent_role="Architect",
                title=f"Evil Title {idx}: {evil}",
                rationale=f"Evil Rationale {idx}: {evil}",
                tags=[evil]
            )
            retrieved = sm.get_decision(dec_id)
            assert retrieved is not None
            assert retrieved["title"] == f"Evil Title {idx}: {evil}"
            assert evil in retrieved["tags"]
        print("[ADVERSARIAL] SQL Injection resistance verified: parameterized queries prevent statement escaping.")
        
        # 2. Foreign Key Constraint Enforcement
        # Attempting to insert a decision with a nonexistent project_id should fail
        with sm.connection() as conn:
            fk_violated = False
            try:
                conn.execute(
                    "INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications) VALUES (?, ?, ?, ?, ?, ?)",
                    ("dec_orphan", "nonexistent_project_999", "Architect", "Orphan", "Orphan", "")
                )
            except sqlite3.IntegrityError:
                fk_violated = True
            assert fk_violated, "Foreign key constraint NOT enforced! Orphan record was allowed."
        print("[ADVERSARIAL] Foreign key enforcement verified: orphan child records rejected by SQLite.")
        
        # 3. Role Normalization and Aliases
        aliases_to_test = [
            ("coordinator", "Coordinator"),
            ("coordinator / manager", "Coordinator"),
            ("MANAGER", "Coordinator"),
            ("github", "GitHub Manager"),
            ("github_manager", "GitHub Manager"),
            ("memory", "Memory & Status Manager"),
            ("MEMORY & STATUS MANAGER", "Memory & Status Manager"),
            ("Developer", "Developer"),
            ("developer", "Developer"),
        ]
        for raw, expected in aliases_to_test:
            norm = normalize_role(raw)
            assert norm == expected, f"Failed normalize_role('{raw}'): got {norm}, expected {expected}"
            
        # Unrecognized role should raise KeyError
        try:
            normalize_role("UnknownHackerRole")
            assert False, "normalize_role should raise KeyError for unknown role"
        except KeyError:
            pass
        print("[ADVERSARIAL] Role normalization and alias mapping verified across all variations.")
        
        # 4. Vector Store Edge Cases
        vs = SQLiteVectorStore(db_path=db_path, project_id="adv_proj")
        
        # Empty documents list
        assert vs.add([]) == []
        
        # Empty query
        assert vs.search("") == []
        assert vs.search("   ") == []
        
        # top_k <= 0
        assert vs.search("test", top_k=0) == []
        assert vs.search("test", top_k=-5) == []
        
        # top_k > available documents
        vs.add(["Only one document exists"])
        results = vs.search("document", top_k=100)
        assert len(results) <= 1
        
        # Search with non-matching metadata filter
        filtered_out = vs.search("document", filter_metadata={"nonexistent_key": "impossible_val"})
        assert len(filtered_out) == 0
        print("[ADVERSARIAL] Vector store edge cases (empty strings, top_k boundaries, non-matching filters) handled gracefully.")
        
        # 5. Multithreaded Concurrency Stress Test
        # 10 worker threads simultaneously logging decisions, tasks, traces, and vector adds
        def concurrent_worker(worker_id: int):
            worker_sm = StateManager(db_path=db_path, project_id="adv_proj")
            worker_vs = SQLiteVectorStore(db_path=db_path, project_id="adv_proj")
            for i in range(5):
                d_id = worker_sm.record_decision(
                    agent_role="Developer",
                    title=f"Worker {worker_id} Decision {i}",
                    rationale="Testing concurrent SQLite writes"
                )
                t_id = f"task_w{worker_id}_{i}"
                worker_sm.record_task_start(t_id, "run_adv", "Developer", f"Worker task {i}")
                worker_sm.start_task(t_id)
                worker_sm.record_task_completion(t_id, actual_output=f"Output from worker {worker_id}")
                worker_vs.add([f"Worker {worker_id} memory item {i}"], ids=[f"mem_w{worker_id}_{i}"])
            return True
            
        sm.create_workflow_run("run_adv", "concurrent_test")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(concurrent_worker, w) for w in range(5)]
            results = [f.result() for f in futures]
            assert all(results)
        print("[ADVERSARIAL] Multithreaded concurrency stress test passed: 5 threads x 5 cycles completed without locking errors.")
        
        # 6. State Export / Import Corrupted Input Tests
        session_mgr = SessionResumptionManager(db_path=db_path, project_id="adv_proj")
        
        # Test nonexistent file
        try:
            session_mgr.import_state(Path(temp_dir) / "does_not_exist.json")
            assert False, "Should raise FileNotFoundError"
        except FileNotFoundError:
            pass
            
        # Test corrupt JSON file
        corrupt_file = Path(temp_dir) / "corrupt.json"
        corrupt_file.write_text("{ unclosed invalid json payload ")
        try:
            session_mgr.import_state(corrupt_file)
            assert False, "Should raise StateIntegrityError"
        except StateIntegrityError:
            pass
            
        # Test schema version mismatch
        bad_ver = Path(temp_dir) / "bad_ver.json"
        bad_ver.write_text(json.dumps({"schema_version": "0.0.1", "projects": [{"project_id": "p"}]}))
        try:
            session_mgr.import_state(bad_ver)
            assert False, "Should raise SchemaVersionMismatchError"
        except SchemaVersionMismatchError:
            pass
            
        # Test missing project records in JSON
        no_proj = Path(temp_dir) / "no_proj.json"
        no_proj.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}))
        try:
            session_mgr.import_state(no_proj)
            assert False, "Should raise StateIntegrityError"
        except StateIntegrityError:
            pass
        print("[ADVERSARIAL] State export/import corruption handling verified (corrupt JSON, version mismatch, missing projects).")

    print("[SUCCESS] ALL ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY.")

if __name__ == "__main__":
    test_adversarial_suite()
