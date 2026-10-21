# Technical Analysis: Real GitHub Adapter, Adapter Factory, and CrewAI Tool Wrappers

**Milestone**: M2 (Workspace & GitHub Tooling)  
**Author**: Explorer M2-3  
**Status**: Completed Analysis & Architecture  
**Target Files Analyzed**:
- `src/persistent_crewai/tools/real_github.py`
- `src/persistent_crewai/tools/github_adapter_factory.py`
- `src/persistent_crewai/tools/__init__.py`
- `tests/test_tools.py`

---

## 1. Executive Summary

Milestone 2 establishes the dual-mode operational tooling for the 8-agent engineering framework:
1. **Sandboxed Workspace Tools** (investigated by Explorer M2-1): Sandboxed local file operations (read, write, list, patch).
2. **Abstract Interface & Mock GitHub Adapter** (investigated by Explorer M2-2): `GitHubAdapterInterface` and `MockGitHubAdapter` with persistent JSON simulation.
3. **Real GitHub Adapter, Factory, and CrewAI Tool Wrappers** (this investigation, Explorer M2-3):
   - Production-ready `RealGitHubAdapter` communicating with GitHub REST API v3 (`https://api.github.com`).
   - Resilient `create_github_adapter()` factory safely auto-detecting `GITHUB_TOKEN` / `GH_TOKEN` or falling back to `MockGitHubAdapter`.
   - Universal `BaseTool` compatible agent tool wrappers (`IssueCreateTool`, `PRCreateTool`, `BranchCreateTool`, `CommitCreateTool`, `RepoStatusTool`, and Workspace tools).
   - Dynamic `get_agent_tools(role: str)` assignment function matching roles to permitted capabilities.
   - Comprehensive unit test plan in `tests/test_tools.py` covering mock HTTP network calls, error translations, factory switching, and tool executions.

---

## 2. Real GitHub Adapter Architecture (`src/persistent_crewai/tools/real_github.py`)

### 2.1 Interface & Protocol Alignment
`RealGitHubAdapter` implements the contract defined in `GitHubAdapterInterface`:

```
                    GitHubAdapterInterface (Protocol / ABC)
                               ▲
               ┌───────────────┴───────────────┐
               │                               │
        MockGitHubAdapter              RealGitHubAdapter
   (Local JSON state simulation)     (GitHub REST API v3 Client)
```

Every method signature, parameter name, default argument, and return type in `RealGitHubAdapter` matches `GitHubAdapterInterface` exactly.

### 2.2 HTTP Network Layer & Authentication
- **HTTP Client**: Uses `requests.Session` for persistent connection pooling, standard headers, and configurable timeouts.
- **Base URL**: Defaults to `https://api.github.com`, configurable via `api_base_url` for GitHub Enterprise or test mock servers.
- **Request Headers**:
  - `Authorization: Bearer <token>`
  - `Accept: application/vnd.github.v3+json`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `User-Agent: Persistent-CrewAI-Framework/0.1.0`
- **Timeout**: Enforces a default 15-second timeout on all requests to prevent agent execution stalls.

### 2.3 Comprehensive Endpoint Mapping & Method Specifications

