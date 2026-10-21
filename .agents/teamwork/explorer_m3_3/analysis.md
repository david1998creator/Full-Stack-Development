# Pipeline Runners Architecture & Implementation Analysis (M3-3)

**Author**: Explorer M3-3 (Milestone 3 Pipeline Runners Explorer)  
**Date**: 2026-10-13  
**Target Module**: `src/persistent_crewai/agents/runner.py`  
**Target Test Suite**: `tests/test_pipeline.py`  
**Related Modules**: `src/persistent_crewai/persistence/state_manager.py`, `src/persistent_crewai/persistence/session.py`, `src/persistent_crewai/agents/crew_builder.py`, `src/persistent_crewai/agents/llm_factory.py`, `src/persistent_crewai/agents/role_definitions.py`

---

## 1. Executive Summary & Problem Scope

Milestone 3 establishes the execution core of the Persistent CrewAI 8-Agent Software Engineering Framework. While `role_definitions.py` specifies agent capabilities and `crew_builder.py` constructs agent and crew instances, `runner.py` is the execution engine responsible for orchestrating end-to-end engineering workflows across the 8 specialized agents:
- **Coordinator / Manager**
- **Architect**
- **Designer**
- **Developer**
- **Tester**
- **Reviewer**
- **GitHub Manager**
- **Memory & Status Manager**

The runner module must satisfy five primary technical requirements:
1. **Sequential Pipeline Runner (Feature 24)**: Deterministic, chained multi-step software engineering pipelines (e.g. Architecture -> Design -> Dev -> Test -> Review -> Git/PR -> Memory Persistence -> Project Summary) with automatic context artifact propagation from upstream to downstream agents.
2. **Hierarchical Pipeline Runner (Feature 25)**: Manager-led orchestration where the Coordinator dynamically delegates subtasks to specialized agents based on user mandate, streaming step telemetry and trapping execution errors.
3. **Resumption & Crash Recovery**: Deep integration with `SessionResumptionManager` to inspect interrupted sessions, recover dangling in-progress tasks and busy agents, rehydrate upstream artifacts, and resume execution seamlessly from the last uncompleted task.
4. **Interface Contract Conformance**: Flawless compliance with `run_project_workflow(workflow_type: str, mode: str = "sequential", project_id: str = None) -> dict` as defined in `PROJECT.md:144` and required by CLI/REPL and E2E test suites.
5. **Deterministic Offline Execution & Test Suite**: A comprehensive test plan for `tests/test_pipeline.py` running in <5 seconds without external network access or paid API keys, using `DeterministicMockLLM`.

---

## 2. Architecture & Design of `src/persistent_crewai/agents/runner.py`

### 2.1 Component Decomposition

```
                              ┌───────────────────────────────────────────────┐
                              │             CLI / REPL / API Call             │
                              │ (run_project_workflow or PipelineRunner.run) │
                              └───────────────────────┬───────────────────────┘
                                                      │
                                                      ▼
                              ┌───────────────────────────────────────────────┐
                              │           Pipeline Execution Router           │
                              │           run_project_workflow()              │
                              └───────┬───────────────────────────────┬───────┘
                                      │ mode="sequential"             │ mode="hierarchical"
                                      ▼                               ▼
                 ┌────────────────────────────────┐       ┌────────────────────────────────┐
                 │    SequentialPipelineRunner    │       │   HierarchicalPipelineRunner   │
                 │   (Chained linear pipeline)    │       │  (Coordinator as Manager)      │
                 └───────────────┬────────────────┘       └───────────────┬────────────────┘
                                 │                                        │
                                 └────────────────────┬───────────────────┘
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       ▼                                                             ▼
        ┌─────────────────────────────┐                               ┌─────────────────────────────┐
        │      Context Artifact       │                               │       StateManager &        │
        │      Propagation Bus        │                               │  SessionResumptionManager   │
        │ - Accumulates outputs       │                               │ - create/update workflow_run│
        │ - Injects into downstreams  │                               │ - record task start/complete│
        │ - Preserves on resumption   │                               │ - track latency & traces    │
        └─────────────────────────────┘                               │ - inspect & recover crashes │
                                                                      └─────────────────────────────┘
```

### 2.2 Dual-Mode Engine Architecture

