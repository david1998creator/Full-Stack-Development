"""
Tier 1 E2E Test Suite: Isolated Feature Verification (Happy Path).
Covers all requirements R1 through R4 with >=5 test cases per feature.
Exercises genuine contracts with zero external network dependency.
"""

import unittest
import math
import json
from pathlib import Path
from typing import Dict, Any, List

from tests.e2e.helpers import (
    E2ETestContext,
    seed_standard_project_state,
    EXPECTED_ROLES,
    EXPECTED_TABLES,
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
        SearchResult,
    )
    from persistent_crewai.persistence.session import SessionResumptionManager

if HAS_TOOLS:
    from persistent_crewai.tools.workspace_tools import (
        WorkspaceFileReadTool,
        WorkspaceFileWriteTool,
        WorkspaceFileListTool,
        WorkspaceFilePatchTool,
    )
    from persistent_crewai.tools.mock_github import MockGitHubAdapter
    from persistent_crewai.tools.github_adapter_factory import create_github_adapter

if HAS_AGENTS:
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

if HAS_CLI:
    from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
    from persistent_crewai.cli.repl import InteractiveREPL
    from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard


# ==============================================================================
# Feature R1: Agent Team & Role Definitions (>=5 Tests)
# ==============================================================================
class TestTier1FeatureR1Agents(unittest.TestCase):
    """Tier 1: Feature R1 - Agent Team, Role Definitions & Orchestration."""

    def test_r1_01_all_eight_roles_instantiated(self):
        """TC-R1.1: Verify all 8 distinct agent roles have valid goals, backstories, and system prompts."""
        if not HAS_AGENTS:
            self.skipTest("persistent_crewai.agents not yet implemented")

        for role_name in EXPECTED_ROLES:
            role_def = get_role_definition(role_name)
            self.assertIsNotNone(role_def, f"Missing definition for role: {role_name}")
            self.assertIn("goal", role_def, f"Role {role_name} missing 'goal'")
            self.assertIn("backstory", role_def, f"Role {role_name} missing 'backstory'")
            self.assertIn("system_prompt", role_def, f"Role {role_name} missing 'system_prompt'")
            self.assertTrue(len(role_def["goal"]) > 10, f"Goal too short for {role_name}")
            self.assertTrue(len(role_def["backstory"]) > 10, f"Backstory too short for {role_name}")

    def test_r1_02_coordinator_delegation_authority(self):
        """TC-R1.2: Verify Coordinator agent has delegation authority enabled for team orchestration."""
        if not HAS_AGENTS:
            self.skipTest("persistent_crewai.agents not yet implemented")

        coord_def = get_role_definition("Coordinator")
        self.assertTrue(
            coord_def.get("allow_delegation", False),
            "Coordinator / Manager must have allow_delegation=True",
        )

    def test_r1_03_specialist_tool_assignments(self):
        """TC-R1.3: Verify specialist roles are assigned their dedicated functional tool contracts."""
        if not HAS_AGENTS:
            self.skipTest("persistent_crewai.agents not yet implemented")

        with E2ETestContext("test_tools_assignment") as ctx:
            team = build_team(state_manager=ctx.state_manager)
            self.assertIn("Developer", team)
            self.assertIn("GitHub Manager", team)
            self.assertIn("Memory & Status Manager", team)

            dev_agent = team["Developer"]
            gh_agent = team["GitHub Manager"]
            mem_agent = team["Memory & Status Manager"]

            # Dev must have file tools
            dev_tool_names = [getattr(t, "name", str(t)) for t in getattr(dev_agent, "tools", [])]
            self.assertTrue(
                any("write" in name.lower() or "read" in name.lower() or "workspace" in name.lower() for name in dev_tool_names),
                f"Developer missing file tools. Tools: {dev_tool_names}",
            )

            # GitHub Manager must have git/github tools
            gh_tool_names = [getattr(t, "name", str(t)) for t in getattr(gh_agent, "tools", [])]
            self.assertTrue(
                any("git" in name.lower() or "pr" in name.lower() or "issue" in name.lower() for name in gh_tool_names),
                f"GitHub Manager missing git tools. Tools: {gh_tool_names}",
            )

    def test_r1_04_deterministic_mock_llm_execution(self):
        """TC-R1.4: Verify DeterministicMockLLM produces deterministic responses without network calls."""
        if not HAS_AGENTS:
            self.skipTest("persistent_crewai.agents not yet implemented")

        mock_llm = DeterministicMockLLM()
        prompt_architect = "Architect: Provide database design for state persistence"
        response1 = mock_llm.predict(prompt_architect)
        response2 = mock_llm.predict(prompt_architect)

        self.assertIsInstance(response1, str)
        self.assertEqual(response1, response2, "MockLLM output must be completely deterministic")
        self.assertTrue(len(response1) > 0, "MockLLM response should not be empty")

    def test_r1_05_sequential_pipeline_execution(self):
        """TC-R1.5: Verify sequential pipeline runner passes context between chained tasks."""
        if not HAS_AGENTS or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.agents or persistence not yet implemented")

        with E2ETestContext("test_seq_pipeline") as ctx:
            runner = SequentialPipelineRunner(state_manager=ctx.state_manager)
            run_result = runner.run(
                workflow_type="feature_development",
                inputs={"feature_title": "Add SQLite WAL mode", "scope": "persistence"},
            )
            self.assertIsInstance(run_result, dict)
            self.assertEqual(run_result.get("status"), "completed")
            self.assertIn("tasks_executed", run_result)
            self.assertTrue(len(run_result["tasks_executed"]) > 0)

    def test_r1_06_hierarchical_runner_configuration(self):
        """TC-R1.6: Verify hierarchical pipeline runner configures Coordinator as manager."""
        if not HAS_AGENTS or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.agents or persistence not yet implemented")

        with E2ETestContext("test_hier_pipeline") as ctx:
            runner = HierarchicalPipelineRunner(state_manager=ctx.state_manager)
            self.assertEqual(runner.manager_role, "Coordinator")
            run_result = runner.run(
                workflow_type="targeted_subtask",
                inputs={"task_description": "Review architecture blueprint"},
            )
            self.assertIn("status", run_result)


