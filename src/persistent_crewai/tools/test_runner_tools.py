"""
Subprocess Test Runner and Test Execution Tool for Persistent CrewAI.
Executes test suites (pytest, unittest) in isolated project workspaces,
captures exit codes, stdout, stderr, and failure tracebacks.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.tools.workspace_tools import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class TestRunResult:
    """Standardized result of a subprocess test execution."""
    success: bool
    exit_code: int
    command: str
    duration_seconds: float
    stdout: str
    stderr: str
    failure_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "command": self.command,
            "duration_seconds": round(self.duration_seconds, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failure_summary": self.failure_summary,
        }


class SubprocessTestRunner:
    """Executes test commands inside target project directories."""

    def __init__(self, project_root: Union[str, Path]) -> None:
        self.project_root = Path(project_root).resolve()

    def run_tests(
        self,
        test_command: Optional[str] = None,
        timeout: int = 60,
    ) -> TestRunResult:
        """
        Executes pytest or unittest in project_root.
        """
        if not test_command:
            # Default to pytest if tests/ exists or unittest discover
            test_dir = self.project_root / "tests"
            if test_dir.exists():
                test_command = f'"{sys.executable}" -m unittest discover -s tests -p "test_*.py"'
            else:
                test_command = f'"{sys.executable}" -m unittest discover'

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{self.project_root};{self.project_root / 'src'}"

        start_time = time.time()
        try:
            proc = subprocess.run(
                test_command,
                shell=True,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=env,
            )
            duration = time.time() - start_time
            success = (proc.returncode == 0)

            failure_summary = None
            if not success:
                # Combine stdout and stderr for error extraction
                combined = f"{proc.stdout}\n{proc.stderr}"
                # Extract error lines (AssertionError, Exception, FAILED)
                error_lines = [line for line in combined.splitlines() if any(k in line for k in ("FAIL", "ERROR", "AssertionError", "Traceback", "SyntaxError", "ImportError"))]
                failure_summary = "\n".join(error_lines[:15]) if error_lines else combined[-500:]

            return TestRunResult(
                success=success,
                exit_code=proc.returncode,
                command=test_command,
                duration_seconds=duration,
                stdout=proc.stdout,
                stderr=proc.stderr,
                failure_summary=failure_summary,
            )

        except subprocess.TimeoutExpired:
            return TestRunResult(
                success=False,
                exit_code=-1,
                command=test_command,
                duration_seconds=float(timeout),
                stdout="",
                stderr=f"Test execution timed out after {timeout} seconds.",
                failure_summary=f"TimeoutExpired: Tests exceeded {timeout}s limit.",
            )
        except Exception as e:
            return TestRunResult(
                success=False,
                exit_code=1,
                command=test_command,
                duration_seconds=time.time() - start_time,
                stdout="",
                stderr=str(e),
                failure_summary=f"ExecutionException: {str(e)}",
            )


class RunTestsTool(BaseTool):
    """CrewAI-compatible BaseTool wrapper for the Tester agent to run real tests."""

    name: str = "run_tests"
    description: str = (
        "Run the project's automated test suite in a subprocess. "
        "Returns exit code, passing/failing status, and stack trace on failure."
    )
    project_root: Optional[Path] = None

    def __init__(self, project_root: Optional[Union[str, Path]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_root:
            self.project_root = Path(project_root).resolve()

    def _run(self, command: Optional[str] = None, **kwargs: Any) -> str:
        root = self.project_root or Path.cwd()
        runner = SubprocessTestRunner(project_root=root)
        result = runner.run_tests(test_command=command)

        if result.success:
            return (
                f"✅ ALL TESTS PASSED in {result.duration_seconds:.2f}s (Exit code: 0)\n"
                f"Output:\n{result.stdout[:800]}"
            )
        else:
            return (
                f"❌ TESTS FAILED (Exit code: {result.exit_code}) in {result.duration_seconds:.2f}s\n"
                f"Command: {result.command}\n"
                f"Failure Summary:\n{result.failure_summary}\n\n"
                f"Raw Errors:\n{result.stderr[:1000] or result.stdout[:1000]}"
            )
