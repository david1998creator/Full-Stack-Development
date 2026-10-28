"""
Crew Builder and Agent Instantiation Engine for Persistent CrewAI.
Supports dual-mode execution (native CrewAI if installed, with a resilient,
deterministic offline fallback engine), tool bindings, and state persistence integration.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from persistent_crewai.config import CANONICAL_ROLES, normalize_role
from persistent_crewai.agents.llm_factory import DeterministicMockLLM, get_llm
from persistent_crewai.agents.role_definitions import (
    RoleDefinition,
    get_role_definition,
)
from persistent_crewai.tools import get_agent_tools

logger = logging.getLogger(__name__)


# =============================================================================
# Process Enum / Constants
# =============================================================================
class Process:
    """Execution process types matching CrewAI Process enum."""
    sequential = "sequential"
    hierarchical = "hierarchical"


# =============================================================================
# CrewOutput Model
# =============================================================================
class CrewOutput:
    """Output container for crew execution results."""

    def __init__(self, raw: str = "", tasks_output: Optional[List[Any]] = None):
        self.raw = raw
        self.tasks_output = tasks_output or []

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"<CrewOutput raw={self.raw[:60]!r}... tasks={len(self.tasks_output)}>"


# =============================================================================
# Task Model (Fallback & Interop)
# =============================================================================
class Task:
    """
    Task representation defining an engineering activity assigned to an agent,
    supporting upstream context injection and execution callbacks.
    """

    def __init__(
        self,
        description: str,
        expected_output: str = "",
        agent: Optional["Agent"] = None,
        context: Optional[List[Any]] = None,
        callback: Optional[Callable[[Any], None]] = None,
        task_id: Optional[str] = None,
        output: Optional[str] = None,
        **kwargs: Any,
    ):
        self.description = description
        self.expected_output = expected_output
        self.agent = agent
        self.context = context or []
        self.callback = callback
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        self.output = output

    def execute(self, context: str = "") -> str:
        """Execute this task using its assigned agent."""
        if not self.agent:
            raise ValueError(f"Task '{self.task_id}' has no assigned agent.")
        out = self.agent.execute_task(self, context=context)
        self.output = out
        if self.callback:
            try:
                self.callback(self)
            except Exception as e:
                logger.warning("Task callback error: %s", e)
        return out

    def __str__(self) -> str:
        return f"Task({self.task_id}: {self.description[:40]})"

    def __repr__(self) -> str:
        return f"<Task id={self.task_id} agent={getattr(self.agent, 'canonical_role', None)}>"


# =============================================================================
# Agent Model (Fallback & Interop)
# =============================================================================
class Agent:
    """
    Autonomous engineering agent configured with distinct role, goal, backstory,
    system prompt, tool suite, and operational state synchronization.
    """

    def __init__(
        self,
        role: str,
        goal: str,
        backstory: str,
        system_template: str = "",
        tools: Optional[List[Any]] = None,
        llm: Optional[Any] = None,
        allow_delegation: bool = False,
        verbose: bool = False,
        state_manager: Optional[Any] = None,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        display_name: str = "",
        **kwargs: Any,
    ):
        self.canonical_role = normalize_role(role)
        self.role = self.canonical_role
        self.display_name = display_name or self.canonical_role
        self.goal = goal
        self.backstory = backstory
        self.system_template = system_template
        self.system_prompt = system_template
        self.tools = tools or []
        self.llm = llm or get_llm()
        self.allow_delegation = allow_delegation
        self.delegation_allowed = allow_delegation
        self.verbose = verbose
        self.state_manager = state_manager
        self.step_callback = step_callback

    def execute_task(self, task: Union[Task, str], context: Optional[str] = None) -> str:
        """
        Execute an assigned task, updating StateManager operational status
        from idle -> busy -> idle (or error) and invoking the LLM/tools.
        """
        task_id = getattr(task, "task_id", None) or f"task_{uuid.uuid4().hex[:8]}"
        task_desc = task.description if isinstance(task, Task) else str(task)

        # 1. Update status to busy
        if self.state_manager and hasattr(self.state_manager, "update_agent_status"):
            try:
                if hasattr(self.state_manager, "get_project") and not self.state_manager.get_project(self.state_manager.project_id):
                    if hasattr(self.state_manager, "create_project"):
                        self.state_manager.create_project(
                            project_id=self.state_manager.project_id,
                            name=f"Project {self.state_manager.project_id}",
                            description="Auto-initialized project for CrewAI team",
                            root_path=getattr(self.state_manager, "workspace_root", None),
                        )
                eff_task_id = None
                if task_id and hasattr(self.state_manager, "get_task") and self.state_manager.get_task(task_id):
                    eff_task_id = task_id
                self.state_manager.update_agent_status(
                    self.canonical_role,
                    status="busy",
                    current_task_id=eff_task_id,
                )
            except Exception as e:
                logger.warning("Failed to update agent status to busy: %s", e)

        # 2. Emit step_callback start event if configured
        if self.step_callback:
            try:
                self.step_callback({
                    "event": "step_start",
                    "agent_role": self.canonical_role,
                    "task_id": task_id,
                    "description": task_desc,
                })
            except Exception as e:
                logger.warning("Error in agent step_callback: %s", e)

        # 3. Construct prompt incorporating role context and upstream artifacts
        prompt_sections = [
            f"You are the {self.display_name} ({self.canonical_role}).",
            f"Goal: {self.goal}",
            f"Backstory: {self.backstory}",
        ]
        if self.system_template:
            prompt_sections.append(f"Operating Guidelines:\n{self.system_template}")
        if context:
            prompt_sections.append(f"Upstream Context:\n{context}")
        prompt_sections.append(f"Task Assignment:\n{task_desc}")

        full_prompt = "\n\n".join(prompt_sections)

        try:
            # 4. Invoke LLM
            if hasattr(self.llm, "predict"):
                raw_response = self.llm.predict(full_prompt)
            elif hasattr(self.llm, "invoke"):
                res = self.llm.invoke(full_prompt)
                raw_response = getattr(res, "content", str(res))
            elif callable(self.llm):
                res = self.llm(full_prompt)
                raw_response = getattr(res, "content", str(res))
            else:
                raw_response = str(self.llm)

            # 5. Emit step_callback complete event
            if self.step_callback:
                try:
                    self.step_callback({
                        "event": "step_complete",
                        "agent_role": self.canonical_role,
                        "task_id": task_id,
                        "output": raw_response,
                    })
                except Exception as e:
                    logger.warning("Error in agent step_callback complete: %s", e)

            # 6. Update status back to idle
            if self.state_manager and hasattr(self.state_manager, "update_agent_status"):
                try:
                    self.state_manager.update_agent_status(
                        self.canonical_role,
                        status="idle",
                        current_task_id=None,
                    )
                except Exception as e:
                    logger.warning("Failed to update agent status to idle: %s", e)

            if isinstance(task, Task):
                task.output = raw_response

            return raw_response

        except Exception as exc:
            # 7. Record error in StateManager
            if self.state_manager and hasattr(self.state_manager, "update_agent_status"):
                try:
                    self.state_manager.update_agent_status(
                        self.canonical_role,
                        status="error",
                        last_error=str(exc),
                    )
                except Exception:
                    pass
            raise exc

    def run(self, prompt: str, context: Optional[str] = None) -> str:
        """Direct execution method for interactive Q&A."""
        full_prompt = prompt
        if context:
            full_prompt = f"Context:\n{context}\n\nQuery:\n{prompt}"
        if hasattr(self.llm, "predict"):
            return self.llm.predict(full_prompt)
        elif hasattr(self.llm, "invoke"):
            res = self.llm.invoke(full_prompt)
            return getattr(res, "content", str(res))
        elif callable(self.llm):
            res = self.llm(full_prompt)
            return getattr(res, "content", str(res))
        return str(self.llm)

    def query_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Query persistent memory records or decisions from StateManager."""
        if not self.state_manager:
            return []
        if hasattr(self.state_manager, "get_vector_store"):
            try:
                vs = self.state_manager.get_vector_store()
                if vs and hasattr(vs, "search"):
                    results = vs.search(query, top_k=limit)
                    return [{"text": r.text, "score": r.score, "metadata": r.metadata} for r in results]
            except Exception:
                pass
        if hasattr(self.state_manager, "get_decisions"):
            try:
                return self.state_manager.get_decisions()[:limit]
            except Exception:
                pass
        return []

    def __str__(self) -> str:
        return f"Agent(role='{self.canonical_role}', allow_delegation={self.allow_delegation})"

    def __repr__(self) -> str:
        return f"<Agent role='{self.canonical_role}' tools={len(self.tools)}>"


