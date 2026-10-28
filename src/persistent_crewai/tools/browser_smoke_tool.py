"""
Live Browser Smoke Testing and Verification Tool for Persistent CrewAI.
Uses Playwright to launch a headless Chromium browser, navigate to the running application,
check for console errors, verify DOM rendering, and capture validation screenshots.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.tools.workspace_tools import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class BrowserSmokeResult:
    """Standardized result of a live browser smoke test."""
    success: bool
    url: str
    status_code: Optional[int]
    page_title: str
    console_errors: List[str] = field(default_factory=list)
    screenshot_path: Optional[str] = None
    duration_seconds: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "url": self.url,
            "status_code": self.status_code,
            "page_title": self.page_title,
            "console_errors": self.console_errors,
            "screenshot_path": self.screenshot_path,
            "duration_seconds": round(self.duration_seconds, 2),
            "error_message": self.error_message,
        }


class PlaywrightSmokeTester:
    """Headless browser verification engine."""

    def __init__(self, project_root: Optional[Union[str, Path]] = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    def run_smoke_test(
        self,
        url: str = "http://127.0.0.1:8080/health",
        expected_title_contains: Optional[str] = None,
        expected_text: Optional[str] = None,
        timeout: int = 15,
        screenshot_name: str = "smoke_test_pass.png",
    ) -> BrowserSmokeResult:
        """
        Launches headless Chromium, visits URL, checks for errors, and captures a screenshot.
        """
        start = time.time()
        console_errors: List[str] = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserSmokeResult(
                success=False,
                url=url,
                status_code=None,
                page_title="",
                duration_seconds=time.time() - start,
                error_message="Playwright is not installed in the current environment.",
            )

        screenshot_dir = self.project_root / "docs"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_file = screenshot_dir / screenshot_name

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                # Collect console errors & uncaught exceptions
                page.on("pageerror", lambda err: console_errors.append(f"Uncaught Exception: {err}"))
                page.on("console", lambda msg: console_errors.append(f"Console {msg.type}: {msg.text}") if msg.type == "error" else None)

                response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                status_code = response.status if response else None
                title = page.title()

                # Basic validation
                success = True
                failure_reasons = []

                if status_code is not None and status_code >= 400:
                    success = False
                    failure_reasons.append(f"HTTP Error Status {status_code}")

                if expected_title_contains and expected_title_contains.lower() not in title.lower():
                    success = False
                    failure_reasons.append(f"Title '{title}' does not contain '{expected_title_contains}'")

                if expected_text:
                    content = page.content()
                    if expected_text.lower() not in content.lower():
                        success = False
                        failure_reasons.append(f"Page body does not contain expected text '{expected_text}'")

                if console_errors:
                    # Non-fatal if page rendered, but recorded
                    pass

                # Capture verification screenshot
                page.screenshot(path=str(screenshot_file), full_page=True)
                browser.close()

                duration = time.time() - start
                return BrowserSmokeResult(
                    success=success,
                    url=url,
                    status_code=status_code,
                    page_title=title,
                    console_errors=console_errors,
                    screenshot_path=str(screenshot_file),
                    duration_seconds=duration,
                    error_message="; ".join(failure_reasons) if failure_reasons else None,
                )

        except Exception as exc:
            duration = time.time() - start
            return BrowserSmokeResult(
                success=False,
                url=url,
                status_code=None,
                page_title="",
                console_errors=console_errors,
                duration_seconds=duration,
                error_message=f"Browser navigation failed: {str(exc)}",
            )


class BrowserSmokeTestTool(BaseTool):
    """BaseTool wrapper allowing agents (Tester/Reviewer) to run browser smoke tests."""

    name: str = "browser_smoke_test"
    description: str = (
        "Perform a live browser smoke test on a running application URL using headless Chromium. "
        "Checks HTTP status, verifies rendering, logs console errors, and takes a screenshot."
    )
    project_root: Optional[Path] = None

    def __init__(self, project_root: Optional[Union[str, Path]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_root:
            self.project_root = Path(project_root).resolve()

    def _run(
        self,
        url: str = "http://127.0.0.1:8080/health",
        expected_text: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        tester = PlaywrightSmokeTester(project_root=self.project_root)
        result = tester.run_smoke_test(url=url, expected_text=expected_text)

        if result.success:
            return (
                f"✅ BROWSER SMOKE TEST PASSED in {result.duration_seconds:.2f}s\n"
                f"- URL: {result.url}\n"
                f"- Status Code: {result.status_code}\n"
                f"- Title: {result.page_title}\n"
                f"- Screenshot: {result.screenshot_path}\n"
                f"- Console Errors: {len(result.console_errors)}"
            )
        else:
            return (
                f"❌ BROWSER SMOKE TEST FAILED in {result.duration_seconds:.2f}s\n"
                f"- URL: {result.url}\n"
                f"- Status Code: {result.status_code}\n"
                f"- Error: {result.error_message}\n"
                f"- Console Errors: {result.console_errors}"
            )