To ensure total resilience across diverse environments (with or without external network, with or without third-party `crewai` installation), `runner.py` is engineered with a **Bimodal Execution Engine**:
- **Native CrewAI Mode**: When `crewai` is available, builds CrewAI `Crew` objects with `Process.sequential` or `Process.hierarchical`, attaching custom callbacks to stream telemetry and persist state.
- **Standalone Orchestration Engine**: When `crewai` is not installed or when running under pure deterministic mock mode, `runner.py` uses its own internal execution loop that directly invokes the agent's LLM/tool pipeline while preserving identical context passing, state management, latency tracking, and trace logging.

This design guarantees 100% test reliability and zero unexpected crashes in offline CI environments.

---

## 3. Sequential Pipeline Runner (Feature 24)

### 3.1 Chained Multi-Step Workflows

A sequential pipeline executes an ordered list of tasks where each step builds upon all preceding deliverables.

#### Canonical Software Engineering Workflow (`feature_development`):

| Step | Role | Step Name | Description | Upstream Context Required |
|:---:|:---|:---|:---|:---|
| **1** | **Architect** | `architecture_design` | Analyze requirements, author ADR, specify SQLite schema and interface boundaries. | User input / project requirements |
| **2** | **Designer** | `ui_ux_specification` | Define CLI formatting, layout, user interaction flows, and command structures. | Step 1 (Architect blueprint) |
| **3** | **Developer** | `feature_implementation` | Implement production Python code in the sandboxed workspace. | Step 1 (Architect) + Step 2 (Designer) |
| **4** | **Tester** | `test_verification` | Author unit/integration tests and verify implementation correctness. | Step 3 (Developer code) |
| **5** | **Reviewer** | `code_review_security` | Perform security audit, static analysis, PEP-8 compliance, and issue approval verdict. | Step 3 (Developer code) + Step 4 (Test report) |
| **6** | **GitHub Manager** | `git_branch_pr` | Create git branch, commit changes, generate Pull Request with description. | Step 5 (Reviewer approval) |
| **7** | **Memory Manager**| `persist_state_milestone`| Record decision records, update project milestones, commit state to SQLite WAL. | Steps 1–6 outputs |
| **8** | **Coordinator** | `executive_summary` | Synthesize overall project deliverables, release notes, and operational status. | Step 7 (Persisted state summary) |

#### Additional Standard Workflows:
1. **`bugfix`**:
   - Step 1: GitHub Manager -> Read issue and reproduction steps
   - Step 2: Developer -> Apply targeted patch using `WorkspaceFilePatchTool`
   - Step 3: Tester -> Execute regression test verification
   - Step 4: Reviewer -> Review patch for safety and style
   - Step 5: GitHub Manager -> Create commit, open PR, link issue
   - Step 6: Memory Manager -> Persist fix record
   - Step 7: Coordinator -> Final summary
2. **`code_review`**:
   - Step 1: Architect / Developer -> Supply codebase context
   - Step 2: Reviewer -> Static analysis, security audit, approval verdict
   - Step 3: Tester -> Verify test coverage metrics
   - Step 4: Coordinator -> Review synthesis and recommendations
3. **`targeted_subtask`**:
   - Direct execution of a single task by a designated specialist agent.

### 3.2 Context Artifact Propagation Mechanics

Context propagation ensures downstream agents have complete visibility into upstream artifacts without manual copy-pasting.

#### Propagation Algorithm:
1. Initialize an in-memory dictionary `context_artifacts: Dict[str, str] = {}` (mapping `agent_role` -> `actual_output`).
2. When executing Step $i$ assigned to Agent $A_i$:
   - Identify prerequisite roles $P = \{A_1, A_2, \dots, A_{i-1}\}$ required for Step $i$.
   - Format upstream context:
     ```markdown
     ## Prior Context Artifacts from Upstream Team:
     ### Architect Specification:
     {context_artifacts['Architect']}
     
     ### Designer Specification:
     {context_artifacts['Designer']}
     ```
   - Prepend/inject this context block into the task description passed to $A_i$.
   - If using native CrewAI, construct `Task(..., context=[task_arch, task_des, ...])`.
3. Capture $A_i$'s output string upon completion.
4. Update `context_artifacts[A_i] = output_str`.
5. Write `actual_output` to SQLite table `task_history`.

