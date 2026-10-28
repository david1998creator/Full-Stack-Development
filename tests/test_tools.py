"""
Comprehensive unit test suite for Milestone 2: Workspace & GitHub Tooling.
Verifies sandboxed workspace file operations, canonical path validation,
MockGitHubAdapter persistence and determinism, RealGitHubAdapter REST calls
and error mappings, create_github_adapter factory, and role tool assignments.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.tools import (
    BranchCreateTool,
    CommitCreateTool,
    GitHubAdapterInterface,
    GitHubAuthError,
    GitHubConflictError,
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
    IssueCreateTool,
    MockGitHubAdapter,
    PRCreateTool,
    QueryMemoryTool,
    RealGitHubAdapter,
    RecordDecisionTool,
    RecordMilestoneTool,
    RepoStatusTool,
    WorkspaceFileListTool,
    WorkspaceFilePatchTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriteTool,
    create_github_adapter,
    get_agent_tools,
    validate_safe_path,
)


class TestWorkspacePathValidation(unittest.TestCase):
    """TC-PATH: Canonical path containment and security boundary tests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_relative_path(self):
        resolved = validate_safe_path("src/app.py", self.workspace_root)
        self.assertEqual(resolved, self.workspace_root / "src" / "app.py")
        self.assertTrue(resolved.is_relative_to(self.workspace_root))

    def test_valid_current_dir(self):
        resolved = validate_safe_path(".", self.workspace_root)
        self.assertEqual(resolved, self.workspace_root)

    def test_valid_absolute_path_inside_root(self):
        inside = self.workspace_root / "nested" / "file.txt"
        resolved = validate_safe_path(inside, self.workspace_root)
        self.assertEqual(resolved, inside)

    def test_upward_traversal_blocked(self):
        with self.assertRaises(PermissionError):
            validate_safe_path("../../etc/shadow", self.workspace_root)

    def test_windows_system_path_blocked(self):
        with self.assertRaises(PermissionError):
            validate_safe_path(r"..\..\Windows\System32\drivers\etc\hosts", self.workspace_root)

    def test_absolute_external_path_blocked(self):
        external = Path(tempfile.gettempdir()).resolve()
        # If external happens to be parent or ancestor, go higher
        if self.workspace_root.is_relative_to(external):
            external = Path(r"C:\NonExistentTestExternalDir")
        with self.assertRaises(PermissionError):
            validate_safe_path(external, self.workspace_root)

    def test_empty_and_whitespace_paths_raise_value_error(self):
        with self.assertRaises(ValueError):
            validate_safe_path("", self.workspace_root)
        with self.assertRaises(ValueError):
            validate_safe_path("   ", self.workspace_root)
        with self.assertRaises(ValueError):
            validate_safe_path(None, self.workspace_root)

    def test_null_byte_in_path_raises_value_error(self):
        with self.assertRaises(ValueError):
            validate_safe_path("file\x00name.txt", self.workspace_root)


