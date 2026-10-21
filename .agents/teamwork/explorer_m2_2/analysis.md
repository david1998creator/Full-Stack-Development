# Architectural Analysis: GitHub Adapter Interface & Persistent Mock Implementation

**Explorer**: M2-2 (Milestone 2 GitHub Adapter & Mock Implementation Explorer)  
**Date**: 2026-10-13  
**Status**: COMPLETE  

---

## 1. Executive Summary

This document provides the complete architectural design and implementation strategy for GitHub tooling in the **Persistent CrewAI 8-Agent Software Engineering Framework**.

The GitHub subsystem serves requirement **R3 (GitHub & Workspace Tooling)**, specifically providing simulated mock repository capabilities for offline development, hermetic CI verification, and future live GitHub REST API integration without modifying calling code.

The strategy specifies:
1. **`src/persistent_crewai/tools/github_interface.py`**:
   - Formal Abstract Base Class `GitHubAdapterInterface` defining contracts for Issues, Pull Requests, Branches, Commits, and Repository Status.
   - Structured error hierarchy (`GitHubError`, `GitHubNotFoundError`, `GitHubAuthError`, `GitHubRateLimitError`, `GitHubConflictError`, `GitHubValidationError`) simulating standard GitHub REST API HTTP status codes (`401`, `403`, `404`, `405`, `422`).
2. **`src/persistent_crewai/tools/mock_github.py`**:
   - `MockGitHubAdapter` implementing `GitHubAdapterInterface`.
   - Local JSON persistence to `.persistent_crewai/mock_github.json` (or custom path).
   - Deterministic sequential numbering: Issues start at `#1`, PRs start at `#101`.
   - In-memory cache + thread-safe atomic file flush (`tempfile` + `replace`).
   - Dual-signature compatibility: supports both explicit `repo=...` kwargs and default repository configurations.
3. **`src/persistent_crewai/tools/github_adapter_factory.py`**:
   - `create_github_adapter()` factory resolving to `MockGitHubAdapter` when `GITHUB_TOKEN` is unset or `force_mock=True`.
4. **Unit Test Plan**:
   - Comprehensive test matrix covering 28 unit scenarios across 8 categories in `tests/test_github_adapter.py`.

---

## 2. Requirements & Existing Test Invariants

Inspection of the codebase, `ORIGINAL_REQUEST.md`, `PROJECT.md`, `specifications.md`, and the authored test suite (`tests/e2e/test_e2e_tier*.py`) revealed several critical design constraints and invariants:

| Requirement / Invariant | Source | Exact Constraint |
|---|---|---|
| **Abstract Base Class** | Dispatch & R3 | `GitHubAdapterInterface(abc.ABC)` with abstract methods for all issue, PR, branch, commit, and repo status operations. |
| **Deterministic Issue Numbering** | `test_r3_04` | Issues must auto-increment sequentially: `#1`, `#2`, `#3`... |
| **Deterministic PR Numbering** | `test_r3_05` | PRs must auto-increment sequentially starting at `#101`: `#101`, `#102`... |
| **Default Branch Initialization** | `test_r3_05` | New/uninitialized repos must automatically initialize a default branch named `"main"`. |
| **Branch Creation Validation** | `test_b14` | Creating a branch that already exists must raise `GitHubError` with `status_code = 422`. |
| **Missing Head Branch Validation** | `test_b12` | Creating a PR with a nonexistent `head_branch` must raise `GitHubError` with `status_code = 422`. |
| **PR Merge State Validation** | `test_b13` | Merging an already merged or closed PR must raise `GitHubError` with `status_code = 405`. Merging returns `{"merged": True, ...}`. |
| **Issue 404 Validation** | `test_b15` | Querying a nonexistent issue must raise `GitHubNotFoundError` (inheriting from `GitHubError`) with `status_code = 404`. |
| **Cross-Instance Persistence** | `test_c06` | Mutated state saved to JSON must survive adapter reloading across separate instances. |
| **Factory Fallback** | `test_r3_06` | `create_github_adapter(force_mock=True, storage_path=...)` returns an instance of `MockGitHubAdapter`. |
| **Signature Flexibility** | Dispatch vs E2E | Calling methods with either `repo=repo, ...` or `repo=None` (falling back to `self.default_repo = "owner/repo"`) must both function seamlessly. |

