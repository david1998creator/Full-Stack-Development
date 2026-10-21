"""
E2E Test Helpers and Shared Test Fixture Infrastructure.
Provides hermetic test execution contexts, mock storage isolation,
and progressive milestone availability detection.
"""

import sys
import os
import shutil
import tempfile
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List

# Ensure src directory is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Standard 8 Agent Roles defined in ORIGINAL_REQUEST.md and PROJECT.md
EXPECTED_ROLES = [
    "Coordinator",
    "Architect",
    "Designer",
    "Developer",
    "Tester",
    "Reviewer",
    "GitHub Manager",
    "Memory & Status Manager",
]

# Expected 8 Database Tables defined in PROJECT.md and specifications.md
EXPECTED_TABLES = [
    "schema_migrations",
    "projects",
    "decisions",
    "workflow_runs",
    "task_history",
    "milestones",
    "agent_status",
    "execution_traces",
    "memory_records",
]

# Milestone 1: Persistence availability check
try:
    from persistent_crewai.persistence.db import get_connection, init_db
    from persistent_crewai.persistence.state_manager import StateManager
    from persistent_crewai.persistence.vector_store import (
        DeterministicHashEmbeddingProvider,
        SQLiteVectorStore,
        SearchResult,
    )
    from persistent_crewai.persistence.session import SessionResumptionManager
    HAS_PERSISTENCE = True
except ImportError:
    HAS_PERSISTENCE = False

# Milestone 2: Tools availability check
try:
    from persistent_crewai.tools.workspace_tools import (
        WorkspaceFileReadTool,
        WorkspaceFileWriteTool,
        WorkspaceFileListTool,
        WorkspaceFilePatchTool,
        validate_safe_path,
    )
    from persistent_crewai.tools.github_interface import (
        GitHubAdapterInterface,
        GitHubError,
    )
    from persistent_crewai.tools.mock_github import MockGitHubAdapter
    from persistent_crewai.tools.github_adapter_factory import create_github_adapter
    HAS_TOOLS = True
except ImportError:
    HAS_TOOLS = False

# Milestone 3: Agents availability check
try:
    from persistent_crewai.agents.role_definitions import (
        AGENT_ROLES,
        get_role_definition,
    )
    from persistent_crewai.agents.llm_factory import (
        DeterministicMockLLM,
        get_llm,
    )
    from persistent_crewai.agents.crew_builder import (
        build_agent,
        build_team,
        build_crew,
    )
    from persistent_crewai.agents.runner import (
        SequentialPipelineRunner,
        HierarchicalPipelineRunner,
    )
    HAS_AGENTS = True
except ImportError:
    HAS_AGENTS = False

# Milestone 4: CLI availability check
try:
    from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
    from persistent_crewai.cli.repl import InteractiveREPL
    from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
    HAS_CLI = True
except ImportError:
    HAS_CLI = False


class E2ETestContext:
    """Hermetic test execution context providing temporary isolated workspace and database."""

    def __init__(self, project_id: str = "test_e2e_proj_01"):
        self.project_id = project_id
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.workspace_root: Optional[Path] = None
        self.db_path: Optional[Path] = None
        self.mock_gh_path: Optional[Path] = None
        self.state_manager: Any = None

    def __enter__(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._temp_dir.name)
        data_dir = self.workspace_root / ".persistent_crewai"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "state.db"
        self.mock_gh_path = data_dir / "mock_github.json"

        if HAS_PERSISTENCE:
            init_db(str(self.db_path))
            self.state_manager = StateManager(
                db_path=str(self.db_path),
                project_id=self.project_id,
                workspace_root=self.workspace_root,
            )
            # Create project record
            self.state_manager.create_project(
                project_id=self.project_id,
                name="Test E2E Project",
                description="Hermetic E2E test project context",
                root_path=str(self.workspace_root),
                integrity_mode="development",
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._temp_dir:
            self._temp_dir.cleanup()


def seed_standard_project_state(state_manager: Any, project_id: str = "test_e2e_proj_01") -> None:
    """Seeds typical decisions, milestones, and task history into StateManager for tests."""
    if not state_manager:
        return

    # Seed Decisions
    state_manager.record_decision(
        agent_role="Architect",
        title="Use SQLite with WAL mode for persistence",
        rationale="Single-file, transactional, zero external dependency",
        implications="Fast local reads and writes; WAL pragma enabled",
        status="accepted",
        tags=["database", "persistence", "sqlite"],
        project_id=project_id,
    )
    state_manager.record_decision(
        agent_role="Architect",
        title="Deterministic token hash embedder for offline tests",
        rationale="Guarantees offline reproducibility without external API keys",
        implications="Normalized 64-dim float vectors for semantic comparison",
        status="accepted",
        tags=["vector", "embeddings", "offline"],
        project_id=project_id,
    )

    # Seed Milestones
    state_manager.create_milestone(
        milestone_id="ms_001",
        title="Foundation & Persistence",
        description="Establish SQLite schema, WAL mode, and vector memory",
        criteria=["SQLite schema initialized", "Vector store operational"],
        target_date="2026-10-15T00:00:00Z",
        project_id=project_id,
    )
    state_manager.create_milestone(
        milestone_id="ms_002",
        title="Core Engineering Pipeline",
        description="Run 8-agent sequential workflow",
        criteria=["Agent team instantiated", "Mock LLM verified"],
        target_date="2026-10-20T00:00:00Z",
        project_id=project_id,
    )

    # Seed Agent Status for all 8 roles
    for role in EXPECTED_ROLES:
        state_manager.update_agent_status(
            agent_role=role,
            status="idle",
            current_task_id=None,
            project_id=project_id,
        )

    # Seed a Workflow Run and completed Task
    state_manager.create_workflow_run(
        run_id="run_init_001",
        workflow_type="feature_development",
        input_prompt="Initialize core system architecture",
        trigger_source="cli",
        project_id=project_id,
    )
    state_manager.record_task_start(
        task_id="task_init_001",
        run_id="run_init_001",
        agent_role="Architect",
        task_name="Author ADR-001",
        description="Specify relational database architecture",
        execution_order=1,
        project_id=project_id,
    )
    state_manager.record_task_completion(
        task_id="task_init_001",
        actual_output="ADR-001 approved: SQLite WAL mode adopted.",
        status="completed",
        project_id=project_id,
    )