class TestWorkspaceFileTools(unittest.TestCase):
    """TC-TOOL-FS: Unit tests for Read, Write, List, and Patch workspace tools."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.mock_sm = MagicMock()
        self.mock_sm.record_event = MagicMock(return_value="trc_test_01")

        self.reader = WorkspaceFileReadTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.writer = WorkspaceFileWriteTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.lister = WorkspaceFileListTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.patcher = WorkspaceFilePatchTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_and_read_full_content(self):
        content = "Line 1\nLine 2\nLine 3\n"
        res = self.writer.write_file("docs/readme.txt", content)
        self.assertIn("written", res.lower())

        read_back = self.reader.read_file("docs/readme.txt")
        self.assertEqual(read_back, content)
        self.mock_sm.record_event.assert_called()

    def test_read_line_slice(self):
        lines = [f"Line {i}\n" for i in range(1, 11)]
        self.writer.write_file("numbers.txt", "".join(lines))

        # 1-indexed lines 3 to 5 inclusive
        slice_content = self.reader.read_file("numbers.txt", start_line=3, end_line=5)
        self.assertEqual(slice_content, "Line 3\nLine 4\nLine 5\n")

        # only start_line
        tail_content = self.reader.read_file("numbers.txt", start_line=9)
        self.assertEqual(tail_content, "Line 9\nLine 10\n")

        # only end_line
        head_content = self.reader.read_file("numbers.txt", end_line=2)
        self.assertEqual(head_content, "Line 1\nLine 2\n")

    def test_read_invalid_bounds_raises_value_error(self):
        self.writer.write_file("test.txt", "abc")
        with self.assertRaises(ValueError):
            self.reader.read_file("test.txt", start_line=0)
        with self.assertRaises(ValueError):
            self.reader.read_file("test.txt", start_line=5, end_line=3)

    def test_read_nonexistent_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.reader.read_file("does_not_exist.txt")

    def test_read_directory_raises_is_a_directory_error(self):
        (self.workspace_root / "subdir").mkdir()
        with self.assertRaises(IsADirectoryError):
            self.reader.read_file("subdir")

    def test_write_overwrite_control(self):
        self.writer.write_file("locked.txt", "initial", overwrite=True)
        with self.assertRaises(FileExistsError):
            self.writer.write_file("locked.txt", "second", overwrite=False)

        # Overwrite=True succeeds
        self.writer.write_file("locked.txt", "second", overwrite=True)
        self.assertEqual(self.reader.read_file("locked.txt"), "second")

    def test_list_files_glob_and_recursive(self):
        self.writer.write_file("root.py", "# py")
        self.writer.write_file("root.txt", "text")
        self.writer.write_file("sub1/mod.py", "# mod")
        self.writer.write_file("sub1/sub2/deep.py", "# deep")

        # Recursive py files
        all_py = self.lister.list_files(".", pattern="*.py", recursive=True)
        py_paths = [f["path"] for f in all_py]
        self.assertIn("root.py", py_paths)
        self.assertTrue(any("mod.py" in p for p in py_paths))
        self.assertTrue(any("deep.py" in p for p in py_paths))
        self.assertFalse(any("root.txt" in p for p in py_paths))

        # Non-recursive
        top_py = self.lister.list_files(".", pattern="*.py", recursive=False)
        top_paths = [f["path"] for f in top_py]
        self.assertIn("root.py", top_paths)
        self.assertFalse(any("deep.py" in p for p in top_paths))

    def test_list_files_errors(self):
        with self.assertRaises(FileNotFoundError):
            self.lister.list_files("nonexistent_dir")
        self.writer.write_file("a_file.txt", "content")
        with self.assertRaises(NotADirectoryError):
            self.lister.list_files("a_file.txt")

    def test_patch_file_success(self):
        self.writer.write_file("service.py", "PORT = 3000\nHOST = 'localhost'\n")
        res = self.patcher.patch_file(
            file_path="service.py",
            search_block="PORT = 3000",
            replace_block="PORT = 8080",
        )
        self.assertIn("patched", res.lower())
        updated = self.reader.read_file("service.py")
        self.assertIn("PORT = 8080", updated)
        self.assertNotIn("PORT = 3000", updated)

    def test_patch_file_zero_matches_raises_value_error(self):
        self.writer.write_file("service.py", "PORT = 3000\n")
        with self.assertRaises(ValueError) as cm:
            self.patcher.patch_file("service.py", search_block="MISSING_BLOCK", replace_block="NEW")
        self.assertIn("not found", str(cm.exception).lower())

    def test_patch_file_ambiguous_matches_raises_value_error(self):
        self.writer.write_file("duplicate.py", "x = 1\nx = 1\n")
        with self.assertRaises(ValueError) as cm:
            self.patcher.patch_file("duplicate.py", search_block="x = 1", replace_block="x = 2")
        self.assertIn("ambiguous", str(cm.exception).lower())

    def test_base_tool_run_fallback_returns_error_string(self):
        # Calling _run with invalid path returns Error string, does not crash
        res = self.reader.run(file_path="../../forbidden.txt")
        self.assertIn("Error:", res)

        patch_res = self.patcher.run(
            file_path="nonexistent.py", search_block="x", replace_block="y"
        )
        self.assertIn("Error:", patch_res)


class TestMockGitHubAdapter(unittest.TestCase):
    """TC-MOCK-GH: Unit tests for MockGitHubAdapter."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "mock_github.json"
        self.repo = "test-org/repo-alpha"
        self.adapter = MockGitHubAdapter(
            storage_path=self.storage_path, default_repo=self.repo
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_state_and_default_branch(self):
        branches = self.adapter.list_branches()
        branch_names = [b["name"] for b in branches]
        self.assertIn("main", branch_names)

        status = self.adapter.get_repo_status()
        self.assertEqual(status["default_branch"], "main")
        self.assertEqual(status["issue_count"], 0)
        self.assertEqual(status["pull_request_count"], 0)

    def test_issue_lifecycle_and_deterministic_ids(self):
        iss1 = self.adapter.create_issue(title="First Issue", body="Description 1", labels=["bug"])
        iss2 = self.adapter.create_issue(title="Second Issue", body="Description 2")

        self.assertEqual(iss1["number"], 1)
        self.assertEqual(iss2["number"], 2)

        fetched = self.adapter.get_issue(1)
        self.assertEqual(fetched["title"], "First Issue")
        self.assertEqual(fetched["state"], "open")

        # Update
        updated = self.adapter.update_issue(1, state="closed", comment="Resolved in PR #101")
        self.assertEqual(updated["state"], "closed")
        self.assertEqual(len(updated["comments"]), 1)

        # Nonexistent issue raises 404
        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.get_issue(9999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_list_issues_filtering(self):
        self.adapter.create_issue(title="Issue A", labels=["backend"])
        self.adapter.create_issue(title="Issue B", labels=["frontend"])
        self.adapter.update_issue(1, state="closed")

        open_issues = self.adapter.list_issues(state="open")
        self.assertEqual(len(open_issues), 1)
        self.assertEqual(open_issues[0]["title"], "Issue B")

        closed_issues = self.adapter.list_issues(state="closed")
        self.assertEqual(len(closed_issues), 1)
        self.assertEqual(closed_issues[0]["title"], "Issue A")

        label_issues = self.adapter.list_issues(state="all", labels=["backend"])
        self.assertEqual(len(label_issues), 1)
        self.assertEqual(label_issues[0]["title"], "Issue A")

    def test_branch_creation_and_duplicate_rejection(self):
        b1 = self.adapter.create_branch(branch_name="feature/login", base="main")
        self.assertEqual(b1["name"], "feature/login")

        # Duplicate branch raises 422
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_branch(branch_name="feature/login", base="main")
        self.assertEqual(cm.exception.status_code, 422)

        # Missing base branch raises 422
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_branch(branch_name="feature/other", base="nonexistent-base")
        self.assertEqual(cm.exception.status_code, 422)

    def test_commit_creation_and_listing(self):
        self.adapter.create_branch(branch_name="feat/db", base="main")
        c1 = self.adapter.create_commit(
            branch="feat/db", message="Add schema DDL", files=["schema.sql"]
        )
        self.assertEqual(len(c1["sha"]), 8)

        # Commit to nonexistent branch raises 422
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_commit(branch="missing-branch", message="bad commit")
        self.assertEqual(cm.exception.status_code, 422)

        commits = self.adapter.list_commits(branch="feat/db")
        self.assertTrue(len(commits) >= 1)
        self.assertEqual(commits[0]["message"], "Add schema DDL")

    def test_pull_request_lifecycle_and_merge(self):
        self.adapter.create_branch(branch_name="feat/auth", base="main")
        pr = self.adapter.create_pull_request(
            title="Implement Auth",
            body="JWT auth module",
            head_branch="feat/auth",
            base_branch="main",
        )
        self.assertEqual(pr["number"], 101)
        self.assertEqual(pr["state"], "open")

        # Merge PR
        merge_res = self.adapter.merge_pull_request(pr_number=101, commit_message="Merge #101")
        self.assertTrue(merge_res["merged"])
        self.assertIsNotNone(merge_res["sha"])

        # Second merge on already merged PR raises 405
        with self.assertRaises(GitHubMethodNotAllowedError) as cm:
            self.adapter.merge_pull_request(pr_number=101)
        self.assertEqual(cm.exception.status_code, 405)

    def test_pull_request_missing_head_raises_422(self):
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_pull_request(
                title="Invalid PR",
                head_branch="nonexistent-head",
                base_branch="main",
            )
        self.assertEqual(cm.exception.status_code, 422)

    def test_persistence_across_separate_instances(self):
        # Mutate in instance 1
        self.adapter.create_branch(branch_name="feat/persisted", base="main")
        self.adapter.create_issue(title="Persisted Issue")

        # Reload from same disk file in instance 2
        adapter2 = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
        branches = [b["name"] for b in adapter2.list_branches()]
        self.assertIn("feat/persisted", branches)

        issue = adapter2.get_issue(1)
        self.assertEqual(issue["title"], "Persisted Issue")

    def test_thread_safety_concurrent_issues(self):
        threads = []
        created_numbers = []
        lock = threading.Lock()

        def worker(idx: int):
            iss = self.adapter.create_issue(title=f"Concurrent Issue {idx}")
            with lock:
                created_numbers.append(iss["number"])

        for i in range(15):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All 15 issues created with unique sequential numbers 1 to 15
        self.assertEqual(len(created_numbers), 15)
        self.assertEqual(len(set(created_numbers)), 15)
        self.assertEqual(sorted(created_numbers), list(range(1, 16)))


class TestRealGitHubAdapter(unittest.TestCase):
    """TC-REAL-GH: Unit tests for RealGitHubAdapter with mocked network calls."""

    def setUp(self):
        self.mock_session = MagicMock()
        self.adapter = RealGitHubAdapter(
            token="ghp_mock_token_1234567890",
            default_repo="owner/my-repo",
            session=self.mock_session,
        )

    def test_missing_token_raises_auth_error(self):
        with self.assertRaises(GitHubAuthError):
            RealGitHubAdapter(token="")
        with self.assertRaises(GitHubAuthError):
            RealGitHubAdapter(token="   ")

    def test_get_repo_status_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "full_name": "owner/my-repo",
            "default_branch": "main",
            "open_issues_count": 5,
            "private": False,
            "html_url": "https://github.com/owner/my-repo",
        }
        self.mock_session.request.return_value = mock_resp

        status = self.adapter.get_repo_status()
        self.assertEqual(status["name"], "owner/my-repo")
        self.assertEqual(status["default_branch"], "main")
        self.assertEqual(status["open_issues_count"], 5)

    def test_create_issue_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "number": 42,
            "title": "Bug in parser",
            "state": "open",
        }
        self.mock_session.request.return_value = mock_resp

        res = self.adapter.create_issue(title="Bug in parser", body="Details", labels=["bug"])
        self.assertEqual(res["number"], 42)
        self.assertEqual(res["title"], "Bug in parser")

    def test_list_issues_filters_out_prs(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"number": 1, "title": "Real Issue", "state": "open"},
            {"number": 2, "title": "Pull Request disguised as issue", "pull_request": {}},
            {"number": 3, "title": "Another Issue", "state": "open"},
        ]
        self.mock_session.request.return_value = mock_resp

        issues = self.adapter.list_issues(state="open")
        self.assertEqual(len(issues), 2)
        self.assertEqual([i["number"] for i in issues], [1, 3])

    def test_create_branch_two_step_resolution(self):
        # Step 1: branch SHA lookup; Step 2: git ref creation
        branch_resp = MagicMock()
        branch_resp.status_code = 200
        branch_resp.json.return_value = {"commit": {"sha": "sha_base_123"}}

        ref_resp = MagicMock()
        ref_resp.status_code = 201
        ref_resp.json.return_value = {"ref": "refs/heads/feature/x"}

        self.mock_session.request.side_effect = [branch_resp, ref_resp]

        res = self.adapter.create_branch(branch_name="feature/x", base="main")
        self.assertEqual(res["name"], "feature/x")
        self.assertEqual(res["sha"], "sha_base_123")

    def test_create_and_merge_pull_request(self):
        pr_resp = MagicMock()
        pr_resp.status_code = 201
        pr_resp.json.return_value = {
            "number": 105,
            "title": "Add feature",
            "state": "open",
            "head": {"ref": "feature/x"},
            "base": {"ref": "main"},
        }

        merge_resp = MagicMock()
        merge_resp.status_code = 200
        merge_resp.json.return_value = {
            "sha": "merge_commit_sha_999",
            "merged": True,
            "message": "Pull Request successfully merged",
        }

        self.mock_session.request.side_effect = [pr_resp, merge_resp]

        pr = self.adapter.create_pull_request(
            title="Add feature", head_branch="feature/x", base_branch="main"
        )
        self.assertEqual(pr["number"], 105)

        merged = self.adapter.merge_pull_request(pr_number=105, commit_message="Merge #105")
        self.assertTrue(merged["merged"])
        self.assertEqual(merged["sha"], "merge_commit_sha_999")

    def test_401_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"message": "Bad credentials"}
        self.mock_session.request.return_value = mock_resp

        with self.assertRaises(GitHubAuthError) as cm:
            self.adapter.get_repo_status()
        self.assertEqual(cm.exception.status_code, 401)

    def test_403_rate_limit_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1730000000"}
        mock_resp.json.return_value = {"message": "API rate limit exceeded for user"}
        self.mock_session.request.return_value = mock_resp

        with self.assertRaises(GitHubRateLimitError) as cm:
            self.adapter.get_repo_status()
        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(cm.exception.reset_time, 1730000000)

    def test_404_not_found_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"message": "Not Found"}
        self.mock_session.request.return_value = mock_resp

        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.get_issue(999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_405_method_not_allowed_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 405
        mock_resp.json.return_value = {"message": "Pull Request is not mergeable"}
        self.mock_session.request.return_value = mock_resp

        with self.assertRaises(GitHubMethodNotAllowedError) as cm:
            self.adapter.merge_pull_request(pr_number=10)
        self.assertEqual(cm.exception.status_code, 405)

    def test_422_validation_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"message": "Reference already exists"}
        self.mock_session.request.return_value = mock_resp

        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_branch("feature/duplicate")
        self.assertEqual(cm.exception.status_code, 422)


