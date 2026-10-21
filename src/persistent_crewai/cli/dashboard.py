"""
Operational status dashboard and real-time execution trace callbacks for Persistent CrewAI.
Provides state aggregation across SQLite persistence, Rich-formatted terminal rendering
with graceful plain-text ASCII fallback, and execution trace streaming with audit persistence.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from persistent_crewai.config import CANONICAL_ROLES, normalize_role
from persistent_crewai.persistence.state_manager import StateManager

logger = logging.getLogger(__name__)

# Attempt to load rich components; fallback gracefully to plain ASCII if unavailable
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = Any  # type: ignore
    Table = Any    # type: ignore
    Panel = Any    # type: ignore
    box = Any      # type: ignore


def get_dashboard_data(
    state_manager: Optional[StateManager] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aggregate project metadata, operational status for all 8 canonical agents,
    milestones, architectural decisions, and recent workflow execution runs.

    Args:
        state_manager: Optional StateManager instance (defaults to default StateManager).
        project_id: Optional project identifier filter.

    Returns:
        Structured dictionary guaranteed to contain 'project', 'agents' (len == 8),
        'decisions', 'milestones', and 'metrics'.
    """
    sm = state_manager or StateManager(project_id=project_id)
    pid = project_id or getattr(sm, "project_id", "default_project")

    raw_data: Dict[str, Any] = {}
    if hasattr(sm, "get_system_dashboard_data"):
        try:
            raw_data = sm.get_system_dashboard_data(pid)
        except Exception as e:
            logger.warning("Failed to invoke sm.get_system_dashboard_data: %s", e)

    # 1. Project metadata
    project = raw_data.get("project")
    if not project or not isinstance(project, dict):
        if hasattr(sm, "get_project"):
            project = sm.get_project(pid)
        if not project:
            project = {
                "project_id": pid,
                "name": f"Project {pid}",
                "status": "active",
                "integrity_mode": getattr(sm, "integrity_mode", "development"),
                "root_path": str(getattr(sm, "workspace_root", "")),
            }

    # 2. Guarantees all 8 canonical agents in canonical order
    raw_agents = raw_data.get("agents")
    if raw_agents is None and hasattr(sm, "get_all_agent_statuses"):
        try:
            raw_agents = sm.get_all_agent_statuses(pid)
        except Exception as e:
            logger.warning("Failed to fetch agent statuses: %s", e)
            raw_agents = []

    raw_agents_list = raw_agents if isinstance(raw_agents, list) else []
    agents_by_role: Dict[str, Dict[str, Any]] = {}
    for a in raw_agents_list:
        if isinstance(a, dict) and "agent_role" in a:
            agents_by_role[a["agent_role"]] = a

    normalized_agents: List[Dict[str, Any]] = []
    for role in CANONICAL_ROLES:
        if role in agents_by_role:
            entry = dict(agents_by_role[role])
        else:
            entry = {
                "agent_role": role,
                "status": "idle",
                "current_task_id": None,
                "tasks_completed_count": 0,
                "last_active_at": None,
                "last_error": None,
                "metadata": {},
            }
        normalized_agents.append(entry)

    # 3. Decisions
    decisions = (
        raw_data.get("recent_decisions")
        or raw_data.get("decisions")
        or (sm.get_decisions(pid, limit=10) if hasattr(sm, "get_decisions") else [])
    )
    if not isinstance(decisions, list):
        decisions = []

    # 4. Milestones
    milestones = (
        raw_data.get("all_milestones")
        or raw_data.get("milestones")
        or (sm.get_milestones(pid) if hasattr(sm, "get_milestones") else [])
    )
    if not isinstance(milestones, list):
        milestones = []

    active_ms = raw_data.get("active_milestone")
    if not active_ms and hasattr(sm, "get_active_milestone"):
        try:
            active_ms = sm.get_active_milestone(pid)
        except Exception:
            active_ms = None

    # 5. Runs
    runs = (
        raw_data.get("recent_runs")
        or raw_data.get("runs")
        or (sm.list_workflow_runs(pid, limit=5) if hasattr(sm, "list_workflow_runs") else [])
    )
    if not isinstance(runs, list):
        runs = []

    # 6. Metrics
    tasks_completed_total = sum(int(a.get("tasks_completed_count") or 0) for a in normalized_agents)
    error_agents_count = sum(1 for a in normalized_agents if a.get("status") == "error")

    metrics = raw_data.get("metrics") or {
        "tasks_completed_total": tasks_completed_total,
        "error_agents_count": error_agents_count,
        "agents_total": len(CANONICAL_ROLES),
        "milestones_total": len(milestones),
    }

    return {
        "project": project,
        "agents": normalized_agents,
        "decisions": decisions,
        "recent_decisions": decisions,
        "milestones": milestones,
        "all_milestones": milestones,
        "active_milestone": active_ms,
        "runs": runs,
        "recent_runs": runs,
        "metrics": metrics,
    }


