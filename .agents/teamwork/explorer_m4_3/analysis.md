# Milestone 4 Dashboard & Tracing Architecture Analysis

**Explorer**: Explorer M4-3 (Milestone 4 Dashboard & Tracing Explorer)  
**Date**: 2026-10-13  
**Target Module**: `src/persistent_crewai/cli/dashboard.py`  
**Test Suite**: `tests/test_cli.py` & E2E Suites (`tests/e2e/test_e2e_tier1_features.py`, `tests/e2e/test_e2e_tier3_combinations.py`, `tests/e2e/test_e2e_tier4_scenarios.py`)

---

## 1. Executive Summary

Milestone 4 requires operational observability for the persistent multi-agent framework. As the system runs sequential or hierarchical multi-agent engineering workflows, human operators and automated harnesses need two core capabilities:
1. **System & Operational Status Dashboard**: Real-time terminal rendering of project metadata, active/completed milestones, operational state of all 8 canonical agents, architectural decisions, and workflow execution runs.
2. **Execution Tracing Callbacks**: Streaming trace callbacks that attach to CrewAI runners and agent executors, outputting live visual progress to the terminal while recording persistent audit rows into SQLite's `execution_traces` relational table.

This document presents the verified architecture, exact API contracts, resilient cross-platform rendering mechanisms (including Windows cp1252 encoding safety), complete drop-in code specification for `src/persistent_crewai/cli/dashboard.py`, and an exhaustive test plan.

---

## 2. Interface Contracts & System Integration

### 2.1 Context in Codebase

```
persistent_crewai/
├── persistence/
│   ├── db.py                # Schema DDL (agent_status, execution_traces, workflow_runs)
│   └── state_manager.py     # StateManager (get_system_dashboard_data, record_trace)
├── agents/
│   ├── runner.py            # Hierarchical & Sequential runners emit step_callback events
│   └── crew_builder.py      # Individual Agent instances emit step_callback events
└── cli/
    ├── dashboard.py         # <-- TARGET: Aggregator, Rich/ASCII Renderer, Step Trace Callback
    ├── interactive_query.py # Grounded single-agent Q&A executor
    ├── repl.py              # Interactive REPL ('status' command calls render_dashboard)
    └── main.py              # Click CLI entrypoint ('persistent-crewai status')
```

### 2.2 Contract Requirements from E2E Tests & Callers

1. **`test_r4_05_dashboard_data_generation` (Tier 1 E2E)**:
   ```python
   data = get_dashboard_data(state_manager=ctx.state_manager)
   assert "project" in data
   assert "agents" in data
   assert "decisions" in data
   assert "milestones" in data
   assert len(data["agents"]) == 8

   rendered = render_dashboard(data)
   assert isinstance(rendered, str)
   assert "DASHBOARD" in rendered.upper()
   ```
2. **`test_c07_execution_trace_streaming_and_relational_log` (Tier 3 E2E)**:
   - Verifies `execution_traces` table captures events: `thought`, `tool_start`, `tool_end`, `step_start`, `step_complete`, `delegation`.
   - Verifies queries via `state_manager.get_traces(run_id=...)` return ordered audit rows with JSON-deserialized payloads.
3. **`test_s04_interactive_human_in_the_loop_repl_session` (Tier 4 E2E)**:
   - `repl.execute_command("status")` expects `"Project"` in the output.
4. **`test_adv_hierarchical_resilience_to_exploding_callback` (Adversarial M3)**:
   - Telemetry sinks must never raise unhandled exceptions that abort running pipelines.

---

## 3. Detailed Component Architecture

### 3.1 Data Aggregation: `get_system_dashboard_data` & `get_dashboard_data`

