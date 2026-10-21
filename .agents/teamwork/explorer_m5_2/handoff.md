# Handoff Report: Packaging, CLI Guide & Offline Quickstart (Milestone 5)

**Agent**: Explorer M5-2 (Milestone 5 Packaging, CLI Guide & Offline Quickstart Explorer)  
**Date**: 2026-10-14  
**Project**: Persistent CrewAI 8-Agent Software Engineering Framework  
**Working Directory**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m5_2`  
**Handoff Type**: Hard (Task Complete)  

---

## 1. Observation

### 1.1 Packaging Configuration (`pyproject.toml`)
- **File**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\pyproject.toml`
- **Build Backend**: `setuptools.build_meta` with `requires = ["setuptools>=68.0", "wheel"]` (lines 1–3).
- **Package Identity**: `name = "persistent-crewai"`, `version = "0.1.0"`, `requires-python = ">=3.11"` (lines 5–10).
- **Core Dependencies**: `crewai>=1.15.0,<2.0.0`, `pydantic>=2.0.0,<3.0.0`, `rich>=13.0.0`, `click>=8.0.0`, `python-dotenv>=1.0.0`, `requests>=2.31.0` (lines 25–32).
- **Optional Dependencies**:
  - `dev = ["pytest>=8.0.0", "pytest-cov>=4.0.0", "pytest-mock>=3.12.0"]` (lines 35–39).
  - `live = ["openai>=1.0.0", "anthropic>=0.30.0", "google-genai>=1.0.0"]` (lines 40–44).
- **CLI Script**: `crewai-team = "persistent_crewai.cli.main:cli"` (lines 46–47).
- **Package Discovery**: `where = ["src"]`, `include = ["persistent_crewai*"]` (lines 49–51).
- **Package Data**: `"*" = ["*.sql", "*.json", "*.md"]` (lines 53–54).
- **Pytest Configuration**: `testpaths = ["tests"]`, `pythonpath = ["src"]`, `addopts = "-v --strict-markers"` (lines 56–65).

### 1.2 CLI Implementation (`src/persistent_crewai/cli/main.py`)
- **Global Options**:
  - `--project-id, -p` (line 27)
  - `--db-path, -d` (line 28)
  - `--workspace-root, -w` (line 29)
  - `--verbose, -v` (line 30)
- **Subcommands**:
  - `repl`: Launches `InteractiveREPL`, supports `--batch, -b` script option (lines 53–78). Default fallback when no subcommand is provided (lines 49–50).
  - `status`: Calls `render_dashboard(sm)` to display Rich/ASCII dashboard (lines 80–91).
  - `ask`: Accepts `role` and multi-token `prompt`, calls `InteractiveAgentExecutor.query_agent` (lines 93–112).
  - `task`: Accepts `role` and multi-token `description`, calls `InteractiveAgentExecutor.run_targeted_subtask` (lines 114–133).
  - `run`: Runs workflows (`feature_development`, `bugfix`, `code_review`), supports `--mode [sequential|hierarchical]` and `--resume-run-id` (lines 135–159).
  - `export`: Exports state to JSON via `SessionResumptionManager.export_state` (lines 161–173).
  - `import`: Imports state from JSON via `SessionResumptionManager.import_state`, supports `--overwrite` (lines 175–188).

### 1.3 Interactive REPL Shell (`src/persistent_crewai/cli/repl.py`)
- **Prompt**: `crewai> ` (line 459).
- **Grammar & Tokenizer**: `shlex.split` handling quoted strings, internal spacing, and Windows backslashes (lines 124–141).
- **Handled Verbs**:
  - `status`, `dashboard` (lines 158–160).
  - `ask <role> <prompt>`, `query <role> <prompt>` (lines 162–169).
  - `task <role> <desc>`, `run task <role> <desc>` (lines 171–183).
  - `run <workflow> [--mode seq|hier]` (lines 185–203).
  - `decisions`, `decision add <role> <title> <rationale>` (lines 205–207, 301–340).
  - `milestones`, `milestone add <title> <criteria>` (lines 209–211, 341–376).
  - `memory query <phrase>`, `memory <phrase>` (lines 213–215, 377–409).
  - `export <path>`, `backup <path>` (lines 217–228).
  - `import <path> [--overwrite]`, `restore <path>` (lines 229–241).
  - `resume <run_id>` (lines 242–253).
  - `help`, `?` (lines 153–155, 277–299).
  - `exit`, `quit`, `q` (lines 149–152).
  - `run_batch(lines)`: Sequentially executes scripts ignoring comments and blank lines (lines 410–430).

