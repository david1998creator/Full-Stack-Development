# Milestone 4 Architecture & Implementation Plan: REPL & CLI Interface

## 1. Executive Summary & Scope

Milestone 4 delivers the user-facing CLI and interactive REPL layer for the Persistent CrewAI 8-agent software engineering framework. It provides:
1. **Interactive REPL (`src/persistent_crewai/cli/repl.py`)**: An interactive command shell supporting grounded agent Q&A, workflow triggering, targeted subtask execution, decision/milestone management, semantic memory query, crash resumption, state backup export/import, and batch script execution.
2. **State-Grounded Single-Agent Executor (`src/persistent_crewai/cli/interactive_query.py`)**: Direct Q&A and targeted subtask executor that queries individual specialist agents (Coordinator, Architect, Designer, Developer, Tester, Reviewer, GitHub Manager, Memory & Status Manager) injected with live SQLite project state, decision logs, milestones, and vector store memory.
3. **Status Dashboard & Tracing (`src/persistent_crewai/cli/dashboard.py`)**: Real-time aggregation of project state, operational status for all 8 agents, active/all milestones, recent decisions, and execution traces rendered via Rich tables with ASCII fallback.
4. **Top-Level CLI Entrypoint (`src/persistent_crewai/cli/main.py`)**: A Click-based CLI entry point (configured in `pyproject.toml` as `crewai-team`) supporting `repl`, `run`, `ask`, `status`, `export`, `import`, and `task` commands.
5. **Comprehensive Test Suite (`tests/test_cli.py`)**: Hermetic unit and integration tests verifying all CLI components, boundary conditions, and end-to-end integration contracts.

---

## 2. Interface Contracts & Dependencies

### 2.1 E2E Test Suite Alignment
Analysis of the authoritative E2E test suites establishes these non-negotiable contracts:

| Test File | Test Case | Expected Interface & Contract |
|---|---|---|
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_01_interactive_query_coordinator` | `executor = InteractiveAgentExecutor(state_manager=sm); resp = executor.query_agent(role="Coordinator", user_prompt="What is the current status of the project?")` -> returns non-empty `str`. |
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_02_interactive_query_architect` | `executor.query_agent(role="Architect", user_prompt="Explain the database design and persistence strategy.")` -> returns non-empty `str`. |
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_03_interactive_query_memory_manager` | `executor.query_agent(role="Memory & Status Manager", user_prompt="Show recorded milestones and session runs.")` -> returns non-empty `str`. |
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_04_targeted_subtask_execution` | `task_output = executor.run_targeted_subtask(role="Tester", task_description="Execute unit test verification for SQLite schema")` -> returns non-empty `str`. |
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_05_dashboard_data_generation` | `data = get_dashboard_data(state_manager=sm)` -> contains keys `"project"`, `"agents"` (len == 8), `"decisions"`, `"milestones"`. `rendered = render_dashboard(data)` -> returns `str` containing `"DASHBOARD"`. |
| `tests/e2e/test_e2e_tier1_features.py` | `test_r4_06_repl_command_dispatch` | `repl = InteractiveREPL(state_manager=sm)` -> `repl.execute_command("status")` contains `"Project"`; `repl.execute_command("decisions")` contains `"WAL"`; `repl.execute_command("milestones")` contains `"Foundation"`. |
| `tests/e2e/test_e2e_tier2_boundaries.py` | `test_b16_query_unknown_agent_role_raises_or_warns` | Requesting an unknown role raises `ValueError` containing `"valid"` in message. |
| `tests/e2e/test_e2e_tier2_boundaries.py` | `test_b17_interactive_query_empty_string_rejected` | `executor.query_agent(role="Coordinator", user_prompt="   ")` raises `ValueError`. |
| `tests/e2e/test_e2e_tier3_combinations.py` | `test_c03_interactive_query_and_session_resumption` | Rehydrated `StateManager` passed to `InteractiveAgentExecutor` accurately grounds responses in restored milestones (`status="achieved"`). |
| `tests/e2e/test_e2e_tier3_combinations.py` | `test_c04_vector_store_memory_and_agent_grounding` | `InteractiveAgentExecutor(state_manager=sm, vector_store=vstore)` retrieves semantic memory from `vstore.search(query)` and injects it into agent grounding. |
| `tests/e2e/test_e2e_tier4_scenarios.py` | `test_s04_interactive_human_in_the_loop_repl_session` | REPL executes sequence: `ask coordinator '<prompt>'`, `ask architect '<prompt>'`, `run task tester '<description>'`, `status`, `export <path>`. Verified exported JSON integrity. |

### 2.2 Module Dependencies & Interactions
```
                    ┌───────────────────────────────────────────────┐
                    │      persistent_crewai.cli.main (Click)       │
                    │   (repl, run, ask, status, export, import)   │
                    └───────┬───────────────────────────────┬───────┘
                            │                               │
                            ▼                               ▼
       ┌───────────────────────────────────────┐   ┌────────────────────────────────┐
       │   persistent_crewai.cli.repl          │   │ persistent_crewai.cli.dashboard│
       │   InteractiveREPL                     │──▶│ get_dashboard_data             │
       │   (command dispatch, batch mode)      │   │ render_dashboard (Rich/ASCII)  │
       └───────────────────┬───────────────────┘   └────────────────────────────────┘
                           │
                           ▼
       ┌────────────────────────────────────────────────────────────┐
       │ persistent_crewai.cli.interactive_query                    │
       │ InteractiveAgentExecutor                                   │
       │ - query_agent(role, user_prompt)                           │
       │ - run_targeted_subtask(role, task_description)             │
       └──────────────┬──────────────────────────────┬──────────────┘
                      │                              │
                      ▼                              ▼
       ┌───────────────────────────────┐   ┌────────────────────────────────┐
       │ persistent_crewai.agents      │   │ persistent_crewai.persistence  │
       │ - build_agent()               │   │ - StateManager                 │
       │ - build_crew(), Task()        │   │ - SQLiteVectorStore            │
       │ - DeterministicMockLLM        │   │ - SessionResumptionManager     │
       │ - run_project_workflow()      │   │ - get_connection()             │
       └───────────────────────────────┘   └────────────────────────────────┘