# Contract alias
get_system_dashboard_data = get_dashboard_data


def _render_dashboard_plain_ascii(data: Dict[str, Any]) -> str:
    """
    Render a clean ASCII-formatted dashboard when Rich is absent or plain output is forced.
    Immune to Windows cp1252 charmap encoding errors.
    """
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("                 PERSISTENT CREWAI SYSTEM DASHBOARD")
    lines.append("=" * 80)

    # Project Section
    proj = data.get("project", {})
    p_name = proj.get("name", "Unknown Project")
    p_id = proj.get("project_id", "N/A")
    p_stat = proj.get("status", "active")
    p_mode = proj.get("integrity_mode", "development")
    p_root = proj.get("root_path", "N/A")

    lines.append(f"Project: {p_name} (ID: {p_id}) | Status: {p_stat} | Mode: {p_mode}")
    lines.append(f"Root: {p_root}")
    lines.append("-" * 80)

    # Milestones Section
    lines.append("[MILESTONES]")
    milestones = data.get("milestones", [])
    if milestones:
        lines.append(f"{'ID':<12} {'Status':<14} {'Title'}")
        lines.append("-" * 60)
        for m in milestones:
            mid = str(m.get("milestone_id", ""))[:11]
            stat = str(m.get("status", ""))[:13]
            title = str(m.get("title", ""))
            lines.append(f"{mid:<12} {stat:<14} {title}")
    else:
        lines.append("  No milestones recorded.")
    lines.append("-" * 80)

    # 8-Agent Operational Team Section
    lines.append("[8-AGENT OPERATIONAL TEAM]")
    lines.append(f"{'Role':<26} {'Status':<10} {'Completed':<10} {'Current Task'}")
    lines.append("-" * 75)
    for a in data.get("agents", []):
        role = str(a.get("agent_role", ""))[:25]
        status = str(a.get("status", "idle"))[:9]
        completed = str(a.get("tasks_completed_count", 0))[:9]
        cur_task = str(a.get("current_task_id") or "-")[:25]
        lines.append(f"{role:<26} {status:<10} {completed:<10} {cur_task}")
    lines.append("-" * 80)

    # Architectural Decisions Section
    lines.append("[RECENT ARCHITECTURAL DECISIONS]")
    decisions = data.get("decisions", [])
    if decisions:
        lines.append(f"{'ID':<10} {'Agent':<20} {'Status':<10} {'Title'}")
        lines.append("-" * 70)
        for d in decisions[:5]:
            did = str(d.get("decision_id", ""))[:9]
            agent = str(d.get("agent_role", ""))[:19]
            d_stat = str(d.get("status", "accepted"))[:9]
            d_title = str(d.get("title", ""))[:38]
            lines.append(f"{did:<10} {agent:<20} {d_stat:<10} {d_title}")
    else:
        lines.append("  No architectural decisions recorded.")
    lines.append("-" * 80)

    # Workflow Runs Section
    runs = data.get("runs", [])
    if runs:
        lines.append("[RECENT WORKFLOW RUNS]")
        lines.append(f"{'Run ID':<22} {'Type':<22} {'Status':<12} {'Started At'}")
        lines.append("-" * 75)
        for r in runs[:5]:
            rid = str(r.get("run_id", ""))[:21]
            wtype = str(r.get("workflow_type", ""))[:21]
            r_stat = str(r.get("status", ""))[:11]
            started = str(r.get("started_at", ""))[:19]
            lines.append(f"{rid:<22} {wtype:<22} {r_stat:<12} {started}")
        lines.append("-" * 80)

    # Metrics Section
    metrics = data.get("metrics", {})
    t_comp = metrics.get("tasks_completed_total", 0)
    e_cnt = metrics.get("error_agents_count", 0)
    lines.append(f"Metrics: Completed Tasks: {t_comp} | Agent Errors: {e_cnt}")
    lines.append("=" * 80)

    return "\n".join(lines)


