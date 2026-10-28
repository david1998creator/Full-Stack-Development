"""
State tracking and transactional state management for Persistent CrewAI.
Orchestrates projects, decisions, workflow runs, task history (with latency tracking),
milestones, agent operational status, and execution traces.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

from persistent_crewai.config import (
    CANONICAL_ROLES,
    ROLE_ALIASES,
    DEFAULT_INTEGRITY_MODE,
    get_workspace_root,
    normalize_role,
)
from persistent_crewai.persistence.db import DatabaseManager, init_db

VALID_TASK_STATUSES = {"pending", "in_progress", "completed", "failed", "interrupted", "skipped", "cancelled"}


def _is_agent_role(val: Any) -> bool:
    """Check if value corresponds to a known canonical agent role or alias."""
    if not val or not isinstance(val, str):
        return False
    c = val.strip().lower()
    return c in ROLE_ALIASES or any(r.lower() == c for r in CANONICAL_ROLES)


class PersistenceError(Exception):
    """Base exception for all persistence failures."""
    pass


class InvalidRoleError(PersistenceError, KeyError):
    """Raised when an unrecognized agent role is provided."""
    pass


class ProjectNotFoundError(PersistenceError):
    """Raised when the specified project ID does not exist."""
    pass


@dataclass
class ProjectMetadata:
    project_id: str
    name: str
    description: str
    root_path: str
    integrity_mode: str = DEFAULT_INTEGRITY_MODE
    status: str = "active"
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
    status: str = "accepted"
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
    workflow_type: str
    trigger_source: str = "cli"
    input_prompt: str = ""
    final_output: Optional[str] = None
    status: str = "running"
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
    status: str = "pending"
    execution_order: int = 0
    error_details: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
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
    target_date: Optional[str] = None
    status: str = "pending"
    achieved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentStatusRecord:
    agent_role: str
    project_id: str
    status: str = "idle"
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
    event_type: str = "operational"
    tool_name: Optional[str] = None
    payload: Any = None
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    """Current UTC timestamp formatted as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp or SQLite CURRENT_TIMESTAMP string."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None


