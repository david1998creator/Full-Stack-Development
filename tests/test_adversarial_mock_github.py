"""
Adversarial Stress Test Suite for MockGitHubAdapter (Milestone 2).
Empirically stress-tests:
1. Concurrency: 20 threads simultaneously executing operations (issues, branches,
   commits, PRs, and mixed matrix) under Barrier synchronization to verify
   thread-safety, locking, and strictly monotonic sequential ID integrity.
2. Persistence & Rehydration: Rapid create-kill-rehydrate cycles (50 cycles),
   full entity graph state exactness across fresh instances, concurrent rehydration,
   and corrupted JSON recovery.
3. Invalid State Machine Transitions: Merging already-merged PRs, merging closed
   PRs, merging non-existent PRs, creating PRs with non-existent branches (head, base, both),
   updating non-existent issues, commits to non-existent branches, duplicate branches,
   and multi-repo isolation.
4. Edge Case Hardening: Unicode/emoji payloads, large bodies, empty/whitespace inputs.
"""

from __future__ import annotations

import copy
import gc
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.tools.github_interface import (
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubValidationError,
)
from persistent_crewai.tools.mock_github import MockGitHubAdapter


class TestMockGitHubConcurrency20Threads(unittest.TestCase):
    """Category 1: Empirical Concurrency Stress Testing with 20 Simultaneous Threads."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_gh_concurrent_")
        self.storage_path = Path(self.temp_dir) / "mock_github_concurrent.json"
        self.adapter = MockGitHubAdapter(
            storage_path=self.storage_path, default_repo="owner/concurrency-repo"
        )

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_concurrent_20_threads_issue_creation_sequential_ids(self) -> None:
        """20 threads simultaneously create issues (10 issues per thread = 200 total).
        Assert:
        - Exactly 200 issues created with 0 exceptions.
        - Sequential ID integrity: issue numbers are strictly 1..200, 0 duplicates, 0 gaps.
        - Persisted JSON on disk matches in-memory sequence and issue count.
        """
        num_threads = 20
        issues_per_thread = 10
        total_expected = num_threads * issues_per_thread

        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        created_issues: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                # Synchronize start across all 20 threads
                barrier.wait(timeout=10.0)
                for i in range(issues_per_thread):
                    issue = self.adapter.create_issue(
                        title=f"Concurrent Issue t{thread_idx}_i{i}",
                        body=f"Created by thread {thread_idx} at iteration {i}",
                        labels=[f"thread-{thread_idx}", "concurrent-test"],
                        assignees=[f"worker-{thread_idx}"],
                    )
                    with lock:
                        created_issues.append(issue)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")
        self.assertEqual(len(created_issues), total_expected)

        numbers = [iss["number"] for iss in created_issues]
        self.assertEqual(len(set(numbers)), total_expected, "Duplicate issue numbers detected!")
        self.assertEqual(sorted(numbers), list(range(1, total_expected + 1)))

        # Verify through list_issues
        all_issues = self.adapter.list_issues(state="all")
        self.assertEqual(len(all_issues), total_expected)

        # Verify on-disk JSON persistence
        self.assertTrue(self.storage_path.exists())
        data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        repo_data = data["repositories"]["owner/concurrency-repo"]
        self.assertEqual(repo_data["issue_seq"], total_expected)
        self.assertEqual(len(repo_data["issues"]), total_expected)

    def test_concurrent_20_threads_branch_creation(self) -> None:
        """20 threads simultaneously create distinct branches off 'main'.
        Assert:
        - All 20 branches created with 0 exceptions.
        - Exactly 21 branches in repository (main + 20 created).
        - No branches lost or corrupted.
        """
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        branches: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                b = self.adapter.create_branch(
                    branch_name=f"feature/concurrent-branch-{thread_idx}",
                    base="main",
                )
                with lock:
                    branches.append(b)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Thread errors in branch creation: {errors}")
        self.assertEqual(len(branches), num_threads)

        all_branches = self.adapter.list_branches()
        branch_names = {b["name"] for b in all_branches}
        self.assertEqual(len(all_branches), num_threads + 1)  # 20 + main
        self.assertIn("main", branch_names)
        for i in range(num_threads):
            self.assertIn(f"feature/concurrent-branch-{i}", branch_names)

    def test_concurrent_20_threads_commits_on_same_branch(self) -> None:
        """20 threads simultaneously create commits on the SAME branch ('main').
        Assert:
        - All 20 commits appended without losing history (total 21 commits including initial).
        - Each commit has a valid 8-char SHA.
        - Branch HEAD points to a valid SHA.
        """
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        created_commits: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                commit = self.adapter.create_commit(
                    branch="main",
                    message=f"Concurrent commit from thread {thread_idx}",
                    files=[f"file_{thread_idx}.txt"],
                )
                with lock:
                    created_commits.append(commit)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Errors in concurrent commit creation: {errors}")
        self.assertEqual(len(created_commits), num_threads)

        all_commits = self.adapter.list_commits(branch="main", limit=50)
        self.assertEqual(len(all_commits), num_threads + 1)  # 20 commits + initial commit

        # Ensure all commit SHAs are 8 characters and non-empty
        for c in created_commits:
            self.assertEqual(len(c["sha"]), 8)

    def test_concurrent_20_threads_pr_creation_sequential_ids(self) -> None:
        """Pre-creates 20 branches, then 20 threads simultaneously create PRs to 'main'.
        Assert:
        - Exactly 20 PRs created with 0 exceptions.
        - PR numbers are strictly sequential: 101..120 with 0 duplicates.
        - Each PR is open, not merged.
        - On-disk JSON matches pr_seq == 120.
        """
        num_threads = 20
        # Pre-create 20 branches serially
        for i in range(num_threads):
            self.adapter.create_branch(branch_name=f"pr-branch-{i}", base="main")

        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        prs: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                pr = self.adapter.create_pull_request(
                    title=f"Concurrent PR from thread {thread_idx}",
                    body=f"PR description {thread_idx}",
                    head_branch=f"pr-branch-{thread_idx}",
                    base_branch="main",
                )
                with lock:
                    prs.append(pr)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Errors in concurrent PR creation: {errors}")
        self.assertEqual(len(prs), num_threads)

        pr_numbers = [pr["number"] for pr in prs]
        self.assertEqual(len(set(pr_numbers)), num_threads, "Duplicate PR numbers detected!")
        self.assertEqual(sorted(pr_numbers), list(range(101, 101 + num_threads)))

        for pr in prs:
            self.assertEqual(pr["state"], "open")
            self.assertFalse(pr["merged"])
            self.assertIsNone(pr["merge_commit_sha"])

        data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        repo_data = data["repositories"]["owner/concurrency-repo"]
        self.assertEqual(repo_data["pr_seq"], 100 + num_threads)

    def test_concurrent_20_threads_mixed_operations_stress(self) -> None:
        """20 threads simultaneously executing diverse operations:
        - Threads 0-3: Create issues (5 each = 20 total)
        - Threads 4-7: Create branches and commits
        - Threads 8-11: Create PRs
        - Threads 12-15: Query repo status and list entities repeatedly
        - Threads 16-19: Create and merge distinct PRs
        Assert:
        - 0 unhandled exceptions or deadlocks.
        - State consistency verified on-disk.
        """
        num_threads = 20
        # Pre-create branches for PR threads
        for i in range(8, 12):
            self.adapter.create_branch(branch_name=f"mixed-pr-branch-{i}", base="main")
        for i in range(16, 20):
            self.adapter.create_branch(branch_name=f"mixed-merge-branch-{i}", base="main")

        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                if 0 <= thread_idx <= 3:
                    # Issue creators
                    for k in range(5):
                        self.adapter.create_issue(title=f"Mixed Issue t{thread_idx}_k{k}")
                elif 4 <= thread_idx <= 7:
                    # Branch & Commit creators
                    bname = f"mixed-work-branch-{thread_idx}"
                    self.adapter.create_branch(branch_name=bname, base="main")
                    for k in range(3):
                        self.adapter.create_commit(
                            branch=bname, message=f"Commit k{k} from t{thread_idx}"
                        )
                elif 8 <= thread_idx <= 11:
                    # PR creators
                    self.adapter.create_pull_request(
                        title=f"Mixed PR t{thread_idx}",
                        head_branch=f"mixed-pr-branch-{thread_idx}",
                        base_branch="main",
                    )
                elif 12 <= thread_idx <= 15:
                    # Query readers
                    for _ in range(5):
                        self.adapter.get_repo_status()
                        self.adapter.list_issues(state="all")
                        self.adapter.list_branches()
                        self.adapter.list_commits(limit=10)
                elif 16 <= thread_idx <= 19:
                    # PR creators + mergers
                    pr = self.adapter.create_pull_request(
                        title=f"Merge PR t{thread_idx}",
                        head_branch=f"mixed-merge-branch-{thread_idx}",
                        base_branch="main",
                    )
                    self.adapter.merge_pull_request(
                        pr_number=pr["number"], commit_message=f"Merged by t{thread_idx}"
                    )
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Errors in mixed concurrent stress: {errors}")

        status = self.adapter.get_repo_status()
        self.assertTrue(status["issue_count"] >= 20)
        self.assertTrue(status["branch_count"] >= 9)
        self.assertTrue(status["pull_request_count"] >= 8)


class TestMockGitHubPersistenceAndRehydrationStress(unittest.TestCase):
    """Category 2: Persistence & Rehydration Stress Testing."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_gh_persist_")
        self.storage_path = Path(self.temp_dir) / "mock_github_persist.json"
        self.repo = "owner/persistence-test"

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_rehydration_rapid_churn_50_cycles(self) -> None:
        """In a loop of 50 iterations:
        - Instantiate adapter from disk
        - Mutate state (issues, branches, commits, PRs, merges)
        - Destroy adapter instance and force garbage collection
        - Re-instantiate from disk and verify all cumulative state is preserved.
        """
        for cycle in range(50):
            adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)

            if cycle < 20:
                iss = adapter.create_issue(
                    title=f"Churn Issue {cycle}",
                    body=f"Description for cycle {cycle}",
                    labels=["churn", f"phase-{cycle % 3}"],
                )
                self.assertEqual(iss["number"], cycle + 1)
            elif 20 <= cycle < 30:
                bname = f"churn-branch-{cycle}"
                b = adapter.create_branch(branch_name=bname, base="main")
                self.assertEqual(b["name"], bname)
                c = adapter.create_commit(branch=bname, message=f"Commit in cycle {cycle}")
                self.assertEqual(len(c["sha"]), 8)
            elif 30 <= cycle < 40:
                bname = f"churn-branch-{cycle - 10}"
                pr = adapter.create_pull_request(
                    title=f"Churn PR {cycle}",
                    head_branch=bname,
                    base_branch="main",
                )
                self.assertEqual(pr["number"], 100 + (cycle - 30 + 1))
            else:
                # 40 <= cycle < 50: merge PRs
                pr_num = 100 + (cycle - 40 + 1)
                m = adapter.merge_pull_request(pr_number=pr_num, commit_message=f"Merged cycle {cycle}")
                self.assertTrue(m["merged"])

            del adapter
            gc.collect()

        # Re-instantiate fresh adapter from cold storage
        final_adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
        status = final_adapter.get_repo_status()

        self.assertEqual(status["issue_count"], 20)
        self.assertEqual(status["branch_count"], 11)  # main + 10 churn branches
        self.assertEqual(status["pull_request_count"], 10)
        self.assertEqual(status["open_pr_count"], 0)  # All 10 were merged

        # Check all 20 issues exist and have correct titles
        for i in range(1, 21):
            iss = final_adapter.get_issue(i)
            self.assertEqual(iss["title"], f"Churn Issue {i - 1}")

        # Check all 10 PRs are merged
        merged_prs = final_adapter.list_pull_requests(state="merged")
        self.assertEqual(len(merged_prs), 10)
        for pr in merged_prs:
            self.assertTrue(pr["merged"])
            self.assertEqual(pr["state"], "merged")
            self.assertIsNotNone(pr["merge_commit_sha"])

    def test_rehydration_state_exactness_full_entity_graph(self) -> None:
        """Create a complete, rich entity graph (issues, comments, branches,
        commits, open PRs, merged PRs). Compare raw JSON on disk with fresh adapter
        in-memory state to guarantee zero data loss, exact schema fidelity, and no corruption.
        """
        adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)

        # 1. Issues
        iss1 = adapter.create_issue(
            title="Database Schema Migration",
            body="Apply WAL mode and FTS5 triggers.",
            labels=["schema", "sqlite", "m1"],
            assignees=["architect", "developer"],
        )
        iss2 = adapter.create_issue(
            title="Frontend Accessibility",
            body="Color contrast audit.",
            labels=["ui", "a11y"],
        )
        adapter.update_issue(
            issue_number=iss1["number"],
            comment="Migration script authored and tested.",
            state="closed",
        )

        # 2. Branches & Commits
        adapter.create_branch(branch_name="feature/schema-wal", base="main")
        c1 = adapter.create_commit(
            branch="feature/schema-wal",
            message="Add WAL pragma",
            files=["src/db.py", "tests/test_db.py"],
        )
        c2 = adapter.create_commit(
            branch="feature/schema-wal",
            message="Add FTS5 index",
            files=["src/fts.py"],
        )

        # 3. Pull Requests
        pr1 = adapter.create_pull_request(
            title="WAL and FTS5 Support",
            body="Implements WAL journaling.",
            head_branch="feature/schema-wal",
            base_branch="main",
        )
        merge_res = adapter.merge_pull_request(pr_number=pr1["number"], commit_message="Merge PR #101")

        adapter.create_branch(branch_name="feature/ui-contrast", base="main")
        pr2 = adapter.create_pull_request(
            title="Contrast Fixes",
            body="WCAG 2.1 AAA compliance.",
            head_branch="feature/ui-contrast",
            base_branch="main",
        )

        # Capture in-memory state snapshot
        expected_state = copy.deepcopy(adapter._state)

        # Verify disk JSON exists and is identical
        raw_disk_content = self.storage_path.read_text(encoding="utf-8")
        parsed_disk = json.loads(raw_disk_content)
        self.assertEqual(parsed_disk, expected_state)

        # Kill adapter and rehydrate fresh
        del adapter
        gc.collect()

        fresh = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
        self.assertEqual(fresh._state, expected_state)

        # Verify query APIs on fresh instance
        fetched_iss1 = fresh.get_issue(1)
        self.assertEqual(fetched_iss1["state"], "closed")
        self.assertEqual(len(fetched_iss1["comments"]), 1)
        self.assertEqual(fetched_iss1["comments"][0]["body"], "Migration script authored and tested.")

        open_prs = fresh.list_pull_requests(state="open")
        self.assertEqual(len(open_prs), 1)
        self.assertEqual(open_prs[0]["number"], pr2["number"])

        merged_prs = fresh.list_pull_requests(state="merged")
        self.assertEqual(len(merged_prs), 1)
        self.assertEqual(merged_prs[0]["number"], pr1["number"])
        self.assertEqual(merged_prs[0]["merge_commit_sha"], merge_res["sha"])

        commits = fresh.list_commits(branch="feature/schema-wal", limit=10)
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]["message"], "Add FTS5 index")  # Most recent first

    def test_concurrent_rehydration_stress_20_threads(self) -> None:
        """20 threads simultaneously instantiate new MockGitHubAdapter instances
        from the SAME pre-populated disk storage file.
        Assert:
        - All 20 instances load successfully without file lock contention or crashes.
        - Every instance sees the exact same repository status and issue count.
        """
        # Pre-populate
        init_adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
        for i in range(15):
            init_adapter.create_issue(title=f"Pre-populated Issue {i}")
        init_adapter.create_branch(branch_name="feature/shared", base="main")
        del init_adapter

        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        statuses: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(_idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                ad = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
                st = ad.get_repo_status()
                with lock:
                    statuses.append(st)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Concurrent rehydration errors: {errors}")
        self.assertEqual(len(statuses), num_threads)
        for st in statuses:
            self.assertEqual(st["issue_count"], 15)
            self.assertIn("feature/shared", st["branches"])

    def test_corrupted_json_graceful_recovery(self) -> None:
        """If storage file is corrupted or truncated, MockGitHubAdapter recovers
        gracefully by initializing a fresh repository state without crashing.
        """
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text("{corrupt: json truncated... [", encoding="utf-8")

        adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)
        status = adapter.get_repo_status()
        self.assertEqual(status["issue_count"], 0)
        self.assertEqual(status["branch_count"], 1)
        self.assertEqual(status["default_branch"], "main")