| Method | HTTP Verb | GitHub REST API v3 Path | Request Payload / Params | Return Schema |
|---|---|---|---|---|
| `get_repo_status(repo)` | GET | `/repos/{owner}/{repo}` | None | Dict: `name`, `full_name`, `default_branch`, `open_issues_count`, `private`, `html_url` |
| `list_issues(repo, state="open")` | GET | `/repos/{owner}/{repo}/issues?state={state}` | Query params: `state` | List[Dict]: Filtered to exclude PRs (`"pull_request" not in item`) |
| `get_issue(repo, issue_number)` | GET | `/repos/{owner}/{repo}/issues/{issue_number}` | None | Dict: `number`, `title`, `body`, `state`, `labels`, `created_at`, `updated_at` |
| `create_issue(repo, title, body, labels, assignees)` | POST | `/repos/{owner}/{repo}/issues` | JSON: `{"title": ..., "body": ..., "labels": ..., "assignees": ...}` | Dict: created issue with `number`, `state="open"`, etc. |
| `update_issue(repo, issue_number, state, comment, title, body, labels)` | PATCH / POST | `/repos/{owner}/{repo}/issues/{issue_number}` and `.../comments` | PATCH JSON: state/title/body/labels; POST JSON: `{"body": comment}` | Dict: updated issue record |
| `list_pull_requests(repo, state="open")` | GET | `/repos/{owner}/{repo}/pulls?state={state}` | Query params: `state` | List[Dict]: `number`, `title`, `body`, `state`, `head_branch`, `base_branch`, `merged` |
| `get_pull_request(repo, pr_number)` | GET | `/repos/{owner}/{repo}/pulls/{pr_number}` | None | Dict: `number`, `title`, `body`, `state`, `head_branch`, `base_branch`, `merged` |
| `create_pull_request(repo, title, body, head_branch, base_branch="main")` | POST | `/repos/{owner}/{repo}/pulls` | JSON: `{"title": ..., "body": ..., "head": ..., "base": ...}` | Dict: created PR with `number`, `state="open"`, etc. |
| `merge_pull_request(repo, pr_number, commit_message)` | PUT | `/repos/{owner}/{repo}/pulls/{pr_number}/merge` | JSON: `{"commit_title": commit_message}` | Dict: `{"merged": True, "sha": ..., "message": ...}` |
| `list_branches(repo)` | GET | `/repos/{owner}/{repo}/branches` | None | List[Dict]: `[{"name": ..., "sha": ...}, ...]` |
| `create_branch(repo, branch_name, from_branch="main")` | POST | `/repos/{owner}/{repo}/git/refs` | 1. Resolve `from_branch` SHA via GET `/branches/{from_branch}`<br>2. POST `{"ref": "refs/heads/{name}", "sha": sha}` | Dict: `{"name": branch_name, "sha": sha, "from_branch": from_branch}` |
| `create_commit(repo, branch, message, files)` | POST | Git Data API / Commit Tree | 1. Get branch commit SHA<br>2. Create commit via `/git/commits`<br>3. Update ref via `/git/refs/heads/{branch}` | Dict: `{"sha": new_sha, "branch": branch, "message": message, "files": files or []}` |
| `list_commits(repo, branch=None, limit=10)` | GET | `/repos/{owner}/{repo}/commits` | Query params: `sha={branch}`, `per_page={limit}` | List[Dict]: `[{"sha": ..., "message": ..., "author": ..., "date": ...}, ...]` |

### 2.4 Error Taxonomy & Exception Handling
GitHub API status codes must map to explicit exception types that inherit from `GitHubError`:

```python
class GitHubError(Exception):
    """Base exception for all GitHub adapter operations."""
    def __init__(self, message: str, status_code: int = 500, response_data: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data or {}

class GitHubAuthError(GitHubError):
    """Raised on HTTP 401 Unauthorized or bad credentials."""
    def __init__(self, message: str = "Invalid or expired GITHUB_TOKEN", response_data: Any = None):
        super().__init__(message, status_code=401, response_data=response_data)

class GitHubNotFoundError(GitHubError):
    """Raised on HTTP 404 Not Found."""
    def __init__(self, message: str = "GitHub resource not found", response_data: Any = None):
        super().__init__(message, status_code=404, response_data=response_data)

class GitHubRateLimitError(GitHubError):
    """Raised on HTTP 403 Rate Limit Exceeded."""
    def __init__(self, message: str = "GitHub API rate limit exceeded", reset_time: Optional[int] = None, response_data: Any = None):
        super().__init__(message, status_code=403, response_data=response_data)
        self.reset_time = reset_time

class GitHubValidationError(GitHubError):
    """Raised on HTTP 422 Unprocessable Entity (e.g. invalid branch or PR validation error)."""
    def __init__(self, message: str = "GitHub API validation failed", status_code: int = 422, response_data: Any = None):
        super().__init__(message, status_code=status_code, response_data=response_data)
```

#### Exception Translation Rules in `_handle_response(resp)`:
1. **HTTP 200 / 201 / 204**: Return parsed JSON (or empty dict for 204).
2. **HTTP 401**: Check for `"Bad credentials"` or unauthorized -> raise `GitHubAuthError`.
3. **HTTP 403**:
   - Inspect headers: `X-RateLimit-Remaining: 0`.
   - Extract `X-RateLimit-Reset` Unix epoch timestamp.
   - If rate limit triggered -> raise `GitHubRateLimitError(message, reset_time=reset_time)`.
   - Otherwise -> raise `GitHubError("Access forbidden", status_code=403)`.
