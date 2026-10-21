"""
Adversarial Stress Test Suite for Milestone 1 Resumption & Cursor Fixes.
Author: Challenger M1 Retry 2 (Empirical Challenger)

Specifically tests:
1. Circular superseded decision imports (2-cycle A <-> B, 3-cycle A -> B -> C -> A, self-referential A -> A,
   cascade deletions, cycle-breaking updates, and dangling references).
2. 100+ rapid DatabaseManager.execute() queries fetching results across multiple threads
   (read-only concurrency, mixed read/write concurrency, in-memory concurrency,
   DetachedCursor DB-API compliance, and connection lifecycle).
"""

from __future__ import annotations

import concurrent.futures
import json
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

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES, CURRENT_SCHEMA_VERSION
from persistent_crewai.persistence.db import DatabaseManager, DetachedCursor, init_db
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    StateIntegrityError,
    StateImportConflictError,
)
from persistent_crewai.persistence.state_manager import StateManager


class TestCircularSupersededDecisionsAdversarial(unittest.TestCase):
    """Adversarial testing of circular and complex topological decision import graphs."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_circ_dec_")
        self.db_path = Path(self.temp_dir) / "circular_decisions.db"
        self.mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_circular")
        self.sm = self.mgr._state_mgr
        self.sm.create_project("proj_circular", "Circular Decisions Test", "Testing circular references")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _create_export_payload(self, decisions: List[Dict[str, Any]], project_id: str = "proj_circular") -> Dict[str, Any]:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": project_id,
            "projects": [
                {
                    "project_id": project_id,
                    "name": "Circular Project",
                    "description": "Project containing circular decisions",
                    "root_path": self.temp_dir,
                    "integrity_mode": "development",
                    "status": "active",
                }
            ],
            "decisions": decisions,
            "workflow_runs": [],
            "task_history": [],
            "milestones": [],
            "agent_status": [],
            "execution_traces": [],
            "memory_records": [],
            "schema_migrations": [
                {"version": CURRENT_SCHEMA_VERSION, "description": "Initial migration"}
            ],
        }

    def test_circular_two_decision_cycle_import_and_query(self) -> None:
        """Test import of Decision A superseded by Decision B, and Decision B superseded by Decision A.
        Verifies:
        - import_state succeeds cleanly without foreign key deadlock or violation
        - PRAGMA foreign_key_check returns 0 errors
        - StateManager.get_decisions() returns both decisions without infinite recursion
        - Decisions can be queried individually with exact superseded_by references intact
        - Subsequent export_state and re-import with overwrite=True preserves cycle and succeeds
        """
        decisions_data = [
            {
                "decision_id": "dec_circ_A",
                "project_id": "proj_circular",
                "agent_role": "Architect",
                "title": "Decision A: Use Monolith",
                "rationale": "Simple deployment initially",
                "implications": "May need microservices later",
                "status": "superseded",
                "tags": ["architecture", "monolith"],
                "superseded_by": "dec_circ_B",
            },
            {
                "decision_id": "dec_circ_B",
                "project_id": "proj_circular",
                "agent_role": "Architect",
                "title": "Decision B: Use Microservices",
                "rationale": "High scalability needed",
                "implications": "Complexity; might rollback to monolith",
                "status": "superseded",
                "tags": ["architecture", "microservices"],
                "superseded_by": "dec_circ_A",
            },
        ]

        payload = self._create_export_payload(decisions_data)
        import_file = Path(self.temp_dir) / "circular_2_cycle.json"
        with open(import_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        # Execute import into existing project with overwrite=True
        result = self.mgr.import_state(import_file, overwrite=True)
        self.assertEqual(result["decisions_imported"], 2)

        # 1. Oracle Verification: PRAGMA foreign_key_check
        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0, f"Foreign key violations found: {fk_violations}")

        # 2. Oracle Verification: Retrieve via StateManager
        all_decs = self.sm.get_decisions(project_id="proj_circular")
        self.assertEqual(len(all_decs), 2)

        dec_a = self.sm.get_decision("dec_circ_A")
        dec_b = self.sm.get_decision("dec_circ_B")

        self.assertIsNotNone(dec_a)
        self.assertIsNotNone(dec_b)
        self.assertEqual(dec_a["superseded_by"], "dec_circ_B")
        self.assertEqual(dec_b["superseded_by"], "dec_circ_A")
        self.assertEqual(dec_a["status"], "superseded")
        self.assertEqual(dec_b["status"], "superseded")

        # 3. Verify export of circular decisions and re-import
        re_export_file = Path(self.temp_dir) / "re_export_circular.json"
        export_summary = self.mgr.export_state(re_export_file, project_id="proj_circular")
        self.assertEqual(export_summary["decisions_count"], 2)

        # Re-import into fresh database
        fresh_db_path = Path(self.temp_dir) / "fresh_circular.db"
        fresh_mgr = SessionResumptionManager(db_path=fresh_db_path, project_id="proj_circular")
        re_result = fresh_mgr.import_state(re_export_file, overwrite=False)
        self.assertEqual(re_result["decisions_imported"], 2)

        fresh_fk_violations = fresh_mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fresh_fk_violations), 0)

    def test_circular_three_decision_cycle_import(self) -> None:
        """Test import of 3-cycle: Decision A -> B -> C -> A.
        Verifies transitive cycle is imported without ordering constraints.
        """
        decisions_data = [
            {
                "decision_id": "dec_tri_A",
                "project_id": "proj_circular",
                "agent_role": "Developer",
                "title": "Decision A (Alpha)",
                "rationale": "Initial alpha design",
                "implications": "Superseded by Beta",
                "status": "superseded",
                "superseded_by": "dec_tri_B",
            },
            {
                "decision_id": "dec_tri_B",
                "project_id": "proj_circular",
                "agent_role": "Developer",
                "title": "Decision B (Beta)",
                "rationale": "Secondary beta design",
                "implications": "Superseded by Gamma",
                "status": "superseded",
                "superseded_by": "dec_tri_C",
            },
            {
                "decision_id": "dec_tri_C",
                "project_id": "proj_circular",
                "agent_role": "Developer",
                "title": "Decision C (Gamma)",
                "rationale": "Tertiary gamma design",
                "implications": "Loops back to Alpha",
                "status": "superseded",
                "superseded_by": "dec_tri_A",
            },
        ]

        payload = self._create_export_payload(decisions_data)
        import_file = Path(self.temp_dir) / "circular_3_cycle.json"
        with open(import_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        result = self.mgr.import_state(import_file, overwrite=True)
        self.assertEqual(result["decisions_imported"], 3)

        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0)

        dec_a = self.sm.get_decision("dec_tri_A")
        dec_b = self.sm.get_decision("dec_tri_B")
        dec_c = self.sm.get_decision("dec_tri_C")

        self.assertEqual(dec_a["superseded_by"], "dec_tri_B")
        self.assertEqual(dec_b["superseded_by"], "dec_tri_C")
        self.assertEqual(dec_c["superseded_by"], "dec_tri_A")

    def test_self_referencing_decision_import(self) -> None:
        """Test import of self-referencing decision: Decision A superseded by Decision A."""
        decisions_data = [
            {
                "decision_id": "dec_self",
                "project_id": "proj_circular",
                "agent_role": "Reviewer",
                "title": "Self-referencing Decision",
                "rationale": "Revised in-place",
                "implications": "Self loop",
                "status": "superseded",
                "superseded_by": "dec_self",
            }
        ]

        payload = self._create_export_payload(decisions_data)
        import_file = Path(self.temp_dir) / "self_ref_decision.json"
        with open(import_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        result = self.mgr.import_state(import_file, overwrite=True)
        self.assertEqual(result["decisions_imported"], 1)

        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0)

        dec = self.sm.get_decision("dec_self")
        self.assertEqual(dec["superseded_by"], "dec_self")

    def test_circular_decision_deletion_and_set_null_cascade(self) -> None:
        """Test deleting one node in a circular pair (A <-> B).
        Because foreign key is ON DELETE SET NULL, deleting A must:
        - Delete A
        - Automatically set B's superseded_by to NULL without error
        - Leave B in a valid state
        """
        self.test_circular_two_decision_cycle_import_and_query()

        # Delete dec_circ_A
        with self.mgr._db_mgr.connection() as conn:
            conn.execute("DELETE FROM decisions WHERE decision_id = 'dec_circ_A'")

        # Verify dec_circ_A is gone
        dec_a = self.sm.get_decision("dec_circ_A")
        self.assertIsNone(dec_a)

        # Verify dec_circ_B is present with superseded_by = None
        dec_b = self.sm.get_decision("dec_circ_B")
        self.assertIsNotNone(dec_b)
        self.assertIsNone(dec_b["superseded_by"], "Deleting A must trigger ON DELETE SET NULL for B's superseded_by")

        # Verify integrity and foreign key validity
        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0)

    def test_circular_decision_break_cycle_via_update(self) -> None:
        """Test breaking a circular reference using update_decision_status."""
        self.test_circular_two_decision_cycle_import_and_query()

        # Break cycle by making A accepted with no superseded_by
        updated = self.sm.update_decision_status("dec_circ_A", new_status="accepted", superseded_by=None)
        self.assertEqual(updated["status"], "accepted")
        self.assertIsNone(updated["superseded_by"])

        # B still points to A
        dec_b = self.sm.get_decision("dec_circ_B")
        self.assertEqual(dec_b["superseded_by"], "dec_circ_A")

        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0)

    def test_dangling_superseded_reference_raises_foreign_key_error(self) -> None:
        """Test that importing a decision pointing to a NON-EXISTENT decision ID fails
        with a foreign key integrity error and rolls back atomically.
        """
        dangling_decisions = [
            {
                "decision_id": "dec_dangling",
                "project_id": "proj_circular",
                "agent_role": "Architect",
                "title": "Dangling Decision",
                "rationale": "Points to ghost decision",
                "implications": "Should fail import",
                "status": "superseded",
                "superseded_by": "NON_EXISTENT_DECISION_ID_9999",
            }
        ]

        payload = self._create_export_payload(dangling_decisions)
        import_file = Path(self.temp_dir) / "dangling_reference.json"
        with open(import_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        with self.assertRaises(sqlite3.IntegrityError):
            self.mgr.import_state(import_file, overwrite=True)

        # Oracle check: Database must not contain dec_dangling
        self.assertIsNone(self.sm.get_decision("dec_dangling"))

    def test_giant_100_node_circular_decision_cycle(self) -> None:
        """Test import of large 100-node circular decision graph (0 -> 1 -> ... -> 99 -> 0).
        Verifies:
        - 100-node circular graph imports with 0 foreign key violations
        - rehydrate_session succeeds
        - export_state outputs all 100 decisions
        """
        n = 100
        decs = []
        for i in range(n):
            next_i = (i + 1) % n
            decs.append({
                "decision_id": f"dec_giant_{i}",
                "project_id": "proj_circular",
                "agent_role": "Architect",
                "title": f"Decision {i}",
                "rationale": f"Rationale {i}",
                "status": "superseded",
                "superseded_by": f"dec_giant_{next_i}",
            })

        payload = self._create_export_payload(decs)
        import_file = Path(self.temp_dir) / "giant_cycle.json"
        with open(import_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        res = self.mgr.import_state(import_file, overwrite=True)
        self.assertEqual(res["decisions_imported"], 100)

        fk_violations = self.mgr._db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_violations), 0)

        ctx = self.mgr.rehydrate_session()
        self.assertIn("Circular Project", ctx.summary)

        export_file = Path(self.temp_dir) / "giant_export.json"
        exp = self.mgr.export_state(export_file, project_id="proj_circular")
        self.assertEqual(exp["decisions_count"], 100)



class TestMultithreadedRapidDetachedCursorExecutionAdversarial(unittest.TestCase):
    """Adversarial stress-testing of DatabaseManager.execute() and DetachedCursor under concurrent multithreading."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_concur_exec_")
        self.db_path = Path(self.temp_dir) / "concurrent_exec.db"
        self.db_mgr = init_db(self.db_path)
        # Prepopulate with 20 projects for querying
        with self.db_mgr.connection() as conn:
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO projects (project_id, name, description, root_path, integrity_mode, status)
                    VALUES (?, ?, ?, ?, 'development', 'active')
                    """,
                    (f"proj_batch_{i}", f"Project Batch {i}", f"Description for project {i}", f"/workspace/{i}"),
                )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_100_rapid_concurrent_select_queries_shared_manager(self) -> None:
        """Test 100+ rapid DatabaseManager.execute() SELECT queries across 10 threads using
        a single shared DatabaseManager instance.
        Verifies:
        - fetchall(), fetchone(), fetchmany(), and iteration return accurate rows
        - No 'database is locked' errors occur
        - No 'SQLite objects created in a thread can only be used in that same thread' errors occur
        - DetachedCursor properties (rowcount, description) are valid
        """
        num_threads = 10
        queries_per_thread = 20  # Total = 200 queries (> 100)
        errors: list[Exception] = []
        total_rows_fetched = [0] * num_threads

        def worker(thread_idx: int) -> None:
            local_count = 0
            for i in range(queries_per_thread):
                try:
                    # Alternating query styles across iterations
                    style = i % 4
                    target_id = f"proj_batch_{i % 20}"

                    if style == 0:
                        # style 0: fetchall()
                        cur = self.db_mgr.execute(
                            "SELECT * FROM projects WHERE project_id = ?", (target_id,)
                        )
                        self.assertIsInstance(cur, DetachedCursor)
                        self.assertIsNotNone(cur.description)
                        rows = cur.fetchall()
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0]["project_id"], target_id)
                        # Exhausted cursor returns empty list
                        self.assertEqual(cur.fetchall(), [])
                        local_count += len(rows)

                    elif style == 1:
                        # style 1: fetchone()
                        cur = self.db_mgr.execute(
                            "SELECT project_id, name FROM projects ORDER BY project_id ASC"
                        )
                        row1 = cur.fetchone()
                        row2 = cur.fetchone()
                        self.assertIsNotNone(row1)
                        self.assertIsNotNone(row2)
                        self.assertNotEqual(row1["project_id"], row2["project_id"])
                        remaining = cur.fetchall()
                        self.assertEqual(len(remaining), 18)
                        local_count += 20

                    elif style == 2:
                        # style 2: fetchmany(5)
                        cur = self.db_mgr.execute("SELECT * FROM projects LIMIT 10")
                        batch1 = cur.fetchmany(5)
                        batch2 = cur.fetchmany(5)
                        batch3 = cur.fetchmany(5)
                        self.assertEqual(len(batch1), 5)
                        self.assertEqual(len(batch2), 5)
                        self.assertEqual(len(batch3), 0)
                        local_count += 10

                    elif style == 3:
                        # style 3: iteration over DetachedCursor
                        cur = self.db_mgr.execute(
                            "SELECT project_id FROM projects WHERE status = 'active'"
                        )
                        row_ids = [r["project_id"] for r in cur]
                        self.assertEqual(len(row_ids), 20)
                        local_count += len(row_ids)

                except Exception as e:
                    errors.append(e)
            total_rows_fetched[thread_idx] = local_count

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, idx) for idx in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Thread errors occurred: {errors}")
        self.assertGreater(sum(total_rows_fetched), 1000)

    def test_100_rapid_concurrent_mixed_read_write_queries(self) -> None:
        """Test 100+ rapid mixed read and write queries via DatabaseManager.execute()
        across 10 concurrent threads hammering on-disk SQLite.
        Verifies:
        - 5 writer threads each perform 15 INSERTs via execute() (75 writes)
        - 5 reader threads each perform 25 SELECTs via execute() (125 reads)
        - Total queries = 200 (> 100)
        - DetachedCursor.rowcount on INSERT is 1
        - All inserted records exist in database
        - Database integrity check is 'ok'
        """
        num_writers = 5
        num_readers = 5
        writes_per_thread = 15
        reads_per_thread = 25
        errors: list[Exception] = []
        successful_inserts = [0] * num_writers
        successful_reads = [0] * num_readers

        def writer_worker(idx: int) -> None:
            count = 0
            for i in range(writes_per_thread):
                try:
                    dec_id = f"dec_concur_w{idx}_{i}"
                    proj_id = f"proj_batch_{i % 20}"
                    cur = self.db_mgr.execute(
                        """
                        INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
                        VALUES (?, ?, 'Developer', ?, 'Adversarial rapid execute test', 'None')
                        """,
                        (dec_id, proj_id, f"Decision {idx}-{i}"),
                    )
                    self.assertIsInstance(cur, DetachedCursor)
                    self.assertEqual(cur.rowcount, 1)
                    count += 1
                except Exception as e:
                    errors.append(e)
            successful_inserts[idx] = count

        def reader_worker(idx: int) -> None:
            count = 0
            for i in range(reads_per_thread):
                try:
                    proj_id = f"proj_batch_{i % 20}"
                    cur = self.db_mgr.execute(
                        "SELECT COUNT(*) AS cnt FROM decisions WHERE project_id = ?",
                        (proj_id,),
                    )
                    self.assertIsInstance(cur, DetachedCursor)
                    row = cur.fetchone()
                    self.assertIsNotNone(row)
                    self.assertGreaterEqual(row["cnt"], 0)
                    count += 1
                except Exception as e:
                    errors.append(e)
            successful_reads[idx] = count

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_writers + num_readers) as executor:
            writer_futures = [executor.submit(writer_worker, i) for i in range(num_writers)]
            reader_futures = [executor.submit(reader_worker, i) for i in range(num_readers)]
            for f in concurrent.futures.as_completed(writer_futures + reader_futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Errors encountered during mixed concurrency: {errors}")
        self.assertEqual(sum(successful_inserts), num_writers * writes_per_thread)
        self.assertEqual(sum(successful_reads), num_readers * reads_per_thread)

        # Oracle check: verify total decisions in DB matches exact number of inserts
        total_dec = self.db_mgr.fetchone("SELECT COUNT(*) AS cnt FROM decisions")["cnt"]
        self.assertEqual(total_dec, num_writers * writes_per_thread)

        # Integrity check
        check = self.db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")

    def test_detached_cursor_full_dbapi_compliance_and_context_manager(self) -> None:
        """Test DetachedCursor compliance with standard Python DB-API expectations:
        - context manager (with cur as c:)
        - close() is safe no-op
        - rowcount, lastrowid, description types
        - empty queries (description is None)
        """
        # DML query
        cur_dml = self.db_mgr.execute(
            """
            INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
            VALUES ('dec_dbapi_1', 'proj_batch_0', 'Architect', 'DBAPI Title', 'Rationale', 'None')
            """
        )
        self.assertEqual(cur_dml.rowcount, 1)
        self.assertIsNone(cur_dml.description)
        self.assertEqual(cur_dml.fetchall(), [])
        cur_dml.close()  # Must not raise

        # Context manager usage
        with self.db_mgr.execute("SELECT * FROM decisions WHERE decision_id = 'dec_dbapi_1'") as cur:
            self.assertIsInstance(cur, DetachedCursor)
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["decision_id"], "dec_dbapi_1")
            self.assertIsNone(cur.fetchone())

    def test_100_rapid_queries_in_memory_database_thread_safety(self) -> None:
        """Test 100+ rapid execute() calls against an in-memory database (:memory:) across multiple threads.
        Verifies that thread locking (_lock) protects the shared in-memory connection.
        """
        mem_mgr = init_db(":memory:")
        num_threads = 8
        queries_per_thread = 20  # 160 queries (> 100)
        errors: list[Exception] = []

        # Seed data
        for i in range(10):
            mem_mgr.execute(
                "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
                (f"mem_proj_{i}", f"Mem Project {i}", "Desc", "/tmp"),
            )

        def mem_worker(thread_idx: int) -> None:
            for i in range(queries_per_thread):
                try:
                    cur = mem_mgr.execute("SELECT * FROM projects WHERE project_id = ?", (f"mem_proj_{i % 10}",))
                    rows = cur.fetchall()
                    self.assertEqual(len(rows), 1)
                except Exception as e:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(mem_worker, i) for i in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"In-memory threading errors: {errors}")

    def test_50_threads_1000_rapid_queries_stress(self) -> None:
        """High-scale concurrency test: 50 concurrent threads executing 20 queries each (1,000 queries total)
        against on-disk SQLite with WAL mode via DatabaseManager.execute().
        Verifies zero connection locks, zero dropped queries, and complete thread safety.
        """
        num_threads = 50
        queries_per_thread = 20  # 1,000 queries total
        errors: list[Exception] = []

        def worker(th_idx: int) -> None:
            for q in range(queries_per_thread):
                try:
                    cur = self.db_mgr.execute(
                        "SELECT project_id, name FROM projects WHERE project_id = ?",
                        (f"proj_batch_{(th_idx + q) % 20}",),
                    )
                    rows = cur.fetchall()
                    if len(rows) != 1:
                        errors.append(ValueError(f"Expected 1 row, got {len(rows)}"))
                except Exception as e:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Errors in 50-thread 1000-query stress test: {errors}")



if __name__ == "__main__":
    unittest.main()
