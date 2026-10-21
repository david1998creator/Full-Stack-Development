"""
Abstract GitHub Adapter Interface and structured exception hierarchy.
Standardizes operations for issues, pull requests, branches, commits,
and repository status across Mock and Real implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class GitHubError(Exception):
    """Base exception for all GitHub adapter operations."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(f"GitHubError [{status_code}]: {message}")
        self.message = message
        self.status_code = status_code
        self.response = response or response_data or {}
        self.response_data = self.response


class GitHubNotFoundError(GitHubError):
    """Raised when an issue, pull request, branch, or repository is not found (HTTP 404)."""

    def __init__(
        self,
        message: str = "Resource not found",
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=404,
            response=response,
            response_data=response_data,
        )


class GitHubValidationError(GitHubError):
    """Raised on invalid request parameters or missing branch on PR creation (HTTP 422)."""

    def __init__(
        self,
        message: str = "Validation failed",
        status_code: int = 422,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            response=response,
            response_data=response_data,
        )


class GitHubMethodNotAllowedError(GitHubError):
    """Raised when operation cannot be performed, e.g. merging a closed PR (HTTP 405)."""

    def __init__(
        self,
        message: str = "Method not allowed",
        status_code: int = 405,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            response=response,
            response_data=response_data,
        )


class GitHubConflictError(GitHubError):
    """Raised on state conflicts, e.g. duplicate branch creation (HTTP 409)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        status_code: int = 409,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            response=response,
            response_data=response_data,
        )


class GitHubAuthError(GitHubError):
    """Raised when GitHub token is missing, expired, or invalid (HTTP 401)."""

    def __init__(
        self,
        message: str = "Authentication failed",
        status_code: int = 401,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            response=response,
            response_data=response_data,
        )


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limits are exceeded (HTTP 403)."""

    def __init__(
        self,
        message: str = "API rate limit exceeded",
        status_code: int = 403,
        reset_time: Optional[int] = None,
        response: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            response=response,
            response_data=response_data,
        )
        self.reset_time = reset_time


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
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Create a new issue. Returns issue entity dictionary."""
        pass

    @abstractmethod
    def get_issue(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Retrieve an issue by number. Raises GitHubNotFoundError if not found."""
        pass

    @abstractmethod
    def list_issues(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """List issues in repository, optionally filtered by state ('open', 'closed', 'all')."""
        pass

    @abstractmethod
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
        """Update an existing issue's state, title, body, comment, or labels."""
        pass

    # -------------------------------------------------------------------------
    # Pull Request Operations
    # -------------------------------------------------------------------------
    @abstractmethod
    def create_pull_request(
        self,
        title: str,
        body: str = "",
        head_branch: str = "feature",
        base_branch: str = "main",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Create a pull request. Raises GitHubError(422) if head branch does not exist."""
        pass

    @abstractmethod
    def get_pull_request(
        self,
        pr_number: int,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Retrieve a pull request by number. Raises GitHubNotFoundError if not found."""
        pass

    @abstractmethod
    def list_pull_requests(
        self,
        state: str = "open",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """List pull requests in repository ('open', 'closed', 'merged', 'all')."""
        pass

    @abstractmethod
    def merge_pull_request(
        self,
        pr_number: int,
        commit_message: str = "",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
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
    ) -> Dict[str, Any]:
        """Create a new branch from a base branch. Raises GitHubError(422) if duplicate or base missing."""
        pass

    @abstractmethod
    def list_branches(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """List all branches in repository."""
        pass

    @abstractmethod
    def create_commit(
        self,
        branch: str,
        message: str,
        files: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Record a commit on a branch with list of modified files."""
        pass

    @abstractmethod
    def list_commits(
        self,
        branch: Optional[str] = None,
        limit: int = 10,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """List commits for a branch or repository."""
        pass

    @abstractmethod
    def get_repo_status(
        self,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Retrieve summary status of repository."""
        pass
