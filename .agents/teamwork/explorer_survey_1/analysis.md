# Technical Analysis: Environment, Dependencies & Toolchain Survey

**Author**: Explorer 1 (Environment & Dependency Explorer)  
**Date**: 2026-10-13  
**Target Project**: Persistent CrewAI Multi-Agent Team Framework  
**Project Root**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team`  

---

## 1. Executive Summary

This report establishes the baseline technical environment, dependency status, package management strategy, and execution constraints for building the **Persistent Multi-Agent Software Engineering Framework in Python using CrewAI**.

Key discoveries:
1. **Python Runtime**: Python 3.13.1 (64-bit) is installed at `C:\Python313\python.exe`.
2. **Filesystem Status**: The project root directory `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team` is pristine, containing only `.agents/teamwork/` metadata. No code, configuration, or `.git` repository currently exists.
3. **Global Site-Packages Restriction**: `C:\Python313\Lib\site-packages` is not writable under standard user privileges ("Defaulting to user installation because normal site-packages is not writeable"). Therefore, a local virtual environment (`.venv`) is strictly mandatory.
4. **Dependency Resolution**:
   - `crewai` (latest stable 1.15.27) and `pytest` (9.1.1) are **not** present in the base Python environment.
   - A dry-run resolution against PyPI verified that `crewai 1.15.27`, `chromadb 1.5.9`, `lancedb 0.30.0`, `onnxruntime 1.31.0`, and `pyarrow 26.0.0` have pre-compiled Windows x64 binary wheels that install cleanly on Python 3.13.
   - Built-in libraries `sqlite3` (3.45.3) and `unittest` are fully functional.
   - Several major libraries (`pydantic` 2.12.5, `rich` 14.3.3, `python-dotenv` 1.2.2, `openai` 2.16.0, `anthropic` 0.76.0, `google-genai` 1.65.0, `ollama` 0.6.1) are already installed in user/global packages.
5. **API Key & Network Independence**:
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `GITHUB_TOKEN` are all **absent** from the host environment.
   - To satisfy the acceptance criteria (unattended automated test suites, repeatable execution, zero-cost development), the framework **must implement a first-class Mock LLM and Mock GitHub Adapter**, alongside transparent configuration mechanisms for real providers via `.env`.

---

## 2. Project Root & Filesystem Survey

- **Root Directory**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team`
- **Existing Content**:
  ```
  persistent_crewai_team/
  └── .agents/
      └── teamwork/
          ├── ORIGINAL_REQUEST.md
          ├── explorer_survey_1/
          ├── explorer_survey_2/
          ├── orchestrator_1/
          ├── sentinel/
          └── spec_miner_survey_3/
  ```
- **Git Status**:
  - `git --version`: `git version 2.55.0.windows.2` at `C:\Program Files\Git\cmd\git.exe`.
  - The project root is currently **not** a Git repository (`fatal: not a git repository`).
  - *Recommendation*: As part of the GitHub Manager / Workspace tooling setup, the project should support initializing a local git repository (`git init`) or managing a local mock git workspace directory.

---

## 3. Python Runtime & Environment Details

| Component | Value / Version | Notes / Location |
|---|---|---|
| **Python Executable** | 3.13.1 (tags/v3.13.1:0671451, Dec 3 2024, 19:06:28) [MSC v.1942 64 bit (AMD64)] | `C:\Python313\python.exe` |
| **Operating System** | Windows (x64) | PowerShell execution environment |
| **Pip Version** | 25.0 | Located in Python 3.13 distribution |
| **Permissions** | Non-elevated user | Global site-packages directory is read-only |
| **Built-in SQLite3** | 3.45.3 | Included in Python standard library |
| **Built-in Venv** | Available | `python -m venv` standard library module works |
| **Built-in Unittest** | Available | Works natively for test discovery |

---

## 4. Dependency Inspection & Audit

### 4.1 Currently Installed Packages (Relevant Subset)

The following libraries were detected in the system/user Python environment:
- `pydantic` (2.12.5), `pydantic-settings` (2.15.0), `pydantic_core` (2.41.5)
- `rich` (14.3.3) — Ideal for formatted terminal dashboards, tables, agent traces
- `python-dotenv` (1.2.2) — For environment variable configuration
- `openai` (2.16.0)
- `anthropic` (0.76.0)
- `google-genai` (1.65.0)
- `ollama` (0.6.1)
- `fastapi` (0.115.12), `starlette` (1.6.0), `uvicorn` (0.34.2)
- `requests` (2.33.0), `httpx` (0.28.1)
- `tenacity` (9.1.4)
- `jinja2` (3.1.6)

