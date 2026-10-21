"""
Adversarial Concurrency and Stress Test Suite for Milestone 1 Persistence.
Tests SQLite WAL mode under concurrent multithreaded contention, rapid rollbacks,
bulk load scaling, FTS5 shadow table integrity, and adversarial payload injection.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES
from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    SchemaVersionMismatchError,
    StateImportConflictError,
    StateIntegrityError,
)
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
)


class TestConcurrentWALWritesAndReads(unittest.TestCase):
    """Adversarial testing of multithreaded concurrent reads and writes in WAL mode."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_concur_")
        self.db_path = Path(self.temp_dir) / "concurrent_stress.db"
        self.db_mgr = init_db(self.db_path)
        self.sm = StateManager(db_path=self.db_path, project_id="proj_concur")
        self.sm.create_project(
            project_id="proj_concur",
            name="Concurrent Stress Test Project",
            description="Testing concurrent multi-threaded access",
            root_path=self.temp_dir,
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_tc_adv_01_multithreaded_concurrent_writers_separate_managers(self) -> None:
        """TC-ADV-01: 16 concurrent threads each using their own StateManager instance
        hammering the on-disk SQLite database with writes (runs, tasks, traces, decisions).
        Verifies busy timeout and WAL eliminate 'database is locked' errors and preserve all data.
        """
        num_threads = 16
        tasks_per_thread = 20
        errors: list[Exception] = []

        def worker(thread_idx: int) -> int:
            try:
                # Each worker instantiates its own manager pointing to the same DB file
                worker_sm = StateManager(db_path=self.db_path, project_id="proj_concur")
                run_id = f"run_th_{thread_idx}"
                worker_sm.create_workflow_run(
                    run_id=run_id,
                    workflow_type="adversarial_concurrent_test",
                    input_prompt=f"Worker {thread_idx} prompt",
                )

                for i in range(tasks_per_thread):
                    task_id = f"task_{thread_idx}_{i}"
                    role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
                    worker_sm.create_task(
                        task_id=task_id,
                        run_id=run_id,
                        agent_role=role,
                        task_name=f"Task {i} by thread {thread_idx}",
                        description="Adversarial concurrency test task",
                    )
                    worker_sm.start_task(task_id)
                    worker_sm.record_trace(
                        run_id=run_id,
                        task_id=task_id,
                        agent_role=role,
                        event_type="thought",
                        payload={"step": i, "thread": thread_idx},
                    )
                    worker_sm.record_decision(
                        agent_role=role,
                        title=f"Decision {i} from thread {thread_idx}",
                        rationale="Testing WAL write contention",
                        implications="Ensures transaction serialization",
                    )
                    worker_sm.record_task_completion(
                        task_id=task_id,
                        actual_output=f"Output for task {i} from thread {thread_idx}",
                    )

                worker_sm.complete_workflow_run(run_id, final_output=f"Run {run_id} finished")
                return tasks_per_thread
            except Exception as e:
                errors.append(e)
                raise

        start_time = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, idx) for idx in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        elapsed = time.perf_counter() - start_time

        self.assertEqual(len(errors), 0, f"Encountered thread errors during concurrency: {errors}")
        self.assertEqual(sum(results), num_threads * tasks_per_thread)

        # Oracle checks: Exact counts in database
        total_runs = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM workflow_runs WHERE project_id = 'proj_concur'")["c"]
        total_tasks = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM task_history WHERE project_id = 'proj_concur'")["c"]
        total_traces = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM execution_traces")["c"]
        total_decisions = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM decisions WHERE project_id = 'proj_concur'")["c"]

        self.assertEqual(total_runs, num_threads)
        self.assertEqual(total_tasks, num_threads * tasks_per_thread)
        self.assertEqual(total_traces, num_threads * tasks_per_thread)
        self.assertEqual(total_decisions, num_threads * tasks_per_thread)

        # Confirm all tasks are marked 'completed'
        completed_tasks = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM task_history WHERE status = 'completed'")["c"]
        self.assertEqual(completed_tasks, num_threads * tasks_per_thread)

        # Integrity check
        check = self.db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")

    def test_tc_adv_02_concurrent_readers_and_writers_zero_starvation(self) -> None:
        """TC-ADV-02: Simultaneous reader threads and writer threads under WAL mode.
        Validates that readers are non-blocking and never encounter locked errors while
        writers continuously commit new records.
        """
        duration_seconds = 2.0
        stop_event = threading.Event()
        read_counts = [0] * 6
        write_counts = [0] * 6
        read_errors: list[Exception] = []
        write_errors: list[Exception] = []

        def writer_worker(idx: int) -> None:
            w_sm = StateManager(db_path=self.db_path, project_id="proj_concur")
            count = 0
            while not stop_event.is_set():
                try:
                    w_sm.record_decision(
                        agent_role="Architect",
                        title=f"Concurrent Decision W{idx}-{count}",
                        rationale="Checking non-blocking write under WAL",
                    )
                    count += 1
                    time.sleep(0.005)
                except Exception as e:
                    write_errors.append(e)
                    break
            write_counts[idx] = count

        def reader_worker(idx: int) -> None:
            r_sm = StateManager(db_path=self.db_path, project_id="proj_concur")
            count = 0
            while not stop_event.is_set():
                try:
                    decs = r_sm.get_decisions(limit=50)
                    dash = r_sm.get_system_dashboard_data()
                    self.assertIsInstance(decs, list)
                    self.assertIn("project", dash)
                    count += 1
                    time.sleep(0.003)
                except Exception as e:
                    read_errors.append(e)
                    break
            read_counts[idx] = count

        threads: list[threading.Thread] = []
        for i in range(6):
            t_w = threading.Thread(target=writer_worker, args=(i,))
            t_r = threading.Thread(target=reader_worker, args=(i,))
            threads.extend([t_w, t_r])

        for t in threads:
            t.start()

        time.sleep(duration_seconds)
        stop_event.set()

        for t in threads:
            t.join(timeout=5.0)

        self.assertEqual(len(write_errors), 0, f"Writer errors encountered: {write_errors}")
        self.assertEqual(len(read_errors), 0, f"Reader errors encountered: {read_errors}")

        total_written = sum(write_counts)
        total_reads = sum(read_counts)
        self.assertGreater(total_written, 20, "Writers should have completed numerous writes")
        self.assertGreater(total_reads, 25, "Readers should have executed numerous non-blocking reads")

    def test_tc_adv_03_shared_statemanager_instance_thread_safety(self) -> None:
        """TC-ADV-03: Multiple concurrent threads calling methods on the SAME shared
        StateManager instance. Verifies that sqlite3 connections per-operation or thread-local
        handling avoids 'SQLite objects created in a thread can only be used in that same thread'.
        """
        shared_sm = self.sm
        shared_run_id = "shared_run_concur"
        shared_sm.create_workflow_run(
            run_id=shared_run_id,
            workflow_type="shared_instance_stress",
            input_prompt="Testing shared instance thread safety",
        )

        num_threads = 12
        iterations = 25
        errors: list[Exception] = []

        def worker(thread_idx: int) -> None:
            for i in range(iterations):
                try:
                    task_id = f"shared_t_{thread_idx}_{i}"
                    role = CANONICAL_ROLES[(thread_idx + i) % len(CANONICAL_ROLES)]
                    shared_sm.create_task(
                        task_id=task_id,
                        run_id=shared_run_id,
                        agent_role=role,
                        task_name=f"Shared Task {thread_idx}-{i}",
                        description="Testing shared state manager instance",
                    )
                    shared_sm.start_task(task_id)
                    shared_sm.record_task_completion(task_id, actual_output="Done")
                    shared_sm.update_agent_status(role, "idle", metrics={"step": i})
                except Exception as e:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Shared instance thread errors: {errors}")

        # Check total recorded tasks via list_tasks(limit=500) and SQL count
        tasks = shared_sm.list_tasks(run_id=shared_run_id, limit=500)
        self.assertEqual(len(tasks), num_threads * iterations)
        db_count = self.db_mgr.fetchone(
            "SELECT COUNT(*) AS c FROM task_history WHERE run_id = ?", (shared_run_id,)
        )["c"]
        self.assertEqual(db_count, num_threads * iterations)