---

## 3. Component Architecture & Detailed Design

### 3.1 `src/persistent_crewai/tools/github_interface.py`

#### 3.1.1 Exception Hierarchy
Standardizing error classes ensures compatibility with both real GitHub REST API errors and mock assertions.

```python
class GitHubError(Exception):
    """Base exception for all GitHub operations."""
    def __init__(self, message: str, status_code: int = 500, response: Optional[dict[str, Any]] = None):
        super().__init__(f"GitHubError [{status_code}]: {message}")
        self.message = message
        self.status_code = status_code
        self.response = response or {}

class GitHubNotFoundError(GitHubError):
    """Raised when an issue, PR, branch, or repository is not found (HTTP 404)."""
    def __init__(self, message: str = "Resource not found", response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=404, response=response)

class GitHubValidationError(GitHubError):
    """Raised on invalid request data or missing branch on PR creation (HTTP 422)."""
    def __init__(self, message: str = "Validation failed", response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=422, response=response)

class GitHubMethodNotAllowedError(GitHubError):
    """Raised when operation cannot be performed, e.g. merging closed PR (HTTP 405)."""
    def __init__(self, message: str = "Method not allowed", response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=405, response=response)

class GitHubConflictError(GitHubError):
    """Raised on state conflicts, e.g. duplicate branch creation (HTTP 409 or 422)."""
    def __init__(self, message: str = "Resource conflict", status_code: int = 409, response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=status_code, response=response)

class GitHubAuthError(GitHubError):
    """Raised when GitHub token is missing, expired, or invalid (HTTP 401)."""
    def __init__(self, message: str = "Authentication failed", response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=401, response=response)

class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limits are exceeded (HTTP 403)."""
    def __init__(self, message: str = "API rate limit exceeded", reset_time: Optional[int] = None, response: Optional[dict[str, Any]] = None):
        super().__init__(message=message, status_code=403, response=response)
        self.reset_time = reset_time
```

#### 3.1.2 Interface Contract: `GitHubAdapterInterface`
The ABC specifies exact method names and flexible parameter handling.

```python
from abc import ABC, abstractmethod
from typing import Any, Optional

class GitHubAdapterInterface(ABC):
    """Abstract interface defining GitHub operations for issues, PRs, branches, and commits."""

    # -------------------------------------------------------------------------
    # Issue Operations
    # -------------------------------------------------------------------------
    @abstractmethod
    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a new issue. Returns issue entity dictionary."""
        pass

    @abstractmethod
    def get_issue(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Retrieve an issue by number. Raises GitHubNotFoundError if not found."""
        pass

    @abstractmethod
    def list_issues(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """List issues in repository, optionally filtered by state ('open', 'closed', 'all')."""
        pass

    @abstractmethod
    def update_issue(
        self,
        issue_number: int,
        state: Optional[str] = None,
        body: Optional[str] = None,
        comment: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update an existing issue's state, body, comment, or labels."""
        pass

    # -------------------------------------------------------------------------
    # Pull Request Operations
    # -------------------------------------------------------------------------
    @abstractmethod
    def create_pull_request(
        self,
        title: str,
        body: str = "",
        head_branch: str = "",
        base_branch: str = "main",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a pull request. Raises GitHubError(422) if head branch does not exist."""
        pass

    @abstractmethod
    def get_pull_request(
        self,
        pr_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Retrieve a pull request by number. Raises GitHubNotFoundError if not found."""
        pass

    @abstractmethod
    def list_pull_requests(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """List pull requests in repository ('open', 'closed', 'merged', 'all')."""
        pass

    @abstractmethod
    def merge_pull_request(
        self,
        pr_number: int,
        commit_message: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Merge a pull request. Raises GitHubError(405) if PR is not open."""
        pass

    # -------------------------------------------------------------------------
    # Branch & Commit Operations
    # -------------------------------------------------------------------------
    @abstractmethod
    def create_branch(
        self,
        branch_name: str,
        base: str = "main",
        from_branch: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a new branch from a base branch. Raises GitHubError(422) if duplicate or base missing."""
        pass

    @abstractmethod
    def list_branches(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """List all branches in repository."""
        pass

    @abstractmethod
    def create_commit(
        self,
        branch: str,
        message: str,
        files: Optional[list[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record a commit on a branch with list of modified files."""
        pass

    @abstractmethod
    def list_commits(
        self,
        branch: Optional[str] = None,
        limit: int = 10,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """List commits for a branch or repository."""
        pass

    @abstractmethod
    def get_repo_status(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Retrieve summary status of repository."""
        pass
```

