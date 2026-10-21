"""
Thread-safe persistent Mock GitHub adapter for offline execution,
hermetic testing, and reproducible multi-agent coordination.
Persists state to local JSON file with deterministic auto-incrementing IDs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.config import get_default_mock_github_path
from persistent_crewai.tools.github_interface import (
    GitHubAdapterInterface,
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubValidationError,
)


class MockGitHubAdapter(GitHubAdapterInterface):
    """File-backed, thread-safe simulation of GitHub REST API v3."""

    def __init__(
        self,
        storage_path: Optional[Union[str, Path]] = None,
        default_repo: str = "owner/repo",
        state_manager: Any = None,
    ):
        self.storage_path = (
            Path(storage_path).resolve()
            if storage_path is not None
            else get_default_mock_github_path()
        )
        self.default_repo = default_repo.strip() if default_repo else "owner/repo"
        self.state_manager = state_manager
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "repositories": {},
        }
        self._load()

    # -------------------------------------------------------------------------
    # Internal Persistence & Helpers
    # -------------------------------------------------------------------------
    def _load(self) -> None:
        """Load state from storage path or initialize fresh state."""
        with self._lock:
            if self.storage_path.exists():
                try:
                    content = self.storage_path.read_text(encoding="utf-8")
                    if content.strip():
                        loaded = json.loads(content)
                        if isinstance(loaded, dict) and "repositories" in loaded:
                            self._state = loaded
                            return
                except Exception:
                    pass
            self._ensure_repo(self.default_repo)
            self._flush()

    def _flush(self) -> None:
        """Atomically write in-memory state to disk."""
        with self._lock:
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.storage_path.with_suffix(f".tmp_{os.getpid()}_{threading.get_ident()}")
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(self._state, f, indent=2, default=str)
                tmp_file.replace(self.storage_path)
            except Exception:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass
                raise

    def _resolve_repo(self, repo: Optional[str]) -> str:
        """Resolve repository name falling back to default."""
        if repo and repo.strip():
            return repo.strip()
        return self.default_repo

    def _ensure_repo(self, repo: str) -> Dict[str, Any]:
        """Ensure repository structure is initialized in state."""
        with self._lock:
            if repo not in self._state["repositories"]:
                now = datetime.now(timezone.utc).isoformat()
                self._state["repositories"][repo] = {
                    "default_branch": "main",
                    "issue_seq": 0,
                    "pr_seq": 100,
                    "branches": {
                        "main": {
                            "name": "main",
                            "sha": "init0000",
                            "protected": False,
                        }
                    },
                    "commits": [
                        {
                            "sha": "init0000",
                            "branch": "main",
                            "message": "Initial commit",
                            "files": [],
                            "timestamp": now,
                        }
                    ],
                    "issues": {},
                    "pull_requests": {},
                }
            return self._state["repositories"][repo]

    def _record_audit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Record operational event to StateManager if attached."""
        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    event_type=event_type,
                    payload=payload,
                    agent_role="GitHub Manager",
                    tool_name="MockGitHubAdapter",
                )
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Issue Operations
    # -------------------------------------------------------------------------
    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            repo_data["issue_seq"] += 1
            issue_num = repo_data["issue_seq"]
            now = datetime.now(timezone.utc).isoformat()

            issue = {
                "number": issue_num,
                "title": title,
                "body": body,
                "labels": list(labels) if labels else [],
                "assignees": list(assignees) if assignees else [],
                "state": "open",
                "comments": [],
                "created_at": now,
                "updated_at": now,
            }

            repo_data["issues"][str(issue_num)] = issue
            self._flush()

            self._record_audit(
                "github_issue_created",
                {"repo": r, "issue_number": issue_num, "title": title},
            )

            return copy.deepcopy(issue)

    def get_issue(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            key = str(issue_number)
            if key not in repo_data["issues"]:
                raise GitHubNotFoundError(
                    f"Issue #{issue_number} not found in {r}",
                    response={"repo": r, "issue_number": issue_number},
                )
            return copy.deepcopy(repo_data["issues"][key])

    def list_issues(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            results = []
            for issue in repo_data["issues"].values():
                if state != "all" and issue["state"] != state:
                    continue
                if labels:
                    issue_labels = set(issue.get("labels", []))
                    if not set(labels).issubset(issue_labels):
                        continue
                results.append(copy.deepcopy(issue))

            results.sort(key=lambda x: x["number"])
            return results

    def update_issue(
        self,
        issue_number: int,
        state: Optional[str] = None,
        body: Optional[str] = None,
        title: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        comment: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            key = str(issue_number)
            if key not in repo_data["issues"]:
                raise GitHubNotFoundError(
                    f"Issue #{issue_number} not found in {r}",
                    response={"repo": r, "issue_number": issue_number},
                )

            issue = repo_data["issues"][key]
            now = datetime.now(timezone.utc).isoformat()

            if state is not None:
                issue["state"] = state
            if body is not None:
                issue["body"] = body
            if title is not None:
                issue["title"] = title
            if labels is not None:
                issue["labels"] = list(labels)
            if assignees is not None:
                issue["assignees"] = list(assignees)
            if comment is not None:
                issue["comments"].append({"body": comment, "created_at": now})

            issue["updated_at"] = now
            self._flush()

            self._record_audit(
                "github_issue_updated",
                {"repo": r, "issue_number": issue_number, "state": issue["state"]},
            )

            return copy.deepcopy(issue)

    # -------------------------------------------------------------------------
    # Branch & Commit Operations
    # -------------------------------------------------------------------------
    def create_branch(
        self,
        branch_name: str,
        base: str = "main",
        from_branch: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            effective_base = from_branch if from_branch is not None else base
            if not effective_base:
                effective_base = "main"

            if branch_name in repo_data["branches"]:
                raise GitHubValidationError(
                    f"Branch '{branch_name}' already exists in {r}",
                    status_code=422,
                    response={"repo": r, "branch": branch_name},
                )

            if effective_base not in repo_data["branches"]:
                raise GitHubValidationError(
                    f"Base branch '{effective_base}' does not exist in {r}",
                    status_code=422,
                    response={"repo": r, "base_branch": effective_base},
                )

            base_sha = repo_data["branches"][effective_base]["sha"]
            branch_record = {
                "name": branch_name,
                "sha": base_sha,
                "protected": False,
            }

            repo_data["branches"][branch_name] = branch_record
            self._flush()

            self._record_audit(
                "github_branch_created",
                {"repo": r, "branch": branch_name, "base": effective_base},
            )

            return copy.deepcopy(branch_record)

    def list_branches(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)
            return [copy.deepcopy(b) for b in repo_data["branches"].values()]

    def create_commit(
        self,
        branch: str,
        message: str,
        files: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            if branch not in repo_data["branches"]:
                raise GitHubValidationError(
                    f"Branch '{branch}' does not exist in {r}",
                    status_code=422,
                    response={"repo": r, "branch": branch},
                )

            now = datetime.now(timezone.utc).isoformat()
            commits = repo_data["commits"]
            raw_hash = f"{r}:{branch}:{message}:{len(commits)}:{now}".encode("utf-8")
            sha = hashlib.sha256(raw_hash).hexdigest()[:8]

            repo_data["branches"][branch]["sha"] = sha
            commit = {
                "sha": sha,
                "branch": branch,
                "message": message,
                "files": list(files) if files else [],
                "timestamp": now,
            }
            commits.append(commit)
            self._flush()

            self._record_audit(
                "github_commit_created",
                {"repo": r, "branch": branch, "sha": sha, "message": message},
            )

            return copy.deepcopy(commit)

    def list_commits(
        self,
        branch: Optional[str] = None,
        limit: int = 10,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            all_commits = repo_data["commits"]
            if branch:
                filtered = [c for c in all_commits if c.get("branch") == branch]
            else:
                filtered = all_commits

            # Most recent first
            reversed_commits = list(reversed(filtered))
            return [copy.deepcopy(c) for c in reversed_commits[:limit]]

    # -------------------------------------------------------------------------
    # Pull Request Operations
    # -------------------------------------------------------------------------
    def create_pull_request(
        self,
        title: str,
        body: str = "",
        head_branch: str = "feature",
        base_branch: str = "main",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            if head_branch not in repo_data["branches"]:
                raise GitHubValidationError(
                    f"Head branch '{head_branch}' does not exist in {r}",
                    status_code=422,
                    response={"repo": r, "head_branch": head_branch},
                )

            if base_branch not in repo_data["branches"]:
                raise GitHubValidationError(
                    f"Base branch '{base_branch}' does not exist in {r}",
                    status_code=422,
                    response={"repo": r, "base_branch": base_branch},
                )

            repo_data["pr_seq"] += 1
            pr_num = repo_data["pr_seq"]
            now = datetime.now(timezone.utc).isoformat()

            pr = {
                "number": pr_num,
                "title": title,
                "body": body,
                "head_branch": head_branch,
                "base_branch": base_branch,
                "head": head_branch,
                "base": base_branch,
                "state": "open",
                "merged": False,
                "merge_commit_sha": None,
                "created_at": now,
                "updated_at": now,
            }

            repo_data["pull_requests"][str(pr_num)] = pr
            self._flush()

            self._record_audit(
                "github_pr_created",
                {"repo": r, "pr_number": pr_num, "title": title, "head": head_branch},
            )

            return copy.deepcopy(pr)

    def get_pull_request(
        self,
        pr_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            key = str(pr_number)
            if key not in repo_data["pull_requests"]:
                raise GitHubNotFoundError(
                    f"Pull request #{pr_number} not found in {r}",
                    response={"repo": r, "pr_number": pr_number},
                )
            return copy.deepcopy(repo_data["pull_requests"][key])

    def list_pull_requests(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            results = []
            for pr in repo_data["pull_requests"].values():
                if state == "open" and pr["state"] != "open":
                    continue
                elif state == "closed" and (pr["state"] != "closed" or pr["merged"]):
                    continue
                elif state == "merged" and not pr["merged"]:
                    continue
                results.append(copy.deepcopy(pr))

            results.sort(key=lambda x: x["number"])
            return results

    def merge_pull_request(
        self,
        pr_number: int,
        commit_message: str = "",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            key = str(pr_number)
            if key not in repo_data["pull_requests"]:
                raise GitHubNotFoundError(
                    f"Pull request #{pr_number} not found in {r}",
                    response={"repo": r, "pr_number": pr_number},
                )

            pr = repo_data["pull_requests"][key]
            if pr["state"] != "open" or pr["merged"]:
                raise GitHubMethodNotAllowedError(
                    f"Pull request #{pr_number} is not open (state: {pr['state']})",
                    status_code=405,
                    response={"repo": r, "pr_number": pr_number, "state": pr["state"]},
                )

            now = datetime.now(timezone.utc).isoformat()
            raw_hash = f"merge:{pr_number}:{commit_message}:{now}".encode("utf-8")
            merge_sha = hashlib.sha256(raw_hash).hexdigest()[:8]

            pr["state"] = "merged"
            pr["merged"] = True
            pr["merge_commit_sha"] = merge_sha
            pr["updated_at"] = now

            base_branch = pr["base_branch"]
            if base_branch in repo_data["branches"]:
                repo_data["branches"][base_branch]["sha"] = merge_sha

            self._flush()

            self._record_audit(
                "github_pr_merged",
                {"repo": r, "pr_number": pr_number, "merge_sha": merge_sha},
            )

            return {
                "merged": True,
                "sha": merge_sha,
                "message": commit_message or f"Merge pull request #{pr_number}",
            }

    # -------------------------------------------------------------------------
    # Repository Status
    # -------------------------------------------------------------------------
    def get_repo_status(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            r = self._resolve_repo(repo)
            repo_data = self._ensure_repo(r)

            branches = repo_data["branches"]
            issues = repo_data["issues"]
            prs = repo_data["pull_requests"]
            commits = repo_data["commits"]

            open_issues_count = sum(1 for i in issues.values() if i["state"] == "open")
            open_prs_count = sum(1 for p in prs.values() if p["state"] == "open")

            return {
                "name": r,
                "default_branch": repo_data["default_branch"],
                "branch_count": len(branches),
                "branches": [b["name"] for b in branches.values()],
                "issue_count": len(issues),
                "open_issues_count": open_issues_count,
                "pull_request_count": len(prs),
                "open_pr_count": open_prs_count,
                "commit_count": len(commits),
            }