def render_dashboard(
    data_or_state_manager: Union[Dict[str, Any], StateManager],
    console: Optional[Any] = None,
    force_plain: bool = False,
) -> str:
    """
    Render terminal dashboard for Persistent CrewAI.
    Accepts either a pre-aggregated data dictionary or a StateManager instance.
    Uses Rich with ASCII box formatting for terminal beauty and cp1252 resilience,
    falling back to clean plain-text ASCII if Rich is unavailable or errors occur.

    Args:
        data_or_state_manager: Pre-aggregated dictionary or StateManager.
        console: Optional existing Rich Console instance.
        force_plain: If True, forces clean plain-text ASCII output.

    Returns:
        Formatted dashboard string containing 'DASHBOARD' and 'Project'.
    """
    # 1. Normalize input
    if isinstance(data_or_state_manager, dict):
        data = dict(data_or_state_manager)
        if (
            "project" not in data
            or "agents" not in data
            or "decisions" not in data
            or "milestones" not in data
            or len(data.get("agents", [])) != 8
        ):
            # Normalize missing fields
            data = get_dashboard_data(
                project_id=data.get("project", {}).get("project_id") if isinstance(data.get("project"), dict) else None
            )
    else:
        data = get_dashboard_data(state_manager=data_or_state_manager)

    # 2. If Rich is unavailable or plain text is requested, use pure ASCII renderer
    if not HAS_RICH or force_plain:
        return _render_dashboard_plain_ascii(data)

    try:
        buffer = io.StringIO()
        # Use box.ASCII to avoid Windows cp1252 charmap encoding errors
        render_console = console or Console(
            file=buffer,
            record=True,
            width=100,
            force_terminal=False,
            color_system=None,
        )

        # Header panel
        proj = data.get("project", {})
        proj_name = proj.get("name", "Unknown Project")
        proj_id = proj.get("project_id", "N/A")
        proj_stat = proj.get("status", "active")
        proj_mode = proj.get("integrity_mode", "development")
        proj_root = proj.get("root_path", "N/A")

        header_text = (
            f"Project: {proj_name} (ID: {proj_id})\n"
            f"Status: {proj_stat.upper()} | Integrity Mode: {proj_mode.upper()} | Root: {proj_root}"
        )
        render_console.print(
            Panel(
                header_text,
                title="PERSISTENT CREWAI SYSTEM DASHBOARD",
                subtitle="Observability & Operational Status",
                box=box.ASCII,
                expand=True,
            )
        )

        # Milestones Table
        milestones = data.get("milestones", [])
        ms_table = Table(title="Project Milestones", box=box.ASCII, expand=True)
        ms_table.add_column("Milestone ID", style="cyan", width=14)
        ms_table.add_column("Title", style="bold", width=30)
        ms_table.add_column("Status", width=14)
        ms_table.add_column("Criteria Count", justify="right", width=14)

        if milestones:
            for m in milestones:
                crit = m.get("criteria", [])
                crit_cnt = str(len(crit)) if isinstance(crit, list) else "-"
                ms_table.add_row(
                    str(m.get("milestone_id", "")),
                    str(m.get("title", "")),
                    str(m.get("status", "pending")).upper(),
                    crit_cnt,
                )
        else:
            ms_table.add_row("-", "No milestones recorded", "-", "-")
        render_console.print(ms_table)

        # 8-Agent Operational Team Table
        agents = data.get("agents", [])
        agent_table = Table(title="8-Agent Engineering Team Status", box=box.ASCII, expand=True)
        agent_table.add_column("Specialist Role", style="bold cyan", width=26)
        agent_table.add_column("Operational Status", width=16)
        agent_table.add_column("Current Task ID", width=18)
        agent_table.add_column("Tasks Completed", justify="right", width=16)

        for a in agents:
            role = str(a.get("agent_role", ""))
            status = str(a.get("status", "idle")).upper()
            task_id = str(a.get("current_task_id") or "-")
            comp_cnt = str(a.get("tasks_completed_count", 0))
            agent_table.add_row(role, status, task_id, comp_cnt)
        render_console.print(agent_table)

        # Decisions Table
        decisions = data.get("decisions", [])
        dec_table = Table(title="Recent Architectural Decisions (ADRs)", box=box.ASCII, expand=True)
        dec_table.add_column("Decision ID", style="cyan", width=12)
        dec_table.add_column("Proposer Role", width=22)
        dec_table.add_column("Status", width=12)
        dec_table.add_column("Title & Rationale", width=46)

        if decisions:
            for d in decisions[:5]:
                did = str(d.get("decision_id", ""))
                agent = str(d.get("agent_role", "Architect"))
                d_stat = str(d.get("status", "accepted")).upper()
                d_title = str(d.get("title", ""))
                d_rat = str(d.get("rationale", ""))
                preview = f"{d_title}: {d_rat}" if d_rat else d_title
                dec_table.add_row(did, agent, d_stat, preview[:44])
        else:
            dec_table.add_row("-", "-", "-", "No decisions recorded yet.")
        render_console.print(dec_table)

        # Recent Runs Table
        runs = data.get("runs", [])
        if runs:
            run_table = Table(title="Recent Workflow Runs", box=box.ASCII, expand=True)
            run_table.add_column("Run ID", style="cyan", width=22)
            run_table.add_column("Workflow Type", width=22)
            run_table.add_column("Status", width=12)
            run_table.add_column("Started At", width=22)

            for r in runs[:5]:
                run_table.add_row(
                    str(r.get("run_id", "")),
                    str(r.get("workflow_type", "")),
                    str(r.get("status", "")).upper(),
                    str(r.get("started_at", ""))[:19],
                )
            render_console.print(run_table)

        # Summary Metrics Panel
        metrics = data.get("metrics", {})
        total_tasks = metrics.get("tasks_completed_total", 0)
        err_agents = metrics.get("error_agents_count", 0)
        total_milestones = metrics.get("milestones_total", len(milestones))
        metrics_text = (
            f"Total Completed Tasks: {total_tasks} | Error Agents: {err_agents} | "
            f"Total Milestones: {total_milestones} | Total Canonical Roles: {len(CANONICAL_ROLES)}"
        )
        render_console.print(
            Panel(
                metrics_text,
                title="System Operational Metrics",
                box=box.ASCII,
                expand=True,
            )
        )

        rendered_str = buffer.getvalue()
        if "DASHBOARD" in rendered_str.upper() and "Project" in rendered_str:
            return rendered_str

        # If Rich buffer was captured externally or stripped, fall back to ASCII string
        return _render_dashboard_plain_ascii(data)

    except Exception as e:
        logger.warning("Rich dashboard rendering encountered error, falling back to ASCII: %s", e)
        return _render_dashboard_plain_ascii(data)