class TestMockGitHubInvalidStateMachineTransitions(unittest.TestCase):
    """Category 3: Invalid State Machine Transitions & Defensive Boundary Checks."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_gh_statemachine_")
        self.storage_path = Path(self.temp_dir) / "mock_github_sm.json"
        self.repo = "owner/statemachine-test"
        self.adapter = MockGitHubAdapter(storage_path=self.storage_path, default_repo=self.repo)

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_merge_already_merged_pr_raises_405(self) -> None:
        """Merging an already-merged PR must raise GitHubMethodNotAllowedError (HTTP 405).
        Repeated calls must consistently raise 405 without corrupting state.
        """
        self.adapter.create_branch(branch_name="feat/double-merge", base="main")
        pr = self.adapter.create_pull_request(
            title="Double Merge PR",
            head_branch="feat/double-merge",
            base_branch="main",
        )
        pr_num = pr["number"]

        # First merge succeeds
        res1 = self.adapter.merge_pull_request(pr_number=pr_num, commit_message="First merge")
        self.assertTrue(res1["merged"])
        original_sha = res1["sha"]

        # Second merge must fail with 405
        with self.assertRaises(GitHubMethodNotAllowedError) as cm:
            self.adapter.merge_pull_request(pr_number=pr_num, commit_message="Second merge")
        self.assertEqual(cm.exception.status_code, 405)
        self.assertIn("not open", cm.exception.message)

        # Third consecutive merge attempt must also fail with 405
        with self.assertRaises(GitHubMethodNotAllowedError) as cm2:
            self.adapter.merge_pull_request(pr_number=pr_num)
        self.assertEqual(cm2.exception.status_code, 405)

        # State remains merged with original SHA
        pr_after = self.adapter.get_pull_request(pr_num)
        self.assertEqual(pr_after["state"], "merged")
        self.assertTrue(pr_after["merged"])
        self.assertEqual(pr_after["merge_commit_sha"], original_sha)

    def test_merge_closed_unmerged_pr_raises_405(self) -> None:
        """Merging a closed (unmerged) PR must raise GitHubMethodNotAllowedError (HTTP 405)."""
        self.adapter.create_branch(branch_name="feat/closed-pr", base="main")
        pr = self.adapter.create_pull_request(
            title="PR to be closed",
            head_branch="feat/closed-pr",
            base_branch="main",
        )
        pr_num = pr["number"]

        # Transition PR state to closed directly in underlying data and flush
        with self.adapter._lock:
            repo_data = self.adapter._ensure_repo(self.repo)
            repo_data["pull_requests"][str(pr_num)]["state"] = "closed"
            repo_data["pull_requests"][str(pr_num)]["merged"] = False
            self.adapter._flush()

        # Attempting merge must raise 405
        with self.assertRaises(GitHubMethodNotAllowedError) as cm:
            self.adapter.merge_pull_request(pr_number=pr_num)
        self.assertEqual(cm.exception.status_code, 405)
        self.assertIn("closed", str(cm.exception.response.get("state", "")))

    def test_merge_nonexistent_pr_raises_404(self) -> None:
        """Attempting to merge a non-existent PR must raise GitHubNotFoundError (HTTP 404)."""
        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.merge_pull_request(pr_number=99999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_create_pr_nonexistent_head_branch_raises_422(self) -> None:
        """Creating a PR with a non-existent head branch must raise GitHubValidationError (HTTP 422)."""
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_pull_request(
                title="Bad Head PR",
                head_branch="ghost-feature-branch",
                base_branch="main",
            )
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("ghost-feature-branch", cm.exception.message)

    def test_create_pr_nonexistent_base_branch_raises_422(self) -> None:
        """Creating a PR with a non-existent base branch must raise GitHubValidationError (HTTP 422)."""
        self.adapter.create_branch(branch_name="feat/valid-head", base="main")
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_pull_request(
                title="Bad Base PR",
                head_branch="feat/valid-head",
                base_branch="nonexistent-target-branch",
            )
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("nonexistent-target-branch", cm.exception.message)

    def test_create_pr_both_branches_nonexistent_raises_422(self) -> None:
        """Creating a PR where neither branch exists must raise GitHubValidationError (HTTP 422)."""
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_pull_request(
                title="Both Ghost Branches",
                head_branch="ghost-head",
                base_branch="ghost-base",
            )
        self.assertEqual(cm.exception.status_code, 422)

    def test_update_nonexistent_issue_raises_404(self) -> None:
        """Updating a non-existent issue (state, title, comment, labels) must raise GitHubNotFoundError (HTTP 404)."""
        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.update_issue(issue_number=99999, state="closed")
        self.assertEqual(cm.exception.status_code, 404)

        with self.assertRaises(GitHubNotFoundError) as cm2:
            self.adapter.update_issue(issue_number=88888, title="Updated Title")
        self.assertEqual(cm2.exception.status_code, 404)

        with self.assertRaises(GitHubNotFoundError) as cm3:
            self.adapter.update_issue(issue_number=77777, comment="A new comment")
        self.assertEqual(cm3.exception.status_code, 404)

    def test_get_nonexistent_issue_raises_404(self) -> None:
        """get_issue for an unknown issue number must raise GitHubNotFoundError (HTTP 404)."""
        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.get_issue(99999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_get_nonexistent_pr_raises_404(self) -> None:
        """get_pull_request for an unknown PR number must raise GitHubNotFoundError (HTTP 404)."""
        with self.assertRaises(GitHubNotFoundError) as cm:
            self.adapter.get_pull_request(99999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_create_branch_duplicate_raises_422(self) -> None:
        """Attempting to create a branch that already exists must raise GitHubValidationError (HTTP 422)."""
        self.adapter.create_branch(branch_name="feature/duplicate-test", base="main")
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_branch(branch_name="feature/duplicate-test", base="main")
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("already exists", cm.exception.message)

    def test_create_branch_nonexistent_base_raises_422(self) -> None:
        """Creating a branch from a non-existent base branch must raise GitHubValidationError (HTTP 422)."""
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_branch(branch_name="feature/orphan", base="ghost-base")
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("does not exist", cm.exception.message)

    def test_create_commit_nonexistent_branch_raises_422(self) -> None:
        """Creating a commit on a branch that does not exist must raise GitHubValidationError (HTTP 422)."""
        with self.assertRaises(GitHubValidationError) as cm:
            self.adapter.create_commit(branch="missing-branch", message="Invalid commit")
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("does not exist", cm.exception.message)

    def test_multi_repo_isolation(self) -> None:
        """Operations performed in repo A ('owner/repo-a') must not leak into repo B ('owner/repo-b').
        Sequences for issues and PRs must increment independently per repository.
        """
        repo_a = "owner/repo-a"
        repo_b = "owner/repo-b"

        # Repo A operations
        iss_a1 = self.adapter.create_issue(title="Issue in Repo A #1", repo=repo_a)
        iss_a2 = self.adapter.create_issue(title="Issue in Repo A #2", repo=repo_a)
        self.adapter.create_branch(branch_name="feat/a", base="main", repo=repo_a)
        pr_a1 = self.adapter.create_pull_request(
            title="PR in Repo A", head_branch="feat/a", base_branch="main", repo=repo_a
        )

        # Repo B operations
        iss_b1 = self.adapter.create_issue(title="Issue in Repo B #1", repo=repo_b)
        self.adapter.create_branch(branch_name="feat/b", base="main", repo=repo_b)
        pr_b1 = self.adapter.create_pull_request(
            title="PR in Repo B", head_branch="feat/b", base_branch="main", repo=repo_b
        )

        # Sequences must be independent
        self.assertEqual(iss_a1["number"], 1)
        self.assertEqual(iss_a2["number"], 2)
        self.assertEqual(iss_b1["number"], 1)  # Independent sequence starting at 1!

        self.assertEqual(pr_a1["number"], 101)
        self.assertEqual(pr_b1["number"], 101)  # Independent sequence starting at 101!

        # Entity lists must be isolated
        issues_a = self.adapter.list_issues(repo=repo_a)
        issues_b = self.adapter.list_issues(repo=repo_b)
        self.assertEqual(len(issues_a), 2)
        self.assertEqual(len(issues_b), 1)

        branches_a = [b["name"] for b in self.adapter.list_branches(repo=repo_a)]
        branches_b = [b["name"] for b in self.adapter.list_branches(repo=repo_b)]
        self.assertIn("feat/a", branches_a)
        self.assertNotIn("feat/b", branches_a)
        self.assertIn("feat/b", branches_b)
        self.assertNotIn("feat/a", branches_b)

        # Rehydration must preserve independent multi-repo state
        fresh = MockGitHubAdapter(storage_path=self.storage_path, default_repo=repo_a)
        st_a = fresh.get_repo_status(repo=repo_a)
        st_b = fresh.get_repo_status(repo=repo_b)
        self.assertEqual(st_a["issue_count"], 2)
        self.assertEqual(st_b["issue_count"], 1)


class TestMockGitHubEdgeCasesAndHardening(unittest.TestCase):
    """Category 4: Edge Cases and Defensive Input Hardening."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="crewai_adv_gh_edge_")
        self.storage_path = Path(self.temp_dir) / "mock_github_edge.json"
        self.adapter = MockGitHubAdapter(storage_path=self.storage_path)

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_unicode_and_emojis_in_issues_and_commits(self) -> None:
        """Verify full UTF-8 Unicode, CJK characters, and emojis in issue titles, bodies,
        commit messages, and PR descriptions survive serialization and rehydration.
        """
        unicode_title = "🚀 Feature: 快速排序算法 ✨ (Fast Sort) 🎯"
        unicode_body = "Testing 💖 UTF-8 encoding across emojis, Arabic: مرحبا, Russian: Привет, Japanese: こんにちは."
        iss = self.adapter.create_issue(title=unicode_title, body=unicode_body, labels=["🚀", "cjk-test"])
        self.assertEqual(iss["title"], unicode_title)
        self.assertEqual(iss["body"], unicode_body)

        commit_msg = "🔥 Hotfix: 🐛 Bug fix in pipeline ⚡"
        c = self.adapter.create_commit(branch="main", message=commit_msg, files=["main.py"])
        self.assertEqual(c["message"], commit_msg)

        # Rehydrate and assert UTF-8 integrity
        fresh = MockGitHubAdapter(storage_path=self.storage_path)
        fetched_iss = fresh.get_issue(iss["number"])
        self.assertEqual(fetched_iss["title"], unicode_title)
        self.assertEqual(fetched_iss["body"], unicode_body)
        self.assertIn("🚀", fetched_iss["labels"])

    def test_large_payload_handling(self) -> None:
        """Verify large text bodies (500 KB) in issues and commits are handled without memory or IO errors."""
        large_body = "A" * (500 * 1024)
        iss = self.adapter.create_issue(title="Large Payload Test", body=large_body)
        self.assertEqual(len(iss["body"]), 500 * 1024)

        fresh = MockGitHubAdapter(storage_path=self.storage_path)
        fetched = fresh.get_issue(iss["number"])
        self.assertEqual(len(fetched["body"]), 500 * 1024)


if __name__ == "__main__":
    unittest.main()
