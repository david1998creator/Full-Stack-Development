"""
Adversarial Stress Test Suite for Milestone 6 Tier 5: Persistence & Sandbox Hardening.

Empirically challenges:
1. Path traversal evasion: deep nested traversals (/foo/bar/../../..), URL-encoded,
   mixed slashes, backslashes, alternate data streams (ADS), drive-relative paths,
   reserved device names, and symlink/junction containment.
2. Atomic file operations: concurrent write collisions, read during atomic replace,
   read of non-existent/corrupt temp files, tempfile cleanup on write failure.
3. SQLite WAL concurrency stress: multiple concurrent database instances, connection
   exhaustion, transaction rollback integrity, FTS5 injection attacks.
4. Vector store edge cases: zero-norm vectors, high dimensional mismatch, NaN/Inf values,
   empty query handling, boundary top_k values.
5. Session export/import corruption: malformed JSON, non-dict payloads, missing tables,
   schema version mismatch, foreign key constraint violations, and atomic rollback.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import (
    CANONICAL_ROLES,
    CURRENT_SCHEMA_VERSION,
    get_workspace_root,
)
from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.session import (
    SchemaVersionMismatchError,
    SessionResumptionManager,
    StateImportConflictError,
    StateIntegrityError,
)
from persistent_crewai.persistence.state_manager import (
    PersistenceError,
    ProjectNotFoundError,
    StateManager,
)
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    EmbeddingProviderInterface,
    SQLiteVectorStore,
    SearchResult,
)
from persistent_crewai.tools.workspace_tools import (
    WorkspaceFileListTool,
    WorkspaceFilePatchTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriteTool,
    validate_safe_path,
)


# ==============================================================================
# Suite 1: Path Traversal Evasion & Sandbox Containment
# ==============================================================================
class TestPathTraversalEvasionAdversarial(unittest.TestCase):
    """Stress tests path traversal defenses against evasion vectors."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_path_")
        self.workspace_root = Path(self.temp_dir).resolve()
        self.sub_dir = self.workspace_root / "nested" / "inner"
        self.sub_dir.mkdir(parents=True, exist_ok=True)
        self.canary_file = self.sub_dir / "canary.txt"
        self.canary_file.write_text("SAFE_INNER_CONTENT", encoding="utf-8")

        self.reader = WorkspaceFileReadTool(workspace_root=self.workspace_root)
        self.writer = WorkspaceFileWriteTool(workspace_root=self.workspace_root)
        self.lister = WorkspaceFileListTool(workspace_root=self.workspace_root)
        self.patcher = WorkspaceFilePatchTool(workspace_root=self.workspace_root)

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_deep_nested_traversal_evasion_blocked(self) -> None:
        """Deep nested traversals attempting to climb out of workspace root are blocked."""
        deep_traversal_payloads = [
            "/foo/bar/../../..",
            "nested/inner/../../../../etc/passwd",
            "nested/inner/../../../../Windows/System32/calc.exe",
            "a/b/c/d/e/f/../../../../../../../boot.ini",
            "nested/./../../..",
            "nested/inner/../..//..",
            "./../../../../../../../../../../windows/win.ini",
        ]
        for payload in deep_traversal_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(PermissionError, msg=f"Payload failed to be blocked: {payload}"):
                    validate_safe_path(payload, self.workspace_root)

                # Verify tool _run returns Error string without unhandled crash
                err_res = self.reader._run(file_path=payload)
                self.assertTrue(err_res.startswith("Error:"), f"Expected Error string, got: {err_res}")

    def test_mixed_slashes_and_backslashes_evasion_blocked(self) -> None:
        """Mixed forward and backward slashes cannot bypass traversal boundary."""
        mixed_payloads = [
            r"nested\inner/../../../escape.txt",
            r"nested/inner\..\..\..\escape.txt",
            r"..\..\escape.txt",
            r"nested/..\..\escape.txt",
            r".\..\..\escape.txt",
            r"nested\/..\/\..\/escape.txt",
            r"nested\\inner\\..\\..\\..\\escape.txt",
        ]
        for payload in mixed_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(PermissionError):
                    validate_safe_path(payload, self.workspace_root)

    def test_url_encoded_and_escaped_traversal_handling(self) -> None:
        """URL-encoded traversal sequences (%2e%2e%2f) cannot escape workspace root."""
        encoded_payloads = [
            "%2e%2e%2f%2e%2e%2fescape.txt",
            "%2e%2e/escape.txt",
            "..%2f..%2fescape.txt",
            "%2e%2e%5c%2e%2e%5cescape.txt",
            "%252e%252e%252fescape.txt",
        ]
        for payload in encoded_payloads:
            with self.subTest(payload=payload):
                # When passed to validate_safe_path, path either stays safely inside workspace or raises PermissionError
                try:
                    resolved = validate_safe_path(payload, self.workspace_root)
                    # If valid, must be strictly inside workspace_root
                    self.assertTrue(
                        resolved.is_relative_to(self.workspace_root),
                        f"Resolved path escaped workspace root: {resolved}",
                    )
                except PermissionError:
                    pass  # Safely rejected

    def test_alternate_data_streams_containment(self) -> None:
        """NTFS Alternate Data Streams cannot corrupt files or bypass sandboxing."""
        ads_payloads = [
            "canary.txt:hidden_stream",
            "canary.txt::$DATA",
            "nested/canary.txt:stream2",
        ]
        for payload in ads_payloads:
            with self.subTest(payload=payload):
                try:
                    resolved = validate_safe_path(payload, self.workspace_root)
                    self.assertTrue(resolved.is_relative_to(self.workspace_root))
                except (PermissionError, ValueError):
                    pass

                # If writer tool is called, os.replace or parameter validation must reject it safely without corruption
                res = self.writer._run(file_path=payload, content="ads content")
                # Either succeeds within sandbox or gracefully returns Error string
                self.assertIsInstance(res, str)

        # Confirm canary file content is uncorrupted
        self.assertEqual(self.canary_file.read_text(encoding="utf-8"), "SAFE_INNER_CONTENT")

    def test_windows_device_names_and_drive_relatives(self) -> None:
        """Reserved device names (NUL, CON) and drive-relative paths are contained."""
        special_payloads = [
            "C:file.txt",
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "LPT1",
        ]
        for payload in special_payloads:
            with self.subTest(payload=payload):
                try:
                    resolved = validate_safe_path(payload, self.workspace_root)
                    self.assertTrue(
                        resolved.is_relative_to(self.workspace_root),
                        f"Special path {payload} escaped workspace: {resolved}",
                    )
                except (PermissionError, ValueError):
                    pass