`StateManager` already implements a base method `get_system_dashboard_data(project_id)`. However, `dashboard.py` must normalize and wrap this output to fulfill all callers:
- Provide both `get_system_dashboard_data(state_manager, project_id=None)` and `get_dashboard_data(state_manager, project_id=None)`.
- Ensure all required keys exist:
  - `project`: Dict containing `project_id`, `name`, `status`, `root_path`, `integrity_mode`.
  - `agents`: List of 8 dictionaries for each canonical role (`Coordinator`, `Architect`, `Designer`, `Developer`, `Tester`, `Reviewer`, `GitHub Manager`, `Memory & Status Manager`). Each dictionary guarantees keys:
    - `agent_role` (str)
    - `status` (str: `idle`, `busy`, `active`, `error`, `blocked`, `completed`)
    - `current_task_id` (Optional[str])
    - `tasks_completed_count` (int)
    - `last_active_at` (Optional[str])
    - `last_error` (Optional[str])
    - `metadata` (dict)
  - `milestones`: List of all milestone dictionaries (`milestone_id`, `title`, `status`, `target_date`, `achieved`).
  - `all_milestones`: Alias to `milestones`.
  - `active_milestone`: Dict of the active milestone or `None`.
  - `decisions`: List of recent decision dictionaries (`decision_id`, `agent_role`, `title`, `rationale`, `status`).
  - `recent_decisions`: Alias to `decisions`.
  - `recent_runs`: List of recent workflow run records (`run_id`, `workflow_type`, `status`, `trigger_source`, `started_at`).
  - `active_runs`: Filtered list of runs where `status == 'running'`.
  - `metrics`: Summary counts (`tasks_completed_total`, `error_agents_count`, `agents_total`, `milestones_total`).

### 3.2 Dual Input Support for `render_dashboard`

Callers in tests and CLI call `render_dashboard` in two distinct ways:
1. `render_dashboard(state_manager: StateManager, console=None)`
2. `render_dashboard(data: dict, console=None)`

`render_dashboard(state_manager_or_data, console=None, force_plain=False)` must seamlessly support both:
- If `isinstance(state_manager_or_data, dict)`: Normalize keys and use directly.
- If `state_manager_or_data` is a `StateManager` (or has `get_system_dashboard_data`): Extract data via `get_system_dashboard_data(state_manager_or_data)`.

### 3.3 The 5 Terminal Dashboard Visual Sections

The dashboard renders 5 structured visual sections:
1. **System & Project Header**:
   - Project Name, ID, Operational Status, Root Path, Integrity Mode, Current UTC timestamp.
   - Bordered Panel with header `PERSISTENT CREWAI SYSTEM DASHBOARD`.
2. **Milestones & Progress**:
   - Active milestone highlight.
   - Table: Milestone ID, Title, Status (colored badge: `[green]achieved[/green]`, `[yellow]in_progress[/yellow]`, `[dim]pending[/dim]`), Target Date, Achieved (`Yes`/`No`).
3. **8-Agent Team Status Grid**:
   - Table displaying all 8 canonical agents in canonical order.
   - Columns: `Role`, `Status` (colored badges: `[green]idle[/green]`, `[yellow]busy[/yellow]`, `[red]error[/red]`), `Current Task ID`, `Tasks Completed`, `Last Active At`.
4. **Recent Architecture & Design Decisions**:
   - Table: Decision ID, Proposer Role, Title, Status (`[green]accepted[/green]`), Rationale preview (truncated to 40-50 chars).
5. **Active / Recent Workflow Runs**:
   - Table: Run ID, Type, Status (`[green]completed[/green]`, `[yellow]running[/yellow]`, `[red]failed[/red]`), Trigger Source, Started At.

### 3.4 Cross-Platform Terminal Safety & Windows cp1252 Resilience

**Critical Discovery**:
During live testing in the Windows environment (Python 3.13 on Windows 10/11), default standard output encoding is `cp1252`.
- Standard high-plane Unicode emojis (`🚀`, `✔`, `▶`, etc.) trigger `UnicodeEncodeError: 'charmap' codec can't encode character ...`.
- Default Rich box-drawing characters (`─`, `│`, `┌`, `┐`, etc.) also fail if printed directly to a `cp1252` console stream.

**Resilience Design**:
1. When capturing the rendered dashboard string, `render_dashboard` creates an internal `Console(file=io.StringIO(), record=True, width=100)`.
2. Use `box.ASCII` (or auto-detect terminal encoding; if non-UTF-8, fall back to `box.ASCII`). Pure ASCII borders (`+`, `-`, `|`) are 100% immune to `UnicodeEncodeError`.
3. Use text-based status badges and ASCII arrows (e.g., `[green]idle[/green]`, `[bold yellow]busy[/bold yellow]`, `[bold red]error[/bold red]`, `->`, `[OK]`, `[RUNNING]`).
4. If an external `console` is passed, wrapping `console.print()` in a `try...except UnicodeEncodeError:` fallback ensures zero crashes regardless of user terminal encoding.
5. If Rich is unavailable or `force_plain=True`, `_render_dashboard_plain_text(data)` renders pure ASCII text with identical section coverage.

