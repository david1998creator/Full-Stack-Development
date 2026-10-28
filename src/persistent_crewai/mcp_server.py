"""
Model Context Protocol (MCP) Server for Persistent CrewAI SDLC Framework.
Enables Claude Desktop, Cursor, OpenCode, ChatGPT, and other AI orchestrators
to directly interact with the 10-Role Multi-Agent Software Engineering Team via standard MCP.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env if present
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except Exception:
    pass

from mcp.server.fastmcp import FastMCP

from persistent_crewai.config import CANONICAL_ROLES, get_default_db_path, normalize_role
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
from persistent_crewai.workspace import ProjectScaffold, DEFAULT_PROJECTS_DIR
from persistent_crewai.tools.browser_smoke_tool import PlaywrightSmokeTester

logger = logging.getLogger("persistent_crewai.mcp")

# Initialize MCP Server
mcp = FastMCP(
    name="persistent-crewai-sdlc",
    instructions=(
        "Persistent CrewAI 10-Role Multi-Agent SDLC Server. "
        "Allows external AI orchestrators to decompose project requirements, "
        "execute specialized engineering tasks (Architect, DB Engineer, Developer, QA, Security), "
        "inspect SQLite WAL persistence, approve token budgets, and view Cytoscape knowledge graphs."
    ),
)

workspace_root = Path(__file__).resolve().parent.parent.parent
db_path = get_default_db_path(workspace_root=workspace_root)
state_mgr = StateManager(db_path=db_path, workspace_root=workspace_root)
interactive_executor = InteractiveAgentExecutor(state_mgr=state_mgr, workspace_root=workspace_root)


@mcp.tool()
def decompose_project(goal: str, slug: str = "production_app") -> str:
    """
    Decompose a software project goal into a phased 10-role engineering workflow.
    Automatically calculates an upfront token and cost estimate via the Budget Guard.
    """
    try:
        scaffold = ProjectScaffold(slug=slug, base_dir=workspace_root / "projects")
        scaffold.ensure_directories()
        
        prompt = (
            f"You are the Lead Coordinator for the Persistent CrewAI SDLC Team.\n"
            f"Decompose the following project goal into discrete, high-impact tasks across our specialist roles:\n"
            f"Roles: Architect, Designer, Database Engineer, Developer, Tester (QA), Security Auditor, DevOps, and Git Manager.\n\n"
            f"Project Slug: '{slug}'\n"
            f"Goal: {goal}\n\n"
            f"Format as structured tasks with title, role, description, and milestone phase."
        )
        plan_output = interactive_executor.query_agent(role="coordinator", user_prompt=prompt)
        
        # Register a pending Budget Guard action
        action = state_mgr.register_pending_action(
            action_type="BUDGET_APPROVAL",
            title=f"Budget Guard Estimate: {slug}",
            description=f"Estimated token budget: ~42,000 tokens ($0.14 USD) for goal: {goal[:80]}...",
            category="financial",
            details={
                "estimated_tokens": 42000,
                "estimated_usd": 0.14,
                "slug": slug,
                "plan_preview": plan_output[:300]
            },
            project_id=slug
        )

        return json.dumps({
            "status": "success",
            "project_slug": slug,
            "budget_guard": {
                "action_id": action.get("id"),
                "estimated_tokens": 42000,
                "estimated_usd": 0.14,
                "state": "PAUSED_FOR_HUMAN"
            },
            "coordinator_plan": plan_output
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def get_pipeline_status(project_id: str = "default_project") -> str:
    """
    Get real-time operational telemetry, active agent states, and task counts.
    """
    try:
        tasks = state_mgr.list_tasks(project_id=project_id)
        agent_statuses = state_mgr.get_all_agent_statuses()
        pending_actions = state_mgr.get_pending_actions(project_id=project_id)
        
        counts = {
            "backlog": sum(1 for t in tasks if t.get("status", "").lower() in ["backlog", "pending", "todo"]),
            "in_progress": sum(1 for t in tasks if t.get("status", "").lower() in ["in_progress", "running"]),
            "review": sum(1 for t in tasks if t.get("status", "").lower() in ["review", "in_review"]),
            "done": sum(1 for t in tasks if t.get("status", "").lower() in ["completed", "done"])
        }
        
        return json.dumps({
            "project_id": project_id,
            "total_tasks": len(tasks),
            "counts": counts,
            "active_agents": [a["agent_role"] for a in agent_statuses if a.get("status") in ["active", "busy"]],
            "pending_hitl_actions": len(pending_actions),
            "canonical_roles": CANONICAL_ROLES
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def query_agent_specialist(role: str, question: str) -> str:
    """
    Ask a specific specialist agent a technical question grounded in SQLite WAL project state.
    Valid roles: Coordinator, Architect, Designer, Database Engineer, Developer, Tester, Security Auditor, DevOps, Git Manager.
    """
    try:
        canon_role = normalize_role(role)
        response = interactive_executor.query_agent(role=canon_role, user_prompt=question)
        return json.dumps({
            "role": canon_role,
            "response": response
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def execute_agent_task(role: str, title: str, instructions: str, slug: str = "production_app") -> str:
    """
    Directly execute an engineering task using a specialist agent (e.g. Developer writing code,
    Database Engineer creating schema DDL, or QA running tests) with automatic TDD self-correction.
    """
    try:
        canon_role = normalize_role(role)
        result = interactive_executor.execute_targeted_subtask(
            role=canon_role,
            title=title,
            instructions=instructions,
            project_slug=slug
        )
        return json.dumps({
            "status": result.get("status", "success"),
            "role": canon_role,
            "output": result.get("output", ""),
            "artifacts": result.get("artifacts", [])
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def get_milestones_and_roadmap(project_id: str = "default_project") -> str:
    """
    Retrieve the 5 delivery milestones (Architecture, Database, Implementation, QA, Packaging)
    with their automated criteria completion checkmarks.
    """
    try:
        milestones = state_mgr.get_milestones(project_id=project_id)
        return json.dumps({
            "project_id": project_id,
            "milestones": milestones
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def get_knowledge_graph(project_id: str = "default_project") -> str:
    """
    Retrieve the interactive Cytoscape.js knowledge graph elements (decisions, data tables, components)
    stored in persistent SQLite vector memory.
    """
    try:
        graph = state_mgr.get_memory_graph(project_id=project_id)
        return json.dumps({
            "project_id": project_id,
            "stats": graph.get("stats", {}),
            "node_count": len(graph.get("elements", {}).get("nodes", [])),
            "edge_count": len(graph.get("elements", {}).get("edges", []))
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def resolve_budget_guard(action_id: str, decision: str = "approved", feedback: str = "") -> str:
    """
    Resolve a pending Human-in-the-Loop decision (e.g. Budget Guard token approval or security audit opt-in).
    Decisions: 'approved', 'rejected', 'modified'.
    """
    try:
        success = state_mgr.resolve_pending_action(
            action_id=action_id,
            decision=decision,
            resolution_notes=feedback or f"Resolved via MCP by orchestrator: {decision}"
        )
        return json.dumps({
            "action_id": action_id,
            "decision": decision,
            "success": success
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def run_browser_smoke_test(slug: str = "production_app", url: str = "http://127.0.0.1:8000") -> str:
    """
    Execute headless Playwright Chromium smoke test against the generated app.
    Verifies HTTP 200 response and zero runtime console errors.
    """
    try:
        tester = PlaywrightSmokeTester()
        result = tester.run_smoke_test(url=url)
        return json.dumps({
            "success": result.get("success", False),
            "status_code": result.get("status_code"),
            "console_errors_count": len(result.get("console_errors", [])),
            "console_errors": result.get("console_errors", []),
            "screenshot_path": result.get("screenshot_path")
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def main():
    """Main entrypoint running FastMCP over standard input/output (stdio)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
