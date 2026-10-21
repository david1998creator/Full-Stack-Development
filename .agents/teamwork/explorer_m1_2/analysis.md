# Milestone 1: StateManager & Session Resumption — Architecture & Implementation Strategy

## 1. Executive Summary & Problem Scope

This document specifies the technical design, concrete class signatures, method implementations, error handling policies, and unit test suite for:
1. `src/persistent_crewai/persistence/state_manager.py`: The centralized transactional state manager orchestrating project context, architectural decisions, workflow runs, task history (with latency tracking), milestones, operational agent statuses, and operational event logging.
2. `src/persistent_crewai/persistence/session.py`: The session resumption engine responsible for detecting existing on-disk state, recovering interrupted workflows/tasks from abnormal termination, and rehydrating active context for downstream agent prompts.
3. Unit test plan for StateManager and Session Resumption verifying zero-leak transactional semantics, deterministic crash recovery, and offline execution without external API dependencies.

---

## 2. Interface Contracts & Alignment

### 2.1 Contracts with Other Milestones
As established in `PROJECT.md` and `specifications.md`:
- **M1 ↔ M2 (Persistence ↔ Tooling)**:
  - `StateManager.get_project_root() -> Path`: Resolves the absolute directory of the project workspace to enforce sandbox traversal constraints in `workspace_tools.py`.
  - `StateManager.record_event(event_type: str, payload: dict, ...) -> str`: Records tool invocations, results, and security events into the `execution_traces` operational audit table.
- **M1/M2 ↔ M3 (Persistence & Tools ↔ Agents)**:
  - `StateManager.get_agent_state(role: str) -> dict`: Hydrates agent operational status (e.g., idle/busy/error, completed tasks count, metrics) for role initialization and monitoring.
  - `StateManager.record_decision(agent_role: str, title: str, rationale: str, ...) -> str`: Persists architectural and technical decisions made by Architect, Coordinator, or Reviewer.
  - `StateManager.record_task_completion(task_id: str, actual_output: str, ...) -> TaskHistoryRecord`: Records finished task artifacts and computes execution latency.
- **M1/M3 ↔ M4 (Agents & State ↔ CLI/REPL)**:
  - `get_system_dashboard_data(project_id: Optional[str]) -> dict`: Aggregates active project details, 8-agent statuses, active milestone criteria, and recent decisions for rendering Rich console tables.
  - `SessionResumptionManager.rehydrate_session(project_id: Optional[str]) -> ResumptionContext`: Extracts grounded project context (milestones, decisions, interrupted tasks) for single-agent Q&A and workflow resumption.

### 2.2 Canonical Role Normalization
The framework mandates 8 persistent agent roles (`ORIGINAL_REQUEST.md` R1):
1. `Coordinator` (also aliases: `Coordinator / Manager`, `manager`)
2. `Architect`
3. `Designer`
4. `Developer`
5. `Tester`
6. `Reviewer`
7. `GitHub Manager` (aliases: `github`, `github_manager`)
8. `Memory & Status Manager` (aliases: `memory`, `memory_manager`, `Memory Manager`)

`StateManager` provides a canonicalizer to guarantee consistent database indexing and foreign key relationships across all interfaces.

```python
CANONICAL_ROLES: dict[str, str] = {
    "coordinator": "Coordinator",
    "manager": "Coordinator",
    "coordinator / manager": "Coordinator",
    "architect": "Architect",
    "designer": "Designer",
    "developer": "Developer",
    "tester": "Tester",
    "reviewer": "Reviewer",
    "github": "GitHub Manager",
    "github manager": "GitHub Manager",
    "github_manager": "GitHub Manager",
    "memory": "Memory & Status Manager",
    "memory manager": "Memory & Status Manager",
    "memory_manager": "Memory & Status Manager",
    "memory & status manager": "Memory & Status Manager",
}
```

---

## 3. Data Models (`src/persistent_crewai/persistence/state_manager.py`)

