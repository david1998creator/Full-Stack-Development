"""
Empirical Adversarial Stress Test Suite for Milestone 4 (REPL & Dashboard).
Challenger M4-2 Stress Harness.

Areas covered:
1. REPL Command Parsing Stress:
   - Malformed commands, unbalanced quotes, escaped quotes, nested quotes
   - Empty lines, whitespace lines, tabs, newlines
   - Excess arguments on commands (help, status, decisions, milestones, ask, etc.)
   - Unknown verbs, case insensitivity, symbols, special characters
   - Quotes with spaces, preserved internal spacing
   - Windows file paths with backslashes, unquoted vs quoted, non-existent directories/files

2. Batch Command Runner:
   - Feed batch script with 10+ commands (mixed valid, comments, blanks, errors)
   - Verify execution sequence and error tolerance (errors do NOT terminate batch early)
   - Verify termination behavior on exit/quit commands

3. Status Dashboard Stress:
   - Empty DB (no projects, decisions, milestones, tasks, runs)
   - DB with 100+ tasks, 100+ decisions, 25 milestones, 30 workflow runs
   - Non-interactive streams (force_plain ASCII, custom console buffer)
   - Invariant: all 8 canonical roles are ALWAYS rendered in exact canonical order,
     even with empty DB, partial agents, reversed agents, or rogue non-canonical roles

4. Step Trace Callback Stress:
   - Stream 50 rapid sequential step events
   - Stream 50 concurrent step events across multiple threads (concurrency & thread safety)
   - Malformed events (None, empty strings, arbitrary objects, missing fields, unknown roles)
   - Auto-creation of task foreign keys and absence of unhandled exceptions
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure src directory is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES
from persistent_crewai.persistence.db import init_db
from persistent_crewai.persistence.session import SessionResumptionManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
)
from persistent_crewai.cli.dashboard import (
    create_step_trace_callback,
    get_dashboard_data,
    get_system_dashboard_data,
    render_dashboard,
)
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
from persistent_crewai.cli.repl import InteractiveREPL


class BaseM4StressFixture(unittest.TestCase):
    """Hermetic test fixture providing isolated SQLite database and workspace directory."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="crewai_m4_stress_")
        self.workspace_root = Path(self.test_dir)
        self.db_path = self.workspace_root / "test_state.db"
        self.project_id = "stress_m4_project"

        init_db(str(self.db_path))
        self.state_manager = StateManager(
            db_path=str(self.db_path),
            project_id=self.project_id,
            workspace_root=self.workspace_root,
        )
        self.state_manager.create_project(
            project_id=self.project_id,
            name="Milestone 4 Stress Project",
            description="Hermetic project for REPL & Dashboard adversarial testing",
            root_path=str(self.workspace_root),
            integrity_mode="development",
        )

        self.embedder = DeterministicHashEmbeddingProvider(dimension=64)
        self.vector_store = SQLiteVectorStore(
            db_path=str(self.db_path),
            embedder=self.embedder,
            project_id=self.project_id,
        )

        self.repl = InteractiveREPL(
            state_manager=self.state_manager,
            project_id=self.project_id,
            workspace_root=self.workspace_root,
            vector_store=self.vector_store,
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass


# ==============================================================================
# Suite 1: REPL Command Parsing Stress Tests
# ==============================================================================
class TestREPLCommandParsingStress(BaseM4StressFixture):
    """Adversarial stress testing of REPL token parsing, syntax variations, and edge cases."""

    def test_01_empty_and_whitespace_lines(self) -> None:
        """Verify empty and whitespace-only command inputs return empty string and do not crash."""
        empty_variations = [
            "",
            "   ",
            "\t",
            "\n",
            "   \t\n   ",
            " \r\n ",
        ]
        for var in empty_variations:
            out = self.repl.execute_command(var)
            self.assertEqual(out, "", f"Expected empty return for input '{repr(var)}', got '{out}'")
        self.assertTrue(self.repl.running, "REPL should remain running after whitespace inputs")

    def test_02_unknown_verbs_and_gibberish(self) -> None:
        """Verify unknown verbs return helpful guidance and never raise unhandled exceptions."""
        gibberish_commands = [
            "foobar",
            "deploy --force production",
            "git push origin master",
            "DROP TABLE projects;",
            "12345",
            "!!! @@ ##",
            ":wq",
            "sudo rm -rf /",
        ]
        for cmd in gibberish_commands:
            out = self.repl.execute_command(cmd)
            self.assertIsInstance(out, str)
            self.assertIn("Unknown command", out, f"Expected 'Unknown command' for '{cmd}', got '{out}'")
            self.assertTrue(self.repl.running, "REPL should not terminate on unknown verb")

    def test_03_case_insensitivity_of_verbs(self) -> None:
        """Verify verbs are case-insensitive (e.g. HELP, Status, AsK, ExIt)."""
        # HELP
        out_help = self.repl.execute_command("HELP")
        self.assertIn("PERSISTENT CREWAI REPL COMMANDS", out_help)

        # Status
        out_status = self.repl.execute_command("StAtUs")
        self.assertIn("DASHBOARD", out_status.upper())

        # Decisions
        out_dec = self.repl.execute_command("DECISIONS")
        self.assertIsInstance(out_dec, str)

        # Milestones
        out_ms = self.repl.execute_command("MileStones")
        self.assertIsInstance(out_ms, str)

        # Ask with mixed case verb and role
        out_ask = self.repl.execute_command("AsK CoOrDiNaToR 'What is the plan?'")
        self.assertIn("Coordinator", out_ask)

    def test_04_unbalanced_and_malformed_quotes(self) -> None:
        """Verify REPL gracefully handles unbalanced quotes without uncaught syntax exceptions."""
        malformed_inputs = [
            'ask coordinator "unclosed double quote',
            "ask architect 'unclosed single quote",
            'decision add architect "title without closing quote rationale',
            'milestone add "title only',
            'task developer "unclosed task description',
            'ask developer "nested \'quotes" without balance',
            '"""',
            "'''",
            '"',
            "'",
        ]
        for raw in malformed_inputs:
            # Must return a string (either error message or processed output), never crash
            try:
                out = self.repl.execute_command(raw)
                self.assertIsInstance(out, str, f"Expected string output for malformed input: {raw}")
            except Exception as e:
                self.fail(f"execute_command raised unhandled exception on input '{raw}': {e}")

    def test_05_quotes_with_multiple_spaces_and_special_chars(self) -> None:
        """Verify multi-word quoted prompts preserve whitespace and special characters."""
        prompt = "What    about   multi-space     formatting   and   symbols:  #@!$%?"
        out = self.repl.execute_command(f'ask Coordinator "{prompt}"')
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)
        self.assertIn("Coordinator", out)

    def test_06_excess_arguments_on_fixed_commands(self) -> None:
        """Verify commands with excess arguments do not crash and handle gracefully."""
        # help with extra args
        out_help = self.repl.execute_command("help me please with extra arguments")
        self.assertIn("PERSISTENT CREWAI REPL COMMANDS", out_help)

        # status with extra args
        out_status = self.repl.execute_command("status --verbose --all --json")
        self.assertIn("DASHBOARD", out_status.upper())

        # exit with extra args
        repl_temp = InteractiveREPL(state_manager=self.state_manager)
        out_exit = repl_temp.execute_command("exit now and forever")
        self.assertIn("Goodbye", out_exit)
        self.assertFalse(repl_temp.running)

    def test_07_windows_paths_with_backslashes(self) -> None:
        """Verify Windows paths with backslashes and spaces are handled correctly in export/import."""
        export_file = self.workspace_root / "test_exports" / "sub_dir" / "backup_state.json"
        export_file.parent.mkdir(parents=True, exist_ok=True)

        # Test Windows style backslash path string without quotes
        win_path_str = str(export_file).replace("/", "\\")
        cmd_export = f"export {win_path_str}"
        out_export = self.repl.execute_command(cmd_export)
        self.assertIn("Export completed", out_export)
        self.assertTrue(export_file.exists(), f"Export file was not created at {export_file}")

        # Test Windows style path with quotes
        quoted_win_path = f'"{win_path_str}"'
        cmd_import = f"import {quoted_win_path} --overwrite"
        out_import = self.repl.execute_command(cmd_import)
        self.assertIn("Import completed", out_import)

    def test_08_windows_paths_with_spaces_in_directory(self) -> None:
        """Verify paths with spaces in folder names work when quoted."""
        space_dir = self.workspace_root / "My Special Directory"
        space_dir.mkdir(parents=True, exist_ok=True)
        export_path = space_dir / "project export.json"
        win_export_path = str(export_path).replace("/", "\\")

        # Export with quotes
        out_export = self.repl.execute_command(f'export "{win_export_path}"')
        self.assertIn("Export completed", out_export)
        self.assertTrue(export_path.exists())

        # Import with quotes
        out_import = self.repl.execute_command(f'import "{win_export_path}" --overwrite')
        self.assertIn("Import completed", out_import)

    def test_09_export_to_invalid_directory(self) -> None:
        """Verify export to non-existent or invalid directory returns clean error string."""
        # Non-existent deep directory
        bad_path = self.workspace_root / "non_existent_folder" / "deep" / "file.json"
        # SessionResumptionManager creates parent dirs if needed, but invalid drive or device path
        out = self.repl.execute_command(f"export {bad_path}")
        self.assertIsInstance(out, str)

    def test_10_import_from_non_existent_file(self) -> None:
        """Verify import from missing file returns clean error string without crashing."""
        missing_path = self.workspace_root / "definitely_does_not_exist_9999.json"
        out = self.repl.execute_command(f"import {missing_path}")
        self.assertIn("Error", out)


