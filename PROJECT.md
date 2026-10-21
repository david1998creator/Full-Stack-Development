# Project: Persistent CrewAI 8-Agent Software Engineering Framework

## Architecture
The system is a persistent, multi-agent software engineering framework built in Python using CrewAI. It provides state and memory persistence, dual-mode GitHub/workspace tooling, an 8-specialist agent team, and an interactive CLI/REPL interface with grounded single-agent Q&A.

```
                    ┌───────────────────────────────────────────────┐
                    │               CLI / REPL Interface            │
                    │  (Interactive Q&A, Workflow Runner, Dashboard)│
                    └───────┬───────────────────────────────┬───────┘
                            │                               │
                            ▼                               ▼
       ┌────────────────────────────────────┐    ┌───────────────────────────────────┐
       │   Interactive Single-Agent Query   │    │ CrewAI Pipeline Runner (Seq/Hier) │
       │  (State-grounded agent dialogue)   │    │  (Coordinator, Architect, Dev...) │
       └────────────────────┬───────────────┘    └──────────────────┬────────────────┘
                            │                                       │
                            └───────────────────┬───────────────────┘
                                                ▼
       ┌─────────────────────────────────────────────────────────────────────────────┐
       │                        8-Agent Engineering Team                             │
       │ [Coordinator, Architect, Designer, Developer, Tester, Reviewer, GH, Memory] │
       │ (Goals, Backstories, System Prompts, Delegation Rules, LLM Factory)         │
       └────────────────────┬───────────────────────────────────────┬────────────────┘
                            │                                       │
                            ▼                                       ▼
       ┌────────────────────────────────────┐    ┌───────────────────────────────────┐
       │     Workspace & GitHub Tooling     │    │     State & Memory Persistence    │
       │ - Sandboxed File Ops (Read/Write)  │    │ - SQLite Database (WAL mode)      │
       │ - Dual GitHub (Mock & Real REST)   │    │ - Vector Store & Hash Embeddings  │
       │ - Issues, PRs, Branches, Commits   │    │ - Session Resumption & Export     │
       └────────────────────────────────────┘    └───────────────────────────────────┘
```