---

## 4. Hierarchical Pipeline Runner (Feature 25)

### 4.1 Manager-Led Orchestration

The hierarchical runner models dynamic delegation under the leadership of the **Coordinator / Manager**:
- **Manager Role Identifier**: Must explicitly expose `runner.manager_role = "Coordinator"` (strictly verified by `test_r1_06_hierarchical_runner_configuration`).
- **Delegation Mechanics**:
  - The Coordinator receives the high-level user mandate (e.g. `workflow_type="targeted_subtask"`, `inputs={"task_description": "Review architecture blueprint"}`).
  - Coordinator evaluates the mandate:
    - If task involves "Review" or "Audit" -> delegates to **Reviewer**.
    - If task involves "Architecture" or "Schema" -> delegates to **Architect**.
    - If task involves "Code" or "Implementation" -> delegates to **Developer**.
    - If task involves "Test" -> delegates to **Tester**.
    - If task involves "UI" or "CLI" -> delegates to **Designer**.
    - If task involves "Git", "Branch", "PR" -> delegates to **GitHub Manager**.
    - If task involves "Memory", "State", "Restore" -> delegates to **Memory & Status Manager**.
  - Coordinator dispatches subtasks, awaits execution, reviews output, and formats the final executive synthesis.

### 4.2 Telemetry Streaming & Error Trapping

The runner implements fine-grained execution observability and fault tolerance:
- **`step_callback` Protocol**:
  - Callback signature: `Callable[[Dict[str, Any]], None]`
  - Emits real-time event payloads:
    ```python
    {
        "event": "step_start" | "thought" | "tool_call" | "step_complete" | "error",
        "run_id": run_id,
        "task_id": task_id,
        "agent_role": agent_role,
        "timestamp": iso_timestamp,
        "payload": event_details,
    }
    ```
- **Execution Traces in SQLite**:
  - Every step event is persisted via `state_manager.record_trace(...)` in the `execution_traces` table.
- **Error Trapping Matrix**:
  - If a specialist agent throws an exception:
    1. Trap exception in a `try...except` block.
    2. Record failure in `task_history`: `state_manager.record_task_failure(task_id, error_details=str(e))`.
    3. Update agent status: `state_manager.update_agent_status(role, status="error", last_error=str(e))`.
    4. Record trace: `state_manager.record_trace(run_id, task_id, role, event_type="task_error", payload={"error": str(e)})`.
    5. In `finally` block: ensure agent is never left in `'busy'` state.
    6. Transition workflow run: `state_manager.complete_workflow_run(run_id, status="failed", error_message=str(e))`.
    7. Return structured error dictionary without terminating the caller process.

---

## 5. State Persistence Integration (`StateManager`)

Every phase of workflow execution interacts directly with `StateManager`:

```
   Workflow Lifecycle Phase               StateManager API Call
┌─────────────────────────────┐        ┌────────────────────────────────────────────────────────┐
│ 1. Workflow Initialized     │ -----> │ sm.create_workflow_run(run_id, workflow_type, prompt)  │
├─────────────────────────────┤        ├────────────────────────────────────────────────────────┤
│ 2. Task Registered          │ -----> │ sm.record_task_start(task_id, run_id, role, name, ...) │
├─────────────────────────────┤        ├────────────────────────────────────────────────────────┤
│ 3. Task Execution Started   │ -----> │ sm.start_task(task_id)                                 │
│                             │        │ -> sets task 'in_progress', agent 'busy'              │
├─────────────────────────────┤        ├────────────────────────────────────────────────────────┤
│ 4. Telemetry Event Recorded │ -----> │ sm.record_trace(run_id, task_id, role, event_type, ...)│
├─────────────────────────────┤        ├────────────────────────────────────────────────────────┤
│ 5. Task Completed           │ -----> │ sm.record_task_completion(task_id, role, output,       │
│                             │        │                           metadata={"latency_ms": ...})│
│                             │        │ -> sets task 'completed', agent 'idle', inc count      │
├─────────────────────────────┤        ├────────────────────────────────────────────────────────┤
│ 6. Workflow Completed       │ -----> │ sm.complete_workflow_run(run_id, final_output, status) │
│                             │        │ sm.record_event("workflow_completed", ...)             │
└─────────────────────────────┘        └────────────────────────────────────────────────────────┘
```

