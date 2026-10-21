# E2E Test Infrastructure Specification & Architecture

## 1. Test Philosophy

The test infrastructure for the **Persistent CrewAI 8-Agent Software Engineering Framework** is built upon four fundamental principles:

1. **Opaque-Box Testing**:
   - Tests exercise public interface contracts, APIs, CLI commands, and database state rather than probing internal implementation details or private variables.
   - Behavior and contract compliance take precedence over implementation layout.
2. **Requirement-Driven & Authoritative Traceability**:
   - Every single test case traces directly back to requirements R1 through R4 in `ORIGINAL_REQUEST.md` and technical specifications in `PROJECT.md` and `specifications.md`.
   - Expected outputs are derived from explicit requirements, mathematical properties (e.g. vector $L_2$ norm $= 1.0$), or documented API contracts.
3. **Zero External Network Dependency & Hermetic Execution**:
   - The entire test suite executes completely offline without external internet access or live API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN`).
   - Testing leverages deterministic components: `DeterministicHashEmbeddingProvider`, `MockGitHubAdapter`, and `DeterministicMockLLM`.
   - All tests run in isolated temporary directories (`tempfile.TemporaryDirectory`) with dedicated SQLite databases and mock workspace roots, leaving zero side-effects.
4. **Dual-Runner Compatibility**:
   - All test suites inherit from standard Python `unittest.TestCase`.
   - Tests run seamlessly under both:
     - `pytest` (e.g. `pytest tests/e2e/ -v`)
     - Standard Python `unittest` (e.g. `python -m unittest discover -s tests -p "test_*.py"`)
   - Requires zero third-party test runners or plugins to verify the test suite.

---

## 2. Feature Inventory Mapping to Test Tiers

The 32 project features defined in `PROJECT.md` and requirements R1–R4 are mapped across four hierarchical test tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Tier 4: Scenarios                               │
│  (End-to-End Real-World Multi-Agent Workflows: Greenfield, Bugfix,     │
│   Crash-Recovery, Interactive Human-in-the-Loop REPL Session)          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                       Tier 3: Combinations                             │
│  (Cross-Feature Pairwise Interactions: Persistence + Agent Execution,  │
│   GitHub Tools + Workspace Tools, Q&A Grounding + Session Resumption)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                        Tier 2: Boundaries                              │
│  (Corner Cases: Empty Inputs, Missing Files, Corrupted State, Invalid  │
│   Tokens, Path Traversal Attempts, Missing Agent Configs)              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                         Tier 1: Features                               │
│  (Isolated Happy Path Verification: R1 Team Roles, R2 State/Memory,    │
│   R3 Workspace & GitHub Tooling, R4 CLI & Interactive Grounded Q&A)    │
└────────────────────────────────────────────────────────────────────────┘
```

### Detailed Mapping Matrix

| Requirement / Feature Area | Feature # in PROJECT.md | Test Tier | Primary Test File | Key Test Focus |
|---|---|---|---|---|
| **R1. Agent Team & Roles** | Feat 14–21 (8 Roles) | Tier 1 | `test_e2e_tier1_features.py` | 8 distinct agent roles, goals, backstories, system prompts |
| **R1. Orchestration Mechanics** | Feat 22–25 (LLM & Runners)| Tier 1 | `test_e2e_tier1_features.py` | MockLLM, sequential context passing, hierarchical runner |
| **R2. SQLite Relational Schema** | Feat 2, 3 (Schema & WAL) | Tier 1 | `test_e2e_tier1_features.py` | 8 tables, indexes, WAL mode, foreign key enforcement |
| **R2. State Manager API** | Feat 4 (State Manager) | Tier 1 | `test_e2e_tier1_features.py` | Projects, decisions, milestones, task history, agent status |
| **R2. Vector & Embeddings** | Feat 5, 6 (Vector Store) | Tier 1 | `test_e2e_tier1_features.py` | Deterministic hash embeddings, vector cosine similarity, FTS5 |
| **R2. Session Resumption** | Feat 7, 8 (Session & Export) | Tier 1 | `test_e2e_tier1_features.py` | Crash recovery, resumption context, JSON export and import |
| **R3. Workspace Tools** | Feat 9 (Sandboxed Tools) | Tier 1 | `test_e2e_tier1_features.py` | Read, write, list, patch with boundary sandboxing |
| **R3. GitHub Adapter & Mock** | Feat 10–13 (GitHub & Tools) | Tier 1 | `test_e2e_tier1_features.py` | MockGitHubAdapter issues, PRs, branches, commits, persistence |
| **R4. Grounded Direct Q&A** | Feat 26, 27 (REPL & Q&A) | Tier 1 | `test_e2e_tier1_features.py` | Interactive query grounded in persisted SQLite state |
| **R4. Subtask & Dashboard** | Feat 28, 29 (Subtask & Trace)| Tier 1 | `test_e2e_tier1_features.py` | Targeted single agent task execution, system dashboard |
| **Edge Cases & Security** | Path Traversal Protection | Tier 2 | `test_e2e_tier2_boundaries.py` | Prevention of `../` traversal outside workspace root |
| **Edge Cases & Robustness** | Empty & Malformed Inputs | Tier 2 | `test_e2e_tier2_boundaries.py` | Empty prompts, blank decisions, empty search queries |
| **Edge Cases & Resilience**| Corrupted State / Foreign Keys | Tier 2 | `test_e2e_tier2_boundaries.py` | Invalid FK insertion, schema version mismatch on import |
| **Edge Cases & Git Safety** | Invalid Branch & PR State | Tier 2 | `test_e2e_tier2_boundaries.py` | PR on missing head branch (422), merge on closed PR (405) |
| **Cross-Feature Interaction**| Persistence + Agent Run | Tier 3 | `test_e2e_tier3_combinations.py`| Agent task completion recorded and hydrated into next agent |
| **Cross-Feature Interaction**| Workspace File + GitHub PR | Tier 3 | `test_e2e_tier3_combinations.py`| Code written to workspace committed and PR opened via mock |
| **Cross-Feature Interaction**| Session Resume + Grounded Q&A | Tier 3 | `test_e2e_tier3_combinations.py`| Rebooted session rehydrates milestones, verified in Q&A |
| **Cross-Feature Interaction**| Vector Memory + Q&A Prompt | Tier 3 | `test_e2e_tier3_combinations.py`| Decision indexed in vector store retrieved in agent prompt |
| **Cross-Feature Interaction**| State Export/Import + Pipeline | Tier 3 | `test_e2e_tier3_combinations.py`| Export state, import to clean DB, resume pending tasks |
| **Full Workflow Scenario** | Greenfield Feature Flow | Tier 4 | `test_e2e_tier4_scenarios.py` | Arch -> Design -> Dev -> Test -> Review -> PR -> Memory |
| **Full Workflow Scenario** | Bugfix & Regression Flow | Tier 4 | `test_e2e_tier4_scenarios.py` | Issue -> Patch file -> Run regression tests -> Merge PR |
| **Full Workflow Scenario** | Crash Recovery Flow | Tier 4 | `test_e2e_tier4_scenarios.py` | Kill mid-execution -> Recover interrupted task -> Resume |
| **Full Workflow Scenario** | Interactive REPL Session | Tier 4 | `test_e2e_tier4_scenarios.py` | Multi-turn Q&A -> Targeted subtask -> Dashboard inspection |