## Code Layout
```
persistent_crewai_team/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── src/
│   └── persistent_crewai/
│       ├── __init__.py
│       ├── config.py                 # Configuration, env vars, paths
│       ├── persistence/              # Milestone 1
│       │   ├── __init__.py
│       │   ├── db.py                 # SQLite connection & schema DDL
│       │   ├── state_manager.py      # State tracking (projects, decisions, tasks)
│       │   ├── vector_store.py       # Vector store & DeterministicHashEmbedding
│       │   └── session.py            # Session resumption & export/import
│       ├── tools/                    # Milestone 2
│       │   ├── __init__.py
│       │   ├── workspace_tools.py    # Sandboxed local file operations
│       │   ├── github_interface.py   # Abstract GitHub adapter interface
│       │   ├── mock_github.py        # Persistent JSON MockGitHubAdapter
│       │   └── real_github.py        # Real GitHub REST API adapter
│       ├── agents/                   # Milestone 3
│       │   ├── __init__.py
│       │   ├── llm_factory.py        # DeterministicMockLLM & real LLM selector
│       │   ├── role_definitions.py   # Goals, backstories, system prompts
│       │   ├── crew_builder.py       # Agent instantiation & tool assignment
│       │   └── runner.py             # Sequential & Hierarchical pipeline runners
│       ├── cli/                      # Milestone 4
│       │   ├── __init__.py
│       │   ├── repl.py               # Interactive CLI / REPL interface
│       │   ├── interactive_query.py  # Grounded single-agent Q&A executor
│       │   ├── dashboard.py          # Rich status dashboards & trace streamer
│       │   └── main.py               # CLI entrypoint commands
├── tests/                            # E2E & Unit Test Suites
│   ├── __init__.py
│   ├── test_persistence.py
│   ├── test_tools.py
│   ├── test_agents.py
│   ├── test_pipeline.py
│   ├── test_cli.py
│   └── e2e/
│       ├── test_e2e_tier1_features.py
│       ├── test_e2e_tier2_boundaries.py
│       ├── test_e2e_tier3_combinations.py
│       └── test_e2e_tier4_scenarios.py
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Python Packaging & Environment | `pyproject.toml`, `requirements.txt`, setup for Python 3.13 `.venv` | M1 | Survey 1 |
| 2 | SQLite Relational Schema | 8 tables (`projects`, `decisions`, `workflow_runs`, `task_history`, `milestones`, `agent_status`, `execution_traces`, `memory_records`) | M1 | Survey 3 |
| 3 | SQLite WAL Mode & FTS5 Indexing | Fast concurrent reads/writes and BM25 hybrid full-text indexing | M1 | Survey 3 |
| 4 | State Manager API | Record/query decisions, milestones, task history, and agent operational status | M1 | Survey 3 |
| 5 | Embedding Provider Interface | `EmbeddingProviderInterface` with `DeterministicHashEmbeddingProvider` for offline reproducibility | M1 | Survey 3 |
| 6 | Persistent Vector Store | `SQLiteVectorStore` with cosine similarity search and metadata filtering | M1 | Survey 3 |
| 7 | Session Resumption Engine | Rehydrate complete project state, task history, and agent status across runs | M1 | Survey 3 |
| 8 | State Export & Import | Export/import database state to JSON format for backup and migration | M1 | Survey 3 |
| 9 | Sandboxed Workspace Tools | File read, write, list, patch with path traversal containment | M2 | Survey 3 |
| 10 | GitHub Adapter Interface | Common abstract interface for issues, PRs, branches, commits, repos | M2 | Survey 3 |
| 11 | Mock GitHub Adapter | Local JSON-backed GitHub simulation for offline testing and CI | M2 | Survey 3 |
| 12 | Real GitHub API Adapter | REST API integration when `GITHUB_TOKEN` is available | M2 | Survey 3 |
| 13 | Issue Tracking & PR Automation Tools | CrewAI tools for creating/updating issues and generating pull requests | M2 | Survey 3 |
| 14 | Coordinator / Manager Agent | Project orchestration, task delegation, backlog management, process sequencing | M3 | Survey 2 |
| 15 | Architect Agent | System design, architecture blueprints, data schema, tech stack evaluation | M3 | Survey 2 |
| 16 | Designer Agent | UI/UX specifications, design systems, interface flows, interaction design | M3 | Survey 2 |
| 17 | Developer Agent | Implementation of features, bug fixes, module integrations | M3 | Survey 2 |
| 18 | Tester Agent | Unit, integration, E2E test authoring, test execution, regression auditing | M3 | Survey 2 |
| 19 | Reviewer Agent | Code review, security auditing, style compliance, refactoring guidance | M3 | Survey 2 |
| 20 | GitHub Manager Agent | Issue/PR automation, branch management, commit hygiene, repo sync | M3 | Survey 2 |
| 21 | Memory & Status Manager Agent | Centralized state management, operational status monitoring, memory persistence | M3 | Survey 2 |
| 22 | Deterministic Mock LLM | Role-aware, deterministic LLM subclass for offline execution & testing | M3 | Survey 2 |
| 23 | LLM Provider Factory | Auto-selects mock vs live LLMs (`OpenAI`, `Google GenAI`, `Anthropic`, `Ollama`) | M3 | Survey 2 |
| 24 | Sequential Pipeline Runner | Chained execution passing context artifacts from upstream to downstream agents | M3 | Survey 2 |
| 25 | Hierarchical Pipeline Runner | Manager-led delegation and task coordination | M3 | Survey 2 |
| 26 | Interactive REPL Interface | Command-line REPL for commands, subtasks, workflows, and queries | M4 | Survey 3 |
| 27 | Grounded Single-Agent Q&A Mode | Direct Q&A with individual agents injected with persisted SQLite context | M4 | Survey 2/3 |
| 28 | Targeted Subtask Execution Trigger | Trigger individual tasks for specific agents from CLI | M4 | Survey 3 |
| 29 | Status Dashboard & Execution Tracing | Rich-formatted status tables and real-time step execution callbacks | M4 | Survey 3 |
| 30 | Documentation & Execution Examples | Comprehensive `README.md` with setup, architecture, and examples | M5 | Survey 1/2/3 |
| 31 | Full E2E Test Suite (Tiers 1-4) | Category-partition, boundary, combination, and real-world scenario tests | M6 | Survey 3 |
| 32 | Adversarial Coverage Hardening (Tier 5) | White-box gap analysis and adversarial stress testing | M6 | Project Pattern |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Foundation & State/Memory Persistence | Package structure, SQLite schema, WAL/FTS5, StateManager, VectorStore, Session | none | DONE |
| M2 | Workspace & GitHub Tooling | Sandboxed file tools, GitHubAdapterInterface, MockGitHubAdapter, RealGitHubAdapter | M1 | DONE |
| M3 | 8-Agent Team & CrewAI Orchestration | Role definitions, DeterministicMockLLM, LLM factory, Crew builder, Seq/Hier runners | M1, M2 | DONE |
| M4 | CLI & Interactive REPL Interface | Interactive REPL, state-grounded direct Q&A, subtask triggers, rich dashboard | M1, M2, M3 | DONE |
| M5 | Documentation & Packaging | Comprehensive README.md with architecture diagrams, setup, and usage examples | M1, M2, M3, M4 | DONE |
| M6 | Final Acceptance: E2E Tests & Adversarial Hardening | Pass 100% E2E tests (Tiers 1-4), adversarial test hardening (Tier 5) | M1, M2, M3, M4, M5 | IN_PROGRESS |

## Interface Contracts

### M1 ↔ M2 (Persistence ↔ Tooling)
- `StateManager.get_project_root() -> Path`: Base directory for workspace sandbox validation.
- `StateManager.record_event(event_type: str, payload: dict) -> None`: Tool events logged to operational audit table.

### M1/M2 ↔ M3 (Persistence & Tools ↔ Agents)
- `StateManager.get_agent_state(role: str) -> dict`: Hydrates agent operational status and memory.
- `StateManager.record_decision(agent: str, title: str, rationale: str) -> int`: Persists architecture and design decisions.
- `StateManager.record_task_completion(task_id: str, agent: str, output: str) -> None`: Records task artifacts.
- `get_agent_tools(role: str) -> list[BaseTool]`: Provides assigned tools to each role (Workspace tools to Developer/Tester, GitHub tools to GitHub Manager, State tools to Memory Manager).

### M1/M3 ↔ M4 (Agents & State ↔ CLI/REPL)
- `InteractiveAgentExecutor.query_agent(role: str, user_prompt: str) -> str`: Injects persisted state into agent prompt, runs single-agent Crew, returns grounded response.
- `run_project_workflow(workflow_type: str, mode: str = "sequential") -> dict`: Triggers full multi-agent engineering workflow.
- `get_system_dashboard_data() -> dict`: Pulls projects, milestones, agent status, and recent decisions for Rich dashboard display.