# ==============================================================================
# Suite 2: Batch Command Runner Tests
# ==============================================================================
class TestBatchCommandRunnerStress(BaseM4StressFixture):
    """Stress testing batch command execution across 10+ sequential commands with errors."""

    def test_11_batch_runner_15_commands_with_mixed_errors_and_continuation(self) -> None:
        """Verify 15-command batch executes all commands in sequence without terminating early on errors."""
        batch_script = [
            "# Step 1: Blank lines and comments",
            "   ",
            "# Step 2: System status check",
            "status",
            "# Step 3: Malformed unknown command (error tolerance test)",
            "invalid_unknown_verb_12345",
            "# Step 4: Add architectural decision",
            "decision add Architect 'Use SQLite WAL Mode' 'High-concurrency file database'",
            "# Step 5: Insufficient arguments error (error tolerance test)",
            "ask",
            "# Step 6: Query architect about decisions",
            "ask Architect 'Explain the persistence architecture'",
            "# Step 7: Malformed decision add (missing rationale)",
            "decision add",
            "# Step 8: Add milestone",
            "milestone add 'Beta Release' 'All unit tests passing'",
            "# Step 9: Malformed milestone add (missing criteria)",
            "milestone add",
            "# Step 10: List milestones",
            "milestones",
            "# Step 11: Targeted subtask execution",
            "task Tester 'Execute database regression audit'",
            "# Step 12: Invalid task command (missing description)",
            "task Developer",
            "# Step 13: List decisions",
            "decisions",
            "# Step 14: Help command",
            "help",
            "# Step 15: Memory search (when empty)",
            "memory query 'sqlite persistence'",
        ]

        outputs = self.repl.run_batch(batch_script)

        # Expected number of non-comment, non-empty commands executed:
        # 1. status
        # 2. invalid_unknown_verb_12345
        # 3. decision add Architect ...
        # 4. ask
        # 5. ask Architect ...
        # 6. decision add
        # 7. milestone add 'Beta Release' ...
        # 8. milestone add
        # 9. milestones
        # 10. task Tester ...
        # 11. task Developer
        # 12. decisions
        # 13. help
        # 14. memory query ...
        # Total: 14 executed commands
        self.assertEqual(len(outputs), 14, f"Expected 14 outputs, got {len(outputs)}")

        # Verify command 1: status output
        self.assertIn("DASHBOARD", outputs[0].upper())

        # Verify command 2: unknown verb handled gracefully without termination
        self.assertIn("Unknown command", outputs[1])

        # Verify command 3: decision added successfully
        self.assertIn("Decision recorded", outputs[2])

        # Verify command 4: usage error on empty ask
        self.assertIn("Usage: ask", outputs[3])

        # Verify command 5: ask architect succeeded despite prior error
        self.assertIn("Architect", outputs[4])

        # Verify command 6: usage error on incomplete decision add
        self.assertIn("Usage: decision add", outputs[5])

        # Verify command 7: milestone added successfully
        self.assertIn("Milestone created", outputs[6])

        # Verify command 8: usage error on incomplete milestone add
        self.assertIn("Usage: milestone add", outputs[7])

        # Verify command 9: milestones list contains Beta Release
        self.assertIn("Beta Release", outputs[8])

        # Verify command 10: targeted subtask completed
        self.assertIn("Tester", outputs[9])

        # Verify command 11: usage error on task
        self.assertIn("Usage: task", outputs[10])

        # Verify command 12: decisions list contains SQLite WAL Mode
        self.assertIn("SQLite WAL Mode", outputs[11])

        # Verify command 13: help contains commands list
        self.assertIn("PERSISTENT CREWAI REPL COMMANDS", outputs[12])

        # Verify command 14: memory query executed
        self.assertIsInstance(outputs[13], str)

        # Verify REPL is still running
        self.assertTrue(self.repl.running, "REPL should remain running after batch completes without exit")

    def test_12_batch_runner_terminates_on_exit(self) -> None:
        """Verify batch stops processing subsequent commands when 'exit' is encountered."""
        batch_script = [
            "status",
            "exit",
            "status",
            "milestones",
        ]
        outputs = self.repl.run_batch(batch_script)
        self.assertEqual(len(outputs), 2)
        self.assertIn("DASHBOARD", outputs[0].upper())
        self.assertIn("Goodbye", outputs[1])
        self.assertFalse(self.repl.running)


