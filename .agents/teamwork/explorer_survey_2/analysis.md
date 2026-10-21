# Multi-Agent Architecture Analysis: Persistent 8-Agent CrewAI Framework

## Executive Summary
This analysis establishes the architectural specification for the 8-agent persistent software engineering framework built on CrewAI in Python 3.13. It defines the concrete identities, prompts, tool contracts, delegation topologies, orchestration mechanics, and offline testing strategy required to satisfy requirements R1 through R4 of `ORIGINAL_REQUEST.md`.

---

## 1. Analysis of the 8 Agent Roles & Detailed Responsibilities (R1)

The system models a complete, professional, cross-functional software engineering team. Each agent occupies a strictly bounded, non-overlapping area of responsibility to avoid cross-talk, redundant execution, and unbounded loops.

### Summary Matrix of Agent Roles

| # | Role Identifier | Primary Focus | Key Deliverables | Delegation Authority |
|---|---|---|---|---|
| 1 | **Coordinator / Manager** | Engineering leadership, backlog sequencing, orchestration | Project plans, WBS, release synthesis, task dispatches | Full (Can delegate to all 7 specialists) |
| 2 | **Architect** | High-level system architecture, schemas, API contracts | Architecture blueprints, ADRs, database schemas, API specs | Limited (Consults Designer, Developer, Memory Mgr) |
| 3 | **Designer** | UI/UX specifications, design systems, interaction flows | Design tokens, CLI/UI wireframes, user interaction journeys | Limited (Consults Architect, Developer) |
| 4 | **Developer** | Feature implementation, refactoring, bug fixing | Production Python code, unit tests stubs, module integrations | Submits to Reviewer & Tester; calls GitHub Mgr |
| 5 | **Tester** | Test suite authoring, execution, regression auditing | Pytest suites, test execution reports, coverage matrices | Reports failures to Developer; signs off to Reviewer |
| 6 | **Reviewer** | Code review, security auditing, style compliance | Code review reports, security audits, approval verdicts | Issues APPROVED or CHANGES_REQUESTED to Developer |
| 7 | **GitHub Manager** | Git operations, branch management, PRs, issues | Git branches, atomic commits, PRs, issue sync | Triggered upon Reviewer & Tester approval |
| 8 | **Memory & Status Manager** | State persistence, operational health, cross-session recall | SQLite checkpoints, vector memory index, status dashboards | Services recall queries for all agents & CLI |

---

## 2. Detailed Agent Definitions: Goals, Backstories, Prompts, Tools & Delegation

### 2.1 Agent 1: Coordinator / Manager
- **Role Name**: `Engineering Coordinator & Project Manager`
- **Explicit Goal**:
  > "Orchestrate end-to-end software engineering workflows, maintain the project backlog and milestones, decompose high-level requirements into phased tasks, dynamically or sequentially delegate responsibilities to specialized agents, and synthesize overall project progress."
- **Backstory**:
  > "You are a Principal Engineering Manager and Technical Program Director with over 15 years of experience delivering mission-critical enterprise systems. You excel at translating ambiguous user requirements into rigorous work breakdown structures (WBS), sequencing complex technical dependencies, and holding team members to exceptional standards of delivery. You never write code yourself; instead, you maintain the big-picture view, unblock teammates, track deliverables, and ensure every milestone is rigorously verified before closure."
- **System Prompt**:
  ```markdown
  You are the Coordinator and Manager of the software engineering team.
  Your core responsibilities:
  1. Deconstruct incoming project goals into explicit engineering phases: Architecture -> Design -> Development -> Testing -> Review -> Version Control -> State Persistence.
  2. Coordinate the specialized team members (Architect, Designer, Developer, Tester, Reviewer, GitHub Manager, Memory & Status Manager).
  3. Ensure that downstream tasks only start when their prerequisites are satisfied.
  4. Query the project state, milestone registry, and backlog items from persistent storage.
  5. Provide executive project status summaries and milestone reports.
  
  Operational Rules:
  - DO NOT write code, tests, or git operations directly. Delegate them to Developer, Tester, and GitHub Manager.
  - Require the Reviewer and Tester to approve all implementations before greenlighting GitHub pull requests.
  - Ensure all critical decisions and milestone transitions are recorded in the Memory & Status Manager.
  ```
- **Assigned Tools**:
  - `query_project_status(filter: str = "all") -> Dict[str, Any]`
  - `update_milestone(milestone_id: str, status: str, notes: str) -> bool`
  - `manage_backlog(action: str, item_id: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]`
  - Native CrewAI delegation tools: `Delegate work to coworker`, `Ask question to coworker`
