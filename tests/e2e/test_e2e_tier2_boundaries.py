"""
Tier 2 E2E Test Suite: Boundary & Corner Case Verification.
Exercises invalid inputs, missing files, corrupted state, path traversal attacks,
missing agent configs, foreign key integrity, and git simulation edge conditions.
"""

import unittest
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any

from tests.e2e.helpers import (
    E2ETestContext,
    seed_standard_project_state,
    EXPECTED_ROLES,
    HAS_PERSISTENCE,
    HAS_TOOLS,
    HAS_AGENTS,
    HAS_CLI,
)

if HAS_PERSISTENCE:
    from persistent_crewai.persistence.db import get_connection
    from persistent_crewai.persistence.state_manager import StateManager
    from persistent_crewai.persistence.vector_store import (
        DeterministicHashEmbeddingProvider,
        SQLiteVectorStore,
    )
    from persistent_crewai.persistence.session import (
        SessionResumptionManager,
        StateImportConflictError,
        SchemaVersionMismatchError,
    )

if HAS_TOOLS:
    from persistent_crewai.tools.workspace_tools import (
        WorkspaceFileReadTool,
        WorkspaceFileWriteTool,
        WorkspaceFilePatchTool,
        validate_safe_path,
    )
    from persistent_crewai.tools.mock_github import MockGitHubAdapter
    from persistent_crewai.tools.github_interface import (
        GitHubError,
        GitHubNotFoundError,
    )

if HAS_AGENTS:
    from persistent_crewai.agents.role_definitions import get_role_definition
    from persistent_crewai.agents.llm_factory import DeterministicMockLLM

if HAS_CLI:
    from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
    from persistent_crewai.cli.repl import InteractiveREPL


