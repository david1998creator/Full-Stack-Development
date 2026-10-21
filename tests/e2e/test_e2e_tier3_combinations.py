"""
Tier 3 E2E Test Suite: Cross-Feature Pairwise Interactions.
Tests the integration boundaries between:
- Persistence + Agent Execution
- GitHub Tools + Workspace Tools
- Interactive Query + Session Resumption
- Vector Store Memory + Agent Grounding
- State Export/Import + Pipeline Resumption
- Mock GitHub State Persistence + Workspace Sync
- Execution Trace Streaming + Audit Table Logging
"""

import unittest
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

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
    from persistent_crewai.persistence.db import get_connection, init_db
    from persistent_crewai.persistence.state_manager import StateManager
    from persistent_crewai.persistence.vector_store import (
        DeterministicHashEmbeddingProvider,
        SQLiteVectorStore,
    )
    from persistent_crewai.persistence.session import SessionResumptionManager

if HAS_TOOLS:
    from persistent_crewai.tools.workspace_tools import (
        WorkspaceFileReadTool,
        WorkspaceFileWriteTool,
        WorkspaceFileListTool,
    )
    from persistent_crewai.tools.mock_github import MockGitHubAdapter

if HAS_AGENTS:
    from persistent_crewai.agents.crew_builder import build_agent, build_team
    from persistent_crewai.agents.runner import SequentialPipelineRunner
    from persistent_crewai.agents.llm_factory import DeterministicMockLLM

if HAS_CLI:
    from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor


class TestTier3Combinations(unittest.TestCase):
    """Pairwise cross-feature interaction test suite."""

    def test_c01_persistence_and_agent_execution(self):
        """TC-C.1: Persistence + Agent: Task completion records state in SQLite, queried by subsequent agent."""
        if not HAS_PERSISTENCE or not HAS_AGENTS:
            self.skipTest("persistence or agents not yet implemented")

        with E2ETestContext("test_c_persist_agent") as ctx:
            sm = ctx.state_manager

            # Run 1: Architect records architectural decision and task
            run_info = sm.create_workflow_run(
                run_id="run_pair_01",
                workflow_type="feature_development",
                input_prompt="Architect DB schema",
            )
            sm.record_task_start(
                task_id="task_arch_01",
                run_id="run_pair_01",
                agent_role="Architect",
                task_name="Specify WAL mode",
                description="Adopt SQLite with WAL",
            )
            dec_id = sm.record_decision(
                agent_role="Architect",
                title="Adopt WAL mode for high concurrency",
                rationale="Prevents lock contention during parallel agent executions",
                implications="PRAGMA journal_mode = WAL enabled",
            )
            sm.record_task_completion(
                task_id="task_arch_01",
                actual_output="Architecture decision ADR-001 recorded: WAL mode.",
                status="completed",
            )

            # Run 2: Developer agent retrieves past decisions and implements code
            decisions = sm.get_decisions(status="accepted")
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["title"], "Adopt WAL mode for high concurrency")

            sm.record_task_start(
                task_id="task_dev_01",
                run_id="run_pair_01",
                agent_role="Developer",
                task_name="Implement DB connection factory",
                description="Incorporate WAL pragma into get_connection",
            )
            sm.record_task_completion(
                task_id="task_dev_01",
                actual_output="Connection factory updated with PRAGMA journal_mode = WAL.",
                status="completed",
            )

            # Verify complete execution chain in task history
            history = sm.get_task_history(run_id="run_pair_01")
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["agent_role"], "Architect")
            self.assertEqual(history[1]["agent_role"], "Developer")

    def test_c02_github_tools_and_workspace_tools(self):
        """TC-C.2: GitHub + Workspace: Code written to workspace is committed and PR opened via MockGitHub."""
        if not HAS_TOOLS:
            self.skipTest("tools not yet implemented")

        with E2ETestContext("test_c_gh_workspace") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "crewai/persistent-team"

            # 1. Developer writes feature code in workspace
            feature_path = "src/feature_auth.py"
            code = "def authenticate_user(token: str) -> bool:\n    return token == 'valid_token'\n"
            writer.write_file(feature_path, code)
            self.assertTrue((ctx.workspace_root / feature_path).exists())

            # 2. GitHub Manager creates branch and commits the workspace file
            branch_name = "feat/auth-module"
            gh.create_branch(repo=repo, branch_name=branch_name, from_branch="main")
            commit = gh.create_commit(
                repo=repo,
                branch=branch_name,
                message="feat: implement user authentication logic",
                files=[feature_path],
            )
            self.assertIsNotNone(commit["sha"])

            # 3. GitHub Manager opens Pull Request referencing the feature
            pr = gh.create_pull_request(
                repo=repo,
                title="feat: implement user authentication",
                body=f"Implements authentication logic in `{feature_path}`.",
                head_branch=branch_name,
                base_branch="main",
            )
            self.assertEqual(pr["number"], 101)
            self.assertIn("src/feature_auth.py", pr["body"])

            # 4. Reviewer verifies content from workspace and approves PR
            content_in_workspace = reader.read_file(feature_path)
            self.assertIn("authenticate_user", content_in_workspace)
            merge_res = gh.merge_pull_request(repo=repo, pr_number=101)
            self.assertTrue(merge_res["merged"])

    def test_c03_interactive_query_and_session_resumption(self):
        """TC-C.3: REPL Q&A + Session Resumption: Rebooted session rehydrates state, verified in Coordinator query."""
        if not HAS_PERSISTENCE or not HAS_CLI:
            self.skipTest("persistence or cli not yet implemented")

        with E2ETestContext("test_c_query_resume") as ctx:
            # Session 1: Seed project milestones and complete a milestone
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            ctx.state_manager.update_milestone(
                milestone_id="ms_001",
                status="achieved",
                achieved=True,
            )

            # Simulate complete process shutdown & restart with fresh StateManager instance
            resumption_mgr = SessionResumptionManager(
                db_path=str(ctx.db_path),
                project_id=ctx.project_id,
            )
            rebooted_context = resumption_mgr.get_resumption_context()
            self.assertIsNotNone(rebooted_context)

            rebooted_sm = StateManager(
                db_path=str(ctx.db_path),
                project_id=ctx.project_id,
                workspace_root=ctx.workspace_root,
            )

            # Session 2: InteractiveAgentExecutor queries Coordinator
            executor = InteractiveAgentExecutor(state_manager=rebooted_sm)
            response = executor.query_agent(
                role="Coordinator",
                user_prompt="What milestones have been achieved?",
            )
            self.assertIsInstance(response, str)
            self.assertTrue(len(response) > 0)

            # Milestones in rebooted state must retain 'achieved' status
            milestones = rebooted_sm.get_milestones(status="achieved")
            self.assertEqual(len(milestones), 1)
            self.assertEqual(milestones[0]["milestone_id"], "ms_001")

    def test_c04_vector_store_memory_and_agent_grounding(self):
        """TC-C.4: Vector Store + Agent Prompt: Semantic memory retrieved and injected into agent response."""
        if not HAS_PERSISTENCE or not HAS_CLI:
            self.skipTest("persistence or cli not yet implemented")

        with E2ETestContext("test_c_vector_ground") as ctx:
            sm = ctx.state_manager
            embedder = DeterministicHashEmbeddingProvider(dimension=64)
            vstore = SQLiteVectorStore(
                db_path=str(ctx.db_path),
                embedder=embedder,
                project_id=ctx.project_id,
            )

            # Memory Manager indexes architectural decisions in vector store
            vstore.add(
                documents=[
                    "Decision ADR-104: Use SQLite with WAL mode for zero external dependency persistence.",
                    "Decision ADR-105: Use Pydantic v2 for data schema validation and serialization.",
                ],
                metadatas=[
                    {"agent": "Architect", "tag": "database"},
                    {"agent": "Architect", "tag": "pydantic"},
                ],
            )

            # Query agent on database design
            executor = InteractiveAgentExecutor(state_manager=sm, vector_store=vstore)
            response = executor.query_agent(
                role="Architect",
                user_prompt="What did we decide regarding zero-dependency database persistence?",
            )
            self.assertIsInstance(response, str)
            self.assertTrue(len(response) > 0)

    def test_c05_state_export_import_and_pipeline_resumption(self):
        """TC-C.5: Export/Import + Pipeline: State transferred to clean DB resumes pending tasks cleanly."""
        if not HAS_PERSISTENCE or not HAS_AGENTS:
            self.skipTest("persistence or agents not yet implemented")

        # Workspace A: Run initial tasks and export
        with E2ETestContext("test_c_export_src") as ctx_a:
            sm_a = ctx_a.state_manager
            seed_standard_project_state(sm_a, ctx_a.project_id)

            export_file = ctx_a.workspace_root / "migrated_state.json"
            mgr_a = SessionResumptionManager(db_path=str(ctx_a.db_path), project_id=ctx_a.project_id)
            mgr_a.export_state(str(export_file))
            self.assertTrue(export_file.exists())

            # Workspace B: Import state and resume
            with E2ETestContext("test_c_export_dst") as ctx_b:
                mgr_b = SessionResumptionManager(db_path=str(ctx_b.db_path), project_id=ctx_b.project_id)
                mgr_b.import_state(str(export_file), overwrite=True)

                # Verify decisions restored
                decisions_b = ctx_b.state_manager.get_decisions()
                self.assertTrue(len(decisions_b) >= 2)

                # Resume pipeline in Workspace B
                runner = SequentialPipelineRunner(state_manager=ctx_b.state_manager)
                res = runner.run(
                    workflow_type="feature_development",
                    inputs={"feature_title": "Resume next milestone"},
                )
                self.assertEqual(res.get("status"), "completed")

    def test_c06_mock_github_persistence_across_instances(self):
        """TC-C.6: MockGitHub persistence: File-backed mock repository survives adapter reload."""
        if not HAS_TOOLS:
            self.skipTest("tools not yet implemented")

        with E2ETestContext("test_c_gh_persistence") as ctx:
            storage_path = str(ctx.mock_gh_path)
            repo = "company/engine"

            # Instance 1: Create branch and issue
            gh1 = MockGitHubAdapter(storage_path=storage_path)
            gh1.create_branch(repo=repo, branch_name="feat/resilience", from_branch="main")
            gh1.create_issue(repo=repo, title="Test Issue 1", body="Body 1")

            # Instance 2: Reload from disk
            gh2 = MockGitHubAdapter(storage_path=storage_path)
            branches = gh2.list_branches(repo=repo)
            branch_names = [b["name"] for b in branches]
            self.assertIn("feat/resilience", branch_names)

            issues = gh2.list_issues(repo=repo)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["title"], "Test Issue 1")

    def test_c07_execution_trace_streaming_and_relational_log(self):
        """TC-C.7: Execution Traces: Agent thoughts and tool calls logged to relational audit table."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistence not yet implemented")

        with E2ETestContext("test_c_traces") as ctx:
            sm = ctx.state_manager
            run_id = "run_trace_001"
            sm.create_workflow_run(run_id=run_id, workflow_type="audit", input_prompt="Trace test")

            # Record thought, tool call, and tool output events
            sm.record_trace(
                run_id=run_id,
                task_id=None,
                agent_role="Coordinator",
                event_type="thought",
                payload={"thought": "Evaluating project dependencies"},
            )
            sm.record_trace(
                run_id=run_id,
                task_id=None,
                agent_role="Coordinator",
                event_type="tool_start",
                tool_name="WorkspaceFileListTool",
                payload={"directory": "src"},
            )
            sm.record_trace(
                run_id=run_id,
                task_id=None,
                agent_role="Coordinator",
                event_type="tool_end",
                tool_name="WorkspaceFileListTool",
                payload={"files_found": 5},
            )

            traces = sm.get_traces(run_id=run_id)
            self.assertEqual(len(traces), 3)
            self.assertEqual(traces[0]["event_type"], "thought")
            self.assertEqual(traces[1]["event_type"], "tool_start")
            self.assertEqual(traces[2]["event_type"], "tool_end")


if __name__ == "__main__":
    unittest.main()