All entities are represented as strongly-typed Python dataclasses with JSON serialization helpers:

```python
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import json

@dataclass
class ProjectMetadata:
    project_id: str
    name: str
    description: str
    root_path: str
    integrity_mode: str = "development"
    status: str = "active"  # active, archived, completed
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class DecisionRecord:
    decision_id: str
    project_id: str
    agent_role: str
    title: str
    rationale: str
    implications: str = ""
    status: str = "accepted"  # proposed, accepted, superseded, rejected
    tags: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class WorkflowRun:
    run_id: str
    project_id: str
    workflow_type: str  # full_pipeline, targeted_subtask, interactive_qa
    trigger_source: str = "cli"  # cli, repl, api, test
    input_prompt: str = ""
    final_output: Optional[str] = None
    status: str = "running"  # pending, running, completed, failed, interrupted, cancelled
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class TaskHistoryRecord:
    task_id: str
    run_id: str
    project_id: str
    agent_role: str
    task_name: str
    description: str
    expected_output: Optional[str] = None
    actual_output: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, failed, interrupted, skipped
    execution_order: int = 0
    error_details: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class MilestoneRecord:
    milestone_id: str
    project_id: str
    title: str
    description: str
    criteria: List[str] = field(default_factory=list)
    sequence_order: int = 0
    target_date: Optional[str] = None
    status: str = "pending"  # pending, in_progress, achieved, missed
    achieved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class AgentStatusRecord:
    agent_role: str
    project_id: str
    status: str = "idle"  # idle, active, busy, blocked, completed, error
    current_task_id: Optional[str] = None
    last_active_at: Optional[str] = None
    tasks_completed_count: int = 0
    last_error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ExecutionTraceRecord:
    trace_id: str
    run_id: str
    task_id: Optional[str] = None
    agent_role: str = ""
    event_type: str = "operational"  # thought, tool_start, tool_end, agent_output, error, operational
    tool_name: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
```

---

## 4. `StateManager` Concrete Implementation Specification

### 4.1 Class Structure & Connection Management
`StateManager` accepts either a path to the SQLite file or a shared connection (supporting in-memory `:memory:` testing and thread safety). It uses context managers for all transactions and integrates directly with `db.py`:

```python
class StateManager:
    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        connection: Optional[sqlite3.Connection] = None,
        active_project_id: Optional[str] = None,
    ):
        # Initializes DB via db.DatabaseManager or uses existing connection
        ...
```

### 4.2 Project Metadata Methods
1. **`initialize_project(project_id, name, description, root_path, integrity_mode="development", metadata=None) -> ProjectMetadata`**:
   - Creates or updates project in `projects` table.
   - Converts `root_path` to canonical string via `Path(root_path).resolve()`.
   - Seeds default `agent_status` rows for all 8 canonical roles (`idle`, `tasks_completed_count=0`).
   - Sets `self.active_project_id = project_id`.
   - Returns `ProjectMetadata`.

2. **`get_project(project_id: Optional[str] = None) -> Optional[ProjectMetadata]`**:
   - Retrieves project by ID (defaults to `self.active_project_id`).
   - Parses `metadata_json` into a Python dict.

3. **`update_project(project_id: Optional[str] = None, **kwargs) -> ProjectMetadata`**:
   - Allows updating `name`, `description`, `status`, `integrity_mode`, and `metadata`.
   - Updates `updated_at = CURRENT_TIMESTAMP`.

4. **`get_project_root(project_id: Optional[str] = None) -> Path`**:
   - Returns `Path(project.root_path)` for workspace boundary checks (M1 ↔ M2 contract).

### 4.3 Decisions Management Methods
1. **`record_decision(agent_role: str, title: str, rationale: str, implications: str = "", tags: Optional[List[str]] = None, status: str = "accepted", project_id: Optional[str] = None, decision_id: Optional[str] = None) -> DecisionRecord`**:
   - Validates non-empty `title` and `rationale` (raises `ValueError` if empty).
   - Normalizes `agent_role` via canonical map.
   - Generates ID `dec_<uuid4().hex[:12]>` if `decision_id` is omitted.
   - Serializes `tags` to JSON array.
   - Inserts row and returns `DecisionRecord`.

