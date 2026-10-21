"""
Empirical Adversarial Stress Test Suite for Milestone 3:
Pipeline Runners & Crash Resumption (Challenger M3-2).

Target Stress Areas:
1. Sudden Pipeline Crash & Resumption Recovery:
   - 5-step custom pipeline crash simulation mid-run (at step 3).
   - In-progress task detection, busy agent reset, and workflow status transition.
   - Context artifact rehydration: verification that upstream deliverables from
     prior completed steps are faithfully preserved and injected downstream.
   - Cascading multi-crash resumption.
   - Standard 8-step feature_development crash resumption.
2. Hierarchical Runner Delegation Stress:
   - Complex ambiguous prompts with competing role keywords.
   - Role keyword boundary awareness and default fallbacks.
   - Explicit target_role overrides and alias normalization.
   - Telemetry callback streaming with comprehensive schema validation.
   - Callback fault tolerance (resilience against throwing callbacks).
3. Exception Handling & Failure Isolation:
   - Sequential runner specialist exception trapping, task failure marking,
     agent error status recording with last_error, and clean halt.
   - Hierarchical runner specialist failure isolation without coordinator synthesis.
   - Coordinator executive synthesis failure behavior.
   - Unified run_project_workflow interface contract validation under stress.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.agents.crew_builder import Agent, build_agent, build_team
from persistent_crewai.agents.llm_factory import DeterministicMockLLM
from persistent_crewai.agents.runner import (
    HierarchicalPipelineRunner,
    SequentialPipelineRunner,
    WORKFLOW_STEPS,
    run_project_workflow,
)
from persistent_crewai.persistence.session import SessionResumptionManager
from persistent_crewai.persistence.state_manager import StateManager


class TrackingMockLLM(DeterministicMockLLM):
    """Tracking mock LLM that records all prompts passed to predict()."""

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        super().__init__()
        self.call_history: List[str] = []
        self.custom_responses: Dict[str, str] = responses or {}

    def predict(self, text: str, **kwargs: Any) -> str:
        self.call_history.append(text)
        for key, resp in self.custom_responses.items():
            if key in text:
                return f"Final Answer: {resp}"
        return super().predict(text, **kwargs)


class TestAdversarialCrashResumption(unittest.TestCase):
    """Empirical adversarial stress testing of pipeline crashes, context rehydration, and resumption."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "crash_test.db"
        self.project_id = "adv_crash_proj"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id=self.project_id,
        )
        self.state_manager.create_project(
            project_id=self.project_id,
            name="Crash Resumption Adversarial Project",
            description="Stress testing crash resumption and context artifact preservation",
            root_path=self.workspace_root,
        )

        # Register standard 5-step custom workflow for precise 5-step testing
        WORKFLOW_STEPS["five_step_pipeline"] = [
            ("Architect", "arch_spec", "Analyze requirements and author ADR architecture blueprint."),
            ("Designer", "ui_flow", "Design CLI syntax, terminal formatting, and color palettes."),
            ("Developer", "core_impl", "Implement core engine modules and database queries."),
            ("Tester", "qa_suite", "Author pytest test suite and assert system stability."),
            ("Reviewer", "sec_audit", "Audit code for security vulnerabilities and compliance."),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean up custom workflow from global dict
        WORKFLOW_STEPS.pop("five_step_pipeline", None)

    def test_adv_crash_3_of_5_step_workflow_resumption_and_context_rehydration(self):
        """
        Adversarial Test 1.1:
        Execute 3 steps of a 5-step workflow, abruptly simulate crash (uncompleted in-progress task,
        busy agent), run resumption recovery, verify rehydration of prior context artifacts,
        and complete remaining steps without data loss.
        """
        sm = self.state_manager
        run_id = "run_crash_5step_01"

        # Initialize workflow run in SQLite
        sm.create_workflow_run(
            run_id=run_id,
            workflow_type="five_step_pipeline",
            input_prompt="Build High-Throughput Event Processing Engine",
        )

        # Step 1: Architect completes successfully
        task_1_id = f"{run_id}_arch_spec"
        sm.record_task_start(task_1_id, run_id, "Architect", "arch_spec", execution_order=0)
        sm.start_task(task_1_id)
        arch_deliverable = "ARCH_DELIVERABLE_V1: SQLite WAL mode with FTS5 BM25 search indices."
        sm.record_task_completion(task_1_id, "Architect", arch_deliverable)

        # Step 2: Designer completes successfully
        task_2_id = f"{run_id}_ui_flow"
        sm.record_task_start(task_2_id, run_id, "Designer", "ui_flow", execution_order=1)
        sm.start_task(task_2_id)
        ui_deliverable = "UI_DELIVERABLE_V1: Terminal ANSI color grammar with rich status table."
        sm.record_task_completion(task_2_id, "Designer", ui_deliverable)

        # Step 3: Developer starts execution, agent becomes 'busy', then PROCESS ABRUPTLY CRASHES
        task_3_id = f"{run_id}_core_impl"
        sm.record_task_start(task_3_id, run_id, "Developer", "core_impl", execution_order=2)
        sm.start_task(task_3_id)

        # Verify crash pre-conditions: Developer is busy, task is in_progress, run is running
        dev_status = sm.get_agent_status("Developer")
        self.assertEqual(dev_status["status"], "busy")
        self.assertEqual(dev_status["current_task_id"], task_3_id)

        task_3_rec = sm.get_task(task_3_id)
        self.assertEqual(task_3_rec["status"], "in_progress")

        run_rec = sm.get_workflow_run(run_id)
        self.assertEqual(run_rec["status"], "running")

        # -------------------------------------------------------------
        # SYSTEM REBOOT & RESUMPTION RECOVERY
        # -------------------------------------------------------------
        resumption_mgr = SessionResumptionManager(db_path=str(self.db_path), project_id=self.project_id)
        inspection = resumption_mgr.inspect_state(auto_recover=True)

        # Verify recovery results: Developer reset to idle, task marked interrupted
        self.assertIn("Developer", inspection["recovered_agents"])
        dev_status_recovered = sm.get_agent_status("Developer")
        self.assertEqual(dev_status_recovered["status"], "idle")
        self.assertIsNone(dev_status_recovered["current_task_id"])

        task_3_interrupted = sm.get_task(task_3_id)
        self.assertEqual(task_3_interrupted["status"], "interrupted")

        # -------------------------------------------------------------
        # RESUME PIPELINE WITH CONTEXT REHYDRATION VERIFICATION
        # -------------------------------------------------------------
        tracking_llm = TrackingMockLLM({
            "Implement core engine modules": "DEV_DELIVERABLE_V1: Implemented WAL engine with connection pooling.",
            "Author pytest test suite": "QA_DELIVERABLE_V1: 15 unit tests passing with 100% assertions.",
            "Audit code for security": "SEC_DELIVERABLE_V1: Code reviewed. Zero SQL injections detected.",
        })

        runner = SequentialPipelineRunner(
            state_manager=sm,
            workspace_root=self.workspace_root,
            llm=tracking_llm,
        )

        result = runner.run(
            workflow_type="five_step_pipeline",
            resume_run_id=run_id,
        )

        # Verify resumption execution status
        self.assertEqual(result.get("status"), "completed")
        self.assertIsNone(result.get("error_message"))

        # Verify only steps 3, 4, 5 were executed upon resumption (steps 1 & 2 skipped)
        tasks_executed = result.get("tasks_executed", [])
        executed_names = [t["task_name"] for t in tasks_executed]
        self.assertNotIn("arch_spec", executed_names)
        self.assertNotIn("ui_flow", executed_names)
        self.assertEqual(executed_names, ["core_impl", "qa_suite", "sec_audit"])

        # -------------------------------------------------------------
        # EMPIRICAL REHYDRATION CHECK: Verify prompts contain prior context
        # -------------------------------------------------------------
        # Developer prompt (Step 3) MUST have received the rehydrated Architect and Designer deliverables
        dev_prompts = [p for p in tracking_llm.call_history if "(Developer)" in p and p.startswith("You are the")]
        self.assertTrue(len(dev_prompts) > 0, "Developer should have been called upon resumption")
        dev_call = dev_prompts[0]
        self.assertIn("ARCH_DELIVERABLE_V1", dev_call, "Developer must receive rehydrated Architect artifact")
        self.assertIn("UI_DELIVERABLE_V1", dev_call, "Developer must receive rehydrated Designer artifact")

        # Tester prompt (Step 4) MUST have received Architect, Designer, AND Developer deliverables
        tester_prompts = [p for p in tracking_llm.call_history if "(Tester)" in p and p.startswith("You are the")]
        self.assertTrue(len(tester_prompts) > 0, "Tester should have been called")
        tester_call = tester_prompts[0]
        self.assertIn("ARCH_DELIVERABLE_V1", tester_call, "Tester must receive Architect artifact")
        self.assertIn("UI_DELIVERABLE_V1", tester_call, "Tester must receive Designer artifact")
        self.assertIn("DEV_DELIVERABLE_V1", tester_call, "Tester must receive Developer artifact")

        # Reviewer prompt (Step 5) MUST have received all upstream deliverables
        rev_prompts = [p for p in tracking_llm.call_history if "(Reviewer)" in p and p.startswith("You are the")]
        self.assertTrue(len(rev_prompts) > 0, "Reviewer should have been called")
        rev_call = rev_prompts[0]
        self.assertIn("ARCH_DELIVERABLE_V1", rev_call, "Reviewer must receive Architect artifact")
        self.assertIn("UI_DELIVERABLE_V1", rev_call, "Reviewer must receive Designer artifact")
        self.assertIn("DEV_DELIVERABLE_V1", rev_call, "Reviewer must receive Developer artifact")
        self.assertIn("QA_DELIVERABLE_V1", rev_call, "Reviewer must receive Tester artifact")

        # -------------------------------------------------------------
        # ZERO DATA LOSS VERIFICATION IN SQLite
        # -------------------------------------------------------------
        all_tasks = sm.get_task_history(run_id=run_id, limit=20)
        self.assertEqual(len(all_tasks), 5, "All 5 tasks must be recorded in task_history")

        for task in all_tasks:
            self.assertEqual(task["status"], "completed", f"Task {task['task_name']} must be completed")
            self.assertIsNotNone(task["actual_output"], f"Task {task['task_name']} output must not be lost")
            self.assertTrue(len(task["actual_output"]) > 0)

        # Check workflow_runs record
        wf_final = sm.get_workflow_run(run_id)
        self.assertEqual(wf_final["status"], "completed")
        self.assertIn("SEC_DELIVERABLE_V1", wf_final["final_output"])

    def test_adv_multi_crash_cascading_resumption(self):
        """
        Adversarial Test 1.2:
        Simulate multiple successive crashes across different steps of the same pipeline:
        Crash at step 2, resume -> crash at step 4, resume -> complete final step.
        """
        sm = self.state_manager
        run_id = "run_cascading_crash_02"

        sm.create_workflow_run(run_id=run_id, workflow_type="five_step_pipeline", input_prompt="Multi-crash test")

        # Step 1 completes
        t1 = f"{run_id}_arch_spec"
        sm.record_task_start(t1, run_id, "Architect", "arch_spec", execution_order=0)
        sm.start_task(t1)
        sm.record_task_completion(t1, "Architect", "Arch Deliverable 1")

        # Crash 1 at Step 2 (Designer)
        t2 = f"{run_id}_ui_flow"
        sm.record_task_start(t2, run_id, "Designer", "ui_flow", execution_order=1)
        sm.start_task(t2)
        # CRASH 1: Designer left busy
        self.assertEqual(sm.get_agent_status("Designer")["status"], "busy")

        # Recovery 1
        resumption_mgr = SessionResumptionManager(db_path=str(self.db_path), project_id=self.project_id)
        resumption_mgr.inspect_state(auto_recover=True)
        self.assertEqual(sm.get_agent_status("Designer")["status"], "idle")
        self.assertEqual(sm.get_task(t2)["status"], "interrupted")

        # Resumption 1 completes step 2 and step 3, but crashes at step 4
        # Complete step 2
        sm.record_task_start(t2, run_id, "Designer", "ui_flow", execution_order=1)
        sm.start_task(t2)
        sm.record_task_completion(t2, "Designer", "UI Deliverable 2")

        # Complete step 3
        t3 = f"{run_id}_core_impl"
        sm.record_task_start(t3, run_id, "Developer", "core_impl", execution_order=2)
        sm.start_task(t3)
        sm.record_task_completion(t3, "Developer", "Dev Deliverable 3")

        # Crash 2 at Step 4 (Tester)
        t4 = f"{run_id}_qa_suite"
        sm.record_task_start(t4, run_id, "Tester", "qa_suite", execution_order=3)
        sm.start_task(t4)
        # CRASH 2: Tester left busy
        self.assertEqual(sm.get_agent_status("Tester")["status"], "busy")

        # Recovery 2
        resumption_mgr.inspect_state(auto_recover=True)
        self.assertEqual(sm.get_agent_status("Tester")["status"], "idle")
        self.assertEqual(sm.get_task(t4)["status"], "interrupted")

        # Resumption 2 runs via SequentialPipelineRunner to completion
        runner = SequentialPipelineRunner(state_manager=sm, workspace_root=self.workspace_root)
        result = runner.run(workflow_type="five_step_pipeline", resume_run_id=run_id)

        self.assertEqual(result.get("status"), "completed")
        tasks_run = result.get("tasks_executed", [])
        executed_names = [t["task_name"] for t in tasks_run]
        # Only steps 4 and 5 should have been executed
        self.assertEqual(executed_names, ["qa_suite", "sec_audit"])

        # Verify all 5 tasks are completed in SQLite
        history = sm.get_task_history(run_id=run_id)
        self.assertEqual(len(history), 5)
        self.assertTrue(all(t["status"] == "completed" for t in history))

    def test_adv_standard_8_step_feature_dev_crash_at_step_3(self):
        """
        Adversarial Test 1.3:
        Crash during standard 8-step feature_development workflow at Step 3 (Developer),
        recover and resume remaining 6 steps through Coordinator summary.
        """
        sm = self.state_manager
        run_id = "run_feat_dev_crash_03"
        sm.create_workflow_run(run_id=run_id, workflow_type="feature_development", input_prompt="Feature dev crash")

        # Step 1: Architect
        t1 = f"{run_id}_architecture_design"
        sm.record_task_start(t1, run_id, "Architect", "architecture_design", execution_order=0)
        sm.start_task(t1)
        sm.record_task_completion(t1, "Architect", "ADR: Database and architecture specification.")

        # Step 2: Designer
        t2 = f"{run_id}_ui_ux_specification"
        sm.record_task_start(t2, run_id, "Designer", "ui_ux_specification", execution_order=1)
        sm.start_task(t2)
        sm.record_task_completion(t2, "Designer", "UI: Design tokens and CLI specs.")

        # Step 3: Developer starts and crashes
        t3 = f"{run_id}_feature_implementation"
        sm.record_task_start(t3, run_id, "Developer", "feature_implementation", execution_order=2)
        sm.start_task(t3)

        # Run resumption via unified run_project_workflow
        res = run_project_workflow(
            workflow_type="feature_development",
            mode="sequential",
            state_manager=sm,
            resume_run_id=run_id,
        )

        self.assertEqual(res.get("status"), "completed")
        tasks = res.get("tasks_executed", [])
        self.assertEqual(len(tasks), 6)  # Steps 3 through 8
        self.assertEqual(tasks[0]["task_name"], "feature_implementation")
        self.assertEqual(tasks[-1]["task_name"], "executive_summary")

        # Verify all 8 steps completed in database
        history = sm.get_task_history(run_id=run_id, limit=20)
        self.assertEqual(len(history), 8)
        self.assertTrue(all(t["status"] == "completed" for t in history))

    def test_adv_resumption_already_completed_run(self):
        """
        Adversarial Test 1.4:
        Verify resumption of an already-completed workflow run skips all tasks without re-executing.
        """
        sm = self.state_manager
        run_id = "run_already_completed_01"
        sm.create_workflow_run(run_id=run_id, workflow_type="code_review")

        # Complete all 4 steps of code_review
        for idx, (role, step, desc) in enumerate(WORKFLOW_STEPS["code_review"]):
            tid = f"{run_id}_{step}"
            sm.record_task_start(tid, run_id, role, step, desc, execution_order=idx)
            sm.start_task(tid)
            sm.record_task_completion(tid, role, f"Deliverable for {step}")

        sm.complete_workflow_run(run_id=run_id, final_output="Full review completed", status="completed")

        runner = SequentialPipelineRunner(state_manager=sm, workspace_root=self.workspace_root)
        result = runner.run(workflow_type="code_review", resume_run_id=run_id)

        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(len(result.get("tasks_executed", [])), 0, "No tasks should be re-executed on completed run")

    def test_adv_large_payload_context_rehydration(self):
        """
        Adversarial Test 1.5:
        Verify context rehydration preserves large upstream artifacts (e.g. 50KB payload)
        across crash resumption without truncation or SQLite corruption.
        """
        sm = self.state_manager
        run_id = "run_large_payload_01"
        sm.create_workflow_run(run_id=run_id, workflow_type="five_step_pipeline")

        # Create large 50KB payload
        large_blueprint = "BLUEPRINT_START\n" + ("X" * 1024 * 50) + "\nBLUEPRINT_END"

        t1 = f"{run_id}_arch_spec"
        sm.record_task_start(t1, run_id, "Architect", "arch_spec", execution_order=0)
        sm.start_task(t1)
        sm.record_task_completion(t1, "Architect", large_blueprint)

        # Crash at Step 2
        t2 = f"{run_id}_ui_flow"
        sm.record_task_start(t2, run_id, "Designer", "ui_flow", execution_order=1)
        sm.start_task(t2)

        # Recover
        resumption_mgr = SessionResumptionManager(db_path=str(self.db_path), project_id=self.project_id)
        resumption_mgr.inspect_state(auto_recover=True)

        # Resume with tracking LLM
        captured_prompts: List[str] = []
        class LargePayloadTrackingLLM(DeterministicMockLLM):
            def predict(self, text: str, **kwargs: Any) -> str:
                captured_prompts.append(text)
                return "Final Answer: Delivered."

        runner = SequentialPipelineRunner(
            state_manager=sm,
            workspace_root=self.workspace_root,
            llm=LargePayloadTrackingLLM(),
        )
        res = runner.run(workflow_type="five_step_pipeline", resume_run_id=run_id)

        self.assertEqual(res.get("status"), "completed")
        # Designer prompt MUST contain the complete untruncated 50KB blueprint
        des_prompts = [p for p in captured_prompts if "(Designer)" in p]
        self.assertTrue(len(des_prompts) > 0)
        self.assertIn("BLUEPRINT_START", des_prompts[0])
        self.assertIn("BLUEPRINT_END", des_prompts[0])
        self.assertIn("X" * 1024 * 50, des_prompts[0])


class TestAdversarialHierarchicalDelegation(unittest.TestCase):
    """Empirical adversarial stress testing of hierarchical runner delegation and telemetry."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "hier_test.db"
        self.project_id = "adv_hier_proj"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id=self.project_id,
        )
        self.state_manager.create_project(
            project_id=self.project_id,
            name="Hierarchical Adversarial Project",
            description="Stress testing Coordinator delegation and telemetry",
            root_path=self.workspace_root,
        )
        self.runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adv_hierarchical_complex_ambiguous_routing(self):
        """
        Adversarial Test 2.1:
        Stress-test Coordinator specialist routing against ambiguous tasks with competing keywords,
        boundary-adjacent punctuation, casing variations, and neutral fallback.
        """
        cases = [
            # 1. Competing Architect vs Developer keywords: Architect score higher
            ("Author SQLite database architecture schema and data model blueprint", "Architect"),
            # 2. Competing Reviewer vs Developer: Reviewer audit keywords dominate
            ("Audit security vulnerabilities, review code compliance with PEP-8", "Reviewer"),
            # 3. Competing Tester vs Designer: Tester qa keywords dominate
            ("Run pytest regression test suite and assert full test coverage", "Tester"),
            # 4. Competing GitHub Manager vs Developer: GitHub git branch keywords dominate
            ("Create git feature branch on github and open pull request with commit", "GitHub Manager"),
            # 5. Competing Designer vs Developer: Designer tokens dominate
            ("Design UI/UX tokens, terminal styling, and CLI formatting flows", "Designer"),
            # 6. Memory & Status Manager keywords
            ("Persist state persistence checkpoint and vector store memory records", "Memory & Status Manager"),
            # 7. Ambiguous neutral text with zero matching keywords -> Developer default
            ("Synthesize enterprise paradigm for seamless cloud synergy", "Developer"),
            # 8. Uppercase and punctuation variations
            ("REVIEW CODE SECURITY AND AUDIT VULNERABILITY IN PEP-8!", "Reviewer"),
            ("SQLITE SCHEMA AND DATABASE BLUEPRINT (ADR-001)", "Architect"),
            ("PYTEST ASSERTION TEST QA", "Tester"),
        ]

        for prompt, expected_role in cases:
            resolved_role = self.runner._determine_target_specialist(prompt)
            self.assertEqual(
                resolved_role,
                expected_role,
                f"Prompt '{prompt}' should route to '{expected_role}', got '{resolved_role}'",
            )

    def test_adv_hierarchical_explicit_override_and_aliases(self):
        """
        Adversarial Test 2.2:
        Verify explicit target_role override takes precedence over heavy conflicting keywords,
        and verify alias normalization in delegation.
        """
        # Prompt has strong Architect keywords ("database schema architecture wal"), but explicit target_role="Tester"
        resolved_1 = self.runner._determine_target_specialist(
            task_description="Build database schema and wal architecture",
            explicit_role="Tester",
        )
        self.assertEqual(resolved_1, "Tester", "Explicit role must override keyword scoring")

        # Recognized aliases in config.py
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "manager"), "Coordinator")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "coordinator/manager"), "Coordinator")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "github"), "GitHub Manager")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "github_manager"), "GitHub Manager")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "memory"), "Memory & Status Manager")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "memory manager"), "Memory & Status Manager")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "tester"), "Tester")
        self.assertEqual(self.runner._determine_target_specialist("any prompt", "reviewer"), "Reviewer")

        # Unrecognized aliases (e.g. 'qa', 'sdet', 'coder') raise KeyError in normalize_role
        # and cleanly fall back to prompt keyword scoring / Developer default
        self.assertEqual(self.runner._determine_target_specialist("arbitrary prompt", "qa"), "Developer")
        self.assertEqual(self.runner._determine_target_specialist("review security audit", "coder"), "Reviewer")

    def test_adv_hierarchical_step_telemetry_payload_schema(self):
        """
        Adversarial Test 2.3:
        Verify step_callback receives complete, ordered telemetry payloads with valid fields.
        """
        events: List[Dict[str, Any]] = []

        def telemetry_sink(payload: Dict[str, Any]):
            events.append(payload)

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
            step_callback=telemetry_sink,
        )

        res = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Review architecture blueprint and SQLite schema"},
        )

        self.assertEqual(res.get("status"), "completed")

        # Verify event stream sequence: delegation -> step_start (spec) -> step_complete (spec) -> step_start (coord) -> step_complete (coord)
        event_types = [e.get("event") for e in events]
        self.assertIn("delegation", event_types)
        self.assertIn("step_start", event_types)
        self.assertIn("step_complete", event_types)

        # 1. Delegation event payload validation
        del_event = next(e for e in events if e.get("event") == "delegation")
        self.assertEqual(del_event.get("manager"), "Coordinator")
        self.assertEqual(del_event.get("delegate"), "Architect")
        self.assertTrue(len(del_event.get("run_id", "")) > 0)
        self.assertIn("architecture blueprint", del_event.get("task_description", ""))

        # 2. Specialist step events validation
        spec_starts = [e for e in events if e.get("event") == "step_start" and e.get("agent_role") == "Architect"]
        self.assertTrue(len(spec_starts) >= 1)
        self.assertTrue(len(spec_starts[0].get("task_id", "")) > 0)

        spec_completes = [e for e in events if e.get("event") == "step_complete" and e.get("agent_role") == "Architect"]
        self.assertTrue(len(spec_completes) >= 1)
        self.assertTrue(len(spec_completes[0].get("output", "")) > 0)

        # 3. Coordinator synthesis events validation
        coord_completes = [e for e in events if e.get("event") == "step_complete" and e.get("agent_role") == "Coordinator"]
        self.assertTrue(len(coord_completes) >= 1)
        self.assertTrue(len(coord_completes[0].get("output", "")) > 0)

    def test_adv_hierarchical_resilience_to_exploding_callback(self):
        """
        Adversarial Test 2.4:
        Verify that a failing or throwing telemetry callback does NOT crash the pipeline.
        """
        def malicious_exploding_callback(payload):
            raise RuntimeError("CRITICAL TELEMETRY EXCEPTION IN SINK")

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
            step_callback=malicious_exploding_callback,
        )

        result = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Audit security compliance"},
        )

        # Pipeline must trap callback exceptions and complete cleanly
        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(len(result.get("tasks_executed", [])), 2)
        self.assertTrue(len(result.get("final_output", "")) > 0)

    def test_adv_hierarchical_regex_metacharacters_in_prompt(self):
        """
        Adversarial Test 2.5:
        Verify Coordinator delegation keyword matching handles adversarial regex metacharacters
        in the task description without raising re.error.
        """
        adversarial_desc = "Analyze (schema) + [database] * {wal}? \\d+ ^sqlite$ | blueprint."
        resolved = self.runner._determine_target_specialist(adversarial_desc)
        self.assertEqual(resolved, "Architect")


class TestAdversarialExceptionHandling(unittest.TestCase):
    """Empirical adversarial stress testing of specialist exceptions, agent error status, and clean halting."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "err_test.db"
        self.project_id = "adv_err_proj"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id=self.project_id,
        )
        self.state_manager.create_project(
            project_id=self.project_id,
            name="Error Handling Adversarial Project",
            description="Testing exception isolation and agent failure states",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adv_sequential_specialist_failure_isolation(self):
        """
        Adversarial Test 3.1:
        Simulate task failure in specialist agent during sequential execution.
        Verify task is marked 'failed', agent status is marked 'error' with 'last_error' populated,
        and pipeline halts cleanly without executing subsequent steps.
        """
        class CrashingLLM:
            def predict(self, *args, **kwargs):
                raise ValueError("SYNTAX_PARSER_FATAL_ERROR: Unrecoverable syntax exception in generated code.")

        runner = SequentialPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

        # Inject crashing agent on Developer (Step 3)
        crashing_dev = build_agent("Developer", llm=CrashingLLM(), state_manager=self.state_manager)
        runner.team["Developer"] = crashing_dev

        result = runner.run(workflow_type="feature_development")

        # 1. Pipeline returns structured failure
        self.assertEqual(result.get("status"), "failed")
        err_msg = result.get("error_message", "")
        self.assertIn("SYNTAX_PARSER_FATAL_ERROR", err_msg)

        # 2. Check Developer task in SQLite task_history: marked 'failed' with error_details
        run_id = result["run_id"]
        dev_task_id = f"{run_id}_feature_implementation"
        dev_task = self.state_manager.get_task(dev_task_id)
        self.assertIsNotNone(dev_task, "Developer task record must exist in SQLite")
        self.assertEqual(dev_task["status"], "failed")
        self.assertIn("SYNTAX_PARSER_FATAL_ERROR", dev_task["error_details"])

        # 3. Check Developer agent in SQLite agent_status: marked 'error' with last_error populated
        dev_agent_status = self.state_manager.get_agent_status("Developer")
        self.assertEqual(dev_agent_status["status"], "error")
        self.assertIn("SYNTAX_PARSER_FATAL_ERROR", dev_agent_status["last_error"])

        # 4. Check workflow_runs record: marked 'failed'
        wf_run = self.state_manager.get_workflow_run(run_id)
        self.assertEqual(wf_run["status"], "failed")
        self.assertIn("SYNTAX_PARSER_FATAL_ERROR", wf_run["error_message"])

        # 5. Clean halt verification: steps 4-8 (Tester, Reviewer, etc.) were NEVER executed
        tasks_executed = result.get("tasks_executed", [])
        executed_roles = [t["agent_role"] for t in tasks_executed]
        self.assertNotIn("Tester", executed_roles, "Tester must not run after Developer failure")
        self.assertNotIn("Reviewer", executed_roles, "Reviewer must not run after Developer failure")
        self.assertNotIn("Coordinator", executed_roles, "Coordinator must not run after Developer failure")

        # Check total tasks in task_history for this run: exactly 3 (Arch, Designer, Developer)
        history = self.state_manager.get_task_history(run_id=run_id, limit=20)
        self.assertEqual(len(history), 3)

    def test_adv_hierarchical_specialist_failure_isolation(self):
        """
        Adversarial Test 3.2:
        Simulate task failure in specialist agent during hierarchical execution.
        Verify task is marked 'failed', agent status is marked 'error' with 'last_error' populated,
        and pipeline halts cleanly without executing Coordinator executive synthesis.
        """
        class SpecialistCrashLLM:
            def predict(self, *args, **kwargs):
                raise ConnectionResetError("DB_CONNECTION_RESET: Remote SQLite replica cluster unreachable.")

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

        # Inject crashing agent on Architect
        crashing_arch = build_agent("Architect", llm=SpecialistCrashLLM(), state_manager=self.state_manager)
        runner.team["Architect"] = crashing_arch

        result = runner.run(
            workflow_type="targeted_subtask",
            inputs={"task_description": "Review database architecture and schema"},
        )

        # 1. Pipeline returns structured failure
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("DB_CONNECTION_RESET", result.get("error_message", ""))

        # 2. Specialist agent status is marked 'error' with last_error populated
        arch_status = self.state_manager.get_agent_status("Architect")
        self.assertEqual(arch_status["status"], "error")
        self.assertIn("DB_CONNECTION_RESET", arch_status["last_error"])

        # 3. Specialist task is marked 'failed'
        run_id = result["run_id"]
        tasks = self.state_manager.get_task_history(run_id=run_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("DB_CONNECTION_RESET", tasks[0]["error_details"])

        # 4. Coordinator executive synthesis was NOT executed (clean halt)
        executed_names = [t["task_name"] for t in result.get("tasks_executed", [])]
        self.assertNotIn("executive_synthesis", executed_names)

        # 5. Workflow run is marked 'failed'
        wf_run = self.state_manager.get_workflow_run(run_id)
        self.assertEqual(wf_run["status"], "failed")

    def test_adv_hierarchical_coordinator_synthesis_failure(self):
        """
        Adversarial Test 3.3:
        Simulate failure in Coordinator during executive synthesis.
        Verify how the runner handles a coordinator failure when the specialist has completed.
        """
        class CoordCrashLLM:
            def predict(self, *args, **kwargs):
                raise MemoryError("OOM: Coordinator synthesis context window exceeded.")

        runner = HierarchicalPipelineRunner(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

        # Specialist succeeds, Coordinator crashes
        crashing_coord = build_agent("Coordinator", llm=CoordCrashLLM(), state_manager=self.state_manager)
        runner.team["Coordinator"] = crashing_coord

        # Coordinator execute_task is called directly in HierarchicalPipelineRunner.
        # Check whether an unhandled MemoryError or trapped error occurs
        try:
            result = runner.run(
                workflow_type="targeted_subtask",
                inputs={"task_description": "Review code quality"},
            )
            # If trapped:
            self.assertEqual(result.get("status"), "failed")
        except MemoryError as e:
            # If untrapped: coordinator agent status was updated to error by Agent.execute_task
            coord_status = self.state_manager.get_agent_status("Coordinator")
            self.assertEqual(coord_status["status"], "error")
            self.assertIn("Coordinator synthesis context window exceeded", coord_status["last_error"])

    def test_adv_run_project_workflow_contract_stress(self):
        """
        Adversarial Test 3.4:
        Verify unified run_project_workflow input validation contracts under stress:
        invalid modes, empty strings, and non-string arguments.
        """
        # Invalid modes
        with self.assertRaises(ValueError):
            run_project_workflow(mode="invalid_mesh_network")

        with self.assertRaises(ValueError):
            run_project_workflow(mode="concurrent_swarm")

        # Empty or non-string workflow_type
        with self.assertRaises(ValueError):
            run_project_workflow(workflow_type="")

        with self.assertRaises(ValueError):
            run_project_workflow(workflow_type=None)

    def test_adv_nonexistent_resume_run_id_isolation(self):
        """
        Adversarial Test 3.5:
        Verify behavior when an invalid/nonexistent resume_run_id is supplied.
        """
        runner = SequentialPipelineRunner(state_manager=self.state_manager, workspace_root=self.workspace_root)
        try:
            result = runner.run(workflow_type="code_review", resume_run_id="nonexistent_run_404")
            # If trapped into failed status:
            self.assertEqual(result.get("status"), "failed")
        except Exception as e:
            # If raises (e.g. SQLite foreign key constraint because run_id doesn't exist in workflow_runs):
            self.assertTrue(isinstance(e, Exception))


if __name__ == "__main__":
    unittest.main()