### High-Resolution Latency Tracking
Latency must be measured accurately per task:
```python
t_start = time.perf_counter()
try:
    task_output = execute_task(...)
finally:
    latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
    state_manager.record_task_completion(
        task_id=task_id,
        agent=agent_role,
        output=task_output,
        metadata={"latency_ms": latency_ms},
    )
```

---

## 6. Resumption & Crash Recovery Mechanics

### 6.1 Crash Recovery Protocol

When an agent process terminates unexpectedly (SIGKILL, power failure, unhandled crash) during execution:
1. In SQLite, the affected task remains `status = 'in_progress'` with no `completed_at` timestamp.
2. The agent remains `status = 'busy'` in `agent_status`.
3. The workflow run remains `status = 'running'` in `workflow_runs`.

Upon system restart, the runner calls `SessionResumptionManager`:
```python
resumption_mgr = SessionResumptionManager(db_path=db_path, project_id=project_id)
inspection = resumption_mgr.inspect_state(auto_recover=True)
```
`inspect_state(auto_recover=True)` automatically:
- Transitions all abandoned `in_progress` tasks to `status = 'interrupted'`.
- Resets all `busy` / `active` agents back to `status = 'idle'`.
- Transitions active `running` workflow runs to `status = 'interrupted'`.

### 6.2 Resumption Execution Protocol

When `runner.resume(run_id)` or `run(..., resume_run_id=run_id)` is invoked:
1. `plan = resumption_mgr.resume_workflow(run_id)`:
   - Identifies:
     - `completed_tasks`: tasks that succeeded before the crash.
     - `interrupted_tasks`: tasks interrupted mid-execution.
     - `pending_tasks`: tasks not yet started.
     - `next_task`: the first interrupted task (to retry) or first pending task.
   - Sets `workflow_run.status = 'running'`.
2. **Context Rehydration**:
   - Reconstructs `context_artifacts` from all `completed_tasks`:
     ```python
     for task in plan.completed_tasks:
         context_artifacts[task["agent_role"]] = task.get("actual_output") or ""
     ```
3. **Execution Resumption**:
   - Identifies all tasks that must run: `tasks_to_run = plan.interrupted_tasks + plan.pending_tasks`.
   - Re-executes `next_task` as a retry, recording its start and completion.
   - Proceeds linearly through the remaining tasks.
   - Completes the workflow run and updates overall status to `'completed'`.

---

## 7. Interface Contract Conformance

Per `PROJECT.md:144` and task specifications:

```python
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
```

### Response Schema:
```python
{
    "run_id": "run_gf_001",
    "project_id": "default_project",
    "workflow_type": "feature_development",
    "mode": "sequential",
    "status": "completed",               # "completed" | "failed" | "interrupted"
    "final_output": "...",               # Final executive summary or task output
    "tasks_executed": [                  # List of executed task summaries
        {
            "task_id": "task_arch",
            "agent_role": "Architect",
            "task_name": "architecture_design",
            "status": "completed",
            "latency_ms": 142.5,
            "output": "..."
        },
        ...
    ],
    "latency_total_ms": 1250.4,
    "error_message": None,
}
```

### Parameter Validation:
- `mode` must be either `"sequential"` or `"hierarchical"`. If invalid, raises `ValueError(f"Invalid execution mode '{mode}'. Must be 'sequential' or 'hierarchical'.")`.
- `workflow_type` must be a non-empty string.
- If `state_manager` is None, initializes default `StateManager(project_id=project_id)`.

---

## 8. Detailed Class Specification for `src/persistent_crewai/agents/runner.py`