### 1.4 State-Grounded Single-Agent Query & Targeted Subtasks (`src/persistent_crewai/cli/interactive_query.py`)
- **Role Validation**: `validate_and_normalize_role(role)` validates against `CANONICAL_ROLES` and `ROLE_ALIASES`, raising `UnknownRoleError` on invalid roles (lines 118–144).
- **Prompt Context Injection**: `_extract_grounded_context` queries SQLite for active project metadata, active/in-progress milestone title and criteria, recent ADRs, and vector memory matches (lines 168–262).
- **Deterministic Mock Isolation**: `query_agent` isolates responses for `DeterministicMockLLM`, appending grounded project details, milestone state, and vector snippets (lines 263–353).
- **Targeted Subtasks**: `execute_targeted_subtask` records `targeted_subtask` run in `workflow_runs`, task start in `task_history`, transitions agent to `busy`, measures latency, resets agent to `idle`, and returns `SubtaskResult` (lines 354–553).

### 1.5 System Dashboard & Execution Traces (`src/persistent_crewai/cli/dashboard.py`)
- **Data Aggregation**: `get_dashboard_data` gathers project context, guaranteed 8 canonical agents in exact canonical order, decisions, milestones, workflow runs, and operational metrics (lines 37–165).
- **Rendering**: `render_dashboard` renders ASCII panels and tables using Rich, with clean plain ASCII fallback immune to Windows cp1252 encoding crashes (lines 171–438).
- **Telemetry Callback**: `create_step_trace_callback` streams live events while guaranteeing atomic foreign key existence in SQLite without unhandled exceptions (lines 440–546).

### 1.6 Offline Mocks & Hermetic Execution
- **LLM**: `DeterministicMockLLM` in `src/persistent_crewai/agents/llm_factory.py` provides deterministic role-grounded technical reports for all 8 roles without external API calls (lines 178–336). Auto-activates when API keys are absent or when `MOCK_LLM=true`.
- **Embeddings**: `DeterministicHashEmbeddingProvider` in `src/persistent_crewai/persistence/vector_store.py` provides 64-dimensional unit-normalized float embeddings using token SHA-256 hashing without external network access.
- **GitHub**: `MockGitHubAdapter` in `src/persistent_crewai/tools/mock_github.py` persists issues, branches, commits, PRs, and merges to local JSON (`.persistent_crewai/mock_github.json`) when `GITHUB_TOKEN` is unset.

### 1.7 Verification Commands Executed
1. `python -m unittest tests/test_persistence.py -v`: **29/29 tests passed in 2.692s**.
2. `python -m unittest tests/test_tools.py -v`: **55/55 tests passed in 0.511s**.
3. `python -m unittest tests/test_agents.py tests/test_pipeline.py -v`: **50/50 tests passed in 8.229s**.
4. `python -m unittest tests/test_cli.py -v`: **37/37 tests passed in 5.651s**.
5. `python -m unittest discover -s tests -p "test_*.py" -v`: **443/443 tests passed in 118.001s** (0 failures, 0 errors).

---

## 2. Logic Chain

1. **Packaging & Entrypoints**:
   - `pyproject.toml` configures `setuptools.build_meta` with package search rooted at `src/` (`include = ["persistent_crewai*"]`).
   - The CLI entrypoint `crewai-team = "persistent_crewai.cli.main:cli"` binds directly to Click's `@click.group` entrypoint.
   - Tested and verified that executing `cli` dispatches all 7 subcommands (`repl`, `status`, `ask`, `task`, `run`, `export`, `import`) with 100% parameter fidelity.