### 3.5 Execution Tracing Callback: `create_step_trace_callback`

**Signature**:
```python
def create_step_trace_callback(
    state_manager: StateManager,
    run_id: str,
    console: Optional[Console] = None,
) -> Callable[[Any], None]:
```

**Responsibilities**:
1. **Event Parsing & Normalization**:
   - Accepts either `dict` payloads (from `SequentialPipelineRunner` or `HierarchicalPipelineRunner`) or arbitrary objects (e.g. CrewAI step objects with `.tool`, `.tool_input`, `.log`).
   - Normalizes event types: `delegation`, `step_start`, `step_complete`, `tool_start`, `tool_end`, `thought`.
   - Normalizes `agent_role` via `normalize_role()` (falling back safely to `"System"` or the raw string if unrecognized).
2. **Relational Foreign Key Safety**:
   - `execution_traces.run_id` has a foreign key constraint referencing `workflow_runs(run_id)`.
   - The callback checks if `state_manager.get_workflow_run(run_id)` exists. If not, it automatically creates a workflow run record (`workflow_type="traced_run"`, `input_prompt="Auto-created for live execution tracing"`) so that subsequent `state_manager.record_trace()` calls never fail with `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
3. **Database Persistence**:
   - Persists event details to `execution_traces` via `state_manager.record_trace(run_id, task_id, agent_role, event_type, payload, tool_name)`.
4. **Live Visual Streaming**:
   - If `console` is provided (or using standard terminal output):
     - `delegation`: `[bold cyan]-> [DELEGATION][/bold cyan] [bold yellow]{manager}[/] -> [bold yellow]{delegate}[/]: [italic]{task_description}[/]`
     - `step_start`: `[bold green]> [START][/bold green] [bold magenta]{agent_role}[/] | Task: {task_id} {step_name}`
     - `step_complete`: `[bold blue]* [COMPLETE][/bold blue] [bold magenta]{agent_role}[/] | Task: {task_id} {output_preview}`
     - `tool_start`: `[bold yellow]~ [TOOL START][/bold yellow] [bold magenta]{agent_role}[/] invoking [cyan]{tool_name}[/]`
     - `tool_end`: `[bold green]* [TOOL END][/bold green] [bold magenta]{agent_role}[/] [cyan]{tool_name}[/] returned`
     - `thought`: `[dim italic]# [THOUGHT] {agent_role}: {thought}[/dim italic]`
5. **Exploding Callback Suppression**:
   - The entire callback execution is wrapped in `try...except Exception as e: logger.warning(...)`.
   - Satisfies TC-ADV-2.4: Telemetry errors NEVER terminate or corrupt running pipelines.

---

## 4. Proposed Implementation Code

Here is the exact proposed implementation for `src/persistent_crewai/cli/dashboard.py`:

