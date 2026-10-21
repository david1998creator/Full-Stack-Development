"""
Adversarial Contract Stress Test Suite for Milestone 1 StateManager.
Targeted adversarial challenge:
1. Concurrency: Execute record_event() across 20 threads simultaneously without any prior create_project() or workflow runs.
   Tested under both shared StateManager instance and separate StateManager instances per thread with a synchronization Barrier.
2. Permutations: Execute record_task_completion() across all signature permutations:
   - Positional contract (task_id, agent, output)
   - Keyword contract (task_id=..., agent=..., output=...)
   - Legacy keyword combinations (actual_output=..., status=...)
   - Legacy positional calls (task_id, output) and (task_id, output, status)
   - Mixed positional and keyword combinations
   - Edge case outputs (output='completed', empty output, large output, unicode/emojis)
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

# Ensure src is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES, ROLE_ALIASES, normalize_role
from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.state_manager import StateManager


class TestRecordEventColdStart20Threads(unittest.TestCase):
    """Adversarial stress testing of record_event() across 20 simultaneous threads
    WITHOUT any prior create_project() or workflow_runs calls.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_m1_event_")
        self.db_path = Path(self.temp_dir) / "cold_start_events.db"

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_record_event_20_threads_shared_manager(self) -> None:
        """Execute record_event() across 20 threads simultaneously sharing ONE StateManager instance
        on a cold database without any prior create_project() or workflow_runs calls.
        """
        num_threads = 20
        events_per_thread = 25
        total_expected_events = num_threads * events_per_thread

        # Cold initialization: StateManager initialized, but NO create_project() or create_workflow_run() called
        sm = StateManager(db_path=self.db_path, project_id="cold_proj_shared")

        barrier = threading.Barrier(num_threads)
        thread_errors: list[Exception] = []
        recorded_ids: list[str] = []
        id_lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                # Wait for all 20 threads to align
                barrier.wait(timeout=10.0)
                for i in range(events_per_thread):
                    role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
                    tid = sm.record_event(
                        event_type=f"cold_event_{thread_idx}_{i}",
                        payload={
                            "thread_id": thread_idx,
                            "seq": i,
                            "timestamp": time.time(),
                            "role": role,
                        },
                        agent_role=role,
                        tool_name="shared_tool",
                    )
                    with id_lock:
                        recorded_ids.append(tid)
            except Exception as e:
                with id_lock:
                    thread_errors.append(e)

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(thread_errors), 0, f"Errors in shared StateManager 20 threads: {thread_errors}")
        self.assertEqual(len(recorded_ids), total_expected_events)

        # Oracle Verifications on database
        db_mgr = DatabaseManager(db_path=self.db_path)
        
        # 1. Verify project auto-created exactly once
        projects = db_mgr.fetchall("SELECT * FROM projects")
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_id"], "cold_proj_shared")
        self.assertEqual(projects[0]["status"], "active")

        # 2. Verify system_run auto-created exactly once
        runs = db_mgr.fetchall("SELECT * FROM workflow_runs")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "system_run")
        self.assertEqual(runs[0]["project_id"], "cold_proj_shared")
        self.assertEqual(runs[0]["status"], "running")

        # 3. Verify exact count of execution_traces
        traces = db_mgr.fetchall("SELECT * FROM execution_traces WHERE run_id = 'system_run'")
        self.assertEqual(len(traces), total_expected_events)

        # 4. Verify all trace IDs are unique
        trace_ids = [r["trace_id"] for r in traces]
        self.assertEqual(len(set(trace_ids)), total_expected_events)

        # 5. Verify database integrity
        check = db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")

    def test_record_event_20_threads_separate_managers(self) -> None:
        """Execute record_event() across 20 threads simultaneously, each with its OWN StateManager instance
        pointing to the same on-disk SQLite DB on a cold database without prior create_project() or workflow runs.
        Database schema is initialized before threads run, but NO project and NO workflow run exists.
        """
        # Pre-initialize schema so DB structure exists, but NO project and NO workflow run is created
        init_db(self.db_path)

        num_threads = 20
        events_per_thread = 25
        total_expected_events = num_threads * events_per_thread

        managers = [StateManager(db_path=self.db_path, project_id="cold_proj_separate") for _ in range(num_threads)]
        barrier = threading.Barrier(num_threads)
        thread_errors: list[Exception] = []
        recorded_ids: list[str] = []
        id_lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                local_sm = managers[thread_idx]
                barrier.wait(timeout=10.0)

                for i in range(events_per_thread):
                    role = CANONICAL_ROLES[(thread_idx + i) % len(CANONICAL_ROLES)]
                    tid = local_sm.record_event(
                        event_type=f"separate_event_{thread_idx}_{i}",
                        payload={
                            "thread_id": thread_idx,
                            "seq": i,
                            "msg": f"Payload from thread {thread_idx} event {i}",
                        },
                        agent_role=role,
                        tool_name=f"tool_th_{thread_idx}",
                    )
                    with id_lock:
                        recorded_ids.append(tid)
            except Exception as e:
                with id_lock:
                    thread_errors.append(e)

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(thread_errors), 0, f"Errors in separate StateManagers 20 threads: {thread_errors}")
        self.assertEqual(len(recorded_ids), total_expected_events)

        # Oracle Verifications on database
        db_mgr = DatabaseManager(db_path=self.db_path)

        # 1. Exactly one project record
        projects = db_mgr.fetchall("SELECT * FROM projects")
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_id"], "cold_proj_separate")

        # 2. Exactly one system_run record
        runs = db_mgr.fetchall("SELECT * FROM workflow_runs")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "system_run")
        self.assertEqual(runs[0]["project_id"], "cold_proj_separate")

        # 3. Exact count of traces
        traces = db_mgr.fetchall("SELECT * FROM execution_traces WHERE run_id = 'system_run'")
        self.assertEqual(len(traces), total_expected_events)

        # 4. Check uniqueness of all returned trace IDs
        trace_ids = [r["trace_id"] for r in traces]
        self.assertEqual(len(set(trace_ids)), total_expected_events)

        # 5. Database integrity check
        check = db_mgr.fetchone("PRAGMA integrity_check;")
        self.assertEqual(list(check.values())[0], "ok")

    def test_record_event_20_threads_adversarial_payload_burst(self) -> None:
        """20 simultaneous threads blasting diverse hostile payloads (deep dicts, unicode, nulls, sql)
        via record_event() on cold start.
        """
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        thread_errors: list[Exception] = []

        hostile_payloads = [
            {"nested": {"a": [1, 2, {"b": "deep"}]}},
            "Plain string event payload",
            {"sql": "'; DROP TABLE execution_traces; --"},
            {"unicode": "🚀 Persistent CrewAI 🤖 テスト ñoño 100%"},
            {"symbols": "!@#$%^&*()_+-=[]{}|;':\",./<>?"},
            {"large": "X" * 10000},
            {"null_safe": "Text with \x00 null char"},
            {"booleans": [True, False, None, 42, 3.14159]},
        ]

        def worker(thread_idx: int) -> None:
            try:
                local_sm = StateManager(db_path=self.db_path, project_id="cold_proj_burst")
                barrier.wait(timeout=10.0)
                payload = hostile_payloads[thread_idx % len(hostile_payloads)]
                tid = local_sm.record_event(
                    event_type=f"burst_event_{thread_idx}",
                    payload=payload,
                    agent_role="Coordinator / Manager",
                    tool_name="burst_logger",
                )
                self.assertTrue(tid.startswith("trc_"))
            except Exception as e:
                thread_errors.append(e)

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(thread_errors), 0, f"Burst errors: {thread_errors}")

        db_mgr = DatabaseManager(db_path=self.db_path)
        traces = db_mgr.fetchall("SELECT * FROM execution_traces WHERE run_id = 'system_run'")
        self.assertEqual(len(traces), num_threads)


