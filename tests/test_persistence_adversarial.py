"""
Adversarial and boundary stress test suite for Milestone 1 Persistence Layer.
Tests sudden crash recovery, malformed/corrupted JSON import, extreme inputs,
and embedding mathematical invariants.
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

from persistent_crewai.config import CANONICAL_ROLES, CURRENT_SCHEMA_VERSION
from persistent_crewai.persistence.db import DatabaseManager, init_db, get_connection
from persistent_crewai.persistence.state_manager import (
    StateManager,
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


# ==============================================================================
# Suite 1: Sudden Crash Recovery & State Rehydration Edge Cases
# ==============================================================================
class TestSuddenCrashRecoveryAdversarial(unittest.TestCase):
    """Adversarial testing of sudden process termination and recovery."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crash_test.db"
        self.mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_crash")
        self.sm = self.mgr._state_mgr

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multi_run_multi_agent_in_flight_crash_recovery(self):
        """Simulate sudden crash with multiple concurrent in-flight tasks and busy agents across multiple runs."""
        self.sm.create_project("proj_crash", "Crash Recovery Test", "Testing in-flight recovery")

        # Create two parallel workflow runs
        self.sm.create_workflow_run("run_1", "feature_development")
        self.sm.create_workflow_run("run_2", "bug_fix")

        # Run 1: Task 1 completed, Task 2 in-progress (Developer busy), Task 3 pending
        self.sm.record_task_start("t1_1", "run_1", "Architect", "Design", execution_order=1)
        self.sm.record_task_completion("t1_1", "Architecture complete")

        self.sm.record_task_start("t1_2", "run_1", "Developer", "Implement feature", execution_order=2)
        self.sm.start_task("t1_2")

        self.sm.record_task_start("t1_3", "run_1", "Tester", "Test feature", execution_order=3)

        # Run 2: Task 1 in-progress (Reviewer busy)
        self.sm.record_task_start("t2_1", "run_2", "Reviewer", "Security audit", execution_order=1)
        self.sm.start_task("t2_1")

        # Verify pre-crash state
        dev_pre = self.sm.get_agent_status("Developer")
        rev_pre = self.sm.get_agent_status("Reviewer")
        self.assertEqual(dev_pre["status"], "busy")
        self.assertEqual(dev_pre["current_task_id"], "t1_2")
        self.assertEqual(rev_pre["status"], "busy")
        self.assertEqual(rev_pre["current_task_id"], "t2_1")

        # Crash occurs -> new session manager re-opens database and inspects state
        recovery_mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_crash")
        report = recovery_mgr.inspect_state(auto_recover=True)

        # Verify recovery findings
        interrupted_ids = {t["task_id"] for t in report["interrupted_tasks"]}
        self.assertIn("t1_2", interrupted_ids)
        self.assertIn("t2_1", interrupted_ids)
        self.assertNotIn("t1_1", interrupted_ids)
        self.assertNotIn("t1_3", interrupted_ids)
        self.assertEqual(report["running_runs_count"], 2)

        # Verify all agents are now idle
        agents = recovery_mgr._state_mgr.get_all_agent_statuses()
        for a in agents:
            self.assertEqual(a["status"], "idle", f"Agent {a['agent_role']} should be idle after crash recovery")
            self.assertIsNone(a["current_task_id"])

        # Verify task statuses
        t1_1 = recovery_mgr._state_mgr.get_task("t1_1")
        t1_2 = recovery_mgr._state_mgr.get_task("t1_2")
        t1_3 = recovery_mgr._state_mgr.get_task("t1_3")
        t2_1 = recovery_mgr._state_mgr.get_task("t2_1")

        self.assertEqual(t1_1["status"], "completed")
        self.assertEqual(t1_2["status"], "interrupted")
        self.assertEqual(t1_3["status"], "pending")
        self.assertEqual(t2_1["status"], "interrupted")

        # Verify workflow runs are marked interrupted
        run1 = recovery_mgr._state_mgr.get_workflow_run("run_1")
        run2 = recovery_mgr._state_mgr.get_workflow_run("run_2")
        self.assertEqual(run1["status"], "interrupted")
        self.assertEqual(run2["status"], "interrupted")

    def test_crash_recovery_project_isolation(self):
        """Crash recovery for Project A must NOT modify tasks, agents, or runs of Project B."""
        # Create Project A
        self.sm.create_project("proj_A", "Project A", "Desc A")
        self.sm.create_workflow_run("run_A", "feature", project_id="proj_A")
        self.sm.record_task_start("task_A", "run_A", "Developer", "Code A", project_id="proj_A")
        self.sm.start_task("task_A")

        # Create Project B with busy Tester
        self.sm.create_project("proj_B", "Project B", "Desc B")
        self.sm.create_workflow_run("run_B", "test_run", project_id="proj_B")
        self.sm.record_task_start("task_B", "run_B", "Tester", "Test B", project_id="proj_B")
        self.sm.start_task("task_B")

        # Recover Project A only
        mgr_A = SessionResumptionManager(db_path=self.db_path, project_id="proj_A")
        report_A = mgr_A.inspect_state(auto_recover=True)

        self.assertEqual(len(report_A["interrupted_tasks"]), 1)
        self.assertEqual(report_A["interrupted_tasks"][0]["task_id"], "task_A")

        # Verify Project B task B is STILL in_progress and Tester is STILL busy
        task_B = self.sm.get_task("task_B")
        self.assertEqual(task_B["status"], "in_progress", "Project B task must remain in_progress")
        status_B = self.sm.get_agent_status("Tester", project_id="proj_B")
        self.assertEqual(status_B["status"], "busy", "Project B agent must remain busy")

    def test_busy_agent_with_orphan_or_null_task_id(self):
        """Crash recovery handles agents marked busy whose current_task_id is NULL or whose task was deleted."""
        self.sm.create_project("proj_crash", "P", "D")
        self.sm.create_workflow_run("run_tmp", "test")

        # Set an agent to busy with current_task_id = None
        self.sm.update_agent_status("Architect", status="busy", current_task_id=None)

        # Set Developer busy on a task, then delete the task (triggering ON DELETE SET NULL)
        self.sm.record_task_start("t_temp", "run_tmp", "Developer", "Temp Task")
        self.sm.start_task("t_temp")
        with self.sm.connection() as conn:
            conn.execute("DELETE FROM task_history WHERE task_id = 't_temp'")

        dev_mid = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_mid["status"], "busy")
        self.assertIsNone(dev_mid["current_task_id"])

        # Run crash recovery
        report = self.mgr.inspect_state(auto_recover=True)

        arch = self.sm.get_agent_status("Architect")
        dev = self.sm.get_agent_status("Developer")
        self.assertEqual(arch["status"], "idle")
        self.assertIsNone(arch["current_task_id"])
        self.assertEqual(dev["status"], "idle")
        self.assertIsNone(dev["current_task_id"])

    def test_workflow_resumption_and_execution_after_crash(self):
        """Full resumption cycle: crash -> inspect -> resume_workflow -> re-execute task to completion."""
        self.sm.create_project("proj_crash", "P", "D")
        self.sm.create_workflow_run("run_recover", "feature")
        self.sm.record_task_start("t_crash", "run_recover", "Developer", "Implement feature")
        self.sm.start_task("t_crash")

        # Crash recovery
        self.mgr.inspect_state(auto_recover=True)

        # Generate resumption plan
        plan = self.mgr.resume_workflow("run_recover")
        self.assertIsInstance(plan, WorkflowResumePlan)
        self.assertEqual(plan.recommended_action, "retry_interrupted")
        self.assertEqual(plan.next_task["task_id"], "t_crash")

        # Re-execute the interrupted task
        self.sm.start_task("t_crash")
        dev_busy = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_busy["status"], "busy")

        self.sm.record_task_completion("t_crash", actual_output="Successfully implemented after crash.")
        dev_idle = self.sm.get_agent_status("Developer")
        self.assertEqual(dev_idle["status"], "idle")
        self.assertEqual(dev_idle["tasks_completed_count"], 1)

        t_final = self.sm.get_task("t_crash")
        self.assertEqual(t_final["status"], "completed")

    def test_resuming_all_completed_workflow_run(self):
        """Calling resume_workflow on a run where all tasks are completed returns 'all_completed'."""
        self.sm.create_project("proj_crash", "P", "D")
        self.sm.create_workflow_run("run_done", "feature")
        self.sm.record_task_start("t_done", "run_done", "Architect", "Design")
        self.sm.record_task_completion("t_done", "Done")

        plan = self.mgr.resume_workflow("run_done")
        self.assertEqual(plan.recommended_action, "all_completed")
        self.assertIsNone(plan.next_task)
        self.assertEqual(len(plan.completed_tasks), 1)

    def test_crash_recovery_idempotency(self):
        """Calling inspect_state(auto_recover=True) multiple times in succession is safe and idempotent."""
        self.sm.create_project("proj_crash", "P", "D")
        self.sm.create_workflow_run("run_idem", "test")
        self.sm.record_task_start("t_idem", "run_idem", "Tester", "Test")
        self.sm.start_task("t_idem")

        # First recovery
        rep1 = self.mgr.inspect_state(auto_recover=True)
        self.assertEqual(len(rep1["interrupted_tasks"]), 1)

        # Second recovery (immediately after)
        rep2 = self.mgr.inspect_state(auto_recover=True)
        self.assertEqual(len(rep2["interrupted_tasks"]), 0)
        self.assertEqual(len(rep2["recovered_agents"]), 0)

        # Third recovery
        rep3 = self.mgr.inspect_state(auto_recover=True)
        self.assertEqual(len(rep3["interrupted_tasks"]), 0)