# ==============================================================================
# Suite 3: Status Dashboard Stress Tests
# ==============================================================================
class TestStatusDashboardStress(BaseM4StressFixture):
    """Stress testing render_dashboard and get_dashboard_data under extreme database sizes."""

    def test_13_dashboard_with_empty_database(self) -> None:
        """Verify dashboard renders cleanly on an empty database with no projects or records."""
        # Create fresh StateManager pointing to empty DB without create_project call
        empty_db_path = self.workspace_root / "empty_state.db"
        init_db(str(empty_db_path))
        empty_sm = StateManager(db_path=str(empty_db_path), project_id="empty_proj")

        data = get_dashboard_data(empty_sm)
        self.assertIn("project", data)
        self.assertIn("agents", data)
        self.assertEqual(len(data["agents"]), 8)

        # Invariant: All 8 canonical roles in exact canonical order
        actual_roles = [a["agent_role"] for a in data["agents"]]
        self.assertEqual(actual_roles, CANONICAL_ROLES)

        # Render Rich dashboard
        rendered = render_dashboard(empty_sm)
        self.assertIn("DASHBOARD", rendered.upper())
        self.assertIn("Project", rendered)

        # Render Plain ASCII dashboard
        rendered_plain = render_dashboard(empty_sm, force_plain=True)
        self.assertIn("PERSISTENT CREWAI SYSTEM DASHBOARD", rendered_plain)
        self.assertIn("8-AGENT OPERATIONAL TEAM", rendered_plain)

        # Verify each canonical role is printed in plain ASCII output
        for role in CANONICAL_ROLES:
            self.assertIn(role, rendered_plain)

    def test_14_dashboard_with_100_plus_decisions_and_tasks(self) -> None:
        """Verify dashboard aggregates and renders cleanly with 100+ tasks, 100+ decisions, 25 milestones, 30 runs."""
        # 1. Seed 120 architectural decisions
        for i in range(120):
            role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
            self.state_manager.record_decision(
                agent_role=role,
                title=f"Architectural Decision #{i:03d} for Subsystem {i % 10}",
                rationale=f"Rationale explaining trade-offs and constraints for decision #{i:03d}",
                project_id=self.project_id,
            )

        # 2. Seed 30 workflow runs
        for r in range(30):
            self.state_manager.create_workflow_run(
                run_id=f"run_{r:03d}",
                workflow_type="feature_development" if r % 2 == 0 else "bugfix",
                input_prompt=f"Execute workflow test #{r:03d}",
                project_id=self.project_id,
            )

        # 3. Seed 150 tasks (across 8 roles, with varying statuses)
        for t in range(150):
            role = CANONICAL_ROLES[t % len(CANONICAL_ROLES)]
            run_id = f"run_{t % 30:03d}"
            status = "completed" if t % 4 != 0 else ("failed" if t % 8 == 0 else "pending")
            self.state_manager.record_task_start(
                task_id=f"task_{t:03d}",
                run_id=run_id,
                agent_role=role,
                task_name=f"Task #{t:03d}",
                description=f"Description for engineering task #{t:03d}",
                execution_order=t,
                project_id=self.project_id,
            )
            if status == "completed":
                self.state_manager.record_task_completion(
                    task_id=f"task_{t:03d}",
                    agent=role,
                    output=f"Artifact output for task #{t:03d}",
                )
            elif status == "failed":
                self.state_manager.record_task_failure(
                    task_id=f"task_{t:03d}",
                    error_details=f"Simulated fault in task #{t:03d}",
                )

        # 4. Seed 25 milestones
        for m in range(25):
            self.state_manager.create_milestone(
                milestone_id=f"ms_{m:03d}",
                title=f"Milestone M{m:02d} Target Definition",
                criteria=[f"Criteria {m}.1", f"Criteria {m}.2"],
                status="achieved" if m < 10 else "pending",
                project_id=self.project_id,
            )

        # Now test dashboard retrieval
        start_time = time.time()
        data = get_dashboard_data(self.state_manager, project_id=self.project_id)
        elapsed_retrieval = time.time() - start_time

        self.assertLess(elapsed_retrieval, 2.0, f"Dashboard retrieval took too long: {elapsed_retrieval:.2f}s")
        self.assertEqual(len(data["agents"]), 8)
        self.assertEqual([a["agent_role"] for a in data["agents"]], CANONICAL_ROLES)

        # Verify completed tasks metrics
        metrics = data["metrics"]
        self.assertGreater(metrics["tasks_completed_total"], 80)
        self.assertEqual(metrics["agents_total"], 8)
        self.assertEqual(metrics["milestones_total"], 25)

        # Render Rich dashboard
        start_render = time.time()
        rendered_rich = render_dashboard(self.state_manager)
        elapsed_render = time.time() - start_render
        self.assertLess(elapsed_render, 2.0, f"Dashboard render took too long: {elapsed_render:.2f}s")
        self.assertIn("DASHBOARD", rendered_rich.upper())
        self.assertIn("Project", rendered_rich)

        # Render Plain ASCII dashboard
        rendered_plain = render_dashboard(data, force_plain=True)
        self.assertIn("PERSISTENT CREWAI SYSTEM DASHBOARD", rendered_plain)
        self.assertIn("Completed Tasks:", rendered_plain)

    def test_15_dashboard_canonical_order_with_reversed_and_rogue_agents(self) -> None:
        """Verify canonical order is maintained even if agent_status table has reversed order and rogue roles."""
        rogue_db_path = self.workspace_root / "rogue_agents.db"
        init_db(str(rogue_db_path))
        sm = StateManager(db_path=str(rogue_db_path), project_id="rogue_proj")
        sm.create_project("rogue_proj", "Rogue Project", "Adversarial test")

        # Insert reversed canonical roles + rogue non-canonical roles into agent_status
        with sm.connection() as conn:
            # Delete default seeded roles
            conn.execute("DELETE FROM agent_status WHERE project_id = 'rogue_proj'")
            # Insert in reverse order
            for role in reversed(CANONICAL_ROLES):
                conn.execute(
                    """
                    INSERT INTO agent_status (agent_role, project_id, status, tasks_completed_count)
                    VALUES (?, 'rogue_proj', 'idle', 5)
                    """,
                    (role,),
                )
            # Insert rogue unknown agents
            conn.execute(
                """
                INSERT INTO agent_status (agent_role, project_id, status, tasks_completed_count)
                VALUES ('HackerBot', 'rogue_proj', 'active', 99)
                """
            )
            conn.execute(
                """
                INSERT INTO agent_status (agent_role, project_id, status, tasks_completed_count)
                VALUES ('Infiltrator', 'rogue_proj', 'error', 0)
                """
            )

        data = get_dashboard_data(sm, project_id="rogue_proj")
        agents = data["agents"]

        # Invariant: exactly 8 agents in canonical order
        self.assertEqual(len(agents), 8)
        self.assertEqual([a["agent_role"] for a in agents], CANONICAL_ROLES)
        for a in agents:
            self.assertEqual(a["tasks_completed_count"], 5)

        # Render dashboard
        rendered = render_dashboard(data, force_plain=True)
        # Rogue agents must not be in the 8-agent table
        self.assertNotIn("HackerBot", rendered)
        self.assertNotIn("Infiltrator", rendered)
        # All 8 canonical roles must be present
        for role in CANONICAL_ROLES:
            self.assertIn(role, rendered)

    def test_16_dashboard_non_interactive_string_streams(self) -> None:
        """Verify render_dashboard operates properly in non-interactive terminal/pipe streams."""
        out_buf = io.StringIO()
        from rich.console import Console
        custom_console = Console(file=out_buf, force_terminal=False, color_system=None, width=120)

        rendered = render_dashboard(self.state_manager, console=custom_console)
        self.assertIsInstance(rendered, str)
        self.assertIn("DASHBOARD", rendered.upper())