2. **`get_decision(decision_id: str) -> Optional[DecisionRecord]`**:
   - Queries by `decision_id`, deserializes tags, returns record.

3. **`list_decisions(project_id: Optional[str] = None, agent_role: Optional[str] = None, status: Optional[str] = None, tag: Optional[str] = None, limit: int = 50) -> List[DecisionRecord]`**:
   - Dynamic query with parameterized SQL filtering.
   - If `tag` is specified, matches tag in JSON array (`LIKE '%"tag"%'` or SQLite `json_each`).
   - Ordered by `created_at DESC`.

4. **`update_decision_status(decision_id: str, new_status: str, superseded_by: Optional[str] = None) -> DecisionRecord`**:
   - Validates `new_status IN ('proposed', 'accepted', 'superseded', 'rejected')`.
   - If `new_status == 'superseded'`, requires or validates `superseded_by`.
   - Updates `updated_at = CURRENT_TIMESTAMP`.

### 4.4 Workflow Runs & Task History Methods
1. **`create_workflow_run(workflow_type: str, input_prompt: str, trigger_source: str = "cli", project_id: Optional[str] = None, run_id: Optional[str] = None) -> WorkflowRun`**:
   - Generates `run_<uuid4().hex[:12]>` if omitted.
   - Inserts run into `workflow_runs` with `status='running'`, `started_at=CURRENT_TIMESTAMP`.
   - Returns `WorkflowRun`.

2. **`complete_workflow_run(run_id: str, final_output: Optional[str] = None, status: str = "completed", error_message: Optional[str] = None) -> WorkflowRun`**:
   - Validates `status IN ('completed', 'failed', 'interrupted', 'cancelled')`.
   - Updates `completed_at=CURRENT_TIMESTAMP`, `final_output`, and `error_message`.

3. **`get_workflow_run(run_id: str) -> Optional[WorkflowRun]`**:
   - Fetches run record.

4. **`list_workflow_runs(project_id: Optional[str] = None, status: Optional[str] = None, limit: int = 20) -> List[WorkflowRun]`**:
   - Queries runs ordered by `started_at DESC`.

5. **`create_task(run_id: str, agent_role: str, task_name: str, description: str, expected_output: Optional[str] = None, execution_order: int = 0, project_id: Optional[str] = None, task_id: Optional[str] = None, metadata: Optional[dict] = None) -> TaskHistoryRecord`**:
   - Generates `task_<uuid4().hex[:12]>` if omitted.
   - Normalizes `agent_role`.
   - Inserts row with `status='pending'`.
   - Returns `TaskHistoryRecord`.

6. **`start_task(task_id: str) -> TaskHistoryRecord`**:
   - Updates task `status='in_progress'`, `started_at=CURRENT_TIMESTAMP`.
   - Automatically marks the assigned agent in `agent_status` as `status='busy'`, `current_task_id=task_id`.

7. **`record_task_completion(task_id: str, actual_output: str, agent_role: Optional[str] = None, metadata: Optional[dict] = None) -> TaskHistoryRecord`**:
   - Computes execution latency:
     ```python
     completed_at = datetime.utcnow()
     latency_ms = None
     if started_at:
         latency_ms = (completed_at - parse_ts(started_at)).total_seconds() * 1000.0
     ```
   - Sets `status='completed'`, `completed_at=CURRENT_TIMESTAMP`, `actual_output=actual_output`.
   - Stores `latency_ms` inside task metadata.
   - Updates `agent_status`: `tasks_completed_count = tasks_completed_count + 1`, `status='idle'`, `current_task_id=None`.
   - Returns `TaskHistoryRecord`.