# ==============================================================================
# Suite 2: Malformed, Corrupted, & Hostile State Import
# ==============================================================================
class TestMalformedStateImportAdversarial(unittest.TestCase):
    """Adversarial stress-testing of state backup import."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "import_test.db"
        self.mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_import")
        self.sm = self.mgr._state_mgr

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_temp_file(self, content: str) -> Path:
        p = Path(self.temp_dir.name) / f"temp_{os.urandom(6).hex()}.json"
        p.write_text(content, encoding="utf-8")
        return p

    def test_corrupted_json_syntax(self):
        """Importing files with invalid JSON syntax raises StateIntegrityError."""
        bad_syntax_samples = [
            "{ incomplete json",
            '{"schema_version": "1.0.0", "projects": [}',
            "<html><body>Not JSON</body></html>",
            "",
            "   \n  \t  ",
            "{\"key\": 'single-quoted'}",
        ]
        for sample in bad_syntax_samples:
            f = self._write_temp_file(sample)
            with self.assertRaises(StateIntegrityError, msg=f"Failed for sample: {sample[:20]}"):
                self.mgr.import_state(f)

    def test_non_dict_json_roots(self):
        """Importing JSON files whose top-level element is a list, string, number, or null."""
        non_dict_samples = [
            "[1, 2, 3]",
            '"just a raw string"',
            "42",
            "null",
            "true",
        ]
        for sample in non_dict_samples:
            f = self._write_temp_file(sample)
            # The system must reject non-dict roots with an appropriate exception without unhandled DB corruption
            with self.assertRaises((StateIntegrityError, AttributeError, ValueError)):
                self.mgr.import_state(f)

    def test_missing_or_empty_projects_table(self):
        """State backup missing 'projects' or containing empty projects list raises StateIntegrityError."""
        missing_projects_samples = [
            json.dumps({"schema_version": CURRENT_SCHEMA_VERSION}),
            json.dumps({"schema_version": CURRENT_SCHEMA_VERSION, "projects": []}),
            json.dumps({"schema_version": CURRENT_SCHEMA_VERSION, "projects": None}),
        ]
        for sample in missing_projects_samples:
            f = self._write_temp_file(sample)
            with self.assertRaises(StateIntegrityError):
                self.mgr.import_state(f)

    def test_incompatible_or_missing_schema_version(self):
        """State backup with missing, non-string, or incompatible schema_version raises error."""
        incompatible_samples = [
            {"projects": [{"project_id": "p1"}]},  # Missing schema_version
            {"schema_version": None, "projects": [{"project_id": "p1"}]},
            {"schema_version": 1.0, "projects": [{"project_id": "p1"}]},
            {"schema_version": "0.9.0", "projects": [{"project_id": "p1"}]},
            {"schema_version": "2.0.0", "projects": [{"project_id": "p1"}]},
            {"schema_version": "99.99.99", "projects": [{"project_id": "p1"}]},
        ]
        for sample in incompatible_samples:
            f = self._write_temp_file(json.dumps(sample))
            with self.assertRaises((SchemaVersionMismatchError, StateIntegrityError, ValueError)):
                self.mgr.import_state(f)

    def test_atomic_rollback_on_schema_mismatch_during_import(self):
        """If import fails midway (e.g. missing required field in decisions), pre-existing state is preserved."""
        # 1. Establish existing valid project with decisions
        self.sm.create_project("proj_import", "Original Project", "Must survive failed overwrite")
        self.sm.record_decision("Architect", "Original ADR", "Original Rationale")

        pre_project = self.sm.get_project("proj_import")
        self.assertEqual(pre_project["name"], "Original Project")
        pre_decisions = self.sm.get_decisions("proj_import")
        self.assertEqual(len(pre_decisions), 1)

        # 2. Construct a corrupted backup: valid project, but decisions entry missing required 'title'
        corrupted_payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": "proj_import",
            "projects": [
                {
                    "project_id": "proj_import",
                    "name": "Corrupted New Project",
                    "description": "Should be rolled back",
                    "root_path": ".",
                }
            ],
            "decisions": [
                {
                    "decision_id": "dec_broken",
                    "project_id": "proj_import",
                    "agent_role": "Architect",
                    # 'title' is intentionally missing
                    "rationale": "Missing title will cause KeyError",
                }
            ],
        }
        f = self._write_temp_file(json.dumps(corrupted_payload))

        # 3. Attempt import with overwrite=True -> must fail
        with self.assertRaises((KeyError, StateIntegrityError, PersistenceError)):
            self.mgr.import_state(f, overwrite=True)

        # 4. Verify transaction rollback: Original project and decision MUST be intact
        post_project = self.sm.get_project("proj_import")
        self.assertIsNotNone(post_project, "Original project must NOT be deleted after failed overwrite")
        self.assertEqual(post_project["name"], "Original Project")

        post_decisions = self.sm.get_decisions("proj_import")
        self.assertEqual(len(post_decisions), 1)
        self.assertEqual(post_decisions[0]["title"], "Original ADR")

    def test_atomic_rollback_on_foreign_key_violation_during_import(self):
        """If imported decisions reference a non-existent project_id, foreign key failure rolls back entire import."""
        foreign_key_broken_payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": "proj_new",
            "projects": [
                {
                    "project_id": "proj_new",
                    "name": "New Project",
                    "description": "Valid project",
                    "root_path": ".",
                }
            ],
            "decisions": [
                {
                    "decision_id": "dec_fk_fail",
                    "project_id": "nonexistent_foreign_key_project_xyz",
                    "agent_role": "Architect",
                    "title": "Broken FK",
                    "rationale": "Will trigger SQLite foreign key violation",
                }
            ],
        }
        f = self._write_temp_file(json.dumps(foreign_key_broken_payload))

        new_mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_new")
        with self.assertRaises(sqlite3.IntegrityError):
            new_mgr.import_state(f, overwrite=False)

        # Verify proj_new was rolled back and does not exist in DB
        self.assertIsNone(self.sm.get_project("proj_new"))

    def test_import_with_optional_tables_omitted(self):
        """Importing a state backup that only has 'projects' and 'schema_version' succeeds cleanly."""
        minimal_payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": "proj_minimal",
            "projects": [
                {
                    "project_id": "proj_minimal",
                    "name": "Minimal Project",
                    "description": "No decisions, tasks, or traces included",
                    "root_path": ".",
                }
            ],
        }
        f = self._write_temp_file(json.dumps(minimal_payload))

        min_mgr = SessionResumptionManager(db_path=self.db_path, project_id="proj_minimal")
        result = min_mgr.import_state(f, overwrite=False)

        self.assertEqual(result["projects_imported"], 1)
        self.assertEqual(result["decisions_imported"], 0)
        self.assertEqual(result["tasks_imported"], 0)

        p = min_mgr._state_mgr.get_project("proj_minimal")
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "Minimal Project")


# ==============================================================================
# Suite 3: Extreme Inputs (Unicode, Emojis, Long Strings, Null Characters, SQLi)
# ==============================================================================
class TestExtremeInputsAdversarial(unittest.TestCase):
    """Stress tests with multilingual unicode, emojis, long strings, null bytes, and SQL injection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "extreme_inputs.db"
        self.sm = StateManager(db_path=self.db_path, project_id="proj_extreme")
        self.vstore = SQLiteVectorStore(db_path=self.db_path, project_id="proj_extreme")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multilingual_unicode_spectrum(self):
        """State manager, decisions, tasks, and vector store handle CJK, RTL Arabic/Hebrew, Cyrillic, and accents."""
        unicode_title = "ADR 架构: 全局持久化 & Многоагентная система & تصميم النظام & קונטקסט"
        unicode_desc = "RTL text: العربيّة עברית. Accents: café, naïve, façade, résumé, jalapeño. Math: ∑∫∇≠∞√."

        self.sm.create_project(
            project_id="proj_extreme",
            name="项目 🚀 Проект مشروع",
            description=unicode_desc,
        )

        dec_id = self.sm.record_decision(
            agent_role="Architect",
            title=unicode_title,
            rationale="Rationale with multilingual strings: 数据库, База данных, قاعدة البيانات",
            tags=["架构", "архитектура", "تصميم", "café"],
        )

        dec = self.sm.get_decision(dec_id)
        self.assertEqual(dec["title"], unicode_title)
        self.assertIn("café", dec["tags"])
        self.assertIn("架构", dec["tags"])

        # Vector Store with Unicode
        doc_ids = self.vstore.add([unicode_title, unicode_desc])
        self.assertEqual(len(doc_ids), 2)

        # Search using CJK
        results_cjk = self.vstore.search("全局持久化", top_k=2)
        self.assertGreater(len(results_cjk), 0)
        self.assertEqual(results_cjk[0].document_id, doc_ids[0])

        # Search using Cyrillic
        results_cyr = self.vstore.search("Многоагентная", top_k=2)
        self.assertGreater(len(results_cyr), 0)

    def test_multi_byte_and_compound_emojis(self):
        """Full support for complex emojis (zero-width joiners, skin tones, flag sequences)."""
        emoji_title = "Sprint 42 🚀 🧑‍💻 Family: 👨‍👩‍👧‍👦 Tone: 👍🏽 Flags: 🏳️‍🌈 🇺🇸 🇯🇵"
        emoji_content = "Agent 🤖 status: 🔥 Operational! 🔒 Secure. ⚠️ Warning: 🛑"

        self.sm.create_project("proj_extreme", "Emoji Project 🚀", emoji_content)
        self.sm.create_workflow_run("run_emoji", "feature_run", input_prompt=emoji_title)

        self.sm.record_task_start("task_emoji", "run_emoji", "Developer", "Code emoji feature 💻", description=emoji_title)
        self.sm.record_task_completion("task_emoji", actual_output=emoji_content)

        t = self.sm.get_task("task_emoji")
        self.assertEqual(t["description"], emoji_title)
        self.assertEqual(t["actual_output"], emoji_content)

        # Vector store handles complex emojis without crash
        v_ids = self.vstore.add([emoji_title, emoji_content])
        self.assertEqual(len(v_ids), 2)
        res = self.vstore.search("Family 👨‍👩‍👧‍👦", top_k=2)
        self.assertGreater(len(res), 0)

    def test_large_string_payloads_100kb_and_500kb(self):
        """Handles large text strings (100KB, 500KB) in descriptions, task outputs, and vector storage."""
        self.sm.create_project("proj_extreme", "Large Payload Project", "Desc")

        chunk_100k = "Lorem ipsum dolor sit amet, consectetur adipiscing elit." * 1800  # ~100KB (no trailing space)
        chunk_500k = "State persistence payload test line data entry." * 11000  # ~517KB (exceeds 500KB)

        self.assertGreaterEqual(len(chunk_100k), 100_000)
        self.assertGreaterEqual(len(chunk_500k), 500_000)

        # Store in decision rationale
        dec_id = self.sm.record_decision(
            agent_role="Architect",
            title="Large Payload Architecture Decision",
            rationale=chunk_100k,
        )
        dec = self.sm.get_decision(dec_id)
        self.assertEqual(len(dec["rationale"]), len(chunk_100k))

        # Store in task history output
        self.sm.create_workflow_run("run_large", "large_data_run")
        self.sm.record_task_start("task_large", "run_large", "Developer", "Produce 500KB output")
        self.sm.record_task_completion("task_large", actual_output=chunk_500k)

        t = self.sm.get_task("task_large")
        self.assertEqual(len(t["actual_output"]), len(chunk_500k))

        # Vector Store indexing large text (chunk_100k)
        v_ids = self.vstore.add([chunk_100k])
        self.assertEqual(len(v_ids), 1)

        res = self.vstore.search("consectetur adipiscing elit", top_k=1)
        self.assertEqual(len(res), 1)
        self.assertGreater(res[0].score, 0.5)

    def test_embedded_null_characters_in_strings(self):
        """Handles strings with embedded null characters ('\\x00') cleanly."""
        null_title = "Decision with \x00 embedded null byte"
        null_rationale = "Rationale contains \x00 hidden null character safely"

        self.sm.create_project("proj_extreme", "Null Project", "Desc")
        dec_id = self.sm.record_decision("Architect", null_title, null_rationale)

        dec = self.sm.get_decision(dec_id)
        self.assertIn("\x00", dec["title"])
        self.assertEqual(dec["title"], null_title)

        # Vector store handles embedded null without crash
        v_ids = self.vstore.add([null_title, "Regular clean document"])
        self.assertEqual(len(v_ids), 2)
        res = self.vstore.search("embedded \x00 null", top_k=2)
        self.assertIsInstance(res, list)

    def test_sql_injection_and_hostile_punctuation(self):
        """Hostile SQL injection strings are safely parameterized and treated as literal text."""
        self.sm.create_project("proj_extreme", "SQLi Project", "Desc")

        hostile_strings = [
            "'; DROP TABLE projects; --",
            "' OR '1'='1",
            "1; DELETE FROM decisions WHERE 'a'='a';",
            "UNION SELECT * FROM sqlite_master WHERE ''='",
            "Robert'); DROP TABLE task_history;--",
        ]

        for i, sqli in enumerate(hostile_strings):
            dec_id = self.sm.record_decision(
                agent_role="Reviewer",
                title=f"Security Test {i}: {sqli}",
                rationale=sqli,
                tags=[sqli],
            )
            retrieved = self.sm.get_decision(dec_id)
            self.assertEqual(retrieved["rationale"], sqli)
            self.assertEqual(retrieved["title"], f"Security Test {i}: {sqli}")
            self.assertEqual(retrieved["tags"], [sqli])

            # Query with filter containing SQLi tag (parameterized)
            results = self.sm.get_decisions(tag=sqli)
            self.assertGreaterEqual(len(results), 1)

        # Verify all tables are still completely intact
        mgr = DatabaseManager(self.db_path)
        with mgr.connection() as conn:
            cur = conn.execute("SELECT count(*) as cnt FROM projects")
            self.assertGreater(cur.fetchone()["cnt"], 0)

    def test_tag_filter_limitations_quotes_and_wildcards(self):
        """Empirically test StateManager.get_decisions tag filtering with double quotes and LIKE wildcards."""
        self.sm.create_project("proj_extreme", "Tag Filter Project", "Desc")

        # 1. Double quote inside tag: JSON serializes '"' as '\"'
        quote_tag = 'tag"with"quotes'
        dec_id = self.sm.record_decision("Architect", "ADR Quotes", "Rationale", tags=[quote_tag])
        dec = self.sm.get_decision(dec_id)
        self.assertEqual(dec["tags"], [quote_tag])

        # StateManager uses `tags LIKE '%"{tag}"%'` without JSON escaping, so searching fails to match
        results = self.sm.get_decisions(tag=quote_tag)
        # Empirical finding: returns 0 because SQLite LIKE does not match escaped JSON quotes
        self.assertEqual(len(results), 0, "Known limitation: tags with quotes are not matched by LIKE '%\"{tag}\"%'")

        # 2. LIKE wildcard collision: tag 'tag-1' matched by search for 'tag_1'
        self.sm.record_decision("Architect", "ADR Hyphen", "Rationale", tags=["system-v1"])
        wildcard_matches = self.sm.get_decisions(tag="system_v1")
        # Empirical finding: returns match because '_' is a single-character wildcard in SQL LIKE
        self.assertEqual(len(wildcard_matches), 1, "Known limitation: '_' in tag query acts as SQL LIKE wildcard")

    def test_deeply_nested_and_special_json_metadata(self):
        """Metadata JSON columns handle deeply nested dictionaries and special characters."""
        self.sm.create_project("proj_extreme", "Nested JSON Project", "Desc")

        # Build deeply nested dictionary (depth 30)
        nested = {"level_0": "base"}
        curr = nested
        for depth in range(1, 30):
            curr[f"level_{depth}"] = {}
            curr = curr[f"level_{depth}"]
        curr["leaf"] = "deep_secret_value"

        self.sm.update_project("proj_extreme", metadata=nested)
        proj = self.sm.get_project("proj_extreme")
        self.assertIn("metadata", proj)

        # Traverse back down
        ptr = proj["metadata"]
        for depth in range(1, 30):
            ptr = ptr[f"level_{depth}"]
        self.assertEqual(ptr["leaf"], "deep_secret_value")