# ==============================================================================
# Suite 4: Step Trace Callback Concurrency & Stress Tests
# ==============================================================================
class TestStepTraceCallbackStress(BaseM4StressFixture):
    """Stress testing step trace callbacks under high concurrency, malformed payloads, and rapid streaming."""

    def test_17_stream_50_rapid_sequential_step_events(self) -> None:
        """Verify streaming 50 rapid sequential step events records cleanly into SQLite."""
        run_id = "run_rapid_sequential_50"
        callback = create_step_trace_callback(self.state_manager, run_id=run_id)

        for i in range(50):
            role = CANONICAL_ROLES[i % len(CANONICAL_ROLES)]
            event = {
                "event": "step_execution",
                "agent_role": role,
                "task_id": f"rapid_task_{i:03d}",
                "tool_name": f"Tool_{i % 5}",
                "payload": {"step_index": i, "timestamp_ms": time.time() * 1000},
            }
            callback(event)

        traces = self.state_manager.get_traces(run_id=run_id, limit=100)
        self.assertEqual(len(traces), 50, f"Expected 50 recorded traces, got {len(traces)}")

    def test_18_stream_50_concurrent_step_events_multithreaded(self) -> None:
        """Verify 50 concurrent step events across 10 threads execute without unhandled exceptions or deadlocks."""
        run_id = "run_concurrent_threads_50"
        callback = create_step_trace_callback(self.state_manager, run_id=run_id)

        num_events = 50
        errors: List[Exception] = []
        barrier = threading.Barrier(10)

        def worker(thread_idx: int, event_slice: List[int]) -> None:
            try:
                barrier.wait(timeout=5.0)
            except Exception:
                pass
            for idx in event_slice:
                try:
                    role = CANONICAL_ROLES[idx % len(CANONICAL_ROLES)]
                    event = {
                        "event": "concurrent_step",
                        "agent_role": role,
                        "task_id": f"conc_task_{idx:03d}",
                        "tool_name": f"ConcurrentTool_{thread_idx}",
                        "payload": {
                            "thread_idx": thread_idx,
                            "event_idx": idx,
                            "data": "x" * 200,
                        },
                    }
                    callback(event)
                except Exception as ex:
                    errors.append(ex)

        threads: List[threading.Thread] = []
        chunk_size = 5
        for t in range(10):
            event_slice = list(range(t * chunk_size, (t + 1) * chunk_size))
            th = threading.Thread(target=worker, args=(t, event_slice))
            threads.append(th)
            th.start()

        for th in threads:
            th.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} thread errors: {errors}")

        traces = self.state_manager.get_traces(run_id=run_id, limit=100)
        self.assertEqual(len(traces), num_events, f"Expected {num_events} traces recorded, got {len(traces)}")

    def test_19_step_trace_callback_resilience_to_all_malformed_types(self) -> None:
        """Verify callback never raises exceptions when passed diverse non-standard or malformed objects."""
        run_id = "run_malformed_objects_test"
        callback = create_step_trace_callback(self.state_manager, run_id=run_id)

        class CustomClass:
            def __init__(self):
                self.event_type = "custom_class_event"
                self.agent_role = "Developer"
                self.task_id = "custom_task"
                self.tool_name = "CustomTool"

        class ExplodingProperty:
            @property
            def agent_role(self):
                raise RuntimeError("Exploding property access")

        malformed_inputs = [
            None,
            "",
            "a string event",
            12345,
            3.14159,
            [],
            [1, 2, 3],
            {},
            {"no_standard_keys": True},
            {"agent_role": "UnknownRoleNotCanonical", "event": "custom_event"},
            {"agent_role": None, "event": None},
            CustomClass(),
            ExplodingProperty(),
            {"task_id": "non_existent_foreign_key_task_999"},
        ]

        for item in malformed_inputs:
            try:
                callback(item)
            except Exception as e:
                self.fail(f"create_step_trace_callback raised exception on malformed item '{item}': {e}")

    def test_20_step_trace_callback_100_events_across_20_threads_with_circular_payload(self) -> None:
        """Verify 100 concurrent events across 20 threads with circular references never crash or deadlock."""
        run_id = "run_circular_ref_20_threads"
        callback = create_step_trace_callback(self.state_manager, run_id=run_id)

        # Create object with circular reference
        circular_dict: Dict[str, Any] = {"name": "circular"}
        circular_dict["self_ref"] = circular_dict

        errors: List[Exception] = []
        barrier = threading.Barrier(20)

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait(timeout=5.0)
            except Exception:
                pass
            for i in range(5):
                try:
                    callback({
                        "event": "circular_trace",
                        "agent_role": CANONICAL_ROLES[i % len(CANONICAL_ROLES)],
                        "task_id": f"t_circ_{thread_idx}_{i}",
                        "payload": circular_dict,
                    })
                except Exception as ex:
                    errors.append(ex)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Expected 0 unhandled exceptions, got {len(errors)}: {errors}")