### 4.2 Missing Packages & PyPI Dry-Run Verification

- `crewai`: **Missing**.
  - A dry-run resolution test (`python -m pip install --dry-run crewai`) was executed successfully.
  - Latest version available on PyPI: `crewai==1.15.27`.
  - Successfully resolved all transitive dependencies with pre-compiled wheels for Windows x64:
    - `chromadb-1.5.9-cp39-abi3-win_amd64.whl` (vector memory)
    - `lancedb-0.30.0-cp39-abi3-win_amd64.whl`
    - `onnxruntime-1.31.0-cp313-cp313-win_amd64.whl`
    - `tokenizers-0.23.3-cp310-abi3-win_amd64.whl`
    - `grpcio-1.84.0-cp313-cp313-win_amd64.whl`
    - `pyarrow-26.0.0-cp313-cp313-win_amd64.whl`
    - `crewai-core-1.15.27`
    - `crewai-cli-1.15.27`
- `pytest`: **Missing**.
  - A dry-run resolution test (`python -m pip install --dry-run pytest`) confirmed clean installation of `pytest 9.1.1`, `iniconfig 2.3.1`, and `pluggy 1.6.0`.

---

## 5. Package Management & Environment Isolation Strategy

### 5.1 Virtual Environment Specification

In strict compliance with the `managing-python-dependencies` skill and due to non-writable global site-packages:
- **Location**: `.venv` directory inside the project root:
  `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.venv`
- **Creation Command**:
  ```powershell
  python -m venv .venv
  ```
- **Activation / Execution**:
  ```powershell
  # Direct execution paths (recommended on Windows PowerShell):
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe -m pytest tests/
  ```

### 5.2 Package Configuration Files

The project must provide both modern standard packaging and classic pip requirements:

1. **`pyproject.toml`** (PEP 517/518/621):
   - Package metadata (`name = "persistent-crewai-team"`, `version = "0.1.0"`)
   - Build system (`setuptools.build_meta`)
   - Dependencies list
   - CLI script entry points (`persistent-crew = "persistent_crew.cli:main"`)
   - Tool configurations (`[tool.pytest.ini_options]`)

2. **`requirements.txt`**:
   Pinned core dependencies:
   ```text
   crewai>=1.15.0
   pydantic>=2.10.0
   python-dotenv>=1.0.0
   rich>=13.7.0
   tabulate>=0.9.0
   ```

3. **`requirements-dev.txt`**:
   Testing & development dependencies:
   ```text
   pytest>=8.0.0
   pytest-cov>=4.0.0
   ```

4. **`.gitignore`**:
   ```text
   .venv/
   __pycache__/
   *.py[cod]
   *$py.class
   .pytest_cache/
   .coverage
   htmlcov/
   dist/
   build/
   *.egg-info/
   .env
   data/*.db
   data/chroma_db/
   ```

---

## 6. API Key, Provider & Mock Execution Strategy

### 6.1 Environment State

The following environment variables were tested and found to be **unset**:
- `OPENAI_API_KEY`: `False`
- `ANTHROPIC_API_KEY`: `False`
- `GEMINI_API_KEY` / `GOOGLE_API_KEY`: `False`
- `GITHUB_TOKEN` / `GH_TOKEN`: `False`

### 6.2 Architectural Requirement: Dual-Mode Execution

Because API keys and network tokens cannot be assumed to exist:

1. **First-Class Mock Mode (`LLM_PROVIDER=mock`)**:
   - The framework **must** include a dedicated `MockLLM` class that produces structured, role-specific responses for each of the 8 agents:
     - Coordinator: Project milestones, task delegations, sequence plans.
     - Architect: System architecture blueprints, schema specs, technology tradeoffs.
     - Designer: User interface wireframes, component design tokens, UX flows.
     - Developer: Python modules, class implementations, integration code.
     - Tester: Test matrices, unit tests, integration test cases.
     - Reviewer: Code review feedback, security evaluations, refactoring suggestions.
     - GitHub Manager: Issue drafts, branch naming, PR descriptions, merge status.
     - Memory Manager: Snapshot status, serialized state summaries, memory retrieval logs.
   - Guarantees that:
     - All 8 agents instantiate and converse without throwing authentication exceptions.
     - Sequential and hierarchical crews run end-to-end deterministically.
     - Automated test suites (`pytest tests/`) execute rapidly without API costs or rate limits.