```python
"""
Operational status dashboard and real-time execution trace callbacks for Persistent CrewAI.
Provides data aggregation across SQLite persistence, Rich-formatted terminal rendering
with graceful plain-text fallback, and execution trace streaming with audit persistence.
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

# Attempt to load rich components; fallback gracefully if unavailable
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
    box = None     # type: ignore


# ==============================================================================
# 1. Data Aggregation
# ==============================================================================

def get_system_dashboard_data(
    state_manager: StateManager,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aggregate project context, 8 agent operational statuses, milestones,
    recent decisions, and active workflow runs from SQLite persistence.
    """
    raw_data = state_manager.get_system_dashboard_data(project_id)
    data = dict(raw_data)

    # Ensure backward-compatible and standard key aliases
    data["decisions"] = data.get("recent_decisions", [])
    data["milestones"] = data.get("all_milestones", [])
    data["active_runs"] = [
        r for r in data.get("recent_runs", []) if r.get("status") == "running"
    ]

    # Guarantee all 8 canonical agents are present and ordered canonically
    agents_map = {a.get("agent_role"): a for a in data.get("agents", [])}
    ordered_agents = []
    pid = project_id or state_manager.project_id
    for role in CANONICAL_ROLES:
        if role in agents_map:
            ag = dict(agents_map[role])
            if "last_active_at" not in ag:
                ag["last_active_at"] = None
            ordered_agents.append(ag)
        else:
            ordered_agents.append({
                "agent_role": role,
                "project_id": pid,
                "status": "idle",
                "current_task_id": None,
                "tasks_completed_count": 0,
                "last_active_at": None,
                "last_error": None,
                "metadata": {},
            })
    data["agents"] = ordered_agents

    return data


def get_dashboard_data(
    state_manager: StateManager,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Function alias for get_system_dashboard_data for E2E contract compliance."""
    return get_system_dashboard_data(state_manager, project_id=project_id)


# ==============================================================================
# 2. Rich & Plain-Text Dashboard Rendering
# ==============================================================================

def _get_box_style():
    """Detect whether standard ASCII or Unicode box drawing characters should be used."""
    if not HAS_RICH or box is None:
        return None
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    if "utf" in encoding.lower():
        return box.ROUNDED
    return box.ASCII


def _render_dashboard_plain_text(data: Dict[str, Any]) -> str:
    """Generate pure-ASCII plain text system dashboard representation."""
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("                  PERSISTENT CREWAI SYSTEM DASHBOARD")
    lines.append("=" * 80)

    # Project Info
    proj = data.get("project", {})
    lines.append(f"Project Name : {proj.get('name', 'Unknown')}")
    lines.append(f"Project ID   : {proj.get('project_id', 'unknown')}")
    lines.append(f"Root Path    : {proj.get('root_path', '-')}")
    lines.append(f"Status       : {proj.get('status', 'active').upper()}")
    lines.append(f"Integrity    : {proj.get('integrity_mode', 'development')}")
    lines.append("-" * 80)

    # Milestones & Progress
    lines.append("MILESTONES & PROGRESS:")
    milestones = data.get("milestones") or data.get("all_milestones", [])
    if milestones:
        for m in milestones:
            achieved_mark = "[X]" if m.get("achieved") else "[ ]"
            lines.append(
                f"  {achieved_mark} {m.get('milestone_id', '-')}: "
                f"{m.get('title', '-')} | Status: {m.get('status', 'pending')} "
                f"| Target: {m.get('target_date', '-')}"
            )
    else:
        lines.append("  (No recorded milestones)")
    lines.append("-" * 80)

    # 8-Agent Team Status Grid
    lines.append("8-AGENT TEAM OPERATIONAL STATUS:")
    lines.append(f"  {'ROLE':<26} {'STATUS':<12} {'CURRENT TASK':<18} {'COMPLETED':<10} {'LAST ACTIVE':<20}")
    lines.append("  " + "-" * 88)
    for a in data.get("agents", []):
        role = a.get("agent_role", "Unknown")
        status = a.get("status", "idle")
        task = a.get("current_task_id") or "-"
        completed = str(a.get("tasks_completed_count", 0))
        last_active = a.get("last_active_at") or "-"
        lines.append(f"  {role:<26} {status:<12} {task:<18} {completed:<10} {last_active:<20}")
    lines.append("-" * 80)

    # Recent Architecture & Design Decisions
    lines.append("RECENT ARCHITECTURAL & DESIGN DECISIONS:")
    decisions = data.get("decisions") or data.get("recent_decisions", [])
    if decisions:
        for d in decisions[:5]:
            title = d.get("title", "Untitled")
            role = d.get("agent_role", "Architect")
            status = d.get("status", "accepted")
            lines.append(f"  [{status.upper()}] {role}: {title}")
    else:
        lines.append("  (No recorded decisions)")
    lines.append("-" * 80)

    # Active / Recent Workflow Runs
    lines.append("ACTIVE / RECENT WORKFLOW RUNS:")
    runs = data.get("recent_runs") or data.get("runs", [])
    if runs:
        for r in runs[:5]:
            rid = r.get("run_id", "-")
            wtype = r.get("workflow_type", "feature")
            status = r.get("status", "running")
            started = r.get("started_at", "-")
            lines.append(f"  Run: {rid} | Type: {wtype} | Status: {status.upper()} | Started: {started}")
    else:
        lines.append("  (No recent workflow runs)")
    lines.append("=" * 80)

    return "\n".join(lines)


def render_dashboard(
    state_manager_or_data: Union[StateManager, Dict[str, Any]],
    console: Optional[Console] = None,
    force_plain: bool = False,
) -> str:
    """
    Render terminal status dashboard. Accepts StateManager or pre-aggregated data dict.
    Returns formatted string containing system headers, milestones, 8-agent grid,
    recent decisions, and workflow runs.
    """
    if isinstance(state_manager_or_data, dict):
        data = state_manager_or_data
    else:
        data = get_system_dashboard_data(state_manager_or_data)

    if force_plain or not HAS_RICH:
        rendered_plain = _render_dashboard_plain_text(data)
        if console is not None:
            try:
                console.print(rendered_plain)
            except Exception:
                pass
        return rendered_plain

    try:
        buf = io.StringIO()
        render_console = Console(file=buf, record=True, width=100)
        box_style = _get_box_style()

        # 1. System & Project Header
        proj = data.get("project", {})
        header_text = Text()
        header_text.append("Project Name : ", style="bold cyan")
        header_text.append(f"{proj.get('name', 'Unknown')}\n", style="bold white")
        header_text.append("Project ID   : ", style="bold cyan")
        header_text.append(f"{proj.get('project_id', 'unknown')}   | Status: ", style="white")
        header_text.append(f"{proj.get('status', 'active').upper()}\n", style="bold green")
        header_text.append("Root Path    : ", style="bold cyan")
        header_text.append(f"{proj.get('root_path', '-')}\n", style="white")
        header_text.append("Integrity    : ", style="bold cyan")
        header_text.append(f"{proj.get('integrity_mode', 'development')}\n", style="white")

        header_panel = Panel(
            header_text,
            title="[bold blue]PERSISTENT CREWAI SYSTEM DASHBOARD[/bold blue]",
            border_style="blue",
            box=box_style,
        )
        render_console.print(header_panel)

        # 2. Milestones & Progress
        ms_table = Table(
            title="Project Milestones & Progress",
            box=box_style,
            header_style="bold cyan",
        )
        ms_table.add_column("ID", width=12)
        ms_table.add_column("Title", width=32)
        ms_table.add_column("Status", width=14)
        ms_table.add_column("Target Date", width=14)
        ms_table.add_column("Achieved", width=10)

        milestones = data.get("milestones") or data.get("all_milestones", [])
        if milestones:
            for m in milestones:
                st = m.get("status", "pending")
                st_badge = f"[green]{st}[/green]" if st == "achieved" else (f"[yellow]{st}[/yellow]" if st == "in_progress" else f"[dim]{st}[/dim]")
                achieved = "[bold green]Yes[/bold green]" if m.get("achieved") else "No"
                ms_table.add_row(
                    str(m.get("milestone_id", "-")),
                    str(m.get("title", "-"))[:30],
                    st_badge,
                    str(m.get("target_date", "-") or "-"),
                    achieved,
                )
        else:
            ms_table.add_row("-", "No recorded milestones", "-", "-", "-")
        render_console.print(ms_table)

        # 3. 8-Agent Team Status Grid
        agent_table = Table(
            title="8-Agent Team Operational Status",
            box=box_style,
            header_style="bold magenta",
        )
        agent_table.add_column("Role", style="bold white", width=26)
        agent_table.add_column("Status", width=14)
        agent_table.add_column("Current Task ID", width=18)
        agent_table.add_column("Completed", justify="right", width=10)
        agent_table.add_column("Last Active At", justify="center", width=22)

        for a in data.get("agents", []):
            role = a.get("agent_role", "Unknown")
            st = a.get("status", "idle")
            if st == "idle":
                st_styled = "[green]idle[/green]"
            elif st in ("busy", "active"):
                st_styled = "[bold yellow]busy[/bold yellow]"
            elif st in ("error", "blocked"):
                st_styled = "[bold red]error[/bold red]"
            elif st == "completed":
                st_styled = "[cyan]completed[/cyan]"
            else:
                st_styled = str(st)

            task_id = a.get("current_task_id") or "[dim]-[/dim]"
            completed = str(a.get("tasks_completed_count", 0))
            last_active = str(a.get("last_active_at") or "-")
            agent_table.add_row(role, st_styled, task_id, completed, last_active)
        render_console.print(agent_table)

        # 4. Recent Architecture & Design Decisions
        dec_table = Table(
            title="Recent Architecture & Design Decisions",
            box=box_style,
            header_style="bold yellow",
        )
        dec_table.add_column("ID", width=12)
        dec_table.add_column("Role", width=16)
        dec_table.add_column("Title", width=30)
        dec_table.add_column("Status", width=12)
        dec_table.add_column("Rationale Preview", width=24)

        decisions = data.get("decisions") or data.get("recent_decisions", [])
        if decisions:
            for d in decisions[:5]:
                did = str(d.get("decision_id", "-"))
                drole = str(d.get("agent_role", "Architect"))
                dtitle = str(d.get("title", "Untitled"))[:28]
                dst = str(d.get("status", "accepted"))
                dst_badge = f"[green]{dst}[/green]" if dst == "accepted" else f"[yellow]{dst}[/yellow]"
                drat = str(d.get("rationale", ""))[:22]
                dec_table.add_row(did, drole, dtitle, dst_badge, drat)
        else:
            dec_table.add_row("-", "-", "No recorded decisions", "-", "-")
        render_console.print(dec_table)

        # 5. Active / Recent Workflow Runs
        runs_table = Table(
            title="Active / Recent Workflow Runs",
            box=box_style,
            header_style="bold green",
        )
        runs_table.add_column("Run ID", width=18)
        runs_table.add_column("Type", width=20)
        runs_table.add_column("Status", width=12)
        runs_table.add_column("Trigger", width=10)
        runs_table.add_column("Started At", width=20)

        runs = data.get("recent_runs") or data.get("runs", [])
        if runs:
            for r in runs[:5]:
                rid = str(r.get("run_id", "-"))
                rtype = str(r.get("workflow_type", "feature"))
                rst = str(r.get("status", "completed"))
                rst_badge = f"[green]{rst}[/green]" if rst == "completed" else (f"[bold yellow]{rst}[/bold yellow]" if rst == "running" else f"[bold red]{rst}[/bold red]")
                rtrig = str(r.get("trigger_source", "cli"))
                rstart = str(r.get("started_at", "-") or "-")
                runs_table.add_row(rid, rtype, rst_badge, rtrig, rstart)
        else:
            runs_table.add_row("-", "-", "No workflow runs", "-", "-")
        render_console.print(runs_table)

        # If external console provided, print renderables directly to it
        if console is not None:
            try:
                console.print(header_panel)
                console.print(ms_table)
                console.print(agent_table)
                console.print(dec_table)
                console.print(runs_table)
            except Exception:
                pass

        rendered_text = render_console.export_text()
        return rendered_text

    except Exception as e:
        logger.warning("Rich dashboard rendering exception; falling back to plain text: %s", e)
        return _render_dashboard_plain_text(data)


# ==============================================================================
# 3. Execution Tracing Callbacks
# ==============================================================================

def create_step_trace_callback(
    state_manager: StateManager,
    run_id: str,
    console: Optional[Console] = None,
) -> Callable[[Any], None]:
    """
    Create a callback suitable for CrewAI / Runner `step_callback`.
    Streams live step notifications to terminal while persisting audit rows
    into the SQLite `execution_traces` relational table.
    """
    def _trace_callback(event_payload: Any) -> None:
        try:
            # 1. Parse payload into normalized dictionary
            if isinstance(event_payload, dict):
                raw_dict = dict(event_payload)
            else:
                # Handle non-dict step objects (e.g. AgentAction, AgentFinish, or tuple)
                tool = getattr(event_payload, "tool", None)
                agent = getattr(event_payload, "agent", None) or getattr(event_payload, "agent_role", None)
                log = getattr(event_payload, "log", "")
                raw_dict = {
                    "event": "tool_start" if tool else ("thought" if log else "step_event"),
                    "agent_role": str(agent) if agent else "Developer",
                    "tool_name": tool,
                    "thought": log,
                    "raw": str(event_payload),
                }

            event_type = raw_dict.get("event") or raw_dict.get("event_type") or "step_event"
            raw_role = (
                raw_dict.get("agent_role")
                or raw_dict.get("agent")
                or raw_dict.get("role")
                or raw_dict.get("manager")
                or "System"
            )
            try:
                agent_role = normalize_role(str(raw_role))
            except KeyError:
                agent_role = str(raw_role)

            task_id = raw_dict.get("task_id")
            tool_name = raw_dict.get("tool_name") or raw_dict.get("tool")

            # 2. Guarantee workflow_run exists in SQLite for FK compliance
            try:
                if not state_manager.get_workflow_run(run_id):
                    state_manager.create_workflow_run(
                        run_id=run_id,
                        workflow_type="traced_run",
                        input_prompt="Auto-created for live execution tracing",
                    )
            except Exception as run_err:
                logger.debug("Workflow run guarantee notice: %s", run_err)

            # 3. Record trace row to SQLite persistence
            try:
                state_manager.record_trace(
                    run_id=run_id,
                    task_id=task_id,
                    agent_role=agent_role,
                    event_type=event_type,
                    payload=raw_dict,
                    tool_name=tool_name,
                )
            except Exception as rec_err:
                logger.warning("Failed to persist trace event '%s': %s", event_type, rec_err)

            # 4. Stream live notification to terminal
            target_console = console
            if target_console is None and HAS_RICH:
                target_console = Console(highlight=False)

            if target_console is not None:
                try:
                    if event_type == "delegation":
                        mgr = raw_dict.get("manager", agent_role)
                        dlg = raw_dict.get("delegate", "Specialist")
                        desc = str(raw_dict.get("task_description", ""))[:60]
                        target_console.print(
                            f"[bold cyan]-> [DELEGATION][/bold cyan] "
                            f"[bold yellow]{mgr}[/] -> [bold yellow]{dlg}[/]: [italic]{desc}[/]"
                        )
                    elif event_type == "step_start":
                        step_name = raw_dict.get("step_name") or raw_dict.get("description", "")
                        tid = task_id or "-"
                        target_console.print(
                            f"[bold green]> [START][/bold green] "
                            f"[bold magenta]{agent_role}[/] (Task: {tid}) {str(step_name)[:50]}"
                        )
                    elif event_type == "step_complete":
                        out_str = str(raw_dict.get("output", ""))[:60].replace("\n", " ")
                        tid = task_id or "-"
                        target_console.print(
                            f"[bold blue]* [COMPLETE][/bold blue] "
                            f"[bold magenta]{agent_role}[/] (Task: {tid}) {out_str}"
                        )
                    elif event_type == "tool_start":
                        target_console.print(
                            f"[bold yellow]~ [TOOL START][/bold yellow] "
                            f"[bold magenta]{agent_role}[/] invoking [cyan]{tool_name}[/]"
                        )
                    elif event_type == "tool_end":
                        target_console.print(
                            f"[bold green]* [TOOL END][/bold green] "
                            f"[bold magenta]{agent_role}[/] [cyan]{tool_name}[/] finished"
                        )
                    elif event_type == "thought":
                        th = str(raw_dict.get("thought", ""))[:80]
                        target_console.print(
                            f"[dim italic]# [THOUGHT] {agent_role}: {th}[/dim italic]"
                        )
                    else:
                        target_console.print(
                            f"[dim]* [{event_type}][/dim] [bold]{agent_role}[/]: {str(raw_dict)[:60]}"
                        )
                except Exception as print_err:
                    logger.debug("Console print exception in trace callback: %s", print_err)

        except Exception as e:
            # Trap any exception to protect pipeline execution
            logger.warning("Suppressed trace callback error: %s", e)

    return _trace_callback
```

