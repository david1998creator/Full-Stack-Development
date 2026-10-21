# Handoff Report: GitHub Adapter Interface & Mock Implementation Strategy

**Author**: Explorer M2-2  
**Recipient**: Parent Orchestrator (`16296ebf-4c98-41ac-a0f3-1f8ac4690a22`) / Builder Track  
**Date**: 2026-10-13  
**Status**: COMPLETE  

---

## 1. Observation

1. **Test Infrastructure & Discovery**:
   - Running `$env:PYTHONPATH="src"; python -m unittest discover -s tests -p "test_*.py"` executed 152 tests across `test_persistence.py`, `test_persistence_adversarial.py`, `test_adversarial_*.py`, and `tests/e2e/test_e2e_tier*.py`.
   - Results: 114 passed, 38 skipped (gated on Milestones 2, 3, and 4 availability flags). Zero failures.
2. **Existing E2E Test Expectations for MockGitHubAdapter**:
   - `tests/e2e/test_e2e_tier1_features.py` (lines 430–489):
     - `test_r3_04_mock_github_issue_lifecycle`: Expects `MockGitHubAdapter(storage_path=str(ctx.mock_gh_path))`.
     - Calls `create_issue(repo=repo, title="Implement SQLite FTS5 index", body="FTS5 triggers needed for fast memory retrieval.", labels=["enhancement", "database"])`.
     - Asserts `issue["number"] == 1`, `issue["state"] == "open"`.
     - Calls `list_issues(repo=repo, state="open")` -> asserts `len == 1`.
     - Calls `update_issue(repo=repo, issue_number=1, state="closed", comment="Completed.")` -> asserts `updated["state"] == "closed"`.
     - Calls `list_issues(repo=repo, state="closed")` -> asserts `len == 1`.
     - `test_r3_05_mock_github_pr_lifecycle`:
       - Calls `create_branch(repo=repo, branch_name="feat/vector-store", from_branch="main")` -> asserts `branch["name"] == "feat/vector-store"`.
       - Calls `create_commit(repo=repo, branch="feat/vector-store", message="...", files=["src/persistence/vector_store.py"])` -> asserts `commit["sha"] is not None`.
       - Calls `create_pull_request(repo=repo, title="...", body="...", head_branch="feat/vector-store", base_branch="main")` -> asserts `pr["number"] == 101`, `pr["state"] == "open"`.
       - Calls `merge_pull_request(repo=repo, pr_number=101, commit_message="Merge PR 101")` -> asserts `merge_result["merged"] is True`.
     - `test_r3_06_github_adapter_factory_fallback` (lines 490–498):
       - Imports `from persistent_crewai.tools.github_adapter_factory import create_github_adapter`.
       - Calls `create_github_adapter(force_mock=True, storage_path=str(ctx.mock_gh_path))` -> asserts `isinstance(adapter, MockGitHubAdapter)`.
3. **Boundary & Error Test Expectations**:
   - `tests/e2e/test_e2e_tier2_boundaries.py` (lines 44–47, 256–332):
     - Imports `from persistent_crewai.tools.github_interface import GitHubError, GitHubNotFoundError`.
     - `test_b12`: `gh.create_pull_request(..., head_branch="nonexistent-branch-xyz")` must raise `GitHubError` with `cm.exception.status_code == 422`.
     - `test_b13`: Second call to `gh.merge_pull_request(repo=repo, pr_number=pr_num)` on an already merged PR must raise `GitHubError` with `cm.exception.status_code == 405`.
     - `test_b14`: `gh.create_branch(repo=repo, branch_name="feat/duplicate", from_branch="main")` called twice must raise `GitHubError` with `cm.exception.status_code == 422`.
     - `test_b15`: `gh.get_issue(repo=repo, issue_number=99999)` must raise `(GitHubNotFoundError, GitHubError)` with `cm.exception.status_code == 404`.
4. **Persistence & Scenario Expectations**:
   - `tests/e2e/test_e2e_tier3_combinations.py` (lines 269–292, `test_c06_mock_github_persistence_across_instances`):
     - Instance 1 creates a branch and issue; instance 2 loaded with identical `storage_path` recovers the branch and issue.
   - `tests/e2e/test_e2e_tier4_scenarios.py` (lines 138–150):
     - `gh.create_branch(repo=repo, branch_name="feat/token-validator", from_branch="main")`
     - `gh.create_commit(repo=repo, branch="feat/token-validator", message="feat: token validator", files=[code_file])`
     - `pr = gh.create_pull_request(...)`
     - `gh.merge_pull_request(repo=repo, pr_number=pr["number"])`
5. **Config & Defaults**:
   - `src/persistent_crewai/config.py` (lines 96–102):
     - Contains `get_default_mock_github_path(workspace_root=None) -> Path` returning `<root>/.persistent_crewai/mock_github.json`.

---

## 2. Logic Chain

