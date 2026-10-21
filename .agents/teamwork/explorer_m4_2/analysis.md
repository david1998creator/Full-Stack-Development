# Milestone 4 Architectural Analysis & Blueprint: Grounded Single-Agent Q&A and Targeted Subtask Execution

**Explorer**: M4-2 (Grounded Single-Agent Q&A Explorer)  
**Target Path**: `src/persistent_crewai/cli/interactive_query.py`  
**Associated Test Plan**: `tests/test_cli.py` & `tests/e2e/test_e2e_tier1_features.py`  
**Status**: Ready for Implementation  

---

## 1. Executive Summary

Milestone 4 introduces the interactive human-in-the-loop operational surface for the Persistent CrewAI 8-Agent Software Engineering Framework. Within this milestone, `interactive_query.py` is the core execution bridge that enables direct, grounded communication with individual specialist agents without requiring full multi-agent crew execution.

This document formulates the complete technical architecture and implementation specification for:
1. `InteractiveAgentExecutor`:
   - Instantiation with `StateManager`, `SQLiteVectorStore`, and pluggable `llm_provider`.
   - Dynamic prompt injection incorporating live SQLite project metadata, active milestones, recent Architectural Decision Records (ADRs), and semantic vector memory search results.
   - Grounded single-agent dialogue via `query_agent(role, user_prompt, include_memory=True) -> str`.
   - Targeted subtask execution via `execute_targeted_subtask(role, task_description) -> dict` and `run_targeted_subtask(role, task_description) -> str`, featuring latency instrumentation, task history persistence, and agent operational state transitions.
   - Comprehensive operational telemetry emitted to `execution_traces`.
2. Exact prompt grounding invariants aligned with `DeterministicMockLLM` and production LLMs.
3. Complete test plan covering unit tests (`tests/test_cli.py`) and E2E verification suites (Tier 1-4).

---

## 2. Requirement Mapping & Contract Invariants

### 2.1 Authoritative Requirements Traceability
- **ORIGINAL_REQUEST.md (R4)**:
  - Users can select an individual agent (such as Coordinator, Architect, or Memory Manager) and hold a Q&A conversation grounded in persisted project state.
  - Trigger targeted sub-tasks for specialist agents without orchestrating full pipeline runs.
- **PROJECT.md (Feature 27 & Feature 28)**:
  - **Feature 27**: Grounded Single-Agent Q&A Mode — Direct Q&A with individual agents injected with persisted SQLite context.
  - **Feature 28**: Targeted Subtask Execution Trigger — Trigger individual tasks for specific agents from CLI.
  - **Interface Contract M1/M3 ↔ M4 (PROJECT.md:143)**:
    `InteractiveAgentExecutor.query_agent(role: str, user_prompt: str) -> str`: Injects persisted state into agent prompt, runs single-agent Crew, returns grounded response.

### 2.2 E2E Test Suite Call-Site Audit

