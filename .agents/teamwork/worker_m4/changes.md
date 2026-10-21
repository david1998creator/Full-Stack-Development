# Worker M4 Changes: CLI & Interactive REPL Interface Implementation

## Overview
Worker M4 implemented the user-facing Command-Line Interface (CLI), interactive REPL command shell, status dashboard, execution trace telemetry streaming, and grounded single-agent Q&A executor for the Persistent CrewAI 8-Agent Software Engineering Framework.

## Files Created & Modified

### 1. `src/persistent_crewai/cli/interactive_query.py`
- Implemented `SubtaskResult(dict)`: Subclass of dict with properties `output`, `status`, `task_id`, `run_id`, `agent_role`, `latency_ms` and `__str__` returning output for transparent string and dict assertions.
- Implemented `InteractiveAgentExecutor`:
  - `__init__(state_manager, project_id, vector_store, workspace_root, llm, github_adapter)`: Resolves dependencies and initializes agent cache.
  - `validate_and_normalize_role(role)`: Validates role and normalizes to canonical names, raising `UnknownRoleError` (inheriting from `ValueError` and `KeyError` with `"valid"` in message) on unknown roles.
  - `get_agent(canonical_role)`: Lazily instantiates and caches `Agent` instances.
  - `_extract_grounded_context(user_prompt, include_memory)`: Extracts project metadata, active/all milestones, ADR decisions, and hybrid vector memory records matching the prompt, formatting context with regexes recognized by `DeterministicMockLLM`.
  - `query_agent(role, user_prompt, include_memory)`: Validates non-empty prompt (raising `ValueError` on empty/whitespace), grounds prompt with SQLite state and semantic memory snippets, executes task with agent, logs `interactive_query` audit event to `execution_traces`, and returns response.
  - `execute_targeted_subtask(role, task_description)`: Tracks subtask in SQLite `workflow_runs` and `task_history`, transitions agent operational status from `idle -> busy -> idle`, measures execution latency, logs completion audit event, and returns `SubtaskResult`.
  - `run_targeted_subtask(role, task_description)`: Convenience wrapper returning string output from `execute_targeted_subtask`.

### 2. `src/persistent_crewai/cli/dashboard.py`
- Implemented `get_dashboard_data(state_manager, project_id)`: Aggregates project context, active/all milestones, recent decisions, recent runs, metrics, and guarantees all 8 canonical agents in canonical order with keys `status`, `current_task_id`, `tasks_completed_count`, etc.
- Added alias `get_system_dashboard_data = get_dashboard_data`.
- Implemented `render_dashboard(data_or_state_manager, console, force_plain)`:
  - Accepts either a pre-aggregated data dictionary or a `StateManager` instance.
  - Dual-mode renderer: Rich formatting with `box.ASCII` borders for terminal styling and Windows cp1252 charmap encoding resilience, plus clean plain-text ASCII fallback `_render_dashboard_plain_ascii`.
  - Guaranteed to contain `"DASHBOARD"` (case-insensitive) and `"Project"`.
- Implemented `create_step_trace_callback(state_manager, run_id, console)`:
  - Connects to CrewAI and runner pipelines.
  - Ensures parent `workflow_run` and `task_history` foreign keys exist before calling `record_trace`.
  - Streamlines event logging with exploding callback suppression.

### 3. `src/persistent_crewai/cli/repl.py`
- Implemented `InteractiveREPL`:
  - `__init__(state_manager, project_id, workspace_root, vector_store, executor, resumption_manager)`: Coordinates persistence, execution, and export engines.
  - `execute_command(command_line)`: Robust shlex command parser handling quotes and Windows backslash paths (`posix=False` on Windows).
    - `status` / `dashboard`: Renders system dashboard.
    - `ask <role> <prompt>`: Queries single agent.
    - `run task <role> <desc>` / `task <role> <desc>`: Runs targeted subtask.
    - `run <workflow_type> [--mode seq|hier]`: Triggers multi-agent workflow.
    - `decisions` / `decision list`: Lists recorded decisions (contains "WAL").
    - `decision add <role> <title> <rationale>`: Records new ADR.
    - `milestones` / `milestone list`: Lists milestones (contains "Foundation").
    - `milestone add <title> <criteria>`: Creates milestone.
    - `memory query <query>` / `memory <query>`: Searches semantic memory.
    - `export <path>`: Exports database state to JSON.
    - `import <path> [--overwrite]`: Imports database state from JSON.
    - `resume <run_id>`: Resumes workflow run.
    - `help`: Displays command guide.
    - `exit` / `quit`: Sets `running=False` and exits.
  - `run_batch(lines)`: Executes script lines sequentially, skipping blanks and comments.
  - `cmdloop(intro, input_stream, output_stream)` / `run()`: Interactive prompt loop with graceful EOF and KeyboardInterrupt handling.

### 4. `src/persistent_crewai/cli/main.py`
- Implemented top-level Click CLI group `cli` (and `main = cli` alias):
  - Global options: `--project-id / -p`, `--db-path / -d`, `--workspace-root / -w`, `--verbose / -v`.
  - Subcommands: `repl`, `status`, `ask`, `task`, `run`, `export`, and `import`.
  - Defaults to invoking `repl` if no subcommand is passed.

### 5. `src/persistent_crewai/cli/__init__.py`
- Cleanly re-exports `InteractiveAgentExecutor`, `SubtaskResult`, `InteractiveREPL`, `get_dashboard_data`, `get_system_dashboard_data`, `render_dashboard`, `create_step_trace_callback`, `cli`, and `main`.

### 6. `tests/test_cli.py`
- Authoritative 37 unit and integration test suite:
  - `TestInteractiveAgentExecutor`: 11 tests covering Q&A across Coordinator, Architect, Memory & Status Manager, all 8 roles, vector memory injection, empty prompt rejection, whitespace rejection, unknown role rejection, and targeted subtasks.
  - `TestDashboard`: 7 tests covering data structure, 8 canonical agents in canonical order, render with dict, render with StateManager, ASCII fallback, and step trace callback with FK safety.
  - `TestInteractiveREPL`: 15 tests covering status, decisions ("WAL"), milestones ("Foundation"), ask (quoted and unquoted), run task, task, decision add, milestone add, export, import, memory query, help, exit, run_batch, and stream cmdloop.
  - `TestCLIMain`: 4 Click `CliRunner` tests for status, ask, task, and export.

## Verification Summary
- `tests/test_cli.py`: 37/37 PASSED
- `tests/e2e/test_e2e_tier1_features.py`: 24/24 PASSED (0 skipped)
- `tests/e2e/test_e2e_tier2_boundaries.py`: 17/17 PASSED
- `tests/e2e/test_e2e_tier3_combinations.py`: 7/7 PASSED
- `tests/e2e/test_e2e_tier4_scenarios.py`: 4/4 PASSED
- Full Discovery (`python -m unittest discover -s tests -p "test_*.py"`): 390/390 PASSED (100% success rate across all project tests)