---

### 3.2 `src/persistent_crewai/tools/mock_github.py`

#### 3.2.1 Data Schema in `mock_github.json`
The file stores a dictionary of repositories, each containing its own isolated sequence counters, branches, commits, issues, and PRs:

```json
{
  "schema_version": "1.0.0",
  "updated_at": "2026-10-13T21:00:00Z",
  "repositories": {
    "owner/repo": {
      "default_branch": "main",
      "issue_seq": 1,
      "pr_seq": 101,
      "branches": {
        "main": {
          "name": "main",
          "sha": "init0000",
          "protected": false
        },
        "feat/vector-store": {
          "name": "feat/vector-store",
          "sha": "a1b2c3d4",
          "protected": false
        }
      },
      "commits": [
        {
          "sha": "init0000",
          "branch": "main",
          "message": "Initial commit",
          "files": [],
          "timestamp": "2026-10-13T20:00:00Z"
        },
        {
          "sha": "a1b2c3d4",
          "branch": "feat/vector-store",
          "message": "feat: add SQLiteVectorStore implementation",
          "files": ["src/persistence/vector_store.py"],
          "timestamp": "2026-10-13T20:30:00Z"
        }
      ],
      "issues": {
        "1": {
          "number": 1,
          "title": "Implement SQLite FTS5 index",
          "body": "FTS5 triggers needed for fast memory retrieval.",
          "labels": ["enhancement", "database"],
          "assignees": [],
          "state": "open",
          "comments": [],
          "created_at": "2026-10-13T20:10:00Z",
          "updated_at": "2026-10-13T20:10:00Z"
        }
      },
      "pull_requests": {
        "101": {
          "number": 101,
          "title": "feat: SQLiteVectorStore implementation",
          "body": "Introduces cosine similarity search and FTS5 fallback.",
          "head_branch": "feat/vector-store",
          "base_branch": "main",
          "head": "feat/vector-store",
          "base": "main",
          "state": "open",
          "merged": false,
          "merge_commit_sha": null,
          "created_at": "2026-10-13T20:35:00Z",
          "updated_at": "2026-10-13T20:35:00Z"
        }
      }
    }
  }
}
```

#### 3.2.2 Key Algorithms & Implementation Details
1. **Thread-Safe Atomic Flush**:
   - `_lock = threading.RLock()`: all state reads and mutations synchronized.
   - Atomic disk write pattern:
     ```python
     def _flush(self) -> None:
         with self._lock:
             self._storage_path.parent.mkdir(parents=True, exist_ok=True)
             tmp_file = self._storage_path.with_suffix(".tmp")
             with open(tmp_file, "w", encoding="utf-8") as f:
                 json.dump(self._state, f, indent=2, default=str)
             tmp_file.replace(self._storage_path)
     ```
2. **Repository Auto-Initialization**:
   - When any method targets a repo string not yet in `self._state["repositories"]`, `_ensure_repo(repo)` auto-creates it with `default_branch="main"`, commit `init0000`, `issue_seq=0`, `pr_seq=100`.