---

## 3. Test Architecture & Directory Layout

```
tests/
├── __init__.py
├── e2e/
│   ├── __init__.py
│   ├── helpers.py                      # Common test fixtures, test doubles, DB seeders
│   ├── test_e2e_tier1_features.py      # >=5 tests per feature for R1-R4 (isolated happy paths)
│   ├── test_e2e_tier2_boundaries.py    # Boundary, corner, and adversarial stress cases
│   ├── test_e2e_tier3_combinations.py  # Pairwise cross-feature interactions
│   └── test_e2e_tier4_scenarios.py     # Real-world multi-agent workflow scenarios
```

### Shared Fixture Infrastructure (`tests/e2e/helpers.py`)

- **`E2ETestContext`**: Managed context manager provisioning:
  - An isolated temporary directory for workspace files.
  - A dedicated SQLite database file with WAL mode initialized.
  - An initialized `StateManager` bound to the test workspace.
  - A mock GitHub state file (`mock_github.json`).
  - Pre-seeded test records (optional) for quick setup.
- **Progressive Testability Enablers**:
  - Availability detectors (`HAS_PERSISTENCE`, `HAS_TOOLS`, `HAS_AGENTS`, `HAS_CLI`) ensure test suites compile and adapt gracefully across incremental implementation milestones.
  - Clean `unittest.skipUnless` notifications provide unambiguous feedback on which milestones are covered as implementation progresses.

---

## 4. Test Runner Instructions

### Using `pytest` (Recommended for Dev & CI)

1. **Run full E2E test suite**:
   ```powershell
   pytest tests/e2e/ -v
   ```
2. **Run a specific test tier**:
   ```powershell
   pytest tests/e2e/test_e2e_tier1_features.py -v
   pytest tests/e2e/test_e2e_tier2_boundaries.py -v
   pytest tests/e2e/test_e2e_tier3_combinations.py -v
   pytest tests/e2e/test_e2e_tier4_scenarios.py -v
   ```
3. **Run with short tracebacks and fail-fast**:
   ```powershell
   pytest tests/e2e/ -v --tb=short -x
   ```
4. **Filter by test name or requirement keyword**:
   ```powershell
   pytest tests/e2e/ -k "persistence or github" -v
   ```

### Using standard `python -m unittest` (Zero Dependency Runner)

1. **Discover and execute all test suites**:
   ```powershell
   python -m unittest discover -s tests -p "test_*.py" -v
   ```
2. **Run individual test files directly**:
   ```powershell
   python -m unittest tests.e2e.test_e2e_tier1_features -v
   python -m unittest tests.e2e.test_e2e_tier2_boundaries -v
   python -m unittest tests.e2e.test_e2e_tier3_combinations -v
   python -m unittest tests.e2e.test_e2e_tier4_scenarios -v
   ```
3. **Run a single test case**:
   ```powershell
   python -m unittest tests.e2e.test_e2e_tier1_features.TestTier1FeatureR1Agents.test_r1_all_eight_roles_instantiated -v
   ```

---

## 5. Coverage Thresholds & Quality Gates

| Metric | Target / Gate | Enforcement Mechanism |
|---|---|---|
| **Tier 1 Feature Tests** | $\ge 20$ test cases ($\ge 5$ per R1–R4 feature) | Automated test count assertion in test runner |
| **Tier 2 Boundary Tests** | $\ge 10$ boundary/corner cases | Automated test count assertion |
| **Tier 3 Combination Tests** | $\ge 6$ cross-feature pairwise interactions | Automated test count assertion |
| **Tier 4 Scenario Tests** | $\ge 4$ complete end-to-end multi-agent workflows | Automated test count assertion |
| **External Network Calls** | Exactly 0 external requests permitted | Offline test execution check |
| **External API Key Dependency** | 0 mandatory API keys | Verified without `OPENAI_API_KEY`, `GITHUB_TOKEN` |
| **Test Pass Rate** | 100% passing tests for implemented milestones | Zero failures or unexpected errors allowed |