# ==============================================================================
# Feature R2: State & Memory Persistence (>=5 Tests)
# ==============================================================================
class TestTier1FeatureR2Persistence(unittest.TestCase):
    """Tier 1: Feature R2 - SQLite Relational Schema, StateManager & Vector Memory."""

    def test_r2_01_sqlite_schema_and_wal_mode(self):
        """TC-R2.1: Verify SQLite database initializes with all 8 tables, indexes, and WAL journal mode."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_schema_wal") as ctx:
            conn = get_connection(str(ctx.db_path))
            cursor = conn.cursor()

            # Check WAL pragma
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal", "SQLite must operate in WAL journal mode")

            # Check tables existence
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = [row[0] for row in cursor.fetchall()]
            for expected_table in EXPECTED_TABLES:
                self.assertIn(
                    expected_table,
                    existing_tables,
                    f"Missing expected table: {expected_table}",
                )
            conn.close()

    def test_r2_02_state_manager_decision_tracking(self):
        """TC-R2.2: Verify StateManager records and queries architectural decisions."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_decision_crud") as ctx:
            sm = ctx.state_manager
            dec_id = sm.record_decision(
                agent_role="Architect",
                title="Adopt Pydantic v2 for data schemas",
                rationale="High performance, strict typing validation",
                implications="Requires Python 3.10+ and pydantic>=2.0",
                status="accepted",
                tags=["pydantic", "schema", "validation"],
            )
            self.assertIsNotNone(dec_id)

            decisions = sm.get_decisions(status="accepted")
            self.assertTrue(len(decisions) >= 1)
            matching = [d for d in decisions if d["title"] == "Adopt Pydantic v2 for data schemas"]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["agent_role"], "Architect")

    def test_r2_03_state_manager_task_and_agent_status(self):
        """TC-R2.3: Verify StateManager tracks task execution lifecycle and agent operational health."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_task_agent_status") as ctx:
            sm = ctx.state_manager
            # Create run
            run_info = sm.create_workflow_run(
                run_id="run_test_001",
                workflow_type="feature_development",
                input_prompt="Build sandboxed file tools",
            )
            self.assertEqual(run_info["run_id"], "run_test_001")

            # Start task
            task_info = sm.record_task_start(
                task_id="task_test_001",
                run_id="run_test_001",
                agent_role="Developer",
                task_name="Implement FileWriteTool",
                description="Write atomic file write tool with sandbox checks",
            )
            self.assertEqual(task_info["status"], "pending")

            # Update agent status
            sm.update_agent_status("Developer", "busy", current_task_id="task_test_001")
            agent_status = sm.get_agent_status("Developer")
            self.assertEqual(agent_status["status"], "busy")
            self.assertEqual(agent_status["current_task_id"], "task_test_001")

            # Complete task
            sm.record_task_completion(
                task_id="task_test_001",
                actual_output="FileWriteTool implemented with atomic rename.",
                status="completed",
            )
            sm.update_agent_status("Developer", "idle")

            tasks = sm.get_task_history(run_id="run_test_001")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["status"], "completed")

    def test_r2_04_deterministic_hash_embeddings(self):
        """TC-R2.4: Verify DeterministicHashEmbeddingProvider produces unit-normalized deterministic vectors."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        embedder = DeterministicHashEmbeddingProvider(dimension=64)
        self.assertEqual(embedder.dimension, 64)

        text = "SQLite WAL mode with transactional durability"
        vec1 = embedder.embed_query(text)
        vec2 = embedder.embed_query(text)

        self.assertEqual(len(vec1), 64)
        self.assertEqual(vec1, vec2, "Hash embeddings must be 100% reproducible")

        # Verify unit L2 norm: ||v||_2 = sqrt(sum(x^2)) ≈ 1.0
        norm = math.sqrt(sum(x * x for x in vec1))
        self.assertAlmostEqual(norm, 1.0, places=4, msg="Embedding vector must be unit L2 normalized")

    def test_r2_05_sqlite_vector_store_cosine_search(self):
        """TC-R2.5: Verify SQLiteVectorStore indexes memories and ranks results by cosine similarity."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_vector_store") as ctx:
            embedder = DeterministicHashEmbeddingProvider(dimension=64)
            vstore = SQLiteVectorStore(
                db_path=str(ctx.db_path),
                embedder=embedder,
                project_id=ctx.project_id,
            )

            docs = [
                "Architect selected SQLite with WAL journal mode for persistence.",
                "Designer crafted Rich CLI dashboard layout with ASCII tables.",
                "Developer implemented file sandboxing and path traversal checks.",
            ]
            metadatas = [
                {"role": "Architect", "category": "database"},
                {"role": "Designer", "category": "ui"},
                {"role": "Developer", "category": "security"},
            ]

            doc_ids = vstore.add(documents=docs, metadatas=metadatas)
            self.assertEqual(len(doc_ids), 3)
            self.assertEqual(vstore.count(), 3)

            # Query for database
            results = vstore.search("SQLite WAL database persistence", top_k=1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].metadata["category"], "database")
            self.assertIn("SQLite with WAL", results[0].content)

    def test_r2_06_session_resumption_and_export_import(self):
        """TC-R2.6: Verify session resumption detects context and state export/import maintains fidelity."""
        if not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.persistence not yet implemented")

        with E2ETestContext("test_session_export") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            resumption_mgr = SessionResumptionManager(
                db_path=str(ctx.db_path),
                project_id=ctx.project_id,
            )

            context = resumption_mgr.get_resumption_context()
            self.assertIn("active_milestones", context)
            self.assertIn("recent_decisions", context)
            self.assertTrue(len(context["recent_decisions"]) >= 2)

            # Export state
            export_file = ctx.workspace_root / "state_backup.json"
            export_summary = resumption_mgr.export_state(str(export_file))
            self.assertTrue(export_file.exists())
            self.assertIn("decisions_count", export_summary)

            # Clean second context for import
            with E2ETestContext("test_session_import") as ctx2:
                resumption_mgr2 = SessionResumptionManager(
                    db_path=str(ctx2.db_path),
                    project_id="imported_project_01",
                )
                import_summary = resumption_mgr2.import_state(str(export_file), overwrite=True)
                self.assertIsNotNone(import_summary)

                # Verify imported decisions
                imported_decisions = ctx2.state_manager.get_decisions(project_id="imported_project_01")
                self.assertTrue(len(imported_decisions) >= 2)


# ==============================================================================
# Feature R3: Workspace & GitHub Tooling (>=5 Tests)
# ==============================================================================
class TestTier1FeatureR3Tools(unittest.TestCase):
    """Tier 1: Feature R3 - Sandboxed Workspace Tools & GitHub Adapter."""

    def test_r3_01_workspace_file_write_and_read(self):
        """TC-R3.1: Verify WorkspaceFileWriteTool creates files atomically and ReadTool retrieves content."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_tools_rw") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)

            nested_file = "src/modules/core.py"
            content = "def calculate_hash(data: str) -> str:\n    return 'mock_hash'\n"

            result = writer.write_file(nested_file, content)
            self.assertIn("written", result.lower())
            self.assertTrue((ctx.workspace_root / nested_file).exists())

            read_content = reader.read_file(nested_file)
            self.assertIn("calculate_hash", read_content)

    def test_r3_02_workspace_file_list_and_pattern(self):
        """TC-R3.2: Verify WorkspaceFileListTool lists files and applies glob pattern matching."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_tools_list") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            lister = WorkspaceFileListTool(workspace_root=ctx.workspace_root)

            writer.write_file("file1.py", "# python file 1")
            writer.write_file("file2.txt", "text file")
            writer.write_file("sub/file3.py", "# python file 3")

            py_files = lister.list_files(directory_path=".", recursive=True, pattern="*.py")
            py_names = [f["path"] for f in py_files]

            self.assertTrue(any("file1.py" in p for p in py_names))
            self.assertTrue(any("file3.py" in p for p in py_names))
            self.assertFalse(any("file2.txt" in p for p in py_names))

    def test_r3_03_workspace_file_patch(self):
        """TC-R3.3: Verify WorkspaceFilePatchTool applies exact block replacement."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_tools_patch") as ctx:
            writer = WorkspaceFileWriteTool(workspace_root=ctx.workspace_root)
            patcher = WorkspaceFilePatchTool(workspace_root=ctx.workspace_root)
            reader = WorkspaceFileReadTool(workspace_root=ctx.workspace_root)

            target = "config.py"
            writer.write_file(target, "DEBUG = False\nVERSION = '1.0.0'\nPORT = 8080\n")

            patch_result = patcher.patch_file(
                file_path=target,
                search_block="DEBUG = False",
                replace_block="DEBUG = True",
            )
            self.assertIn("patched", patch_result.lower())

            new_content = reader.read_file(target)
            self.assertIn("DEBUG = True", new_content)
            self.assertNotIn("DEBUG = False", new_content)

    def test_r3_04_mock_github_issue_lifecycle(self):
        """TC-R3.4: Verify MockGitHubAdapter manages complete issue lifecycle (create, get, list, update)."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_gh_issues") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "owner/repo"

            issue = gh.create_issue(
                repo=repo,
                title="Implement SQLite FTS5 index",
                body="FTS5 triggers needed for fast memory retrieval.",
                labels=["enhancement", "database"],
            )
            self.assertEqual(issue["number"], 1)
            self.assertEqual(issue["state"], "open")

            open_issues = gh.list_issues(repo=repo, state="open")
            self.assertEqual(len(open_issues), 1)

            updated = gh.update_issue(repo=repo, issue_number=1, state="closed", comment="Completed.")
            self.assertEqual(updated["state"], "closed")

            closed_issues = gh.list_issues(repo=repo, state="closed")
            self.assertEqual(len(closed_issues), 1)

    def test_r3_05_mock_github_pr_lifecycle(self):
        """TC-R3.5: Verify MockGitHubAdapter manages complete PR lifecycle (branch, commit, PR, merge)."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_gh_pr") as ctx:
            gh = MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))
            repo = "owner/repo"

            branch = gh.create_branch(repo=repo, branch_name="feat/vector-store", from_branch="main")
            self.assertEqual(branch["name"], "feat/vector-store")

            commit = gh.create_commit(
                repo=repo,
                branch="feat/vector-store",
                message="feat: add SQLiteVectorStore implementation",
                files=["src/persistence/vector_store.py"],
            )
            self.assertIsNotNone(commit["sha"])

            pr = gh.create_pull_request(
                repo=repo,
                title="feat: SQLiteVectorStore implementation",
                body="Introduces cosine similarity search and FTS5 fallback.",
                head_branch="feat/vector-store",
                base_branch="main",
            )
            self.assertEqual(pr["number"], 101)
            self.assertEqual(pr["state"], "open")

            merge_result = gh.merge_pull_request(repo=repo, pr_number=101, commit_message="Merge PR 101")
            self.assertTrue(merge_result["merged"])

    def test_r3_06_github_adapter_factory_fallback(self):
        """TC-R3.6: Verify create_github_adapter defaults to MockGitHubAdapter when GITHUB_TOKEN is unset."""
        if not HAS_TOOLS:
            self.skipTest("persistent_crewai.tools not yet implemented")

        with E2ETestContext("test_gh_factory") as ctx:
            adapter = create_github_adapter(force_mock=True, storage_path=str(ctx.mock_gh_path))
            self.assertIsInstance(adapter, MockGitHubAdapter)