def create_step_trace_callback(
    state_manager: StateManager,
    run_id: Optional[str] = None,
    console: Optional[Any] = None,
) -> Callable[[Any], None]:
    """
    Create an execution telemetry step callback for runners and crews.
    Formats real-time progress to terminal while safely logging audit events
    to the SQLite 'execution_traces' table.

    Guarantees:
    - Never raises unhandled exceptions (exploding callback protection).
    - Ensures foreign key presence in 'workflow_runs' before logging.
    - Normalizes agent roles and event types.

    Args:
        state_manager: Active StateManager instance.
        run_id: Associated workflow run ID.
        console: Optional Rich Console for live streaming.

    Returns:
        Callable callback accepting a step event dictionary or object.
    """
    active_run_id = run_id or f"run_trace_{uuid.uuid4().hex[:8]}"

    # Ensure parent workflow run exists in SQLite to satisfy foreign key constraint
    if hasattr(state_manager, "get_workflow_run") and hasattr(state_manager, "create_workflow_run"):
        try:
            existing = state_manager.get_workflow_run(active_run_id)
            if not existing:
                state_manager.create_workflow_run(
                    run_id=active_run_id,
                    workflow_type="traced_run",
                    input_prompt="Auto-initialized for live execution tracing",
                    project_id=getattr(state_manager, "project_id", "default_project"),
                )
        except Exception as e:
            logger.warning("Could not pre-register traced workflow run: %s", e)

    def step_callback(event: Any) -> None:
        try:
            # 1. Normalize event data
            if isinstance(event, dict):
                event_type = str(event.get("event") or event.get("event_type") or "step")
                role = str(event.get("agent_role") or event.get("role") or "Coordinator")
                task_id = event.get("task_id")
                tool_name = event.get("tool_name") or event.get("tool")
                payload = event.get("payload") or event
            else:
                event_type = getattr(event, "event_type", "step")
                role = getattr(event, "agent_role", getattr(event, "role", "Coordinator"))
                task_id = getattr(event, "task_id", None)
                tool_name = getattr(event, "tool_name", getattr(event, "tool", None))
                payload = {"raw_event": str(event)}

            # Normalize role
            try:
                norm_role = normalize_role(str(role))
            except Exception:
                norm_role = str(role)

            # Ensure task foreign key exists in task_history if task_id provided
            eff_task_id = None
            if task_id:
                if hasattr(state_manager, "get_task") and state_manager.get_task(task_id):
                    eff_task_id = task_id
                elif hasattr(state_manager, "record_task_start"):
                    try:
                        state_manager.record_task_start(
                            task_id=task_id,
                            run_id=active_run_id,
                            agent_role=norm_role,
                            task_name=f"trace_{task_id}",
                            description="Live execution traced step",
                            project_id=getattr(state_manager, "project_id", "default_project"),
                        )
                        eff_task_id = task_id
                    except Exception:
                        eff_task_id = None

            # 2. Record to execution_traces in SQLite
            if hasattr(state_manager, "record_trace"):
                state_manager.record_trace(
                    run_id=active_run_id,
                    task_id=eff_task_id,
                    agent_role=norm_role,
                    event_type=event_type,
                    payload=payload,
                    tool_name=tool_name,
                )

            # 3. Live visual streaming (safe against encoding errors)
            if console is not None and HAS_RICH:
                msg = f"[{norm_role}] {event_type.upper()}"
                if tool_name:
                    msg += f" -> Tool: {tool_name}"
                try:
                    console.print(f"[bold cyan]> {msg}[/bold cyan]")
                except Exception:
                    pass

        except Exception as ex:
            # Resilient telemetry sink: never crash runner pipelines
            logger.warning("Telemetry callback suppression: %s", ex)

    return step_callback


__all__ = [
    "get_dashboard_data",
    "get_system_dashboard_data",
    "render_dashboard",
    "create_step_trace_callback",
]