---

## 5. Comprehensive Test Plan

We plan 4 test suites to be placed in `tests/test_cli.py` or executed via unittest.

### Test Suite 1: Dashboard Data Aggregation (`TestDashboardDataAggregation`)
| Test ID | Test Case | Target / Assertions |
|---------|-----------|---------------------|
| TC-DASH-01 | Key completeness | `get_system_dashboard_data` returns dict containing keys: `project`, `agents`, `milestones`, `decisions`, `recent_runs`, `metrics`. |
| TC-DASH-02 | 8-agent canonical grid | `len(data["agents"]) == 8`. Roles match `CANONICAL_ROLES`. Default status is `idle`, completed count is `0`. |
| TC-DASH-03 | Metric calculation | `tasks_completed_total`, `error_agents_count`, `agents_total` accurately computed from state. |
| TC-DASH-04 | Key alias compatibility | `get_dashboard_data` returns identical dictionary; `decisions` aliases `recent_decisions`, `milestones` aliases `all_milestones`. |
| TC-DASH-05 | Populated state aggregation | Seeding milestones, decisions, task completions, and workflow runs reflects in dashboard aggregation. |

### Test Suite 2: Rich & Plain-Text Dashboard Rendering (`TestDashboardRendering`)
| Test ID | Test Case | Target / Assertions |
|---------|-----------|---------------------|
| TC-REND-01 | StateManager input | `render_dashboard(sm)` returns string containing `"DASHBOARD"` (case-insensitive) and `"Project"`. |
| TC-REND-02 | Dict input | `render_dashboard(data)` returns string containing `"DASHBOARD"` and all 8 agent names. |
| TC-REND-03 | 5 Visual sections | Output contains headers: System Header, Milestones & Progress, 8-Agent Team Status, Recent Decisions, Workflow Runs. |
| TC-REND-04 | Plain text fallback | `render_dashboard(data, force_plain=True)` produces clean ASCII output containing `"SYSTEM DASHBOARD"`, `"Project"`, and tables without Rich dependencies. |
| TC-REND-05 | Windows cp1252 safe rendering | Verify encoding string in cp1252 raises zero `UnicodeEncodeError`. |
| TC-REND-06 | Missing / sparse keys | Passing empty dict `{}` or dict with None values renders safely without exceptions. |
| TC-REND-07 | Long field truncation | Long decision rationales (1000+ chars) and prompt inputs are truncated cleanly without overflowing columns. |