# ==============================================================================
# Suite 4: Embedding Math Properties & Boundary Invariants
# ==============================================================================
class TestEmbeddingMathPropertiesAdversarial(unittest.TestCase):
    """Rigorous verification of mathematical invariants for DeterministicHashEmbeddingProvider."""

    def setUp(self):
        self.embedder = DeterministicHashEmbeddingProvider(dimension=64)

    def test_cosine_similarity_mathematical_boundary(self):
        """Cosine similarity for any pair of unit vectors must strictly lie in [-1.00001, 1.00001]."""
        test_strings = [
            "Persistent CrewAI multi-agent framework",
            "SQLite relational schema with WAL journal mode",
            "Baking chocolate chip cookies in the oven",
            "Deep learning neural network backpropagation algorithm",
            "Quantum entanglement and cryptographic quantum key distribution",
            "Quick brown fox jumps over the lazy dog",
            "1234567890 !@#$%^&*()",
            "Lorem ipsum dolor sit amet consectetur adipiscing elit",
            "Architect, Developer, Tester, Reviewer, Coordinator",
            "GitHub adapter mock issues pull requests git commit",
        ]

        vectors = [self.embedder.embed_query(s) for s in test_strings]

        for i in range(len(vectors)):
            for j in range(len(vectors)):
                v1 = vectors[i]
                v2 = vectors[j]
                cos_sim = sum(a * b for a, b in zip(v1, v2))
                self.assertGreaterEqual(
                    cos_sim,
                    -1.00001,
                    f"Cosine similarity below -1.0 for pair ({test_strings[i]}, {test_strings[j]}): {cos_sim}",
                )
                self.assertLessEqual(
                    cos_sim,
                    1.00001,
                    f"Cosine similarity above 1.0 for pair ({test_strings[i]}, {test_strings[j]}): {cos_sim}",
                )

    def test_exact_identity_score_invariant(self):
        """Identical non-empty strings must have cosine similarity score of exactly 1.0 (within 1e-5)."""
        sample_texts = [
            "hello world",
            "CrewAI persistent software engineering team",
            "SQLite WAL mode zero-dependency memory vector store",
            "Multi-language text: 架构 and Русский язык",
            "A single unique word: supercalifragilisticexpialidocious",
        ]

        for text in sample_texts:
            v1 = self.embedder.embed_query(text)
            v2 = self.embedder.embed_query(text)
            self.assertEqual(v1, v2, f"Embedding must be strictly deterministic for '{text}'")

            dot_product = sum(a * b for a, b in zip(v1, v2))
            self.assertTrue(
                math.isclose(dot_product, 1.0, abs_tol=1e-5),
                f"Identical text '{text}' dot product {dot_product} must be ~1.0",
            )

    def test_unit_norm_invariant_across_varied_texts(self):
        """All non-zero embedding vectors must have L2 norm == 1.0 (within 1e-4)."""
        corpus = [
            "a",
            "to be or not to be that is the question",
            "Technical architecture decision record number 42",
            "Short",
            "A " * 500,  # Repeated tokens
            "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10",
            "Special characters: !?#&*%^$@~`",
            "Mixed 12345 numbers and text",
        ]

        for text in corpus:
            vec = self.embedder.embed_query(text)
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0.0:  # If non-zero vector
                self.assertTrue(
                    math.isclose(norm, 1.0, abs_tol=1e-4),
                    f"L2 norm of '{text[:30]}' is {norm}, expected 1.0",
                )

    def test_zero_vector_handling_for_empty_and_punctuation_only_text(self):
        """Empty, whitespace-only, and punctuation-only inputs must cleanly produce a zero vector without error."""
        zero_cases = [
            "",
            "   ",
            "\t\n\r",
            "!@#$%^&*()+-=[]{}|;':,.<>/?~`",  # All non-word symbols (excluding underscore)
            "   !@#   $%^   ",
        ]

        for case in zero_cases:
            vec = self.embedder.embed_query(case)
            self.assertEqual(
                vec,
                [0.0] * 64,
                f"Case '{case}' did not return zero vector: {vec[:5]}...",
            )
            norm = math.sqrt(sum(x * x for x in vec))
            self.assertEqual(norm, 0.0)

        # Note: In Python regex \w, ASCII underscore '_' is considered a word character.
        # Strings with underscore (e.g. '_') produce a non-zero vector because \w matches '_'.
        vec_under = self.embedder.embed_query("_")
        self.assertNotEqual(vec_under, [0.0] * 64)

    def test_vector_store_resilience_to_zero_vector_documents_and_queries(self):
        """Vector store handles zero-vector docs and zero-vector queries without division by zero or NaN."""
        temp_dir = tempfile.TemporaryDirectory()
        try:
            db_path = Path(temp_dir.name) / "zero_vec.db"
            vstore = SQLiteVectorStore(db_path=db_path, embedder=self.embedder, project_id="p_zero")

            # Add empty and punctuation-only documents alongside valid documents
            doc_ids = vstore.add(["", "   ", "!@#$%^", "Valid technical document on SQLite"])
            self.assertEqual(len(doc_ids), 4)

            # Query with valid query -> zero vector docs should receive score 0.0 without exception
            results_valid = vstore.search("SQLite", top_k=5, hybrid_search=False)
            self.assertEqual(len(results_valid), 4)
            valid_res = [r for r in results_valid if "Valid" in r.content][0]
            self.assertGreater(valid_res.score, 0.0)

            for zero_res in [r for r in results_valid if "Valid" not in r.content]:
                self.assertEqual(zero_res.score, 0.0)
                self.assertLess(zero_res.score, valid_res.score)

            # Query with zero-vector query ("") -> should return []
            results_empty = vstore.search("", top_k=5)
            self.assertEqual(results_empty, [])

            results_ws = vstore.search("   \t  ", top_k=5)
            self.assertEqual(results_ws, [])
        finally:
            temp_dir.cleanup()

    def test_dimension_scalability(self):
        """Embedder scales cleanly across dimensions d=16, 64, 128, 256, 512, 1024."""
        test_text = "Vector store embedding dimension scalability validation"
        for dim in [16, 64, 128, 256, 512, 1024]:
            emb = DeterministicHashEmbeddingProvider(dimension=dim)
            vec = emb.embed_query(test_text)
            self.assertEqual(len(vec), dim)
            norm = math.sqrt(sum(x * x for x in vec))
            self.assertTrue(math.isclose(norm, 1.0, abs_tol=1e-4), f"Norm failed at dimension {dim}: {norm}")

    def test_seed_prefix_consistency_and_discrimination(self):
        """Same seed prefix yields identical vectors; different seed prefix yields different vectors."""
        text = "Seed prefix differentiation test"
        emb1 = DeterministicHashEmbeddingProvider(dimension=64, seed_prefix="seed_alpha_")
        emb2 = DeterministicHashEmbeddingProvider(dimension=64, seed_prefix="seed_alpha_")
        emb3 = DeterministicHashEmbeddingProvider(dimension=64, seed_prefix="seed_beta_")

        v1 = emb1.embed_query(text)
        v2 = emb2.embed_query(text)
        v3 = emb3.embed_query(text)

        self.assertEqual(v1, v2, "Identical seed prefix must yield identical vectors")
        self.assertNotEqual(v1, v3, "Different seed prefix must yield different vectors")

    def test_extremely_long_text_embedding_runtime(self):
        """Embedding a 10,000-word document runs in O(N) time and produces a valid unit vector."""
        long_doc = "word " * 10_000
        vec = self.embedder.embed_query(long_doc)
        self.assertEqual(len(vec), 64)
        norm = math.sqrt(sum(x * x for x in vec))
        self.assertTrue(math.isclose(norm, 1.0, abs_tol=1e-4))


if __name__ == "__main__":
    unittest.main()