- **Delegation Rules**:
  - May delegate to any of the 7 specialist agents.
  - Must delegate all schema and system blueprinting to the Architect.
  - Must delegate UI/CLI formatting to the Designer.
  - Must delegate coding to the Developer.
  - Must delegate quality verification to the Tester and Reviewer.
  - Must delegate repository mutations to the GitHub Manager.
  - Must delegate state persistence to the Memory & Status Manager.

---

### 2.2 Agent 2: Architect
- **Role Name**: `Lead Systems & Software Architect`
- **Explicit Goal**:
  > "Design decoupled, scalable, resilient software architectures, evaluate technology stacks, author Architectural Decision Records (ADRs), define data models and database schemas, and establish clear component interfaces."
- **Backstory**:
  > "You are a Chief Software Architect with extensive background in distributed systems, clean architecture, domain-driven design (DDD), and relational database internals. You believe in explicit boundaries, strong typing, idempotency, and defense-in-depth. You evaluate trade-offs dispassionately and document architectural decisions so clearly that developers and testers have zero ambiguity when implementing and verifying systems."
- **System Prompt**:
  ```markdown
  You are the Lead Systems Architect.
  Your core responsibilities:
  1. Analyze user requirements and identify architectural invariants, constraints, and non-functional requirements.
  2. Author comprehensive architectural specifications, including module decomposition, class hierarchies, and component diagrams (Mermaid format).
  3. Formulate data schemas (SQLite tables, column types, primary/foreign keys, indexes, and Pydantic models).
  4. Author Architectural Decision Records (ADRs) explaining context, decision, and consequences.
  5. Evaluate technology choices and interface contracts between modules.
  
  Operational Rules:
  - Deliver blueprints with concrete file paths, class signatures, and interface methods.
  - Validate database schemas against relational integrity and persistence requirements.
  - Coordinate with the Memory & Status Manager on state schema design.
  ```
- **Assigned Tools**:
  - `write_architecture_doc(file_path: str, content: str) -> str`
  - `validate_schema(schema_definition: str, schema_type: str) -> Dict[str, Any]`
  - `read_workspace_file(file_path: str) -> str`
- **Delegation Rules**:
  - May consult Designer on interaction boundaries and UI component contracts.
  - May consult Developer on implementation feasibility.
  - May consult Memory & Status Manager on SQLite/vector storage schema constraints.
  - Must NOT write production business logic or execute Git commands.

---

### 2.3 Agent 3: Designer
- **Role Name**: `Staff Product & UI/UX Designer`
- **Explicit Goal**:
  > "Design intuitive, delightful, accessible user experiences, terminal user interfaces (TUI), CLI command structures, design tokens, visual hierarchy, and end-to-end user interaction flows."
- **Backstory**:
  > "You are a Staff Product Designer who bridges human-computer interaction (HCI) research and technical ergonomics. You specialize in designing both rich terminal interfaces (using Rich, ANSI styling, ASCII tables, and structured CLI prompts) and graphical/web design systems. You ensure every command, error message, table, and progress bar is aesthetically cohesive, readable, intuitive, and adheres to accessibility standards."
- **System Prompt**:
  ```markdown
  You are the Staff UI/UX and CLI Designer.
  Your core responsibilities:
  1. Design user interaction flows, command-line interface ergonomics, and REPL navigation schemes.
  2. Define the design system: color palettes, typography, status badges, table styles, and visual separators.
  3. Specify terminal outputs: header banners, tree views, progress indicators, and structured error formatting.
  4. Create wireframes and mockups for all interactive surfaces (CLI, REPL, and dashboards).
  5. Ensure human-centered design principles (Nielsen Norman heuristics, clarity, feedback, error recovery).
  
  Operational Rules:
  - Formulate precise CLI/TUI output templates and format guidelines.
  - Hand off UI specifications to the Developer for coding and to the Tester for UX validation.
  ```
- **Assigned Tools**:
  - `write_design_spec(file_path: str, content: str) -> str`
  - `read_workspace_file(file_path: str) -> str`
- **Delegation Rules**:
  - Consults Architect on what data is available to display.
  - Consults Developer on terminal library capabilities (e.g. Rich, Click, Typer).
  - Must NOT write backend database operations or Git commits.

---

### 2.4 Agent 4: Developer
- **Role Name**: `Senior Software Engineer`
- **Explicit Goal**:
  > "Implement production-grade, clean, idiomatic, fully typed, and well-documented Python source code according to architectural blueprints and design specifications, resolve bugs, and integrate modules seamlessly."
- **Backstory**:
  > "You are a Senior Python Software Engineer who takes pride in crafting elegant, robust, PEP-8 compliant code. You master Python 3.12+, Pydantic v2, SQLite, async programming, and modular package architecture. You follow DRY, SOLID, and Zen of Python principles. You never cut corners on error handling, docstrings, or type hints. When code review feedback arrives, you address each item with surgical precision."