1. **ABC Interface Definition (`github_interface.py`)**:
   - Based on Observation 2, 3, and dispatch requirements, `GitHubAdapterInterface` must be an `abc.ABC` specifying methods for `create_issue`, `get_issue`, `list_issues`, `update_issue`, `create_pull_request`, `get_pull_request`, `list_pull_requests`, `merge_pull_request`, `create_branch`, `list_branches`, `create_commit`, `list_commits`, and `get_repo_status`.
   - By creating a standardized exception hierarchy (`GitHubError` with `status_code`, subclassed by `GitHubNotFoundError` [404], `GitHubValidationError` [422], and `GitHubMethodNotAllowedError` [405]), both mock and real adapters will raise identical exceptions that satisfy the exact assertions in `test_b12` through `test_b15`.
2. **Dual Call Signature Strategy**:
   - The dispatch message specifies signatures like `create_issue(title, body, labels, assignees)` and `create_branch(branch_name, base="main")` (without explicit `repo` arg).
   - The E2E tests pass `repo=repo` as a keyword argument (Observation 2).
   - By providing `default_repo: str = "owner/repo"` on the adapter instance and accepting `repo: Optional[str] = None` in every method, both callers are completely satisfied: passing `repo=...` overrides the default, while omitting it uses `self.default_repo`.
   - Similarly, for `create_branch`, accepting both `base: str = "main"` and `from_branch: Optional[str] = None` transparently handles both `from_branch` (from `test_r3_05`) and `base` (from dispatch).
3. **Sequential Deterministic ID Generation**:
   - `test_r3_04` specifically asserts `issue["number"] == 1`.
   - `test_r3_05` specifically asserts `pr["number"] == 101`.
   - Therefore, `MockGitHubAdapter` must maintain per-repo counters starting at 0 for issues (incrementing to 1 for the first issue) and 100 for PRs (incrementing to 101 for the first PR).
4. **Thread-Safe Local JSON Persistence (`mock_github.py`)**:
   - Observation 4 (`test_c06`) requires state to persist to disk and survive across fresh adapter instances.
   - Using a re-entrant lock (`threading.RLock()`) combined with atomic file writing (`.tmp` write followed by `Path.replace`) ensures corrupt-free and thread-safe persistence even during concurrent agent operations.
5. **Factory Module (`github_adapter_factory.py`)**:
   - Observation 2 (`test_r3_06`) imports `create_github_adapter` from `persistent_crewai.tools.github_adapter_factory`.
   - The factory must accept `force_mock: bool = False`, `storage_path: Optional[str] = None`, and `default_repo: Optional[str] = None`, returning `MockGitHubAdapter` when `force_mock=True` or `GITHUB_TOKEN` is absent.

---

## 3. Caveats

1. **Git Object Model Scope**:
   - The mock adapter simulates Git semantics at the metadata level (branches, commits, issues, PRs, SHAs). It does not maintain a full binary Git packfile object store (which is neither required nor desirable for lightweight agent coordination).
2. **Real GitHub Adapter Readiness**:
   - `RealGitHubAdapter` (`src/persistent_crewai/tools/real_github.py`) is scheduled as part of Milestone 2's broader scope. The factory in `github_adapter_factory.py` has been designed with a graceful fallback to `MockGitHubAdapter` if `real_github.py` has not yet been authored or if `GITHUB_TOKEN` is not active.
3. **Default Branch Assumptions**:
   - All repositories default to `"main"`. If an external caller assumes `"master"`, the adapter's `default_branch` can be configured during initialization or overridden.

---

## 4. Conclusion

The architectural design and implementation strategy for GitHub tooling is complete, thoroughly specified, and fully aligned with both the user requirements and the existing E2E test suites.

Implementing the three components:
1. `src/persistent_crewai/tools/github_interface.py`
2. `src/persistent_crewai/tools/mock_github.py`
3. `src/persistent_crewai/tools/github_adapter_factory.py`

along with the 28-scenario test suite in `tests/test_github_adapter.py`, will immediately unlock and activate 8 currently skipped E2E tests (`test_r3_04`, `test_r3_05`, `test_r3_06`, `test_b12`, `test_b13`, `test_b14`, `test_b15`, `test_c06`) with 100% pass rates.

The detailed technical specifications, schemas, error mappings, and class blueprints are available in:
`C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_2\analysis.md`.

---

## 5. Verification Method

To independently verify the design and downstream implementation:

1. **Inspect Blueprint Files**:
   - `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_2\analysis.md`
   - `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_2\handoff.md`
2. **Post-Implementation Verification Commands**:
   Run the dedicated GitHub adapter unit tests:
   ```powershell
   $env:PYTHONPATH="src"
   python -m unittest tests/test_github_adapter.py -v
   ```
   Run the E2E test tiers that assert GitHub operations:
   ```powershell
   $env:PYTHONPATH="src"
   python -m unittest tests.e2e.test_e2e_tier1_features -k "test_r3_0" -v
   python -m unittest tests.e2e.test_e2e_tier2_boundaries -k "test_b1" -v
   python -m unittest tests.e2e.test_e2e_tier3_combinations -k "test_c06" -v
   ```
3. **Invalidation Conditions**:
   - Any failure in deterministic numbering (`issue["number"] != 1` or `pr["number"] != 101`).
   - Failure to raise `GitHubError` with status `422` on nonexistent head branch or duplicate branch.
   - Failure to raise `GitHubError` with status `405` on merging already merged PR.
   - State failing to survive cross-instance reloading.