# ==============================================================================
# Suite 5: Advanced REPL & Dashboard Edge-Case Hardening
# ==============================================================================
class TestAdvancedREPLEdgeCases(BaseM4StressFixture):
    """Stress testing control characters, unknown workflow modes, and corrupt metadata resilience."""

    def test_21_control_characters_and_escapes_in_repl(self) -> None:
        """Verify commands with null bytes, bell, escape characters do not crash REPL."""
        control_chars_inputs = [
            "ask coordinator \x00 null byte in prompt",
            "task developer \x07 bell \x08 backspace \x1b[31m ansi code",
            "decision add Architect \x00Title \x00Rationale",
        ]
        for cmd in control_chars_inputs:
            out = self.repl.execute_command(cmd)
            self.assertIsInstance(out, str)

    def test_22_unknown_workflow_and_invalid_mode(self) -> None:
        """Verify 'run <unknown_workflow> --mode invalid' returns descriptive error without crashing REPL."""
        out = self.repl.execute_command("run non_existent_workflow_xyz --mode quantum")
        self.assertIsInstance(out, str)
        # Should return error message about unknown workflow or execute safely
        self.assertTrue(self.repl.running, "REPL should remain running")

    def test_23_unknown_role_in_task_command(self) -> None:
        """Verify 'run task <bad_role> <desc>' and 'task <bad_role> <desc>' return clean error."""
        out_run_task = self.repl.execute_command("run task Necromancer 'Summon minions'")
        self.assertIn("Error", out_run_task)

        out_task = self.repl.execute_command("task Alchemist 'Transmute lead into gold'")
        self.assertIn("Error", out_task)

    def test_24_dashboard_resilience_to_corrupt_database_metadata_json(self) -> None:
        """Verify REPL fault tolerance against corrupt database metadata and document get_project JSONDecodeError."""
        # Directly insert corrupt JSON strings into metadata columns
        with self.state_manager.connection() as conn:
            conn.execute(
                """
                UPDATE projects
                SET metadata_json = '{{MALFORMED JSON NOT VALID'
                WHERE project_id = ?
                """,
                (self.project_id,),
            )
            conn.execute(
                """
                UPDATE agent_status
                SET metadata_json = '{{{corrupt'
                WHERE project_id = ?
                """,
                (self.project_id,),
            )

        # 1. Under REPL, status command catches exception gracefully without crashing the shell
        out = self.repl.execute_command("status")
        self.assertIsInstance(out, str)
        self.assertIn("Error", out)
        self.assertTrue(self.repl.running, "REPL shell must remain running despite corrupt database metadata")

        # 2. Document that state_manager.get_project currently raises JSONDecodeError when metadata_json is corrupted
        import json
        with self.assertRaises(json.JSONDecodeError):
            self.state_manager.get_project(self.project_id)

    def test_25_massive_batch_runner_50_commands(self) -> None:
        """Verify massive 50-command batch script executes completely with sequence preservation."""
        script: List[str] = []
        for i in range(50):
            if i % 5 == 0:
                script.append(f"# Comment line {i}")
            elif i % 5 == 1:
                script.append("   ")  # blank line
            elif i % 5 == 2:
                script.append(f"decision add Architect 'Dec #{i}' 'Rationale #{i}'")
            elif i % 5 == 3:
                script.append(f"milestone add 'Milestone #{i}' 'Criteria #{i}'")
            else:
                script.append(f"invalid_command_verb_{i}")

        outputs = self.repl.run_batch(script)
        # Expected count: 50 total minus (10 comments + 10 blanks) = 30 executed commands
        self.assertEqual(len(outputs), 30, f"Expected 30 executed outputs, got {len(outputs)}")
        self.assertTrue(self.repl.running)


if __name__ == "__main__":
    unittest.main()