```

---

## 3. Component Architecture & Detailed Design

### 3.1 `src/persistent_crewai/cli/interactive_query.py`
`InteractiveAgentExecutor` is the state-grounded query engine for single-agent dialogues and targeted subtask runs.

#### Class Specification: `InteractiveAgentExecutor`
```python
class InteractiveAgentExecutor:
    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        vector_store: Optional[SQLiteVectorStore] = None,
        llm: Optional[Any] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        project_id: Optional[str] = None,
        **kwargs: Any,
    ):
        ...
```
#### Key Methods & Algorithms:
1. `query_agent(self, role: str, user_prompt: str) -> str`:
   - **Step 1: Input Validation**:
     If `not user_prompt or not user_prompt.strip()`:
     `raise ValueError("User prompt cannot be empty or whitespace.")` (Complies with TC-B.17).
   - **Step 2: Role Validation & Normalization**:
     Normalize role via `normalize_role(role)` from `persistent_crewai.config`.
     If `KeyError` or unknown:
     `raise ValueError(f"Invalid agent role '{role}'. Valid roles: {', '.join(CANONICAL_ROLES)}")` (Complies with TC-B.16).
   - **Step 3: Relational State Extraction**:
     Query `StateManager`:
     - Project metadata: `sm.get_project(pid)`
     - Active & pending milestones: `sm.get_milestones(pid)`
     - Accepted decisions: `sm.get_decisions(pid, limit=10)`
     - Recent task history: `sm.get_task_history(project_id=pid, limit=10)`
     - Agent status: `sm.get_agent_status(canonical_role, project_id=pid)`
   - **Step 4: Vector Memory Retrieval**:
     If `self.vector_store is not None`:
     Perform semantic query: `search_results = self.vector_store.search(user_prompt, k=3)`.
     Extract matching document contents and tags.
   - **Step 5: Grounded Context Assembly**:
     Format context with explicit headers recognized by `DeterministicMockLLM._extract_grounded_context`:
     ```text
     --- Current Project Context ---
     Project: {project_name} (ID: {pid})
     Current Active Milestone: {active_milestone_title} (Criteria: {criteria})
     
     Decisions Recorded:
     - {decision_1}
     - {decision_2}
     
     Relevant Memory Documents:
     - {doc_1}
     ```
   - **Step 6: Agent Invocation**:
     Build single agent via `build_agent(role=canonical_role, state_manager=self.state_manager, llm=self.llm, workspace_root=self.workspace_root)`.
     Create `Task(description=f"User Query: {user_prompt.strip()}", agent=agent)`.
     Execute via `agent.execute_task(task, context=grounded_context)`.
     Log trace event via `state_manager.record_event("interactive_query", {...})`.
     Return response string.

2. `run_targeted_subtask(self, role: str, task_description: str) -> str`:
   - Validate `task_description` (non-empty).
   - Validate and normalize `role`.
   - Record task initiation in `state_manager.record_task_start(...)` and set agent status to `busy`.
   - Build agent with tools via `build_agent(role=canonical_role, tools=get_agent_tools(canonical_role, ...))`.
   - Execute task with agent.
   - Record completion in `state_manager.record_task_completion(...)` and set agent status back to `idle`.
   - Return task deliverable string.

---

### 3.2 `src/persistent_crewai/cli/dashboard.py`
Provides state aggregation and visual dashboard rendering for the terminal.

#### Functions:
1. `get_dashboard_data(state_manager: Optional[StateManager] = None, project_id: Optional[str] = None) -> Dict[str, Any]`:
   - Resolves or initializes `state_manager`.
   - Invokes `state_manager.get_system_dashboard_data(project_id)`.
   - Ensures compatibility with `test_r4_05`:
     - `"project"`: dict of active project metadata.
     - `"agents"`: list of 8 agent status records. If fewer than 8 exist in DB, synthesizes missing canonical roles with status `"idle"` so `len(data["agents"]) == 8`.
     - `"decisions"`: alias/list of recent decisions (asserted in `test_r4_05`).
     - `"milestones"`: alias/list of milestones (asserted in `test_r4_05`).
     - `"metrics"`: dictionary containing counts of completed tasks, error agents, milestones.

2. `render_dashboard(data: Dict[str, Any], use_rich: bool = True) -> str`:
   - Formats a comprehensive textual dashboard.
   - Contains header with `"PERSISTENT CREWAI SYSTEM DASHBOARD"` (ensures `assertIn("DASHBOARD", rendered.upper())` passes).
   - Sections rendered:
     1. **Project Header**: Name, ID, Integrity Mode, Workspace Root.
     2. **Milestones Overview**: ID, Title, Status, Criteria progress.
     3. **8-Agent Operational Roster**: Role, Status (`idle`, `busy`, `error`), Current Task ID, Tasks Completed count.
     4. **Recent Architectural Decisions**: Title, Status, Agent, Rationale.
     5. **System Metrics**: Total Completed Tasks, Active Workflow Runs.
   - Rendering Engine:
     - If `rich` is available and `use_rich=True`, uses `rich.table.Table` and `rich.panel.Panel` captured via `rich.console.Console(record=True, width=100)`.
     - Standard library ASCII fallback using clean formatted borders (`+---+---+`) ensuring consistent display in CI, headless environments, and raw text capture.

---

### 3.3 `src/persistent_crewai/cli/repl.py`
Provides the interactive command loop, command parser, command dispatcher, and batch execution runner.

#### Class Specification: `InteractiveREPL`
```python
class InteractiveREPL:
    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        vector_store: Optional[SQLiteVectorStore] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        project_id: Optional[str] = None,
        interactive_executor: Optional[InteractiveAgentExecutor] = None,
        resumption_manager: Optional[SessionResumptionManager] = None,
        **kwargs: Any,
    ):
        ...
