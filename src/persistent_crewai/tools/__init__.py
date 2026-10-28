"""
Persistent CrewAI Tools Package.
Exports sandboxed workspace tools, GitHub adapter interface & implementations,
CrewAI BaseTool wrappers, and role-to-tool assignment mappings.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import ConfigDict, Field

from persistent_crewai.config import normalize_role
from persistent_crewai.tools.github_interface import (
    GitHubAdapterInterface,
    GitHubAuthError,
    GitHubConflictError,
    GitHubError,
    GitHubMethodNotAllowedError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from persistent_crewai.tools.mock_github import MockGitHubAdapter
from persistent_crewai.tools.real_github import RealGitHubAdapter, create_github_adapter
from persistent_crewai.tools.workspace_tools import (
    BaseTool,
    WorkspaceFileListTool,
    WorkspaceFilePatchTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriteTool,
    validate_safe_path,
)
from persistent_crewai.tools.test_runner_tools import RunTestsTool, SubprocessTestRunner
from persistent_crewai.tools.browser_smoke_tool import BrowserSmokeTestTool, PlaywrightSmokeTester
from persistent_crewai.tools.git_tool import RealGitTool, RealGitRepoManager
from persistent_crewai.tools.subagent_tool import SpawnAssistantTool

# Tool aliases
WorkspaceReadTool = WorkspaceFileReadTool
WorkspaceWriteTool = WorkspaceFileWriteTool
WorkspaceListTool = WorkspaceFileListTool
WorkspacePatchTool = WorkspaceFilePatchTool


# =============================================================================
# GitHub Automation Tools (CrewAI BaseTool Wrappers)
# =============================================================================
class IssueCreateTool(BaseTool):
    """Tool for creating and querying GitHub issues."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "github_issue_tool"
    description: str = "Create, view, and list GitHub issues in the repository."
    github_adapter: Any = Field(default=None, exclude=True)

    def __init__(self, github_adapter: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.github_adapter = github_adapter or create_github_adapter()

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.create_issue(
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
            repo=repo,
            **kwargs,
        )

    def get_issue(self, issue_number: int, repo: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return self.github_adapter.get_issue(issue_number=issue_number, repo=repo, **kwargs)

    def list_issues(self, state: str = "open", repo: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.github_adapter.list_issues(state=state, repo=repo, **kwargs)

    def update_issue(
        self,
        issue_number: int,
        state: Optional[str] = None,
        body: Optional[str] = None,
        comment: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.update_issue(
            issue_number=issue_number,
            state=state,
            body=body,
            comment=comment,
            repo=repo,
            **kwargs,
        )

    def _run(self, title: str, body: str = "", labels: Optional[List[str]] = None, **kwargs: Any) -> str:
        try:
            res = self.create_issue(title=title, body=body, labels=labels, **kwargs)
            return f"Issue #{res.get('number')} created: {res.get('title')}"
        except Exception as e:
            return f"Error: {e}"


GitHubIssueTool = IssueCreateTool


class PRCreateTool(BaseTool):
    """Tool for managing Pull Requests."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "github_pr_tool"
    description: str = "Create, view, and merge pull requests in the repository."
    github_adapter: Any = Field(default=None, exclude=True)

    def __init__(self, github_adapter: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.github_adapter = github_adapter or create_github_adapter()

    def create_pull_request(
        self,
        title: str,
        body: str = "",
        head_branch: str = "feature",
        base_branch: str = "main",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.create_pull_request(
            title=title,
            body=body,
            head_branch=head_branch,
            base_branch=base_branch,
            repo=repo,
            **kwargs,
        )

    def get_pull_request(self, pr_number: int, repo: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return self.github_adapter.get_pull_request(pr_number=pr_number, repo=repo, **kwargs)

    def list_pull_requests(self, state: str = "open", repo: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.github_adapter.list_pull_requests(state=state, repo=repo, **kwargs)

    def merge_pull_request(
        self,
        pr_number: int,
        commit_message: str = "",
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.merge_pull_request(
            pr_number=pr_number,
            commit_message=commit_message,
            repo=repo,
            **kwargs,
        )

    def _run(
        self,
        title: str,
        body: str = "",
        head_branch: str = "feature",
        base_branch: str = "main",
        **kwargs: Any,
    ) -> str:
        try:
            res = self.create_pull_request(
                title=title,
                body=body,
                head_branch=head_branch,
                base_branch=base_branch,
                **kwargs,
            )
            return f"Pull Request #{res.get('number')} created: {res.get('title')}"
        except Exception as e:
            return f"Error: {e}"


GitHubPRTool = PRCreateTool


class BranchCreateTool(BaseTool):
    """Tool for managing git branches."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "github_branch_tool"
    description: str = "Create and list repository branches."
    github_adapter: Any = Field(default=None, exclude=True)

    def __init__(self, github_adapter: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.github_adapter = github_adapter or create_github_adapter()

    def create_branch(
        self,
        branch_name: str,
        base: str = "main",
        from_branch: Optional[str] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.create_branch(
            branch_name=branch_name,
            base=base,
            from_branch=from_branch,
            repo=repo,
            **kwargs,
        )

    def list_branches(self, repo: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.github_adapter.list_branches(repo=repo, **kwargs)

    def _run(self, branch_name: str, base: str = "main", **kwargs: Any) -> str:
        try:
            res = self.create_branch(branch_name=branch_name, base=base, **kwargs)
            return f"Branch '{res.get('name')}' created at {res.get('sha')}"
        except Exception as e:
            return f"Error: {e}"


GitHubBranchTool = BranchCreateTool


class CommitCreateTool(BaseTool):
    """Tool for recording commits."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "github_commit_tool"
    description: str = "Create commits and inspect commit history."
    github_adapter: Any = Field(default=None, exclude=True)

    def __init__(self, github_adapter: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.github_adapter = github_adapter or create_github_adapter()

    def create_commit(
        self,
        branch: str,
        message: str,
        files: Optional[List[str]] = None,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.github_adapter.create_commit(
            branch=branch,
            message=message,
            files=files,
            repo=repo,
            **kwargs,
        )

    def list_commits(
        self,
        branch: Optional[str] = None,
        limit: int = 10,
        repo: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        return self.github_adapter.list_commits(branch=branch, limit=limit, repo=repo, **kwargs)

    def _run(self, branch: str, message: str, files: Optional[List[str]] = None, **kwargs: Any) -> str:
        try:
            res = self.create_commit(branch=branch, message=message, files=files, **kwargs)
            return f"Commit {res.get('sha')} created on branch '{branch}': {message}"
        except Exception as e:
            return f"Error: {e}"


GitHubCommitTool = CommitCreateTool


class RepoStatusTool(BaseTool):
    """Tool for querying repository metadata and health."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "github_repo_status_tool"
    description: str = "Get repository status, branch count, open issues, and PR counts."
    github_adapter: Any = Field(default=None, exclude=True)

    def __init__(self, github_adapter: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.github_adapter = github_adapter or create_github_adapter()

    def get_repo_status(self, repo: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return self.github_adapter.get_repo_status(repo=repo, **kwargs)

    def _run(self, repo: Optional[str] = None, **kwargs: Any) -> str:
        try:
            res = self.get_repo_status(repo=repo, **kwargs)
            return json.dumps(res, indent=2)
        except Exception as e:
            return f"Error: {e}"


GitHubRepoStatusTool = RepoStatusTool


# =============================================================================
# State & Persistence Tools (CrewAI BaseTool Wrappers)
# =============================================================================
class RecordDecisionTool(BaseTool):
    """Tool for recording architectural decisions into SQLite persistence."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "record_decision"
    description: str = "Record an architectural or technical decision to the persistent SQLite database."
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(self, state_manager: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.state_manager = state_manager

    def record_decision(
        self,
        agent_role: str,
        title: str,
        rationale: str,
        implications: str = "",
        status: str = "accepted",
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Any:
        if self.state_manager and hasattr(self.state_manager, "record_decision"):
            return self.state_manager.record_decision(
                agent_role=agent_role,
                title=title,
                rationale=rationale,
                implications=implications,
                status=status,
                tags=tags,
                **kwargs,
            )
        return 0

    def _run(
        self,
        agent_role: str,
        title: str,
        rationale: str,
        implications: str = "",
        **kwargs: Any,
    ) -> str:
        try:
            dec_id = self.record_decision(
                agent_role=agent_role,
                title=title,
                rationale=rationale,
                implications=implications,
                **kwargs,
            )
            return f"Decision #{dec_id} successfully recorded: '{title}'."
        except Exception as e:
            return f"Error: {e}"


class RecordMilestoneTool(BaseTool):
    """Tool for creating or updating project milestones."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "record_milestone"
    description: str = "Create or update project milestone progress in persistence."
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(self, state_manager: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.state_manager = state_manager

    def create_milestone(
        self,
        milestone_id: str,
        title: str,
        description: str = "",
        criteria: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Any:
        if self.state_manager and hasattr(self.state_manager, "create_milestone"):
            return self.state_manager.create_milestone(
                milestone_id=milestone_id,
                title=title,
                description=description,
                criteria=criteria,
                **kwargs,
            )
        return None

    def _run(self, milestone_id: str, title: str, description: str = "", **kwargs: Any) -> str:
        try:
            self.create_milestone(milestone_id=milestone_id, title=title, description=description, **kwargs)
            return f"Milestone '{milestone_id}' ('{title}') recorded."
        except Exception as e:
            return f"Error: {e}"


class QueryMemoryTool(BaseTool):
    """Tool for querying past decisions, milestones, and semantic memory."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "query_memory"
    description: str = "Query past decisions, task history, and semantic memory."
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(self, state_manager: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.state_manager = state_manager

    def query_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if not self.state_manager:
            return []
        if hasattr(self.state_manager, "get_vector_store"):
            try:
                vs = self.state_manager.get_vector_store()
                if vs and hasattr(vs, "search"):
                    results = vs.search(query, top_k=limit)
                    return [{"text": r.text, "score": r.score, "metadata": r.metadata} for r in results]
            except Exception:
                pass
        if hasattr(self.state_manager, "get_decisions"):
            try:
                return self.state_manager.get_decisions()[:limit]
            except Exception:
                pass
        return []

    def _run(self, query: str, limit: int = 5, **kwargs: Any) -> str:
        try:
            items = self.query_memory(query, limit=limit)
            return json.dumps(items, indent=2, default=str)
        except Exception as e:
            return f"Error: {e}"


# =============================================================================
# Agent Role Tool Assignment (`get_agent_tools`)
# =============================================================================
def get_agent_tools(
    role: str,
    workspace_root: Optional[Union[str, Path]] = None,
    github_adapter: Optional[GitHubAdapterInterface] = None,
    state_manager: Any = None,
    **kwargs: Any,
) -> List[BaseTool]:
    """Provide assigned tools to each role matching system architecture.

    Roles:
    - Developer: Read, Write, Patch, List files
    - Tester: Read, List files
    - Reviewer: Read, List files, Query memory
    - Designer: Read, Write, List files
    - GitHub Manager: Issue, PR, Branch, Commit, Repo status tools
    - Memory & Status Manager: Record decision, Record milestone, Query memory
    - Architect: Read, List files, Record decision, Query memory
    - Coordinator: List files, Query memory, Record milestone
    """
    canonical_role = normalize_role(role)

    # Instantiate workspace tools
    reader = WorkspaceFileReadTool(workspace_root=workspace_root, state_manager=state_manager)
    writer = WorkspaceFileWriteTool(workspace_root=workspace_root, state_manager=state_manager)
    lister = WorkspaceFileListTool(workspace_root=workspace_root, state_manager=state_manager)
    patcher = WorkspaceFilePatchTool(workspace_root=workspace_root, state_manager=state_manager)

    # Instantiate GitHub adapter and tools
    gh_adapter = github_adapter or create_github_adapter(workspace_root=workspace_root)
    issue_tool = IssueCreateTool(github_adapter=gh_adapter)
    pr_tool = PRCreateTool(github_adapter=gh_adapter)
    branch_tool = BranchCreateTool(github_adapter=gh_adapter)
    commit_tool = CommitCreateTool(github_adapter=gh_adapter)
    status_tool = RepoStatusTool(github_adapter=gh_adapter)

    # Instantiate state tools
    decision_tool = RecordDecisionTool(state_manager=state_manager)
    milestone_tool = RecordMilestoneTool(state_manager=state_manager)
    memory_tool = QueryMemoryTool(state_manager=state_manager)

    # Instantiate verification & real git tools
    test_tool = RunTestsTool(project_root=workspace_root)
    browser_tool = BrowserSmokeTestTool(project_root=workspace_root)
    git_tool = RealGitTool(repo_dir=workspace_root)
    subagent_tool = SpawnAssistantTool(workspace_root=workspace_root, state_manager=state_manager, parent_role=canonical_role)

    if canonical_role == "Developer":
        return [reader, writer, patcher, lister, subagent_tool]
    elif canonical_role == "Tester":
        return [reader, lister, test_tool, browser_tool]
    elif canonical_role == "Reviewer":
        return [reader, lister, memory_tool, browser_tool]
    elif canonical_role == "Designer":
        return [reader, writer, lister]
    elif canonical_role == "GitHub Manager":
        return [git_tool, issue_tool, pr_tool, branch_tool, commit_tool, status_tool]
    elif canonical_role == "Memory & Status Manager":
        return [decision_tool, milestone_tool, memory_tool]
    elif canonical_role == "Architect":
        return [reader, lister, writer, decision_tool, memory_tool]
    elif canonical_role == "Coordinator":
        return [lister, memory_tool, milestone_tool]
    else:
        raise KeyError(f"No tool mapping defined for role '{role}'")


# =============================================================================
# Package Exports and Module Aliasing
# =============================================================================
# Register persistent_crewai.tools.github_adapter_factory dynamically in sys.modules
# so `from persistent_crewai.tools.github_adapter_factory import create_github_adapter` works
_factory_mod = types.ModuleType("persistent_crewai.tools.github_adapter_factory")
_factory_mod.create_github_adapter = create_github_adapter
sys.modules["persistent_crewai.tools.github_adapter_factory"] = _factory_mod


__all__ = [
    # Sandboxed Workspace Tools
    "validate_safe_path",
    "BaseTool",
    "WorkspaceFileReadTool",
    "WorkspaceFileWriteTool",
    "WorkspaceFileListTool",
    "WorkspaceFilePatchTool",
    "WorkspaceReadTool",
    "WorkspaceWriteTool",
    "WorkspaceListTool",
    "WorkspacePatchTool",
    # GitHub Adapter Interface & Exceptions
    "GitHubAdapterInterface",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubValidationError",
    "GitHubMethodNotAllowedError",
    "GitHubConflictError",
    "GitHubAuthError",
    "GitHubRateLimitError",
    # Adapters & Factory
    "MockGitHubAdapter",
    "RealGitHubAdapter",
    "create_github_adapter",
    # GitHub Agent Tools
    "IssueCreateTool",
    "PRCreateTool",
    "BranchCreateTool",
    "CommitCreateTool",
    "RepoStatusTool",
    "GitHubIssueTool",
    "GitHubPRTool",
    "GitHubBranchTool",
    "GitHubCommitTool",
    "GitHubRepoStatusTool",
    # State Tools
    "RecordDecisionTool",
    "RecordMilestoneTool",
    "QueryMemoryTool",
    # Verification & Git Tools
    "RunTestsTool",
    "SubprocessTestRunner",
    "BrowserSmokeTestTool",
    "PlaywrightSmokeTester",
    "RealGitTool",
    "RealGitRepoManager",
    "SpawnAssistantTool",
    # Role Tool Assignment
    "get_agent_tools",
]