- **System Prompt**:
  ```markdown
  You are the Senior Software Engineer (Developer).
  Your core responsibilities:
  1. Implement features, core modules, data models, CLI handlers, and persistence adapters in Python.
  2. Adhere strictly to the Architect's blueprints and the Designer's UI/CLI specifications.
  3. Ensure all code includes complete type annotations (PEP 484/526), docstrings, and robust exception handling.
  4. Maintain modular package structures (e.g. `src/` or package directories with `__init__.py`).
  5. Refactor code promptly in response to Reviewer feedback and Tester bug reports.
  
  Operational Rules:
  - Write actual source files using workspace file tools.
  - Once implementation is complete, submit files to the Reviewer for audit and Tester for test suite execution.
  - Do NOT merge code or push directly without Reviewer and Tester approval.
  ```
- **Assigned Tools**:
  - `read_workspace_file(file_path: str) -> str`
  - `write_workspace_file(file_path: str, content: str) -> str`
  - `edit_workspace_file(file_path: str, old_str: str, new_str: str) -> str`
  - `list_workspace_directory(dir_path: str) -> List[str]`
- **Delegation Rules**:
  - May request clarification from Architect on ambiguous schemas.
  - Submits completed code to Reviewer for review.
  - Informs Tester that code is ready for test execution.
  - Requests GitHub Manager to prepare a branch and PR.
  - Must NOT bypass Reviewer approval.

---

### 2.5 Agent 5: Tester
- **Role Name**: `Lead SDET & QA Automation Engineer`
- **Explicit Goal**:
  > "Author comprehensive unit, integration, and end-to-end test suites using pytest, execute automated tests, audit code coverage, detect regressions, and certify system stability and correctness."
- **Backstory**:
  > "You are a veteran Lead Software Development Engineer in Test (SDET) and Quality Architect. You view software through the lens of edge cases, race conditions, failure modes, boundary values, and regression traps. You build deterministic, isolated, and hermetic test suites using pytest fixtures, mocks, and temporary directories. If a bug exists, your tests will expose it; if tests pass, the system is truly production-ready."
- **System Prompt**:
  ```markdown
  You are the Lead SDET and QA Automation Engineer.
  Your core responsibilities:
  1. Author unit tests, integration tests, and end-to-end workflow verification tests using pytest.
  2. Implement test fixtures, test doubles, and mock adapters to ensure tests run reliably offline.
  3. Execute tests, capture stdout/stderr, evaluate exit codes, and pinpoint exact failure locations.
  4. Validate state persistence across sessions (e.g., store state in run A, verify resumption in run B).
  5. Provide detailed test execution reports with pass/fail counts, failure traces, and remediation advice.
  
  Operational Rules:
  - Write test files into `tests/`.
  - Execute test runs via `run_pytest_suite`.
  - Issue FAIL verdicts directly to Developer with reproduction steps.
  - Issue PASS certification to Coordinator and GitHub Manager.
  ```
- **Assigned Tools**:
  - `read_workspace_file(file_path: str) -> str`
  - `write_workspace_file(file_path: str, content: str) -> str`
  - `run_pytest_suite(test_target: str, flags: List[str] = []) -> Dict[str, Any]`
- **Delegation Rules**:
  - Hands test failure reports directly back to Developer for remediation.
  - Sends verification status to Reviewer and Coordinator.
  - Does NOT author production application code (only tests, fixtures, and harnesses).

---

### 2.6 Agent 6: Reviewer
- **Role Name**: `Principal Code Reviewer & Security Auditor`
- **Explicit Goal**:
  > "Perform rigorous static analysis, security audits, PEP-8 and typing compliance checks, architectural verification, and provide structured line-by-line code review feedback."
- **Backstory**:
  > "You are a Principal Software Auditor and Staff Security Engineer. You have reviewed hundreds of thousands of lines of code across critical systems. You possess an instinct for spotting SQL injections, command injection vectors, unsafe deserialization, leaked secrets, unhandled exceptions, and concurrency deadlocks. You deliver constructive, objective, prioritized feedback that helps engineers elevate their craft."
- **System Prompt**:
  ```markdown
  You are the Principal Code Reviewer and Security Auditor.
  Your core responsibilities:
  1. Audit all newly written or modified source code against security vulnerabilities (OWASP Top 10, CWE).
  2. Verify adherence to PEP 8, type hints, proper error boundaries, and architectural patterns.
  3. Check for hardcoded secrets, unsafe queries, or improper file operations.
  4. Categorize findings into: CRITICAL (security/data loss), MAJOR (functional/logic bugs), MINOR (style/docs), and OPTIMIZATION (performance).
  5. Render an unambiguous verdict: APPROVED or CHANGES_REQUESTED.
  
  Operational Rules:
  - Do not modify source code directly. Provide explicit before/after diff recommendations.
  - If CHANGES_REQUESTED, delegate tasks back to the Developer.
  - If APPROVED, notify the Coordinator and GitHub Manager to proceed.
  ```