8. **`record_task_failure(task_id: str, error_details: str) -> TaskHistoryRecord`**:
   - Sets `status='failed'`, `completed_at=CURRENT_TIMESTAMP`, `error_details=error_details`.
   - Updates `agent_status`: `status='error'`, `last_error=error_details`, `current_task_id=None`.

9. **`get_task(task_id: str) -> Optional[TaskHistoryRecord]`**:
   - Retrieves single task by ID.

10. **`list_tasks(run_id: Optional[str] = None, project_id: Optional[str] = None, agent_role: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> List[TaskHistoryRecord]`**:
    - Queries tasks ordered by `execution_order ASC, started_at ASC`.

### 4.5 Milestone Management Methods
1. **`create_milestone(title: str, description: str, criteria: List[str], sequence_order: int = 0, target_date: Optional[str] = None, status: str = "pending", project_id: Optional[str] = None, milestone_id: Optional[str] = None) -> MilestoneRecord`**:
   - Generates `ms_<uuid4().hex[:12]>`.
   - Serializes `criteria` to JSON array.
   - Inserts row and returns `MilestoneRecord`.

2. **`get_milestone(milestone_id: str) -> Optional[MilestoneRecord]`**:
   - Fetches milestone by ID.

3. **`list_milestones(project_id: Optional[str] = None, status: Optional[str] = None) -> List[MilestoneRecord]`**:
   - Queries ordered by `sequence_order ASC, created_at ASC`.

4. **`get_active_milestone(project_id: Optional[str] = None) -> Optional[MilestoneRecord]`**:
   - Finds first milestone with `status='in_progress'`.
   - If none, falls back to first milestone with `status='pending'` ordered by sequence.

5. **`update_milestone_status(milestone_id: str, status: str, criteria: Optional[List[str]] = None) -> MilestoneRecord`**:
   - Validates `status IN ('pending', 'in_progress', 'achieved', 'missed')`.
   - If `status == 'achieved'`, sets `achieved_at=CURRENT_TIMESTAMP`.
   - Updates `updated_at=CURRENT_TIMESTAMP`.

### 4.6 Operational Agent Status Methods
1. **`get_agent_state(agent_role: str, project_id: Optional[str] = None) -> Dict[str, Any]`**:
   - Normalizes role name.
   - Queries `agent_status`.
   - Returns structured dict:
     ```python
     {
         "agent_role": record.agent_role,
         "project_id": record.project_id,
         "status": record.status,
         "current_task_id": record.current_task_id,
         "last_active_at": record.last_active_at,
         "tasks_completed_count": record.tasks_completed_count,
         "last_error": record.last_error,
         "metrics": record.metadata,
     }
     ```

2. **`update_agent_status(agent_role: str, status: str, current_task_id: Optional[str] = None, last_error: Optional[str] = None, metrics: Optional[dict] = None, project_id: Optional[str] = None) -> AgentStatusRecord`**:
   - Validates role name against canonical list (raises `KeyError` if unrecognized).
   - Validates `status IN ('idle', 'active', 'busy', 'blocked', 'completed', 'error')`.
   - Updates `last_active_at=CURRENT_TIMESTAMP`.
   - Merges metrics into `metadata_json`.

3. **`list_agent_statuses(project_id: Optional[str] = None) -> List[AgentStatusRecord]`**:
   - Returns status of all 8 agents for dashboard rendering.

### 4.7 Operational Event Logging (`execution_traces`)
1. **`record_event(event_type: str, payload: Dict[str, Any] | str, run_id: Optional[str] = None, task_id: Optional[str] = None, agent_role: Optional[str] = None, tool_name: Optional[str] = None) -> str`**:
   - Generates `trc_<uuid4().hex[:12]>`.
   - Safely serializes payload to JSON via `json.dumps(..., default=str)`.
   - Inserts trace event row with `timestamp=CURRENT_TIMESTAMP`.
   - Returns `trace_id`.