### Test Suite 3: Execution Tracing Callbacks (`TestExecutionTracingCallbacks`)
| Test ID | Test Case | Target / Assertions |
|---------|-----------|---------------------|
| TC-TRC-01 | Event streaming & persistence | Feeding `step_start`, `step_complete`, `delegation`, `tool_start`, `tool_end`, `thought` records 6 rows in `execution_traces`. |
| TC-TRC-02 | Auto workflow run creation | Creating callback with fresh `run_id` (not previously in `workflow_runs`) automatically creates run to satisfy SQLite FK constraints without error. |
| TC-TRC-03 | Role normalization | Passing alias roles (e.g. `manager`, `reviewer`, `github`) normalizes to canonical role in `execution_traces`. |
| TC-TRC-04 | Object payload parsing | Feeding non-dict object with attributes (`tool`, `log`, `agent`) correctly parses into tool and thought trace rows. |
| TC-TRC-05 | Exploding callback resilience | When console or DB throws, callback suppresses exception cleanly without terminating caller. |
| TC-TRC-06 | End-to-end runner integration | Attaching `step_callback=create_step_trace_callback(sm, run_id)` to `SequentialPipelineRunner` or `HierarchicalPipelineRunner` records full trace history. |

### Test Suite 4: Acceptance & E2E Test Compatibility
| Test ID | Test Case | Expected Result |
|---------|-----------|-----------------|
| TC-E2E-R4.5 | `tests/e2e/test_e2e_tier1_features.py::TestTier1FeatureR4CLI::test_r4_05_dashboard_data_generation` | PASS |
| TC-E2E-C07 | `tests/e2e/test_e2e_tier3_combinations.py::TestTier3Combinations::test_c07_execution_trace_streaming_and_relational_log` | PASS |
| TC-E2E-S04 | `tests/e2e/test_e2e_tier4_scenarios.py::TestTier4Scenarios::test_s04_interactive_human_in_the_loop_repl_session` | PASS |

---

## 6. Verification Commands

```powershell
# 1. Run persistence unit tests (verify existing state manager & traces)
python -m unittest tests/test_persistence.py

# 2. Run pipeline unit tests (verify runner step_callback compatibility)
python -m unittest tests/test_pipeline.py

# 3. Run new CLI & Dashboard unit tests once implemented
python -m unittest tests/test_cli.py

# 4. Run Tier 1 and Tier 3 E2E test suites
python -m unittest tests/e2e/test_e2e_tier1_features.py
python -m unittest tests/e2e/test_e2e_tier3_combinations.py
```
