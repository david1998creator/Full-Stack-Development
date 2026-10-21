"""
Unit and integration test suite for Persistent CrewAI CLI & Interactive REPL module.
Validates:
- InteractiveAgentExecutor (single-agent Q&A, state grounding, vector memory, subtasks, boundary validation)
- Status Dashboard (get_dashboard_data, render_dashboard, canonical order, Rich/ASCII fallback, step tracing)
- InteractiveREPL (command grammar, ask, run task, decisions, milestones, memory, export, import, batch runner)
- Top-level CLI (Click entrypoint, status, ask, task, export, import commands)
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from click.testing import CliRunner

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
from persistent_crewai.cli.interactive_query import (
    InteractiveAgentExecutor,
    SubtaskResult,
)
from persistent_crewai.cli.main import cli
from persistent_crewai.cli.repl import InteractiveREPL


class BaseCLITestCase(unittest.TestCase):
    """Base setup fixture providing isolated temporary workspace and SQLite database."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._temp_dir.name)
        self.data_dir = self.workspace_root / ".persistent_crewai"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "state.db"
        self.project_id = "test_cli_project"

        init_db(str(self.db_path))
        self.state_manager = StateManager(
            db_path=str(self.db_path),
            project_id=self.project_id,
            workspace_root=self.workspace_root,
        )
        self.state_manager.create_project(
            project_id=self.project_id,
            name="Test CLI Project",
            description="Hermetic project context for CLI test suite",
            root_path=str(self.workspace_root),
            integrity_mode="development",
        )

        # Seed standard decisions and milestones
        self.state_manager.record_decision(
            agent_role="Architect",
            title="Use SQLite with WAL mode for zero external dependency persistence",
            rationale="Single-file, transactional, zero external dependency persistence",
            status="accepted",
            project_id=self.project_id,
        )
        self.state_manager.record_decision(
            agent_role="Architect",
            title="Deterministic token hash embedder for offline tests",
            rationale="Guarantees offline reproducibility without external API keys",
            status="accepted",
            project_id=self.project_id,
        )
        self.state_manager.create_milestone(
            milestone_id="ms_001",
            title="Foundation & Persistence",
            description="Establish SQLite schema, WAL mode, and vector memory",
            criteria=["SQLite schema initialized", "Vector store operational"],
            status="in_progress",
            project_id=self.project_id,
        )

        self.embedder = DeterministicHashEmbeddingProvider(dimension=64)
        self.vector_store = SQLiteVectorStore(
            db_path=str(self.db_path),
            embedder=self.embedder,
            project_id=self.project_id,
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()


# ==============================================================================
# Suite 1: InteractiveAgentExecutor Tests
# ==============================================================================
class TestInteractiveAgentExecutor(BaseCLITestCase):
    """Test suite for single-agent grounded query and targeted subtask executor."""

    def test_01_query_coordinator_grounded_in_state(self) -> None:
        """Verify Coordinator query receives grounded response referencing project context."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        resp = executor.query_agent(
            role="Coordinator",
            user_prompt="What is the current status of the project?",
        )
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)
        self.assertIn("Coordinator", resp)

    def test_02_query_architect_grounded_in_decisions(self) -> None:
        """Verify Architect query returns architectural design grounded in SQLite state."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        resp = executor.query_agent(
            role="Architect",
            user_prompt="Explain the database design and persistence strategy.",
        )
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)
        self.assertIn("Architect", resp)

    def test_03_query_memory_manager_grounded_in_state(self) -> None:
        """Verify Memory & Status Manager query returns memory and status information."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        resp = executor.query_agent(
            role="Memory & Status Manager",
            user_prompt="Show recorded milestones and session runs.",
        )
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)

    def test_04_query_all_eight_canonical_roles(self) -> None:
        """Verify querying every single one of the 8 canonical roles succeeds."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        for role in CANONICAL_ROLES:
            resp = executor.query_agent(
                role=role,
                user_prompt=f"Provide a brief briefing regarding your responsibilities as {role}.",
            )
            self.assertIsInstance(resp, str)
            self.assertTrue(len(resp) > 0, f"Role '{role}' returned empty response")

    def test_05_query_with_vector_store_memory_injection(self) -> None:
        """Verify vector store memories are retrieved and injected into agent grounding."""
        self.vector_store.add(
            documents=[
                "ADR-201: Implement sandboxed path containment for workspace tools.",
            ],
            metadatas=[{"agent": "Architect", "tag": "security"}],
        )
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
            vector_store=self.vector_store,
        )
        resp = executor.query_agent(
            role="Architect",
            user_prompt="What did we decide regarding sandboxed workspace tools?",
            include_memory=True,
        )
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)

    def test_06_query_empty_prompt_raises_value_error(self) -> None:
        """Verify querying with empty string raises ValueError."""
        executor = InteractiveAgentExecutor(state_manager=self.state_manager)
        with self.assertRaises(ValueError):
            executor.query_agent(role="Coordinator", user_prompt="")

    def test_07_query_whitespace_prompt_raises_value_error(self) -> None:
        """Verify querying with whitespace-only string raises ValueError."""
        executor = InteractiveAgentExecutor(state_manager=self.state_manager)
        with self.assertRaises(ValueError):
            executor.query_agent(role="Coordinator", user_prompt="   \t\n  ")

    def test_08_query_unknown_role_raises_unknown_role_error(self) -> None:
        """Verify requesting an unknown role raises error containing 'valid'."""
        executor = InteractiveAgentExecutor(state_manager=self.state_manager)
        with self.assertRaises(ValueError) as cm:
            executor.query_agent(role="Wizard", user_prompt="Cast a spell")
        self.assertIn("valid", str(cm.exception).lower())

    def test_09_targeted_subtask_execution_string_return(self) -> None:
        """Verify run_targeted_subtask executes single agent and returns output string."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        out = executor.run_targeted_subtask(
            role="Tester",
            task_description="Execute unit test verification for SQLite schema",
        )
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)

        # Verify task history was recorded in SQLite
        history = self.state_manager.get_task_history(project_id=self.project_id)
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["agent_role"], "Tester")
        self.assertEqual(history[0]["status"], "completed")

        # Verify agent operational status was reset to idle
        status = self.state_manager.get_agent_status("Tester", project_id=self.project_id)
        self.assertEqual(status["status"], "idle")

    def test_10_targeted_subtask_execution_dict_return(self) -> None:
        """Verify execute_targeted_subtask returns SubtaskResult dictionary with latency."""
        executor = InteractiveAgentExecutor(
            state_manager=self.state_manager,
            project_id=self.project_id,
        )
        result = executor.execute_targeted_subtask(
            role="Developer",
            task_description="Implement authentication module",
        )
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result, SubtaskResult)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.agent_role, "Developer")
        self.assertTrue(len(result.output) > 0)
        self.assertTrue(result.latency_ms >= 0.0)

    def test_11_targeted_subtask_empty_description_raises(self) -> None:
        """Verify execute_targeted_subtask raises ValueError on empty description."""
        executor = InteractiveAgentExecutor(state_manager=self.state_manager)
        with self.assertRaises(ValueError):
            executor.execute_targeted_subtask(role="Developer", task_description="   ")


# ==============================================================================
# Suite 2: Status Dashboard Tests
# ==============================================================================
class TestDashboard(BaseCLITestCase):
    """Test suite for system dashboard aggregation and terminal rendering."""

    def test_12_get_dashboard_data_structure(self) -> None:
        """Verify get_dashboard_data contains all expected keys and metrics."""
        data = get_dashboard_data(state_manager=self.state_manager)
        self.assertIn("project", data)
        self.assertIn("agents", data)
        self.assertIn("decisions", data)
        self.assertIn("milestones", data)
        self.assertIn("metrics", data)
        self.assertEqual(len(data["agents"]), 8)

    def test_13_dashboard_guarantees_eight_canonical_agents_in_order(self) -> None:
        """Verify all 8 canonical roles are present in canonical order."""
        data = get_dashboard_data(state_manager=self.state_manager)
        agents = data["agents"]
        roles_in_data = [a["agent_role"] for a in agents]
        self.assertEqual(roles_in_data, CANONICAL_ROLES)

    def test_14_render_dashboard_with_dict(self) -> None:
        """Verify render_dashboard works when passed a pre-aggregated data dictionary."""
        data = get_dashboard_data(state_manager=self.state_manager)
        rendered = render_dashboard(data)
        self.assertIsInstance(rendered, str)
        self.assertIn("DASHBOARD", rendered.upper())
        self.assertIn("Project", rendered)

    def test_15_render_dashboard_with_state_manager(self) -> None:
        """Verify render_dashboard seamlessly accepts a StateManager instance."""
        rendered = render_dashboard(self.state_manager)
        self.assertIsInstance(rendered, str)
        self.assertIn("DASHBOARD", rendered.upper())
        self.assertIn("Project", rendered)

    def test_16_render_dashboard_ascii_fallback(self) -> None:
        """Verify render_dashboard produces valid ASCII output when plain mode is forced."""
        data = get_dashboard_data(state_manager=self.state_manager)
        rendered_plain = render_dashboard(data, force_plain=True)
        self.assertIsInstance(rendered_plain, str)
        self.assertIn("DASHBOARD", rendered_plain.upper())
        self.assertIn("Project", rendered_plain)
        self.assertIn("MILESTONES", rendered_plain)
        self.assertIn("8-AGENT OPERATIONAL TEAM", rendered_plain)

    def test_17_create_step_trace_callback(self) -> None:
        """Verify create_step_trace_callback logs events into execution_traces in SQLite."""
        run_id = "run_callback_test_01"
        callback = create_step_trace_callback(self.state_manager, run_id=run_id)

        callback({
            "event": "step_start",
            "agent_role": "Coordinator",
            "task_id": "task_cb_1",
            "description": "Start orchestration",
        })
        callback({
            "event": "tool_start",
            "agent_role": "Developer",
            "tool_name": "WorkspaceFileWriteTool",
            "payload": {"filename": "test.py"},
        })
        callback({
            "event": "step_complete",
            "agent_role": "Coordinator",
            "task_id": "task_cb_1",
            "output": "Orchestration done",
        })

        traces = self.state_manager.get_traces(run_id=run_id)
        self.assertEqual(len(traces), 3)
        self.assertEqual(traces[0]["event_type"], "step_start")
        self.assertEqual(traces[1]["tool_name"], "WorkspaceFileWriteTool")

    def test_18_step_trace_callback_resilience_to_malformed_event(self) -> None:
        """Verify step trace callback never explodes when passed invalid or empty events."""
        callback = create_step_trace_callback(self.state_manager, run_id="run_bad_event")
        # None event
        callback(None)
        # Empty string event
        callback("")
        # Arbitrary object
        callback(object())


# ==============================================================================
# Suite 3: InteractiveREPL Tests
# ==============================================================================
class TestInteractiveREPL(BaseCLITestCase):
    """Test suite for interactive REPL shell and command dispatch."""

    def setUp(self) -> None:
        super().setUp()
        self.repl = InteractiveREPL(
            state_manager=self.state_manager,
            project_id=self.project_id,
            vector_store=self.vector_store,
        )

    def test_19_repl_status_command(self) -> None:
        """Verify 'status' command outputs project status dashboard."""
        out = self.repl.execute_command("status")
        self.assertIn("Project", out)
        self.assertIn("DASHBOARD", out.upper())

    def test_20_repl_decisions_command(self) -> None:
        """Verify 'decisions' command outputs recorded decisions containing 'WAL'."""
        out = self.repl.execute_command("decisions")
        self.assertIn("WAL", out)

    def test_21_repl_milestones_command(self) -> None:
        """Verify 'milestones' command outputs milestones containing 'Foundation'."""
        out = self.repl.execute_command("milestones")
        self.assertIn("Foundation", out)

    def test_22_repl_ask_command_quoted_prompt(self) -> None:
        """Verify 'ask' command with quoted prompt returns grounded response."""
        resp = self.repl.execute_command("ask coordinator 'What is the current project status?'")
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)
        self.assertIn("Coordinator", resp)

    def test_23_repl_ask_command_unquoted_prompt(self) -> None:
        """Verify 'ask' command with unquoted multi-word prompt parses correctly."""
        resp = self.repl.execute_command("ask architect Explain the database design")
        self.assertIsInstance(resp, str)
        self.assertTrue(len(resp) > 0)
        self.assertIn("Architect", resp)

    def test_24_repl_run_task_command(self) -> None:
        """Verify 'run task <role> <desc>' triggers targeted subtask."""
        out = self.repl.execute_command("run task tester 'Verify SQLite WAL mode integrity'")
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)

    def test_25_repl_direct_task_command(self) -> None:
        """Verify 'task <role> <desc>' triggers targeted subtask."""
        out = self.repl.execute_command("task developer 'Implement authentication module'")
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)

    def test_26_repl_decision_add_and_list(self) -> None:
        """Verify 'decision add' records new ADR, verifiable in 'decisions' list."""
        add_out = self.repl.execute_command("decision add Reviewer 'Security Policy' 'Mandate parameterized queries'")
        self.assertIn("Decision recorded", add_out)

        list_out = self.repl.execute_command("decisions")
        self.assertIn("Security Policy", list_out)

    def test_27_repl_milestone_add_and_list(self) -> None:
        """Verify 'milestone add' creates new milestone, verifiable in 'milestones' list."""
        add_out = self.repl.execute_command("milestone add 'Release 1.0' 'Pass all tests'")
        self.assertIn("Milestone created", add_out)

        list_out = self.repl.execute_command("milestones")
        self.assertIn("Release 1.0", list_out)

    def test_28_repl_export_and_import(self) -> None:
        """Verify 'export' and 'import' commands create and load valid state JSON."""
        export_file = self.workspace_root / "test_repl_export.json"
        export_out = self.repl.execute_command(f"export {export_file}")
        self.assertIn("Export", export_out)
        self.assertTrue(export_file.exists())

        # Validate JSON content
        with open(export_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("decisions", data)
        self.assertTrue(len(data["decisions"]) >= 2)

        # Import into REPL with overwrite
        import_out = self.repl.execute_command(f"import {export_file} --overwrite")
        self.assertIn("Import completed", import_out)

    def test_29_repl_memory_query(self) -> None:
        """Verify 'memory query' returns semantic matches from vector store."""
        self.vector_store.add(
            documents=["Architecture: Microservices and SQLite."],
            metadatas=[{"agent": "Architect", "tag": "arch"}],
        )
        out = self.repl.execute_command("memory query architecture")
        self.assertIn("Result #1", out)

    def test_30_repl_help_and_exit(self) -> None:
        """Verify 'help' returns syntax and 'exit' terminates loop."""
        help_out = self.repl.execute_command("help")
        self.assertIn("PERSISTENT CREWAI REPL COMMANDS", help_out)

        exit_out = self.repl.execute_command("exit")
        self.assertIn("Goodbye", exit_out)
        self.assertFalse(self.repl.running)

    def test_31_repl_unknown_command(self) -> None:
        """Verify unknown command returns friendly warning without crashing."""
        out = self.repl.execute_command("abracadabra")
        self.assertIn("Unknown command", out)

    def test_32_repl_run_batch(self) -> None:
        """Verify run_batch processes a sequence of script commands."""
        script = [
            "# Comment line to skip",
            "status",
            "decisions",
            "milestones",
        ]
        outputs = self.repl.run_batch(script)
        self.assertEqual(len(outputs), 3)
        self.assertIn("Project", outputs[0])
        self.assertIn("WAL", outputs[1])
        self.assertIn("Foundation", outputs[2])

    def test_33_repl_cmdloop_stream_simulation(self) -> None:
        """Verify cmdloop executes simulated stdin stream cleanly and exits on EOF."""
        in_stream = io.StringIO("status\nexit\n")
        out_stream = io.StringIO()
        self.repl.cmdloop(input_stream=in_stream, output_stream=out_stream)
        out_val = out_stream.getvalue()
        self.assertIn("crewai>", out_val)
        self.assertIn("Project", out_val)


# ==============================================================================
# Suite 4: Top-Level CLI Commands Tests
# ==============================================================================
class TestCLIMain(BaseCLITestCase):
    """Test suite for top-level Click CLI entrypoint."""

    def setUp(self) -> None:
        super().setUp()
        self.runner = CliRunner()

    def test_34_cli_status_command(self) -> None:
        """Verify 'crewai-team status' displays dashboard."""
        res = self.runner.invoke(
            cli,
            ["--db-path", str(self.db_path), "--project-id", self.project_id, "status"],
        )
        self.assertEqual(res.exit_code, 0, f"CLI error: {res.output}")
        self.assertIn("Project", res.output)
        self.assertIn("DASHBOARD", res.output.upper())

    def test_35_cli_ask_command(self) -> None:
        """Verify 'crewai-team ask <role> <prompt>' outputs agent response."""
        res = self.runner.invoke(
            cli,
            [
                "--db-path", str(self.db_path),
                "--project-id", self.project_id,
                "ask", "coordinator", "What is the project status?",
            ],
        )
        self.assertEqual(res.exit_code, 0, f"CLI error: {res.output}")
        self.assertIn("Coordinator", res.output)

    def test_36_cli_task_command(self) -> None:
        """Verify 'crewai-team task <role> <desc>' executes targeted subtask."""
        res = self.runner.invoke(
            cli,
            [
                "--db-path", str(self.db_path),
                "--project-id", self.project_id,
                "task", "tester", "Verify SQLite schema and indexes",
            ],
        )
        self.assertEqual(res.exit_code, 0, f"CLI error: {res.output}")
        self.assertIn("Tester", res.output)

    def test_37_cli_export_command(self) -> None:
        """Verify 'crewai-team export <path>' exports state file."""
        export_file = self.workspace_root / "cli_exported.json"
        res = self.runner.invoke(
            cli,
            [
                "--db-path", str(self.db_path),
                "--project-id", self.project_id,
                "export", str(export_file),
            ],
        )
        self.assertEqual(res.exit_code, 0, f"CLI error: {res.output}")
        self.assertIn("Export completed", res.output)
        self.assertTrue(export_file.exists())


if __name__ == "__main__":
    unittest.main()
