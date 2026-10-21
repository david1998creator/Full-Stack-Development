"""
CLI and Interactive REPL module for Persistent CrewAI multi-agent software engineering framework.
Exposes state-grounded single agent queries, Rich/ASCII status dashboards, execution telemetry tracing,
and interactive command-line REPL.
"""

from __future__ import annotations

from persistent_crewai.cli.dashboard import (
    create_step_trace_callback,
    get_dashboard_data,
    get_system_dashboard_data,
    render_dashboard,
)
from persistent_crewai.cli.interactive_query import (
    InteractiveAgentExecutor,
    SubtaskResult,
)
from persistent_crewai.cli.main import cli, main
from persistent_crewai.cli.repl import InteractiveREPL

__all__ = [
    "InteractiveAgentExecutor",
    "SubtaskResult",
    "InteractiveREPL",
    "get_dashboard_data",
    "get_system_dashboard_data",
    "render_dashboard",
    "create_step_trace_callback",
    "cli",
    "main",
]