class TestGitHubAdapterFactory(unittest.TestCase):
    """TC-FACTORY: Unit tests for create_github_adapter factory."""

    def test_force_mock_returns_mock_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "mock.json"
            adapter = create_github_adapter(force_mock=True, storage_path=storage)
            self.assertIsInstance(adapter, MockGitHubAdapter)

    def test_no_token_defaults_to_mock_adapter(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = create_github_adapter()
            self.assertIsInstance(adapter, MockGitHubAdapter)

    def test_whitespace_token_defaults_to_mock_adapter(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "   "}, clear=True):
            adapter = create_github_adapter()
            self.assertIsInstance(adapter, MockGitHubAdapter)

    def test_explicit_token_returns_real_adapter(self):
        adapter = create_github_adapter(token="ghp_explicit_token_123")
        self.assertIsInstance(adapter, RealGitHubAdapter)

    def test_env_token_returns_real_adapter(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_env_token_456"}, clear=True):
            adapter = create_github_adapter()
            self.assertIsInstance(adapter, RealGitHubAdapter)

    def test_gh_token_alias_returns_real_adapter(self):
        with patch.dict(os.environ, {"GH_TOKEN": "ghp_alias_token_789"}, clear=True):
            adapter = create_github_adapter()
            self.assertIsInstance(adapter, RealGitHubAdapter)


class TestRoleToolAssignments(unittest.TestCase):
    """TC-ROLES: Unit tests for get_agent_tools and agent tool wrappers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_developer_tools(self):
        tools = get_agent_tools("Developer", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("write_workspace_file", tool_names)
        self.assertIn("patch_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("spawn_subagent_assistant", tool_names)
        self.assertEqual(len(tools), 5)

    def test_tester_tools(self):
        tools = get_agent_tools("Tester", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("run_tests", tool_names)
        self.assertIn("browser_smoke_test", tool_names)
        self.assertEqual(len(tools), 4)

    def test_github_manager_tools(self):
        tools = get_agent_tools("GitHub Manager", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("github_issue_tool", tool_names)
        self.assertIn("github_pr_tool", tool_names)
        self.assertIn("github_branch_tool", tool_names)
        self.assertIn("github_commit_tool", tool_names)
        self.assertIn("github_repo_status_tool", tool_names)
        self.assertIn("local_git_tool", tool_names)
        self.assertEqual(len(tools), 6)

    def test_memory_manager_tools(self):
        tools = get_agent_tools("Memory & Status Manager", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("record_decision", tool_names)
        self.assertIn("record_milestone", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertEqual(len(tools), 3)

    def test_architect_tools(self):
        tools = get_agent_tools("Architect", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("write_workspace_file", tool_names)
        self.assertIn("record_decision", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertEqual(len(tools), 5)

    def test_designer_tools(self):
        tools = get_agent_tools("Designer", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("write_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertEqual(len(tools), 3)

    def test_reviewer_tools(self):
        tools = get_agent_tools("Reviewer", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertIn("browser_smoke_test", tool_names)
        self.assertEqual(len(tools), 4)

    def test_coordinator_tools(self):
        tools = get_agent_tools("Coordinator", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertIn("record_milestone", tool_names)
        self.assertEqual(len(tools), 3)

    def test_database_engineer_tools(self):
        tools = get_agent_tools("Database Engineer", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("write_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("record_decision", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertEqual(len(tools), 5)

    def test_security_auditor_tools(self):
        tools = get_agent_tools("Security Auditor", workspace_root=self.workspace_root)
        tool_names = [t.name for t in tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)
        self.assertIn("record_decision", tool_names)
        self.assertIn("query_memory", tool_names)
        self.assertEqual(len(tools), 4)

    def test_unknown_role_raises_key_error(self):
        with self.assertRaises(KeyError):
            get_agent_tools("InvalidRole")


if __name__ == "__main__":
    unittest.main()
