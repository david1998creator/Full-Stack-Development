"""
Tier 4 E2E Test Suite: Real-World Multi-Agent Engineering Workflow Scenarios.
Simulates production software engineering workflows across the 8-agent team:
1. Scenario 1: Greenfield Feature Development Workflow (Arch -> Design -> Dev -> Test -> Review -> PR -> Memory)
2. Scenario 2: Bugfix & Regression Verification Workflow (Issue -> Patch -> Regression Test -> PR Merge -> Close)
3. Scenario 3: Crash Recovery & Resumption Workflow (Interrupted task detection -> Recovery -> Resumption)
4. Scenario 4: Interactive Human-in-the-Loop REPL Session (Multi-turn Q&A -> Targeted subtask -> Dashboard -> Export)
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
        WorkspaceFilePatchTool,
        WorkspaceFileListTool,
    )
    from persistent_crewai.tools.mock_github import MockGitHubAdapter

if HAS_AGENTS:
    from persistent_crewai.agents.crew_builder import build_team, build_crew
    from persistent_crewai.agents.runner import (
        SequentialPipelineRunner,
        HierarchicalPipelineRunner,
    )
    from persistent_crewai.agents.llm_factory import DeterministicMockLLM

if HAS_CLI:
    from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
    from persistent_crewai.cli.repl import InteractiveREPL
    from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard


class TestTier4Scenarios(unittest.TestCase):
    """End-to-end multi-agent software engineering workflow scenarios."""

    def test_s01_greenfield_feature_development_workflow(self):
        """Scenario 1: Complete greenfield feature development lifecycle across all 8 roles."""
        if not HAS_PERSISTENCE or not HAS_TOOLS or not HAS_AGENTS:
            self.skipTest("persistence, tools, or agents not yet implemented")

        with E2ETestContext("test_s01_greenfield") as ctx:
            sm = ctx.state_manager
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "crewai/auth-service"

            # Step 1: Coordinator initializes feature workflow and milestone
            run_id = "run_gf_001"
            sm.create_workflow_run(
                run_id=run_id,
                workflow_type="feature_development",
                input_prompt="Build user session token validator",
            )
            sm.create_milestone(
                milestone_id="ms_auth",
                title="User Session Token Validator",
                description="Token validation with expiration and signature check",
                criteria=["ADR documented", "Code implemented", "Tests passing", "PR merged"],
            )

            # Step 2: Architect documents ADR
            sm.update_agent_status("Architect", "active", current_task_id="task_arch")
            dec_id = sm.record_decision(
                agent_role="Architect",
                title="Use HMAC-SHA256 for session token signature",
                rationale="Lightweight, fast, standard library hmac support",
                implications="Tokens must contain payload and signature",
                tags=["auth", "security", "tokens"],
            )
            sm.update_agent_status("Architect", "idle")

            # Step 3: Designer specifies interface contract
            sm.update_agent_status("Designer", "active")
            spec_file = "docs/token_interface.md"
            writer.write_file(spec_file, "# Token Validator Spec\nFormat: payload.signature\n")
            sm.update_agent_status("Designer", "idle")

            # Step 4: Developer implements code in workspace
            sm.update_agent_status("Developer", "active")
            code_file = "src/token_validator.py"
            code = (
                "import hmac, hashlib\n\n"
                "def validate_token(token: str, secret: str) -> bool:\n"
                "    parts = token.split('.')\n"
                "    if len(parts) != 2:\n"
                "        return False\n"
                "    payload, sig = parts\n"
                "    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()\n"
                "    return hmac.compare_digest(sig, expected)\n"
            )
            writer.write_file(code_file, code)
            sm.update_agent_status("Developer", "idle")

            # Step 5: Tester writes and verifies unit test
            sm.update_agent_status("Tester", "active")
            test_file = "tests/test_token_validator.py"
            test_code = (
                "from src.token_validator import validate_token\n"
                "def test_token():\n"
                "    assert not validate_token('invalid', 'secret')\n"
            )
            writer.write_file(test_file, test_code)
            sm.record_task_completion("task_test", "1 unit test passed with 100% assertions.")
            sm.update_agent_status("Tester", "idle")

            # Step 6: Reviewer audits security and approves
            sm.update_agent_status("Reviewer", "active")
            review_notes = "Security Audit: HMAC timing-safe compare_digest utilized. APPROVED."
            sm.record_trace(run_id, None, "Reviewer", "audit_approved", review_notes)
            sm.update_agent_status("Reviewer", "idle")

            # Step 7: GitHub Manager creates branch, commit, and PR
            sm.update_agent_status("GitHub Manager", "active")
            gh.create_branch(repo=repo, branch_name="feat/token-validator", from_branch="main")
            gh.create_commit(repo=repo, branch="feat/token-validator", message="feat: token validator", files=[code_file])
            pr = gh.create_pull_request(
                repo=repo,
                title="feat: implement session token validator",
                body="Implements HMAC-SHA256 session token verification.",
                head_branch="feat/token-validator",
                base_branch="main",
            )
            gh.merge_pull_request(repo=repo, pr_number=pr["number"])
            sm.update_agent_status("GitHub Manager", "idle")

            # Step 8: Memory & Status Manager records completion and updates milestone
            sm.update_agent_status("Memory & Status Manager", "active")
            sm.update_milestone("ms_auth", status="achieved", achieved=True)
            sm.update_workflow_run(run_id, status="completed", final_output="Feature successfully developed and merged.")
            sm.update_agent_status("Memory & Status Manager", "idle")

            # Assertions on entire lifecycle
            self.assertTrue((ctx.workspace_root / code_file).exists())
            self.assertTrue((ctx.workspace_root / test_file).exists())
            achieved_ms = sm.get_milestones(status="achieved")
            self.assertEqual(len(achieved_ms), 1)
            self.assertEqual(achieved_ms[0]["milestone_id"], "ms_auth")

            # Verify all 8 agents are idle at the end of run
            all_statuses = sm.get_all_agent_statuses()
            for agent in all_statuses:
                self.assertEqual(agent["status"], "idle", f"Agent {agent['agent_role']} should be idle")

    def test_s02_bugfix_and_regression_verification_workflow(self):
        """Scenario 2: Bug detection, file patch, regression testing, and PR closure."""
        if not HAS_PERSISTENCE or not HAS_TOOLS:
            self.skipTest("persistence or tools not yet implemented")

        with E2ETestContext("test_s02_bugfix") as ctx:
            sm = ctx.state_manager
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            patcher = WorkspaceFilePatchTool(workspace_root=ctx.workspace_root)
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "crewai/platform"

            # 1. GitHub Manager logs bug issue #1
            issue = gh.create_issue(
                repo=repo,
                title="Bug: Pagination off-by-one error",
                body="page_size calculation exceeds total elements when page_offset=0.",
                labels=["bug", "high-priority"],
            )
            self.assertEqual(issue["number"], 1)

            # 2. Existing buggy code in workspace
            target_file = "src/pagination.py"
            buggy_code = (
                "def get_slice(items, page, page_size):\n"
                "    start = page * page_size\n"
                "    end = start + page_size + 1  # BUGGY off-by-one\n"
                "    return items[start:end]\n"
            )
            writer.write_file(target_file, buggy_code)

            # 3. Developer applies patch using WorkspaceFilePatchTool
            fixed_line = "    end = start + page_size  # Fixed bound"
            patcher.patch_file(
                file_path=target_file,
                search_block="    end = start + page_size + 1  # BUGGY off-by-one",
                replace_block=fixed_line,
            )

            # Verify patch was applied
            patched_content = reader.read_file(target_file)
            self.assertIn("Fixed bound", patched_content)
            self.assertNotIn("BUGGY", patched_content)

            # 4. GitHub Manager branches, commits, opens fix PR, and merges
            gh.create_branch(repo=repo, branch_name="fix/pagination-bounds", from_branch="main")
            gh.create_commit(repo=repo, branch="fix/pagination-bounds", message="fix: pagination bounds", files=[target_file])
            pr = gh.create_pull_request(
                repo=repo,
                title="fix: correct pagination off-by-one boundary",
                body="Resolves #1 by fixing slice calculation end index.",
                head_branch="fix/pagination-bounds",
                base_branch="main",
            )
            gh.merge_pull_request(repo=repo, pr_number=pr["number"])

            # 5. Issue is closed
            gh.update_issue(repo=repo, issue_number=1, state="closed", comment="Resolved in PR 101.")
            closed_issue = gh.get_issue(repo=repo, issue_number=1)
            self.assertEqual(closed_issue["state"], "closed")

    def test_s03_crash_recovery_and_resumption_workflow(self):
        """Scenario 3: Interrupted task detection upon crash and seamless workflow resumption."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistence not yet implemented")

        with E2ETestContext("test_s03_crash") as ctx:
            sm = ctx.state_manager
            run_id = "run_crashed_01"

            # 1. Start workflow and complete task 1
            sm.create_workflow_run(run_id=run_id, workflow_type="feature_development", input_prompt="Build crash handler")
            sm.record_task_start("task_01", run_id, "Architect", "Design DB", "Design schema", execution_order=1)
            sm.record_task_completion("task_01", "Schema designed.", status="completed")

            # 2. Task 2 starts, Developer is set to 'busy', but process is killed mid-flight
            sm.record_task_start("task_02", run_id, "Developer", "Implement DB", "Write db.py", execution_order=2)
            # Update agent status to busy simulating in-flight work
            sm.update_agent_status("Developer", "busy", current_task_id="task_02")

            # 3. Process reboots: SessionResumptionManager inspects state
            resumption_mgr = SessionResumptionManager(db_path=str(ctx.db_path), project_id=ctx.project_id)
            inspection = resumption_mgr.inspect_state()

            # Verify crash detection: task_02 marked 'interrupted', Developer reset to 'idle'
            self.assertIn("interrupted_tasks", inspection)
            self.assertEqual(len(inspection["interrupted_tasks"]), 1)
            self.assertEqual(inspection["interrupted_tasks"][0]["task_id"], "task_02")

            dev_status = sm.get_agent_status("Developer")
            self.assertEqual(dev_status["status"], "idle", "Interrupted agent should be reset to idle")

            # 4. Pipeline resumes: re-executes task_02 to completion
            sm.record_task_start("task_02_retry", run_id, "Developer", "Implement DB", "Write db.py retry", execution_order=2)
            sm.record_task_completion("task_02_retry", "db.py written successfully.", status="completed")
            sm.update_workflow_run(run_id, status="completed", final_output="Workflow resumed and completed.")

            history = sm.get_task_history(run_id=run_id)
            statuses = [t["status"] for t in history]
            self.assertIn("interrupted", statuses)
            self.assertIn("completed", statuses)

    def test_s04_interactive_human_in_the_loop_repl_session(self):
        """Scenario 4: Interactive multi-turn CLI session with grounded Q&A, subtask, dashboard, export."""
        if not HAS_PERSISTENCE or not HAS_CLI:
            self.skipTest("persistence or cli not yet implemented")

        with E2ETestContext("test_s04_repl") as ctx:
            sm = ctx.state_manager
            seed_standard_project_state(sm, ctx.project_id)
            repl = InteractiveREPL(state_manager=sm)

            # 1. Operator queries Coordinator for status
            resp_status = repl.execute_command("ask coordinator 'What is the current project status?'")
            self.assertIsInstance(resp_status, str)
            self.assertTrue(len(resp_status) > 0)

            # 2. Operator queries Architect for schema details
            resp_arch = repl.execute_command("ask architect 'Explain the database design'")
            self.assertIsInstance(resp_arch, str)
            self.assertTrue(len(resp_arch) > 0)

            # 3. Operator triggers targeted subtask for Tester
            resp_subtask = repl.execute_command("run task tester 'Verify SQLite WAL mode integrity'")
            self.assertIsInstance(resp_subtask, str)
            self.assertTrue(len(resp_subtask) > 0)

            # 4. Operator views status dashboard
            resp_dash = repl.execute_command("status")
            self.assertIn("Project", resp_dash)

            # 5. Operator exports project state
            export_target = ctx.workspace_root / "operator_export.json"
            resp_export = repl.execute_command(f"export {export_target}")
            self.assertIn("Export", resp_export)
            self.assertTrue(export_target.exists())

            # Validate exported JSON
            with open(export_target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("schema_version", data)
            self.assertIn("decisions", data)
            self.assertTrue(len(data["decisions"]) >= 2)


if __name__ == "__main__":
    unittest.main()
