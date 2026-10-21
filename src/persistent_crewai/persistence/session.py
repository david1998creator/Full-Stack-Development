"""
Session resumption, context rehydration, crash recovery, and state export/import
for Persistent CrewAI.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.config import (
    CURRENT_SCHEMA_VERSION,
    CANONICAL_ROLES,
    get_default_db_path,
)
from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.state_manager import (
    PersistenceError,
    ProjectNotFoundError,
    StateManager,
    _now_iso,
)


class SessionNotFoundError(PersistenceError):
    """Raised when attempting to resume a nonexistent session."""
    pass


class StateImportConflictError(PersistenceError, ValueError):
    """Raised when importing into an existing project without overwrite=True."""
    pass


class SchemaVersionMismatchError(PersistenceError, ValueError):
    """Raised when import JSON has an incompatible schema version."""
    pass


class StateIntegrityError(PersistenceError, ValueError):
    """Raised when state backup JSON is corrupted or violates schema constraints."""
    pass


@dataclass
class WorkflowResumePlan:
    """Plan for resuming an interrupted or paused workflow run."""
    run_id: str
    workflow_type: str
    completed_tasks: List[Dict[str, Any]]
    interrupted_tasks: List[Dict[str, Any]]
    pending_tasks: List[Dict[str, Any]]
    next_task: Optional[Dict[str, Any]]
    recommended_action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MilestoneResumePlan:
    """Plan for resuming progress on a project milestone."""
    milestone: Dict[str, Any]
    completed_criteria: List[str]
    unfulfilled_criteria: List[str]
    suggested_role: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResumptionContext:
    """Rehydrated session context for grounding downstream agents."""
    project: Dict[str, Any]
    active_milestone: Optional[Dict[str, Any]]
    active_milestones: List[Dict[str, Any]]
    recent_decisions: List[Dict[str, Any]]
    agent_states: Dict[str, Dict[str, Any]]
    interrupted_tasks: List[Dict[str, Any]]
    pending_tasks: List[Dict[str, Any]]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SessionResumptionManager:
    """
    Coordinates session state detection, crash recovery, context rehydration,
    resumption planning, and portable JSON export/import.
    """

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        project_id: Optional[str] = None,
    ) -> None:
        self.db_path = Path(db_path).resolve() if db_path and str(db_path) != ":memory:" else (Path(":memory:") if str(db_path) == ":memory:" else get_default_db_path())
        self.project_id = project_id or "default_project"
        self._db_mgr = DatabaseManager(db_path=self.db_path)
        self._db_mgr.initialize_schema()
        self._state_mgr = StateManager(db_path=self.db_path, project_id=self.project_id)
        self._lock = threading.RLock()

    def detect_existing_session(self) -> bool:
        """Detect whether a valid existing project session is stored on disk."""
        with self._db_mgr.connection() as conn:
            cur = conn.execute(
                "SELECT count(*) as cnt FROM projects WHERE status != 'archived'"
            )
            row = cur.fetchone()
            return (row["cnt"] > 0) if row else False

    def inspect_state(self, auto_recover: bool = True) -> Dict[str, Any]:
        """
        Inspect the database for crash remnants (in_progress tasks, busy agents).
        Automatically recovers by marking tasks as interrupted and resetting busy agents to idle.
        """
        now = _now_iso()
        with self._lock:
            with self._db_mgr.connection() as conn:
                # Find in_progress tasks or tasks associated with busy/active agents
                cur = conn.execute(
                    """
                    SELECT DISTINCT t.* FROM task_history t
                    WHERE t.project_id = ? AND t.status != 'completed' AND (
                        t.status = 'in_progress'
                        OR t.task_id IN (
                            SELECT current_task_id FROM agent_status
                            WHERE project_id = ? AND status IN ('busy', 'active') AND current_task_id IS NOT NULL
                        )
                    )
                    """,
                    (self.project_id, self.project_id),
                )
                in_prog_tasks = [dict(r) for r in cur.fetchall()]

                # Find running workflow runs
                cur = conn.execute(
                    "SELECT * FROM workflow_runs WHERE project_id = ? AND status = 'running'",
                    (self.project_id,),
                )
                running_runs = [dict(r) for r in cur.fetchall()]

                # Find busy/active agents
                cur = conn.execute(
                    "SELECT * FROM agent_status WHERE project_id = ? AND status IN ('busy', 'active')",
                    (self.project_id,),
                )
                busy_agents = [dict(r) for r in cur.fetchall()]

                recovered_tasks: list[dict[str, Any]] = []
                if auto_recover:
                    # Transition tasks to interrupted
                    if in_prog_tasks:
                        task_ids = [t["task_id"] for t in in_prog_tasks]
                        placeholders = ", ".join("?" for _ in task_ids)
                        conn.execute(
                            f"""
                            UPDATE task_history
                            SET status = 'interrupted',
                                error_details = 'Session interrupted: process terminated during execution',
                                completed_at = ?
                            WHERE project_id = ? AND task_id IN ({placeholders})
                            """,
                            [now, self.project_id, *task_ids],
                        )
                        for t in in_prog_tasks:
                            t_copy = dict(t)
                            t_copy["status"] = "interrupted"
                            recovered_tasks.append(t_copy)

                    # Reset busy agents to idle
                    if busy_agents:
                        conn.execute(
                            """
                            UPDATE agent_status
                            SET status = 'idle',
                                current_task_id = NULL,
                                last_active_at = ?
                            WHERE project_id = ? AND status IN ('busy', 'active')
                            """,
                            (now, self.project_id),
                        )

                    # Transition running runs to interrupted
                    if running_runs:
                        conn.execute(
                            """
                            UPDATE workflow_runs
                            SET status = 'interrupted',
                                error_message = 'Workflow interrupted due to abnormal termination',
                                completed_at = ?
                            WHERE project_id = ? AND status = 'running'
                            """,
                            (now, self.project_id),
                        )

        return {
            "interrupted_tasks": recovered_tasks or in_prog_tasks,
            "recovered_agents": [a["agent_role"] for a in busy_agents],
            "running_runs_count": len(running_runs),
            "recovered_at": now if auto_recover else None,
        }

    def get_resumption_context(self, auto_recover: bool = True) -> Dict[str, Any]:
        """
        Extract complete project context for rehydration into downstream agent prompts.
        """
        if auto_recover:
            self.inspect_state(auto_recover=True)

        project = self._state_mgr.get_project(self.project_id)
        if not project:
            # Fall back to latest active project
            with self._db_mgr.connection() as conn:
                cur = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    project = dict(row)
                    self.project_id = project["project_id"]
                    self._state_mgr.project_id = self.project_id
                else:
                    project = {
                        "project_id": self.project_id,
                        "name": "Default Project",
                        "status": "active",
                    }

        milestones = self._state_mgr.get_milestones(self.project_id)
        active_milestones = [m for m in milestones if m.get("status") in ("in_progress", "pending")]
        active_milestone = active_milestones[0] if active_milestones else None

        decisions = self._state_mgr.get_decisions(self.project_id, status="accepted", limit=10)
        agents = self._state_mgr.get_all_agent_statuses(self.project_id)
        agent_dict = {a["agent_role"]: a for a in agents}

        tasks_interrupted = self._state_mgr.get_task_history(project_id=self.project_id, status="interrupted")
        tasks_pending = self._state_mgr.get_task_history(project_id=self.project_id, status="pending")

        summary = (
            f"Project '{project.get('name', self.project_id)}' (Status: {project.get('status', 'active')}). "
            f"Active Milestone: {active_milestone.get('title') if active_milestone else 'None'}. "
            f"Decisions Recorded: {len(decisions)}. "
            f"Interrupted Tasks: {len(tasks_interrupted)}. Pending Tasks: {len(tasks_pending)}."
        )

        return {
            "project": project,
            "active_milestone": active_milestone,
            "active_milestones": active_milestones or milestones,
            "all_milestones": milestones,
            "recent_decisions": decisions,
            "agent_states": agent_dict,
            "interrupted_tasks": tasks_interrupted,
            "pending_tasks": tasks_pending,
            "summary": summary,
        }

    def rehydrate_session(self, project_id: Optional[str] = None, auto_recover: bool = True) -> ResumptionContext:
        """Type-safe wrapper returning ResumptionContext."""
        if project_id:
            self.project_id = project_id
            self._state_mgr.project_id = project_id

        ctx = self.get_resumption_context(auto_recover=auto_recover)
        return ResumptionContext(
            project=ctx["project"],
            active_milestone=ctx["active_milestone"],
            active_milestones=ctx["active_milestones"],
            recent_decisions=ctx["recent_decisions"],
            agent_states=ctx["agent_states"],
            interrupted_tasks=ctx["interrupted_tasks"],
            pending_tasks=ctx["pending_tasks"],
            summary=ctx["summary"],
        )

    def resume_workflow(self, run_id: str) -> WorkflowResumePlan:
        """
        Analyze run state and build resumption plan.
        Transitions workflow run back to 'running'.
        """
        run = self._state_mgr.get_workflow_run(run_id)
        if not run:
            raise PersistenceError(f"Workflow run '{run_id}' not found.")

        tasks = self._state_mgr.get_task_history(run_id=run_id)
        completed = [t for t in tasks if t.get("status") == "completed"]
        interrupted = [t for t in tasks if t.get("status") == "interrupted"]
        pending = [t for t in tasks if t.get("status") == "pending"]

        if interrupted:
            next_task = interrupted[0]
            recommended = "retry_interrupted"
        elif pending:
            next_task = pending[0]
            recommended = "continue_pending"
        else:
            next_task = None
            recommended = "all_completed"

        self._state_mgr.update_workflow_run(run_id, status="running")

        return WorkflowResumePlan(
            run_id=run_id,
            workflow_type=run.get("workflow_type", "feature_development"),
            completed_tasks=completed,
            interrupted_tasks=interrupted,
            pending_tasks=pending,
            next_task=next_task,
            recommended_action=recommended,
        )

    def resume_milestone(self, milestone_id: Optional[str] = None) -> MilestoneResumePlan:
        """Analyze milestone criteria fulfillment and suggest next responsible agent role."""
        if milestone_id:
            ms = self._state_mgr.get_milestone(milestone_id)
        else:
            ms = self._state_mgr.get_active_milestone(self.project_id)

        if not ms:
            raise PersistenceError("No active milestone found to resume.")

        criteria = ms.get("criteria", [])
        tasks = self._state_mgr.get_task_history(project_id=self.project_id, status="completed")
        task_outputs = " ".join((t.get("actual_output") or "") for t in tasks).lower()

        completed_crit: list[str] = []
        unfulfilled_crit: list[str] = []

        for c in criteria:
            c_low = c.lower()
            tokens = [w for w in c_low.split() if len(w) > 3]
            if tokens and any(tok in task_outputs for tok in tokens):
                completed_crit.append(c)
            else:
                unfulfilled_crit.append(c)

        # Suggest next role based on remaining criteria
        unfulfilled_str = " ".join(unfulfilled_crit).lower()
        if "adr" in unfulfilled_str or "architect" in unfulfilled_str or "schema" in unfulfilled_str:
            suggested = "Architect"
        elif "design" in unfulfilled_str or "ui" in unfulfilled_str or "ux" in unfulfilled_str:
            suggested = "Designer"
        elif "code" in unfulfilled_str or "implement" in unfulfilled_str or "develop" in unfulfilled_str:
            suggested = "Developer"
        elif "test" in unfulfilled_str or "verify" in unfulfilled_str or "audit" in unfulfilled_str:
            suggested = "Tester"
        elif "review" in unfulfilled_str or "security" in unfulfilled_str:
            suggested = "Reviewer"
        elif "pr" in unfulfilled_str or "branch" in unfulfilled_str or "merge" in unfulfilled_str or "issue" in unfulfilled_str:
            suggested = "GitHub Manager"
        else:
            suggested = "Coordinator"

        return MilestoneResumePlan(
            milestone=ms,
            completed_criteria=completed_crit,
            unfulfilled_criteria=unfulfilled_crit,
            suggested_role=suggested,
        )

    # -------------------------------------------------------------------------
    # State Export and Import
    # -------------------------------------------------------------------------
    def export_state(
        self,
        target_path: str | Path,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export complete relational database state into portable JSON format.
        """
        pid = project_id or self.project_id
        target = Path(target_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            with self._db_mgr.connection() as conn:
                # 1. Projects
                if pid:
                    cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (pid,))
                else:
                    cur = conn.execute("SELECT * FROM projects")
                projects = [dict(r) for r in cur.fetchall()]

                # 2. Decisions
                cur = conn.execute("SELECT * FROM decisions WHERE project_id = ?", (pid,))
                decisions = [dict(r) for r in cur.fetchall()]

                # 3. Workflow Runs
                cur = conn.execute("SELECT * FROM workflow_runs WHERE project_id = ?", (pid,))
                runs = [dict(r) for r in cur.fetchall()]

                # 4. Task History
                cur = conn.execute("SELECT * FROM task_history WHERE project_id = ?", (pid,))
                tasks = [dict(r) for r in cur.fetchall()]

                # 5. Milestones
                cur = conn.execute("SELECT * FROM milestones WHERE project_id = ?", (pid,))
                milestones = [dict(r) for r in cur.fetchall()]

                # 6. Agent Status
                cur = conn.execute("SELECT * FROM agent_status WHERE project_id = ?", (pid,))
                agent_statuses = [dict(r) for r in cur.fetchall()]

                # 7. Execution Traces (join workflow_runs to filter by project)
                cur = conn.execute(
                    """
                    SELECT t.* FROM execution_traces t
                    JOIN workflow_runs r ON t.run_id = r.run_id
                    WHERE r.project_id = ?
                    """,
                    (pid,),
                )
                traces = [dict(r) for r in cur.fetchall()]

                # 8. Memory Records
                cur = conn.execute("SELECT * FROM memory_records WHERE project_id = ?", (pid,))
                memories = [dict(r) for r in cur.fetchall()]

                # 9. Schema Migrations
                cur = conn.execute("SELECT * FROM schema_migrations")
                migrations = [dict(r) for r in cur.fetchall()]

        payload: Dict[str, Any] = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "project_id": pid,
            "projects": projects,
            "decisions": decisions,
            "workflow_runs": runs,
            "task_history": tasks,
            "milestones": milestones,
            "agent_status": agent_statuses,
            "execution_traces": traces,
            "memory_records": memories,
            "schema_migrations": migrations,
        }

        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        summary = {
            "export_file": str(target),
            "project_id": pid,
            "projects_count": len(projects),
            "decisions_count": len(decisions),
            "workflow_runs_count": len(runs),
            "tasks_count": len(tasks),
            "milestones_count": len(milestones),
            "agent_status_count": len(agent_statuses),
            "traces_count": len(traces),
            "memories_count": len(memories),
        }
        return summary

    def import_state(
        self,
        source_path: str | Path,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Atomically import state from JSON backup.
        Validates schema version, checks conflicts, and populates tables.
        """
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"State backup file '{source}' does not exist.")

        try:
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise StateIntegrityError(f"Malformed or corrupted state backup JSON: {e}")

        # Validate schema version
        ver = data.get("schema_version")
        if ver != CURRENT_SCHEMA_VERSION:
            raise SchemaVersionMismatchError(
                f"Schema version mismatch: backup has '{ver}', expected '{CURRENT_SCHEMA_VERSION}'."
            )

        # Validate project presence
        projects_data = data.get("projects") or ([data["project"]] if "project" in data else [])
        if not projects_data:
            raise StateIntegrityError("No project records found in state backup.")

        # Determine target project ID mapping
        source_pid = data.get("project_id") or projects_data[0]["project_id"]
        target_pid = self.project_id if (self.project_id and self.project_id != "default_project") else source_pid

        with self._lock:
            with self._db_mgr.connection() as conn:
                # Defer foreign key evaluation to commit time for arbitrary topological graph ordering
                conn.execute("PRAGMA defer_foreign_keys = ON;")

                # Check for existing project conflict
                cur = conn.execute("SELECT project_id FROM projects WHERE project_id = ?", (target_pid,))
                existing = cur.fetchone()

                if existing and not overwrite:
                    raise StateImportConflictError(
                        f"Project '{target_pid}' already exists in database. Pass overwrite=True to replace."
                    )

                # If overwriting, remove existing project and cascade children
                if existing and overwrite:
                    conn.execute("DELETE FROM projects WHERE project_id = ?", (target_pid,))

                # 1. Schema migrations
                for mig in data.get("schema_migrations", []):
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
                        (mig["version"], mig.get("description", "Imported migration")),
                    )

                # 2. Projects
                for p in projects_data:
                    p_id = target_pid if p["project_id"] == source_pid else p["project_id"]
                    conn.execute(
                        """
                        INSERT INTO projects (project_id, name, description, root_path, integrity_mode, status, created_at, updated_at, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_id) DO UPDATE SET
                            name = excluded.name,
                            description = excluded.description,
                            root_path = excluded.root_path,
                            integrity_mode = excluded.integrity_mode,
                            status = excluded.status,
                            updated_at = excluded.updated_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            p_id,
                            p["name"],
                            p["description"],
                            p["root_path"],
                            p.get("integrity_mode", "development"),
                            p.get("status", "active"),
                            p.get("created_at", _now_iso()),
                            p.get("updated_at", _now_iso()),
                            p.get("metadata_json") or json.dumps(p.get("metadata", {}), ensure_ascii=False),
                        ),
                    )

                # 3. Decisions
                # Pass 1: Insert all decisions with superseded_by = NULL to eliminate forward-reference FK constraints
                for d in data.get("decisions", []):
                    p_id = target_pid if d["project_id"] == source_pid else d["project_id"]
                    tags_val = d.get("tags")
                    tags_str = tags_val if isinstance(tags_val, str) else json.dumps(tags_val or [], ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO decisions (decision_id, project_id, agent_role, title, rationale, implications, status, tags, superseded_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        ON CONFLICT(decision_id) DO UPDATE SET
                            project_id = excluded.project_id,
                            agent_role = excluded.agent_role,
                            title = excluded.title,
                            rationale = excluded.rationale,
                            implications = excluded.implications,
                            status = excluded.status,
                            tags = excluded.tags,
                            superseded_by = excluded.superseded_by,
                            updated_at = excluded.updated_at
                        """,
                        (
                            d["decision_id"],
                            p_id,
                            d["agent_role"],
                            d["title"],
                            d["rationale"],
                            d.get("implications", ""),
                            d.get("status", "accepted"),
                            tags_str,
                            d.get("created_at", _now_iso()),
                            d.get("updated_at", _now_iso()),
                        ),
                    )

                # Pass 2: Link superseded_by references once all target decisions exist in table
                for d in data.get("decisions", []):
                    superseded = d.get("superseded_by")
                    if superseded:
                        conn.execute(
                            "UPDATE decisions SET superseded_by = ? WHERE decision_id = ?",
                            (superseded, d["decision_id"]),
                        )

                # 4. Workflow Runs
                for r in data.get("workflow_runs", []):
                    p_id = target_pid if r["project_id"] == source_pid else r["project_id"]
                    conn.execute(
                        """
                        INSERT INTO workflow_runs (run_id, project_id, workflow_type, trigger_source, input_prompt, final_output, status, error_message, started_at, completed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            project_id = excluded.project_id,
                            workflow_type = excluded.workflow_type,
                            input_prompt = excluded.input_prompt,
                            final_output = excluded.final_output,
                            status = excluded.status,
                            error_message = excluded.error_message,
                            completed_at = excluded.completed_at
                        """,
                        (
                            r["run_id"],
                            p_id,
                            r["workflow_type"],
                            r.get("trigger_source", "cli"),
                            r.get("input_prompt", ""),
                            r.get("final_output"),
                            r.get("status", "completed"),
                            r.get("error_message"),
                            r.get("started_at", _now_iso()),
                            r.get("completed_at"),
                        ),
                    )

                # 5. Task History
                for t in data.get("task_history", []):
                    p_id = target_pid if t["project_id"] == source_pid else t["project_id"]
                    meta_val = t.get("metadata_json") or json.dumps(t.get("metadata", {}), ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO task_history (task_id, run_id, project_id, agent_role, task_name, description, expected_output, actual_output, status, execution_order, error_details, started_at, completed_at, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(task_id) DO UPDATE SET
                            actual_output = excluded.actual_output,
                            status = excluded.status,
                            completed_at = excluded.completed_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            t["task_id"],
                            t["run_id"],
                            p_id,
                            t["agent_role"],
                            t["task_name"],
                            t.get("description", ""),
                            t.get("expected_output"),
                            t.get("actual_output"),
                            t.get("status", "completed"),
                            t.get("execution_order", 0),
                            t.get("error_details"),
                            t.get("started_at"),
                            t.get("completed_at"),
                            meta_val,
                        ),
                    )

                # 6. Milestones
                for m in data.get("milestones", []):
                    p_id = target_pid if m["project_id"] == source_pid else m["project_id"]
                    crit_val = m.get("criteria_json")
                    crit_str = crit_val if isinstance(crit_val, str) else json.dumps(m.get("criteria", []), ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO milestones (milestone_id, project_id, title, description, criteria_json, target_date, status, achieved_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(milestone_id) DO UPDATE SET
                            title = excluded.title,
                            description = excluded.description,
                            criteria_json = excluded.criteria_json,
                            status = excluded.status,
                            achieved_at = excluded.achieved_at,
                            updated_at = excluded.updated_at
                        """,
                        (
                            m["milestone_id"],
                            p_id,
                            m["title"],
                            m.get("description", ""),
                            crit_str,
                            m.get("target_date"),
                            m.get("status", "pending"),
                            m.get("achieved_at"),
                            m.get("created_at", _now_iso()),
                            m.get("updated_at", _now_iso()),
                        ),
                    )

                # 7. Agent Status
                for a in data.get("agent_status", []):
                    p_id = target_pid if a["project_id"] == source_pid else a["project_id"]
                    meta_val = a.get("metadata_json") or json.dumps(a.get("metadata", {}), ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO agent_status (agent_role, project_id, status, current_task_id, last_active_at, tasks_completed_count, last_error, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(agent_role, project_id) DO UPDATE SET
                            status = excluded.status,
                            current_task_id = excluded.current_task_id,
                            last_active_at = excluded.last_active_at,
                            tasks_completed_count = excluded.tasks_completed_count,
                            last_error = excluded.last_error,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            a["agent_role"],
                            p_id,
                            a.get("status", "idle"),
                            a.get("current_task_id"),
                            a.get("last_active_at", _now_iso()),
                            a.get("tasks_completed_count", 0),
                            a.get("last_error"),
                            meta_val,
                        ),
                    )

                # 8. Execution Traces
                for tr in data.get("execution_traces", []):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO execution_traces (trace_id, run_id, task_id, agent_role, event_type, tool_name, payload, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tr["trace_id"],
                            tr["run_id"],
                            tr.get("task_id"),
                            tr["agent_role"],
                            tr["event_type"],
                            tr.get("tool_name"),
                            tr["payload"],
                            tr.get("timestamp", _now_iso()),
                        ),
                    )

                # 9. Memory Records
                for mem in data.get("memory_records", []):
                    p_id = target_pid if mem["project_id"] == source_pid else mem["project_id"]
                    conn.execute(
                        """
                        INSERT INTO memory_records (record_id, project_id, agent_role, category, content, metadata_json, embedding_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(record_id) DO UPDATE SET
                            content = excluded.content,
                            metadata_json = excluded.metadata_json,
                            embedding_json = excluded.embedding_json
                        """,
                        (
                            mem["record_id"],
                            p_id,
                            mem.get("agent_role"),
                            mem.get("category", "general"),
                            mem["content"],
                            mem.get("metadata_json", "{}"),
                            mem.get("embedding_json"),
                            mem.get("created_at", _now_iso()),
                        ),
                    )

        return {
            "source_file": str(source),
            "target_project_id": target_pid,
            "projects_imported": len(projects_data),
            "decisions_imported": len(data.get("decisions", [])),
            "tasks_imported": len(data.get("task_history", [])),
            "milestones_imported": len(data.get("milestones", [])),
            "memories_imported": len(data.get("memory_records", [])),
        }
