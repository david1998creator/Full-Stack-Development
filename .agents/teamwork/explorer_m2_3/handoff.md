# Handoff Report: Real GitHub Adapter, Adapter Factory & CrewAI Tool Wrappers

**Role**: Explorer M2-3  
**Status**: Task Complete (Hard Handoff)  
**Date**: 2026-10-13T21:28:30Z  
**Working Directory**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_3`  
**Reference Analysis**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_3\analysis.md`  

---

## 1. Observation

1. **Test Infrastructure & Milestone Availability Gates**:
   - `tests/e2e/helpers.py` (lines 60-77) checks Milestone 2 availability by importing:
     ```python
     from persistent_crewai.tools.workspace_tools import (
         WorkspaceFileReadTool,
         WorkspaceFileWriteTool,
         WorkspaceFileListTool,
         WorkspaceFilePatchTool,
         validate_safe_path,
     )
     from persistent_crewai.tools.github_interface import (
         GitHubAdapterInterface,
         GitHubError,
     )
     from persistent_crewai.tools.mock_github import MockGitHubAdapter
     from persistent_crewai.tools.github_adapter_factory import create_github_adapter
     ```
   - Running `python -m unittest discover -s tests -p "test_*.py"` executes 152 tests with:
     ```
     Ran 152 tests in 48.252s
     OK (skipped=38)
     ```
     All 38 skipped tests correspond to gated tests waiting for `HAS_TOOLS`, `HAS_AGENTS`, or `HAS_CLI`.

2. **Environment & Package Inventory**:
   - Running `python -c "import pydantic, requests; print('pydantic:', pydantic.__version__, 'requests:', requests.__version__)"` yields:
     ```
     pydantic: 2.12.5 requests: 2.33.0
     ```
   - `requirements.txt` contains `requests>=2.31.0` and `pydantic>=2.0.0,<3.0.0`.
   - `crewai` is listed in `pyproject.toml` and `requirements.txt`, but offline/zero-dependency test execution runs with `python -m unittest` without third-party agent frameworks required at test time.

3. **E2E Tool Contracts & Invariants**:
   - `tests/e2e/test_e2e_tier1_features.py:431-499`:
     - `create_github_adapter(force_mock=True, storage_path=str(ctx.mock_gh_path))` returns `MockGitHubAdapter`.
     - `gh.create_issue(repo=..., title=..., body=..., labels=[...])` returns dict with `number` and `state="open"`.
     - `gh.update_issue(repo=..., issue_number=1, state="closed", comment="Completed.")` updates status.
     - `gh.create_branch(repo=..., branch_name=..., from_branch="main")` creates branch.
     - `gh.create_commit(repo=..., branch=..., message=..., files=[...])` creates commit with `sha`.
     - `gh.create_pull_request(repo=..., title=..., body=..., head_branch=..., base_branch="main")` returns PR with `number` (starting at 101 in mock) and `state="open"`.
     - `gh.merge_pull_request(repo=..., pr_number=101, commit_message=...)` returns `{"merged": True, ...}`.
   - `tests/e2e/test_e2e_tier2_boundaries.py:260-331`:
     - Creating PR on nonexistent head branch raises `GitHubError` with `status_code=422`.
     - Merging closed or non-open PR raises `GitHubError` with `status_code=405`.
     - Creating duplicate branch raises `GitHubError` with `status_code=422`.
     - Getting nonexistent issue raises `GitHubNotFoundError` (subclass of `GitHubError`) with `status_code=404`.

4. **Project Requirements R3 & Specifications**:
   - `ORIGINAL_REQUEST.md` R3 requires operational integration with local workspace files and GitHub operations (via simulated mock adapter or GitHub API when tokens are available) for issue tracking and PR generation.
   - `PROJECT.md` Section "Interface Contracts" requires `get_agent_tools(role: str) -> list[BaseTool]`.

---

## 2. Logic Chain

1. **From Observation 1 & 3 (Contract & Gate Enforcement)**:
   - For Milestone 2 to unlock the 38 skipped tests, `src/persistent_crewai/tools/` must export `WorkspaceFileReadTool`, `WorkspaceFileWriteTool`, `WorkspaceFileListTool`, `WorkspaceFilePatchTool`, `validate_safe_path`, `GitHubAdapterInterface`, `GitHubError`, `MockGitHubAdapter`, and `create_github_adapter`.
   - In addition, `create_github_adapter` must reside in `src/persistent_crewai/tools/github_adapter_factory.py` and be exported in `src/persistent_crewai/tools/__init__.py`.

2. **From Observation 2 & Dispatch Instruction 1 (Real GitHub REST Client)**:
   - `RealGitHubAdapter` must communicate over HTTP REST API v3 using `requests.Session` (or `urllib.request`), passing `Authorization: Bearer <token>`, `Accept: application/vnd.github.v3+json`, and `User-Agent: Persistent-CrewAI-Framework/0.1.0`.
   - Every GitHub endpoint must translate HTTP status codes to standardized exceptions:
     - 401 -> `GitHubAuthError`
     - 403 (with `X-RateLimit-Remaining: 0`) -> `GitHubRateLimitError`
     - 404 -> `GitHubNotFoundError`
     - 405 -> `GitHubError(status_code=405)`
     - 422 -> `GitHubValidationError(status_code=422)`
   - This ensures 100% boundary test consistency with real GitHub behavior.

3. **From Observation 1 & Dispatch Instruction 2 (Adapter Factory Safe Fallback)**:
   - `create_github_adapter` must check `force_mock` first; if False, check `token` argument or `os.environ.get("GITHUB_TOKEN")` / `os.environ.get("GH_TOKEN")`.
   - If a valid token exists, instantiate `RealGitHubAdapter`; otherwise safely default to `MockGitHubAdapter(storage_path=...)` with an informational log.
   - This guarantees zero test suite breakage in air-gapped CI/test environments while enabling live GitHub execution when credentials are provided.