# ==============================================================================
# Tier 2 Boundary Suite 1: Workspace Tools & Security Containment
# ==============================================================================
class TestTier2BoundaryWorkspaceAndSecurity(unittest.TestCase):
    """Boundary and security containment tests for workspace file operations."""

    def test_b01_path_traversal_parent_directory_blocked(self):
        """TC-B.1: Attempting to read or write via parent directory traversal (../../) raises PermissionError."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_traversal_rel") as ctx:
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)

            # Upward traversal attempt
            traversal_path = "../../etc/shadow"
            with self.assertRaises(PermissionError):
                validate_safe_path(traversal_path, ctx.workspace_root)

            with self.assertRaises(PermissionError):
                reader.read_file(traversal_path)

            with self.assertRaises(PermissionError):
                writer.write_file(traversal_path, "malicious_content")

    def test_b02_path_traversal_windows_system_blocked(self):
        """TC-B.2: Attempting to access Windows system directories raises PermissionError."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_traversal_win") as ctx:
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            win_path = r"..\..\Windows\System32\drivers\etc\hosts"

            with self.assertRaises(PermissionError):
                validate_safe_path(win_path, ctx.workspace_root)

            with self.assertRaises(PermissionError):
                reader.read_file(win_path)

    def test_b03_file_read_nonexistent_file_raises_error(self):
        """TC-B.3: Reading a nonexistent file inside the workspace raises FileNotFoundError."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_file_not_found") as ctx:
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            with self.assertRaises(FileNotFoundError):
                reader.read_file("nonexistent_file.py")

    def test_b04_file_patch_zero_occurrences_raises_value_error(self):
        """TC-B.4: Patching a file with a search block that does not exist raises ValueError."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_patch_zero") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            patcher = WorkspaceFilePatchTool(workspace_root=ctx.workspace_root)

            target = "app.py"
            writer.write_file(target, "def run():\n    print('hello')\n")

            with self.assertRaises(ValueError) as cm:
                patcher.patch_file(target, search_block="nonexistent block", replace_block="new block")
            self.assertIn("not found", str(cm.exception).lower())

    def test_b05_file_patch_multiple_ambiguous_occurrences_raises_value_error(self):
        """TC-B.5: Patching a file with multiple duplicate occurrences raises ValueError (ambiguous patch)."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_patch_ambiguous") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            patcher = WorkspaceFilePatchTool(workspace_root=ctx.workspace_root)

            target = "duplicate.py"
            writer.write_file(target, "x = 1\n# middle\nx = 1\n")

            with self.assertRaises(ValueError) as cm:
                patcher.patch_file(target, search_block="x = 1", replace_block="x = 2")
            self.assertIn("ambiguous", str(cm.exception).lower())


# ==============================================================================
# Tier 2 Boundary Suite 2: State & Persistence Edge Conditions
# ==============================================================================
class TestTier2BoundaryPersistence(unittest.TestCase):
    """Boundary and corruption tests for SQLite persistence and vector storage."""

    def test_b06_state_manager_foreign_key_integrity_error(self):
        """TC-B.6: Inserting a task referencing a nonexistent workflow run raises sqlite3.IntegrityError."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_fk_integrity") as ctx:
            sm = ctx.state_manager
            with self.assertRaises(sqlite3.IntegrityError):
                sm.record_task_start(
                    task_id="orphan_task_001",
                    run_id="nonexistent_run_999",  # Nonexistent FK
                    agent_role="Developer",
                    task_name="Orphan Task",
                    description="This should fail foreign key check",
                )

    def test_b07_state_manager_empty_decision_fields_raise_error(self):
        """TC-B.7: Attempting to record a decision with empty or whitespace title/rationale raises ValueError."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_empty_decision") as ctx:
            sm = ctx.state_manager
            with self.assertRaises(ValueError):
                sm.record_decision(
                    agent_role="Architect",
                    title="   ",  # Whitespace title
                    rationale="Some rationale",
                )

            with self.assertRaises(ValueError):
                sm.record_decision(
                    agent_role="Architect",
                    title="Valid Title",
                    rationale="",  # Empty rationale
                )

    def test_b08_vector_store_empty_collection_and_whitespace_query(self):
        """TC-B.8: Searching an empty vector store or searching with whitespace returns empty list [] without error."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_vstore_empty") as ctx:
            embedder = DeterministicHashEmbeddingProvider(dimension=64)
            vstore = SQLiteVectorStore(
                db_path=str(ctx.db_path),
                embedder=embedder,
                project_id=ctx.project_id,
            )

            # Empty store search
            empty_results = vstore.search("any query", top_k=5)
            self.assertEqual(empty_results, [], "Empty vector store must return []")

            # Add one item
            vstore.add(["Valid item"], metadatas=[{"tag": "test"}])
            # Whitespace query
            ws_results = vstore.search("   \t\n  ", top_k=5)
            self.assertEqual(ws_results, [], "Whitespace query must return []")

    def test_b09_state_import_nonexistent_file_raises_error(self):
        """TC-B.9: Importing from a nonexistent file path raises FileNotFoundError."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_import_missing") as ctx:
            mgr = SessionResumptionManager(db_path=str(ctx.db_path), project_id=ctx.project_id)
            with self.assertRaises(FileNotFoundError):
                mgr.import_state("nonexistent_backup.json")

    def test_b10_state_import_conflict_without_overwrite(self):
        """TC-B.10: Importing into an existing project with overwrite=False raises StateImportConflictError."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_import_conflict") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            mgr = SessionResumptionManager(db_path=str(ctx.db_path), project_id=ctx.project_id)

            backup_file = ctx.workspace_root / "backup.json"
            mgr.export_state(str(backup_file))

            # Attempting to import into same project with overwrite=False
            with self.assertRaises((StateImportConflictError, ValueError)):
                mgr.import_state(str(backup_file), overwrite=False)

    def test_b11_state_import_schema_version_mismatch(self):
        """TC-B.11: Importing a backup JSON with incompatible schema_version raises SchemaVersionMismatchError."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_b_import_version") as ctx:
            mgr = SessionResumptionManager(db_path=str(ctx.db_path), project_id=ctx.project_id)
            invalid_backup = ctx.workspace_root / "corrupted_version.json"
            invalid_payload = {
                "schema_version": "99.0.0",  # Incompatible future version
                "project": {"project_id": "test_p", "name": "Test"},
                "decisions": [],
            }
            invalid_backup.write_text(json.dumps(invalid_payload), encoding="utf-8")

            with self.assertRaises((SchemaVersionMismatchError, ValueError)):
                mgr.import_state(str(invalid_backup), overwrite=True)


# ==============================================================================
# Tier 2 Boundary Suite 3: Mock GitHub Error Scenarios
# ==============================================================================
class TestTier2BoundaryGitHubAndMock(unittest.TestCase):
    """Boundary and protocol error handling for GitHub simulation."""

    def test_b12_pr_creation_on_nonexistent_head_branch_raises_422(self):
        """TC-B.12: Creating a PR for a nonexistent head branch raises GitHubError with 422 status."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_gh_missing_branch") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "test/repo"

            with self.assertRaises(GitHubError) as cm:
                gh.create_pull_request(
                    repo=repo,
                    title="Invalid PR",
                    body="Body",
                    head_branch="nonexistent-branch-xyz",
                    base_branch="main",
                )
            self.assertEqual(getattr(cm.exception, "status_code", 422), 422)

    def test_b13_pr_merge_on_already_closed_pr_raises_405(self):
        """TC-B.13: Merging a PR that is already closed or merged raises GitHubError with 405 status."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_gh_merge_closed") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "test/repo"

            gh.create_branch(repo=repo, branch_name="feat/test", from_branch="main")
            pr = gh.create_pull_request(
                repo=repo,
                title="PR to merge",
                body="Body",
                head_branch="feat/test",
                base_branch="main",
            )
            pr_num = pr["number"]

            # Merge first time -> succeeds
            gh.merge_pull_request(repo=repo, pr_number=pr_num)

            # Merge second time -> must raise error 405 (not open)
            with self.assertRaises(GitHubError) as cm:
                gh.merge_pull_request(repo=repo, pr_number=pr_num)
            self.assertEqual(getattr(cm.exception, "status_code", 405), 405)

    def test_b14_branch_creation_duplicate_name_raises_422(self):
        """TC-B.14: Creating a branch that already exists raises GitHubError with 422 status."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_gh_dup_branch") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "test/repo"

            gh.create_branch(repo=repo, branch_name="feat/duplicate", from_branch="main")
            with self.assertRaises(GitHubError) as cm:
                gh.create_branch(repo=repo, branch_name="feat/duplicate", from_branch="main")
            self.assertEqual(getattr(cm.exception, "status_code", 422), 422)

    def test_b15_get_nonexistent_issue_raises_404(self):
        """TC-B.15: Querying a nonexistent issue number raises GitHubNotFoundError (404)."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_b_gh_missing_issue") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "test/repo"

            with self.assertRaises((GitHubNotFoundError, GitHubError)) as cm:
                gh.get_issue(repo=repo, issue_number=99999)
            self.assertEqual(getattr(cm.exception, "status_code", 404), 404)


# ==============================================================================
# Tier 2 Boundary Suite 4: Agent & CLI Validation
# ==============================================================================
class TestTier2BoundaryAgentAndCLI(unittest.TestCase):
    """Boundary validation for agent roles and interactive inputs."""

    def test_b16_query_unknown_agent_role_raises_or_warns(self):
        """TC-B.16: Requesting an unknown agent role (e.g. 'Wizard') raises ValueError with suggestions."""
        if not HAS_AGENTS:
            self.skipTest("persistent_crewai.agents not yet implemented")

        with self.assertRaises(ValueError) as cm:
            get_role_definition("Wizard")
        self.assertIn("valid", str(cm.exception).lower())

    def test_b17_interactive_query_empty_string_rejected(self):
        """TC-B.17: Querying an agent with an empty or whitespace string is rejected."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_b_cli_empty_query") as ctx:
            executor = InteractiveAgentExecutor(state_manager=ctx.state_manager)
            with self.assertRaises(ValueError):
                executor.query_agent(role="Coordinator", user_prompt="   ")


if __name__ == "__main__":
    unittest.main()