```

#### Command Grammar & Dispatch Routing:
Command input is parsed via `shlex.split()`. The dispatcher routes to handlers:

| Command Syntax | Aliases / Variants | Handler Action |
|---|---|---|
| `help [command]` | `?` | Displays formatted list of commands, usage syntax, and parameter documentation. |
| `status` | `dashboard` | Invokes `get_dashboard_data()` and returns `render_dashboard()`. Outputs project, agents, decisions. |
| `ask <role> <prompt...>` | `query <role> <prompt>` | Routes to `executor.query_agent(role, prompt)`. Supports both single quotes and unquoted multi-word prompts. |
| `run task <role> <desc...>` | `task <role> <desc...>` | Routes to `executor.run_targeted_subtask(role, desc)`. Directly satisfies `test_s04` (`run task tester '...'`). |
| `run <workflow_type> [--mode seq\|hier]` | `workflow <type>` | Invokes `run_project_workflow(workflow_type=..., mode=...)`. Displays workflow execution summary and outputs. |
| `decisions` | `decision list` | Queries `state_manager.get_decisions()`. Formats decision ID, Agent, Title, Rationale. Satisfies `test_r4_06`. |
| `decision add <role> <title> <rationale>` | `decision record ...` | Calls `state_manager.record_decision()`. Returns confirmation string with decision ID. |
| `milestones` | `milestone list` | Queries `state_manager.get_milestones()`. Formats milestone ID, Title, Status, Criteria. Satisfies `test_r4_06`. |
| `milestone add <title> <description>` | `milestone create ...` | Calls `state_manager.create_milestone()`. Returns confirmation with milestone ID. |
| `memory query <query...>` | `memory search ...` | Searches vector store if configured, queries `memory_records` FTS5 table, formats matched context chunks. |
| `resume <run_id>` | `resume run <run_id>` | Calls `run_project_workflow(resume_run_id=run_id)` using `SessionResumptionManager`. |
| `export <path>` | `backup <path>` | Invokes `resumption_manager.export_state(path)`. Returns string starting with `"Export completed: <path>"`. Satisfies `test_s04`. |
| `import <path> [--overwrite]` | `restore <path>` | Invokes `resumption_manager.import_state(path, overwrite=...)`. Returns import summary. |
| `exit` | `quit`, `q` | Sets `self.running = False`, returns `"Goodbye!"`. |

#### Method Specifications:
1. `execute_command(self, command_line: str) -> str`:
   - Strips whitespace. If empty, returns `""`.
   - Parses arguments using `shlex.split(command_line)`.
   - Matches primary command verb.
   - Handles edge cases:
     - `run task <role> <description>` intercepted before general `run <workflow>`.
     - Multi-word arguments without quotes joined naturally (e.g., `ask architect Explain the database design`).
     - Error handling: catches `ValueError`, `KeyError`, `FileNotFoundError`, returns clean formatted error string without crashing REPL loop.
2. `run_batch(self, lines: Union[List[str], Iterable[str]]) -> List[str]`:
   - Iterates through lines. Ignores empty lines and `#` comments.
   - Calls `execute_command()` for each line.
   - Accumulates and returns list of output strings.