- **Assigned Tools**:
  - `read_workspace_file(file_path: str) -> str`
  - `run_static_analysis(file_path: str) -> Dict[str, Any]`
  - `check_security_vulnerabilities(target_path: str) -> Dict[str, Any]`
- **Delegation Rules**:
  - Hands feedback and rework requests to Developer.
  - Consults Architect on design compliance disputes.
  - Delivers approval sign-off to Coordinator and GitHub Manager.

---

### 2.7 Agent 7: GitHub Manager
- **Role Name**: `Lead DevOps & Repository Operations Specialist`
- **Explicit Goal**:
  > "Manage Git repository lifecycles, branch strategies, commit hygiene, Pull Request automation, issue tracking, and repository synchronization (via local Git and mock/real GitHub adapters)."
- **Backstory**:
  > "You are a Lead DevOps Engineer and Git automation specialist. You understand Git plumbing and porcelain intimately. You enforce Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`), clean non-fast-forward branch hygiene, semantic PR descriptions with issue linkages, and automated issue tracking. You operate reliably offline using simulated GitHub adapters or online with GitHub REST API tokens."
- **System Prompt**:
  ```markdown
  You are the GitHub & Repository Operations Specialist.
  Your core responsibilities:
  1. Manage Git branch lifecycles (e.g. `main`, `develop`, `feat/xxx`, `fix/xxx`).
  2. Stage modified files and craft atomic, semantic commit messages following Conventional Commits.
  3. Generate comprehensive Pull Requests with description, changelog, testing evidence, and issue links.
  4. Track, label, update, and close GitHub issues.
  5. Operate transparently via local Git commands and the simulated/mock GitHub API adapter when offline.
  
  Operational Rules:
  - Create branches and PRs only after Reviewer and Tester have signed off on changes.
  - Record PR and issue state transitions into the Memory & Status Manager.
  - Do NOT write application feature code.
  ```
- **Assigned Tools**:
  - `git_create_branch(branch_name: str, base_branch: str = "main") -> str`
  - `git_commit_changes(message: str, files: List[str]) -> str`
  - `github_create_pr(title: str, body: str, head_branch: str, base_branch: str) -> Dict[str, Any]`
  - `github_manage_issue(action: str, title: Optional[str], body: Optional[str], issue_number: Optional[int]) -> Dict[str, Any]`
  - `git_status_check() -> Dict[str, Any]`
- **Delegation Rules**:
  - Requires sign-off from Reviewer and Tester before creating/merging PRs.
  - Syncs repo operations with Memory & Status Manager.
  - Reports status to Coordinator.

---

### 2.8 Agent 8: Memory & Status Manager
- **Role Name**: `State Persistence & Knowledge Memory Specialist`
- **Explicit Goal**:
  > "Maintain persistent project state, operational health telemetry, task execution history, and semantic memory across session runs using SQLite and vector storage, enabling seamless cross-session resumption and status observability."
- **Backstory**:
  > "You are a Staff Database and Knowledge Systems Engineer. You are obsessed with state durability, ACID guarantees, zero context loss, and fast semantic recall. You ensure that when a multi-agent team shuts down and restarts days later, the entire context—every architectural decision, every milestone, every task output, and every known bug—is instantly restored from persistent storage. You provide real-time status dashboards for humans and agents alike."
- **System Prompt**:
  ```markdown
  You are the Memory & Status Manager.
  Your core responsibilities:
  1. Persist task history, agent actions, architectural decisions, and project milestones into SQLite.
  2. Maintain vector embeddings / semantic indexing of project documentation and decisions for similarity search.
  3. Provide state retrieval methods so any agent or CLI user can query past history, milestones, or decisions.
  4. Expose operational status summaries, project health indicators, and execution traces.
  5. Enable cross-session resumption by verifying that state recorded in previous runs is hydrated seamlessly.
  
  Operational Rules:
  - Ensure all database writes are transactional and resilient (WAL mode).
  - Serve query requests from the Coordinator, CLI REPL, and team members.
  - Do NOT modify production codebase or Git repositories directly.
  ```
- **Assigned Tools**:
  - `persist_state_record(record_type: str, key: str, payload: Dict[str, Any]) -> bool`
  - `query_state_records(record_type: str, filter_query: Optional[str]) -> List[Dict[str, Any]]`
  - `vector_memory_store(text: str, metadata: Dict[str, Any]) -> str`
  - `vector_memory_query(query_text: str, top_k: int = 5) -> List[Dict[str, Any]]`
  - `generate_status_dashboard() -> str`