2. **Real LLM Backend Support (`LLM_PROVIDER=openai|anthropic|gemini|ollama`)**:
   - Seamlessly activated when keys are supplied via `.env` or system environment.
   - Configured via a central `config.py` using `pydantic-settings` or `dotenv`.

3. **Mock GitHub Adapter vs Real GitHub API**:
   - When `GITHUB_TOKEN` is unset, `GitHubAdapter` runs in `simulated/mock` mode:
     - Maintains issues, pull requests, commits, and branches in the local SQLite database or mock directory.
     - When `GITHUB_TOKEN` is set, calls the GitHub REST/GraphQL API.

---

## 7. Recommended Project Layout

```
persistent_crewai_team/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
├── README.md
├── data/
│   └── .gitkeep                 # Default directory for SQLite & vector stores
├── persistent_crew/
│   ├── __init__.py
│   ├── config.py                # Environment & settings configuration
│   ├── cli.py                   # Interactive CLI / REPL interface
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── definitions.py       # Roles, backstories, system prompts
│   │   └── factory.py           # CrewAI Agent instantiation logic
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── definitions.py       # Task definitions for the 8 roles
│   │   └── pipelines.py         # Sequential & hierarchical workflows
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── db.py                # SQLite database manager & schema
│   │   ├── memory_store.py      # Vector / semantic memory manager
│   │   └── state_manager.py     # State reload, persistence, snapshots
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── workspace_tools.py   # File read, write, patch, list
│   │   └── github_adapter.py    # Mock & real GitHub adapter
│   └── llm/
│       ├── __init__.py
│       ├── provider.py          # Unified LLM provider factory
│       └── mock_llm.py          # Deterministic role-aware Mock LLM
└── tests/
    ├── __init__.py
    ├── conftest.py              # Shared fixtures & test db setup
    ├── test_agents.py           # Instantiation & role verification
    ├── test_persistence.py      # SQLite & memory state persistence
    ├── test_github_adapter.py   # Mock GitHub operations
    ├── test_pipeline.py         # Crew workflow execution
    └── test_cli.py              # Interactive CLI & Q&A verification
```

---

## 8. Verification Strategy & Toolchain Commands

1. **Environment Setup**:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
2. **Automated Testing**:
   ```powershell
   # Primary test command (with pytest in venv):
   .\.venv\Scripts\python.exe -m pytest tests/ -v
   
   # Fallback test command (built-in standard library unittest):
   python -m unittest discover -s tests -p "test_*.py" -v
   ```
3. **CLI Execution**:
   ```powershell
   .\.venv\Scripts\python.exe -m persistent_crew.cli --help
   .\.venv\Scripts\python.exe -m persistent_crew.cli query --agent "Coordinator / Manager" "What is the project status?"
   .\.venv\Scripts\python.exe -m persistent_crew.cli run-pipeline --mode sequential
   ```

---

## 9. Compatibility & Risk Matrix

| Risk / Constraint | Severity | Mitigation Strategy |
|---|---|---|
| **Python 3.13 Wheel Availability** | Low | Confirmed: PyPI has pre-compiled `cp39-abi3` and `cp313` binary wheels for all core CrewAI dependencies on Windows x64. |
| **No Admin Rights / Read-only Site-Packages** | High | Use project-local `.venv`. Never run global `pip install`. |
| **Missing API Keys in Host Environment** | High | Implement built-in `MockLLM` with deterministic role prompts. Default to mock mode when keys are absent. |
| **Missing GITHUB_TOKEN** | Medium | Implement `MockGitHubAdapter` maintaining persistent issue/PR records in SQLite. |
| **CrewAI Package Weight (~150MB dependencies)** | Medium | Provide dual-layer compatibility: core models and mock workflows work cleanly out of the box; full CrewAI integration operates when dependencies are installed in `.venv`. |