2. **`list_traces(run_id: Optional[str] = None, agent_role: Optional[str] = None, event_type: Optional[str] = None, limit: int = 100) -> List[ExecutionTraceRecord]`**:
   - Retrieves traces ordered by `timestamp ASC`.

### 4.8 System Dashboard Data Aggregation
1. **`get_system_dashboard_data(project_id: Optional[str] = None) -> Dict[str, Any]`**:
   - Assembles:
     - `project`: ProjectMetadata dict
     - `agents`: List of all 8 AgentStatusRecord dicts
     - `active_milestone`: Active milestone dict or None
     - `all_milestones`: List of all milestone dicts
     - `recent_decisions`: Top 5 accepted decisions
     - `recent_runs`: Top 5 workflow runs
     - `metrics`: Total tasks completed, active errors, uptime statistics.

---

## 5. `SessionResumptionManager` Concrete Implementation Specification

### 5.1 Architecture & Workflow
Located in `src/persistent_crewai/persistence/session.py`.
The session resumption manager coordinates system boot, crash recovery, context rehydration, and execution resumption.

```
                    ┌─────────────────────────────────┐
                    │  detect_existing_session(path)  │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │      rehydrate_session()        │
                    └────────────────┬────────────────┘
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│     Crash Recovery Routine      │         │   Context Extraction Routine    │
│ - Find tasks 'in_progress'      │         │ - Fetch active project info     │
│ - Transition to 'interrupted'   │         │ - Fetch active milestone        │
│ - Reset busy agents to 'idle'   │         │ - Load accepted decisions       │
│ - Transition run to interrupted │         │ - Load 8 agent statuses         │
└─────────────────────────────────┘         └────────────────┬────────────────┘
                                                             │
                                                             ▼
                                            ┌─────────────────────────────────┐
                                            │        ResumptionContext        │
                                            └─────────────────────────────────┘
```

### 5.2 Resumption Data Structures

```python
@dataclass
class ResumptionContext:
    project: ProjectMetadata
    active_milestone: Optional[MilestoneRecord]
    pending_milestones: List[MilestoneRecord]
    interrupted_tasks: List[TaskHistoryRecord]
    pending_tasks: List[TaskHistoryRecord]
    recent_decisions: List[DecisionRecord]
    agent_states: Dict[str, AgentStatusRecord]
    active_run: Optional[WorkflowRun]
    summary: str

@dataclass
class WorkflowResumePlan:
    run_id: str
    workflow_type: str
    completed_tasks: List[TaskHistoryRecord]
    interrupted_tasks: List[TaskHistoryRecord]
    pending_tasks: List[TaskHistoryRecord]
    next_task: Optional[TaskHistoryRecord]
    recommended_action: str  # e.g., "retry_interrupted", "continue_pending", "all_completed"

@dataclass
class MilestoneResumePlan:
    milestone: MilestoneRecord
    completed_criteria: List[str]
    unfulfilled_criteria: List[str]
    suggested_role: str
```

### 5.3 Concrete Resumption Methods

1. **`detect_existing_session(db_path: Optional[str | Path] = None) -> bool`**:
   - Checks if SQLite database exists and is non-empty.
   - Verifies `projects` table exists and contains at least 1 record with `status != 'archived'`.
   - Returns `True` if resumable, `False` otherwise.

2. **`get_latest_project_id(db_path: Optional[str | Path] = None) -> Optional[str]`**:
   - Returns the ID of the project with the latest `updated_at`.

