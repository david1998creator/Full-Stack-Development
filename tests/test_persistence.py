"""
Comprehensive unit test suite for Milestone 1: Persistence Layer.
Covers SQLite connection and WAL/FTS5 schema, StateManager CRUD,
DeterministicHashEmbeddingProvider, SQLiteVectorStore, Session Resumption,
Crash Recovery, and State Export/Import.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES, CURRENT_SCHEMA_VERSION, normalize_role
from persistent_crewai.persistence.db import (
    DatabaseManager,
    get_connection,
    init_db,
    SCHEMA_DDL,
)
from persistent_crewai.persistence.state_manager import (
    StateManager,
    ProjectMetadata,
    DecisionRecord,
    WorkflowRun,
    TaskHistoryRecord,
    MilestoneRecord,
    AgentStatusRecord,
    ExecutionTraceRecord,
    PersistenceError,
    ProjectNotFoundError,
)
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
    SearchResult,
)
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    WorkflowResumePlan,
    MilestoneResumePlan,
    ResumptionContext,
    SessionNotFoundError,
    StateImportConflictError,
    SchemaVersionMismatchError,
    StateIntegrityError,
)


class TestDatabaseSchemaAndConnection(unittest.TestCase):
    """Unit tests for db.py connection management, WAL mode, foreign keys, and FTS5 triggers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_state.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wal_mode_and_pragmas_on_disk(self):
        """TC-DB-01: Verify connection enables WAL mode, foreign keys, and busy timeout."""
        mgr = init_db(self.db_path)
        self.assertEqual(mgr.get_schema_version(), CURRENT_SCHEMA_VERSION)

        conn = get_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        self.assertEqual(cur.fetchone()[0].lower(), "wal")

        cur.execute("PRAGMA foreign_keys;")
        self.assertEqual(cur.fetchone()[0], 1)

        cur.execute("PRAGMA busy_timeout;")
        self.assertEqual(cur.fetchone()[0], 5000)
        conn.close()

    def test_all_eight_tables_exist(self):
        """TC-DB-02: Verify all 8 core tables plus schema_migrations exist in DB."""
        mgr = init_db(self.db_path)
        expected = [
            "schema_migrations",
            "projects",
            "decisions",
            "workflow_runs",
            "task_history",
            "milestones",
            "agent_status",
            "execution_traces",
            "memory_records",
        ]
        with mgr.connection() as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cur.fetchall()]
            for exp in expected:
                self.assertIn(exp, tables, f"Expected table {exp} missing")

    def test_foreign_key_cascade_deletion(self):
        """TC-DB-03: Deleting a project cascades to decisions, tasks, milestones, agent_status, memory."""
        mgr = init_db(self.db_path)
        with mgr.connection() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
                ("proj_1", "Project 1", "Desc", "/tmp"),
            )
            conn.execute(
                "INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications) VALUES (?, ?, ?, ?, ?, ?)",
                ("dec_1", "proj_1", "Architect", "Title", "Rationale", "Impl"),
            )
            conn.execute(
                "INSERT INTO memory_records (record_id, project_id, content) VALUES (?, ?, ?)",
                ("mem_1", "proj_1", "Important architecture memory"),
            )

        # Confirm rows exist
        self.assertIsNotNone(mgr.fetchone("SELECT * FROM decisions WHERE decision_id = 'dec_1'"))
        self.assertIsNotNone(mgr.fetchone("SELECT * FROM memory_records WHERE record_id = 'mem_1'"))

        # Delete parent project
        mgr.execute("DELETE FROM projects WHERE project_id = 'proj_1'")

        # Confirm cascade deletion
        self.assertIsNone(mgr.fetchone("SELECT * FROM decisions WHERE decision_id = 'dec_1'"))
        self.assertIsNone(mgr.fetchone("SELECT * FROM memory_records WHERE record_id = 'mem_1'"))
        # Confirm FTS5 also cleaned by trigger
        self.assertIsNone(mgr.fetchone("SELECT * FROM memory_fts WHERE record_id = 'mem_1'"))

    def test_fts5_triggers_synchronization(self):
        """TC-DB-04: Test INSERT, UPDATE, and DELETE synchronization triggers for memory_fts."""
        mgr = init_db(self.db_path)
        mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("proj_fts", "FTS Project", "Desc", "/tmp"),
        )

        # 1. Insert trigger
        mgr.execute(
            "INSERT INTO memory_records (record_id, project_id, content) VALUES (?, ?, ?)",
            ("mem_fts_1", "proj_fts", "Deterministic hash embeddings for persistent memory"),
        )
        row = mgr.fetchone("SELECT * FROM memory_fts WHERE memory_fts MATCH 'deterministic'")
        self.assertIsNotNone(row)
        self.assertEqual(row["record_id"], "mem_fts_1")

        # 2. Update trigger
        mgr.execute(
            "UPDATE memory_records SET content = ? WHERE record_id = ?",
            ("Quantum computing security algorithms", "mem_fts_1"),
        )
        row_old = mgr.fetchone("SELECT * FROM memory_fts WHERE memory_fts MATCH 'deterministic'")
        self.assertIsNone(row_old)
        row_new = mgr.fetchone("SELECT * FROM memory_fts WHERE memory_fts MATCH 'quantum'")
        self.assertIsNotNone(row_new)
        self.assertEqual(row_new["record_id"], "mem_fts_1")

        # 3. Delete trigger
        mgr.execute("DELETE FROM memory_records WHERE record_id = 'mem_fts_1'")
        row_del = mgr.fetchone("SELECT * FROM memory_fts WHERE memory_fts MATCH 'quantum'")
        self.assertIsNone(row_del)

    def test_reset_database(self):
        """TC-DB-05: Reset database drops all data and cleanly reinitializes schema."""
        mgr = init_db(self.db_path)
        mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("proj_reset", "Name", "Desc", "/tmp"),
        )
        self.assertIsNotNone(mgr.fetchone("SELECT * FROM projects WHERE project_id = 'proj_reset'"))

        mgr.reset_database()
        self.assertIsNone(mgr.fetchone("SELECT * FROM projects WHERE project_id = 'proj_reset'"))
        self.assertEqual(mgr.get_schema_version(), CURRENT_SCHEMA_VERSION)

    def test_execute_returns_safe_detached_cursor(self):
        """TC-DB-06: DatabaseManager.execute() returns DetachedCursor supporting fetchall, fetchone, rowcount without ProgrammingError."""
        mgr = init_db(self.db_path)

        # 1. SELECT query with fetchall
        cur = mgr.execute("SELECT * FROM schema_migrations")
        rows = cur.fetchall()
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version"], CURRENT_SCHEMA_VERSION)
        # Verify subsequent fetchall returns empty list as per standard DB-API
        self.assertEqual(cur.fetchall(), [])

        # 2. DML statement with rowcount and lastrowid
        cur_dml = mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("proj_det_1", "Detached 1", "Desc", "/tmp"),
        )
        self.assertEqual(cur_dml.rowcount, 1)
        self.assertIsNone(cur_dml.description)

        # 3. SELECT query with fetchone and fetchmany
        mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("proj_det_2", "Detached 2", "Desc", "/tmp"),
        )
        cur_sel = mgr.execute("SELECT project_id, name FROM projects ORDER BY project_id ASC")
        r1 = cur_sel.fetchone()
        self.assertIsNotNone(r1)
        self.assertEqual(r1["project_id"], "proj_det_1")
        m_rows = cur_sel.fetchmany(5)
        self.assertEqual(len(m_rows), 1)
        self.assertEqual(m_rows[0]["project_id"], "proj_det_2")
        self.assertIsNone(cur_sel.fetchone())

        # 4. Iteration protocol support
        cur_iter = mgr.execute("SELECT project_id FROM projects ORDER BY project_id ASC")
        ids = [row["project_id"] for row in cur_iter]
        self.assertEqual(ids, ["proj_det_1", "proj_det_2"])

        # 5. Context manager usage
        with mgr.execute("SELECT count(*) as cnt FROM projects") as cur_cm:
            self.assertEqual(cur_cm.fetchone()["cnt"], 2)

        # 6. executemany support
        cur_many = mgr.executemany(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            [
                ("proj_det_3", "Detached 3", "Desc", "/tmp"),
                ("proj_det_4", "Detached 4", "Desc", "/tmp"),
            ],
        )
        self.assertEqual(cur_many.rowcount, 2)


