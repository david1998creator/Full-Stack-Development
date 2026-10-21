"""
Interactive REPL command shell for Persistent CrewAI multi-agent software engineering framework.
Provides interactive human-in-the-loop agent Q&A, workflow dispatch, targeted subtask execution,
state dashboard inspection, memory queries, and cross-session export/import.
"""

from __future__ import annotations

import io
import logging
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

from persistent_crewai.config import CANONICAL_ROLES, get_workspace_root
from persistent_crewai.persistence.session import SessionResumptionManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import SQLiteVectorStore
from persistent_crewai.agents.runner import run_project_workflow
from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor

logger = logging.getLogger(__name__)


class InteractiveREPL:
    """
    Command-line REPL for human-in-the-loop communication with the 8-agent persistent team.
    Supports single-agent queries, workflow runs, targeted subtasks, state inspection,
    and portable JSON export/import.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        project_id: Optional[str] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        vector_store: Optional[SQLiteVectorStore] = None,
        executor: Optional[InteractiveAgentExecutor] = None,
        resumption_manager: Optional[SessionResumptionManager] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the interactive REPL.

        Args:
            state_manager: StateManager instance for state and audit persistence.
            project_id: Target project identifier.
            workspace_root: Base directory for workspace files.
            vector_store: Optional vector store for semantic memory searches.
            executor: Optional InteractiveAgentExecutor instance.
            resumption_manager: Optional SessionResumptionManager instance.
        """
        if state_manager is not None:
            self.state_manager = state_manager
            self.project_id = project_id or getattr(state_manager, "project_id", "default_project")
        else:
            self.project_id = project_id or "default_project"
            self.state_manager = StateManager(
                project_id=self.project_id,
                workspace_root=workspace_root,
            )

        resolved_root = workspace_root or getattr(self.state_manager, "workspace_root", None)
        self.workspace_root = Path(resolved_root).resolve() if resolved_root else get_workspace_root()

        self.vector_store = vector_store

        if executor is not None:
            self.executor = executor
        else:
            self.executor = InteractiveAgentExecutor(
                state_manager=self.state_manager,
                project_id=self.project_id,
                vector_store=self.vector_store,
                workspace_root=self.workspace_root,
            )

        if resumption_manager is not None:
            self.resumption_manager = resumption_manager
        else:
            db_path = getattr(self.state_manager, "db_path", None)
            if db_path is None and hasattr(self.state_manager, "_db_mgr") and self.state_manager._db_mgr:
                db_path = getattr(self.state_manager._db_mgr, "db_path", None)
            self.resumption_manager = SessionResumptionManager(
                db_path=db_path,
                project_id=self.project_id,
            )

        self.running: bool = True

    def execute_command(self, command_line: str) -> str:
        """
        Parse and dispatch a single REPL command line.

        Supported syntax:
        - help / ?: Displays command guide.
        - status / dashboard: Renders operational status dashboard.
        - ask <role> <prompt...>: Queries specialist agent with state grounding.
        - run task <role> <desc...>: Triggers targeted subtask.
        - task <role> <desc...>: Alias for targeted subtask.
        - run <workflow_type> [--mode seq|hier]: Triggers multi-agent workflow.
        - decisions / decision list: Lists architectural decisions.
        - decision add <role> <title> <rationale>: Records a new decision.
        - milestones / milestone list: Lists project milestones.
        - milestone add <title> <criteria>: Creates a new milestone.
        - memory query <query...> / memory <query...>: Searches semantic memory.
        - export <path>: Exports relational database state to JSON.
        - import <path> [--overwrite]: Imports database state from JSON.
        - resume <run_id>: Resumes an interrupted workflow run.
        - exit / quit / q: Exits REPL.

        Args:
            command_line: Raw input string.

        Returns:
            Command output string.
        """
        raw = command_line.strip()
        if not raw:
            return ""

        # Parse tokens handling quotes and Windows backslashes safely
        try:
            if "\\" in raw or sys.platform == "win32":
                raw_tokens = shlex.split(raw, posix=False)
                tokens = []
                for t in raw_tokens:
                    if len(t) >= 2 and (
                        (t.startswith('"') and t.endswith('"'))
                        or (t.startswith("'") and t.endswith("'"))
                    ):
                        tokens.append(t[1:-1])
                    else:
                        tokens.append(t)
            else:
                tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()

        if not tokens:
            return ""

        cmd = tokens[0].lower()

        try:
            # 1. Exit Commands
            if cmd in ("exit", "quit", "q"):
                self.running = False
                return "Goodbye!"

            # 2. Help Command
            if cmd in ("help", "?"):
                return self._cmd_help()

            # 3. Status / Dashboard
            if cmd in ("status", "dashboard"):
                return render_dashboard(self.state_manager)

            # 4. Ask / Query Command
            if cmd in ("ask", "query"):
                if len(tokens) < 3:
                    return "Usage: ask <role> <user_prompt>"
                role = tokens[1]
                # Reconstruct prompt to support both quoted and unquoted multi-word prompts
                prompt = self._extract_rest_of_string(raw, tokens[:2])
                return self.executor.query_agent(role=role, user_prompt=prompt)

            # 5. Targeted Subtask Execution
            # Match 'run task <role> <desc>' or 'task <role> <desc>'
            if cmd == "run" and len(tokens) >= 4 and tokens[1].lower() == "task":
                role = tokens[2]
                desc = self._extract_rest_of_string(raw, tokens[:3])
                return self.executor.run_targeted_subtask(role=role, task_description=desc)

            if cmd == "task":
                if len(tokens) < 3:
                    return "Usage: task <role> <task_description>"
                role = tokens[1]
                desc = self._extract_rest_of_string(raw, tokens[:2])
                return self.executor.run_targeted_subtask(role=role, task_description=desc)

            # 6. Workflow Runner
            if cmd in ("run", "workflow"):
                if len(tokens) < 2:
                    return "Usage: run <workflow_type> [--mode sequential|hierarchical]"
                workflow_type = tokens[1]
                mode = "sequential"
                if "--mode" in tokens:
                    idx = tokens.index("--mode")
                    if idx + 1 < len(tokens):
                        mode = tokens[idx + 1]

                result = run_project_workflow(
                    workflow_type=workflow_type,
                    mode=mode,
                    state_manager=self.state_manager,
                    project_id=self.project_id,
                )
                output_summary = result.get("final_output") or f"Workflow '{workflow_type}' ({mode}) executed."
                return f"[Workflow Complete]\nRun ID: {result.get('run_id')}\nStatus: {result.get('status')}\n\n{output_summary}"

            # 7. Decisions Management
            if cmd in ("decisions", "decision"):
                return self._handle_decisions(tokens, raw)

            # 8. Milestones Management
            if cmd in ("milestones", "milestone"):
                return self._handle_milestones(tokens, raw)

            # 9. Memory Query
            if cmd == "memory":
                return self._handle_memory(tokens, raw)

            # 10. State Export
            if cmd in ("export", "backup"):
                if len(tokens) < 2:
                    return "Usage: export <file_path>"
                target_path = Path(tokens[1]).resolve()
                summary = self.resumption_manager.export_state(target_path, project_id=self.project_id)
                dec_cnt = summary.get("decisions_count", 0)
                ms_cnt = summary.get("milestones_count", 0)
                return (
                    f"Export completed: {target_path}\n"
                    f"Exported {dec_cnt} decisions, {ms_cnt} milestones for project '{self.project_id}'."
                )

            # 11. State Import
            if cmd in ("import", "restore"):
                if len(tokens) < 2:
                    return "Usage: import <file_path> [--overwrite]"
                source_path = Path(tokens[1]).resolve()
                overwrite = "--overwrite" in tokens
                summary = self.resumption_manager.import_state(source_path, overwrite=overwrite)
                return (
                    f"Import completed successfully from: {source_path}\n"
                    f"Restored project: {summary.get('project_id')} "
                    f"({summary.get('decisions_imported', 0)} decisions, {summary.get('milestones_imported', 0)} milestones)."
                )

            # 12. Workflow Resume
            if cmd == "resume":
                if len(tokens) < 2:
                    return "Usage: resume <run_id>"
                run_id = tokens[1]
                result = run_project_workflow(
                    resume_run_id=run_id,
                    state_manager=self.state_manager,
                    resumption_manager=self.resumption_manager,
                )
                return f"[Workflow Resumed]\nRun ID: {result.get('run_id')}\nStatus: {result.get('status')}\n\n{result.get('final_output')}"

            # Unrecognized Command
            return (
                f"Unknown command '{cmd}'. Type 'help' for available commands.\n"
                f"Valid verbs: ask, run task, run <workflow>, status, decisions, milestones, memory, export, import, exit."
            )

        except Exception as e:
            logger.warning("Error executing REPL command '%s': %s", command_line, e)
            return f"Error: {e}"

    def _extract_rest_of_string(self, raw_line: str, prefix_tokens: List[str]) -> str:
        """Helper to extract unparsed tail string preserving internal whitespace or stripping outer quotes."""
        # Check if parsed tail exists in tokens
        tokens = shlex.split(raw_line) if '"' in raw_line or "'" in raw_line else raw_line.split()
        tail_tokens = tokens[len(prefix_tokens):]
        if tail_tokens:
            joined = " ".join(tail_tokens)
            # Strip enclosing single/double quotes if balanced
            if (joined.startswith("'") and joined.endswith("'")) or (joined.startswith('"') and joined.endswith('"')):
                return joined[1:-1].strip()
            return joined.strip()
        return ""

    def _cmd_help(self) -> str:
        """Generate help message detailing REPL syntax."""
        return (
            "================================================================================\n"
            "                      PERSISTENT CREWAI REPL COMMANDS\n"
            "================================================================================\n"
            "status                        Render real-time system & agent dashboard\n"
            "ask <role> <prompt>           Query individual specialist agent with state grounding\n"
            "run task <role> <desc>        Trigger targeted subtask execution for specialist agent\n"
            "task <role> <desc>            Alias for 'run task'\n"
            "run <workflow> [--mode ...]   Trigger workflow ('feature_development', 'bugfix', 'code_review')\n"
            "decisions                     List recorded architectural decisions (ADRs)\n"
            "decision add <role> <title> <rationale>  Record new architectural decision\n"
            "milestones                    List project milestones and criteria\n"
            "milestone add <title> <criteria>         Create new project milestone\n"
            "memory query <query>          Search vector store memories\n"
            "export <path>                 Export relational state database to JSON\n"
            "import <path> [--overwrite]   Import state backup from JSON\n"
            "resume <run_id>               Resume an interrupted workflow run\n"
            "help                          Show this help message\n"
            "exit / quit                   Exit the interactive REPL shell\n"
            "================================================================================"
        )

    def _handle_decisions(self, tokens: List[str], raw: str) -> str:
        """Handle decision listing and recording."""
        if len(tokens) >= 2 and tokens[1].lower() in ("add", "record", "create"):
            if len(tokens) < 5:
                return "Usage: decision add <role> <title> <rationale>"
            role = tokens[2]
            title = tokens[3]
            rationale = " ".join(tokens[4:])
            did = self.state_manager.record_decision(
                agent_role=role,
                title=title,
                rationale=rationale,
                project_id=self.project_id,
            )
            return f"Decision recorded successfully. Decision ID: {did}"

        # List decisions
        decisions = self.state_manager.get_decisions(self.project_id, limit=20)
        if not decisions:
            return "No decisions recorded."

        lines = [
            "=" * 80,
            f" ARCHITECTURAL DECISION RECORDS (ADRs) - Project: {self.project_id}",
            "=" * 80,
        ]
        for d in decisions:
            did = d.get("decision_id", "N/A")
            agent = d.get("agent_role", "Unknown")
            status = d.get("status", "accepted")
            title = d.get("title", "")
            rat = d.get("rationale", "")
            lines.append(f"[{did}] ({status.upper()}) Proposer: {agent}")
            lines.append(f"  Title: {title}")
            if rat:
                lines.append(f"  Rationale: {rat}")
            lines.append("-" * 80)

        return "\n".join(lines)

    def _handle_milestones(self, tokens: List[str], raw: str) -> str:
        """Handle milestone listing and creation."""
        if len(tokens) >= 2 and tokens[1].lower() in ("add", "create"):
            if len(tokens) < 4:
                return "Usage: milestone add <title> <criteria>"
            title = tokens[2]
            criteria = [" ".join(tokens[3:])]
            mid = self.state_manager.create_milestone(
                title=title,
                criteria=criteria,
                project_id=self.project_id,
            )
            return f"Milestone created successfully. Milestone ID: {mid}"

        # List milestones
        milestones = self.state_manager.get_milestones(self.project_id)
        if not milestones:
            return "No milestones recorded."

        lines = [
            "=" * 80,
            f" PROJECT MILESTONES - Project: {self.project_id}",
            "=" * 80,
        ]
        for m in milestones:
            mid = m.get("milestone_id", "N/A")
            status = m.get("status", "pending")
            title = m.get("title", "")
            criteria = m.get("criteria", [])
            crit_str = ", ".join(criteria) if isinstance(criteria, list) else str(criteria)
            lines.append(f"[{mid}] ({status.upper()}) {title}")
            lines.append(f"  Criteria: {crit_str}")
            lines.append("-" * 80)

        return "\n".join(lines)

    def _handle_memory(self, tokens: List[str], raw: str) -> str:
        """Handle semantic memory searches."""
        # Query tokens: strip 'memory' and optional 'query' or 'search'
        sub_tokens = tokens[1:]
        if sub_tokens and sub_tokens[0].lower() in ("query", "search"):
            sub_tokens = sub_tokens[1:]

        if not sub_tokens:
            return "Usage: memory query <search_phrase>"

        query_str = " ".join(sub_tokens)

        if self.vector_store is not None:
            results = self.vector_store.search(query_str, top_k=5)
            if not results:
                return f"No memory records matched query '{query_str}'."

            lines = [
                "=" * 80,
                f" SEMANTIC MEMORY SEARCH RESULTS: '{query_str}'",
                "=" * 80,
            ]
            for idx, r in enumerate(results, start=1):
                content = getattr(r, "content", str(r))
                score = getattr(r, "score", 0.0)
                meta = getattr(r, "metadata", {})
                lines.append(f"Result #{idx} (Score: {score:.4f}) [Tag: {meta.get('tag', 'general')}]:")
                lines.append(f"  {content}")
                lines.append("-" * 80)
            return "\n".join(lines)
        else:
            return "Vector store memory is not configured for this session."

    def run_batch(self, lines: Union[List[str], Iterable[str]]) -> List[str]:
        """
        Execute a sequence of commands sequentially, skipping blank lines and comments.

        Args:
            lines: Iterable of command line strings.

        Returns:
            List of non-empty command response strings.
        """
        results: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            out = self.execute_command(stripped)
            results.append(out)
            if not self.running:
                break
        return results

    def cmdloop(
        self,
        intro: Optional[str] = None,
        input_stream: Optional[Any] = None,
        output_stream: Optional[Any] = None,
    ) -> None:
        """
        Run interactive REPL loop reading from input_stream and writing to output_stream.

        Args:
            intro: Optional startup banner string.
            input_stream: Stream to read commands from (defaults to sys.stdin).
            output_stream: Stream to print output to (defaults to sys.stdout).
        """
        in_stream = input_stream or sys.stdin
        out_stream = output_stream or sys.stdout

        intro_msg = intro or (
            "Persistent CrewAI 8-Agent Software Engineering Framework REPL\n"
            "Type 'help' for command summary, 'status' for system dashboard, 'exit' to quit.\n"
        )
        out_stream.write(intro_msg)
        out_stream.flush()

        self.running = True

        while self.running:
            try:
                out_stream.write("crewai> ")
                out_stream.flush()

                line = in_stream.readline()
                if not line:  # EOF encountered
                    out_stream.write("\n")
                    break

                response = self.execute_command(line)
                if response:
                    out_stream.write(response + "\n")
                    out_stream.flush()

            except KeyboardInterrupt:
                out_stream.write("\n")
                out_stream.flush()
                continue
            except Exception as e:
                out_stream.write(f"Error: {e}\n")
                out_stream.flush()

    def run(self) -> None:
        """Alias for cmdloop() for interface convenience."""
        self.cmdloop()


__all__ = [
    "InteractiveREPL",
]
