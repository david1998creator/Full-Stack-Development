"""
Adversarial Stress Testing Suite for Milestone 2 (Workspace & GitHub Tooling).
Tests boundary conditions, stress scenarios, and potential failure modes.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.tools import (
    GitHubAuthError,
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
    MockGitHubAdapter,
    RealGitHubAdapter,
    WorkspaceFileListTool,
    WorkspaceFilePatchTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriteTool,
    create_github_adapter,
    validate_safe_path,
)


def run_adversarial_tests():
    print("=" * 70)
    print("ADVERSARIAL STRESS TESTING - MILESTONE 2")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # STRESS 1: Advanced Path Traversal & Windows Specific Boundaries
    # -------------------------------------------------------------------------
    print("\n[STRESS 1] Testing Advanced Path Traversal Boundaries...")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        sub = root / "nested" / "inner"
        sub.mkdir(parents=True)

        evasion_paths = [
            "nested/inner/../../../../",
            "nested/./../../",
            "..\\..\\",
            "nested/inner/../../..",
            "./nested/../../",
        ]
        for p in evasion_paths:
            try:
                validate_safe_path(p, root)
                assert False, f"Failed to block evasion path: {p}"
            except PermissionError:
                pass
        print(f"  -> Successfully blocked {len(evasion_paths)} complex relative traversal patterns.")

        # Absolute paths outside sandbox
        external_paths = [
            r"C:\Windows\System32\cmd.exe",
            r"C:\Users",
            r"C:\Program Files",
        ]
        for ep in external_paths:
            try:
                validate_safe_path(ep, root)
                assert False, f"Failed to block external path: {ep}"
            except PermissionError:
                pass
        print(f"  -> Successfully blocked {len(external_paths)} absolute external OS paths.")

    # -------------------------------------------------------------------------
    # STRESS 2: Multibyte UTF-8 & Line Boundary Edge Cases in File Tools
    # -------------------------------------------------------------------------
    print("\n[STRESS 2] Testing Multibyte UTF-8 & Line Boundary Edge Cases...")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        reader = WorkspaceFileReadTool(workspace_root=root)
        writer = WorkspaceFileWriteTool(workspace_root=root)
        patcher = WorkspaceFilePatchTool(workspace_root=root)

        # Unicode content with emoji and Chinese characters
        unicode_content = "第1行: 🚀 Начало\n第2行: 中文测试 🌟\n第3行: Конец 🎉\n"
        writer.write_file("unicode.txt", unicode_content)
        read_back = reader.read_file("unicode.txt")
        assert read_back == unicode_content
        print("  -> Multibyte UTF-8 content read/write verified.")

        # Slicing with start_line beyond EOF raises ValueError because default end_line is len(lines) < start_line
        try:
            reader.read_file("unicode.txt", start_line=10)
            assert False, "Should raise ValueError when start_line > len(lines)"
        except ValueError as ve:
            assert "cannot be less than start_line" in str(ve)
            print("  -> start_line beyond EOF raises ValueError as expected.")

        # Slicing end_line beyond total lines
        end_beyond = reader.read_file("unicode.txt", start_line=2, end_line=100)
        assert end_beyond == "第2行: 中文测试 🌟\n第3行: Конец 🎉\n"
        print("  -> end_line beyond EOF clamped cleanly to end of file.")

        # Patching multibyte unicode
        patcher.patch_file(
            "unicode.txt",
            search_block="第2行: 中文测试 🌟\n",
            replace_block="第2行: 替换成功 ⚡\n",
        )
        patched_read = reader.read_file("unicode.txt")
        assert "第2行: 替换成功 ⚡\n" in patched_read
        print("  -> Multibyte UTF-8 patching verified.")

    # -------------------------------------------------------------------------
    # STRESS 3: MockGitHub PR State Transitions & Commit Chaining
    # -------------------------------------------------------------------------
    print("\n[STRESS 3] Testing MockGitHub PR State Transitions & Commit Chaining...")
    with tempfile.TemporaryDirectory() as td:
        json_file = Path(td) / "stress_gh.json"
        adapter = MockGitHubAdapter(storage_path=json_file, default_repo="org/stress-repo")

        # Create feature branch
        adapter.create_branch("feature/pipeline", base="main")

        # Chain 5 commits
        for i in range(1, 6):
            c = adapter.create_commit("feature/pipeline", message=f"Commit #{i}", files=[f"file_{i}.py"])
            assert len(c["sha"]) == 8

        commits = adapter.list_commits(branch="feature/pipeline", limit=10)
        assert len(commits) == 5
        # Most recent first
        assert commits[0]["message"] == "Commit #5"
        assert commits[4]["message"] == "Commit #1"
        print("  -> Commit chain correctly ordered (most recent first).")

        # Create PR
        pr = adapter.create_pull_request(
            title="Pipeline integration",
            head_branch="feature/pipeline",
            base_branch="main",
        )
        pr_num = pr["number"]

        # Filter PR lists
        open_prs = adapter.list_pull_requests(state="open")
        assert len(open_prs) == 1
        merged_prs = adapter.list_pull_requests(state="merged")
        assert len(merged_prs) == 0

        # Merge PR
        m = adapter.merge_pull_request(pr_num)
        assert m["merged"] is True

        open_prs_post = adapter.list_pull_requests(state="open")
        assert len(open_prs_post) == 0
        merged_prs_post = adapter.list_pull_requests(state="merged")
        assert len(merged_prs_post) == 1
        print("  -> PR list state filtering ('open', 'merged') verified.")

        # Repo status reflection
        st = adapter.get_repo_status()
        assert st["open_pr_count"] == 0
        assert st["pull_request_count"] == 1
        assert st["commit_count"] >= 6  # Initial commit + 5 feature commits
        print("  -> Repo status accurately reflects commit and PR counts.")

    # -------------------------------------------------------------------------
    # STRESS 4: RealGitHubAdapter Network Exception Handling & Malformed Payloads
    # -------------------------------------------------------------------------
    print("\n[STRESS 4] Testing RealGitHubAdapter Malformed Responses & Network Errors...")
    session = MagicMock()
    adapter = RealGitHubAdapter(token="ghp_stress_token", default_repo="org/app", session=session)

    # 4.1 Non-JSON body on HTTP 500 error
    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.json.side_effect = ValueError("No JSON")
    resp_500.text = "Internal Server Error HTML page"
    session.request.return_value = resp_500

    try:
        adapter.get_repo_status()
        assert False, "Should raise GitHubError on 500"
    except GitHubError as ge:
        assert ge.status_code == 500
        print("  -> Handled non-JSON error response gracefully as GitHubError [500].")

    # 4.2 Network timeout exception
    session.request.side_effect = Exception("HTTPSConnectionPool: Read timed out")
    try:
        adapter.get_issue(1)
        assert False, "Should translate timeout into GitHubError"
    except GitHubError as ge:
        assert ge.status_code == 408
        assert "timed out" in ge.message.lower()
        print("  -> Network timeout mapped cleanly to GitHubError [408].")

    # 4.3 Connection failure
    session.request.side_effect = Exception("Failed to connect to api.github.com: Name or service not known")
    try:
        adapter.get_issue(1)
        assert False, "Should translate connection failure into GitHubError"
    except GitHubError as ge:
        assert ge.status_code == 503
        assert "connect" in ge.message.lower()
        print("  -> Connection failure mapped cleanly to GitHubError [503].")

    print("\n" + "=" * 70)
    print("ALL ADVERSARIAL STRESS TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_adversarial_tests()