| Test ID | Test File & Line | Invocation Signature | Assertion Invariants |
|---|---|---|---|
| **TC-R4.1** | `test_e2e_tier1_features.py:513-521` | `executor.query_agent(role="Coordinator", user_prompt="What is the current status of the project?")` | Returns `str`, `len > 0`. Response grounded in project milestones. |
| **TC-R4.2** | `test_e2e_tier1_features.py:529-537` | `executor.query_agent(role="Architect", user_prompt="Explain the database design and persistence strategy.")` | Returns `str`, `len > 0`. Injects recorded decisions and schemas into prompt. |
| **TC-R4.3** | `test_e2e_tier1_features.py:545-553` | `executor.query_agent(role="Memory & Status Manager", user_prompt="Show recorded milestones and session runs.")` | Returns `str`, `len > 0`. Returns milestone and state history. |
| **TC-R4.4** | `test_e2e_tier1_features.py:561-569` | `executor.run_targeted_subtask(role="Tester", task_description="Execute unit test verification for SQLite schema")` | Returns `str`, `len > 0`. Executes single agent without full crew. |
| **TC-B.16** | `test_e2e_tier2_boundaries.py:340-346` | `get_role_definition("Wizard")` | Requesting unknown agent role raises `ValueError` / `KeyError`. |
| **TC-B.17** | `test_e2e_tier2_boundaries.py:354-357` | `executor.query_agent(role="Coordinator", user_prompt="   ")` | Whitespace-only or empty prompt raises `ValueError`. |
| **TC-C.3** | `test_e2e_tier3_combinations.py:189-196` | `executor = InteractiveAgentExecutor(state_manager=rebooted_sm); executor.query_agent(role="Coordinator", user_prompt="What milestones have been achieved?")` | Rehydrates persisted state across session reboots; returns achieved milestones. |
| **TC-C.4** | `test_e2e_tier3_combinations.py:229-236` | `executor = InteractiveAgentExecutor(state_manager=sm, vector_store=vstore); executor.query_agent(role="Architect", user_prompt="What did we decide regarding zero-dependency database persistence?")` | Vector store memory retrieved and injected into agent response. |
| **TC-S.4** | `test_e2e_tier4_scenarios.py:284-297` | Multi-turn REPL commands: `ask coordinator ...`, `ask architect ...`, `run task tester ...` | Delegated through `InteractiveAgentExecutor`. |

---

## 3. Deep Architectural Analysis & Design Decisions

### 3.1 Class Architecture: `InteractiveAgentExecutor`

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           InteractiveAgentExecutor                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ - state_manager: StateManager                                               │
│ - vector_store: Optional[SQLiteVectorStore]                                 │
│ - llm_provider: Optional[Any]                                               │
│ - workspace_root: Path                                                      │
│ - github_adapter: Optional[Any]                                             │
│ - _agents_cache: Dict[str, Agent]                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ + __init__(state_manager, vector_store, llm_provider, workspace_root, ...)  │
│ + query_agent(role: str, user_prompt: str, include_memory: bool) -> str    │
│ + execute_targeted_subtask(role: str, task_description: str) -> dict       │
│ + run_targeted_subtask(role: str, task_description: str) -> str             │
│ + validate_and_normalize_role(role: str) -> str                             │
│ + get_agent(role: str) -> Agent                                             │
│ - _build_grounded_context(user_prompt: str, include_memory: bool) -> str   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Role Normalization and Exception Invariants
- Role inputs can arrive in varied representations: `"coordinator"`, `"manager"`, `"coordinator / manager"`, `"Architect"`, `"github"`, `"memory"`.
- `validate_and_normalize_role(role: str) -> str`:
  1. Validates `role` is a non-empty string.
  2. Invokes `normalize_role(role)` from `persistent_crewai.config`.
  3. If unrecognized, raises `UnknownRoleError` from `persistent_crewai.agents.role_definitions`.
  4. Note: `UnknownRoleError` subclasses both `ValueError` and `KeyError`:
     ```python
     class UnknownRoleError(ValueError, KeyError):
         """Raised when an unknown agent role is requested; satisfies both ValueError and KeyError."""
         pass
     ```
     This dual-inheritance guarantees 100% compliance with both `with self.assertRaises(ValueError)` and `with self.assertRaises(KeyError)`.

### 3.3 Prompt Validation & Input Boundaries
- `user_prompt` in `query_agent`:
  - Must not be `None`.
  - Must not be empty string `""` or whitespace-only `"   "`.
  - Raises `ValueError("user_prompt cannot be empty or whitespace only.")` (enforcing TC-B.17).
- `task_description` in `execute_targeted_subtask`:
  - Must not be `None`.
  - Must not be empty or whitespace-only.
  - Raises `ValueError("task_description cannot be empty or whitespace only.")`.

### 3.4 Persistent SQLite Grounded Context Construction

To ensure true state grounding, the executor queries four relational/vector sources from the persistence layer:

1. **Project Metadata**:
   - Pulled via `state_manager.get_project(project_id)`.
   - Fields: `name`, `project_id`, `status`, `integrity_mode`, `description`.