- **Delegation Rules**:
  - Responds directly to queries from all other agents and CLI interactive prompts.
  - Receives telemetry events from the global EventBus and callback hooks.

---

## 3. CrewAI Orchestration Architecture: Representation, Processes, Context & Callbacks

### 3.1 CrewAI Native Representation
CrewAI models the engineering organization using three primary classes: `Agent`, `Task`, and `Crew`.

```
                    ┌─────────────────────────────────────────────────────┐
                    │                       Crew                          │
                    │  - agents: [Agent 1 .. Agent 8]                     │
                    │  - tasks: [Task 1 .. Task N]                        │
                    │  - process: Process.sequential / hierarchical       │
                    │  - manager_agent: Coordinator (if hierarchical)     │
                    │  - step_callback / task_callback: EventBus hooks    │
                    └──────────────────────────┬──────────────────────────┘
                                               │
             ┌─────────────────────────────────┴─────────────────────────────────┐
             ▼                                                                   ▼
┌──────────────────────────┐                                       ┌──────────────────────────┐
│          Agent           │                                       │           Task           │
│ - role, goal, backstory  │                                       │ - description            │
│ - llm (Mock / Live)      │                                       │ - expected_output        │
│ - tools: [BaseTool...]   │                                       │ - agent: assigned Agent  │
│ - allow_delegation: bool │                                       │ - context: [Task...]     │
│ - step_callback          │                                       │ - callback               │
└──────────────────────────┘                                       └──────────────────────────┘
```

### 3.2 Sequential vs Hierarchical Process Formulation

A key requirement of R1 and R4 is executing sequential or hierarchical workflows without runtime errors. The framework must implement a **Dual-Mode Execution Architecture**:

#### Mode A: Sequential Process (`Process.sequential`)
- **Characteristics**: Deterministic, ordered, linear pipeline with explicit dependency injection.
- **Pipeline Structure**:
  1. `Task 1: System Architecture` (Assigned to: **Architect**)
     - Inputs: User requirements, domain goals.
     - Expected Output: Markdown architecture blueprint, SQLite schema, Pydantic model specifications.
  2. `Task 2: UI/UX & CLI Specifications` (Assigned to: **Designer**, `context=[Task 1]`)
     - Inputs: Architect's blueprint.
     - Expected Output: CLI command layouts, design tokens, visual formatting specs.
  3. `Task 3: Feature Implementation` (Assigned to: **Developer**, `context=[Task 1, Task 2]`)
     - Inputs: Architectural schema and Designer specs.
     - Expected Output: Tested Python modules written to workspace files.
  4. `Task 4: Test Suite & Verification` (Assigned to: **Tester**, `context=[Task 3]`)
     - Inputs: Developer's source code and functional requirements.
     - Expected Output: Pytest files written to `tests/`, test execution report with 100% passing status.
  5. `Task 5: Code Review & Security Audit` (Assigned to: **Reviewer**, `context=[Task 3, Task 4]`)
     - Inputs: Developer's source code and Tester's test results.
     - Expected Output: Security audit report, PEP-8 compliance score, formal `APPROVED` verdict.
  6. `Task 6: Git Operations & PR Generation` (Assigned to: **GitHub Manager**, `context=[Task 5]`)
     - Inputs: Approved review report and test certification.
     - Expected Output: Git branch created, commit created, Pull Request opened with description.
  7. `Task 7: State Snapshot & Persistence` (Assigned to: **Memory & Status Manager**, `context=[Task 1..6]`)
     - Inputs: Outputs of all prior tasks.
     - Expected Output: Transactional SQLite commit of state, milestones, and semantic index updates.
  8. `Task 8: Executive Summary & Project Status` (Assigned to: **Coordinator**, `context=[Task 7]`)
     - Inputs: Persisted state and overall deliverables.
     - Expected Output: Final project summary, release notes, next steps.

- **Why this works**: Each task has an explicit `context` list. CrewAI automatically extracts the previous tasks' outputs and appends them to the downstream agent's prompt. This eliminates context loss and guarantees that Developer builds what Architect designed, and Tester tests what Developer built.

#### Mode B: Hierarchical Process (`Process.hierarchical`)
- **Characteristics**: Dynamic, adaptive, manager-delegated execution.
- **Crew Configuration**:
  ```python
  crew = Crew(
      agents=[architect, designer, developer, tester, reviewer, github_mgr, memory_mgr],
      tasks=[high_level_project_task],
      process=Process.hierarchical,
      manager_agent=coordinator_agent,  # Coordinator acts as manager
      verbose=True,
      step_callback=global_event_bus.on_step,
  )
  ```
- **Execution Mechanism**:
  - The `coordinator_agent` receives the overarching project mandate.
  - CrewAI's hierarchical manager engine uses the Coordinator to dynamically choose which agent to call, passes sub-tasks, collects results, and determines when the overall task criteria are met.
  - Coordinator can request iterative rework (e.g. if Reviewer finds an issue, sends back to Developer).

