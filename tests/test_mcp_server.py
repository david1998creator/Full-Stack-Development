"""
Unit and integration tests for Model Context Protocol (MCP) server.
Tests tool execution, task decomposition, Budget Guard approval flow,
SQLite persistence lifecycle, and physical file scaffolding.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from persistent_crewai.config import CANONICAL_ROLES
from persistent_crewai.persistence.db import DatabaseManager
from persistent_crewai.persistence.state_manager import StateManager
import persistent_crewai.mcp_server as mcp_mod


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.old_mock = os.environ.get("MOCK_LLM")
        os.environ["MOCK_LLM"] = "1"

        self.temp_dir = tempfile.mkdtemp(prefix="test_mcp_")
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "state.db"
        DatabaseManager(self.db_path).initialize_schema()

        self.state_mgr = StateManager(db_path=self.db_path, workspace_root=self.workspace_root)
        with self.state_mgr.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO projects (project_id, name, description, root_path, status) "
                "VALUES ('mcp_test_proj', 'MCP Test App', 'Test description', ?, 'active')",
                (str(self.workspace_root / "projects" / "mcp_test_proj"),)
            )

        # Patch state_mgr and workspace_root in mcp_server module
        self.orig_state_mgr = mcp_mod.state_mgr
        self.orig_workspace_root = mcp_mod.workspace_root
        self.orig_db_path = mcp_mod.db_path

        mcp_mod.state_mgr = self.state_mgr
        mcp_mod.workspace_root = self.workspace_root
        mcp_mod.db_path = self.db_path
        mcp_mod.interactive_executor.state_mgr = self.state_mgr
        mcp_mod.interactive_executor.workspace_root = self.workspace_root

    def tearDown(self):
        mcp_mod.state_mgr = self.orig_state_mgr
        mcp_mod.workspace_root = self.orig_workspace_root
        mcp_mod.db_path = self.orig_db_path
        mcp_mod.interactive_executor.state_mgr = self.orig_state_mgr
        mcp_mod.interactive_executor.workspace_root = self.orig_workspace_root

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.old_mock is not None:
            os.environ["MOCK_LLM"] = self.old_mock
        else:
            os.environ.pop("MOCK_LLM", None)

    def test_get_capabilities(self):
        """Test capability interrogation and canonical role compliance."""
        res_str = mcp_mod.get_capabilities()
        res = json.loads(res_str)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["mode"], "mock")
        self.assertEqual(len(res["canonical_roles"]), 10)
        self.assertIn("Coordinator", res["canonical_roles"])
        self.assertIn("Architect", res["canonical_roles"])
        self.assertIn("Developer", res["canonical_roles"])
        self.assertIn("Tester", res["canonical_roles"])
        self.assertIn("Security Auditor", res["canonical_roles"])

    def test_decompose_project_and_budget_guard_flow(self):
        """Test decomposition, SQLite persistence, and Budget Guard approval."""
        res_str = mcp_mod.decompose_project("Build Analytics Dashboard", "mcp_test_proj")
        res = json.loads(res_str)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["created_tasks_count"], 0)
        self.assertIn("budget_guard", res)
        action_id = res["budget_guard"]["action_id"]
        self.assertTrue(action_id.startswith("act_"))

        # Check tasks recorded in SQLite
        tasks = self.state_mgr.list_tasks(project_id="mcp_test_proj")
        self.assertGreaterEqual(len(tasks), 1)

        # Resolve Budget Guard
        resolve_str = mcp_mod.resolve_budget_guard(action_id, decision="approved", feedback="Looks good")
        resolve_res = json.loads(resolve_str)
        self.assertEqual(resolve_res["status"], "success")
        self.assertEqual(resolve_res["decision"], "approved")
        self.assertGreater(resolve_res["ready_tasks_count"], 0)

    def test_create_and_execute_task(self):
        """Test manual task creation, role normalization, and execution."""
        create_str = mcp_mod.create_task(
            role="coder",  # Alias for Developer
            title="Implement REST API",
            description="Create FastAPI endpoints",
            slug="mcp_test_proj"
        )
        create_res = json.loads(create_str)
        self.assertEqual(create_res["status"], "success")
        self.assertEqual(create_res["role"], "Developer")
        task_id = create_res["task_id"]

        # Execute task
        exec_str = mcp_mod.execute_task(task_id, slug="mcp_test_proj")
        exec_res = json.loads(exec_str)
        self.assertEqual(exec_res["status"], "success")
        self.assertEqual(exec_res["role"], "Developer")

        # Verify task is completed in SQLite
        task = self.state_mgr.get_task(task_id)
        self.assertEqual(task["status"], "completed")

        # Verify physical scaffold index.html was created
        project_dir = self.workspace_root / "projects" / "mcp_test_proj"
        self.assertTrue((project_dir / "index.html").exists())

    def test_pipeline_status(self):
        """Test pipeline telemetry aggregation."""
        # Create a task
        mcp_mod.create_task(
            role="qa",  # Alias for Tester
            title="Run Unit Tests",
            description="Execute automated test suite",
            slug="mcp_test_proj"
        )
        status_str = mcp_mod.get_pipeline_status("mcp_test_proj")
        status_res = json.loads(status_str)
        self.assertEqual(status_res["status"], "success")
        self.assertGreaterEqual(status_res["total_tasks"], 1)
        self.assertIn("counts", status_res)
        self.assertIn("canonical_roles", status_res)


if __name__ == "__main__":
    unittest.main()