class TestStateManager(unittest.TestCase):
    """Unit tests for StateManager CRUD, role normalization, latency tracking, and dashboard."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state.db"
        self.sm = StateManager(db_path=self.db_path, project_id="test_sm_proj", workspace_root=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_get_project(self):
        """TC-SM-01: Create and retrieve project, verify 8 canonical agent roles seeded idle."""
        proj = self.sm.create_project(
            project_id="test_sm_proj",
            name="Persistent CrewAI System",
            description="8-agent engineering team",
            metadata={"env": "testing"},
        )
        self.assertEqual(proj["project_id"], "test_sm_proj")
        self.assertEqual(proj["status"], "active")
        self.assertEqual(proj["metadata"]["env"], "testing")

        agents = self.sm.get_all_agent_statuses()
        self.assertEqual(len(agents), 8)
        for a in agents:
            self.assertEqual(a["status"], "idle")
            self.assertEqual(a["tasks_completed_count"], 0)

    def test_project_root_resolution(self):
        """TC-SM-02: Verify project root resolves to canonical Path."""
        self.sm.create_project(
            project_id="test_sm_proj",
            name="Proj",
            description="Desc",
            root_path=self.temp_dir.name,
        )
        root = self.sm.get_project_root()
        self.assertEqual(root, Path(self.temp_dir.name).resolve())

    def test_record_decision_validations_and_retrieval(self):
        """TC-SM-03: Record decision, reject empty fields, and retrieve with status and tag filters."""
        self.sm.create_project("test_sm_proj", "P", "D")

        # Validation: empty title
        with self.assertRaises(ValueError):
            self.sm.record_decision("Architect", "   ", "Valid rationale")

        # Validation: empty rationale
        with self.assertRaises(ValueError):
            self.sm.record_decision("Architect", "Valid title", "")

        # Valid decision
        dec_id = self.sm.record_decision(
            agent_role="architect",
            title="Use SQLite WAL mode",
            rationale="Zero external dependency, fast local reads",
            tags=["sqlite", "wal", "storage"],
        )
        self.assertIsNotNone(dec_id)

        dec = self.sm.get_decision(dec_id)
        self.assertIsNotNone(dec)
        self.assertEqual(dec["agent_role"], "Architect")  # Normalized role
        self.assertIn("sqlite", dec["tags"])

        # Query filtering
        filtered = self.sm.get_decisions(status="accepted", tag="wal")
        self.assertEqual(len(filtered), 1)

        # Update status (record superseding decision to satisfy foreign key)
        dec_future_id = self.sm.record_decision(
            agent_role="Architect",
            title="Future DB architecture",
            rationale="Next generation requirements",
        )
        self.sm.update_decision_status(dec_id, "superseded", superseded_by=dec_future_id)
        updated = self.sm.get_decision(dec_id)
        self.assertEqual(updated["status"], "superseded")
        self.assertEqual(updated["superseded_by"], dec_future_id)

    def test_workflow_run_lifecycle(self):
        """TC-SM-04: Full lifecycle of workflow runs (creation, update, completion)."""
        self.sm.create_project("test_sm_proj", "P", "D")
        run = self.sm.create_workflow_run(
            run_id="run_01",
            workflow_type="feature_development",
            input_prompt="Build vector store",
        )
        self.assertEqual(run["status"], "running")

        self.sm.update_workflow_run("run_01", status="running", final_output="In progress")
        run_updated = self.sm.get_workflow_run("run_01")
        self.assertEqual(run_updated["final_output"], "In progress")

        self.sm.complete_workflow_run("run_01", final_output="Done successfully")
        run_comp = self.sm.get_workflow_run("run_01")
        self.assertEqual(run_comp["status"], "completed")
        self.assertEqual(run_comp["final_output"], "Done successfully")

    def test_task_lifecycle_with_latency_tracking(self):
        """TC-SM-05: Task start, in-progress agent busy transition, completion with latency calculation."""
        self.sm.create_project("test_sm_proj", "P", "D")
        self.sm.create_workflow_run("run_task_01", "test")

        task = self.sm.record_task_start(
            task_id="t_01",
            run_id="run_task_01",
            agent_role="developer",
            task_name="Write code",
        )
        self.assertEqual(task["status"], "pending")

        # Start task
        self.sm.start_task("t_01")
        dev_status = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_status["status"], "busy")
        self.assertEqual(dev_status["current_task_id"], "t_01")

        # Complete task
        self.sm.record_task_completion("t_01", actual_output="Code written.")
        t_done = self.sm.get_task("t_01")
        self.assertEqual(t_done["status"], "completed")
        self.assertIn("latency_ms", t_done["metadata"])
        self.assertGreaterEqual(t_done["metadata"]["latency_ms"], 0.0)

        # Developer returned to idle and counter incremented
        dev_idle = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_idle["status"], "idle")
        self.assertEqual(dev_idle["tasks_completed_count"], 1)

    def test_task_failure_recording(self):
        """TC-SM-06: Task failure marks task failed and agent status error."""
        self.sm.create_project("test_sm_proj", "P", "D")
        self.sm.create_workflow_run("run_err_01", "test")
        self.sm.record_task_start("t_err", "run_err_01", "Tester", "Run suite")
        self.sm.start_task("t_err")

        self.sm.record_task_failure("t_err", error_details="AssertionError in test 42")
        task = self.sm.get_task("t_err")
        self.assertEqual(task["status"], "failed")

        tester_status = self.sm.get_agent_status("Tester")
        self.assertEqual(tester_status["status"], "error")
        self.assertEqual(tester_status["last_error"], "AssertionError in test 42")

    def test_milestone_management(self):
        """TC-SM-07: Create milestones, query active milestone, update achieved status."""
        self.sm.create_project("test_sm_proj", "P", "D")
        self.sm.create_milestone(
            milestone_id="ms_alpha",
            title="Alpha Release",
            description="Core persistence and tools operational",
            criteria=["Schema ready", "Tests pass"],
            status="in_progress",
        )
        self.sm.create_milestone(
            milestone_id="ms_beta",
            title="Beta Release",
            description="All 8 agents operational",
            criteria=["E2E tests pass"],
            status="pending",
        )

        active = self.sm.get_active_milestone()
        self.assertIsNotNone(active)
        self.assertEqual(active["milestone_id"], "ms_alpha")

        # Mark alpha achieved
        self.sm.update_milestone("ms_alpha", status="achieved", achieved=True)
        active_after = self.sm.get_active_milestone()
        self.assertEqual(active_after["milestone_id"], "ms_beta")

    def test_execution_traces_and_dashboard(self):
        """TC-SM-08: Log execution traces and aggregate rich system dashboard data."""
        self.sm.create_project("test_sm_proj", "P", "D")
        self.sm.create_workflow_run("run_dash_01", "feature")
        self.sm.record_trace("run_dash_01", None, "Architect", "thought", {"step": "evaluating WAL"})
        self.sm.record_trace("run_dash_01", None, "Reviewer", "audit_approved", "Approved by Reviewer")

        traces = self.sm.list_traces(run_id="run_dash_01")
        self.assertEqual(len(traces), 2)

        dash = self.sm.get_system_dashboard_data()
        self.assertIn("project", dash)
        self.assertEqual(len(dash["agents"]), 8)
        self.assertIn("metrics", dash)
        self.assertEqual(dash["metrics"]["agents_total"], 8)

    def test_record_event_guarantees_system_run_atomically(self):
        """TC-SM-09: record_event guarantees 'system_run' and project presence atomically without prior run_id."""
        # 1. Cold start: record_event without prior create_project or workflow_run
        trace_id1 = self.sm.record_event("system_boot", {"stage": "init"})
        self.assertTrue(trace_id1.startswith("trc_"))

        # Verify system_run workflow run and project exist
        sys_run = self.sm.get_workflow_run("system_run")
        self.assertIsNotNone(sys_run)
        self.assertEqual(sys_run["run_id"], "system_run")
        self.assertEqual(sys_run["status"], "running")

        # Verify trace is recorded and retrievable
        traces = self.sm.list_traces(run_id="system_run")
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["event_type"], "system_boot")
        self.assertEqual(traces[0]["payload"], {"stage": "init"})

        # 2. Call record_event with established project and positional PROJECT.md contract
        self.sm.create_project("test_sm_proj", "Test Proj", "Desc")
        trace_id2 = self.sm.record_event("tool_event", {"tool": "file_reader", "bytes": 1024})
        self.assertTrue(trace_id2.startswith("trc_"))

        # 3. Call record_event with optional role and tool_name
        trace_id3 = self.sm.record_event(
            event_type="git_commit",
            payload={"hash": "abcdef123"},
            agent_role="GitHub Manager",
            tool_name="git_adapter",
        )
        self.assertTrue(trace_id3.startswith("trc_"))

        all_traces = self.sm.list_traces(run_id="system_run")
        self.assertEqual(len(all_traces), 3)
        self.assertEqual(all_traces[2]["agent_role"], "GitHub Manager")
        self.assertEqual(all_traces[2]["tool_name"], "git_adapter")

    def test_record_task_completion_signature_resolution(self):
        """TC-SM-05B: Verify record_task_completion supports contract positional, keyword, and legacy signatures."""
        self.sm.create_project("test_sig_proj", "Signature Project", "Test signature resolution")
        self.sm.create_workflow_run("run_sig_01", "sig_test")

        # 1. Positional contract call: (task_id, agent, output) as defined in PROJECT.md:139
        self.sm.record_task_start("t_pos_contract", "run_sig_01", "Developer", "Positional Contract Task")
        self.sm.start_task("t_pos_contract")
        res1 = self.sm.record_task_completion("t_pos_contract", "Developer", "Code successfully written.")
        self.assertEqual(res1["status"], "completed")
        self.assertEqual(res1["actual_output"], "Code successfully written.")
        t1 = self.sm.get_task("t_pos_contract")
        self.assertEqual(t1["status"], "completed")
        self.assertEqual(t1["actual_output"], "Code successfully written.")
        dev_stat = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_stat["status"], "idle")
        self.assertIsNone(dev_stat["current_task_id"])
        self.assertEqual(dev_stat["tasks_completed_count"], 1)

        # 2. Keyword contract call: (task_id=..., agent=..., output=...)
        self.sm.record_task_start("t_kw_contract", "run_sig_01", "Architect", "Keyword Contract Task")
        self.sm.start_task("t_kw_contract")
        res2 = self.sm.record_task_completion(task_id="t_kw_contract", agent="Architect", output="Architecture verified.")
        self.assertEqual(res2["status"], "completed")
        self.assertEqual(res2["actual_output"], "Architecture verified.")
        t2 = self.sm.get_task("t_kw_contract")
        self.assertEqual(t2["status"], "completed")
        self.assertEqual(t2["actual_output"], "Architecture verified.")
        arch_stat = self.sm.get_agent_status("Architect")
        self.assertEqual(arch_stat["status"], "idle")
        self.assertEqual(arch_stat["tasks_completed_count"], 1)

        # 3. Legacy keyword call: (task_id=..., actual_output=..., status=...)
        self.sm.record_task_start("t_kw_legacy", "run_sig_01", "Tester", "Keyword Legacy Task")
        self.sm.start_task("t_kw_legacy")
        res3 = self.sm.record_task_completion(task_id="t_kw_legacy", actual_output="All tests passed.", status="completed")
        self.assertEqual(res3["status"], "completed")
        self.assertEqual(res3["actual_output"], "All tests passed.")
        t3 = self.sm.get_task("t_kw_legacy")
        self.assertEqual(t3["status"], "completed")
        self.assertEqual(t3["actual_output"], "All tests passed.")

        # 4. Legacy 2-argument positional call: (task_id, output)
        self.sm.record_task_start("t_pos_legacy2", "run_sig_01", "Reviewer", "Legacy 2-arg Task")
        self.sm.start_task("t_pos_legacy2")
        res4 = self.sm.record_task_completion("t_pos_legacy2", "Code review approved.")
        self.assertEqual(res4["status"], "completed")
        self.assertEqual(res4["actual_output"], "Code review approved.")
        t4 = self.sm.get_task("t_pos_legacy2")
        self.assertEqual(t4["status"], "completed")
        self.assertEqual(t4["actual_output"], "Code review approved.")

        # 5. Hybrid 2-argument positional + keyword status: (task_id, output, status="completed")
        self.sm.record_task_start("t_hybrid", "run_sig_01", "Designer", "Hybrid Task")
        self.sm.start_task("t_hybrid")
        res5 = self.sm.record_task_completion("t_hybrid", "Figma design ready.", status="completed")
        self.assertEqual(res5["status"], "completed")
        self.assertEqual(res5["actual_output"], "Figma design ready.")

        # 6. Edge case: output text is literally 'completed' with contract positional signature
        self.sm.record_task_start("t_edge_completed", "run_sig_01", "Developer", "Edge Output Completed")
        res6 = self.sm.record_task_completion("t_edge_completed", "Developer", "completed")
        self.assertEqual(res6["status"], "completed")
        self.assertEqual(res6["actual_output"], "completed")

        # 7. Positional contract with non-completed status via kwarg: (task_id, agent, output, status="failed")
        self.sm.record_task_start("t_pos_failed", "run_sig_01", "Tester", "Failed Task")
        res7 = self.sm.record_task_completion("t_pos_failed", "Tester", "Syntax error in module", status="failed")
        self.assertEqual(res7["status"], "failed")
        self.assertEqual(res7["actual_output"], "Syntax error in module")


class TestVectorStoreAndEmbeddings(unittest.TestCase):
    """Unit tests for DeterministicHashEmbeddingProvider and SQLiteVectorStore."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "vector.db"
        self.embedder = DeterministicHashEmbeddingProvider(dimension=64)
        self.vstore = SQLiteVectorStore(
            db_path=self.db_path,
            embedder=self.embedder,
            project_id="test_vec_proj",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_hash_embedder_properties(self):
        """TC-VEC-01: Hash embeddings are deterministic, unit-normalized, and 64-dimensional."""
        text = "Persistent CrewAI multi-agent software engineering team"
        v1 = self.embedder.embed_query(text)
        v2 = self.embedder.embed_query(text)
        self.assertEqual(len(v1), 64)
        self.assertEqual(v1, v2)

        # Unit norm
        norm = math.sqrt(sum(x * x for x in v1))
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_hash_embedder_edge_cases(self):
        """TC-VEC-02: Empty text, whitespace, and invalid dimensions are handled properly."""
        v_empty = self.embedder.embed_query("")
        self.assertEqual(v_empty, [0.0] * 64)

        v_spaces = self.embedder.embed_query("    \t\n ")
        self.assertEqual(v_spaces, [0.0] * 64)

        with self.assertRaises(ValueError):
            DeterministicHashEmbeddingProvider(dimension=0)

    def test_hash_embedder_semantic_discrimination(self):
        """TC-VEC-03: Query has higher cosine similarity with topical text than unrelated text."""
        q = "SQLite WAL mode persistence"
        doc_match = "We use SQLite database with WAL journal mode for persistence."
        doc_unrelated = "Baking delicious chocolate chip cookies in an oven."

        q_vec = self.embedder.embed_query(q)
        m_vec = self.embedder.embed_query(doc_match)
        u_vec = self.embedder.embed_query(doc_unrelated)

        cos_match = sum(a * b for a, b in zip(q_vec, m_vec))
        cos_unrelated = sum(a * b for a, b in zip(q_vec, u_vec))

        self.assertGreater(cos_match, cos_unrelated)
        self.assertGreater(cos_match, 0.4)

    def test_vector_store_add_count_and_delete(self):
        """TC-VEC-04: Add memories, count them, and delete by ID."""
        docs = [
            "Architect selected SQLite with WAL journal mode.",
            "Developer implemented file sandboxing.",
            "Tester authored unit tests for persistence.",
        ]
        ids = self.vstore.add(
            documents=docs,
            metadatas=[
                {"role": "Architect", "category": "db"},
                {"role": "Developer", "category": "security"},
                {"role": "Tester", "category": "test"},
            ],
        )
        self.assertEqual(len(ids), 3)
        self.assertEqual(self.vstore.count(), 3)
        self.assertEqual(self.vstore.count(category="db"), 1)

        # Delete 1 record
        deleted = self.vstore.delete([ids[0]])
        self.assertTrue(deleted)
        self.assertEqual(self.vstore.count(), 2)

    def test_vector_store_cosine_and_metadata_search(self):
        """TC-VEC-05: Cosine search with metadata filtering."""
        self.vstore.add(
            documents=[
                "Database design ADR: SQLite with WAL mode.",
                "UI design specification for Rich dashboard.",
                "Security audit findings for token verification.",
            ],
            metadatas=[
                {"role": "Architect", "cat": "db"},
                {"role": "Designer", "cat": "ui"},
                {"role": "Reviewer", "cat": "sec"},
            ],
        )

        # Search without filter
        results = self.vstore.search("SQLite WAL database", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["role"], "Architect")

        # Search with filter
        filtered_results = self.vstore.search(
            "design specification",
            top_k=5,
            filter_metadata={"role": "Designer"},
        )
        self.assertEqual(len(filtered_results), 1)
        self.assertEqual(filtered_results[0].metadata["role"], "Designer")

    def test_vector_store_fts5_syntax_safety(self):
        """TC-VEC-06: Special characters in search query do not raise SQLite FTS5 errors."""
        self.vstore.add(["Standard test document"])
        # Query with FTS special operators and unclosed quotes
        bad_query = 'WAL AND NOT (OR * "" unclosed'
        results = self.vstore.search(bad_query, top_k=5)
        self.assertIsInstance(results, list)


class TestSessionResumptionAndExportImport(unittest.TestCase):
    """Unit tests for SessionResumptionManager, crash recovery, and state export/import."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "session.db"
        self.mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_session")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_existing_session(self):
        """TC-SR-01: Detect existing session returns False on clean DB, True after project creation."""
        self.assertFalse(self.mgr.detect_existing_session())
        self.mgr._state_mgr.create_project("proj_session", "Session Project", "Desc")
        self.assertTrue(self.mgr.detect_existing_session())

    def test_crash_recovery_routine(self):
        """TC-SR-02: Crash recovery transitions in_progress tasks to interrupted and resets busy agents."""
        sm = self.mgr._state_mgr
        sm.create_project("proj_session", "P", "D")
        sm.create_workflow_run("run_crash_01", "test")

        # Task 1 completed
        sm.record_task_start("t_done", "run_crash_01", "Architect", "Design")
        sm.record_task_completion("t_done", "Output")

        # Task 2 left in_progress with busy agent
        sm.record_task_start("t_interrupted", "run_crash_01", "Developer", "Code")
        sm.start_task("t_interrupted")
        self.assertEqual(sm.get_agent_status("Developer")["status"], "busy")

        # Simulate reboot and inspection
        inspection = self.mgr.inspect_state(auto_recover=True)
        self.assertEqual(len(inspection["interrupted_tasks"]), 1)
        self.assertEqual(inspection["interrupted_tasks"][0]["task_id"], "t_interrupted")

        # Verify agent reset to idle
        dev = sm.get_agent_status("Developer")
        self.assertEqual(dev["status"], "idle")

        # Verify task marked interrupted
        task = sm.get_task("t_interrupted")
        self.assertEqual(task["status"], "interrupted")

    def test_resumption_context_generation(self):
        """TC-SR-03: Verify get_resumption_context extracts project, milestones, decisions, and agents."""
        sm = self.mgr._state_mgr
        sm.create_project("proj_session", "Test Project", "Desc")
        sm.record_decision("Architect", "Title 1", "Rationale 1")
        sm.record_decision("Architect", "Title 2", "Rationale 2")
        sm.create_milestone(title="M1", description="D", criteria=["C1"])

        ctx = self.mgr.get_resumption_context()
        self.assertIn("project", ctx)
        self.assertIn("active_milestone", ctx)
        self.assertIn("recent_decisions", ctx)
        self.assertEqual(len(ctx["recent_decisions"]), 2)
        self.assertIn("agent_states", ctx)
        self.assertEqual(len(ctx["agent_states"]), 8)

    def test_workflow_and_milestone_resume_plans(self):
        """TC-SR-04: Generate resume plans for interrupted workflow and active milestone."""
        sm = self.mgr._state_mgr
        sm.create_project("proj_session", "P", "D")
        sm.create_workflow_run("run_plan_01", "feature")
        sm.record_task_start("t_int", "run_plan_01", "Developer", "Task")
        sm.start_task("t_int")
        self.mgr.inspect_state(auto_recover=True)

        plan = self.mgr.resume_workflow("run_plan_01")
        self.assertIsInstance(plan, WorkflowResumePlan)
        self.assertEqual(plan.recommended_action, "retry_interrupted")
        self.assertEqual(plan.next_task["task_id"], "t_int")

        # Milestone plan
        sm.create_milestone(
            milestone_id="ms_plan",
            title="M",
            description="D",
            criteria=["ADR documented", "Unit tests passing"],
            status="in_progress",
        )
        ms_plan = self.mgr.resume_milestone("ms_plan")
        self.assertIsInstance(ms_plan, MilestoneResumePlan)
        self.assertEqual(len(ms_plan.unfulfilled_criteria), 2)
        self.assertEqual(ms_plan.suggested_role, "Architect")

    def test_export_and_import_state_roundtrip(self):
        """TC-SR-05: State export to JSON and clean import into fresh database."""
        sm = self.mgr._state_mgr
        sm.create_project("proj_session", "Export Project", "Desc")
        sm.record_decision("Architect", "ADR-01", "Rationale")
        sm.create_milestone(milestone_id="ms_01", title="M1", description="D", criteria=["C1"])

        export_path = Path(self.temp_dir.name) / "backup.json"
        summary = self.mgr.export_state(export_path)
        self.assertTrue(export_path.exists())
        self.assertGreaterEqual(summary["decisions_count"], 1)

        # Import into fresh database
        db_path_2 = Path(self.temp_dir.name) / "imported.db"
        mgr2 = SessionResumptionManager(db_path=db_path_2, project_id="proj_session")
        import_res = mgr2.import_state(export_path, overwrite=True)
        self.assertEqual(import_res["decisions_imported"], 1)

        # Verify decisions present in imported DB
        decisions = mgr2._state_mgr.get_decisions("proj_session")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["title"], "ADR-01")

    def test_import_error_conditions(self):
        """TC-SR-06: Test FileNotFoundError, SchemaVersionMismatchError, and StateImportConflictError."""
        # Missing file
        with self.assertRaises(FileNotFoundError):
            self.mgr.import_state("nonexistent_backup.json")

        # Incompatible schema version
        bad_ver_file = Path(self.temp_dir.name) / "bad_ver.json"
        bad_ver_file.write_text(json.dumps({"schema_version": "9.9.9", "projects": [{"project_id": "p"}]}))
        with self.assertRaises(SchemaVersionMismatchError):
            self.mgr.import_state(bad_ver_file)

        # Project conflict without overwrite
        self.mgr._state_mgr.create_project("proj_session", "P", "D")
        valid_file = Path(self.temp_dir.name) / "valid.json"
        self.mgr.export_state(valid_file)
        with self.assertRaises(StateImportConflictError):
            self.mgr.import_state(valid_file, overwrite=False)

    def test_import_state_forward_referencing_superseded_decisions(self):
        """TC-SR-07: Verify state import succeeds when decisions contain forward-referencing superseded_by keys."""
        # 1. Create source state with forward reference (Decision 1 superseded by Decision 2)
        source_db = Path(self.temp_dir.name) / "source_superseded.db"
        srm1 = SessionResumptionManager(db_path=source_db, project_id="proj_superseded")
        srm1._state_mgr.create_project("proj_superseded", "Superseded Proj", "Testing forward FK")
        d1 = srm1._state_mgr.record_decision("Architect", "Original Architecture", "Initial choice", status="proposed")
        d2 = srm1._state_mgr.record_decision("Architect", "Refined Architecture", "Newer choice", status="superseded")
        srm1._state_mgr.update_decision_status(d1, "proposed", superseded_by=d2)

        # 2. Export state to JSON
        export_file = Path(self.temp_dir.name) / "superseded_export.json"
        srm1.export_state(export_file)

        # 3. Import into completely fresh database
        target_db = Path(self.temp_dir.name) / "target_superseded.db"
        srm2 = SessionResumptionManager(db_path=target_db, project_id="proj_superseded")
        summary = srm2.import_state(export_file, overwrite=True)

        self.assertEqual(summary["decisions_imported"], 2)

        # 4. Verify decisions exist with accurate linkage
        imported_d1 = srm2._state_mgr.get_decision(d1)
        imported_d2 = srm2._state_mgr.get_decision(d2)
        self.assertIsNotNone(imported_d1)
        self.assertIsNotNone(imported_d2)
        self.assertEqual(imported_d1["superseded_by"], d2)
        self.assertIsNone(imported_d2["superseded_by"])

        # 5. Verify corrupt foreign key still triggers IntegrityError and atomic rollback
        corrupt_file = Path(self.temp_dir.name) / "corrupt_fk.json"
        with open(export_file, "r", encoding="utf-8") as f:
            corrupt_data = json.load(f)
        corrupt_data["decisions"].append({
            "decision_id": "dec_orphan_fk",
            "project_id": "proj_superseded",
            "agent_role": "Architect",
            "title": "Orphan reference",
            "rationale": "Points to ghost decision",
            "status": "proposed",
            "superseded_by": "dec_ghost_id_does_not_exist",
        })
        with open(corrupt_file, "w", encoding="utf-8") as f:
            json.dump(corrupt_data, f)

        target_corrupt_db = Path(self.temp_dir.name) / "target_corrupt.db"
        srm3 = SessionResumptionManager(db_path=target_corrupt_db, project_id="proj_superseded")
        with self.assertRaises(sqlite3.IntegrityError):
            srm3.import_state(corrupt_file, overwrite=True)


if __name__ == "__main__":
    unittest.main()