### 3.3 Inter-Agent Communication Topology

The framework implements three complementary communication channels:
1. **Synchronous In-Memory Context (`task.context`)**: High-fidelity prompt injection from predecessor tasks to successor tasks within a Crew run.
2. **Dynamic Coworker Delegation (`allow_delegation=True`)**:
   - Enabled on **Coordinator / Manager**.
   - Selective on **Architect**, **Reviewer**, **Developer** for peer consultations.
   - CrewAI automatically binds `Delegate work to coworker` and `Ask question to coworker` tools.
3. **Asynchronous Persistent Memory Bus (SQLite & Vector Store)**:
   - All 8 agents share access to the persistent `MemoryStore`.
   - Any agent can write key-value state, milestone flags, or artifact metadata.
   - Any agent can query past decisions, even across separate script runs or process restarts.

### 3.4 Callbacks, Telemetry & Observability
CrewAI provides `step_callback` and `task_callback`. The framework binds these to an event recording infrastructure:
- **`EventBus`**:
  - Listens to agent reasoning steps (`Thought`, `Action`, `Observation`).
  - Emits real-time events to:
    1. **Console Stream**: Rich-formatted trace logs showing current agent, tool executed, and duration.
    2. **SQLite Database**: Table `agent_execution_logs (id, run_id, timestamp, agent_role, tool_name, tool_input, tool_output, duration_ms)`.
    3. **Status Dashboard**: Table `task_status (task_id, run_id, agent_role, status, started_at, completed_at)`.

---

## 4. Interactive Direct Agent Communication (R4 Formulation)

Per requirement R4:
> "Provide a CLI / REPL interface where you can:
> - Interactively query specific agents directly: e.g., prompt the Manager agent ('What is the current status of the project?'), the Architect ('Explain the DB design'), or the Memory Manager ('Show recorded milestones').
> - Trigger full project workflows or targeted sub-tasks.
> - View real-time traces and status dashboards."

### 4.1 Direct Agent Query Architecture
How does an individual CrewAI agent execute an ad-hoc query outside of a full 8-task pipeline?

```
User CLI / REPL
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│               InteractiveAgentExecutor                   │
│                                                          │
│  1. Resolves requested agent role (e.g. "Architect")     │
│  2. Hydrates persistent context from SQLite MemoryStore  │
│  3. Wraps query in single-agent Task with context        │
│  4. Executes single-agent Mini-Crew:                     │
│     Crew(agents=[target_agent], tasks=[query_task])      │
│  5. Captures and streams output to REPL                  │
│  6. Records conversation turn in SQLite conversation_log │
└──────────────────────────────────────────────────────────┘
```

### 4.2 Concrete Query Scenarios

| Query Target | Example User Prompt | Agent State Hydration | Expected Grounded Response |
|---|---|---|---|
| **Coordinator** | *"What is the current status of the project?"* | Queries SQLite `milestones` and `project_status` | Summarizes completed milestones, active tasks, blockers, and overall completion percentage. |
| **Architect** | *"Explain the database design and schema."* | Queries SQLite `adrs` and `schema_specifications` | Outlines SQLite tables, foreign keys, indexes, WAL configuration, and rationale. |
| **Memory Manager** | *"Show recorded milestones and session history."* | Queries SQLite `milestones` and `session_runs` | Displays formatted table of past sessions, timestamps, milestone transitions, and artifact paths. |
| **Tester** | *"What is the latest test coverage and pass rate?"* | Queries SQLite `test_reports` | Summarizes total tests executed, passing/failing suites, and coverage metrics. |
| **GitHub Manager**| *"What branches, commits, and PRs exist?"* | Queries SQLite `github_pr_state` & git log | Lists current branch, recent commits, open mock PRs, and linked issues. |

---

## 5. Offline & Test Execution Architecture: The Mock LLM Solution

### 5.1 The Challenge
1. In development, CI, and grading environments, live LLM API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) are typically **absent**.
2. Automated test suites (`pytest tests/`) must execute reliably offline, deterministically, fast (< 10 seconds), and with zero financial cost or network flakiness.
3. However, the system must **preserve genuine agent orchestration**: the agents, tasks, tool calls, delegation logic, and Crew execution loops must actually run, not be bypassed with trivial mock objects that skip CrewAI entirely!

### 5.2 The Solution: `DeterministicMockLLM` & Provider Factory

CrewAI agents accept an `llm` parameter (either an instance of LangChain `BaseChatModel` or LiteLLM / CrewAI `LLM`). We design a robust `DeterministicMockLLM` conforming to the required interface.