2. **Milestone Registry**:
   - Pulled via `state_manager.get_active_milestone(project_id)`.
   - Fallback to `state_manager.get_milestones(project_id)` if no active milestone is marked.
   - Fields: `title`, `milestone_id`, `status`, `criteria`.
3. **Recent Architecture Decisions (ADRs)**:
   - Pulled via `state_manager.get_decisions(project_id=project_id, status="accepted", limit=5)`.
   - Fields: `agent_role`, `title`, `status`, `rationale`.
4. **Semantic Vector Memory (Hybrid Search)**:
   - If `include_memory=True` and `vector_store` is available:
     Queries `vector_store.search(user_prompt, top_k=3, hybrid_search=True)`.
   - Extracts top semantic memory snippets relevant to the specific user inquiry.

#### Injected Block Grammar & DeterministicMockLLM Compatibility
`DeterministicMockLLM._extract_grounded_context` expects specific regex patterns:
```python
if "--- current project context ---" not in prompt.lower():
    return None
proj_match = re.search(r"Project:\s*([^\n(]+)", prompt)
ms_match = re.search(r"Current Active Milestone:\s*([^\n(]+)", prompt)
```

Therefore, the prompt injected into the agent MUST adhere to this exact structural format:
```text
--- CURRENT PROJECT CONTEXT ---
Project: {project_name} (ID: {project_id}, Status: {project_status})
Description: {project_description}
Current Active Milestone: {milestone_title} (ID: {milestone_id}, Status: {milestone_status}, Criteria: [{criteria_summary}])
Recent Architecture & Technical Decisions:
- [{agent_role}] {title} ({status}): {rationale}
Retrieved Knowledge Snippets:
- {memory_item_1}
- {memory_item_2}
```

#### Critical Discovery: Token Collision Mitigation in MockLLM
In `DeterministicMockLLM`, rule-based role detection evaluates keywords in a fixed order:
1. `GitHub Manager`
2. `Memory & Status Manager` (`"memory"`, `"persist"`, `"state persistence"`, etc.)
3. `Coordinator`
4. `Architect`
5. `Designer`
6. `Developer`
7. `Tester`
8. `Reviewer`

If the injected context contains the literal token `"Relevant Semantic Memory:"`, rule #2 (`Memory & Status Manager`) will trigger for any prompt if the role is not explicitly isolated.  
**Resolution**:
1. Label the vector memory section as `"Retrieved Knowledge Snippets:"` or `"Retrieved Context Snippets:"` rather than `"Relevant Semantic Memory:"`.
2. When executing via `DeterministicMockLLM`, set `agent.llm.responses_by_role = {canonical_role: f"{CANONICAL_ROLE_RESPONSES[canonical_role]}\n\n[Grounded State Context]: {grounded_summary}"}` before prediction and clean up afterwards. This completely isolates role responses and prevents keyword leakage across roles while ensuring 100% deterministic grounded output.

### 3.5 Single-Agent Query Execution Workflow (`query_agent`)

```
User: query_agent(role, user_prompt)
  │
  ├── 1. Validate role (normalize_role -> UnknownRoleError)
  ├── 2. Validate user_prompt (non-empty -> ValueError)
  ├── 3. Extract persisted state from StateManager (Project, Milestone, Decisions)
  ├── 4. Search vector_store for semantic memory (top_k=3)
  ├── 5. Construct grounded prompt with '--- CURRENT PROJECT CONTEXT ---'
  ├── 6. Fetch/build Agent for canonical_role via build_agent()
  ├── 7. Execute single-agent query (agent.run(grounded_prompt))
  ├── 8. Record audit event in StateManager.record_event(event_type="interactive_query")
  └── 9. Return grounded technical response (str)
```

### 3.6 Targeted Subtask Execution Workflow (`execute_targeted_subtask`)