3. **`rehydrate_session(project_id: Optional[str] = None, auto_recover: bool = True) -> ResumptionContext`**:
   - Step 1: Resolve project. If not found, raises `SessionNotFoundError`.
   - Step 2: **Crash & Interruption Recovery**:
     - Query `task_history` for tasks with `status='in_progress'`.
     - For each, transition `status='interrupted'`, `error_details="Session interrupted: process terminated during execution"`.
     - Query `workflow_runs` for runs with `status='running'`.
     - Transition run to `status='interrupted'`.
     - Query `agent_status` for agents with `status IN ('busy', 'active')`.
     - Reset them to `status='idle'` and `current_task_id=None`.
   - Step 3: Fetch active milestone and pending milestones.
   - Step 4: Fetch accepted decisions for grounding.
   - Step 5: Fetch all 8 agent statuses.
   - Step 6: Log operational trace event `session_rehydrated`.
   - Step 7: Synthesize human-readable `summary`.
   - Returns `ResumptionContext`.

4. **`resume_workflow(run_id: str) -> WorkflowResumePlan`**:
   - Analyzes execution history of `run_id`.
   - Categorizes tasks into completed, interrupted, and pending.
   - Identifies `next_task`: first interrupted task (to retry) or first pending task (to execute).
   - Updates `workflow_runs` status back to `'running'`.
   - Returns `WorkflowResumePlan`.

5. **`resume_milestone(milestone_id: Optional[str] = None) -> MilestoneResumePlan`**:
   - Identifies active milestone.
   - Compares criteria against recorded completed task outputs.
   - Flags unfulfilled criteria.
   - Suggests next responsible role (e.g., if code needs writing -> `Developer`; if testing -> `Tester`).

---

## 6. Concurrency, Transactional Safety & Error Handling

### 6.1 Concurrency Architecture
- SQLite Write-Ahead Logging (`WAL`) mode allows concurrent readers while a write is occurring.
- `PRAGMA busy_timeout = 5000;` ensures threads wait up to 5 seconds before raising `sqlite3.OperationalError: database is locked`.
- StateManager wraps write operations in `with self._lock:` (threading.RLock) to serialize in-process multi-agent concurrent writes safely.

### 6.2 Transactional Integrity
- Every multi-step operation (such as `start_task` updating both `task_history` and `agent_status`) is wrapped in a single SQLite transaction `with self.connection:`.
- If an exception occurs, changes roll back completely, preventing inconsistent agent states.

### 6.3 Exception Hierarchy
```python
class PersistenceError(Exception):
    """Base exception for all persistence failures."""
    pass

class SessionNotFoundError(PersistenceError):
    """Raised when attempting to resume a non-existent session."""
    pass

class ProjectNotFoundError(PersistenceError):
    """Raised when project ID does not exist."""
    pass

class InvalidRoleError(PersistenceError, KeyError):
    """Raised when an unknown agent role is provided."""
    pass

class TransactionRollbackError(PersistenceError):
    """Raised when a transactional block fails and rolls back."""
    pass
```

---

## 7. Comprehensive Unit Test Plan

### 7.1 Test Organization
Tests will be implemented in `tests/test_persistence.py` and dedicated sub-modules:
- `tests/test_state_manager.py` (StateManager API unit tests)
- `tests/test_session_resumption.py` (Session resumption & recovery unit tests)

### 7.2 StateManager Test Matrix (`tests/test_state_manager.py`)