class TestRecordTaskCompletionSignaturePermutations(unittest.TestCase):
    """Adversarial stress testing of record_task_completion() across all signature permutations:
    positional (task_id, agent, output), keyword (agent=..., output=...), legacy keyword combinations,
    legacy positional, mixed calls, and edge cases.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_m1_sig_")
        self.db_path = Path(self.temp_dir) / "signature_permutations.db"
        self.sm = StateManager(db_path=self.db_path, project_id="sig_proj")
        self.sm.create_project(
            project_id="sig_proj",
            name="Signature Permutations Test Project",
            description="Testing all signature permutations of record_task_completion",
            root_path=self.temp_dir,
        )
        self.run_id = "sig_run_01"
        self.sm.create_workflow_run(
            run_id=self.run_id,
            workflow_type="signature_verification",
            input_prompt="Verify signatures",
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _create_and_start_task(self, task_id: str, agent_role: str, task_name: str) -> None:
        """Helper to create and start a task, setting agent to busy."""
        self.sm.record_task_start(
            task_id=task_id,
            run_id=self.run_id,
            agent_role=agent_role,
            task_name=task_name,
            description=f"Description for {task_id}",
        )
        self.sm.start_task(task_id)
        # Verify agent is busy
        try:
            canon = normalize_role(agent_role)
        except KeyError:
            canon = agent_role
        stat = self.sm.get_agent_status(canon)
        self.assertEqual(stat["status"], "busy")
        self.assertEqual(stat["current_task_id"], task_id)

    def test_permutation_01_positional_contract_canonical_roles(self) -> None:
        """P1: Positional contract call: record_task_completion(task_id, agent, output)
        across all 8 canonical roles.
        """
        for idx, role in enumerate(CANONICAL_ROLES):
            task_id = f"task_p1_{idx}"
            self._create_and_start_task(task_id, role, f"Task P1 for {role}")

            output_text = f"Artifact output produced by {role}"
            res = self.sm.record_task_completion(task_id, role, output_text)

            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["actual_output"], output_text)

            # Database row verification
            task = self.sm.get_task(task_id)
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["actual_output"], output_text)
            self.assertIn("latency_ms", task["metadata"])
            self.assertGreaterEqual(task["metadata"]["latency_ms"], 0.0)

            # Agent status reset verification
            stat = self.sm.get_agent_status(role)
            self.assertEqual(stat["status"], "idle")
            self.assertIsNone(stat["current_task_id"])
            self.assertGreaterEqual(stat["tasks_completed_count"], 1)

    def test_permutation_02_positional_contract_role_aliases(self) -> None:
        """P2: Positional contract call with documented aliases and case variations."""
        aliases = [
            ("developer", "Developer"),
            ("manager", "Coordinator"),
            ("architect", "Architect"),
            ("designer", "Designer"),
            ("tester", "Tester"),
            ("reviewer", "Reviewer"),
            ("github", "GitHub Manager"),
            ("github_manager", "GitHub Manager"),
            ("memory", "Memory & Status Manager"),
            ("memory_manager", "Memory & Status Manager"),
        ]
        for idx, (alias, canonical) in enumerate(aliases):
            task_id = f"task_p2_{idx}"
            self._create_and_start_task(task_id, alias, f"Task P2 for {alias}")

            output_text = f"Output from alias {alias}"
            res = self.sm.record_task_completion(task_id, alias, output_text)

            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["actual_output"], output_text)

            stat = self.sm.get_agent_status(canonical)
            self.assertEqual(stat["status"], "idle")
            self.assertIsNone(stat["current_task_id"])

    def test_permutation_02b_positional_contract_custom_role_fallback(self) -> None:
        """P2B: Positional contract call with unlisted custom role strings (e.g. 'dev', 'custom_agent').
        Verifies task is recorded and completed, while agent status defaults gracefully for unseeded roles.
        """
        custom_roles = ["dev", "custom_worker", "security_auditor"]
        for idx, role in enumerate(custom_roles):
            task_id = f"task_p2b_{idx}"
            self.sm.record_task_start(
                task_id=task_id,
                run_id=self.run_id,
                agent_role=role,
                task_name=f"Task P2B for {role}",
                description=f"Description for {task_id}",
            )
            self.sm.start_task(task_id)

            output_text = f"Output from custom role {role}"
            res = self.sm.record_task_completion(task_id, role, output_text)

            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["actual_output"], output_text)

            task = self.sm.get_task(task_id)
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["agent_role"], role)

    def test_permutation_03_positional_contract_output_named_completed(self) -> None:
        """P3: Positional contract call where output string is literally 'completed'.
        Must NOT confuse the 3rd positional argument as a status override.
        """
        task_id = "task_p3_completed"
        self._create_and_start_task(task_id, "Developer", "Output Named Completed")

        res = self.sm.record_task_completion(task_id, "Developer", "completed")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "completed")

        task = self.sm.get_task(task_id)
        self.assertEqual(task["actual_output"], "completed")

    def test_permutation_04_positional_contract_output_named_failed(self) -> None:
        """P4: Positional contract call where output string is literally 'failed'.
        Must record actual_output as 'failed' while status defaults to 'completed'.
        """
        task_id = "task_p4_failed_str"
        self._create_and_start_task(task_id, "Tester", "Output Named Failed String")

        res = self.sm.record_task_completion(task_id, "Tester", "failed")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "failed")

    def test_permutation_05_positional_contract_4_args_with_explicit_status(self) -> None:
        """P5: 4-arg positional call: record_task_completion(task_id, agent, output, status)."""
        task_id = "task_p5_explicit_status"
        self._create_and_start_task(task_id, "Reviewer", "Task P5 with 4 args")

        res = self.sm.record_task_completion(task_id, "Reviewer", "Security concerns noted", "interrupted")
        self.assertEqual(res["status"], "interrupted")
        self.assertEqual(res["actual_output"], "Security concerns noted")

        task = self.sm.get_task(task_id)
        self.assertEqual(task["status"], "interrupted")

    def test_permutation_06_keyword_contract_full(self) -> None:
        """K1: Keyword contract call: record_task_completion(task_id=..., agent=..., output=...)."""
        task_id = "task_k1"
        self._create_and_start_task(task_id, "Architect", "Task K1")

        res = self.sm.record_task_completion(
            task_id=task_id,
            agent="Architect",
            output="High-level architecture completed",
        )
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "High-level architecture completed")

        stat = self.sm.get_agent_status("Architect")
        self.assertEqual(stat["status"], "idle")

    def test_permutation_07_keyword_contract_with_status_and_metadata(self) -> None:
        """K2: Keyword contract call with status, metadata, and custom project_id."""
        task_id = "task_k2"
        self._create_and_start_task(task_id, "Developer", "Task K2")

        res = self.sm.record_task_completion(
            task_id=task_id,
            agent="Developer",
            output="Module implemented",
            status="completed",
            metadata={"lines_written": 250, "test_coverage": 0.98},
            project_id="sig_proj",
        )
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "Module implemented")

        task = self.sm.get_task(task_id)
        self.assertEqual(task["metadata"]["lines_written"], 250)
        self.assertEqual(task["metadata"]["test_coverage"], 0.98)
        self.assertIn("latency_ms", task["metadata"])

    def test_permutation_08_legacy_keyword_actual_output(self) -> None:
        """L1: Legacy keyword call: record_task_completion(task_id=..., actual_output=...)."""
        task_id = "task_l1"
        self._create_and_start_task(task_id, "Designer", "Task L1")

        res = self.sm.record_task_completion(
            task_id=task_id,
            actual_output="Design system token map",
        )
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "Design system token map")

        stat = self.sm.get_agent_status("Designer")
        self.assertEqual(stat["status"], "idle")

    def test_permutation_09_legacy_keyword_actual_output_and_status(self) -> None:
        """L2: Legacy keyword call: record_task_completion(task_id=..., actual_output=..., status=...)."""
        task_id = "task_l2"
        self._create_and_start_task(task_id, "Tester", "Task L2")

        res = self.sm.record_task_completion(
            task_id=task_id,
            actual_output="Tests executed with 3 failures",
            status="failed",
        )
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["actual_output"], "Tests executed with 3 failures")

        task = self.sm.get_task(task_id)
        self.assertEqual(task["status"], "failed")

    def test_permutation_10_legacy_positional_2_args(self) -> None:
        """LP1: Legacy 2-argument positional call: record_task_completion(task_id, output)."""
        task_id = "task_lp1"
        self._create_and_start_task(task_id, "GitHub Manager", "Task LP1")

        res = self.sm.record_task_completion(task_id, "PR #42 opened and merged")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "PR #42 opened and merged")

        stat = self.sm.get_agent_status("GitHub Manager")
        self.assertEqual(stat["status"], "idle")

    def test_permutation_11_legacy_positional_3_args_with_status(self) -> None:
        """LP2: Legacy 3-argument positional call: record_task_completion(task_id, output, status)."""
        task_id = "task_lp2"
        self._create_and_start_task(task_id, "Reviewer", "Task LP2")

        res = self.sm.record_task_completion(task_id, "Review rejected for security", "failed")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["actual_output"], "Review rejected for security")

        task = self.sm.get_task(task_id)
        self.assertEqual(task["status"], "failed")

    def test_permutation_12_mixed_positional_task_id_and_kw_agent_output(self) -> None:
        """M1: Mixed call: record_task_completion(task_id, agent=..., output=...)."""
        task_id = "task_m1"
        self._create_and_start_task(task_id, "Memory & Status Manager", "Task M1")

        res = self.sm.record_task_completion(
            task_id,
            agent="Memory & Status Manager",
            output="Session state flushed to disk",
        )
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "Session state flushed to disk")

        stat = self.sm.get_agent_status("Memory & Status Manager")
        self.assertEqual(stat["status"], "idle")

    def test_permutation_13_mixed_positional_agent_and_kw_output(self) -> None:
        """M2: Mixed call: record_task_completion(task_id, agent, output=...)."""
        task_id = "task_m2"
        self._create_and_start_task(task_id, "Coordinator / Manager", "Task M2")

        res = self.sm.record_task_completion(
            task_id,
            "Coordinator / Manager",
            output="Sprint planning finished",
        )
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["actual_output"], "Sprint planning finished")

    def test_permutation_14_adversarial_outputs_empty_unicode_large(self) -> None:
        """E1: Adversarial outputs: empty string, unicode emojis, large text payload."""
        # Empty output
        t_empty = "task_e_empty"
        self._create_and_start_task(t_empty, "Developer", "Empty Output Task")
        res_empty = self.sm.record_task_completion(t_empty, "Developer", "")
        self.assertEqual(res_empty["actual_output"], "")

        # Unicode emojis and formatting
        t_emoji = "task_e_emoji"
        self._create_and_start_task(t_emoji, "Designer", "Emoji Output Task")
        emoji_out = "🎨 Beautiful UI complete with 🌟 stars and 🚀 rockets!"
        res_emoji = self.sm.record_task_completion(t_emoji, "Designer", emoji_out)
        self.assertEqual(res_emoji["actual_output"], emoji_out)

        # Large payload (50KB)
        t_large = "task_e_large"
        self._create_and_start_task(t_large, "Reviewer", "Large Output Task")
        large_out = "A" * 50000
        res_large = self.sm.record_task_completion(t_large, "Reviewer", large_out)
        self.assertEqual(res_large["actual_output"], large_out)

        db_task = self.sm.get_task(t_large)
        self.assertEqual(len(db_task["actual_output"]), 50000)

    def test_permutation_15_concurrent_task_completions(self) -> None:
        """C1: 20 threads simultaneously completing 20 distinct tasks across agents."""
        num_tasks = 20
        task_ids = [f"conc_task_{i}" for i in range(num_tasks)]
        for i, tid in enumerate(task_ids):
            role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
            self._create_and_start_task(tid, role, f"Concurrent Task {i}")

        barrier = threading.Barrier(num_tasks)
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                tid = task_ids[idx]
                role = CANONICAL_ROLES[idx % len(CANONICAL_ROLES)]
                barrier.wait(timeout=10.0)
                # Alternating between positional and keyword calls
                if idx % 2 == 0:
                    self.sm.record_task_completion(tid, role, f"Output from thread {idx}")
                else:
                    self.sm.record_task_completion(task_id=tid, agent=role, output=f"KW Output {idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_tasks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Concurrent completion errors: {errors}")

        # Verify all 20 tasks are marked completed
        for tid in task_ids:
            task = self.sm.get_task(tid)
            self.assertEqual(task["status"], "completed")
            self.assertIsNotNone(task["actual_output"])


if __name__ == "__main__":
    unittest.main()