```python
"""
Runner module for Persistent CrewAI multi-agent software engineering pipelines.
Provides SequentialPipelineRunner (Feature 24), HierarchicalPipelineRunner (Feature 25),
session resumption & crash recovery, and the run_project_workflow() interface contract.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

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

# Dynamic imports from sibling M3 modules
def _get_llm():
    from persistent_crewai.agents.llm_factory import get_llm
    return get_llm()

def _build_team(state_manager=None, llm=None):
    from persistent_crewai.agents.crew_builder import build_team
    return build_team(state_manager=state_manager, llm=llm)


class SequentialPipelineRunner:
    """Sequential Pipeline Runner (Feature 24).
    Chains tasks in linear sequence, propagates context artifacts downstream,
    persists all steps and latencies via StateManager, and supports resumption.
    """
    ...

class HierarchicalPipelineRunner:
    """Hierarchical Pipeline Runner (Feature 25).
    Manager-led orchestration with Coordinator as manager_role, dynamic
    specialist subtask delegation, telemetry streaming, and error trapping.
    """
    manager_role: str = "Coordinator"
    ...

def run_project_workflow(...) -> Dict[str, Any]:
    """Unified entrypoint contract for running or resuming project workflows."""
    ...
```

---

## 9. Comprehensive Test Plan (`tests/test_pipeline.py`)

A standalone test suite `tests/test_pipeline.py` will verify all aspects of pipeline execution using standard library `unittest` and `DeterministicMockLLM`.

### Test Matrix

| Test ID | Test Class | Focus / Objective | Assertions |
|---|---|---|---|
| `TC-P1.1` | `TestSequentialPipelineRunner` | Default initialization & properties | Runner holds `state_manager`, `workspace_root`, initialized team |
| `TC-P1.2` | `TestSequentialPipelineRunner` | Full 8-step `feature_development` run | `status == "completed"`, `len(tasks_executed) == 8`, all tasks completed |
| `TC-P1.3` | `TestSequentialPipelineRunner` | Upstream context propagation | Downstream prompts receive upstream outputs; verified via call traces |
| `TC-P1.4` | `TestSequentialPipelineRunner` | State persistence & latency tracking | SQLite tables `workflow_runs`, `task_history`, `agent_status` match run; `latency_ms > 0` |
| `TC-P1.5` | `TestSequentialPipelineRunner` | `bugfix` multi-step pipeline | Executes patch, test, review, and git steps; verifies `status == "completed"` |
| `TC-P1.6` | `TestSequentialPipelineRunner` | Step failure trapping & rollback | Failed task recorded as `failed`, workflow marked `failed`, agents set to `idle`/`error` |
| `TC-P2.1` | `TestHierarchicalPipelineRunner` | Manager role identity | `runner.manager_role == "Coordinator"` |
| `TC-P2.2` | `TestHierarchicalPipelineRunner` | Targeted subtask dynamic delegation | Coordinator delegates to requested specialist; returns valid output |
| `TC-P2.3` | `TestHierarchicalPipelineRunner` | Step execution telemetry streaming | Custom `step_callback` called for all reasoning and tool steps |
| `TC-P2.4` | `TestHierarchicalPipelineRunner` | Error trapping during delegation | Catches specialist exceptions, updates `agent_status.last_error`, no crash |
| `TC-P3.1` | `TestPipelineResumption` | Crash detection & state inspection | Interrupted task transitioned to `interrupted`, busy agents reset to `idle` |
| `TC-P3.2` | `TestPipelineResumption` | Resume workflow from last completed task | Steps 1-2 completed, step 3 interrupted -> resumes from step 3, skipping 1-2, preserving context |
| `TC-P4.1` | `TestRunProjectWorkflowContract` | Contract conformance (sequential) | `run_project_workflow("feature_development", mode="sequential")` returns valid schema |
| `TC-P4.2` | `TestRunProjectWorkflowContract` | Contract conformance (hierarchical) | `run_project_workflow("targeted_subtask", mode="hierarchical")` returns valid schema |
| `TC-P4.3` | `TestRunProjectWorkflowContract` | Invalid mode validation | `mode="invalid"` raises `ValueError` |
| `TC-P5.1` | `TestPipelineEdgeCases` | Empty inputs dictionary | Runs successfully with sensible defaults |
| `TC-P5.2` | `TestPipelineEdgeCases` | High precision latency non-negativity | `latency_ms >= 0` across all steps |

---

## 10. Verification & Execution Strategy

1. **Deterministic Execution**: By binding `DeterministicMockLLM`, tests execute completely offline without network calls or API token dependencies.
2. **Execution Time**: The complete `tests/test_pipeline.py` suite will run in <2 seconds.
3. **Compatibility**: Conforms with `tests/e2e/test_e2e_tier1_features.py`, `test_e2e_tier3_combinations.py`, and `test_e2e_tier4_scenarios.py`.