| Test ID | Method Under Test | Scenario & Input | Expected Outcome |
|---------|-------------------|------------------|------------------|
| **TC-SM-01** | `initialize_project` | Fresh DB, project info provided | Row inserted, root path resolved, 8 agents initialized to `idle`. |
| **TC-SM-02** | `get_project` & `get_project_root` | Query existing project | Returns `ProjectMetadata`, root path equals canonical Path. |
| **TC-SM-03** | `record_decision` | Valid title, agent, rationale, tags | `decision_id` returned, tags serialized as JSON array, status `accepted`. |
| **TC-SM-04** | `record_decision` (Validation) | Empty title or empty rationale | Raises `ValueError` with clear message; 0 rows inserted. |
| **TC-SM-05** | `list_decisions` | Filter by agent, status, or tag | Returns filtered `DecisionRecord` list; respect limit & order. |
| **TC-SM-06** | `update_decision_status` | Mark as superseded by another decision | Status becomes `superseded`, `superseded_by` populated. |
| **TC-SM-07** | `workflow_run_lifecycle` | Create run, complete run with output | Status moves `running` -> `completed`, timestamps populated. |
| **TC-SM-08** | `task_lifecycle_latency` | Create task, `start_task`, `record_task_completion` | Agent status toggles `busy` -> `idle`, latency ms calculated and stored in metadata. |
| **TC-SM-09** | `task_failure` | Create task, start, `record_task_failure` | Task marked `failed`, agent marked `error`, `last_error` recorded. |
| **TC-SM-10** | `milestone_lifecycle` | Create milestones with sequence, get active | `get_active_milestone` returns sequence 1, update to `achieved` sets `achieved_at`. |
| **TC-SM-11** | `agent_status_metrics` | Update status with custom metrics dict | Metrics serialized to JSON, role validated against canonical map. |
| **TC-SM-12** | `record_event` & `list_traces` | Record thought, tool_start, error | Trace rows inserted, payload preserved safely via JSON, searchable. |
| **TC-SM-13** | `get_system_dashboard_data` | Call on populated project | Returns dictionary with project, agents (8), milestones, decisions, metrics. |

### 7.3 Session Resumption Test Matrix (`tests/test_session_resumption.py`)

| Test ID | Method Under Test | Scenario & Input | Expected Outcome |
|---------|-------------------|------------------|------------------|
| **TC-SR-01** | `detect_existing_session` | Empty DB file vs. DB with initialized project | Returns `False` for empty, `True` for existing project. |
| **TC-SR-02** | `rehydrate_session` (Clean) | Project with completed tasks, idle agents | Returns `ResumptionContext` with 0 interrupted tasks, idle agents. |
| **TC-SR-03** | `crash_recovery_interrupted_tasks` | Task left with `status='in_progress'`, agent `busy` | `rehydrate_session` transitions task to `interrupted`, agent to `idle`. |
| **TC-SR-04** | `crash_recovery_workflow_run` | Run left with `status='running'` | Transitioned to `status='interrupted'`, noted in context summary. |
| **TC-SR-05** | `resume_workflow` | Run with 1 completed, 1 interrupted, 1 pending | Produces `WorkflowResumePlan` recommending retry of interrupted task first. |
| **TC-SR-06** | `resume_milestone` | Milestone with 2 criteria, 1 completed | `MilestoneResumePlan` lists unfulfilled criteria and suggests next role. |
| **TC-SR-07** | `session_not_found` | Query non-existent project ID | Raises `SessionNotFoundError`. |
| **TC-SR-08** | `multi_session_isolation` | Two distinct project IDs in same DB | Rehydrating project A only returns context, decisions, and tasks for A. |

### 7.4 Transaction & Concurrency Test Matrix

| Test ID | Method Under Test | Scenario & Input | Expected Outcome |
|---------|-------------------|------------------|------------------|
| **TC-TX-01** | `transactional_rollback` | Multi-step write where second step violates FK | Entire transaction rolls back; initial table state unchanged. |
| **TC-TX-02** | `thread_safe_writes` | 10 worker threads recording 20 decisions each | 200 decisions recorded cleanly without sqlite lock error. |

---

## 8. Implementation Code Architecture

Below is the file layout within `src/persistent_crewai/persistence/`:
```
src/persistent_crewai/persistence/
├── __init__.py           # Exports StateManager, SessionResumptionManager, data models
├── db.py                 # SQLite connection manager, WAL mode, pragmas, 8-table DDL (M1-1)
├── state_manager.py      # StateManager implementation, canonical roles, data models (M1-2)
├── session.py            # SessionResumptionManager, crash recovery, rehydration (M1-2)
└── vector_store.py       # DeterministicHashEmbeddingProvider, SQLiteVectorStore (M1-3)
```

Both `state_manager.py` and `session.py` will have zero external network dependencies and will execute with 100% determinism on Python 3.13 / Windows.