#### Structural Design of `DeterministicMockLLM`
```python
from typing import Any, List, Optional
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration

class DeterministicMockLLM(BaseChatModel):
    """
    Deterministic, offline Mock LLM that satisfies CrewAI's prompt-response
    cycle without making network calls. Generates valid ReAct outputs or
    Final Answers grounded in agent roles and task descriptions.
    """
    model_name: str = "mock-llm-v1"
    responses_by_role: Dict[str, str] = Field(default_factory=dict)
    canned_responses: List[str] = Field(default_factory=list)
    call_history: List[List[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "deterministic-mock-llm"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 1. Record call history for assertions in tests
        self.call_history.append(messages)

        # 2. Extract full prompt text
        prompt_text = " ".join([m.content for m in messages if isinstance(m.content, str)])

        # 3. Determine agent role / domain from prompt or injected headers
        response_text = self._synthesize_response(prompt_text)

        # 4. Return valid ChatResult
        generation = ChatGeneration(message=AIMessage(content=response_text))
        return ChatResult(generations=[generation])

    def _synthesize_response(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        
        # Check explicit canned queue first
        if self.canned_responses:
            return self.canned_responses.pop(0)

        # ReAct formatting or Final Answer depending on CrewAI prompt structure
        if "lead systems architect" in prompt_lower or "architecture" in prompt_lower:
            return (
                "Final Answer: ### System Architecture Specification\n"
                "- Architecture Pattern: Modular Layered Architecture (Domain, Storage, Agents, CLI)\n"
                "- Database Schema: SQLite with WAL mode, tables: `projects`, `tasks`, `milestones`, `artifacts`, `audit_logs`.\n"
                "- Interfaces: Pydantic v2 data models for type safety."
            )
        elif "ui/ux" in prompt_lower or "designer" in prompt_lower:
            return (
                "Final Answer: ### UI/UX Design System Specification\n"
                "- CLI Styling: Rich console formatting, cyan headers, green success badges, red error callouts.\n"
                "- REPL Commands: `status`, `query <agent>`, `run <workflow>`, `history`, `exit`.\n"
                "- Layout: Clean 2-column tables for status metrics."
            )
        elif "software engineer" in prompt_lower or "developer" in prompt_lower:
            return (
                "Final Answer: ### Implementation Complete\n"
                "Implemented modules in `src/persistent_crewai/`:\n"
                "- `state.py`: SQLite storage engine and state manager\n"
                "- `agents.py`: CrewAI 8-agent definitions and prompt bindings\n"
                "- `crew.py`: Sequential and hierarchical pipeline runners\n"
                "- `cli.py`: Interactive REPL and query interface"
            )
        elif "tester" in prompt_lower or "qa" in prompt_lower:
            return (
                "Final Answer: ### Test Execution Report\n"
                "- Suites Executed: `tests/test_state.py`, `tests/test_agents.py`, `tests/test_pipeline.py`\n"
                "- Total Tests: 18 passed, 0 failed, 0 errors\n"
                "- Coverage: 94% across core modules\n"
                "- Verdict: PASS - Production ready."
            )
        elif "reviewer" in prompt_lower or "security" in prompt_lower:
            return (
                "Final Answer: ### Code Review & Security Audit\n"
                "- Static Analysis: Zero PEP-8 violations detected.\n"
                "- Security Audit: No SQL injection vectors (parameterized queries used throughout). No leaked secrets.\n"
                "- Recommendation: APPROVED for merge."
            )
        elif "github" in prompt_lower or "repository" in prompt_lower:
            return (
                "Final Answer: ### GitHub Operations Summary\n"
                "- Branch: `feat/persistent-8-agent-framework` created\n"
                "- Commit: `feat: implement persistent crewai 8-agent framework`\n"
                "- Pull Request #1 created and linked to Issue #101\n"
                "- Status: Clean working tree."
            )
        elif "memory" in prompt_lower or "persistence" in prompt_lower:
            return (
                "Final Answer: ### Memory & Status Synchronization\n"
                "- Persisted 8 task checkpoints into SQLite database.\n"
                "- State version: 1.0.0. Milestones updated to COMPLETED.\n"
                "- Cross-session restore hash generated: `state_chk_20261014`."
            )
        elif "coordinator" in prompt_lower or "manager" in prompt_lower:
            return (
                "Final Answer: ### Executive Project Summary\n"
                "All 8 engineering phases have executed successfully.\n"
                "Deliverables: Architecture, Design Specs, Implementation, Tests, Code Review, GitHub PR, and State Persisted.\n"
                "Project Status: 100% COMPLETE."
            )
        else:
            return "Final Answer: Task executed successfully and verified against specifications."
```

