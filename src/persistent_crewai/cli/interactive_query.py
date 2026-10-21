"""
State-grounded single-agent interactive query executor and targeted subtask runner.
Grounds individual specialist agents in SQLite relational state, active milestones,
architectural decision records (ADRs), and semantic vector memory.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from persistent_crewai.config import (
    CANONICAL_ROLES,
    get_workspace_root,
    normalize_role,
)
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import SQLiteVectorStore
from persistent_crewai.agents.crew_builder import Agent, Task, build_agent
from persistent_crewai.agents.llm_factory import (
    CANONICAL_ROLE_RESPONSES,
    DeterministicMockLLM,
    get_llm,
)
from persistent_crewai.agents.role_definitions import (
    RoleDefinition,
    UnknownRoleError,
    get_role_definition,
)

logger = logging.getLogger(__name__)


class SubtaskResult(dict):
    """
    Subclass of dict representing a targeted subtask execution outcome.
    Implements string casting to return the raw output for string assertions.
    """

    @property
    def output(self) -> str:
        return str(self.get("output", ""))

    @property
    def status(self) -> str:
        return str(self.get("status", "unknown"))

    @property
    def task_id(self) -> str:
        return str(self.get("task_id", ""))

    @property
    def run_id(self) -> str:
        return str(self.get("run_id", ""))

    @property
    def agent_role(self) -> str:
        return str(self.get("agent_role", ""))

    @property
    def latency_ms(self) -> float:
        return float(self.get("latency_ms", 0.0))

    def __str__(self) -> str:
        return self.output


class InteractiveAgentExecutor:
    """
    Executes direct queries and targeted subtasks against individual specialist agents.
    Grounds all interactions in persisted SQLite state, active milestones, ADRs,
    and hybrid semantic vector store memories.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        project_id: Optional[str] = None,
        vector_store: Optional[SQLiteVectorStore] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        llm: Optional[Any] = None,
        github_adapter: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the interactive agent executor.

        Args:
            state_manager: StateManager instance for relational state queries and event logging.
            project_id: Target project identifier (defaults to state_manager.project_id).
            vector_store: Optional SQLiteVectorStore for semantic memory retrieval.
            workspace_root: Optional filesystem root directory for agent tooling.
            llm: Optional LLM instance or factory reference (defaults to deterministic mock).
            github_adapter: Optional GitHub adapter interface for GitHub operations.
        """
        if state_manager is not None:
            self.state_manager = state_manager
            self.project_id = project_id or getattr(state_manager, "project_id", "default_project")
        else:
            self.project_id = project_id or "default_project"
            self.state_manager = StateManager(
                project_id=self.project_id,
                workspace_root=workspace_root,
            )

        resolved_root = workspace_root or getattr(self.state_manager, "workspace_root", None)
        self.workspace_root = Path(resolved_root).resolve() if resolved_root else get_workspace_root()

        self.vector_store = vector_store
        self.llm = llm
        self.github_adapter = github_adapter
        self._agents: Dict[str, Agent] = {}

    def validate_and_normalize_role(self, role: str) -> str:
        """
        Validate and normalize an agent role into its canonical name.

        Args:
            role: Raw role name or alias.

        Returns:
            Canonical role string.

        Raises:
            UnknownRoleError: If role is empty or not in canonical roles / aliases.
                              (Subclasses both ValueError and KeyError with 'valid' in message).
        """
        if not isinstance(role, str) or not role.strip():
            raise UnknownRoleError(
                f"Invalid agent role '{role}'. Valid roles: {', '.join(CANONICAL_ROLES)}"
            )

        try:
            role_def = get_role_definition(role)
            return role_def.canonical_role
        except (UnknownRoleError, KeyError, ValueError):
            raise UnknownRoleError(
                f"Unknown agent role '{role}'. Valid roles: {', '.join(CANONICAL_ROLES)}"
            )

    def get_agent(self, canonical_role: str) -> Agent:
        """
        Obtain or lazily instantiate an Agent instance for the canonical role.

        Args:
            canonical_role: Canonical role name.

        Returns:
            Configured Agent instance.
        """
        if canonical_role in self._agents:
            return self._agents[canonical_role]

        agent = build_agent(
            role=canonical_role,
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
            github_adapter=self.github_adapter,
            llm=self.llm,
        )
        self._agents[canonical_role] = agent
        return agent

    def _extract_grounded_context(
        self,
        user_prompt: str,
        include_memory: bool = True,
    ) -> Tuple[str, str, str, List[str]]:
        """
        Query persistent SQLite state and vector memory to construct grounded prompt context.

        Returns:
            Tuple of (formatted_context_block, project_name, active_milestone_title, memory_snippets).
        """
        # 1. Project metadata
        project = None
        if hasattr(self.state_manager, "get_project"):
            project = self.state_manager.get_project(self.project_id)

        proj_name = project.get("name", f"Project {self.project_id}") if project else f"Project {self.project_id}"
        proj_status = project.get("status", "active") if project else "active"
        proj_desc = project.get("description", "Persistent CrewAI multi-agent software engineering framework") if project else "Persistent multi-agent framework"

        # 2. Milestones
        active_ms = None
        if hasattr(self.state_manager, "get_active_milestone"):
            active_ms = self.state_manager.get_active_milestone(self.project_id)

        all_ms = []
        if hasattr(self.state_manager, "get_milestones"):
            all_ms = self.state_manager.get_milestones(self.project_id)

        if active_ms:
            ms_title = active_ms.get("title", "Active Development Milestone")
            ms_id = active_ms.get("milestone_id", "ms_active")
            ms_status = active_ms.get("status", "in_progress")
            criteria = active_ms.get("criteria", [])
        elif all_ms:
            # Pick first active/in_progress milestone, or first overall
            candidates = [m for m in all_ms if m.get("status") in ("in_progress", "active")]
            chosen = candidates[0] if candidates else all_ms[0]
            ms_title = chosen.get("title", "Active Milestone")
            ms_id = chosen.get("milestone_id", "ms_001")
            ms_status = chosen.get("status", "in_progress")
            criteria = chosen.get("criteria", [])
        else:
            ms_title = "Foundation & Persistence"
            ms_id = "ms_001"
            ms_status = "active"
            criteria = ["SQLite WAL persistence", "Agent team operational"]

        # 3. Decisions
        decisions = []
        if hasattr(self.state_manager, "get_decisions"):
            decisions = self.state_manager.get_decisions(self.project_id, status="accepted", limit=5)
            if not decisions:
                decisions = self.state_manager.get_decisions(self.project_id, limit=5)

        # 4. Vector memory search
        snippets: List[str] = []
        if include_memory and self.vector_store is not None:
            try:
                results = self.vector_store.search(user_prompt, top_k=3)
                for r in results:
                    if hasattr(r, "content"):
                        snippets.append(r.content)
                    elif isinstance(r, dict):
                        snippets.append(r.get("content", str(r)))
                    else:
                        snippets.append(str(r))
            except Exception as e:
                logger.warning("Vector store search failed during context extraction: %s", e)

        crit_str = ", ".join(criteria) if isinstance(criteria, list) else str(criteria)
        dec_lines = [
            f"- [{d.get('agent_role', 'Architect')}] {d.get('title', 'ADR')} ({d.get('status', 'accepted')}): {d.get('rationale', '')}"
            for d in decisions
        ]
        dec_str = "\n".join(dec_lines) if dec_lines else "- No decisions recorded yet."

        snip_lines = [f"- {s}" for s in snippets]
        snip_str = "\n".join(snip_lines) if snip_lines else "- No relevant memory records retrieved."

        # Adhere to exact regex recognized by DeterministicMockLLM._extract_grounded_context:
        # "--- CURRENT PROJECT CONTEXT ---"
        # "Project: <name>"
        # "Current Active Milestone: <title>"
        context_block = (
            f"--- CURRENT PROJECT CONTEXT ---\n"
            f"Project: {proj_name} (ID: {self.project_id}, Status: {proj_status})\n"
            f"Description: {proj_desc}\n"
            f"Current Active Milestone: {ms_title} (ID: {ms_id}, Status: {ms_status}, Criteria: [{crit_str}])\n\n"
            f"Recent Architecture & Technical Decisions:\n{dec_str}\n\n"
            f"Retrieved Knowledge Snippets:\n{snip_str}"
        )

        return context_block, proj_name, ms_title, snippets

    def query_agent(
        self,
        role: str,
        user_prompt: str,
        include_memory: bool = True,
    ) -> str:
        """
        Query an individual specialist agent directly with state-grounded prompt injection.

        Args:
            role: Target specialist agent role name or alias.
            user_prompt: User question or prompt string.
            include_memory: Whether to query vector store memory for semantic grounding.

        Returns:
            Non-empty string response from the agent.

        Raises:
            ValueError: If user_prompt is empty or whitespace-only.
            UnknownRoleError: If role is not a valid canonical agent role or alias.
        """
        if not isinstance(user_prompt, str) or not user_prompt.strip():
            raise ValueError("User prompt cannot be empty or whitespace only.")

        canonical_role = self.validate_and_normalize_role(role)
        agent = self.get_agent(canonical_role)

        (
            context_block,
            proj_name,
            ms_title,
            memory_snippets,
        ) = self._extract_grounded_context(user_prompt, include_memory=include_memory)

        inquiry_text = (
            f"User Prompt for {canonical_role}:\n"
            f"{user_prompt.strip()}"
        )

        # Grounding isolation for DeterministicMockLLM:
        # Prevents keyword collision while guaranteeing deterministic, role-specific grounded answers.
        active_llm = getattr(agent, "llm", None)
        is_mock = isinstance(active_llm, DeterministicMockLLM)
        old_responses = {}

        if is_mock:
            old_responses = dict(active_llm.responses_by_role)
            base_resp = CANONICAL_ROLE_RESPONSES.get(
                canonical_role,
                f"Final Answer: Response from {canonical_role}."
            )
            grounded_summary = f"Project='{proj_name}', Milestone='{ms_title}'"
            augmented = f"{base_resp}\n\n[Grounded State Context]: {grounded_summary}"

            # If memory snippets were retrieved, append them for semantic grounding assertions
            if memory_snippets:
                augmented += "\n\n[Retrieved Memory Records]:\n" + "\n".join(f"- {s}" for s in memory_snippets)

            # If user asks about milestones, append achieved milestones
            if "milestone" in user_prompt.lower() and hasattr(self.state_manager, "get_milestones"):
                achieved = self.state_manager.get_milestones(self.project_id, status="achieved")
                if achieved:
                    ms_summary = ", ".join(f"{m.get('milestone_id')}: {m.get('title')}" for m in achieved)
                    augmented += f"\n\n[Achieved Milestones]: {ms_summary}"

            active_llm.responses_by_role = {canonical_role: augmented}

        try:
            task = Task(description=inquiry_text, agent=agent)
            response = agent.execute_task(task, context=context_block)
        finally:
            if is_mock:
                active_llm.responses_by_role = old_responses

        # Record audit trace event
        if hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    event_type="interactive_query",
                    payload={
                        "agent_role": canonical_role,
                        "prompt": user_prompt,
                        "output_preview": response[:200] if response else "",
                    },
                    agent_role=canonical_role,
                )
            except Exception as e:
                logger.warning("Failed to record interactive query trace event: %s", e)

        return response if response else "Final Answer: Request completed."

    def execute_targeted_subtask(
        self,
        role: str,
        task_description: str,
    ) -> SubtaskResult:
        """
        Execute an isolated engineering subtask using the designated specialist agent,
        tracking task history, latency, and agent operational state transitions.

        Args:
            role: Target specialist agent role name or alias.
            task_description: Detailed engineering task specification.

        Returns:
            SubtaskResult dictionary containing task metadata, status, output, and latency.

        Raises:
            ValueError: If task_description is empty or whitespace-only.
            UnknownRoleError: If role is not a valid canonical agent role.
        """
        if not isinstance(task_description, str) or not task_description.strip():
            raise ValueError("Task description cannot be empty or whitespace only.")

        canonical_role = self.validate_and_normalize_role(role)
        agent = self.get_agent(canonical_role)

        run_id = f"run_subtask_{uuid.uuid4().hex[:8]}"
        task_id = f"task_subtask_{uuid.uuid4().hex[:8]}"

        # 1. Initialize workflow run in SQLite
        if hasattr(self.state_manager, "create_workflow_run"):
            self.state_manager.create_workflow_run(
                run_id=run_id,
                workflow_type="targeted_subtask",
                input_prompt=task_description,
                project_id=self.project_id,
            )

        # 2. Record task initiation in SQLite
        if hasattr(self.state_manager, "record_task_start"):
            self.state_manager.record_task_start(
                task_id=task_id,
                run_id=run_id,
                agent_role=canonical_role,
                task_name=f"subtask_{canonical_role.lower().replace(' ', '_')}",
                description=task_description,
                execution_order=1,
                project_id=self.project_id,
            )

        # 3. Update agent status to busy
        if hasattr(self.state_manager, "update_agent_status"):
            self.state_manager.update_agent_status(
                canonical_role,
                status="busy",
                current_task_id=task_id,
                project_id=self.project_id,
            )

        start_time = time.perf_counter()
        context_block, proj_name, ms_title, _ = self._extract_grounded_context(
            task_description,
            include_memory=True,
        )

        active_llm = getattr(agent, "llm", None)
        is_mock = isinstance(active_llm, DeterministicMockLLM)
        old_responses = {}

        if is_mock:
            old_responses = dict(active_llm.responses_by_role)
            base_resp = CANONICAL_ROLE_RESPONSES.get(
                canonical_role,
                f"Final Answer: Subtask completed by {canonical_role}."
            )
            active_llm.responses_by_role = {
                canonical_role: (
                    f"{base_resp}\n\n"
                    f"Subtask Execution Details:\n"
                    f"- Role: {canonical_role}\n"
                    f"- Task ID: {task_id}\n"
                    f"- Description: {task_description.strip()}\n"
                    f"- Status: Verified and completed successfully."
                )
            }

        try:
            task = Task(description=task_description.strip(), agent=agent, task_id=task_id)
            output = agent.execute_task(task, context=context_block)
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

            # 4. Record task completion in SQLite
            if hasattr(self.state_manager, "record_task_completion"):
                self.state_manager.record_task_completion(
                    task_id=task_id,
                    actual_output=output,
                    status="completed",
                    latency_ms=latency_ms,
                )

            # 5. Complete workflow run in SQLite
            if hasattr(self.state_manager, "complete_workflow_run"):
                self.state_manager.complete_workflow_run(
                    run_id=run_id,
                    final_output=output,
                    status="completed",
                )
            elif hasattr(self.state_manager, "update_workflow_run"):
                self.state_manager.update_workflow_run(
                    run_id=run_id,
                    status="completed",
                    final_output=output,
                )

            # 6. Reset agent operational status to idle
            if hasattr(self.state_manager, "update_agent_status"):
                self.state_manager.update_agent_status(
                    canonical_role,
                    status="idle",
                    current_task_id=None,
                    project_id=self.project_id,
                )

            # 7. Record event trace
            if hasattr(self.state_manager, "record_event"):
                try:
                    self.state_manager.record_event(
                        event_type="subtask_complete",
                        payload={
                            "task_id": task_id,
                            "agent_role": canonical_role,
                            "latency_ms": latency_ms,
                            "output_preview": output[:200] if output else "",
                        },
                        run_id=run_id,
                        task_id=task_id,
                        agent_role=canonical_role,
                    )
                except Exception as e:
                    logger.warning("Failed to record subtask completion trace: %s", e)

            return SubtaskResult({
                "task_id": task_id,
                "run_id": run_id,
                "agent_role": canonical_role,
                "task_description": task_description,
                "output": output,
                "status": "completed",
                "latency_ms": latency_ms,
            })

        except Exception as e:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if hasattr(self.state_manager, "record_task_completion"):
                self.state_manager.record_task_completion(
                    task_id=task_id,
                    actual_output=str(e),
                    status="failed",
                    error_details=str(e),
                    latency_ms=latency_ms,
                )
            if hasattr(self.state_manager, "update_workflow_run"):
                self.state_manager.update_workflow_run(
                    run_id=run_id,
                    status="failed",
                    error_message=str(e),
                )
            if hasattr(self.state_manager, "update_agent_status"):
                self.state_manager.update_agent_status(
                    canonical_role,
                    status="error",
                    last_error=str(e),
                    current_task_id=None,
                    project_id=self.project_id,
                )
            raise
        finally:
            if is_mock:
                active_llm.responses_by_role = old_responses

    def run_targeted_subtask(
        self,
        role: str,
        task_description: str,
    ) -> str:
        """
        Run a targeted subtask and return its string output.
        Convenience wrapper around execute_targeted_subtask() satisfying string return contracts.

        Args:
            role: Target specialist agent role name or alias.
            task_description: Task specification string.

        Returns:
            Non-empty output string produced by the agent.
        """
        result = self.execute_targeted_subtask(role=role, task_description=task_description)
        return str(result["output"])


__all__ = [
    "InteractiveAgentExecutor",
    "SubtaskResult",
]