2. **CLI & REPL Ergonomics**:
   - The REPL shell (`InteractiveREPL`) encapsulates the entire state persistence and agent orchestration lifecycle.
   - Grounded Q&A dynamically inspects SQLite to extract the active project name, active milestone criteria, recent ADRs, and vector memory records, formatting them into a standard `--- CURRENT PROJECT CONTEXT ---` prompt header.
   - Targeted subtasks trigger individual agent execution with precise start/finish SQLite audit records and execution latency measurement without spawning full 8-agent crews.
   - ADR and milestone management commands (`decisions`, `decision add`, `milestones`, `milestone add`) provide human-in-the-loop operational governance.
   - State export and import commands enable reliable session backups and portability across workstations.

3. **100% Offline Reproducibility**:
   - `DeterministicMockLLM` guarantees role-specific, grounded, deterministic outputs without network connectivity.
   - `DeterministicHashEmbeddingProvider` provides offline vector search capabilities.
   - `MockGitHubAdapter` simulates GitHub REST API v3 operations using a thread-safe local JSON store.
   - Zero external API keys or cloud credentials are required to install, run, and verify the framework.

---

## 3. Caveats

- **Active Virtual Environment**: When running tests or CLI commands directly via terminal, ensure the active Python interpreter matches the environment containing dependencies (e.g. `C:\Python313\python.exe` or an activated `.venv` with `pip install -e .` executed).
- **Windows Terminal Codepage**: While Rich supports full Unicode, the dashboard implementation explicitly uses `box.ASCII` and provides `_render_dashboard_plain_ascii` fallback to ensure compatibility with Windows `cp1252` encoding environments.
- **Batch Script Format**: Batch scripts for `crewai-team repl --batch` execute sequentially and ignore lines starting with `#`. If a command encounters an error, the REPL reports the error and continues executing remaining commands in the batch.

---

## 4. Conclusion

The blueprint for packaging (`pyproject.toml`), the CLI and interactive REPL guide (with comprehensive command walkthroughs and input/output examples), and the 100% offline quickstart and test reproducibility guide has been formulated and documented in:
- `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m5_2\analysis.md`

All features, contracts, and test commands are 100% verified and operational. The full test discovery suite passes 443 out of 443 tests with zero defects. The project is fully prepared for Milestone 5 final acceptance and documentation consolidation.

---

## 5. Verification Method

To independently verify all findings and test suites:

### 1. Verify Packaging Configuration
Inspect `pyproject.toml` and verify `crewai-team` script entrypoint:
```powershell
python -c "import tomllib; f = open('pyproject.toml', 'rb'); data = tomllib.load(f); print(data['project']['scripts'])"
```

### 2. Verify Milestone-Specific Unit & Integration Tests
```powershell
# Milestone 1: State & Memory Persistence (29 tests)
python -m unittest tests/test_persistence.py -v

# Milestone 2: Workspace & GitHub Tooling (55 tests)
python -m unittest tests/test_tools.py -v

# Milestone 3: 8-Agent Team & Pipelines (50 tests)
python -m unittest tests/test_agents.py tests/test_pipeline.py -v

# Milestone 4: CLI & Interactive REPL (37 tests)
python -m unittest tests/test_cli.py -v
```

### 3. Verify Full E2E & Adversarial Discovery Suite
```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
Expected result: `Ran 443 tests ... OK`.

### 4. Verify CLI Entrypoint Commands
```powershell
# Verify help
python -m persistent_crewai.cli.main --help

# Verify status dashboard
python -m persistent_crewai.cli.main status

# Verify grounded single-agent Q&A
python -m persistent_crewai.cli.main ask coordinator "What is the project status?"

# Verify targeted subtask execution
python -m persistent_crewai.cli.main task tester "Run regression test verification"
```