# =============================================================================
# Crew Model (Fallback & Interop)
# =============================================================================
class Crew:
    """
    Execution container managing a team of agents, a collection of tasks,
    and the execution flow (sequential or hierarchical).
    """

    def __init__(
        self,
        agents: List[Agent],
        tasks: Optional[List[Task]] = None,
        process: Any = Process.sequential,
        manager_agent: Optional[Agent] = None,
        manager_llm: Optional[Any] = None,
        verbose: bool = False,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        task_callback: Optional[Callable[[Any], None]] = None,
        state_manager: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.agents = agents
        self.tasks = tasks or []
        self.process = process
        self.manager_agent = manager_agent
        self.manager_llm = manager_llm
        self.verbose = verbose
        self.step_callback = step_callback
        self.task_callback = task_callback
        self.state_manager = state_manager

    def kickoff(self, inputs: Optional[Dict[str, Any]] = None) -> CrewOutput:
        """
        Execute tasks in the configured process order.
        Propagates outputs from upstream tasks into downstream tasks.
        """
        inputs = inputs or {}
        context_accumulator: List[str] = []
        task_outputs: List[Any] = []
        final_output = ""

        # Process each task
        for i, task in enumerate(self.tasks):
            # Format upstream context from previous tasks or task.context references
            upstream_context = ""
            if task.context:
                ctx_parts = []
                for ctx_item in task.context:
                    if isinstance(ctx_item, Task) and ctx_item.output:
                        ctx_parts.append(f"[{ctx_item.agent.canonical_role}]: {ctx_item.output}")
                    elif isinstance(ctx_item, str):
                        ctx_parts.append(ctx_item)
                upstream_context = "\n\n".join(ctx_parts)
            elif context_accumulator:
                upstream_context = "\n\n".join(context_accumulator)

            # If inputs provided and this is the first task, inject inputs
            if i == 0 and inputs:
                input_str = "\n".join(f"{k}: {v}" for k, v in inputs.items())
                upstream_context = f"Workflow Inputs:\n{input_str}\n\n{upstream_context}".strip()

            out = task.execute(context=upstream_context)
            final_output = out
            task_outputs.append(out)

            agent_role = getattr(task.agent, "canonical_role", f"Agent_{i}")
            context_accumulator.append(f"### {agent_role} Deliverable:\n{out}")

            if self.task_callback:
                try:
                    self.task_callback(task)
                except Exception as e:
                    logger.warning("Crew task_callback error: %s", e)

        return CrewOutput(raw=final_output, tasks_output=task_outputs)

    def __str__(self) -> str:
        return f"Crew(agents={len(self.agents)}, tasks={len(self.tasks)}, process={self.process})"

    def __repr__(self) -> str:
        return f"<Crew agents={len(self.agents)} process={self.process}>"


# =============================================================================
# Factory Functions
# =============================================================================
def build_agent(
    role: str,
    llm: Optional[Any] = None,
    state_manager: Optional[Any] = None,
    workspace_root: Optional[Union[str, Path]] = None,
    github_adapter: Optional[Any] = None,
    tools: Optional[List[Any]] = None,
    verbose: bool = False,
    step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    **kwargs: Any,
) -> Agent:
    """
    Construct and configure a specialized Agent instance.
    
    Args:
        role: Canonical role name or recognized alias.
        llm: Optional LLM instance; defaults to LLMFactory.get_llm().
        state_manager: Optional StateManager instance for operational telemetry.
        workspace_root: Root workspace directory for sandboxed file tools.
        github_adapter: Optional GitHub adapter interface.
        tools: Optional list of pre-instantiated tools; defaults to get_agent_tools(role).
        verbose: Verbose logging flag.
        step_callback: Execution telemetry callback.
        
    Returns:
        Configured Agent instance.
    """
    role_def = get_role_definition(role)
    canonical = role_def.canonical_role

    resolved_llm = llm if llm is not None else get_llm(role=canonical)

    if tools is None:
        try:
            resolved_tools = get_agent_tools(
                role=canonical,
                workspace_root=workspace_root,
                github_adapter=github_adapter,
                state_manager=state_manager,
            )
        except Exception as e:
            logger.warning("Could not instantiate default tools for %s: %s", canonical, e)
            resolved_tools = []
    else:
        resolved_tools = list(tools)

    allow_delegation = (canonical == "Coordinator")

    # Initialize agent status in StateManager if available
    if state_manager and hasattr(state_manager, "update_agent_status"):
        try:
            if hasattr(state_manager, "get_project") and not state_manager.get_project(state_manager.project_id):
                if hasattr(state_manager, "create_project"):
                    state_manager.create_project(
                        project_id=state_manager.project_id,
                        name=f"Project {state_manager.project_id}",
                        description="Auto-initialized project for CrewAI team",
                        root_path=getattr(state_manager, "workspace_root", None),
                    )
            state_manager.update_agent_status(canonical, status="idle")
        except Exception as e:
            logger.warning("Failed to initialize agent status in StateManager: %s", e)

    return Agent(
        role=canonical,
        display_name=role_def.display_name,
        goal=role_def.goal,
        backstory=role_def.backstory,
        system_template=role_def.system_prompt,
        tools=resolved_tools,
        llm=resolved_llm,
        allow_delegation=allow_delegation,
        verbose=verbose,
        state_manager=state_manager,
        step_callback=step_callback,
        **kwargs,
    )


def build_team(
    roles: Optional[List[str]] = None,
    llm: Optional[Any] = None,
    state_manager: Optional[Any] = None,
    workspace_root: Optional[Union[str, Path]] = None,
    github_adapter: Optional[Any] = None,
    verbose: bool = False,
    **kwargs: Any,
) -> Dict[str, Agent]:
    """
    Instantiate the engineering specialist team mapped by canonical role name.
    
    Args:
        roles: Optional subset of roles; defaults to all 8 CANONICAL_ROLES.
        llm: Optional shared LLM instance.
        state_manager: Optional shared StateManager instance.
        workspace_root: Root workspace directory.
        github_adapter: Optional GitHub adapter interface.
        verbose: Verbose logging flag.
        
    Returns:
        Dictionary mapping canonical role names to configured Agent instances.
    """
    target_roles = roles if roles is not None else CANONICAL_ROLES
    team: Dict[str, Agent] = {}

    for r in target_roles:
        agent = build_agent(
            role=r,
            llm=llm,
            state_manager=state_manager,
            workspace_root=workspace_root,
            github_adapter=github_adapter,
            verbose=verbose,
            **kwargs,
        )
        team[agent.canonical_role] = agent

    return team


def build_crew(
    agents: Union[List[Agent], Dict[str, Agent]],
    tasks: Optional[List[Task]] = None,
    process: Any = Process.sequential,
    manager_agent: Optional[Agent] = None,
    manager_llm: Optional[Any] = None,
    verbose: bool = False,
    step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    task_callback: Optional[Callable[[Any], None]] = None,
    state_manager: Optional[Any] = None,
    **kwargs: Any,
) -> Crew:
    """
    Construct a Crew instance from agents, tasks, and process flow.
    If process is hierarchical and manager_agent is omitted, automatically assigns
    the Coordinator agent from the agents team.
    
    Args:
        agents: List of Agent instances or dict returned by build_team().
        tasks: Optional list of Task instances.
        process: Process.sequential or Process.hierarchical.
        manager_agent: Optional manager agent for hierarchical flow.
        manager_llm: Optional manager LLM.
        verbose: Verbose logging flag.
        step_callback: Execution telemetry callback.
        task_callback: Task completion callback.
        state_manager: Optional StateManager instance.
        
    Returns:
        Configured Crew instance.
        
    Raises:
        ValueError: If hierarchical process requested but no manager_agent or Coordinator found.
    """
    if isinstance(agents, dict):
        agent_list = list(agents.values())
    else:
        agent_list = list(agents)

    # Normalize process
    if isinstance(process, str):
        p_clean = process.strip().lower()
        if p_clean in ("hierarchical", "hierarchy"):
            resolved_process = Process.hierarchical
        else:
            resolved_process = Process.sequential
    else:
        resolved_process = process

    # Handle hierarchical manager resolution
    resolved_manager = manager_agent
    if resolved_process == Process.hierarchical and resolved_manager is None:
        for ag in agent_list:
            if getattr(ag, "canonical_role", getattr(ag, "role", "")) == "Coordinator":
                resolved_manager = ag
                break
        if resolved_manager is None:
            raise ValueError("Hierarchical crew requires a manager_agent or a Coordinator in agents list")

    return Crew(
        agents=agent_list,
        tasks=tasks or [],
        process=resolved_process,
        manager_agent=resolved_manager,
        manager_llm=manager_llm,
        verbose=verbose,
        step_callback=step_callback,
        task_callback=task_callback,
        state_manager=state_manager,
        **kwargs,
    )


__all__ = [
    "Process",
    "CrewOutput",
    "Task",
    "Agent",
    "Crew",
    "build_agent",
    "build_team",
    "build_crew",
]