3. **Deterministic ID Sequencing**:
   - Issue creation: `repo["issue_seq"] += 1` -> IDs `1`, `2`, `3`...
   - PR creation: `repo["pr_seq"] += 1` -> IDs `101`, `102`, `103`...
4. **Deterministic SHA Generation**:
   - Commit: `hashlib.sha256(f"{repo}:{branch}:{message}:{len(commits)}".encode()).hexdigest()[:8]`
   - Merge: `hashlib.sha256(f"merge:{pr_number}:{commit_message}".encode()).hexdigest()[:8]`
5. **Exact HTTP Error Emulation**:
   - Nonexistent issue: `raise GitHubNotFoundError(f"Issue #{num} not found in {repo}")` (status 404).
   - Nonexistent PR: `raise GitHubNotFoundError(f"Pull request #{num} not found in {repo}")` (status 404).
   - Duplicate branch: `raise GitHubValidationError(f"Branch '{name}' already exists in {repo}")` (status 422).
   - Nonexistent head branch: `raise GitHubValidationError(f"Head branch '{head}' does not exist in {repo}")` (status 422).
   - Merge non-open PR: `raise GitHubMethodNotAllowedError(f"Pull request #{num} is not open")` (status 405).
6. **Dual Call Signature Normalization**:
   Each method normalizes arguments to support both `repo` passed as kwarg or positional, and default repo fallback:
   ```python
   def _resolve_repo(self, repo: Optional[str]) -> str:
       if repo and repo.strip():
           return repo.strip()
       return self.default_repo
   ```

---

### 3.3 `src/persistent_crewai/tools/github_adapter_factory.py`

Factory resolution logic:
```python
import os
from pathlib import Path
from typing import Optional, Union
from persistent_crewai.tools.github_interface import GitHubAdapterInterface
from persistent_crewai.tools.mock_github import MockGitHubAdapter

def create_github_adapter(
    force_mock: bool = False,
    storage_path: Optional[Union[str, Path]] = None,
    default_repo: Optional[str] = None,
) -> GitHubAdapterInterface:
    """Instantiate a GitHub adapter.
    
    If force_mock is True or GITHUB_TOKEN is not in environment,
    instantiates MockGitHubAdapter. Otherwise, attempts RealGitHubAdapter.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if force_mock or not token:
        return MockGitHubAdapter(
            storage_path=storage_path,
            default_repo=default_repo or "owner/repo",
        )
    try:
        from persistent_crewai.tools.real_github import RealGitHubAdapter
        return RealGitHubAdapter(token=token, default_repo=default_repo)
    except (ImportError, Exception):
        # Graceful fallback to mock if real adapter not configured
        return MockGitHubAdapter(
            storage_path=storage_path,
            default_repo=default_repo or "owner/repo",
        )
```

---

## 4. Comprehensive Unit Test Plan (`tests/test_github_adapter.py`)

A standalone unit test suite dedicated to `MockGitHubAdapter` and `create_github_adapter` must be authored, executing under both `pytest` and `python -m unittest`.

### Test Matrix (28 Scenarios across 8 Categories)