4. **HTTP 404**: Raise `GitHubNotFoundError` (matching `test_e2e_tier2_boundaries.py:328`).
5. **HTTP 405**: Method Not Allowed (e.g., merging an unmergeable / already closed PR) -> raise `GitHubError("Pull request is not open or cannot be merged", status_code=405)`.
6. **HTTP 422**: Validation Failed (e.g., head branch does not exist for PR, or branch already exists) -> raise `GitHubValidationError(message, status_code=422)`.
7. **Network Timeouts & Connection Failures**:
   - `requests.exceptions.Timeout` -> raise `GitHubError("GitHub API request timed out", status_code=408)`.
   - `requests.exceptions.ConnectionError` -> raise `GitHubError("Failed to connect to GitHub API", status_code=503)`.

---

## 3. Adapter Factory Architecture (`src/persistent_crewai/tools/github_adapter_factory.py`)

### 3.1 Design Principles
1. **Zero-Configuration Offline Default**: If no token is provided and none exists in the environment, fallback gracefully to `MockGitHubAdapter` without raising exceptions.
2. **Explicit Override**: `force_mock=True` forces `MockGitHubAdapter` even if `GITHUB_TOKEN` is present in the environment (essential for hermetic test execution).
3. **Token Sanitization**: Disregard empty strings or whitespace-only values.
4. **Persistence Path Binding**: Ensure `MockGitHubAdapter` saves to `<workspace_root>/.persistent_crewai/mock_github.json` unless a custom `storage_path` is explicitly passed.

### 3.2 Factory Implementation Strategy

```python
def create_github_adapter(
    force_mock: bool = False,
    token: Optional[str] = None,
    storage_path: Optional[str | Path] = None,
    workspace_root: Optional[Path | str] = None,
    api_base_url: Optional[str] = None,
) -> GitHubAdapterInterface:
    """Create and return a GitHub adapter instance (Mock or Real).
    
    Resolution order:
    1. If `force_mock` is True -> MockGitHubAdapter.
    2. If `token` is provided or found in os.environ (`GITHUB_TOKEN` or `GH_TOKEN`) -> RealGitHubAdapter.
    3. Otherwise -> MockGitHubAdapter with informational log.
    """
    if force_mock:
        resolved_path = storage_path or get_default_mock_github_path(workspace_root)
        return MockGitHubAdapter(storage_path=resolved_path)

    # Check environment or parameter for token
    active_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if active_token and active_token.strip():
        return RealGitHubAdapter(
            token=active_token.strip(),
            api_base_url=api_base_url or "https://api.github.com",
        )

    # Safe fallback to MockGitHubAdapter
    resolved_path = storage_path or get_default_mock_github_path(workspace_root)
    return MockGitHubAdapter(storage_path=resolved_path)
```

Both `from persistent_crewai.tools.github_adapter_factory import create_github_adapter` (used in `tests/e2e/helpers.py`) and `from persistent_crewai.tools import create_github_adapter` must be supported via exports in `__init__.py`.

---

## 4. Agent Tool Classes & CrewAI Tool Wrappers (`src/persistent_crewai/tools/`)

### 4.1 Dual-Mode BaseTool Foundation
In production, CrewAI uses `crewai.tools.BaseTool` (which extends Pydantic `BaseModel`). However, to ensure hermetic offline testing and prevent import failures when `crewai` is not installed or when running under standard Python `unittest`, we implement a resilient `BaseTool` abstraction:

```python
try:
    from crewai.tools import BaseTool as CrewAIBaseTool
except (ImportError, Exception):
    from pydantic import BaseModel
    class CrewAIBaseTool(BaseModel):
        name: str = ""
        description: str = ""
        def run(self, *args, **kwargs) -> Any:
            return self._run(*args, **kwargs)
        def _run(self, *args, **kwargs) -> Any:
            raise NotImplementedError
```

Each tool class inherits from `CrewAIBaseTool` and provides:
1. **Direct method access**: e.g. `tool.write_file(...)`, `tool.read_file(...)`, `tool.create_issue(...)` (used in direct testing and CLI commands).
2. **CrewAI `_run(...)` method**: String-in, String-out execution consuming agent LLM function-call parameters and returning formatted text or JSON.
3. **Callable interface**: `__call__(*args, **kwargs)` forwarding to `run()`.

### 4.2 Comprehensive Tool Catalog

#### 1. Workspace File Tools (`src/persistent_crewai/tools/workspace_tools.py`)
- `WorkspaceFileReadTool` (Alias: `WorkspaceReadTool`):
  - `name`: `"read_workspace_file"`
  - Direct method: `read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str`
  - Agent `_run`: Formats content with line numbers.
