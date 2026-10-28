"""
Real Git Repository Automation Tool for Persistent CrewAI.
Executes actual local Git commands (git init, branch, commit, status, diff)
within the physical project workspace directory.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.tools.workspace_tools import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class GitOperationResult:
    """Standardized output of a Git command execution."""
    success: bool
    command: str
    output: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "command": self.command,
            "output": self.output,
            "error": self.error,
        }


class RealGitRepoManager:
    """Manages physical git operations in a project directory."""

    def __init__(self, repo_dir: Union[str, Path]) -> None:
        self.repo_dir = Path(repo_dir).resolve()

    def _run_git(self, args: List[str]) -> GitOperationResult:
        cmd = ["git"] + args
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            success = (proc.returncode == 0)
            return GitOperationResult(
                success=success,
                command=" ".join(cmd),
                output=proc.stdout.strip(),
                error=proc.stderr.strip() if not success else None,
            )
        except Exception as e:
            return GitOperationResult(
                success=False,
                command=" ".join(cmd),
                output="",
                error=str(e),
            )

    def init_repo(self, initial_branch: str = "main") -> GitOperationResult:
        """Initialize git repository if not already initialized."""
        git_dir = self.repo_dir / ".git"
        if git_dir.exists():
            return GitOperationResult(success=True, command="git init", output="Repository already initialized.")
        res = self._run_git(["init", "-b", initial_branch])
        if res.success:
            # Set local git config if not set
            self._run_git(["config", "user.name", "Persistent-CrewAI-Agent"])
            self._run_git(["config", "user.email", "agent@persistent-crewai.local"])
        return res

    def create_branch(self, branch_name: str) -> GitOperationResult:
        """Create and switch to a new branch."""
        return self._run_git(["checkout", "-b", branch_name])

    def commit_all(self, message: str) -> GitOperationResult:
        """Stage all changes and commit with conventional message."""
        add_res = self._run_git(["add", "."])
        if not add_res.success:
            return add_res
        return self._run_git(["commit", "-m", message])

    def status(self) -> GitOperationResult:
        """Get git status porcelain output."""
        return self._run_git(["status", "--short"])

    def get_log(self, limit: int = 5) -> GitOperationResult:
        """Get git commit log."""
        return self._run_git(["log", f"-n{limit}", "--oneline"])

    def diff(self, target: str = "HEAD~1") -> GitOperationResult:
        """Get git diff output against target branch or previous commit."""
        return self._run_git(["diff", target])

    def get_current_branch(self) -> GitOperationResult:
        """Get current branch name."""
        return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])

    def create_pr_summary(self) -> Dict[str, Any]:
        """Generate a production Pull Request summary containing commit history and diff statistics."""
        branch = self.get_current_branch().output or "main"
        log = self.get_log(limit=10).output or "No commits"
        diff_stat = self._run_git(["diff", "--stat", "HEAD~1"]).output or "No diff stats"
        status = self.status().output or "Working directory clean"

        markdown_pr = (
            f"## Pull Request: Autonomous Swarm Build Summary\n\n"
            f"- **Branch:** `{branch}`\n"
            f"- **Working Tree Status:**\n```\n{status}\n```\n\n"
            f"### Recent Commits:\n```\n{log}\n```\n\n"
            f"### Diff Stat:\n```\n{diff_stat}\n```\n"
        )
        return {
            "branch": branch,
            "markdown": markdown_pr,
            "log": log,
            "diff_stat": diff_stat,
        }


class RealGitTool(BaseTool):
    """CrewAI-compatible BaseTool for GitHub Manager to run real local Git operations."""

    name: str = "local_git_tool"
    description: str = (
        "Run local Git repository commands: 'init', 'branch', 'commit', 'status', 'log', 'diff', 'pr_summary'. "
        "Usage: action='commit', message='feat: add user authentication', branch='feature/auth'."
    )
    repo_dir: Optional[Path] = None

    def __init__(self, repo_dir: Optional[Union[str, Path]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if repo_dir:
            self.repo_dir = Path(repo_dir).resolve()

    def _run(
        self,
        action: str = "status",
        message: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        root = self.repo_dir or Path.cwd()
        mgr = RealGitRepoManager(root)
        action_clean = action.lower().strip()

        if action_clean == "init":
            res = mgr.init_repo(initial_branch=branch or "main")
        elif action_clean in ("branch", "checkout"):
            if not branch:
                return "Error: 'branch' argument required for branch creation."
            res = mgr.create_branch(branch)
        elif action_clean == "commit":
            if not message:
                return "Error: 'message' argument required for commit."
            res = mgr.commit_all(message)
        elif action_clean == "log":
            res = mgr.get_log(limit=limit)
        elif action_clean == "diff":
            res = mgr.diff(target=branch or "HEAD~1")
        elif action_clean in ("pr_summary", "pr"):
            pr = mgr.create_pr_summary()
            return f"Git PR Summary Generated:\n\n{pr['markdown']}"
        else:
            res = mgr.status()

        if res.success:
            return f"Git [{action_clean}] SUCCESS:\n{res.output or 'OK'}"
        return f"Git [{action_clean}] FAILED:\n{res.error or res.output}"