| # | Test Category | Test Case Method | Description & Assertions |
|---|---|---|---|
| 1 | **Init & Storage** | `test_init_default_path` | Verifies default path resolves to `.persistent_crewai/mock_github.json`. |
| 2 | **Init & Storage** | `test_init_custom_path` | Verifies custom path creates parent directory and persists correctly. |
| 3 | **Init & Storage** | `test_init_auto_creates_main_branch` | Verifies fresh repo initializes with branch `"main"`. |
| 4 | **Persistence** | `test_persistence_across_instances` | Mutates repo in instance 1; verifies instance 2 loads same state from disk. |
| 5 | **Persistence** | `test_atomic_file_write` | Ensures `.tmp` file is used during flush and replaced atomically. |
| 6 | **Persistence** | `test_corrupt_file_recovery` | Verifies handling when JSON file contains invalid content (raises or re-inits). |
| 7 | **Issues** | `test_create_issue_sequential_id` | Verifies first issue is #1, second is #2, third is #3. |
| 8 | **Issues** | `test_create_issue_metadata` | Verifies title, body, labels, assignees, state="open", timestamps. |
| 9 | **Issues** | `test_get_issue_success` | Verifies retrieving created issue by number. |
| 10 | **Issues** | `test_get_issue_nonexistent_raises_404` | Querying nonexistent issue raises `GitHubNotFoundError` (status 404). |
| 11 | **Issues** | `test_list_issues_filter_state` | Tests filtering by `state="open"`, `state="closed"`, and `state="all"`. |
| 12 | **Issues** | `test_update_issue_close_and_comment` | Updates state to `"closed"`, adds comment; verifies state updated. |
| 13 | **Issues** | `test_update_issue_nonexistent_raises_404` | Updating nonexistent issue raises `GitHubNotFoundError`. |
| 14 | **Branches** | `test_create_branch_success` | Creates branch `feat/test` from `main`; verifies in `list_branches`. |
| 15 | **Branches** | `test_create_branch_duplicate_raises_422` | Creating existing branch name raises `GitHubError` (status 422). |
| 16 | **Branches** | `test_create_branch_missing_base_raises_422` | Base branch missing raises `GitHubError` (status 422). |
| 17 | **Branches** | `test_list_branches` | Verifies list contains branch names and SHA hashes. |
| 18 | **Commits** | `test_create_commit_success` | Commits to branch; returns 8-char SHA, message, files, timestamp. |
| 19 | **Commits** | `test_create_commit_missing_branch_raises_422`| Committing to nonexistent branch raises `GitHubError` (status 422). |
| 20 | **Commits** | `test_list_commits_ordered` | Lists commits in reverse chronological order; respects limit param. |
| 21 | **Pull Requests** | `test_create_pr_sequential_id` | Verifies first PR is #101, second is #102. |
| 22 | **Pull Requests** | `test_create_pr_missing_head_raises_422` | Creating PR with nonexistent head branch raises `GitHubError` (status 422). |
| 23 | **Pull Requests** | `test_merge_pr_success` | Merges open PR; sets `merged=True`, `state="merged"`, updates base branch SHA. |
| 24 | **Pull Requests** | `test_merge_pr_already_merged_raises_405` | Second merge call raises `GitHubError` (status 405). |
| 25 | **Pull Requests** | `test_list_prs_filter_state` | Filters PRs by `state="open"`, `state="merged"`, `state="all"`. |
| 26 | **Repo Status** | `test_get_repo_status` | Returns branch count, issue counts, PR counts, commit counts. |
| 27 | **Repo Isolation**| `test_multi_repo_isolation` | Verifies mutations in `repo1` do not leak into `repo2`. |
| 28 | **Factory & ABC**| `test_factory_and_abc_compliance` | Verifies `isinstance(MockGitHubAdapter(), GitHubAdapterInterface)` and factory behavior. |

---

## 5. Verification & Downstream Hand-off

The proposed design directly unblocks downstream Milestones:
- **Milestone 2 Implementer (M2-2 Track)**: Can directly implement `github_interface.py`, `mock_github.py`, and `github_adapter_factory.py` according to this blueprint.
- **E2E Test Suites**: Will immediately discover passing tests in:
  - `tests/e2e/test_e2e_tier1_features.py`: `test_r3_04`, `test_r3_05`, `test_r3_06`
  - `tests/e2e/test_e2e_tier2_boundaries.py`: `test_b12`, `test_b13`, `test_b14`, `test_b15`
  - `tests/e2e/test_e2e_tier3_combinations.py`: `test_c06`
  - `tests/e2e/test_e2e_tier4_scenarios.py`: Full multi-agent scenario GitHub operations (Step 7).
- **CrewAI Tools (Milestone 2 Tooling)**: CrewAI `@tool` functions or LangChain `BaseTool` wrappers for the GitHub Manager agent can wrap `GitHubAdapterInterface` without depending on whether live tokens exist.
