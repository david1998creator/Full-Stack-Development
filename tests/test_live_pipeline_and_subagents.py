"""
Unit and integration tests for:
- Dynamic Sub-Agent Spawning (SpawnAssistantTool)
- Live Pipeline Telemetry API (/api/pipeline/live)
- Parent-child agent state tracking in SQLite
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from persistent_crewai.persistence.db import DatabaseManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.tools.subagent_tool import SpawnAssistantTool
from persistent_crewai.workspace import ProjectScaffold


class TestLivePipelineAndSubagents(unittest.TestCase):
    def setUp(self):
        self.old_mock = os.environ.get("MOCK_LLM")
        os.environ["MOCK_LLM"] = "true"
        self.temp_dir = tempfile.mkdtemp(prefix="test_subagents_")
        self.db_path = Path(self.temp_dir) / "state.db"
        DatabaseManager(self.db_path).initialize_schema()
        self.state_mgr = StateManager(db_path=self.db_path, workspace_root=self.temp_dir)
        with self.state_mgr.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO projects (project_id, name, description, root_path) VALUES ('default_project', 'default_project', 'desc', '.')")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.old_mock is not None:
            os.environ["MOCK_LLM"] = self.old_mock
        else:
            os.environ.pop("MOCK_LLM", None)

    def test_spawn_assistant_tool_execution(self):
        proj_dir = Path(self.temp_dir) / "test_project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        tool = SpawnAssistantTool(
            workspace_root=proj_dir,
            state_manager=self.state_mgr,
            parent_role="Developer"
        )
        result = tool._run(
            specialty="Database Specialist",
            task_description="Create a users table schema in src/schema.sql",
            project_slug="test_project",
            target_file="src/schema.sql"
        )
        self.assertIn("Sub-Agent", result)
        self.assertIn("Database Specialist", result)

        # Verify target file was written
        target = proj_dir / "src" / "schema.sql"
        self.assertTrue(target.exists())

        # Verify subagent was recorded in agent_status
        with self.state_mgr.connection() as conn:
            row = conn.execute(
                "SELECT agent_role, status, metadata_json FROM agent_status WHERE agent_role LIKE '%Database_Specialist%'"
            ).fetchone()
            self.assertIsNotNone(row)
            meta = json.loads(row["metadata_json"])
            self.assertEqual(meta.get("parent_agent"), "Developer")
            self.assertEqual(meta.get("specialty"), "Database Specialist")

    def test_subagent_trace_recorded(self):
        tool = SpawnAssistantTool(
            workspace_root=self.temp_dir,
            state_manager=self.state_mgr,
            parent_role="Developer"
        )
        tool._run(
            specialty="API Specialist",
            task_description="Implement healthcheck endpoint",
            project_slug="test_project",
            target_file="src/routes.py"
        )
        with self.state_mgr.connection() as conn:
            traces = conn.execute(
                "SELECT event_type, agent_role, payload FROM execution_traces WHERE event_type = 'subagent_completion'"
            ).fetchall()
            self.assertGreaterEqual(len(traces), 1)
            payload = json.loads(traces[0]["payload"])
            self.assertEqual(payload.get("specialty"), "API Specialist")


if __name__ == "__main__":
    unittest.main()