4. **From Observation 2 & Dispatch Instruction 3 (Dual-Mode BaseTool Wrappers)**:
   - To operate seamlessly whether `crewai` is installed or not, tool wrappers should inherit from `crewai.tools.BaseTool` when available, falling back to a lightweight Pydantic `BaseModel` tool class.
   - Each tool class (`WorkspaceReadTool`, `WorkspaceWriteTool`, `IssueCreateTool`, `PRCreateTool`, etc.) must provide direct helper methods (`read_file`, `write_file`, `create_issue`, etc.) as well as an agent `_run(**kwargs)` method.
   - `get_agent_tools(role: str)` dynamically maps canonical agent roles to their designated capabilities as defined in `PROJECT.md`.

5. **From Observation 1 & Dispatch Instruction 4 (Unit Test Plan)**:
   - `tests/test_tools.py` must test:
     1. Mocked HTTP calls for `RealGitHubAdapter` (15 tests).
     2. HTTP error translations (401, 403, 404, 405, 422, timeouts) (8 tests).
     3. Factory fallback and environment detection (6 tests).
     4. CrewAI tool classes and `get_agent_tools` role assignment (10 tests).
     5. Boundary/edge cases (5 tests).
   - All 44 unit tests execute in memory/mock mode with zero external network access.

---

## 3. Caveats

1. **External GitHub API Rate Limits in Production**:
   - When running with live tokens, GitHub imposes a limit of 5,000 requests/hour for authenticated personal access tokens. `GitHubRateLimitError` provides reset timestamps, but exponential backoff logic should be employed if high-frequency agent workflows are executed in live mode.
2. **Commit Creation over REST API**:
   - In GitHub REST API v3, creating commits programmatically without local git CLI requires the Git Data API (Tree -> Commit -> Ref update). Our specification provides this complete REST workflow.
3. **Pydantic Version Interop**:
   - Pydantic 2.x is used; tool schemas should use standard type hints and `Field` annotations without deprecated Pydantic 1.x syntax.

---

## 4. Conclusion

1. **`RealGitHubAdapter` Specification**:
   - Located at `src/persistent_crewai/tools/real_github.py`.
   - Inherits/implements `GitHubAdapterInterface`.
   - Uses `requests.Session` with Bearer auth, custom User-Agent, and 15s timeout.
   - Implements full issue, PR, branch, and commit lifecycles.
   - Maps 401, 403, 404, 405, 422 status codes to distinct `GitHubError` subclasses.

2. **`create_github_adapter()` Specification**:
   - Located at `src/persistent_crewai/tools/github_adapter_factory.py` and exported in `src/persistent_crewai/tools/__init__.py`.
   - Resolves `force_mock` -> `token`/`GITHUB_TOKEN`/`GH_TOKEN` -> fallback `MockGitHubAdapter`.

3. **CrewAI Tool Wrappers Specification**:
   - Standardized in `src/persistent_crewai/tools/github_tools.py`, `workspace_tools.py`, `state_tools.py`, and exported via `__init__.py`.
   - Dual-mode `BaseTool` support for both production CrewAI runs and offline tests.
   - `get_agent_tools(role: str)` correctly partitions tools across the 8 specialist roles.

4. **Unit Test Suite Specification**:
   - 44 comprehensive unit test cases planned in `tests/test_tools.py`.
   - Complements existing E2E tiers 1-4 and unlocks all 38 currently skipped tests when Milestone 2 is implemented.

---

## 5. Verification Method

To independently verify this strategy once implemented:

1. **Run Full Test Suite via unittest**:
   ```powershell
   python -m unittest discover -s tests -p "test_*.py" -v
   ```
   **Expected Outcome**: 0 errors, 0 failures, all Milestone 2 tests pass (TC-R3.1 through TC-R3.6 and TC-B.1 through TC-B.15).

2. **Run Dedicated Tools Unit Test Suite**:
   ```powershell
   python -m unittest tests.test_tools -v
   ```
   **Expected Outcome**: All 44 unit tests pass in < 5 seconds.

3. **Verify Factory Fallback & Live Switching**:
   ```python
   from persistent_crewai.tools.github_adapter_factory import create_github_adapter
   from persistent_crewai.tools.mock_github import MockGitHubAdapter
   from persistent_crewai.tools.real_github import RealGitHubAdapter

   # Test fallback without token
   adapter1 = create_github_adapter(force_mock=False)
   assert isinstance(adapter1, MockGitHubAdapter)

   # Test live instantiation with token
   adapter2 = create_github_adapter(token="ghp_test1234567890")
   assert isinstance(adapter2, RealGitHubAdapter)
   ```

4. **Verify Tool Export and Role Assignment**:
   ```python
   from persistent_crewai.tools import (
       get_agent_tools,
       IssueCreateTool,
       PRCreateTool,
       WorkspaceReadTool,
       WorkspaceWriteTool,
   )
   dev_tools = get_agent_tools("Developer")
   assert any(isinstance(t, WorkspaceWriteTool) for t in dev_tools)
   gh_tools = get_agent_tools("GitHub Manager")
   assert any(isinstance(t, IssueCreateTool) for t in gh_tools)
   ```

5. **Invalidation Conditions**:
   - If any tool crashes with `ModuleNotFoundError: crewai` when crewai is absent.
   - If `RealGitHubAdapter` raises raw HTTP 500/404 exceptions instead of typed `GitHubError`.
   - If `create_github_adapter()` attempts network connections when `GITHUB_TOKEN` is unset.