3. `cmdloop(self, intro: Optional[str] = None, input_stream=None, output_stream=None) -> None`:
   - Interactive loop reading from `input_stream` (default `sys.stdin`) and writing to `output_stream` (default `sys.stdout`).
   - Prompt: `persistent-crewai> `.
   - Catches `KeyboardInterrupt` (prints newline and continues prompt).
   - Catches `EOFError` or empty string from stream (exits cleanly).

---

### 3.4 `src/persistent_crewai/cli/main.py`
Top-level CLI application entrypoint using Click (with seamless standard library fallback).

#### Click Application Structure:
```python
@click.group(invoke_without_command=True)
@click.option("--project-id", "-p", default=None, help="Target project identifier")
@click.option("--db-path", "-d", default=None, help="SQLite state database path")
@click.option("--workspace-root", "-w", default=None, help="Workspace root directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose debug logging")
@click.pass_context
def cli(ctx: click.Context, project_id: Optional[str], db_path: Optional[str], workspace_root: Optional[str], verbose: bool) -> None:
    ...
    # If no subcommand specified, launch interactive REPL
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)
```

#### CLI Commands:
1. `repl`:
   `@cli.command("repl")`
   - Option `--batch <file>`: executes batch script file.
   - Launches `InteractiveREPL(state_manager=sm, vector_store=vs).cmdloop()`.