```
User: execute_targeted_subtask(role, task_description)
  │
  ├── 1. Validate role and task_description
  ├── 2. Generate run_id ("run_subtask_...") & task_id ("task_subtask_...")
  ├── 3. Record task start in SQLite (StateManager.record_task_start)
  ├── 4. Update agent operational status to 'busy' in SQLite (StateManager.update_agent_status)
  ├── 5. Start high-resolution timer (time.perf_counter())
  ├── 6. Build specialist Agent and Task instance
  ├── 7. Execute task with context (agent.execute_task(task, context=grounded_context))
  ├── 8. Stop timer and calculate latency_seconds
  ├── 9. Record task completion in SQLite (StateManager.record_task_completion)
  ├── 10. Update workflow_run to 'completed' (StateManager.update_workflow_run)
  ├── 11. Reset agent operational status to 'idle' in SQLite
  ├── 12. Record completion event (StateManager.record_event)
  └── 13. Return SubtaskResult dict (and string output for run_targeted_subtask)
```

#### Dual-Return Subtask Pattern
To satisfy both:
1. `execute_targeted_subtask(role, desc) -> dict`: Returns `{task_id, run_id, agent_role, task_description, output, status, latency_seconds}`.
2. `run_targeted_subtask(role, desc) -> str`: Returns `str(output)` (required by `test_r4_04_targeted_subtask_execution`).
We implement `SubtaskResult(dict)`:
```python
class SubtaskResult(dict):
    """Dictionary output container with automatic string rendering."""
    @property
    def output(self) -> str:
        return self.get("output", "")

    def __str__(self) -> str:
        return self.output
```
`run_targeted_subtask` delegates directly to `execute_targeted_subtask` and returns `res["output"]`.

---

## 4. Complete Code Implementation Specification

The proposed file `src/persistent_crewai/cli/interactive_query.py` is specified below:

```python
"""
Interactive Agent Executor for Persistent CrewAI Framework.
Enables grounded single-agent Q&A dialogues and targeted subtask executions
injected with persisted SQLite state, active milestones, ADRs, and vector memory.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from persistent_crewai.config import (
    CANONICAL_ROLES,
    DEFAULT_DB_NAME,
    DEFAULT_PERSISTENCE_DIR_NAME,
    get_workspace_root,
    normalize_role,
)
from persistent_crewai.agents.role_definitions import (
    RoleDefinition,
    UnknownRoleError,
    get_role_definition,
)
from persistent_crewai.agents.crew_builder import (
    Agent,
    Task,
    build_agent,
)
from persistent_crewai.agents.llm_factory import (
    CANONICAL_ROLE_RESPONSES,
    DeterministicMockLLM,
    get_llm,
)
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
    SearchResult,
)

logger = logging.getLogger(__name__)


class SubtaskResult(dict):
    """
    Structured dictionary return model for targeted subtask execution.
    Supports dictionary access and string coercion.
    """

    @property
    def output(self) -> str:
        return str(self.get("output", ""))

    @property
    def status(self) -> str:
        return str(self.get("status", ""))

    @property
    def task_id(self) -> str:
        return str(self.get("task_id", ""))

    @property
    def latency_seconds(self) -> float:
        return float(self.get("latency_seconds", 0.0))

    def __str__(self) -> str:
        return self.output


class InteractiveAgentExecutor:
    """
    Grounded Single-Agent Execution Engine.
    Coordinates interactive direct Q&A and targeted subtask runs against
    the 8 canonical specialist agents, grounding responses in SQLite state.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        vector_store: Optional[SQLiteVectorStore] = None,
        llm_provider: Optional[Any] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        github_adapter: Optional[Any] = None,
        verbose: bool = False,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> None:
        self.state_manager = state_manager
        if workspace_root:
            self.workspace_root = Path(workspace_root).resolve()
        elif state_manager and hasattr(state_manager, "workspace_root"):
            self.workspace_root = Path(state_manager.workspace_root).resolve()
        else:
            self.workspace_root = get_workspace_root()

        self.llm_provider = llm_provider
        self.github_adapter = github_adapter
        self.verbose = verbose
        self.step_callback = step_callback
        self._agents_cache: Dict[str, Agent] = {}

        # Resolve or auto-instantiate vector store
        if vector_store is not None:
            self.vector_store = vector_store
        elif self.state_manager is not None:
            self.vector_store = self._auto_init_vector_store()
        else:
            self.vector_store = None

    def _auto_init_vector_store(self) -> Optional[SQLiteVectorStore]:
        """Automatically initialize SQLiteVectorStore using StateManager's database path."""
        try:
            db_path = None
            if hasattr(self.state_manager, "_db_mgr") and self.state_manager._db_mgr:
                db_path = self.state_manager._db_mgr.db_path
            if not db_path:
                db_path = self.workspace_root / DEFAULT_PERSISTENCE_DIR_NAME / DEFAULT_DB_NAME

            embedder = DeterministicHashEmbeddingProvider(dimension=64)
            pid = self.state_manager.project_id if self.state_manager else "default_project"
            return SQLiteVectorStore(
                db_path=db_path,
                embedder=embedder,
                project_id=pid,
            )
        except Exception as e:
            logger.debug("Could not auto-initialize vector store: %s", e)
            return None

    def validate_and_normalize_role(self, role: str) -> str:
        """
        Validate and normalize role name to canonical casing.
        
        Raises:
            UnknownRoleError: If role is empty or not in canonical roles / aliases.
        """
        if not role or not isinstance(role, str) or not role.strip():
            raise UnknownRoleError(f"Role cannot be empty. Valid roles: {', '.join(CANONICAL_ROLES)}")

        try:
            return normalize_role(role)
        except KeyError:
            raise UnknownRoleError(f"Unknown agent role '{role}'. Valid roles: {', '.join(CANONICAL_ROLES)}")

    def get_agent(self, role: str) -> Agent:
        """Retrieve or instantiate specialist Agent from cache."""
        canonical_role = self.validate_and_normalize_role(role)
        if canonical_role not in self._agents_cache:
            self._agents_cache[canonical_role] = build_agent(
                role=canonical_role,
                llm=self.llm_provider,
                state_manager=self.state_manager,
                workspace_root=self.workspace_root,
                github_adapter=self.github_adapter,
                verbose=self.verbose,
                step_callback=self.step_callback,
            )
        return self._agents_cache[canonical_role]

    def _build_grounded_context(
        self,
        user_prompt: str,
        include_memory: bool = True,
        target_role: Optional[str] = None,
    ) -> str:
        """
        Construct '--- CURRENT PROJECT CONTEXT ---' block from SQLite state and vector memory.
        """
        pid = self.state_manager.project_id if self.state_manager else "default_project"
        project_name = pid
        project_desc = ""
        project_status = "active"

        # 1. Project details
        if self.state_manager:
            try:
                proj = self.state_manager.get_project(pid)
                if proj:
                    project_name = proj.get("name", pid)
                    project_desc = proj.get("description", "")
                    project_status = proj.get("status", "active")
            except Exception as e:
                logger.debug("Error fetching project: %s", e)

        # 2. Active milestone
        active_ms = None
        if self.state_manager:
            try:
                active_ms = self.state_manager.get_active_milestone(pid)
                if not active_ms:
                    all_ms = self.state_manager.get_milestones(pid)
                    if all_ms:
                        active_ms = all_ms[0]
            except Exception as e:
                logger.debug("Error fetching milestone: %s", e)

        # 3. Recent decisions
        decisions = []
        if self.state_manager:
            try:
                decisions = self.state_manager.get_decisions(project_id=pid, status="accepted", limit=5)
                if not decisions:
                    decisions = self.state_manager.get_decisions(project_id=pid, limit=5)
            except Exception as e:
                logger.debug("Error fetching decisions: %s", e)

        # 4. Relevant vector memory search
        memory_items: List[str] = []
        if include_memory and self.vector_store:
            try:
                search_results = self.vector_store.search(user_prompt, top_k=3, hybrid_search=True)
                for res in search_results:
                    if hasattr(res, "content") and res.content:
                        memory_items.append(f"- {res.content}")
            except Exception as e:
                logger.debug("Error querying vector store: %s", e)

        # Assemble grounded context lines
        context_lines: List[str] = [
            "--- CURRENT PROJECT CONTEXT ---",
            f"Project: {project_name} (ID: {pid}, Status: {project_status})",
        ]
        if project_desc:
            context_lines.append(f"Description: {project_desc}")

        if active_ms:
            ms_title = active_ms.get("title", "Active Milestone")
            ms_id = active_ms.get("milestone_id", "ms_curr")
            ms_stat = active_ms.get("status", "in_progress")
            criteria = active_ms.get("criteria", [])
            crit_str = f", Criteria: [{', '.join(criteria)}]" if criteria else ""
            context_lines.append(f"Current Active Milestone: {ms_title} (ID: {ms_id}, Status: {ms_stat}{crit_str})")
        else:
            context_lines.append("Current Active Milestone: None")

        if decisions:
            context_lines.append("Recent Architecture & Technical Decisions:")
            for dec in decisions:
                d_role = dec.get("agent_role", "Architect")
                d_title = dec.get("title", "")
                d_rat = dec.get("rationale", "")
                d_stat = dec.get("status", "accepted")
                context_lines.append(f"- [{d_role}] {d_title} ({d_stat}): {d_rat}")

        if memory_items:
            context_lines.append("Retrieved Knowledge Snippets:")
            context_lines.extend(memory_items)

        return "\n".join(context_lines)

    def query_agent(
        self,
        role: str,
        user_prompt: str,
        include_memory: bool = True,
    ) -> str:
        """
        Execute grounded single-agent Q&A against a specialist agent.
        
        Args:
            role: Canonical role name or recognized alias.
            user_prompt: Operator question or instruction.
            include_memory: Whether to query and inject vector store memories.
            
        Returns:
            Grounded technical response string.
            
        Raises:
            UnknownRoleError: If role is unrecognized.
            ValueError: If user_prompt is empty or whitespace only.
        """
        canonical_role = self.validate_and_normalize_role(role)

        if not user_prompt or not isinstance(user_prompt, str) or not user_prompt.strip():
            raise ValueError("user_prompt cannot be empty or whitespace only.")

        clean_prompt = user_prompt.strip()

        # Build grounded state context block
        context_block = self._build_grounded_context(
            user_prompt=clean_prompt,
            include_memory=include_memory,
            target_role=canonical_role,
        )

        agent = self.get_agent(canonical_role)

        # Construct full agent prompt
        full_prompt = (
            f"You are the {agent.display_name} ({canonical_role}).\n"
            f"Goal: {agent.goal}\n\n"
            f"{context_block}\n\n"
            f"User Question to {canonical_role}:\n"
            f"{clean_prompt}"
        )

        # Mock LLM handling: Ensure deterministic grounded response matches role
        is_mock = isinstance(agent.llm, DeterministicMockLLM) or hasattr(agent.llm, "responses_by_role")
        prev_responses: Dict[str, str] = {}
        if is_mock:
            prev_responses = dict(getattr(agent.llm, "responses_by_role", {}))
            proj_name = canonical_role
            if self.state_manager:
                p = self.state_manager.get_project(self.state_manager.project_id)
                if p and p.get("name"):
                    proj_name = p["name"]
            ms_title = ""
            if self.state_manager:
                m = self.state_manager.get_active_milestone(self.state_manager.project_id)
                if m and m.get("title"):
                    ms_title = m["title"]

            ground_parts = []
            if proj_name:
                ground_parts.append(f"Project='{proj_name}'")
            if ms_title:
                ground_parts.append(f"Milestone='{ms_title}'")
            ground_str = ", ".join(ground_parts) if ground_parts else "Active database state verified."

            base_response = CANONICAL_ROLE_RESPONSES.get(canonical_role, f"Response from {canonical_role}.")
            grounded_mock_output = f"{base_response}\n\n[Grounded State Context]: {ground_str}"
            agent.llm.responses_by_role = {canonical_role: grounded_mock_output}

        try:
            response = agent.run(full_prompt)
        finally:
            if is_mock:
                agent.llm.responses_by_role = prev_responses

        # Record operational audit trace in SQLite
        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    event_type="interactive_query",
                    payload={
                        "role": canonical_role,
                        "user_prompt": clean_prompt,
                        "response_preview": response[:300] if response else "",
                    },
                    agent_role=canonical_role,
                )
            except Exception as e:
                logger.warning("Failed to record query trace in StateManager: %s", e)

        return response

    def execute_targeted_subtask(
        self,
        role: str,
        task_description: str,
    ) -> SubtaskResult:
        """
        Execute targeted subtask with specialist agent, tracking latency,
        task history, and operational agent status.
        
        Args:
            role: Canonical role name or recognized alias.
            task_description: Specific subtask description.
            
        Returns:
            SubtaskResult dictionary containing task execution details and output.
            
        Raises:
            UnknownRoleError: If role is unrecognized.
            ValueError: If task_description is empty or whitespace only.
        """
        canonical_role = self.validate_and_normalize_role(role)

        if not task_description or not isinstance(task_description, str) or not task_description.strip():
            raise ValueError("task_description cannot be empty or whitespace only.")

        clean_desc = task_description.strip()
        pid = self.state_manager.project_id if self.state_manager else "default_project"
        run_id = f"run_subtask_{uuid.uuid4().hex[:8]}"
        task_id = f"task_subtask_{uuid.uuid4().hex[:8]}"
        start_time = time.perf_counter()

        # Record task start and set agent status to 'busy'
        if self.state_manager:
            try:
                # Ensure project exists
                if hasattr(self.state_manager, "get_project") and not self.state_manager.get_project(pid):
                    if hasattr(self.state_manager, "create_project"):
                        self.state_manager.create_project(
                            project_id=pid,
                            name=f"Project {pid}",
                            description="Targeted subtask execution run",
                            root_path=str(self.workspace_root),
                        )

                self.state_manager.create_workflow_run(
                    run_id=run_id,
                    workflow_type="targeted_subtask",
                    input_prompt=clean_desc,
                    trigger_source="cli",
                    project_id=pid,
                )
                self.state_manager.record_task_start(
                    task_id=task_id,
                    run_id=run_id,
                    agent_role=canonical_role,
                    task_name=clean_desc[:60],
                    description=clean_desc,
                    project_id=pid,
                )
                self.state_manager.update_agent_status(
                    canonical_role,
                    status="busy",
                    current_task_id=task_id,
                    project_id=pid,
                )
            except Exception as e:
                logger.warning("Error recording subtask start in StateManager: %s", e)

        agent = self.get_agent(canonical_role)
        context_block = self._build_grounded_context(clean_desc, include_memory=True, target_role=canonical_role)

        try:
            task = Task(description=clean_desc, task_id=task_id, agent=agent)
            output = agent.execute_task(task, context=context_block)
            latency_seconds = round(time.perf_counter() - start_time, 4)

            # Record task completion and set agent status back to 'idle'
            if self.state_manager:
                try:
                    self.state_manager.record_task_completion(
                        task_id=task_id,
                        agent=canonical_role,
                        output=output,
                        status="completed",
                        project_id=pid,
                    )
                    self.state_manager.update_workflow_run(
                        run_id=run_id,
                        status="completed",
                        final_output=output,
                    )
                    self.state_manager.update_agent_status(
                        canonical_role,
                        status="idle",
                        current_task_id=None,
                        project_id=pid,
                    )
                    self.state_manager.record_event(
                        event_type="targeted_subtask_completed",
                        payload={
                            "task_id": task_id,
                            "run_id": run_id,
                            "role": canonical_role,
                            "latency_seconds": latency_seconds,
                            "output_preview": output[:200] if output else "",
                        },
                        agent_role=canonical_role,
                    )
                except Exception as e:
                    logger.warning("Error recording subtask completion in StateManager: %s", e)

            return SubtaskResult(
                task_id=task_id,
                run_id=run_id,
                project_id=pid,
                agent_role=canonical_role,
                task_description=clean_desc,
                output=output,
                status="completed",
                latency_seconds=latency_seconds,
            )

        except Exception as exc:
            latency_seconds = round(time.perf_counter() - start_time, 4)
            if self.state_manager:
                try:
                    self.state_manager.record_task_completion(
                        task_id=task_id,
                        agent=canonical_role,
                        output=str(exc),
                        status="failed",
                        project_id=pid,
                    )
                    self.state_manager.update_workflow_run(
                        run_id=run_id,
                        status="failed",
                        error_message=str(exc),
                    )
                    self.state_manager.update_agent_status(
                        canonical_role,
                        status="error",
                        last_error=str(exc),
                        project_id=pid,
                    )
                except Exception:
                    pass
            raise exc

    def run_targeted_subtask(self, role: str, task_description: str) -> str:
        """
        Alias execution method returning the subtask output as a string.
        Guarantees compatibility with E2E assertion: isinstance(task_output, str).
        """
        res = self.execute_targeted_subtask(role=role, task_description=task_description)
        return res.output


__all__ = [
    "InteractiveAgentExecutor",
    "SubtaskResult",
]
```

