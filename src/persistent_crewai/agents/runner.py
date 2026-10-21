"""
Runner module for Persistent CrewAI multi-agent software engineering pipelines.
Provides SequentialPipelineRunner (Feature 24), HierarchicalPipelineRunner (Feature 25),
session resumption & crash recovery, and the run_project_workflow() interface contract.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from persistent_crewai.config import (
    CANONICAL_ROLES,
    get_workspace_root,
    normalize_role,
)
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    WorkflowResumePlan,
)
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.agents.crew_builder import build_team, Agent

logger = logging.getLogger(__name__)


# =============================================================================
# Standard Workflow Definitions
# =============================================================================
# (Role, StepName, StepDescription)
WORKFLOW_STEPS: Dict[str, List[Tuple[str, str, str]]] = {
    "feature_development": [
        ("Architect", "architecture_design", "Analyze requirements, author ADR, specify SQLite schema and component boundaries."),
        ("Designer", "ui_ux_specification", "Design UI/UX specifications, CLI command grammar, and terminal interaction flows."),
        ("Developer", "feature_implementation", "Implement core production modules with type annotations and clean error handling."),
        ("Tester", "test_verification", "Author comprehensive test suites and verify system stability."),
        ("Reviewer", "code_review_security", "Audit code for security vulnerabilities, PEP 8 compliance, and issue verdict."),
        ("GitHub Manager", "git_branch_pr", "Create git feature branch, record commit, and generate pull request."),
        ("Memory & Status Manager", "persist_state_milestone", "Record architectural decisions, update milestone criteria, and commit state to SQLite."),
        ("Coordinator", "executive_summary", "Synthesize overall project status, verify backlog, and prepare release report."),
    ],
    "bugfix": [
        ("GitHub Manager", "inspect_issue", "Inspect issue report and retrieve reproduction steps."),
        ("Developer", "apply_fix_patch", "Apply targeted bugfix patch to affected workspace files."),
        ("Tester", "verify_fix_tests", "Run regression tests to verify bug resolution."),
        ("Reviewer", "review_fix_patch", "Review patch for security and correctness."),
        ("GitHub Manager", "commit_pr_fix", "Commit fix to branch and open pull request."),
        ("Memory & Status Manager", "persist_fix_state", "Record bugfix decision in memory."),
        ("Coordinator", "bugfix_summary", "Summarize bugfix completion and close issue."),
    ],
    "code_review": [
        ("Architect", "provide_architecture_context", "Provide architectural context and design constraints."),
        ("Reviewer", "static_analysis_and_audit", "Execute static analysis, security audit, and PEP-8 check."),
        ("Tester", "test_coverage_audit", "Audit test coverage and identify untested paths."),
        ("Coordinator", "review_synthesis", "Synthesize review findings into actionable recommendations."),
    ],
    "targeted_subtask": [
        ("Developer", "targeted_task", "Execute specific assigned engineering subtask."),
    ],
}


# =============================================================================
# SequentialPipelineRunner (Feature 24)
# =============================================================================
class SequentialPipelineRunner:
    """
    Sequential Pipeline Runner (Feature 24).
    Chains tasks in linear sequence, propagates context artifacts downstream,
    persists all steps and latencies via StateManager, and supports resumption.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        github_adapter: Optional[Any] = None,
        llm: Optional[Any] = None,
        team: Optional[Dict[str, Agent]] = None,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ):
        self.state_manager = state_manager or StateManager(workspace_root=workspace_root)
        self.workspace_root = workspace_root or self.state_manager.get_project_root()
        self.github_adapter = github_adapter
        self.llm = llm
        self.step_callback = step_callback

        if self.state_manager and hasattr(self.state_manager, "get_project"):
            try:
                if not self.state_manager.get_project(self.state_manager.project_id):
                    if hasattr(self.state_manager, "create_project"):
                        self.state_manager.create_project(
                            project_id=self.state_manager.project_id,
                            name=f"Project {self.state_manager.project_id}",
                            description="Auto-initialized project for CrewAI team",
                            root_path=self.workspace_root,
                        )
            except Exception as e:
                logger.warning("Project auto-creation warning: %s", e)

        if team:
            self.team = team
        else:
            self.team = build_team(
                llm=self.llm,
                state_manager=self.state_manager,
                workspace_root=self.workspace_root,
                github_adapter=self.github_adapter,
                step_callback=self.step_callback,
                **kwargs,
            )

    def run(
        self,
        workflow_type: str = "feature_development",
        inputs: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        resume_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a sequential multi-step engineering pipeline.
        
        Args:
            workflow_type: Standard workflow identifier ('feature_development', 'bugfix', 'code_review').
            inputs: Optional parameters passed into the pipeline.
            run_id: Optional unique identifier for the workflow run.
            resume_run_id: Optional existing run ID to resume from crash/interruption.
            
        Returns:
            Structured dictionary containing run metadata, tasks executed, outputs, and latencies.
        """
        inputs = inputs or {}
        active_run_id = resume_run_id or run_id or f"run_seq_{uuid.uuid4().hex[:8]}"
        project_id = self.state_manager.project_id

        # Determine steps for workflow
        raw_steps = WORKFLOW_STEPS.get(workflow_type)
        if not raw_steps:
            # Custom or fallback single task
            raw_steps = [("Developer", "custom_task", f"Execute custom {workflow_type} task")]

        context_artifacts: Dict[str, str] = {}
        completed_step_names = set()

        # Handle Resumption
        if resume_run_id:
            db_path = None
            if hasattr(self.state_manager, "_db_mgr") and self.state_manager._db_mgr and hasattr(self.state_manager._db_mgr, "db_path"):
                db_path = str(self.state_manager._db_mgr.db_path)
            elif hasattr(self.state_manager, "db_path"):
                db_path = str(self.state_manager.db_path)

            resumption_mgr = SessionResumptionManager(db_path=db_path, project_id=project_id)
            resumption_mgr.inspect_state(auto_recover=True)
            try:
                plan = resumption_mgr.resume_workflow(resume_run_id)
                for t in plan.completed_tasks:
                    completed_step_names.add(t.get("task_name"))
                    if t.get("agent_role") and t.get("actual_output"):
                        context_artifacts[t["agent_role"]] = t["actual_output"]
            except Exception as e:
                logger.warning("Error inspecting resumption plan: %s", e)
        else:
            input_prompt = json.dumps(inputs, default=str) if inputs else f"Execute {workflow_type}"
            self.state_manager.create_workflow_run(
                run_id=active_run_id,
                workflow_type=workflow_type,
                input_prompt=input_prompt,
            )

        tasks_executed: List[Dict[str, Any]] = []
        final_output = ""

        # Execute remaining steps in linear chain
        for step_idx, (role, step_name, description) in enumerate(raw_steps):
            if step_name in completed_step_names:
                # Task was already successfully completed in prior run
                continue

            task_id = f"{active_run_id}_{step_name}"

            # Format accumulated context from upstream deliverables
            upstream_parts = []
            if inputs:
                upstream_parts.append(f"Workflow Mandate: {json.dumps(inputs, default=str)}")
            for upstream_role, upstream_out in context_artifacts.items():
                upstream_parts.append(f"### {upstream_role} Specification:\n{upstream_out}")

            upstream_context_str = "\n\n".join(upstream_parts)

            # Record task start in SQLite
            self.state_manager.record_task_start(
                task_id=task_id,
                run_id=active_run_id,
                agent_role=role,
                task_name=step_name,
                description=description,
                execution_order=step_idx,
            )
            self.state_manager.start_task(task_id)
            self.state_manager.record_trace(
                run_id=active_run_id,
                task_id=task_id,
                agent_role=role,
                event_type="task_start",
                payload={"description": description, "step_name": step_name},
            )

            if self.step_callback:
                try:
                    self.step_callback({
                        "event": "step_start",
                        "run_id": active_run_id,
                        "task_id": task_id,
                        "agent_role": role,
                        "step_name": step_name,
                    })
                except Exception as e:
                    logger.warning("step_callback start error: %s", e)

            # Execute step with high-resolution latency tracking
            t_start = time.perf_counter()
            agent = self.team.get(role)
            if not agent:
                agent = build_agent(role, state_manager=self.state_manager, llm=self.llm)
                self.team[role] = agent

            try:
                task_prompt = f"{description}"
                step_output = agent.execute_task(task_prompt, context=upstream_context_str)
                latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

                self.state_manager.record_task_completion(
                    task_id=task_id,
                    agent=role,
                    output=step_output,
                    metadata={"latency_ms": latency_ms, "step_name": step_name},
                )
                self.state_manager.record_trace(
                    run_id=active_run_id,
                    task_id=task_id,
                    agent_role=role,
                    event_type="task_complete",
                    payload={"output_preview": step_output[:200], "latency_ms": latency_ms},
                )

                if self.step_callback:
                    try:
                        self.step_callback({
                            "event": "step_complete",
                            "run_id": active_run_id,
                            "task_id": task_id,
                            "agent_role": role,
                            "output": step_output,
                        })
                    except Exception as e:
                        logger.warning("step_callback complete error: %s", e)

                context_artifacts[role] = step_output
                final_output = step_output

                tasks_executed.append({
                    "task_id": task_id,
                    "agent_role": role,
                    "task_name": step_name,
                    "status": "completed",
                    "latency_ms": latency_ms,
                    "output": step_output,
                })

            except Exception as exc:
                latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                err_msg = str(exc)
                logger.error("Step '%s' failed for agent '%s': %s", step_name, role, err_msg)

                self.state_manager.record_task_failure(task_id, error_details=err_msg)
                self.state_manager.update_agent_status(role, status="error", last_error=err_msg)
                self.state_manager.record_trace(
                    run_id=active_run_id,
                    task_id=task_id,
                    agent_role=role,
                    event_type="task_error",
                    payload={"error": err_msg, "latency_ms": latency_ms},
                )
                self.state_manager.complete_workflow_run(
                    run_id=active_run_id,
                    status="failed",
                    error_message=err_msg,
                )

                return {
                    "run_id": active_run_id,
                    "project_id": project_id,
                    "workflow_type": workflow_type,
                    "mode": "sequential",
                    "status": "failed",
                    "final_output": final_output,
                    "tasks_executed": tasks_executed,
                    "latency_total_ms": round(sum(t.get("latency_ms", 0.0) for t in tasks_executed) + latency_ms, 2),
                    "error_message": err_msg,
                }

        # Complete workflow run in persistence
        self.state_manager.complete_workflow_run(
            run_id=active_run_id,
            final_output=final_output,
            status="completed",
        )
        self.state_manager.record_event(
            "workflow_completed",
            payload={"run_id": active_run_id, "tasks_executed_count": len(tasks_executed)},
            run_id=active_run_id,
        )

        total_latency = round(sum(t.get("latency_ms", 0.0) for t in tasks_executed), 2)
        return {
            "run_id": active_run_id,
            "project_id": project_id,
            "workflow_type": workflow_type,
            "mode": "sequential",
            "status": "completed",
            "final_output": final_output,
            "tasks_executed": tasks_executed,
            "latency_total_ms": total_latency,
            "error_message": None,
        }


# =============================================================================
# HierarchicalPipelineRunner (Feature 25)
# =============================================================================
class HierarchicalPipelineRunner:
    """
    Hierarchical Pipeline Runner (Feature 25).
    Manager-led orchestration with Coordinator as manager_role, dynamic
    specialist subtask delegation, telemetry streaming, and error trapping.
    """

    manager_role: str = "Coordinator"

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        github_adapter: Optional[Any] = None,
        llm: Optional[Any] = None,
        team: Optional[Dict[str, Agent]] = None,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ):
        self.state_manager = state_manager or StateManager(workspace_root=workspace_root)
        self.workspace_root = workspace_root or self.state_manager.get_project_root()
        self.github_adapter = github_adapter
        self.llm = llm
        self.step_callback = step_callback

        if self.state_manager and hasattr(self.state_manager, "get_project"):
            try:
                if not self.state_manager.get_project(self.state_manager.project_id):
                    if hasattr(self.state_manager, "create_project"):
                        self.state_manager.create_project(
                            project_id=self.state_manager.project_id,
                            name=f"Project {self.state_manager.project_id}",
                            description="Auto-initialized project for CrewAI team",
                            root_path=self.workspace_root,
                        )
            except Exception as e:
                logger.warning("Project auto-creation warning: %s", e)

        if team:
            self.team = team
        else:
            self.team = build_team(
                llm=self.llm,
                state_manager=self.state_manager,
                workspace_root=self.workspace_root,
                github_adapter=self.github_adapter,
                step_callback=self.step_callback,
                **kwargs,
            )

    def _determine_target_specialist(self, task_description: str, explicit_role: Optional[str] = None) -> str:
        """Dynamically resolve specialist agent based on task mandate keywords."""
        if explicit_role:
            try:
                return normalize_role(explicit_role)
            except KeyError:
                pass

        desc_lower = task_description.lower()
        role_keywords = [
            ("Architect", ["architect", "architecture", "blueprint", "schema", "database", "sqlite", "wal", "data model", "db design"]),
            ("Designer", ["designer", "design tokens", "design", "ui/ux", "ui", "ux", "cli", "token", "tui", "formatting", "styling", "wireframe"]),
            ("Developer", ["developer", "implement", "coding", "code", "develop", "module", "python", "feature", "bugfix", "refactor"]),
            ("Tester", ["tester", "test coverage", "pytest", "test", "qa", "verify", "sdet", "regression", "assertion"]),
            ("Reviewer", ["reviewer", "code review", "security", "audit", "pep-8", "compliance", "vulnerability"]),
            ("GitHub Manager", ["github", "git", "branch", "pr", "pull request", "issue", "commit", "repository"]),
            ("Memory & Status Manager", ["memory", "status manager", "persist", "checkpoint", "state persistence", "vector store"]),
        ]

        scores = {}
        for role, kws in role_keywords:
            score = 0
            for kw in kws:
                if re.search(r"(?:\b|^)" + re.escape(kw) + r"(?:\b|$)", desc_lower):
                    score += 1
            scores[role] = score

        best_role, best_score = max(scores.items(), key=lambda item: item[1])
        if best_score > 0:
            return best_role

        return "Developer"

    def run(
        self,
        workflow_type: str = "targeted_subtask",
        inputs: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute manager-led hierarchical task delegation.
        Coordinator analyzes task mandate, delegates to specialist,
        and synthesizes the executive response.
        """
        inputs = inputs or {}
        active_run_id = run_id or f"run_hier_{uuid.uuid4().hex[:8]}"
        project_id = self.state_manager.project_id

        task_desc = inputs.get("task_description") or inputs.get("prompt") or f"Execute {workflow_type}"
        target_role = self._determine_target_specialist(task_desc, inputs.get("target_role"))

        # Initialize workflow run in StateManager
        self.state_manager.create_workflow_run(
            run_id=active_run_id,
            workflow_type=workflow_type,
            input_prompt=f"Coordinator Delegating: {task_desc}",
        )

        tasks_executed: List[Dict[str, Any]] = []

        # 1. Coordinator Delegation Event
        self.state_manager.record_trace(
            run_id=active_run_id,
            task_id=None,
            agent_role="Coordinator",
            event_type="delegation",
            payload={"delegated_to": target_role, "task_description": task_desc},
        )
        if self.step_callback:
            try:
                self.step_callback({
                    "event": "delegation",
                    "run_id": active_run_id,
                    "manager": "Coordinator",
                    "delegate": target_role,
                    "task_description": task_desc,
                })
            except Exception as e:
                logger.warning("step_callback delegation error: %s", e)

        # 2. Specialist Execution
        spec_task_id = f"{active_run_id}_{target_role.lower().replace(' ', '_')}"
        self.state_manager.record_task_start(
            task_id=spec_task_id,
            run_id=active_run_id,
            agent_role=target_role,
            task_name="delegated_subtask",
            description=task_desc,
            execution_order=0,
        )
        self.state_manager.start_task(spec_task_id)
        self.state_manager.record_trace(
            run_id=active_run_id,
            task_id=spec_task_id,
            agent_role=target_role,
            event_type="task_start",
            payload={"description": task_desc},
        )

        specialist_agent = self.team.get(target_role)
        if not specialist_agent:
            specialist_agent = build_agent(target_role, state_manager=self.state_manager, llm=self.llm)
            self.team[target_role] = specialist_agent

        t_spec = time.perf_counter()
        try:
            specialist_output = specialist_agent.execute_task(
                task_desc,
                context=f"Mandate from Coordinator: {task_desc}",
            )
            spec_latency = round((time.perf_counter() - t_spec) * 1000.0, 2)

            self.state_manager.record_task_completion(
                task_id=spec_task_id,
                agent=target_role,
                output=specialist_output,
                metadata={"latency_ms": spec_latency},
            )
            self.state_manager.record_trace(
                run_id=active_run_id,
                task_id=spec_task_id,
                agent_role=target_role,
                event_type="task_complete",
                payload={"output_preview": specialist_output[:200], "latency_ms": spec_latency},
            )

            tasks_executed.append({
                "task_id": spec_task_id,
                "agent_role": target_role,
                "task_name": "delegated_subtask",
                "status": "completed",
                "latency_ms": spec_latency,
                "output": specialist_output,
            })

        except Exception as exc:
            spec_latency = round((time.perf_counter() - t_spec) * 1000.0, 2)
            err_msg = str(exc)
            logger.error("Delegated task failed for specialist '%s': %s", target_role, err_msg)

            self.state_manager.record_task_failure(spec_task_id, error_details=err_msg)
            self.state_manager.update_agent_status(target_role, status="error", last_error=err_msg)
            self.state_manager.record_trace(
                run_id=active_run_id,
                task_id=spec_task_id,
                agent_role=target_role,
                event_type="task_error",
                payload={"error": err_msg, "latency_ms": spec_latency},
            )
            self.state_manager.complete_workflow_run(
                run_id=active_run_id,
                status="failed",
                error_message=err_msg,
            )

            return {
                "run_id": active_run_id,
                "project_id": project_id,
                "workflow_type": workflow_type,
                "mode": "hierarchical",
                "status": "failed",
                "final_output": "",
                "tasks_executed": tasks_executed,
                "latency_total_ms": spec_latency,
                "error_message": err_msg,
            }

        # 3. Coordinator Synthesis
        coord_agent = self.team.get("Coordinator")
        if not coord_agent:
            coord_agent = build_agent("Coordinator", state_manager=self.state_manager, llm=self.llm)
            self.team["Coordinator"] = coord_agent

        summary_task_id = f"{active_run_id}_coord_summary"
        self.state_manager.record_task_start(
            task_id=summary_task_id,
            run_id=active_run_id,
            agent_role="Coordinator",
            task_name="executive_synthesis",
            description="Synthesize specialist output",
            execution_order=1,
        )
        self.state_manager.start_task(summary_task_id)

        t_coord = time.perf_counter()
        coord_output = coord_agent.execute_task(
            f"Synthesize deliverables for task: {task_desc}",
            context=f"Specialist ({target_role}) deliverable:\n{specialist_output}",
        )
        coord_latency = round((time.perf_counter() - t_coord) * 1000.0, 2)

        self.state_manager.record_task_completion(
            task_id=summary_task_id,
            agent="Coordinator",
            output=coord_output,
            metadata={"latency_ms": coord_latency},
        )
        self.state_manager.record_trace(
            run_id=active_run_id,
            task_id=summary_task_id,
            agent_role="Coordinator",
            event_type="task_complete",
            payload={"output_preview": coord_output[:200], "latency_ms": coord_latency},
        )

        tasks_executed.append({
            "task_id": summary_task_id,
            "agent_role": "Coordinator",
            "task_name": "executive_synthesis",
            "status": "completed",
            "latency_ms": coord_latency,
            "output": coord_output,
        })

        self.state_manager.complete_workflow_run(
            run_id=active_run_id,
            final_output=coord_output,
            status="completed",
        )

        total_latency = round(sum(t.get("latency_ms", 0.0) for t in tasks_executed), 2)
        return {
            "run_id": active_run_id,
            "project_id": project_id,
            "workflow_type": workflow_type,
            "mode": "hierarchical",
            "status": "completed",
            "final_output": coord_output,
            "tasks_executed": tasks_executed,
            "latency_total_ms": total_latency,
            "error_message": None,
        }


# =============================================================================
# Contract Function: run_project_workflow
# =============================================================================
def run_project_workflow(
    workflow_type: str = "feature_development",
    mode: str = "sequential",
    project_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    state_manager: Optional[StateManager] = None,
    resumption_manager: Optional[SessionResumptionManager] = None,
    resume_run_id: Optional[str] = None,
    step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Unified entrypoint contract for running or resuming multi-agent workflows.
    Conforms to PROJECT.md interface contracts.
    
    Args:
        workflow_type: Workflow type ('feature_development', 'bugfix', 'code_review', 'targeted_subtask').
        mode: Execution orchestration mode ('sequential' or 'hierarchical').
        project_id: Target project identifier.
        inputs: Input parameters for the workflow.
        state_manager: Optional StateManager instance.
        resumption_manager: Optional SessionResumptionManager.
        resume_run_id: Interrupted workflow run ID to resume.
        step_callback: Execution telemetry callback.
        **kwargs: Additional parameters passed to runners.
        
    Returns:
        Structured dictionary with run metadata, status, tasks, and final output.
        
    Raises:
        ValueError: If mode is not 'sequential' or 'hierarchical', or workflow_type is invalid.
    """
    if not workflow_type or not isinstance(workflow_type, str):
        raise ValueError("workflow_type must be a non-empty string.")

    mode_clean = (mode or "sequential").strip().lower()
    if mode_clean not in ("sequential", "hierarchical"):
        raise ValueError(f"Invalid execution mode '{mode}'. Must be 'sequential' or 'hierarchical'.")

    sm = state_manager or StateManager(project_id=project_id)

    if mode_clean == "sequential":
        runner = SequentialPipelineRunner(
            state_manager=sm,
            step_callback=step_callback,
            **kwargs,
        )
        return runner.run(
            workflow_type=workflow_type,
            inputs=inputs,
            resume_run_id=resume_run_id,
        )
    else:
        hier_runner = HierarchicalPipelineRunner(
            state_manager=sm,
            step_callback=step_callback,
            **kwargs,
        )
        return hier_runner.run(
            workflow_type=workflow_type,
            inputs=inputs,
        )


__all__ = [
    "SequentialPipelineRunner",
    "HierarchicalPipelineRunner",
    "run_project_workflow",
    "WORKFLOW_STEPS",
]