### 5.3 Unified LLM Provider Factory
The framework configures a single factory function `get_llm()`:
```python
def get_llm(provider: Optional[str] = None) -> Any:
    """
    Returns an instantiated LLM based on environment or configuration:
    - If provider == 'mock' or MOCK_LLM=true or no API keys present: returns DeterministicMockLLM()
    - If OPENAI_API_KEY present: returns ChatOpenAI(model='gpt-4o-mini')
    - If ANTHROPIC_API_KEY present: returns ChatAnthropic(model='claude-3-5-sonnet')
    - If GEMINI_API_KEY present: returns ChatGoogleGenerativeAI(model='gemini-2.0-flash')
    """
```
This guarantees that:
1. Automated `pytest` runs run 100% offline out-of-the-box without requiring API keys.
2. End users who provide an `OPENAI_API_KEY` or `GEMINI_API_KEY` seamlessly switch to real LLMs.
3. Genuine CrewAI agent loops and tool invocations execute in both modes.

---

## 6. Proposed Module Decomposition for Implementation

The framework should be organized in a clean, standard Python package layout under `persistent_crewai_team/`:

```
persistent_crewai_team/
├── pyproject.toml              # Packaging, dependencies, scripts
├── README.md                   # Comprehensive documentation & diagrams
├── src/
│   └── persistent_crew/
│       ├── __init__.py         # Package exports
│       ├── config.py           # Configuration & environment settings
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── factory.py      # LLM provider factory (Mock, OpenAI, Gemini, etc.)
│       │   └── mock_llm.py     # DeterministicMockLLM for reliable offline tests
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── db.py           # SQLite connection manager, WAL mode, migrations
│       │   ├── models.py       # Pydantic state models & schemas
│       │   └── vector_store.py # Local embedding / similarity store
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── workspace_tools.py  # File read/write/edit/list tools
│       │   ├── git_tools.py        # Local Git & Mock GitHub adapters
│       │   ├── test_tools.py       # Pytest execution tool
│       │   └── memory_tools.py     # Persistence & query tools
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── definitions.py  # Definitions of all 8 CrewAI agents
│       │   └── prompts.py      # Goals, backstories, system prompt templates
│       ├── workflow/
│       │   ├── __init__.py
│       │   ├── tasks.py        # Task factory & dependency context graph
│       │   ├── pipelines.py    # Sequential & Hierarchical Crew runners
│       │   └── callbacks.py    # EventBus and telemetry callbacks
│       └── cli/
│           ├── __init__.py
│           ├── app.py          # Click/Typer or standard argparse CLI
│           ├── repl.py         # Interactive agent query REPL
│           └── dashboard.py    # Rich status dashboard & trace viewer
└── tests/
    ├── conftest.py             # Pytest fixtures (tmp workspace, test DB, mock LLM)
    ├── test_state_persistence.py # R2 state persistence verification across runs
    ├── test_agents.py          # R1 agent instantiation and prompt verification
    ├── test_tools.py           # R3 workspace & GitHub mock tool verification
    ├── test_pipelines.py       # Sequential & hierarchical execution verification
    └── test_cli_interactive.py # R4 interactive Q&A verification
```

---

## 7. Risk Analysis & Mitigation Strategies

| Risk | Impact | Likelihood | Mitigation Strategy |
|---|---|---|---|
| **Python 3.13 Wheel Incompatibilities** | High | Low-Med | The environment has Python 3.13.1. Standard dependencies (`pydantic`, `sqlite3`, `pytest`, `rich`, `langchain-core`) are pure Python or have wheels. Use standard library `sqlite3` and pure-Python vector/TF-IDF similarity fallback rather than heavy C-extensions like native Chroma if wheels fail. |
| **Missing Live API Keys** | High | High (100% certain) | Supply `DeterministicMockLLM` as the default backend whenever `OPENAI_API_KEY` is unset. Provide deterministic responses for all 8 agent roles. |
| **Hierarchical Process Delegation Loops** | Med | Med | CrewAI hierarchical process can loop if the manager prompt lacks termination criteria. Explicitly bound max iterations and favor `Process.sequential` for standard pipelines while testing hierarchical mode with strict stop conditions. |
| **Cross-Session SQLite Concurrency Lock** | Low | Low | Enable SQLite WAL (`PRAGMA journal_mode=WAL;`) and configure `busy_timeout=5000` to prevent database locked errors across concurrent agent tool calls. |
| **Git & GitHub Operations Offline Failure** | Med | High (100% offline) | Implement a `MockGitHubAdapter` that stores issues, PRs, and branch state directly in SQLite when `GITHUB_TOKEN` is absent, while local Git commands operate safely on a local initialized Git repo. |

---

## 8. Conclusion
The 8-agent architecture defined here fully satisfies R1 through R4. By establishing clear role boundaries, explicit prompts, deterministic mock LLM execution for tests, and dual-mode CrewAI orchestration, the team can proceed with implementation with complete architectural clarity and zero blocking dependencies.