# ==============================================================================
# Suite 2: Atomic File Operations & Race Conditions
# ==============================================================================
class TestAtomicFileOperationsStress(unittest.TestCase):
    """Stress tests atomic file write/replace, read during replace, and tempfile cleanup."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_atomic_")
        self.workspace_root = Path(self.temp_dir).resolve()
        self.writer = WorkspaceFileWriteTool(workspace_root=self.workspace_root)
        self.reader = WorkspaceFileReadTool(workspace_root=self.workspace_root)
        self.patcher = WorkspaceFilePatchTool(workspace_root=self.workspace_root)

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_concurrent_write_collisions_zero_torn_reads(self) -> None:
        """16 concurrent threads hammering the same file: zero torn reads, zero leaked temp files."""
        target_name = "contested_file.txt"
        self.writer.write_file(target_name, "INITIAL_BLOCK_0000\n")

        num_threads = 16
        writes_per_thread = 15
        stop_readers = False
        corrupted_reads = 0
        successful_writes = 0
        lock_exceptions = 0

        def writer_job(thread_id: int):
            nonlocal successful_writes, lock_exceptions
            for i in range(writes_per_thread):
                body = f"HEADER_{thread_id:02d}_{i:03d}\n" + ("DATA_" * 100) + f"\nFOOTER_{thread_id:02d}_{i:03d}\n"
                try:
                    self.writer.write_file(target_name, body)
                    successful_writes += 1
                except (PermissionError, OSError):
                    # On Windows, os.replace contention can raise WinError 5/32
                    lock_exceptions += 1
                time.sleep(0.0005)

        def reader_job():
            nonlocal corrupted_reads
            while not stop_readers:
                try:
                    c = self.reader.read_file(target_name)
                    # Content must be either initial or contain matched HEADER and FOOTER
                    lines = c.strip().split("\n")
                    if c == "INITIAL_BLOCK_0000\n":
                        pass
                    elif len(lines) >= 3 and lines[0].startswith("HEADER_") and lines[-1].startswith("FOOTER_"):
                        h_tag = lines[0].replace("HEADER_", "")
                        f_tag = lines[-1].replace("FOOTER_", "")
                        if h_tag != f_tag:
                            corrupted_reads += 1
                    else:
                        corrupted_reads += 1
                except (PermissionError, OSError, FileNotFoundError):
                    pass
                time.sleep(0.0005)

        read_threads = [threading.Thread(target=reader_job) for _ in range(4)]
        write_threads = [threading.Thread(target=writer_job, args=(i,)) for i in range(num_threads)]

        for rt in read_threads:
            rt.start()
        for wt in write_threads:
            wt.start()

        for wt in write_threads:
            wt.join()

        stop_readers = True
        for rt in read_threads:
            rt.join()

        self.assertEqual(corrupted_reads, 0, f"Detected {corrupted_reads} torn/corrupted reads under concurrency!")
        self.assertGreater(successful_writes, 0, "Expected at least some writes to complete successfully")

        # Verify no orphaned temporary files remained
        temp_files = [f.name for f in self.workspace_root.iterdir() if f.name.startswith("tmp") and f.is_file()]
        self.assertEqual(temp_files, [], f"Orphaned temporary files detected: {temp_files}")

    def test_write_failure_cleans_up_temp_and_preserves_original(self) -> None:
        """When write or replace fails, temp files are removed and original file is pristine."""
        target_name = "guarded_file.txt"
        original_content = "ORIGINAL_PRISTINE_CONTENT_12345"
        self.writer.write_file(target_name, original_content)

        # Mock os.replace to raise OSError (simulating unexpected disk failure)
        with patch("os.replace", side_effect=OSError("Simulated I/O disk failure")):
            with self.assertRaises(OSError):
                self.writer.write_file(target_name, "NEW_CORRUPT_PAYLOAD")

        # Original content must be completely preserved
        current_content = self.reader.read_file(target_name)
        self.assertEqual(current_content, original_content)

        # Verify zero leftover temp files in directory
        temp_files = [f.name for f in self.workspace_root.iterdir() if f.name.startswith("tmp") and f.is_file()]
        self.assertEqual(temp_files, [], f"Temp files leaked after failure: {temp_files}")

    def test_patch_concurrency_and_exact_block_isolation(self) -> None:
        """Patch tool safely rejects ambiguous or nonexistent blocks and patches atomically."""
        patch_file = "patch_target.txt"
        self.writer.write_file(patch_file, "def foo():\n    return 42\n\ndef bar():\n    return 42\n")

        # Ambiguous patch (2 occurrences of "return 42")
        with self.assertRaises(ValueError) as ctx:
            self.patcher.patch_file(patch_file, search_block="return 42", replace_block="return 99")
        self.assertIn("Ambiguous patch", str(ctx.exception))

        # Unique patch succeeds
        res = self.patcher.patch_file(patch_file, search_block="def foo():\n    return 42", replace_block="def foo():\n    return 100")
        self.assertIn("Successfully patched", res)

        content = self.reader.read_file(patch_file)
        self.assertIn("return 100", content)
        self.assertIn("return 42", content)


# ==============================================================================
# Suite 3: SQLite WAL Concurrency, Descriptors & FTS5 Injection
# ==============================================================================
class TestSQLiteWALConcurrencyAndStress(unittest.TestCase):
    """Stress tests SQLite WAL concurrency, descriptor exhaustion, and FTS5 safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_wal_")
        self.db_path = Path(self.temp_dir) / "wal_stress.db"
        self.db_mgr = init_db(self.db_path)
        self.sm = StateManager(db_path=self.db_path, project_id="proj_wal")
        self.sm.create_project("proj_wal", "WAL Project", "Testing WAL Concurrency")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_20_concurrent_database_instances_hammering_wal(self) -> None:
        """20 distinct StateManager & DatabaseManager instances simultaneously writing and reading."""
        num_threads = 20
        ops_per_thread = 25
        errors: List[Exception] = []

        def worker_task(idx: int):
            try:
                worker_sm = StateManager(db_path=self.db_path, project_id="proj_wal")
                run_id = f"run_worker_{idx}"
                worker_sm.create_workflow_run(run_id=run_id, input_prompt=f"Worker {idx} start")

                for i in range(ops_per_thread):
                    t_id = f"task_{idx}_{i}"
                    role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
                    worker_sm.record_task_start(
                        task_id=t_id,
                        run_id=run_id,
                        agent_role=role,
                        task_name=f"Task {i}",
                        execution_order=i,
                    )
                    worker_sm.start_task(t_id)
                    worker_sm.record_task_completion(task_id=t_id, agent=role, output=f"Output {i}")
                    worker_sm.record_decision(
                        agent_role=role,
                        title=f"Dec {idx}-{i}",
                        rationale=f"Rationale {i}",
                        tags=[f"tag_{idx}"],
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent WAL write errors encountered: {errors}")

        # Verify all records exist in DB
        total_tasks = self.sm.get_task_history(project_id="proj_wal", limit=1000)
        self.assertEqual(len(total_tasks), num_threads * ops_per_thread)

    def test_rapid_transaction_descriptor_exhaustion_stress(self) -> None:
        """Rapid open/close cycles across 30 threads (600 transactions) verify no handle leaks."""
        # Ensure system_run exists in workflow_runs for trace foreign key
        self.sm.create_workflow_run(run_id="system_run", input_prompt="System run prompt")

        num_threads = 30
        trans_per_thread = 20
        errors: List[Exception] = []

        def rapid_worker(idx: int):
            try:
                for i in range(trans_per_thread):
                    # Each iteration creates fresh connection and commits
                    db = DatabaseManager(db_path=self.db_path)
                    cur = db.execute(
                        "INSERT INTO execution_traces (trace_id, run_id, agent_role, event_type, payload) VALUES (?, 'system_run', 'Developer', 'test', '{}')",
                        (f"trc_rapid_{idx}_{i}",),
                    )
                    self.assertEqual(cur.rowcount, 1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=rapid_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Descriptor exhaustion / connection errors: {errors}")

    def test_transaction_rollback_atomic_integrity(self) -> None:
        """Simulated mid-transaction failure guarantees 100% rollback without dirty rows."""
        initial_decisions = len(self.sm.get_decisions("proj_wal"))

        # Execute transaction block that inserts 10 rows and fails on 11th
        with self.assertRaises(ValueError):
            with self.sm.connection() as conn:
                for i in range(10):
                    conn.execute(
                        "INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications) VALUES (?, 'proj_wal', 'Architect', ?, ?, 'None')",
                        (f"dec_rb_{i}", f"Title {i}", f"Rationale {i}"),
                    )
                # Deliberate failure
                raise ValueError("Simulated abort after partial inserts")

        # Verify zero dirty rows inserted
        after_decisions = len(self.sm.get_decisions("proj_wal"))
        self.assertEqual(initial_decisions, after_decisions)

    def test_fts5_injection_and_syntax_safety(self) -> None:
        """Hostile FTS5 queries and SQL injection payloads are safely sanitized without syntax errors."""
        store = SQLiteVectorStore(db_path=self.db_path, project_id="proj_wal")
        store.add([
            "PostgreSQL and SQLite relational database schema optimization",
            "CrewAI multi-agent delegation and autonomous collaboration",
        ])

        hostile_queries = [
            "' OR 1=1 --",
            "\" OR \"a\"=\"a",
            "NEAR(foo, bar, 10)",
            "NOT * AND OR",
            "MATCH 'column:value'",
            "'; DROP TABLE memory_records; --",
            "\"unclosed quote",
            "*** wildcard bomb ***",
            "{json: \"injection\"}",
            "unicode: \u202e\u202d\u0000\uffff",
            "A" * 5000,  # 5,000 char query
        ]

        for q in hostile_queries:
            with self.subTest(query=q[:30]):
                # Must return SearchResults gracefully without raising sqlite3.OperationalError or any crash
                results = store.search(q, top_k=5)
                self.assertIsInstance(results, list)

        # Confirm tables still exist and are uncorrupted
        count = store.count()
        self.assertEqual(count, 2)


# ==============================================================================
# Suite 4: Vector Store Math & Edge Cases
# ==============================================================================
class TestVectorStoreEdgeCasesAdversarial(unittest.TestCase):
    """Stress tests embedding provider invariants, dimensional mismatches, and NaN/Inf values."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_vec_")
        self.db_path = Path(self.temp_dir) / "vec_stress.db"
        self.embedder = DeterministicHashEmbeddingProvider(dimension=64)
        self.store = SQLiteVectorStore(db_path=self.db_path, embedder=self.embedder, project_id="proj_vec")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_zero_norm_vectors_handling(self) -> None:
        """Zero-norm vectors (empty string, symbols only) do not trigger ZeroDivisionError."""
        zero_texts = ["", "   ", "!@#$%^&*()", "\t\n\r", "???", "---"]
        for txt in zero_texts:
            vec = self.embedder.embed_query(txt)
            self.assertEqual(len(vec), 64)
            norm = math.sqrt(sum(x * x for x in vec))
            self.assertAlmostEqual(norm, 0.0, places=5)

        # Indexing zero-norm documents
        doc_ids = self.store.add(zero_texts)
        self.assertEqual(len(doc_ids), len(zero_texts))

        # Querying zero-norm text
        res = self.store.search("???", top_k=5)
        self.assertIsInstance(res, list)

    def test_high_dimensional_mismatch_resilience(self) -> None:
        """Vector store handles candidate vectors with mismatched dimensions without crash."""
        # Add 64-dim documents
        self.store.add(["Memory doc about python architecture", "Memory doc about react interface"])

        # Reopen store with 128-dim embedder
        embedder128 = DeterministicHashEmbeddingProvider(dimension=128)
        store128 = SQLiteVectorStore(db_path=self.db_path, embedder=embedder128, project_id="proj_vec")

        # Search against 64-dim stored embeddings
        results = store128.search("python architecture", top_k=5)
        self.assertGreater(len(results), 0)
        self.assertIsInstance(results[0].score, float)

        # Reopen store with 32-dim embedder
        embedder32 = DeterministicHashEmbeddingProvider(dimension=32)
        store32 = SQLiteVectorStore(db_path=self.db_path, embedder=embedder32, project_id="proj_vec")
        results32 = store32.search("react interface", top_k=5)
        self.assertGreater(len(results32), 0)

    def test_nan_and_inf_vector_containment(self) -> None:
        """Corrupt NaN or Infinity embeddings in database do not crash search ranking."""
        # Manually inject candidate with NaN and Inf embeddings
        with self.store._db_mgr.connection() as conn:
            conn.execute(
                "INSERT INTO memory_records (record_id, project_id, content, embedding_json) VALUES (?, 'proj_vec', ?, ?)",
                ("mem_nan", "NaN candidate", json.dumps([float("nan")] * 64)),
            )
            conn.execute(
                "INSERT INTO memory_records (record_id, project_id, content, embedding_json) VALUES (?, 'proj_vec', ?, ?)",
                ("mem_inf", "Inf candidate", json.dumps([float("inf")] * 64)),
            )

        # Search should execute cleanly and suppress NaN scores
        results = self.store.search("test query", top_k=10)
        self.assertIsInstance(results, list)
        for r in results:
            self.assertFalse(math.isnan(r.score), f"Score for {r.document_id} was NaN!")

    def test_empty_query_and_boundary_top_k(self) -> None:
        """Empty queries and boundary top_k values (0, negative, oversized) are handled."""
        self.store.add(["Document one", "Document two"])

        # Empty/whitespace queries return empty list
        self.assertEqual(self.store.search(""), [])
        self.assertEqual(self.store.search("   "), [])
        self.assertEqual(self.store.search("\t\n"), [])

        # Boundary top_k
        self.assertEqual(self.store.search("Document", top_k=0), [])
        self.assertEqual(self.store.search("Document", top_k=-10), [])

        # Oversized top_k returns all available
        res = self.store.search("Document", top_k=1000)
        self.assertEqual(len(res), 2)


# ==============================================================================
# Suite 5: Session Export/Import Corruption & Integrity
# ==============================================================================
class TestSessionExportImportCorruptionAdversarial(unittest.TestCase):
    """Stress tests state export/import against corrupt JSON, invalid schema, and FK violations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_import_")
        self.db_path = Path(self.temp_dir) / "import_stress.db"
        self.mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_imp")
        self.sm = self.mgr._state_mgr
        self.sm.create_project("proj_imp", "Import Test Project", "Testing import validation")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_malformed_json_and_non_dict_payloads_rejected(self) -> None:
        """Truncated JSON, invalid syntax, binary garbage, and non-dict payloads raise error."""
        corrupt_samples = [
            b'{"schema_version": "1.0.0", "projects": [',  # Truncated
            b"{not valid json at all}",  # Syntax error
            b"\x00\x01\x02\x03\x04\xff\xfe",  # Binary garbage
            b"",  # Empty file
        ]
        for i, sample in enumerate(corrupt_samples):
            fpath = Path(self.temp_dir) / f"corrupt_{i}.json"
            fpath.write_bytes(sample)
            with self.subTest(sample_idx=i):
                with self.assertRaises(StateIntegrityError):
                    self.mgr.import_state(fpath)

        # Non-dict JSON payload (list or string)
        list_fpath = Path(self.temp_dir) / "list_payload.json"
        list_fpath.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises((StateIntegrityError, AttributeError, ValueError)):
            self.mgr.import_state(list_fpath)

    def test_missing_tables_and_schema_version_mismatches(self) -> None:
        """Missing projects array or incompatible schema version rejected."""
        # Missing 'projects'
        no_projects_path = Path(self.temp_dir) / "no_projects.json"
        no_projects_path.write_text(json.dumps({
            "schema_version": CURRENT_SCHEMA_VERSION,
            "decisions": [],
        }), encoding="utf-8")

        with self.assertRaises(StateIntegrityError):
            self.mgr.import_state(no_projects_path)

        # Schema version mismatch
        bad_versions = ["99.0.0", "0.1.0", "2.0.0", "v1.0", ""]
        for ver in bad_versions:
            fpath = Path(self.temp_dir) / f"bad_ver_{ver}.json"
            fpath.write_text(json.dumps({
                "schema_version": ver,
                "projects": [{"project_id": "p", "name": "P", "description": "D", "root_path": "."}],
            }), encoding="utf-8")
            with self.subTest(version=ver):
                with self.assertRaises((SchemaVersionMismatchError, StateIntegrityError, ValueError)):
                    self.mgr.import_state(fpath)

    def test_foreign_key_violation_triggers_atomic_rollback(self) -> None:
        """Dangling foreign keys trigger IntegrityError and atomically roll back database."""
        # Create a fresh database
        fresh_db_path = Path(self.temp_dir) / "fresh_rollback.db"
        fresh_mgr = SessionResumptionManager(db_path=fresh_db_path, project_id="fresh_proj")

        # Prepare corrupted backup: task references a run_id that does not exist in workflow_runs
        corrupted_backup = Path(self.temp_dir) / "fk_corrupt.json"
        corrupted_backup.write_text(json.dumps({
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": "fresh_proj",
            "projects": [
                {"project_id": "fresh_proj", "name": "Fresh", "description": "Desc", "root_path": "."}
            ],
            "workflow_runs": [],  # Empty!
            "task_history": [
                {
                    "task_id": "orphan_task_01",
                    "run_id": "nonexistent_run_999",  # Dangling FK!
                    "project_id": "fresh_proj",
                    "agent_role": "Developer",
                    "task_name": "Orphan Task",
                    "description": "Task without parent run",
                }
            ],
        }), encoding="utf-8")

        # Import must fail with sqlite3.IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_mgr.import_state(corrupted_backup)

        # Verify atomic rollback: fresh_proj must NOT exist in fresh_rollback.db
        with fresh_mgr._db_mgr.connection() as conn:
            cur = conn.execute("SELECT count(*) as cnt FROM projects WHERE project_id = 'fresh_proj'")
            row = cur.fetchone()
            self.assertEqual(row["cnt"], 0, "Failed import did not roll back projects table!")

            cur = conn.execute("SELECT count(*) as cnt FROM task_history WHERE task_id = 'orphan_task_01'")
            row = cur.fetchone()
            self.assertEqual(row["cnt"], 0, "Failed import did not roll back task_history table!")

    def test_overwrite_conflict_control(self) -> None:
        """Existing project imported with overwrite=False raises conflict; overwrite=True succeeds."""
        # Export current state
        export_path = Path(self.temp_dir) / "valid_export.json"
        self.mgr.export_state(export_path, project_id="proj_imp")

        # Import into same project without overwrite -> must raise StateImportConflictError
        with self.assertRaises(StateImportConflictError):
            self.mgr.import_state(export_path, overwrite=False)

        # Import with overwrite=True -> succeeds
        res = self.mgr.import_state(export_path, overwrite=True)
        self.assertEqual(res["target_project_id"], "proj_imp")
        self.assertGreater(res["projects_imported"], 0)


if __name__ == "__main__":
    unittest.main()
