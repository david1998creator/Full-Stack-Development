"""
Top-level Command-Line Interface (CLI) entrypoint for Persistent CrewAI.
Exposes 'crewai-team' CLI commands: repl, run, ask, task, status, export, and import.
Configured via pyproject.toml project.scripts: crewai-team = "persistent_crewai.cli.main:cli".
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import click

from persistent_crewai.persistence.session import SessionResumptionManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.agents.runner import run_project_workflow
from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
from persistent_crewai.cli.repl import InteractiveREPL

logger = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.option("--project-id", "-p", default=None, help="Target project identifier")
@click.option("--db-path", "-d", default=None, help="Path to SQLite state database")
@click.option("--workspace-root", "-w", default=None, help="Path to workspace root directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose debug logging")
@click.pass_context
def cli(
    ctx: click.Context,
    project_id: Optional[str],
    db_path: Optional[str],
    workspace_root: Optional[str],
    verbose: bool,
) -> None:
    """Persistent CrewAI: Multi-agent software engineering framework with state persistence."""
    target_level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("persistent_crewai").setLevel(target_level)

    ctx.ensure_object(dict)
    ctx.obj["project_id"] = project_id
    ctx.obj["db_path"] = Path(db_path).resolve() if db_path else None
    ctx.obj["workspace_root"] = Path(workspace_root).resolve() if workspace_root else None

    # If no subcommand given, invoke the interactive REPL by default
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command("repl")
@click.option("--batch", "-b", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Execute script file in batch mode")
@click.pass_context
def repl(ctx: click.Context, batch: Optional[Path]) -> None:
    """Launch the interactive human-in-the-loop REPL shell."""
    sm = StateManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    shell = InteractiveREPL(
        state_manager=sm,
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )

    if batch:
        with open(batch, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
        outputs = shell.run_batch(lines)
        for out in outputs:
            if out:
                click.echo(out)
    else:
        shell.cmdloop()


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Display real-time system and operational status dashboard."""
    sm = StateManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    dashboard_text = render_dashboard(sm)
    click.echo(dashboard_text)


@cli.command("ask")
@click.argument("role")
@click.argument("prompt", nargs=-1, required=True)
@click.pass_context
def ask(ctx: click.Context, role: str, prompt: tuple[str, ...]) -> None:
    """Query a specialist agent with state grounding."""
    user_prompt = " ".join(prompt)
    sm = StateManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    executor = InteractiveAgentExecutor(
        state_manager=sm,
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    response = executor.query_agent(role=role, user_prompt=user_prompt)
    click.echo(response)


@cli.command("task")
@click.argument("role")
@click.argument("description", nargs=-1, required=True)
@click.pass_context
def task(ctx: click.Context, role: str, description: tuple[str, ...]) -> None:
    """Execute a targeted engineering subtask with a specialist agent."""
    task_desc = " ".join(description)
    sm = StateManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    executor = InteractiveAgentExecutor(
        state_manager=sm,
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    output = executor.run_targeted_subtask(role=role, task_description=task_desc)
    click.echo(output)


@cli.command("run")
@click.argument("workflow_type", default="feature_development")
@click.option("--mode", "-m", type=click.Choice(["sequential", "hierarchical"], case_sensitive=False), default="sequential", help="Execution orchestration mode")
@click.option("--resume-run-id", default=None, help="Interrupted workflow run ID to resume")
@click.pass_context
def run(ctx: click.Context, workflow_type: str, mode: str, resume_run_id: Optional[str]) -> None:
    """Run a multi-agent engineering workflow or resume an interrupted run."""
    sm = StateManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
        workspace_root=ctx.obj.get("workspace_root"),
    )
    result = run_project_workflow(
        workflow_type=workflow_type,
        mode=mode,
        state_manager=sm,
        project_id=ctx.obj.get("project_id"),
        resume_run_id=resume_run_id,
    )
    click.echo(f"Workflow Run ID: {result.get('run_id')}")
    click.echo(f"Status: {result.get('status')}")
    if result.get("final_output"):
        click.echo("\n--- Final Output ---")
        click.echo(result["final_output"])


@cli.command("export")
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
@click.pass_context
def export(ctx: click.Context, path: Path) -> None:
    """Export relational state database to portable JSON format."""
    resumption = SessionResumptionManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
    )
    summary = resumption.export_state(path, project_id=ctx.obj.get("project_id"))
    click.echo(f"Export completed successfully: {path}")
    click.echo(f"Exported {summary.get('decisions_count', 0)} decisions, {summary.get('milestones_count', 0)} milestones.")


@cli.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--overwrite", is_flag=True, help="Overwrite existing project state if project ID collides")
@click.pass_context
def import_state(ctx: click.Context, path: Path, overwrite: bool) -> None:
    """Import relational state database from portable JSON format."""
    resumption = SessionResumptionManager(
        db_path=ctx.obj.get("db_path"),
        project_id=ctx.obj.get("project_id"),
    )
    summary = resumption.import_state(path, overwrite=overwrite)
    click.echo(f"Import completed successfully from: {path}")
    click.echo(f"Restored project: {summary.get('project_id')} ({summary.get('decisions_imported', 0)} decisions).")


# Alias main to cli for direct execution
main = cli

if __name__ == "__main__":
    cli()