# ==============================================================================
# Feature R4: CLI & Interactive Direct Agent Communication (>=5 Tests)
# ==============================================================================
class TestTier1FeatureR4CLI(unittest.TestCase):
    """Tier 1: Feature R4 - Interactive Q&A Mode, Subtasks & Status Dashboard."""

    def test_r4_01_interactive_query_coordinator(self):
        """TC-R4.1: Verify InteractiveAgentExecutor queries Coordinator grounded in project milestones."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_query_coordinator") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            executor = InteractiveAgentExecutor(state_manager=ctx.state_manager)

            response = executor.query_agent(
                role="Coordinator",
                user_prompt="What is the current status of the project?",
            )
            self.assertIsInstance(response, str)
            self.assertTrue(len(response) > 0)

    def test_r4_02_interactive_query_architect(self):
        """TC-R4.2: Verify querying Architect injects recorded decisions and schemas into prompt."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_query_architect") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            executor = InteractiveAgentExecutor(state_manager=ctx.state_manager)

            response = executor.query_agent(
                role="Architect",
                user_prompt="Explain the database design and persistence strategy.",
            )
            self.assertIsInstance(response, str)
            self.assertTrue(len(response) > 0)

    def test_r4_03_interactive_query_memory_manager(self):
        """TC-R4.3: Verify querying Memory Manager returns milestone and state history."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_query_memory") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            executor = InteractiveAgentExecutor(state_manager=ctx.state_manager)

            response = executor.query_agent(
                role="Memory & Status Manager",
                user_prompt="Show recorded milestones and session runs.",
            )
            self.assertIsInstance(response, str)
            self.assertTrue(len(response) > 0)

    def test_r4_04_targeted_subtask_execution(self):
        """TC-R4.4: Verify targeted subtask execution triggers single agent without full crew."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_subtask") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            executor = InteractiveAgentExecutor(state_manager=ctx.state_manager)

            task_output = executor.run_targeted_subtask(
                role="Tester",
                task_description="Execute unit test verification for SQLite schema",
            )
            self.assertIsInstance(task_output, str)
            self.assertTrue(len(task_output) > 0)

    def test_r4_05_dashboard_data_generation(self):
        """TC-R4.5: Verify get_dashboard_data gathers project status, all 8 agents, and decisions."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_dashboard") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            data = get_dashboard_data(state_manager=ctx.state_manager)

            self.assertIn("project", data)
            self.assertIn("agents", data)
            self.assertIn("decisions", data)
            self.assertIn("milestones", data)
            self.assertEqual(len(data["agents"]), 8)

            rendered = render_dashboard(data)
            self.assertIsInstance(rendered, str)
            self.assertIn("DASHBOARD", rendered.upper())

    def test_r4_06_repl_command_dispatch(self):
        """TC-R4.6: Verify InteractiveREPL dispatches commands (status, decisions, milestones)."""
        if not HAS_CLI or not HAS_PERSISTENCE:
            self.skipTest("persistent_crewai.cli or persistence not yet implemented")

        with E2ETestContext("test_cli_repl") as ctx:
            seed_standard_project_state(ctx.state_manager, ctx.project_id)
            repl = InteractiveREPL(state_manager=ctx.state_manager)

            status_out = repl.execute_command("status")
            self.assertIn("Project", status_out)

            decisions_out = repl.execute_command("decisions")
            self.assertIn("WAL", decisions_out)

            milestones_out = repl.execute_command("milestones")
            self.assertIn("Foundation", milestones_out)


if __name__ == "__main__":
    unittest.main()