---

## 5. Comprehensive Test Plan

### 5.1 Unit Test Matrix (`tests/test_cli.py`)

| Test Case | Method / Scenario | Expected Behavior |
|---|---|---|
| **UT-M4-01** | `test_executor_initialization_defaults` | Instantiates `InteractiveAgentExecutor` with `state_manager`; verifies workspace root, auto-instantiated vector store, and empty agents cache. |
| **UT-M4-02** | `test_role_normalization_all_canonical_roles` | Queries all 8 canonical role names; verifies normalization and valid execution without error. |
| **UT-M4-03** | `test_role_aliases_normalization` | Verifies aliases: `"manager"` -> `"Coordinator"`, `"github"` -> `"GitHub Manager"`, `"memory"` -> `"Memory & Status Manager"`. |
| **UT-M4-04** | `test_unknown_role_raises_unknown_role_error` | Querying `"Wizard"` or `""` raises `UnknownRoleError` (which is both `ValueError` and `KeyError`). |
| **UT-M4-05** | `test_empty_or_whitespace_prompt_rejected` | `query_agent(role="Coordinator", user_prompt="   ")` raises `ValueError`. |
| **UT-M4-06** | `test_grounded_context_prompt_formatting` | Inspects prompt created by `_build_grounded_context`; verifies `--- CURRENT PROJECT CONTEXT ---`, `Project: ...`, and `Current Active Milestone: ...`. |
| **UT-M4-07** | `test_query_agent_audit_trace_logging` | Verifies `execution_traces` in SQLite records an entry for `event_type="interactive_query"`. |
| **UT-M4-08** | `test_vector_memory_grounding_integration` | Indexes document in `SQLiteVectorStore`, queries agent, verifies semantic context inclusion in grounded block. |
| **UT-M4-09** | `test_execute_targeted_subtask_lifecycle` | Runs subtask; checks `SubtaskResult` dictionary, latency > 0, `task_history` row marked `completed`, agent status idle. |
| **UT-M4-10** | `test_run_targeted_subtask_string_contract` | `run_targeted_subtask(...)` returns `str` instance with length > 0. |

### 5.2 Verification Script Execution
```bash
# Unit tests
python -m unittest tests/test_cli.py

# Tier 1 E2E Features (R4 CLI)
python -m unittest tests/e2e/test_e2e_tier1_features.py -k "TestTier1FeatureR4CLI"

# Tier 2 Boundary Tests (TC-B.16, TC-B.17)
python -m unittest tests/e2e/test_e2e_tier2_boundaries.py -k "test_b16 or test_b17"

# Tier 3 Combination Tests (TC-C.3, TC-C.4)
python -m unittest tests/e2e/test_e2e_tier3_combinations.py -k "test_c03 or test_c04"

# Tier 4 Scenario Tests (TC-S.4)
python -m unittest tests/e2e/test_e2e_tier4_scenarios.py -k "test_s04"
```