class StateManager:
    """
    Centralized state management API for 8-agent persistent CrewAI framework.
    Manages SQLite relational tables with WAL mode, foreign key validation,
    and thread safety.
    """

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        project_id: Optional[str] = None,
        workspace_root: Optional[str | Path] = None,
        connection: Optional[sqlite3.Connection] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
        self.project_id = project_id or "default_project"
        self._lock = threading.RLock()

        if connection is not None:
            self._db_mgr = None
            self._external_conn = connection
        else:
            self._external_conn = None
            self._db_mgr = DatabaseManager(db_path=db_path)
            self._db_mgr.initialize_schema()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Transactional context manager for database operations."""
        if self._external_conn is not None:
            with self._lock:
                try:
                    yield self._external_conn
                    self._external_conn.commit()
                except Exception:
                    self._external_conn.rollback()
                    raise
        else:
            with self._lock:
                with self._db_mgr.connection() as conn:
                    yield conn

    # -------------------------------------------------------------------------
    # Project CRUD
    # -------------------------------------------------------------------------
    def create_project(
        self,
        project_id: str,
        name: str,
        description: str,
        root_path: Optional[str | Path] = None,
        integrity_mode: str = DEFAULT_INTEGRITY_MODE,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update a project record and seed initial agent statuses."""
        resolved_root = str(Path(root_path).resolve() if root_path else self.workspace_root)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        now = _now_iso()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO projects (project_id, name, description, root_path, integrity_mode, status, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    root_path = excluded.root_path,
                    integrity_mode = excluded.integrity_mode,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (project_id, name, description, resolved_root, integrity_mode, now, now, meta_json),
            )

            # Seed all 8 canonical agent roles with idle status
            for role in CANONICAL_ROLES:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO agent_status (agent_role, project_id, status, tasks_completed_count, last_active_at, metadata_json)
                    VALUES (?, ?, 'idle', 0, ?, '{}')
                    """,
                    (role, project_id, now),
                )

        self.project_id = project_id
        return self.get_project(project_id) or {
            "project_id": project_id,
            "name": name,
            "description": description,
            "root_path": resolved_root,
            "integrity_mode": integrity_mode,
            "status": "active",
            "metadata": metadata or {},
        }

    def initialize_project(self, *args, **kwargs) -> Dict[str, Any]:
        """Alias for create_project."""
        return self.create_project(*args, **kwargs)

    def get_project(self, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve project record by ID."""
        pid = project_id or self.project_id
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (pid,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
            return data

    def update_project(self, project_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Update fields of an existing project."""
        pid = project_id or self.project_id
        updates = []
        params: list[Any] = []
        for k, v in kwargs.items():
            if k == "metadata":
                updates.append("metadata_json = ?")
                params.append(json.dumps(v, ensure_ascii=False))
            elif k in ("name", "description", "root_path", "integrity_mode", "status"):
                updates.append(f"{k} = ?")
                params.append(v)
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(pid)

        with self.connection() as conn:
            conn.execute(f"UPDATE projects SET {', '.join(updates)} WHERE project_id = ?", params)

        res = self.get_project(pid)
        if not res:
            raise ProjectNotFoundError(f"Project '{pid}' not found.")
        return res

    def get_project_root(self, project_id: Optional[str] = None) -> Path:
        """Resolve project root directory for workspace boundary sandboxing."""
        project = self.get_project(project_id)
        if project and project.get("root_path"):
            return Path(project["root_path"]).resolve()
        return self.workspace_root

    # -------------------------------------------------------------------------
    # Decisions Management
    # -------------------------------------------------------------------------
    def record_decision(
        self,
        agent_role: str,
        title: str,
        rationale: str,
        implications: str = "",
        tags: Optional[List[str]] = None,
        status: str = "accepted",
        project_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> str:
        """Record an architectural, design, or technical decision."""
        if not title or not title.strip():
            raise ValueError("Decision title cannot be empty or whitespace.")
        if not rationale or not rationale.strip():
            raise ValueError("Decision rationale cannot be empty or whitespace.")

        try:
            canonical_role = normalize_role(agent_role)
        except KeyError:
            canonical_role = agent_role

        pid = project_id or self.project_id
        dec_id = decision_id or f"dec_{uuid.uuid4().hex[:12]}"
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        now = _now_iso()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications, status, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (dec_id, pid, canonical_role, title.strip(), rationale.strip(), implications, status, tags_json, now, now),
            )

        return dec_id

    def get_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single decision by ID."""
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["tags"] = json.loads(data.pop("tags", "[]") or "[]")
            return data

    def get_decisions(
        self,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_role: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query decisions with optional filtering."""
        pid = project_id or self.project_id
        query = "SELECT * FROM decisions WHERE project_id = ?"
        params: list[Any] = [pid]

        if status:
            query += " AND status = ?"
            params.append(status)
        if agent_role:
            try:
                norm_role = normalize_role(agent_role)
            except KeyError:
                norm_role = agent_role
            query += " AND agent_role = ?"
            params.append(norm_role)
        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["tags"] = json.loads(d.pop("tags", "[]") or "[]")
                results.append(d)
            return results

    def list_decisions(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Alias for get_decisions."""
        return self.get_decisions(*args, **kwargs)

    def update_decision_status(
        self,
        decision_id: str,
        new_status: str,
        superseded_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update decision status (e.g. accepted -> superseded)."""
        if new_status not in ("proposed", "accepted", "superseded", "rejected"):
            raise ValueError(f"Invalid decision status '{new_status}'.")

        with self.connection() as conn:
            conn.execute(
                """
                UPDATE decisions
                SET status = ?, superseded_by = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                (new_status, superseded_by, _now_iso(), decision_id),
            )

        res = self.get_decision(decision_id)
        if not res:
            raise PersistenceError(f"Decision '{decision_id}' not found.")
        return res

    # -------------------------------------------------------------------------
    # Workflow Runs & Tasks
    # -------------------------------------------------------------------------
    def create_workflow_run(
        self,
        run_id: Optional[str] = None,
        workflow_type: str = "feature_development",
        input_prompt: str = "",
        trigger_source: str = "cli",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new workflow run session."""
        pid = project_id or self.project_id
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        now = _now_iso()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (run_id, project_id, workflow_type, trigger_source, input_prompt, status, started_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (rid, pid, workflow_type, trigger_source, input_prompt, now),
            )

        return {
            "run_id": rid,
            "project_id": pid,
            "workflow_type": workflow_type,
            "trigger_source": trigger_source,
            "input_prompt": input_prompt,
            "status": "running",
            "started_at": now,
        }

    def update_workflow_run(
        self,
        run_id: str,
        status: Optional[str] = None,
        final_output: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update workflow run state and output."""
        updates = []
        params: list[Any] = []
        if status:
            updates.append("status = ?")
            params.append(status)
            if status in ("completed", "failed", "interrupted", "cancelled"):
                updates.append("completed_at = ?")
                params.append(_now_iso())
        if final_output is not None:
            updates.append("final_output = ?")
            params.append(final_output)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if updates:
            params.append(run_id)
            with self.connection() as conn:
                conn.execute(f"UPDATE workflow_runs SET {', '.join(updates)} WHERE run_id = ?", params)

        res = self.get_workflow_run(run_id)
        return res or {"run_id": run_id, "status": status}

    def complete_workflow_run(
        self,
        run_id: str,
        final_output: Optional[str] = None,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark workflow run as completed or failed."""
        return self.update_workflow_run(run_id, status=status, final_output=final_output, error_message=error_message)

    def get_workflow_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve workflow run record."""
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_workflow_runs(
        self,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List workflow runs for project."""
        pid = project_id or self.project_id
        query = "SELECT * FROM workflow_runs WHERE project_id = ?"
        params: list[Any] = [pid]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def record_task_start(
        self,
        task_id: str,
        run_id: str,
        agent_role: str,
        task_name: str,
        description: str = "",
        expected_output: Optional[str] = None,
        execution_order: int = 0,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record the start/creation of a task."""
        pid = project_id or self.project_id
        try:
            canonical_role = normalize_role(agent_role)
        except KeyError:
            canonical_role = agent_role

        now = _now_iso()
        meta_dict = metadata or {}
        meta_dict["started_at"] = now
        meta_json = json.dumps(meta_dict, ensure_ascii=False)

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO task_history (
                    task_id, run_id, project_id, agent_role, task_name,
                    description, expected_output, status, execution_order,
                    started_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    agent_role = excluded.agent_role,
                    task_name = excluded.task_name,
                    description = excluded.description,
                    expected_output = excluded.expected_output,
                    status = excluded.status,
                    execution_order = excluded.execution_order,
                    started_at = excluded.started_at,
                    metadata_json = excluded.metadata_json
                """,
                (task_id, run_id, pid, canonical_role, task_name, description, expected_output, execution_order, now, meta_json),
            )

        return {
            "task_id": task_id,
            "run_id": run_id,
            "project_id": pid,
            "agent_role": canonical_role,
            "task_name": task_name,
            "description": description,
            "status": "pending",
            "execution_order": execution_order,
            "started_at": now,
            "metadata": meta_dict,
        }

    def create_task(self, *args, **kwargs) -> Dict[str, Any]:
        """Alias for record_task_start."""
        return self.record_task_start(*args, **kwargs)

    def start_task(self, task_id: str) -> Dict[str, Any]:
        """Transition task to 'in_progress' and set agent to 'busy'."""
        now = _now_iso()
        with self.connection() as conn:
            cur = conn.execute("SELECT agent_role, project_id FROM task_history WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                raise PersistenceError(f"Task '{task_id}' not found.")
            agent_role = row["agent_role"]
            pid = row["project_id"]

            conn.execute(
                "UPDATE task_history SET status = 'in_progress', started_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.execute(
                """
                UPDATE agent_status
                SET status = 'busy', current_task_id = ?, last_active_at = ?
                WHERE agent_role = ? AND project_id = ?
                """,
                (task_id, now, agent_role, pid),
            )

        res = self.get_task(task_id)
        return res or {"task_id": task_id, "status": "in_progress"}

    def record_task_completion(
        self,
        task_id: str,
        *args: Any,
        agent: Optional[str] = None,
        output: Optional[str] = None,
        actual_output: Optional[str] = None,
        status: str = "completed",
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Record task completion, calculate latency, and reset agent status to idle.
        
        Supports both the PROJECT.md:139 contract signature:
            record_task_completion(task_id: str, agent: str, output: str) -> None
        and keyword arguments:
            record_task_completion(task_id=..., agent=..., output=..., actual_output=..., status=...)
        as well as legacy positional calls:
            record_task_completion(task_id, output)
        """
        eff_agent = agent
        eff_output = output if output is not None else actual_output
        eff_status = status

        if len(args) == 1:
            # record_task_completion(task_id, output)
            if eff_output is None:
                eff_output = args[0]
        elif len(args) == 2:
            # Either (task_id, agent, output) [PROJECT.md] or legacy (task_id, actual_output, status)
            arg1, arg2 = args[0], args[1]
            if arg2 in VALID_TASK_STATUSES and not _is_agent_role(arg1):
                if eff_output is None:
                    eff_output = arg1
                eff_status = arg2
            else:
                if eff_agent is None:
                    eff_agent = arg1
                if eff_output is None:
                    eff_output = arg2
        elif len(args) >= 3:
            # record_task_completion(task_id, agent, output, status)
            if eff_agent is None:
                eff_agent = args[0]
            if eff_output is None:
                eff_output = args[1]
            if args[2] in VALID_TASK_STATUSES:
                eff_status = args[2]

        if eff_output is None:
            eff_output = ""

        canonical_agent = None
        if eff_agent:
            try:
                canonical_agent = normalize_role(eff_agent)
            except KeyError:
                canonical_agent = eff_agent

        now = _now_iso()
        completed_dt = datetime.now(timezone.utc)
        latency_ms: Optional[float] = None

        with self.connection() as conn:
            cur = conn.execute(
                "SELECT agent_role, project_id, started_at, metadata_json FROM task_history WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
            agent_role = canonical_agent or (row["agent_role"] if row else None)
            pid = (row["project_id"] if row else None) or project_id or self.project_id
            started_at = row["started_at"] if row else None
            existing_meta = json.loads(row["metadata_json"] or "{}") if (row and row["metadata_json"]) else {}

            if started_at:
                s_dt = _parse_ts(started_at)
                if s_dt:
                    latency_ms = (completed_dt - s_dt).total_seconds() * 1000.0

            merged_meta = {**existing_meta, **(metadata or {})}
            if latency_ms is not None:
                merged_meta["latency_ms"] = round(latency_ms, 2)
            merged_meta["completed_at"] = now
            meta_json = json.dumps(merged_meta, ensure_ascii=False)

            conn.execute(
                """
                UPDATE task_history
                SET actual_output = ?, status = ?, completed_at = ?, metadata_json = ?,
                    agent_role = COALESCE(?, agent_role)
                WHERE task_id = ?
                """,
                (eff_output, eff_status, now, meta_json, canonical_agent, task_id),
            )
            if agent_role and pid:
                conn.execute(
                    """
                    UPDATE agent_status
                    SET status = 'idle',
                        current_task_id = NULL,
                        tasks_completed_count = tasks_completed_count + 1,
                        last_active_at = ?
                    WHERE agent_role = ? AND project_id = ?
                    """,
                    (now, agent_role, pid),
                )

        res = self.get_task(task_id)
        return res or {"task_id": task_id, "status": eff_status, "actual_output": eff_output}

    def record_task_failure(
        self,
        task_id: str,
        error_details: str,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record task failure and set agent status to error."""
        now = _now_iso()
        with self.connection() as conn:
            cur = conn.execute("SELECT agent_role, project_id FROM task_history WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            agent_role = row["agent_role"] if row else None
            pid = (row["project_id"] if row else None) or project_id or self.project_id

            conn.execute(
                """
                UPDATE task_history
                SET status = 'failed', error_details = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (error_details, now, task_id),
            )
            if agent_role and pid:
                conn.execute(
                    """
                    UPDATE agent_status
                    SET status = 'error',
                        current_task_id = NULL,
                        last_error = ?,
                        last_active_at = ?
                    WHERE agent_role = ? AND project_id = ?
                    """,
                    (error_details, now, agent_role, pid),
                )

        res = self.get_task(task_id)
        return res or {"task_id": task_id, "status": "failed", "error_details": error_details}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve task by ID."""
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM task_history WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
            return data

    def get_task_history(
        self,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_role: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieve task history records."""
        conditions = []
        params: list[Any] = []

        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if agent_role:
            try:
                norm_role = normalize_role(agent_role)
            except KeyError:
                norm_role = agent_role
            conditions.append("agent_role = ?")
            params.append(norm_role)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM task_history {where_clause} ORDER BY execution_order ASC, started_at ASC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
                results.append(d)
            return results

    def list_tasks(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Alias for get_task_history."""
        return self.get_task_history(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Milestones Management
    # -------------------------------------------------------------------------
    def create_milestone(
        self,
        milestone_id: Optional[str] = None,
        title: str = "",
        description: str = "",
        criteria: Optional[List[str]] = None,
        sequence_order: int = 0,
        target_date: Optional[str] = None,
        status: str = "pending",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create project milestone record."""
        pid = project_id or self.project_id
        mid = milestone_id or f"ms_{uuid.uuid4().hex[:12]}"
        criteria_list = criteria or []
        criteria_json = json.dumps(criteria_list, ensure_ascii=False)
        now = _now_iso()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO milestones (milestone_id, project_id, title, description, criteria_json, target_date, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(milestone_id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    criteria_json = excluded.criteria_json,
                    target_date = excluded.target_date,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (mid, pid, title, description, criteria_json, target_date, status, now, now),
            )

        return {
            "milestone_id": mid,
            "project_id": pid,
            "title": title,
            "description": description,
            "criteria": criteria_list,
            "target_date": target_date,
            "status": status,
            "created_at": now,
        }

    def get_milestone(self, milestone_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve milestone by ID."""
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM milestones WHERE milestone_id = ?", (milestone_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["criteria"] = json.loads(data.pop("criteria_json", "[]") or "[]")
            return data

    def get_milestones(
        self,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all milestones for project."""
        pid = project_id or self.project_id
        query = "SELECT * FROM milestones WHERE project_id = ?"
        params: list[Any] = [pid]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"

        with self.connection() as conn:
            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["criteria"] = json.loads(d.pop("criteria_json", "[]") or "[]")
                results.append(d)
            return results

    def list_milestones(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Alias for get_milestones."""
        return self.get_milestones(*args, **kwargs)

    def get_active_milestone(self, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get the current in-progress or next pending milestone."""
        in_progress = self.get_milestones(project_id=project_id, status="in_progress")
        if in_progress:
            return in_progress[0]
        pending = self.get_milestones(project_id=project_id, status="pending")
        if pending:
            return pending[0]
        return None

    def update_milestone(
        self,
        milestone_id: str,
        status: Optional[str] = None,
        achieved: Optional[bool] = None,
        criteria: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update milestone status or criteria."""
        now = _now_iso()
        new_status = status
        if achieved is True:
            new_status = "achieved"

        updates = ["updated_at = ?"]
        params: list[Any] = [now]
        if new_status:
            updates.append("status = ?")
            params.append(new_status)
            if new_status == "achieved":
                updates.append("achieved_at = ?")
                params.append(now)
        if criteria is not None:
            updates.append("criteria_json = ?")
            params.append(json.dumps(criteria, ensure_ascii=False))

        params.append(milestone_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE milestones SET {', '.join(updates)} WHERE milestone_id = ?", params)

        res = self.get_milestone(milestone_id)
        return res or {"milestone_id": milestone_id, "status": new_status}

    def update_milestone_status(self, *args, **kwargs) -> Dict[str, Any]:
        """Alias for update_milestone."""
        return self.update_milestone(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Operational Agent Status
    # -------------------------------------------------------------------------
    def update_agent_status(
        self,
        agent_role: str,
        status: str,
        current_task_id: Optional[str] = None,
        last_error: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update operational status for one of the 8 canonical agents."""
        try:
            canonical_role = normalize_role(agent_role)
        except KeyError:
            canonical_role = agent_role

        if status not in ("idle", "active", "busy", "blocked", "completed", "error"):
            raise ValueError(f"Invalid agent status '{status}'.")

        pid = project_id or self.project_id
        now = _now_iso()

        with self.connection() as conn:
            if current_task_id:
                task_check = conn.execute("SELECT 1 FROM task_history WHERE task_id = ?", (current_task_id,)).fetchone()
                if not task_check:
                    run_row = conn.execute("SELECT run_id FROM workflow_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT 1", (pid,)).fetchone()
                    rid = run_row["run_id"] if run_row else f"run_auto_{pid}"
                    if not run_row:
                        conn.execute(
                            "INSERT OR IGNORE INTO workflow_runs (run_id, project_id, workflow_type, input_prompt, status) VALUES (?, ?, 'general', 'Auto run', 'running')",
                            (rid, pid),
                        )
                    conn.execute(
                        "INSERT OR IGNORE INTO task_history (task_id, run_id, project_id, agent_role, task_name, description, status) VALUES (?, ?, ?, ?, ?, ?, 'in_progress')",
                        (current_task_id, rid, pid, canonical_role, current_task_id, current_task_id),
                    )

            cur = conn.execute("SELECT metadata_json FROM agent_status WHERE agent_role = ? AND project_id = ?", (canonical_role, pid))
            row = cur.fetchone()
            existing_meta = json.loads(row["metadata_json"] or "{}") if (row and row["metadata_json"]) else {}
            merged_meta = {**existing_meta, **(metrics or {})}
            meta_json = json.dumps(merged_meta, ensure_ascii=False)

            conn.execute(
                """
                INSERT INTO agent_status (agent_role, project_id, status, current_task_id, last_error, last_active_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_role, project_id) DO UPDATE SET
                    status = excluded.status,
                    current_task_id = excluded.current_task_id,
                    last_error = COALESCE(excluded.last_error, agent_status.last_error),
                    last_active_at = excluded.last_active_at,
                    metadata_json = excluded.metadata_json
                """,
                (canonical_role, pid, status, current_task_id, last_error, now, meta_json),
            )

        return self.get_agent_status(canonical_role, project_id=pid)

    def get_agent_status(self, agent_role: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieve agent operational status."""
        try:
            canonical_role = normalize_role(agent_role)
        except KeyError:
            canonical_role = agent_role

        pid = project_id or self.project_id
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM agent_status WHERE agent_role = ? AND project_id = ?", (canonical_role, pid))
            row = cur.fetchone()
            if not row:
                return {
                    "agent_role": canonical_role,
                    "project_id": pid,
                    "status": "idle",
                    "current_task_id": None,
                    "tasks_completed_count": 0,
                    "last_error": None,
                    "metadata": {},
                }
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
            return data

    def get_agent_state(self, role: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Alias for get_agent_status."""
        return self.get_agent_status(role, project_id=project_id)

    def get_all_agent_statuses(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve operational status for all 8 canonical agents."""
        pid = project_id or self.project_id
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM agent_status WHERE project_id = ? ORDER BY agent_role ASC", (pid,))
            rows = cur.fetchall()
            found = {row["agent_role"]: dict(row) for row in rows}

            results = []
            for role in CANONICAL_ROLES:
                if role in found:
                    d = found[role]
                    d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
                    results.append(d)
                else:
                    results.append({
                        "agent_role": role,
                        "project_id": pid,
                        "status": "idle",
                        "current_task_id": None,
                        "tasks_completed_count": 0,
                        "last_error": None,
                        "metadata": {},
                    })
            return results

    def list_agent_statuses(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Alias for get_all_agent_statuses."""
        return self.get_all_agent_statuses(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Execution Traces & Operational Events
    # -------------------------------------------------------------------------
    def _ensure_system_run(self, conn: sqlite3.Connection, now: str) -> None:
        """Guarantee that self.project_id and 'system_run' exist for FK compliance."""
        conn.execute(
            """
            INSERT OR IGNORE INTO projects (project_id, name, description, root_path, integrity_mode, status, created_at, updated_at, metadata_json)
            VALUES (?, ?, 'Auto-created operational workspace', ?, 'development', 'active', ?, ?, '{}')
            """,
            (self.project_id, self.project_id, str(self.workspace_root), now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO workflow_runs (run_id, project_id, workflow_type, trigger_source, input_prompt, status, started_at)
            VALUES ('system_run', ?, 'system', 'cli', 'System operational traces', 'running', ?)
            """,
            (self.project_id, now),
        )

    def record_trace(
        self,
        run_id: str,
        task_id: Optional[str],
        agent_role: str,
        event_type: str,
        payload: Any,
        tool_name: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        """Record thought, tool invocation, result, or approval trace."""
        trc_id = trace_id or f"trc_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        try:
            canonical_role = normalize_role(agent_role)
        except KeyError:
            canonical_role = agent_role

        payload_str = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)

        with self.connection() as conn:
            if run_id == "system_run":
                self._ensure_system_run(conn, now)

            conn.execute(
                """
                INSERT INTO execution_traces (trace_id, run_id, task_id, agent_role, event_type, tool_name, payload, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trc_id, run_id, task_id, canonical_role, event_type, tool_name, payload_str, now),
            )

        return trc_id

    def record_event(
        self,
        event_type: str,
        payload: Any,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> str:
        """Record general operational or audit event. Guarantees 'system_run' FK presence."""
        rid = run_id or "system_run"
        role = agent_role or "System"
        return self.record_trace(
            run_id=rid,
            task_id=task_id,
            agent_role=role,
            event_type=event_type,
            payload=payload,
            tool_name=tool_name,
        )

    def list_traces(
        self,
        run_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List execution traces ordered chronologically."""
        conditions = []
        params: list[Any] = []
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if agent_role:
            try:
                norm_role = normalize_role(agent_role)
            except KeyError:
                norm_role = agent_role
            conditions.append("agent_role = ?")
            params.append(norm_role)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM execution_traces {where_clause} ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            results = []
            for r in cur.fetchall():
                d = dict(r)
                if isinstance(d.get("payload"), str):
                    try:
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_traces(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Alias for list_traces."""
        return self.list_traces(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Dashboard Aggregation
    # -------------------------------------------------------------------------
    def get_system_dashboard_data(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate project context, 8 agent statuses, milestones, and decisions."""
        pid = project_id or self.project_id
        project = self.get_project(pid) or {
            "project_id": pid,
            "name": "Unknown Project",
            "status": "active",
            "root_path": str(self.workspace_root),
        }
        agents = self.get_all_agent_statuses(pid)
        active_ms = self.get_active_milestone(pid)
        all_ms = self.get_milestones(pid)
        decisions = self.get_decisions(pid, status="accepted", limit=5)
        runs = self.list_workflow_runs(pid, limit=5)

        total_completed = sum(a.get("tasks_completed_count", 0) for a in agents)
        active_errors = [a for a in agents if a.get("status") == "error"]

        return {
            "project": project,
            "agents": agents,
            "active_milestone": active_ms,
            "all_milestones": all_ms,
            "recent_decisions": decisions,
            "recent_runs": runs,
            "metrics": {
                "tasks_completed_total": total_completed,
                "error_agents_count": len(active_errors),
                "agents_total": len(CANONICAL_ROLES),
                "milestones_total": len(all_ms),
            },
        }

    # -------------------------------------------------------------------------
    # Token Ledger & Cost Metering
    # -------------------------------------------------------------------------
    def record_token_usage(
        self,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_role: str = "Developer",
        provider: str = "openrouter",
        model_name: str = "qwen/qwen-2.5-coder-32b-instruct",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        estimated_cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record token consumption and estimated cost for an LLM call into token_ledger."""
        from persistent_crewai.config import calculate_token_cost
        pid = project_id or self.project_id
        lid = f"tkn_{uuid.uuid4().hex[:12]}"
        t_tokens = total_tokens if total_tokens is not None else (prompt_tokens + completion_tokens)
        cost = estimated_cost_usd if estimated_cost_usd is not None else calculate_token_cost(model_name, prompt_tokens, completion_tokens)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO token_ledger (
                    ledger_id, project_id, run_id, task_id, agent_role, provider,
                    model_name, prompt_tokens, completion_tokens, total_tokens,
                    estimated_cost_usd, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lid, pid, run_id, task_id, agent_role, provider, model_name, prompt_tokens, completion_tokens, t_tokens, cost, meta_json)
            )

        return {
            "ledger_id": lid,
            "project_id": pid,
            "agent_role": agent_role,
            "provider": provider,
            "model_name": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": t_tokens,
            "estimated_cost_usd": cost,
        }

    def get_token_summary(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate token usage and cost accounting across providers, agents, and projects."""
        conditions = []
        params: List[Any] = []
        if project_id and project_id != "all":
            conditions.append("project_id = ?")
            params.append(project_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.connection() as conn:
            # Overall totals
            tot_row = conn.execute(
                f"""
                SELECT 
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt,
                    COALESCE(SUM(completion_tokens), 0) as total_completion,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost,
                    COUNT(*) as total_calls
                FROM token_ledger {where}
                """,
                params
            ).fetchone()

            # Group by provider
            prov_rows = conn.execute(
                f"""
                SELECT 
                    provider,
                    SUM(total_tokens) as tokens,
                    SUM(estimated_cost_usd) as cost,
                    COUNT(*) as calls
                FROM token_ledger {where}
                GROUP BY provider
                """,
                params
            ).fetchall()

            # Group by agent
            agent_rows = conn.execute(
                f"""
                SELECT 
                    agent_role,
                    SUM(total_tokens) as tokens,
                    SUM(estimated_cost_usd) as cost,
                    COUNT(*) as calls
                FROM token_ledger {where}
                GROUP BY agent_role
                ORDER BY tokens DESC
                """,
                params
            ).fetchall()

            # Recent invocations
            recent_rows = conn.execute(
                f"""
                SELECT 
                    ledger_id, agent_role, provider, model_name, total_tokens, estimated_cost_usd, timestamp
                FROM token_ledger {where}
                ORDER BY timestamp DESC LIMIT 10
                """,
                params
            ).fetchall()

        by_provider = {r["provider"]: {"tokens": r["tokens"], "cost": round(r["cost"], 4), "calls": r["calls"]} for r in prov_rows}
        by_agent = {r["agent_role"]: {"tokens": r["tokens"], "cost": round(r["cost"], 4), "calls": r["calls"]} for r in agent_rows}

        return {
            "total_tokens": tot_row["total_tokens"] if tot_row else 0,
            "total_prompt_tokens": tot_row["total_prompt"] if tot_row else 0,
            "total_completion_tokens": tot_row["total_completion"] if tot_row else 0,
            "total_cost_usd": round(tot_row["total_cost"], 4) if tot_row else 0.0,
            "total_calls": tot_row["total_calls"] if tot_row else 0,
            "by_provider": by_provider,
            "by_agent": by_agent,
            "recent_calls": [dict(r) for r in recent_rows],
        }
