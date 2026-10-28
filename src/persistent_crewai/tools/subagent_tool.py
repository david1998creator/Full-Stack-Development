"""
Dynamic Sub-Agent Spawning Engine for Persistent CrewAI.
Allows primary specialist agents (e.g. Developer) to instantiate scoped,
on-demand sub-agent assistants (Frontend, Database, Migration, etc.)
with isolated workspace tooling and state persistence.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.tools.workspace_tools import BaseTool
from persistent_crewai.workspace import DEFAULT_PROJECTS_DIR, ProjectScaffold, ASTSymbolExtractor

logger = logging.getLogger(__name__)


class SpawnAssistantTool(BaseTool):
    """Tool allowing Lead Developer to spawn specialized sub-agents on demand."""

    name: str = "spawn_subagent_assistant"
    description: str = (
        "Spawn an on-demand specialized assistant sub-agent (e.g., 'Frontend Specialist', 'Database Assistant', "
        "'API Integration Assistant') to execute a dedicated engineering sub-task. "
        "Usage: specialty='Frontend Specialist', task_description='Create React Login Form component in src/components/Login.jsx'"
    )
    workspace_root: Optional[Path] = None
    state_manager: Optional[Any] = None
    parent_role: str = "Developer"

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Optional[Any] = None,
        parent_role: str = "Developer",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if workspace_root:
            self.workspace_root = Path(workspace_root).resolve()
        self.state_manager = state_manager
        self.parent_role = parent_role

    def _run(
        self,
        specialty: str = "Frontend Specialist",
        task_description: str = "",
        project_slug: str = "production_app",
        target_file: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        if not task_description.strip():
            return "Error: task_description cannot be empty."

        clean_specialty = specialty.strip() or "General Assistant"
        subagent_id = f"SubAgent_{clean_specialty.replace(' ', '_')}"
        project_dir = (self.workspace_root or (DEFAULT_PROJECTS_DIR / project_slug)).resolve()
        scaffold = ProjectScaffold(project_slug, base_dir=project_dir.parent)

        # 1. Register active subagent in StateManager / SQLite if available
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if self.state_manager and hasattr(self.state_manager, "connection"):
            try:
                pid = getattr(self.state_manager, "project_id", "default_project")
                with self.state_manager.connection() as conn:
                    # Ensure project exists with valid defaults
                    conn.execute(
                        "INSERT OR IGNORE INTO projects (project_id, name, description, root_path) VALUES (?, ?, 'Project Workspace', '.')",
                        (pid, pid)
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO agent_status (
                            agent_role, project_id, status, current_task_id, last_active_at, metadata_json
                        ) VALUES (?, ?, 'busy', NULL, ?, ?)
                        """,
                        (
                            subagent_id,
                            pid,
                            now_iso,
                            json.dumps({
                                "parent_agent": self.parent_role,
                                "specialty": clean_specialty,
                                "is_subagent": True,
                                "task": task_description[:100],
                            }),
                        ),
                    )
            except Exception as e:
                logger.warning("Could not register subagent in agent_status: %s", e)

        # 2. Extract AST context of existing codebase
        ast_map = ""
        if project_dir.exists():
            try:
                extractor = ASTSymbolExtractor(project_dir)
                ast_map = extractor.generate_repo_map(max_length=2000)
            except Exception:
                pass

        # 3. Prompt specialized LLM assistant
        subagent_prompt = (
            f"You are a specialized sub-agent assistant: '{clean_specialty}'.\n"
            f"Parent Lead Agent: {self.parent_role}\n"
            f"Project: {project_slug}\n"
            f"Subtask: {task_description}\n"
        )
        if target_file:
            subagent_prompt += f"Target File to create/edit: {target_file}\n"
        if ast_map:
            subagent_prompt += f"\nCodebase Context:\n{ast_map}\n"

        subagent_prompt += (
            "\nProduce the clean, modular code implementation. "
            "Output the code clearly with file destination indicated."
        )

        try:
            from persistent_crewai.agents.llm_factory import get_llm
            llm = get_llm(role="Developer")
            resp = llm.predict(subagent_prompt) if hasattr(llm, "predict") else str(llm.invoke(subagent_prompt))

            # 4. If target_file was provided, write file directly to disk
            if target_file:
                code_content = resp
                if "```" in resp:
                    parts = resp.split("```")
                    if len(parts) >= 3:
                        code_lines = parts[1].splitlines()
                        if code_lines and code_lines[0].strip().lower() in ("python", "py", "javascript", "js", "html", "css", "json", "tsx", "jsx", "sql"):
                            code_content = "\n".join(code_lines[1:])
                        else:
                            code_content = parts[1]
                scaffold.write_file(target_file, code_content)

            # 5. Record trace event in SQLite
            if self.state_manager and hasattr(self.state_manager, "record_event"):
                try:
                    self.state_manager.record_event(
                        event_type="subagent_completion",
                        payload={
                            "subagent_id": subagent_id,
                            "parent_agent": self.parent_role,
                            "specialty": clean_specialty,
                            "task": task_description,
                            "target_file": target_file,
                            "output_snippet": resp[:200],
                        },
                        agent_role=self.parent_role,
                    )
                except Exception:
                    pass

            # 6. Mark sub-agent completed in agent_status
            if self.state_manager and hasattr(self.state_manager, "connection"):
                try:
                    with self.state_manager.connection() as conn:
                        conn.execute(
                            "UPDATE agent_status SET status = 'idle', current_task_id = NULL WHERE agent_role = ?",
                            (subagent_id,),
                        )
                except Exception:
                    pass

            return (
                f"[Sub-Agent: '{clean_specialty}'] COMPLETED subtask:\n"
                f"- Task: {task_description}\n"
                f"- Target File: {target_file or 'N/A'}\n"
                f"- Output:\n{resp}"
            )

        except Exception as exc:
            logger.error("Subagent execution failed: %s", exc)
            return f"[Sub-Agent Error] '{clean_specialty}' failed: {str(exc)}"