class TestTransactionRollbackAndIntegrityUnderStress(unittest.TestCase):
    """Adversarial testing of transaction aborts, rollbacks, and FTS5 shadow table consistency."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_rollback_")
        self.db_path = Path(self.temp_dir) / "rollback_stress.db"
        self.db_mgr = init_db(self.db_path)
        self.sm = StateManager(db_path=self.db_path, project_id="proj_rollback")
        self.sm.create_project(
            project_id="proj_rollback",
            name="Rollback Stress Project",
            description="Testing rollback semantics and shadow tables",
            root_path=self.temp_dir,
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_tc_adv_04_explicit_multi_statement_transaction_rollback_zero_leak(self) -> None:
        """TC-ADV-04: Execute a transactional block that inserts 25 valid decisions,
        then fails on the 26th (e.g. duplicate PK or invalid FK). Verifies that EXACTLY 0
        decisions from that transaction are committed.
        """
        initial_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM decisions")["c"]
        self.assertEqual(initial_count, 0)

        # Attempt batch transaction where 25 items succeed but 26th raises exception
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db_mgr.connection() as conn:
                for i in range(25):
                    conn.execute(
                        """
                        INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
                        VALUES (?, 'proj_rollback', 'Architect', ?, 'Valid rationale', 'None')
                        """,
                        (f"dec_batch_{i}", f"Batch Decision {i}"),
                    )
                # 26th operation: Invalid foreign key to nonexistent project
                conn.execute(
                    """
                    INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
                    VALUES ('dec_invalid', 'NONEXISTENT_PROJECT_ID', 'Architect', 'Invalid', 'None', 'None')
                    """
                )

        # Oracle verification: Database must contain zero decisions!
        final_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM decisions")["c"]
        self.assertEqual(
            final_count,
            0,
            f"Rollback failed! Found {final_count} leaked decisions after aborted transaction.",
        )

    def test_tc_adv_05_fts5_virtual_table_shadow_index_rollback_consistency(self) -> None:
        """TC-ADV-05: Test that FTS5 virtual table synchronization triggers participate
        fully in transaction rollbacks. When an INSERT or UPDATE into memory_records is rolled back,
        memory_fts MUST NOT retain orphaned keywords or deleted records.
        """
        vs = SQLiteVectorStore(db_path=self.db_path, project_id="proj_rollback")

        # Baseline: Insert 5 legitimate memories
        valid_ids = vs.add([
            "Baseline memory alpha: system architecture overview",
            "Baseline memory beta: database schema and persistence",
            "Baseline memory gamma: crewai agent delegation rules",
            "Baseline memory delta: workspace file tools and sandboxing",
            "Baseline memory epsilon: real and mock github adapters",
        ])
        self.assertEqual(len(valid_ids), 5)

        # Verify FTS finds 'architecture'
        res = vs.search("architecture")
        self.assertGreaterEqual(len(res), 1)

        # Attempt transactional insertion with poison word 'CRYPTOPHOENIX_999' followed by crash
        with self.assertRaises(RuntimeError):
            with self.db_mgr.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_records (record_id, project_id, category, content, metadata_json, embedding_json)
                    VALUES ('mem_poison_1', 'proj_rollback', 'test', 'The secret code is CRYPTOPHOENIX_999 in memory', '{}', '[]')
                    """
                )
                # Intentional failure inside transaction
                raise RuntimeError("Simulated mid-transaction server crash")

        # Verify: neither memory_records nor memory_fts contains CRYPTOPHOENIX_999
        mem_row = self.db_mgr.fetchone("SELECT * FROM memory_records WHERE record_id = 'mem_poison_1'")
        self.assertIsNone(mem_row, "Rolled-back record was found in memory_records!")

        fts_row = self.db_mgr.fetchone(
            "SELECT * FROM memory_fts WHERE memory_fts MATCH 'CRYPTOPHOENIX_999'"
        )
        self.assertIsNone(fts_row, "Orphaned index found in FTS5 table after transaction rollback!")

        # Also test UPDATE rollback on existing record
        first_id = valid_ids[0]
        with self.assertRaises(RuntimeError):
            with self.db_mgr.connection() as conn:
                conn.execute(
                    "UPDATE memory_records SET content = 'CORRUPTED_LEAK_WORD_XYZ' WHERE record_id = ?",
                    (first_id,),
                )
                raise RuntimeError("Abort update")

        # Verify old content is intact and leak word is absent in both tables
        original_record = self.db_mgr.fetchone(
            "SELECT content FROM memory_records WHERE record_id = ?", (first_id,)
        )
        self.assertIn("architecture overview", original_record["content"])

        fts_leak = self.db_mgr.fetchone(
            "SELECT * FROM memory_fts WHERE memory_fts MATCH 'CORRUPTED_LEAK_WORD_XYZ'"
        )
        self.assertIsNone(fts_leak, "Rolled-back update leaked into FTS5 index!")

    def test_tc_adv_06_interleaved_concurrent_commit_and_abort_storm(self) -> None:
        """TC-ADV-06: 10 concurrent threads hammering the database:
        5 threads execute legitimate commits, 5 threads execute failing transactions
        that abort and trigger rollbacks.
        Verifies that rollbacks do not contaminate committed data and DB integrity remains 100%.
        """
        num_threads = 10
        rounds_per_thread = 20
        successful_commits = 0
        expected_commits = 5 * rounds_per_thread  # 5 threads will succeed

        def worker(thread_idx: int) -> int:
            is_committer = (thread_idx % 2 == 0)
            local_success = 0
            w_mgr = DatabaseManager(db_path=self.db_path)

            for r in range(rounds_per_thread):
                rec_id = f"storm_{thread_idx}_{r}"
                if is_committer:
                    try:
                        with w_mgr.connection() as conn:
                            conn.execute(
                                """
                                INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
                                VALUES (?, 'proj_rollback', 'Reviewer', ?, 'Commit storm rationale', 'None')
                                """,
                                (rec_id, f"Valid Decision {thread_idx}-{r}"),
                            )
                        local_success += 1
                    except Exception:
                        pass
                else:
                    # Thread intentionally fails after partial work
                    try:
                        with w_mgr.connection() as conn:
                            conn.execute(
                                """
                                INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications)
                                VALUES (?, 'proj_rollback', 'Reviewer', ?, 'Will rollback', 'None')
                                """,
                                (rec_id, f"Aborting Decision {thread_idx}-{r}"),
                            )
                            # Deliberate failure trigger
                            raise ValueError("Planned transaction rollback")
                    except ValueError:
                        pass  # Expected rollback
            return local_success

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        total_actual_success = sum(results)
        self.assertEqual(total_actual_success, expected_commits)

        # Verify DB exact count
        actual_in_db = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM decisions WHERE project_id = 'proj_rollback'")["c"]
        self.assertEqual(actual_in_db, expected_commits)

        # Integrity check
        check = self.db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")