- `WorkspaceFileWriteTool` (Alias: `WorkspaceWriteTool`):
  - `name`: `"write_workspace_file"`
  - Direct method: `write_file(file_path: str, content: str, overwrite: bool = True) -> str`
  - Agent `_run`: Writes atomically via temp file; returns confirmation string.
- `WorkspaceFileListTool` (Alias: `WorkspaceListTool`):
  - `name`: `"list_workspace_files"`
  - Direct method: `list_files(directory_path: str = ".", recursive: bool = False, pattern: Optional[str] = None) -> list[dict]`
  - Agent `_run`: Returns formatted JSON or markdown list of files.
- `WorkspaceFilePatchTool` (Alias: `WorkspacePatchTool`):
  - `name`: `"patch_workspace_file"`
  - Direct method: `patch_file(file_path: str, search_block: str, replace_block: str) -> str`
  - Agent `_run`: Replaces exact snippet; rejects ambiguous or missing blocks with `ValueError`.

#### 2. GitHub Automation Tools (`src/persistent_crewai/tools/github_tools.py`)
- `IssueCreateTool` (Alias: `GitHubIssueTool`):
  - `name`: `"github_issue_tool"`
  - Methods: `create_issue(...)`, `list_issues(...)`, `get_issue(...)`, `update_issue(...)`
  - `_run(action: str, repo: str, ...)`: Dispatcher returning formatted string to LLM.
- `PRCreateTool` (Alias: `GitHubPRTool`):
  - `name`: `"github_pr_tool"`
  - Methods: `create_pull_request(...)`, `list_pull_requests(...)`, `get_pull_request(...)`, `merge_pull_request(...)`
  - `_run(action: str, repo: str, ...)`: Handles PR lifecycle operations.
- `BranchCreateTool` (Alias: `GitHubBranchTool`):
  - `name`: `"github_branch_tool"`
  - Methods: `create_branch(...)`, `list_branches(...)`
- `CommitCreateTool` (Alias: `GitHubCommitTool`):
  - `name`: `"github_commit_tool"`
  - Methods: `create_commit(...)`, `list_commits(...)`
- `RepoStatusTool`:
  - `name`: `"github_repo_status_tool"`
  - Method: `get_repo_status(repo: str) -> dict`

#### 3. State & Memory Tools (`src/persistent_crewai/tools/state_tools.py`)
- `RecordDecisionTool`: Records architectural decisions directly to SQLite `decisions` table.
- `RecordMilestoneTool`: Creates or updates milestone status in `milestones` table.
- `QueryMemoryTool`: Queries `SQLiteVectorStore` and FTS5 memory index for project context.

### 4.3 Agent Role Tool Assignment (`get_agent_tools`)
Role normalization via `normalize_role()` maps user/configuration strings to canonical roles:

| Agent Role | Assigned Tools | Rationale |
|---|---|---|
| **Developer** | `WorkspaceFileReadTool`, `WorkspaceFileWriteTool`, `WorkspaceFilePatchTool`, `WorkspaceFileListTool` | Primary file authoring and modification |
| **Tester** | `WorkspaceFileReadTool`, `WorkspaceFileListTool` | Inspect workspace files and verify test outputs |
| **GitHub Manager** | `IssueCreateTool`, `PRCreateTool`, `BranchCreateTool`, `CommitCreateTool`, `RepoStatusTool` | Issue tracking, branches, PR creation, and merges |
| **Memory & Status Manager** | `RecordDecisionTool`, `RecordMilestoneTool`, `QueryMemoryTool` | Persistence management, milestones, and semantic recall |
| **Architect** | `WorkspaceFileReadTool`, `WorkspaceFileListTool`, `RecordDecisionTool`, `QueryMemoryTool` | Architecture decisions, system blueprints |
| **Designer** | `WorkspaceFileReadTool`, `WorkspaceFileWriteTool`, `WorkspaceFileListTool` | UI/UX specifications, mockups, design tokens |
| **Reviewer** | `WorkspaceFileReadTool`, `WorkspaceFileListTool`, `QueryMemoryTool` | Code review and audit inspection |
| **Coordinator / Manager** | `WorkspaceFileListTool`, `QueryMemoryTool`, `RecordMilestoneTool` | Project tracking, task delegation |

---

## 5. Comprehensive Unit Test Plan (`tests/test_tools.py`)

