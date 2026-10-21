"""
Production Real GitHub REST API v3 adapter and factory function.
Communicates with https://api.github.com using Bearer token authentication,
translates HTTP status codes into typed GitHub exceptions, and defaults
to MockGitHubAdapter when token/repo is unconfigured or force_mock is requested.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import requests
except ImportError:
    requests = None  # Fallback to urllib if requests unavailable

from persistent_crewai.config import get_default_mock_github_path
from persistent_crewai.tools.github_interface import (
    GitHubAdapterInterface,
    GitHubAuthError,
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from persistent_crewai.tools.mock_github import MockGitHubAdapter


class RealGitHubAdapter(GitHubAdapterInterface):
    """GitHub REST API v3 adapter with bearer token auth and status code mapping."""

    def __init__(
        self,
        token: str,
        default_repo: str = "owner/repo",
        api_base_url: str = "https://api.github.com",
        timeout: int = 15,
        state_manager: Any = None,
        session: Any = None,
    ):
        if not token or not str(token).strip():
            raise GitHubAuthError("Missing or empty GitHub token", status_code=401)

        self.token = str(token).strip()
        self.default_repo = default_repo.strip() if default_repo else "owner/repo"
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = timeout
        self.state_manager = state_manager

        if session is not None:
            self._session = session
        elif requests is not None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Persistent-CrewAI-Framework/0.1.0",
            })
        else:
            self._session = None

    def _resolve_repo(self, repo: Optional[str]) -> str:
        """Resolve repository name in 'owner/repo' format."""
        if repo and repo.strip():
            return repo.strip()
        return self.default_repo

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute HTTP request against GitHub REST API and handle errors."""
        url = f"{self.api_base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Persistent-CrewAI-Framework/0.1.0",
        }

        try:
            if self._session is not None:
                resp = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json_data,
                    headers=headers,
                    timeout=self.timeout,
                )
                return self._handle_response(resp)
            else:
                # urllib fallback if requests is not installed
                import json
                import urllib.error
                import urllib.parse
                import urllib.request

                if params:
                    query = urllib.parse.urlencode(params)
                    url = f"{url}?{query}"

                req_data = json.dumps(json_data).encode("utf-8") if json_data is not None else None
                req = urllib.request.Request(url, data=req_data, headers=headers, method=method.upper())
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        status = resp.status
                        body = resp.read().decode("utf-8")
                        data = json.loads(body) if body else {}
                        return data
                except urllib.error.HTTPError as he:
                    body = he.read().decode("utf-8")
                    data = json.loads(body) if body else {}
                    self._raise_for_status(he.code, data, dict(he.headers))
        except (GitHubError, GitHubAuthError, GitHubNotFoundError, GitHubRateLimitError, GitHubValidationError, GitHubMethodNotAllowedError):
            raise
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                raise GitHubError(f"GitHub API request timed out: {e}", status_code=408) from e
            elif "connection" in err_str or "failed to connect" in err_str or "name or service not known" in err_str:
                raise GitHubError(f"Failed to connect to GitHub API: {e}", status_code=503) from e
            raise GitHubError(f"GitHub API unexpected network error: {e}", status_code=500) from e

    def _handle_response(self, resp: Any) -> Any:
        """Translate HTTP status codes to typed exceptions."""
        status_code = getattr(resp, "status_code", 200)
        headers = getattr(resp, "headers", {})

        # Parse body
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": getattr(resp, "text", "")}

        if 200 <= status_code < 300:
            return data

        self._raise_for_status(status_code, data, headers)

    def _raise_for_status(self, status_code: int, data: Any, headers: Any) -> None:
        """Map HTTP error status codes to typed GitHub exceptions."""
        msg = data.get("message", "GitHub API error") if isinstance(data, dict) else str(data)

        if status_code == 401:
            raise GitHubAuthError(f"GitHub authentication failed: {msg}", response_data=data)

        if status_code == 403:
            rem = headers.get("X-RateLimit-Remaining") if hasattr(headers, "get") else None
            reset_t = headers.get("X-RateLimit-Reset") if hasattr(headers, "get") else None
            if rem == "0" or "rate limit" in str(msg).lower():
                reset_int = int(reset_t) if reset_t and str(reset_t).isdigit() else None
                raise GitHubRateLimitError(
                    f"GitHub API rate limit exceeded: {msg}",
                    reset_time=reset_int,
                    response_data=data,
                )
            raise GitHubError(f"GitHub API forbidden: {msg}", status_code=403, response_data=data)

        if status_code == 404:
            raise GitHubNotFoundError(f"GitHub resource not found: {msg}", response_data=data)

        if status_code == 405:
            raise GitHubMethodNotAllowedError(f"GitHub method not allowed: {msg}", response_data=data)

        if status_code == 422:
            raise GitHubValidationError(f"GitHub validation failed: {msg}", response_data=data)

        raise GitHubError(f"GitHub API error [{status_code}]: {msg}", status_code=status_code, response_data=data)

    def _record_audit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    event_type=event_type,
                    payload=payload,
                    agent_role="GitHub Manager",
                    tool_name="RealGitHubAdapter",
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
        r = self._resolve_repo(repo)
        payload = {
            "title": title,
            "body": body,
            "labels": list(labels) if labels else [],
            "assignees": list(assignees) if assignees else [],
        }
        res = self._request("POST", f"repos/{r}/issues", json_data=payload)
        self._record_audit(
            "github_issue_created",
            {"repo": r, "issue_number": res.get("number"), "title": title},
        )
        return res

    def get_issue(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        r = self._resolve_repo(repo)
        return self._request("GET", f"repos/{r}/issues/{issue_number}")

    def list_issues(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        r = self._resolve_repo(repo)
        params: Dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)
        items = self._request("GET", f"repos/{r}/issues", params=params)
        # Exclude PRs returned by the issues endpoint
        if isinstance(items, list):
            return [i for i in items if "pull_request" not in i]
        return []

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
        r = self._resolve_repo(repo)
        payload: Dict[str, Any] = {}
        if state is not None:
            payload["state"] = state
        if body is not None:
            payload["body"] = body
        if title is not None:
            payload["title"] = title
        if labels is not None:
            payload["labels"] = list(labels)
        if assignees is not None:
            payload["assignees"] = list(assignees)

        res = self._request("PATCH", f"repos/{r}/issues/{issue_number}", json_data=payload)

        if comment is not None:
            self._request(
                "POST",
                f"repos/{r}/issues/{issue_number}/comments",
                json_data={"body": comment},
            )

        self._record_audit(
            "github_issue_updated",
            {"repo": r, "issue_number": issue_number, "state": state},
        )
        return res

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
        r = self._resolve_repo(repo)
        effective_base = from_branch if from_branch is not None else base
        if not effective_base:
            effective_base = "main"

        # 1. Resolve base branch commit SHA
        base_branch_data = self._request("GET", f"repos/{r}/branches/{effective_base}")
        base_sha = base_branch_data.get("commit", {}).get("sha")
        if not base_sha:
            raise GitHubValidationError(f"Could not resolve base branch SHA for '{effective_base}'")

        # 2. Create git ref for new branch
        ref_payload = {"ref": f"refs/heads/{branch_name}", "sha": base_sha}
        self._request("POST", f"repos/{r}/git/refs", json_data=ref_payload)

        self._record_audit(
            "github_branch_created",
            {"repo": r, "branch": branch_name, "base": effective_base},
        )
        return {"name": branch_name, "sha": base_sha, "from_branch": effective_base}

    def list_branches(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        r = self._resolve_repo(repo)
        raw_branches = self._request("GET", f"repos/{r}/branches")
        if isinstance(raw_branches, list):
            return [
                {
                    "name": b.get("name"),
                    "sha": b.get("commit", {}).get("sha", ""),
                    "protected": b.get("protected", False),
                }
                for b in raw_branches
            ]
        return []

    def create_commit(
        self,
        branch: str,
        message: str,
        files: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        r = self._resolve_repo(repo)
        # 1. Get branch head ref
        ref_data = self._request("GET", f"repos/{r}/git/refs/heads/{branch}")
        parent_sha = ref_data.get("object", {}).get("sha")

        # 2. Create commit
        commit_payload = {
            "message": message,
            "parents": [parent_sha] if parent_sha else [],
        }
        commit_res = self._request("POST", f"repos/{r}/git/commits", json_data=commit_payload)
        new_sha = commit_res.get("sha", parent_sha or "00000000")

        # 3. Update branch ref
        self._request("PATCH", f"repos/{r}/git/refs/heads/{branch}", json_data={"sha": new_sha})

        self._record_audit(
            "github_commit_created",
            {"repo": r, "branch": branch, "sha": new_sha, "message": message},
        )
        return {
            "sha": new_sha,
            "branch": branch,
            "message": message,
            "files": list(files) if files else [],
        }

    def list_commits(
        self,
        branch: Optional[str] = None,
        limit: int = 10,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        r = self._resolve_repo(repo)
        params: Dict[str, Any] = {"per_page": limit}
        if branch:
            params["sha"] = branch
        items = self._request("GET", f"repos/{r}/commits", params=params)
        if isinstance(items, list):
            return [
                {
                    "sha": c.get("sha", ""),
                    "message": c.get("commit", {}).get("message", ""),
                    "author": c.get("commit", {}).get("author", {}).get("name", ""),
                    "date": c.get("commit", {}).get("author", {}).get("date", ""),
                }
                for c in items
            ]
        return []

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
        r = self._resolve_repo(repo)
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }
        res = self._request("POST", f"repos/{r}/pulls", json_data=payload)
        res["head_branch"] = head_branch
        res["base_branch"] = base_branch
        self._record_audit(
            "github_pr_created",
            {"repo": r, "pr_number": res.get("number"), "title": title},
        )
        return res

    def get_pull_request(
        self,
        pr_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        r = self._resolve_repo(repo)
        res = self._request("GET", f"repos/{r}/pulls/{pr_number}")
        if isinstance(res, dict):
            res["head_branch"] = res.get("head", {}).get("ref", "")
            res["base_branch"] = res.get("base", {}).get("ref", "")
        return res

    def list_pull_requests(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        r = self._resolve_repo(repo)
        params = {"state": state}
        items = self._request("GET", f"repos/{r}/pulls", params=params)
        if isinstance(items, list):
            for item in items:
                item["head_branch"] = item.get("head", {}).get("ref", "")
                item["base_branch"] = item.get("base", {}).get("ref", "")
            return items
        return []

    def merge_pull_request(
        self,
        pr_number: int,
        commit_message: str = "",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        r = self._resolve_repo(repo)
        payload = {"commit_title": commit_message} if commit_message else {}
        res = self._request("PUT", f"repos/{r}/pulls/{pr_number}/merge", json_data=payload)
        self._record_audit(
            "github_pr_merged",
            {"repo": r, "pr_number": pr_number, "sha": res.get("sha")},
        )
        return {
            "merged": res.get("merged", True),
            "sha": res.get("sha", ""),
            "message": res.get("message", commit_message or f"Merge pull request #{pr_number}"),
        }

    # -------------------------------------------------------------------------
    # Repository Status
    # -------------------------------------------------------------------------
    def get_repo_status(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        r = self._resolve_repo(repo)
        res = self._request("GET", f"repos/{r}")
        return {
            "name": res.get("full_name", r),
            "default_branch": res.get("default_branch", "main"),
            "open_issues_count": res.get("open_issues_count", 0),
            "private": res.get("private", False),
            "html_url": res.get("html_url", ""),
        }


def create_github_adapter(
    token: Optional[str] = None,
    repo: Optional[str] = None,
    default_repo: Optional[str] = None,
    storage_path: Optional[Union[str, Path]] = None,
    force_mock: bool = False,
    workspace_root: Optional[Union[Path, str]] = None,
    api_base_url: Optional[str] = None,
    **kwargs: Any,
) -> GitHubAdapterInterface:
    """Factory creating MockGitHubAdapter or RealGitHubAdapter based on configuration.

    Resolution rules:
    1. If force_mock is True -> returns MockGitHubAdapter.
    2. If token is provided or found in environment (GITHUB_TOKEN / GH_TOKEN) -> returns RealGitHubAdapter.
    3. Otherwise -> defaults safely to MockGitHubAdapter for offline development and hermetic CI.
    """
    eff_repo = repo or default_repo or "owner/repo"

    if force_mock:
        resolved_path = (
            Path(storage_path).resolve()
            if storage_path is not None
            else get_default_mock_github_path(workspace_root)
        )
        return MockGitHubAdapter(
            storage_path=resolved_path,
            default_repo=eff_repo,
            **kwargs,
        )

    active_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if active_token and active_token.strip():
        return RealGitHubAdapter(
            token=active_token.strip(),
            default_repo=eff_repo,
            api_base_url=api_base_url or "https://api.github.com",
            **kwargs,
        )

    resolved_path = (
        Path(storage_path).resolve()
        if storage_path is not None
        else get_default_mock_github_path(workspace_root)
    )
    return MockGitHubAdapter(
        storage_path=resolved_path,
        default_repo=eff_repo,
        **kwargs,
    )
