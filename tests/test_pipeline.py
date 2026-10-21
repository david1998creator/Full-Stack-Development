"""
Comprehensive unit and integration test suite for Persistent CrewAI Milestone 3:
SequentialPipelineRunner (Feature 24), HierarchicalPipelineRunner (Feature 25),
context artifact propagation, state persistence, crash recovery & resumption,
and the run_project_workflow() contract.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure src is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.persistence.session import SessionResumptionManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.agents.crew_builder import build_agent
from persistent_crewai.agents.llm_factory import DeterministicMockLLM
from persistent_crewai.agents.runner import (
    HierarchicalPipelineRunner,
    SequentialPipelineRunner,
    WORKFLOW_STEPS,
    run_project_workflow,
)


class TestSequentialPipelineRunner(unittest.TestCase):
    """Test suite for SequentialPipelineRunner (Feature 24)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "test_state.db"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id="test_seq_proj",
        )
        self.state_manager.create_project(
            project_id="test_seq_proj",
            name="Sequential Test Project",
            description="Testing sequential pipeline execution",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_p01_sequential_runner_initialization(self):
        """Verify SequentialPipelineRunner initializes with StateManager and team of 8 agents."""
        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        self.assertIs(runner.state_manager, self.state_manager)
        self.assertEqual(len(runner.team), 8)
        self.assertIn("Coordinator", runner.team)
        self.assertIn("Architect", runner.team)
        self.assertIn("Developer", runner.team)

    def test_p02_feature_development_full_pipeline(self):
        """Verify full 8-step feature_development pipeline runs to completion."""
        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        result = runner.run(
            workflow_type="feature_development",
            inputs={"feature_title": "Implement SQLite WAL Mode", "priority": "high"},
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "completed")
        self.assertIsNone(result.get("error_message"))
        self.assertEqual(result.get("workflow_type"), "feature_development")
        self.assertEqual(result.get("mode"), "sequential")

        tasks = result.get("tasks_executed", [])
        self.assertEqual(len(tasks), 8)

        # Verify step order
        expected_steps = [s[1] for s in WORKFLOW_STEPS["feature_development"]]
        actual_steps = [t["task_name"] for t in tasks]
        self.assertEqual(actual_steps, expected_steps)

        # Verify final output
        final_output = result.get("final_output", "")
        self.assertTrue(len(final_output) > 0)
        self.assertTrue(result.get("latency_total_ms", 0) >= 0)

    def test_p03_upstream_context_propagation(self):
        """Verify outputs from upstream agents are propagated into downstream prompts."""
        call_log = []

        class TrackingMockLLM(DeterministicMockLLM):
            def predict(self, text: str, **kwargs: Any) -> str:
                call_log.append(text)
                return super().predict(text, **kwargs)

        custom_llm = TrackingMockLLM()
        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
            llm=custom_llm,
        )
        runner.run(
            workflow_type="feature_development",
            inputs={"spec": "Context Propagation Test"},
        )

        # Developer prompt (step 3) must contain Architect and Designer outputs
        dev_prompts = [p for p in call_log if "Developer" in p]
        self.assertTrue(len(dev_prompts) > 0)
        dev_prompt = dev_prompts[0]
        self.assertIn("Architect", dev_prompt)
        self.assertIn("Designer", dev_prompt)

    def test_p04_state_persistence_and_latency_tracking(self):
        """Verify workflow runs, task history, traces, and latencies are persisted in SQLite."""
        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        run_res = runner.run(workflow_type="code_review")
        run_id = run_res["run_id"]

        # Check workflow_runs table
        wf_record = self.state_manager.get_workflow_run(run_id)
        self.assertIsNotNone(wf_record)
        self.assertEqual(wf_record["status"], "completed")

        # Check task_history table
        history = self.state_manager.get_task_history(run_id=run_id)
        self.assertEqual(len(history), 4)
        for task in history:
            self.assertEqual(task["status"], "completed")
            meta = task.get("metadata", {})
            self.assertIn("latency_ms", meta)
            self.assertGreaterEqual(meta["latency_ms"], 0)

        # Check execution_traces table
        traces = self.state_manager.list_traces(run_id=run_id)
        self.assertTrue(len(traces) >= 8)  # At least start and complete for 4 tasks

    def test_p05_bugfix_pipeline_execution(self):
        """Verify bugfix pipeline executes all 7 standard steps."""
        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        result = runner.run(
            workflow_type="bugfix",
            inputs={"issue_id": "#42", "bug_desc": "Fix SQLite lock contention"},
        )
        self.assertEqual(result.get("status"), "completed")
        tasks = result.get("tasks_executed", [])
        self.assertEqual(len(tasks), 7)
        self.assertEqual(tasks[0]["agent_role"], "GitHub Manager")
        self.assertEqual(tasks[1]["agent_role"], "Developer")
        self.assertEqual(tasks[2]["agent_role"], "Tester")

    def test_p06_step_failure_and_error_trapping(self):
        """Verify task failure traps exception, sets agent to error, and records failed status."""
        class FailingLLM:
            def predict(self, *args, **kwargs):
                raise ValueError("Simulated code synthesis crash")

        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        # Inject failing LLM for Developer
        failing_dev = build_agent("Developer", llm=FailingLLM(), state_manager=self.state_manager)
        runner.team["Developer"] = failing_dev

        result = runner.run(workflow_type="feature_development")
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("Simulated code synthesis crash", result.get("error_message", ""))

        # Check StateManager records
        agent_status = self.state_manager.get_agent_status("Developer")
        self.assertEqual(agent_status["status"], "error")
        self.assertIn("Simulated code synthesis crash", agent_status["last_error"])

        wf = self.state_manager.get_workflow_run(result["run_id"])
        self.assertEqual(wf["status"], "failed")