2. `run`:
   `@cli.command("run")`
   - Argument: `workflow_type` (default `"feature_development"`).
   - Option `--mode` (default `"sequential"`, choices `["sequential", "hierarchical"]`).
   - Option `--resume-run-id`: resume interrupted run.
   - Calls `run_project_workflow()` and prints execution summary.
3. `ask`:
   `@cli.command("ask")`
   - Argument: `role`.
   - Argument: `prompt`.
   - Executes `executor.query_agent(role, prompt)` and prints response.
4. `task`:
   `@cli.command("task")`
   - Argument: `role`.
   - Argument: `description`.
   - Executes `executor.run_targeted_subtask(role, description)` and prints output.
5. `status`:
   `@cli.command("status")`
   - Calls `get_dashboard_data()` and prints `render_dashboard()`.
6. `export`:
   `@cli.command("export")`
   - Argument: `path`.
   - Calls `SessionResumptionManager.export_state(path)` and prints confirmation.
7. `import`:
   `@cli.command("import")`
   - Argument: `path`.
   - Option `--overwrite`: overwrite existing project.
   - Calls `SessionResumptionManager.import_state(path, overwrite=overwrite)` and prints summary.

---

### 3.5 Package Init Exports (`src/persistent_crewai/cli/__init__.py`)
To ensure full compatibility with `tests/e2e/helpers.py` and downstream modules:
```python
from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
from persistent_crewai.cli.main import cli
from persistent_crewai.cli.repl import InteractiveREPL

__all__ = [
    "InteractiveAgentExecutor",
    "InteractiveREPL",
    "get_dashboard_data",
    "render_dashboard",
    "cli",
]
```

---

## 4. Test Suite Architecture (`tests/test_cli.py`)

A comprehensive test suite of 25+ test cases structured in four logical TestCase classes:

### 4.1 `TestInteractiveAgentExecutor`
- `test_01_query_coordinator_grounded_in_state`: Seed milestones; verify response is non-empty string referencing state.
- `test_02_query_architect_grounded_in_decisions`: Seed ADR decisions; verify response references schema/decisions.
- `test_03_query_memory_manager_grounded_in_runs`: Seed workflow runs; verify memory manager summary.
- `test_04_query_all_eight_roles`: Loop over all canonical roles in `EXPECTED_ROLES`; verify each succeeds without exception.
- `test_05_query_with_vector_store_memory_injection`: Seed vector store with architectural decisions; verify vector store search is called and incorporated.
- `test_06_query_empty_string_rejected`: Passing `""` raises `ValueError`.
- `test_07_query_whitespace_string_rejected`: Passing `"   \n\t"` raises `ValueError`.
- `test_08_query_unknown_role_raises_value_error`: Passing `"Wizard"` raises `ValueError` with `"valid"` in message.
- `test_09_targeted_subtask_execution`: Call `run_targeted_subtask("Tester", "Run unit tests")`; verify task starts, completes, and records in `task_history`.
- `test_10_targeted_subtask_developer_file_operation`: Developer subtask generates code; verify output string.

### 4.2 `TestDashboard`
- `test_11_dashboard_data_structure`: `get_dashboard_data` returns dict with `"project"`, `"agents"` (len == 8), `"decisions"`, `"milestones"`, `"metrics"`.
- `test_12_dashboard_data_guarantees_eight_agents`: Even on fresh/empty DB, all 8 roles are returned with status `"idle"`.
- `test_13_render_dashboard_contains_keyword`: `render_dashboard(data)` returns string with `"DASHBOARD"` uppercase.
- `test_14_render_dashboard_contains_all_sections`: Output includes Project, Milestones, Agent Status, Decisions.
- `test_15_render_dashboard_ascii_fallback`: Verify ASCII rendering when rich formatting is disabled.