The unit test suite for Milestone 2 tools must execute completely offline, without network calls or external dependencies, adhering to `unittest.TestCase`.

### Test Architecture

```
tests/test_tools.py
├── TestRealGitHubAdapterRequestsMocked (15 test cases)
│   ├── test_real_github_headers_and_auth
│   ├── test_get_repo_status_success
│   ├── test_list_issues_filters_prs
│   ├── test_get_issue_success
│   ├── test_create_issue_success
│   ├── test_update_issue_comment_and_state
│   ├── test_list_pull_requests_success
│   ├── test_get_pull_request_success
│   ├── test_create_pull_request_success
│   ├── test_merge_pull_request_success
│   ├── test_list_branches_success
│   ├── test_create_branch_ref_resolution
│   ├── test_create_commit_success
│   ├── test_list_commits_with_branch_and_limit
│   └── test_custom_api_base_url
├── TestRealGitHubAdapterErrorHandling (8 test cases)
│   ├── test_401_unauthorized_raises_github_auth_error
│   ├── test_404_not_found_raises_github_not_found_error
│   ├── test_403_rate_limit_exceeded_raises_rate_limit_error
│   ├── test_403_forbidden_other_raises_github_error
│   ├── test_422_validation_error_raises_validation_error
│   ├── test_405_method_not_allowed_raises_github_error
│   ├── test_timeout_raises_github_error_with_408
│   └── test_connection_error_raises_github_error_with_503
├── TestGitHubAdapterFactory (6 test cases)
│   ├── test_force_mock_returns_mock_adapter
│   ├── test_token_in_env_returns_real_adapter
│   ├── test_gh_token_alias_in_env_returns_real_adapter
│   ├── test_explicit_token_arg_returns_real_adapter
│   ├── test_missing_token_defaults_to_mock_adapter
│   └── test_empty_whitespace_token_defaults_to_mock_adapter
├── TestCrewAIToolWrappers (10 test cases)
│   ├── test_issue_create_tool_direct_and_run
│   ├── test_pr_create_tool_direct_and_run
│   ├── test_branch_tool_direct_and_run
│   ├── test_commit_tool_direct_and_run
│   ├── test_repo_status_tool_direct_and_run
│   ├── test_workspace_tool_aliases
│   ├── test_get_agent_tools_developer
│   ├── test_get_agent_tools_github_manager
│   ├── test_get_agent_tools_memory_manager
│   └── test_get_agent_tools_unknown_role_raises_key_error
└── TestToolEdgeCasesAndSerialization (5 test cases)
    ├── test_tool_call_with_json_string_input
    ├── test_tool_exception_returns_clean_error_string
    ├── test_mock_github_persistence_reload
    ├── test_mock_github_unmergeable_pr
    └── test_mock_github_duplicate_branch
```

Total planned test cases in `tests/test_tools.py`: **44 unit test cases**, providing 100% contract coverage across all public methods and error paths.

---

## 6. Implementation Readiness & File Manifest

The following implementation files are planned for Milestone 2:
1. `src/persistent_crewai/tools/github_interface.py`: ABC `GitHubAdapterInterface`, `GitHubError`, `GitHubAuthError`, `GitHubNotFoundError`, `GitHubRateLimitError`, `GitHubValidationError`.
2. `src/persistent_crewai/tools/real_github.py`: `RealGitHubAdapter` using `requests.Session` with error translation and REST API v3 mapping.
3. `src/persistent_crewai/tools/mock_github.py`: `MockGitHubAdapter` with persistent JSON simulation.
4. `src/persistent_crewai/tools/github_adapter_factory.py`: `create_github_adapter()` with safe fallback.
5. `src/persistent_crewai/tools/workspace_tools.py`: Sandboxed file operations (`read_file`, `write_file`, `list_files`, `patch_file`, `validate_safe_path`).
6. `src/persistent_crewai/tools/github_tools.py`: CrewAI wrappers (`IssueCreateTool`, `PRCreateTool`, `BranchCreateTool`, `CommitCreateTool`, `RepoStatusTool`).
7. `src/persistent_crewai/tools/state_tools.py`: CrewAI wrappers for persistence (`RecordDecisionTool`, `RecordMilestoneTool`, `QueryMemoryTool`).
8. `src/persistent_crewai/tools/__init__.py`: Unified exports and `get_agent_tools(role: str)`.
9. `tests/test_tools.py`: Comprehensive test suite containing 44 test cases.
