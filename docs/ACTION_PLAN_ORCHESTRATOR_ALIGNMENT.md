# Action Plan: External Orchestrator Alignment (OpenCode, Claude, Antigravity)

**Source Document:** `c:\Users\User\Downloads\crewai-handoff.md`  
**Target Goal:** Make `Full-Stack-Development` (Persistent CrewAI 10-Role SDLC) fully orchestrable across **Antigravity**, **Claude (Desktop & Code)**, **OpenCode**, and **Cursor** without breaking existing capabilities.

---

## 1. Review & Triage Matrix (Filtering Necessary vs. Unnecessary Items)

| Item in Brief | Category | Recommendation | Technical Rationale |
| :--- | :---: | :---: | :--- |
| **P0-1: Unify Role Taxonomy** | P0 Blocker | **ACCEPTED (CRITICAL)** | `config.py:CANONICAL_ROLES` lists `Reviewer`, but `AGENTS.md` said `DevOps`. Common aliases like `qa`, `sdet`, `devops`, `db engineer` cause `KeyError`. Must unify on `config.py` and add aliases. |
| **P0-2: Fix False Offline Claim in Docs** | P0 Blocker | **ACCEPTED** | `README.md` must accurately state that live runs require an API key, while offline testing requires `--mock` or `MOCK_LLM=1`. |
| **P0-3: `decompose_project` Dead End** | P0 Blocker | **ACCEPTED (CRITICAL)** | `decompose_project` currently outputs a prose plan and registers Budget Guard, but doesn't insert tasks into SQLite. On approval, tasks must be inserted into SQLite so `get_pipeline_status()` and execution tools can run them. |
| **P0-4: OpenCode Config & Path Portability** | P0 Blocker | **ACCEPTED** | OpenCode ignores `mcp.json`. Add `opencode.json`. In `mcp_server.py`, resolve `sys.path` dynamically in Python rather than relying on Windows-specific `PYTHONPATH="src;."`. |
| **P1-1: Broken Quickstart Line** | P1 Correctness | **ACCEPTED** | `README.md` has `cd persistent_crewai_team` instead of `cd Full-Stack-Development`. 1-line fix. |
| **P1-2: OpenCode Skill Discovery** | P1 Correctness | **ACCEPTED** | Add `.opencode/skill/crewai-orchestrator/SKILL.md` alongside `.agents/skills/` (for Antigravity/Cursor) and `skills/`. |
| **P1-3: Mock Mode Produces No Files** | P1 Correctness | **ACCEPTED** | In `--mock` mode, Developer should generate an initial physical scaffold (e.g., `index.html`) so Playwright smoke tests succeed. |
| **P1-4: GitHub Actions CI** | P1 Quality | **ACCEPTED** | Add `.github/workflows/tests.yml` to run test suites on Python 3.11, 3.12, and 3.13. |
| **P1-5: `get_capabilities()` MCP Tool** | P1 Feature | **ACCEPTED** | Adds an MCP tool returning `mode` (`live`/`mock`), `available_providers`, `canonical_roles`, and paths. Highly valuable for orchestrators. |
| **Lockfile (`uv.lock`)** | P1 Optional | **REJECTED / DEFERRED** | Forcing `uv` adds friction for developers using standard pip and virtual environments on Windows/macOS. Standard `requirements.txt` and `pyproject.toml` are sufficient. |
| **Strict Error JSON Envelope Refactor** | P2 Hygiene | **ACCEPTED (LIGHTWEIGHT)** | Return structured error codes (`{"status": "error", "code": "MISSING_KEY", ...}`) in MCP responses. |

---

## 2. Action Plan: Phased Implementation

### Phase 1: Canonical Role Unification & Alias Expansion (P0-1)
1. **Source of Truth**: Lock `src/persistent_crewai/config.py:CANONICAL_ROLES` to the canonical 10:
   - `Coordinator`, `Architect`, `Designer`, `Database Engineer`, `Developer`, `Tester`, `Reviewer`, `Security Auditor`, `GitHub Manager`, `Memory & Status Manager`.