### 4.3 `TestInteractiveREPL`
- `test_16_repl_status_command`: `repl.execute_command("status")` contains `"Project"` and `"DASHBOARD"`.
- `test_17_repl_decisions_command`: `repl.execute_command("decisions")` returns list containing recorded decisions.
- `test_18_repl_milestones_command`: `repl.execute_command("milestones")` returns list containing recorded milestones.
- `test_19_repl_ask_command_quoted_prompt`: `repl.execute_command("ask coordinator 'What is the project status?'")` returns valid answer.
- `test_20_repl_ask_command_unquoted_prompt`: `repl.execute_command("ask architect Explain the database design")` parses multi-word prompt correctly.
- `test_21_repl_run_task_command`: `repl.execute_command("run task tester 'Verify SQLite WAL mode'")` executes targeted subtask.
- `test_22_repl_direct_task_command`: `repl.execute_command("task developer 'Implement auth module'")` executes targeted subtask.
- `test_23_repl_decision_add_and_list`: `repl.execute_command("decision add Architect 'ADR-10' 'Use SQLite'")` records decision; verified in `decision list`.
- `test_24_repl_milestone_add_and_list`: `repl.execute_command("milestone add 'M4' 'CLI interface'")` creates milestone; verified in `milestone list`.
- `test_25_repl_memory_query_command`: `repl.execute_command("memory query persistence")` queries vector store and returns matches.
- `test_26_repl_export_command`: `repl.execute_command("export <path>")` outputs `"Export completed"` and creates valid JSON.
- `test_27_repl_import_command`: `repl.execute_command("import <path> --overwrite")` restores state from JSON.
- `test_28_repl_help_command`: `repl.execute_command("help")` lists all commands and syntax.
- `test_29_repl_exit_command`: `repl.execute_command("exit")` sets `repl.running = False`.
- `test_30_repl_batch_execution`: `repl.run_batch(["status", "ask coordinator 'status'", "decisions"])` executes list of commands.
- `test_31_repl_cmdloop_stream_mock`: Run `cmdloop()` with `io.StringIO("status\nexit\n")`; verify termination without hang.
- `test_32_repl_unknown_command`: `repl.execute_command("foobar")` returns unknown command message with suggestion.

### 4.4 `TestCLIEntrypoint`
- `test_33_cli_runner_status`: Use Click `CliRunner().invoke(cli, ["status"])` -> exit_code == 0, output contains `"DASHBOARD"`.
- `test_34_cli_runner_ask`: `CliRunner().invoke(cli, ["ask", "coordinator", "status"])` -> exit_code == 0.
- `test_35_cli_runner_export_import`: Test exporting state to file, importing back via CLI.
- `test_36_cli_runner_default_repl`: Invoking CLI without arguments invokes REPL.
- `test_37_cli_runner_invalid_arguments`: Invalid arguments return non-zero exit code.

---

## 5. Implementation Roadmap & File Locations

| File Path | Component | Status | Key Deliverable |
|---|---|---|---|
| `src/persistent_crewai/cli/__init__.py` | CLI Module Exporter | New | Clean re-exports of `InteractiveAgentExecutor`, `InteractiveREPL`, `get_dashboard_data`, `render_dashboard`, `cli`. |
| `src/persistent_crewai/cli/interactive_query.py` | State-Grounded Agent Q&A | New | `InteractiveAgentExecutor` class with `query_agent()`, `run_targeted_subtask()`, SQLite and VectorStore grounding. |
| `src/persistent_crewai/cli/dashboard.py` | Terminal Dashboard | New | `get_dashboard_data()`, `render_dashboard()` supporting Rich tables and ASCII fallback. |
| `src/persistent_crewai/cli/repl.py` | Interactive REPL Shell | New | `InteractiveREPL` class with comprehensive command dispatch, shlex parsing, batch runner, headless stream support. |
| `src/persistent_crewai/cli/main.py` | Click CLI Entrypoint | New | `cli` group with `repl`, `run`, `ask`, `status`, `export`, `import`, `task` commands. |
| `tests/test_cli.py` | Test Suite | New | 35+ unit & integration test cases validating 100% of CLI features and boundary conditions. |

This architecture fully satisfies all requirements of Milestone 4, adheres strictly to project conventions, guarantees zero regression across Milestones 1-3, and enables 100% passage of E2E Tiers 1-4.