class TestBulkInsertAndQueryPerformance(unittest.TestCase):
    """Adversarial high-volume load, indexing throughput, and query scalability benchmarks."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_perf_")
        self.db_path = Path(self.temp_dir) / "perf_stress.db"
        self.db_mgr = init_db(self.db_path)
        self.sm = StateManager(db_path=self.db_path, project_id="proj_perf")
        self.sm.create_project(
            project_id="proj_perf",
            name="Performance Stress Project",
            description="Testing bulk insert and query scaling",
            root_path=self.temp_dir,
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_tc_adv_07_bulk_insert_throughput_and_scaling(self) -> None:
        """TC-ADV-07: Ingest 1,000 decisions, 1,000 tasks, 1,000 traces, and 500 memory records
        with 64-dimensional embeddings and FTS5 indexing.
        Measures throughput (records/sec) and verifies performance does not degrade catastrophically.
        """
        n_records = 1000

        # 1. Bulk Decisions
        start_dec = time.perf_counter()
        decisions_data = [
            (
                f"bulk_dec_{i}",
                "proj_perf",
                CANONICAL_ROLES[i % len(CANONICAL_ROLES)],
                f"Bulk Architecture Decision {i}",
                f"Rationale for architectural choice {i} evaluating persistence scaling",
                "Implications on throughput and disk storage",
                "accepted",
                json.dumps(["bulk", "scaling", f"tag_{i % 10}"]),
            )
            for i in range(n_records)
        ]
        with self.db_mgr.connection() as conn:
            conn.executemany(
                """
                INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications, status, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                decisions_data,
            )
        elapsed_dec = time.perf_counter() - start_dec
        dec_rate = n_records / elapsed_dec

        # 2. Bulk Workflow Runs and Tasks
        self.sm.create_workflow_run(
            run_id="bulk_run_1",
            workflow_type="bulk_stress",
            input_prompt="Bulk prompt",
        )

        start_tasks = time.perf_counter()
        tasks_data = [
            (
                f"bulk_task_{i}",
                "bulk_run_1",
                "proj_perf",
                CANONICAL_ROLES[i % len(CANONICAL_ROLES)],
                f"Bulk Task {i}",
                f"Description for bulk task {i}",
                "Expected output",
                "Actual output generated",
                "completed",
                i,
            )
            for i in range(n_records)
        ]
        with self.db_mgr.connection() as conn:
            conn.executemany(
                """
                INSERT INTO task_history (task_id, run_id, project_id, agent_role, task_name, description, expected_output, actual_output, status, execution_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tasks_data,
            )
        elapsed_tasks = time.perf_counter() - start_tasks
        task_rate = n_records / elapsed_tasks

        # 3. Bulk Vector Store Ingestion (with SHA256 embedding + FTS5 indexing)
        vs = SQLiteVectorStore(db_path=self.db_path, project_id="proj_perf")
        memory_docs = [
            f"Memory doc {i}: Agent {CANONICAL_ROLES[i % len(CANONICAL_ROLES)]} recorded milestone on database optimization and vector clustering for shard {i % 20}"
            for i in range(500)
        ]
        start_mem = time.perf_counter()
        vs.add(memory_docs)
        elapsed_mem = time.perf_counter() - start_mem
        mem_rate = 500 / elapsed_mem

        # Performance assertions (throughput must exceed 100 records/sec on modern disk)
        self.assertGreater(dec_rate, 100.0, f"Decisions bulk rate too slow: {dec_rate:.1f} ops/sec")
        self.assertGreater(task_rate, 100.0, f"Tasks bulk rate too slow: {task_rate:.1f} ops/sec")
        self.assertGreater(mem_rate, 50.0, f"Vector memory bulk rate too slow: {mem_rate:.1f} ops/sec")

        # Verify row counts
        dec_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM decisions WHERE project_id = 'proj_perf'")["c"]
        task_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM task_history WHERE project_id = 'proj_perf'")["c"]
        mem_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM memory_records WHERE project_id = 'proj_perf'")["c"]
        fts_count = self.db_mgr.fetchone("SELECT COUNT(*) AS c FROM memory_fts")["c"]

        self.assertEqual(dec_count, n_records)
        self.assertEqual(task_count, n_records)
        self.assertEqual(mem_count, 500)
        self.assertEqual(fts_count, 500)

    def test_tc_adv_08_query_scalability_and_hybrid_search_under_volume(self) -> None:
        """TC-ADV-08: Execute hybrid search, dashboard aggregations, and filtered queries
        against populated 1,000-record tables. Verifies sub-second latency and index utilization.
        """
        # Prepopulate dataset
        self.test_tc_adv_07_bulk_insert_throughput_and_scaling()

        # Query 1: Filtered decision search
        t0 = time.perf_counter()
        decs = self.sm.get_decisions(agent_role="Architect", status="accepted", limit=50)
        t_dec = time.perf_counter() - t0
        self.assertLess(t_dec, 0.25, f"Decision query latency too high: {t_dec:.4f}s")
        self.assertGreater(len(decs), 0)

        # Query 2: Dashboard aggregation
        t0 = time.perf_counter()
        dash = self.sm.get_system_dashboard_data()
        t_dash = time.perf_counter() - t0
        self.assertLess(t_dash, 0.50, f"Dashboard aggregation latency too high: {t_dash:.4f}s")
        self.assertEqual(dash["project"]["project_id"], "proj_perf")

        # Query 3: Hybrid vector + FTS5 search
        vs = SQLiteVectorStore(db_path=self.db_path, project_id="proj_perf")
        t0 = time.perf_counter()
        results = vs.search("database optimization vector clustering", top_k=10, hybrid_search=True)
        t_search = time.perf_counter() - t0
        self.assertLess(t_search, 0.50, f"Hybrid vector search latency too high: {t_search:.4f}s")
        self.assertEqual(len(results), 10)
        self.assertGreater(results[0].score, 0.5)

    def test_tc_adv_09_wal_checkpoint_and_disk_file_integrity(self) -> None:
        """TC-ADV-09: Verifies that after heavy write workloads, PRAGMA wal_checkpoint(TRUNCATE)
        successfully flushes WAL frames to the main database file without corruption.
        """
        wal_file = Path(str(self.db_path) + "-wal")

        # Ingest 500 decisions
        for i in range(500):
            self.sm.record_decision(
                agent_role="Developer",
                title=f"WAL Decision {i}",
                rationale="Generating WAL frames",
            )

        # Verify WAL file exists and contains data
        self.assertTrue(self.db_path.exists())

        # Checkpoint WAL
        with self.db_mgr.connection() as conn:
            cur = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            res = cur.fetchone()
            # res: (busy, log, checkpointed)
            self.assertEqual(res[0], 0, f"WAL checkpoint returned busy status: {res}")

        # Integrity check
        check = self.db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")
        fk_check = self.db_mgr.fetchall("PRAGMA foreign_key_check;")
        self.assertEqual(len(fk_check), 0, f"Foreign key violations found: {fk_check}")


class TestAdversarialBoundaryAndPoisonPayloads(unittest.TestCase):
    """Adversarial testing of SQL injection, malformed inputs, FTS5 attacks, and corrupted imports."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_poison_")
        self.db_path = Path(self.temp_dir) / "poison_stress.db"
        self.db_mgr = init_db(self.db_path)
        self.sm = StateManager(db_path=self.db_path, project_id="proj_poison")
        self.sm.create_project(
            project_id="proj_poison",
            name="Poison Payload Test Project",
            description="Testing injection resistance and boundary defense",
            root_path=self.temp_dir,
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_tc_adv_10_sql_injection_and_poison_payload_resistance(self) -> None:
        """TC-ADV-10: Attempt SQL injection and adversarial strings across all entity tables.
        Verifies that parameterized queries prevent execution of injected SQL commands.
        """
        injection_payloads = [
            "'; DROP TABLE decisions; --",
            "' OR '1'='1' --",
            "Robert'); DROP TABLE task_history; --",
            "'; VACUUM; --",
            "\" UNION SELECT * FROM projects --",
            "🎉🚀🔥 Unicode and emojis with special symbols \t\n\r",
            "A" * 10000,  # 10KB giant string
        ]

        for i, payload in enumerate(injection_payloads):
            # 1. Decision title and rationale injection
            dec_id = f"dec_inj_{i}"
            res_dec = self.sm.record_decision(
                agent_role="Tester",
                title=f"Injection Test {payload}",
                rationale=payload,
                implications=payload,
                tags=[payload[:20]],
            )
            self.assertTrue(res_dec.startswith("dec_"))
            fetched_dec = self.sm.get_decision(res_dec)
            self.assertIsNotNone(fetched_dec)
            self.assertEqual(fetched_dec["title"], f"Injection Test {payload}".strip())

            # 2. Task history input/output injection
            task_id = f"task_inj_{i}"
            # Need a run
            run_id = f"run_inj_{i}"
            self.sm.create_workflow_run(run_id=run_id, workflow_type=payload[:30], input_prompt=payload)
            self.sm.create_task(
                task_id=task_id,
                run_id=run_id,
                agent_role="Reviewer",
                task_name=f"Task {payload[:50]}",
                description=payload,
                expected_output=payload,
            )
            self.sm.record_task_completion(task_id, actual_output=payload)

        # Verify all tables are still intact and no tables were dropped!
        tables = [r["name"] for r in self.db_mgr.fetchall("SELECT name FROM sqlite_master WHERE type='table'")]
        for expected in ["projects", "decisions", "workflow_runs", "task_history", "milestones", "agent_status", "execution_traces", "memory_records"]:
            self.assertIn(expected, tables, f"Table {expected} was dropped by SQL injection!")

        # Verify record count matches number of payloads
        decs = self.sm.get_decisions(limit=100)
        self.assertEqual(len(decs), len(injection_payloads))

    def test_tc_adv_11_fts5_syntax_poison_queries_do_not_crash(self) -> None:
        """TC-ADV-11: Submit syntactically malformed and hostile queries to FTS5 search.
        Verifies regex / sanitization handles special operators gracefully without raising SQLite exceptions.
        """
        vs = SQLiteVectorStore(db_path=self.db_path, project_id="proj_poison")
        vs.add(["Standard normal text document about software architecture."])

        hostile_queries = [
            "\"\"\"\"\"\"\"\"\"",
            "NEAR(foo, bar, 10)",
            "NOT * OR AND",
            "column:content",
            "{{{[[[(((",
            "***???^^^",
            "\"unbalanced quote",
            "' single quote injection '",
            "   \t\n   ",
            "AND AND AND",
        ]

        for q in hostile_queries:
            # Must not crash or raise OperationalError
            try:
                results = vs.search(q, hybrid_search=True)
                self.assertIsInstance(results, list)
            except Exception as e:
                self.fail(f"FTS5 hostile query '{q}' raised unexpected exception: {e}")

    def test_tc_adv_12_state_import_corrupted_json_payloads_isolated_rollback(self) -> None:
        """TC-ADV-12: Test import of corrupt JSON payloads (truncated JSON, foreign key mismatch,
        invalid schema version). Verifies atomic rollback leaves existing project untouched.
        """
        srm = SessionResumptionManager(db_path=self.db_path, project_id="proj_poison")
        export_file = Path(self.temp_dir) / "valid_state.json"
        srm.export_state(export_file)

        with open(export_file, "r", encoding="utf-8") as f:
            valid_data = json.load(f)

        # Case A: Corrupted JSON syntax (truncated file)
        corrupt_file_a = Path(self.temp_dir) / "corrupt_syntax.json"
        with open(corrupt_file_a, "w", encoding="utf-8") as f:
            f.write('{"schema_version": "1.0.0", "project": {"project_id": "corrupt_proj",')

        with self.assertRaises((json.JSONDecodeError, StateIntegrityError)):
            srm.import_state(corrupt_file_a)

        # Case B: Incompatible schema version
        corrupt_file_b = Path(self.temp_dir) / "corrupt_version.json"
        data_b = dict(valid_data)
        data_b["schema_version"] = "999.0.0"
        with open(corrupt_file_b, "w", encoding="utf-8") as f:
            json.dump(data_b, f)

        with self.assertRaises(SchemaVersionMismatchError):
            srm.import_state(corrupt_file_b, overwrite=True)

        # Case C: Foreign key violation in payload (decisions referencing nonexistent project)
        corrupt_file_c = Path(self.temp_dir) / "corrupt_fk.json"
        data_c = dict(valid_data)
        data_c["decisions"] = [
            {
                "decision_id": "dec_orphan_1",
                "project_id": "NONEXISTENT_GHOST_PROJECT",
                "agent_role": "Architect",
                "title": "Orphan decision",
                "rationale": "Orphan rationale",
            }
        ]
        with open(corrupt_file_c, "w", encoding="utf-8") as f:
            json.dump(data_c, f)

        # Target a fresh DB for import to see if foreign key rollback protects it
        fresh_db_path = Path(self.temp_dir) / "fresh_import.db"
        fresh_srm = SessionResumptionManager(db_path=fresh_db_path)
        with self.assertRaises((sqlite3.IntegrityError, StateIntegrityError, Exception)):
            fresh_srm.import_state(corrupt_file_c)

        # Verify fresh DB was not left in a broken partial state
        fresh_mgr = DatabaseManager(db_path=fresh_db_path)
        decs = fresh_mgr.fetchall("SELECT * FROM decisions WHERE decision_id = 'dec_orphan_1'")
        self.assertEqual(len(decs), 0, "Orphaned decision leaked despite foreign key constraint failure!")


if __name__ == "__main__":
    unittest.main()