class TestHierarchicalPipelineRunner(unittest.TestCase):
    """Test suite for HierarchicalPipelineRunner (Feature 25)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "test_state.db"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id="test_hier_proj",
        )
        self.state_manager.create_project(
            project_id="test_hier_proj",
            name="Hierarchical Test Project",
            description="Testing hierarchical pipeline execution",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_h01_manager_role_attribute(self):
        """Verify runner explicitly exposes manager_role = 'Coordinator'."""
        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        self.assertEqual(runner.manager_role, "Coordinator")
        self.assertEqual(HierarchicalPipelineRunner.manager_role, "Coordinator")

    def test_h02_dynamic_delegation_to_architect(self):
        """Verify Coordinator dynamically delegates architecture task to Architect."""
        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        result = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Review architecture blueprint and SQLite schema"},
        )

        self.assertEqual(result.get("status"), "completed")
        tasks = result.get("tasks_executed", [])
        self.assertEqual(len(tasks), 2)  # Specialist task + Coordinator synthesis

        self.assertEqual(tasks[0]["agent_role"], "Architect")
        self.assertEqual(tasks[1]["agent_role"], "Coordinator")
        self.assertTrue(len(result.get("final_output", "")) > 0)

    def test_h03_dynamic_delegation_to_reviewer(self):
        """Verify Coordinator delegates security audit task to Reviewer."""
        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        result = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Audit security vulnerabilities and PEP-8 compliance"},
        )
        self.assertEqual(result.get("status"), "completed")
        tasks = result.get("tasks_executed", [])
        self.assertEqual(tasks[0]["agent_role"], "Reviewer")

    def test_h04_step_telemetry_streaming(self):
        """Verify step_callback receives delegation and execution telemetry events."""
        events: List[Dict[str, Any]] = []

        def callback(event_dict):
            events.append(event_dict)

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
            step_callback=callback,
        )
        runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Design UI/UX tokens and CLI style"},
        )

        event_types = [e.get("event") for e in events]
        self.assertIn("delegation", event_types)
        self.assertIn("step_start", event_types)
        self.assertIn("step_complete", event_types)

    def test_h05_hierarchical_error_trapping(self):
        """Verify specialist exception is trapped, logged, and returns structured failure."""
        class FailingLLM:
            def predict(self, *args, **kwargs):
                raise RuntimeError("Specialist execution crash")

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        failing_tester = build_agent("Tester", llm=FailingLLM(), state_manager=self.state_manager)
        runner.team["Tester"] = failing_tester

        result = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Run pytest suite and test coverage"},
        )
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("Specialist execution crash", result.get("error_message", ""))


class TestPipelineResumption(unittest.TestCase):
    """Test suite for crash recovery and session resumption."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "test_state.db"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id="test_resume_proj",
        )
        self.state_manager.create_project(
            project_id="test_resume_proj",
            name="Resumption Project",
            description="Testing crash recovery and workflow resumption",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_r01_inspect_state_auto_recover(self):
        """Verify inspect_state(auto_recover=True) resets busy agents and marks in-progress tasks as interrupted."""
        sm = self.state_manager
        run_id = "run_crash_01"
        sm.create_workflow_run(run_id=run_id, workflow_type="feature_development")

        # Simulate task in progress and busy agent at crash time
        task_id = "task_crash_01"
        sm.record_task_start(task_id, run_id, "Developer", "feature_implementation")
        sm.start_task(task_id)

        status_before = sm.get_agent_status("Developer")
        self.assertEqual(status_before["status"], "busy")

        # Run crash recovery
        resumption_mgr = SessionResumptionManager(db_path=str(self.db_path), project_id="test_resume_proj")
        inspection = resumption_mgr.inspect_state(auto_recover=True)

        self.assertIn("Developer", inspection["recovered_agents"])

        # Agent must be reset to idle
        status_after = sm.get_agent_status("Developer")
        self.assertEqual(status_after["status"], "idle")

        # Task must be marked interrupted
        task_rec = sm.get_task(task_id)
        self.assertEqual(task_rec["status"], "interrupted")

    def test_r02_resume_workflow_skips_completed_and_continues(self):
        """Verify resuming an interrupted workflow skips already completed steps and preserves context."""
        sm = self.state_manager
        run_id = "run_crash_02"
        sm.create_workflow_run(run_id=run_id, workflow_type="feature_development")

        # Complete step 1 (Architect)
        task_arch = f"{run_id}_architecture_design"
        sm.record_task_start(task_arch, run_id, "Architect", "architecture_design")
        sm.start_task(task_arch)
        sm.record_task_completion(task_arch, "Architect", "ADR-001: Adopt SQLite with WAL mode.")

        # Complete step 2 (Designer)
        task_des = f"{run_id}_ui_ux_specification"
        sm.record_task_start(task_des, run_id, "Designer", "ui_ux_specification")
        sm.start_task(task_des)
        sm.record_task_completion(task_des, "Designer", "CLI spec: cyan headers and green status.")

        # Interrupted at step 3 (Developer)
        task_dev = f"{run_id}_feature_implementation"
        sm.record_task_start(task_dev, run_id, "Developer", "feature_implementation")
        sm.start_task(task_dev)

        # Resume the pipeline
        runner = SequentialPipelineRunner(
            state_manager=sm,
            workspace_root=self.workspace_root,
        )
        res = runner.run(
            workflow_type="feature_development",
            resume_run_id=run_id,
        )

        self.assertEqual(res.get("status"), "completed")
        tasks_executed = res.get("tasks_executed", [])

        # Should only execute steps 3 through 8 (6 steps)
        executed_names = [t["task_name"] for t in tasks_executed]
        self.assertNotIn("architecture_design", executed_names)
        self.assertNotIn("ui_ux_specification", executed_names)
        self.assertIn("feature_implementation", executed_names)
        self.assertIn("executive_summary", executed_names)
        self.assertEqual(len(executed_names), 6)


class TestRunProjectWorkflowContract(unittest.TestCase):
    """Test suite for the unified run_project_workflow interface contract."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "test_state.db"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id="test_contract_proj",
        )
        self.state_manager.create_project(
            project_id="test_contract_proj",
            name="Contract Project",
            description="Testing run_project_workflow contract",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_c01_run_project_workflow_sequential(self):
        """Verify run_project_workflow in sequential mode returns standard response schema."""
        res = run_project_workflow(
            workflow_type="feature_development",
            mode="sequential",
            state_manager=self.state_manager,
            inputs={"scope": "testing"},
        )
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("status"), "completed")
        self.assertEqual(res.get("mode"), "sequential")
        self.assertIn("tasks_executed", res)
        self.assertIn("latency_total_ms", res)
        self.assertIn("final_output", res)

    def test_c02_run_project_workflow_hierarchical(self):
        """Verify run_project_workflow in hierarchical mode returns standard response schema."""
        res = run_project_workflow(
            workflow_type="targeted_subtask",
            mode="hierarchical",
            state_manager=self.state_manager,
            inputs={"task_description": "Review code quality"},
        )
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("status"), "completed")
        self.assertEqual(res.get("mode"), "hierarchical")

    def test_c03_invalid_mode_raises_valueerror(self):
        """Verify invalid mode argument raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            run_project_workflow(mode="unsupported_quantum_flow")
        self.assertIn("Invalid execution mode", str(ctx.exception))

    def test_c04_empty_workflow_type_raises_valueerror(self):
        """Verify empty workflow_type raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            run_project_workflow(workflow_type="")
        self.assertIn("workflow_type must be a non-empty string", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