2. **Expand `ROLE_ALIASES` in `config.py`**:
   - `"qa"`, `"sdet"`, `"quality assurance"` $\to$ `"Tester"`
   - `"devops"`, `"devops engineer"`, `"infra"` $\to$ `"Architect"`
   - `"db"`, `"db engineer"` $\to$ `"Database Engineer"`
   - `"code review"`, `"code reviewer"` $\to$ `"Reviewer"`
   - `"secops"`, `"security"` $\to$ `"Security Auditor"`
   - `"git"` $\to$ `"GitHub Manager"`
3. **Synchronize Documentation**:
   - Update `README.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, and all `SKILL.md` files to reference the exact 10 canonical roles.

### Phase 2: MCP Server Execution Pipeline & Capabilities (P0-3, P1-5)
1. **Dynamic `sys.path` Resolution**:
   - In `src/persistent_crewai/mcp_server.py` and `mcp_server.py`, add `sys.path.insert(0, str(workspace_root / "src"))` so MCP works without requiring external `PYTHONPATH` environment variables.
2. **Real Task Creation in `decompose_project`**:
   - When `decompose_project` is called, parse the coordinator's generated tasks and write them into SQLite (`state_mgr.record_task_start()`), returning their task IDs and roles.
3. **Add Direct Task Execution & Creation Tools**:
   - `create_task(role, title, description, slug, priority)`: Allows external orchestrators (OpenCode/Claude) to inject custom tasks.
   - `execute_task(task_id, slug)`: Executes an assigned task on disk with self-correction.
4. **Add `get_capabilities()` Tool**:
   - Returns mode (`live` vs `mock`), detected API keys, database path, and canonical roles.

### Phase 3: Multi-Platform Orchestrator Configurations (P0-4, P1-1, P1-2)
1. **Create `opencode.json`**:
   - Add standard OpenCode MCP configuration at the repository root.
2. **Mirror Skill for OpenCode**:
   - Create `.opencode/skill/crewai-orchestrator/SKILL.md` (while preserving `.agents/skills/crewai-orchestrator/SKILL.md` for Antigravity and Cursor).
3. **Fix `README.md` Quickstart**:
   - Correct `cd persistent_crewai_team` $\to$ `cd Full-Stack-Development`.
   - Update offline claims to clearly state that runtime requires keys unless `--mock` is specified.

### Phase 4: Mock Artifact Scaffolding & CI Pipeline (P1-3, P1-4)
1. **Mock Artifact Scaffolding**:
   - In `ui/server.py` and `mcp_server.py`, when Developer executes in mock mode and no `index.html` exists in `projects/<slug>/`, write a clean initial HTML scaffold so Playwright smoke tests pass without errors.
2. **GitHub Actions CI Workflow**:
   - Add `.github/workflows/tests.yml` to automatically run unit tests on push/PR for Python 3.11, 3.12, and 3.13.

---

## 3. Verification & Acceptance Criteria

- [ ] `MOCK_LLM=1 python -c "from persistent_crewai.config import normalize_role; print([normalize_role(r) for r in ['devops', 'qa', 'db engineer', 'security auditor']])"` passes without raising.
- [ ] `python mcp_server.py` starts stdio server cleanly on Windows and Linux/macOS without needing `PYTHONPATH`.
- [ ] In OpenCode, Claude Desktop, and Antigravity:
  - `get_capabilities()` reports active mode and 10 canonical roles.
  - `decompose_project()` creates real tasks in SQLite.
  - `get_pipeline_status()` displays the created tasks.
  - `execute_agent_task()` executes tasks and produces physical files on disk.
- [ ] `python -m unittest tests/test_agents.py tests/test_pipeline.py` passes 100%.
- [ ] `python tests/e2e/test_milestones_and_hitl.py` passes with 0 console errors.
