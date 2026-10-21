## 2026-10-13T23:10:00Z
You are Worker M4 (CLI & Interactive REPL Interface Implementation Worker).
Your working directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\worker_m4
The project root directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team
The authoritative user request is located at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\ORIGINAL_REQUEST.md
The project specification document is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\PROJECT.md
The Explorer findings are located at:
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m4_1\analysis.md
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m4_2\analysis.md
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m4_3\analysis.md

MANDATORY FIRST STEP: Read ORIGINAL_REQUEST.md and PROJECT.md completely.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Scope & Exclusive File Ownership:
You own and must create the following files exclusively:
- src/persistent_crewai/cli/__init__.py
- src/persistent_crewai/cli/interactive_query.py
- src/persistent_crewai/cli/dashboard.py
- src/persistent_crewai/cli/repl.py
- src/persistent_crewai/cli/main.py
- tests/test_cli.py

Implementation Tasks:
1. `src/persistent_crewai/cli/interactive_query.py` (Feature 27, 28):
   - `InteractiveAgentExecutor`:
     - Constructor accepting `state_manager: StateManager = None`, `project_id: str = None`, `vector_store = None`, `workspace_root = None`, `llm = None`.
     - `query_agent(role: str, user_prompt: str, include_memory: bool = True) -> str`:
       - Validate `user_prompt`: if not isinstance(user_prompt, str) or not user_prompt.strip(): raise ValueError("User prompt cannot be empty").
       - Normalize role via `normalize_role(role)` (raises ValueError/KeyError if invalid role, with message containing 'valid' or role name).
       - Context extraction: pull project metadata, active milestones, recent decisions, and hybrid vector memory records matching `user_prompt`.
       - Format prompt with `--- CURRENT PROJECT CONTEXT ---`.
       - Execute query using `build_agent(role)` or `DeterministicMockLLM.predict()`.
       - Log trace event to SQLite `StateManager.record_event()` or `record_trace()`.
       - Return non-empty string response.
     - `run_targeted_subtask(role: str, task_description: str) -> str`:
       - Runs specific task with agent, updates SQLite `task_history` and `agent_status`, returns output string.
     - `execute_targeted_subtask(role: str, task_description: str) -> dict`:
       - Also provide dictionary return `{"status": "completed", "output": ..., "latency_ms": ...}` for interface flexibility.
2. `src/persistent_crewai/cli/dashboard.py` (Feature 29):
   - `get_dashboard_data(state_manager: StateManager, project_id: str = None) -> dict`:
     - Must return dict with keys `"project"`, `"agents"`, `"decisions"`, `"milestones"` (and `"runs"`).
     - `"agents"` must contain entries for all 8 canonical roles in canonical order.
   - `get_system_dashboard_data`: Alias or function with same capability.
   - `render_dashboard(data_or_state_manager, console=None) -> str`:
     - Seamlessly accepts either a pre-aggregated `dict` or a `StateManager` instance.
     - Formats output using Rich (`Table`, `Panel`, `Console`) with fallback to clean ASCII if rich is absent or stdout is non-interactive.
     - Output string MUST contain `"DASHBOARD"` (case-insensitive) and `"Project"`.
   - `create_step_trace_callback(state_manager: StateManager, run_id: str = None, console=None)`:
     - Step callback for runner/crew, formats step events, records to `execution_traces`.
3. `src/persistent_crewai/cli/repl.py` (Feature 26):
   - `InteractiveREPL`:
     - Constructor accepting `state_manager: StateManager = None`, `project_id: str = None`, `workspace_root: Path = None`, `executor = None`.
     - `execute_command(command: str) -> str`:
       - `help`: list available commands
       - `status`: render system dashboard (contains "Project")
       - `ask <role> <prompt>`: query single agent via `InteractiveAgentExecutor`
       - `run task <role> <desc>` or `task <role> <desc>`: run targeted subtask
       - `run <workflow_type> [--mode sequential|hierarchical]`: trigger workflow runner
       - `decisions` / `decision list`: list decisions (contains "WAL" if WAL decision exists)
       - `milestones` / `milestone list`: list milestones (contains "Foundation" if M1 exists)
       - `memory query <query>` / `memory <query>`: search memory
       - `export <path>`: call `SessionResumptionManager.export_state(path)`
       - `import <path>`: call `SessionResumptionManager.import_state(path)`
       - `resume <run_id>`: resume workflow run
       - `exit` / `quit`
     - `run()`: interactive command loop with prompt `crewai> ` and graceful EOF handling.
4. `src/persistent_crewai/cli/main.py`:
   - Top-level CLI command entrypoint using Click (with graceful argparse fallback if click is unavailable):
     - `crewai-team repl`
     - `crewai-team run <workflow_type> [--mode sequential|hierarchical]`
     - `crewai-team ask <role> <prompt>`
     - `crewai-team status`
     - `crewai-team export <path>`
     - `crewai-team import <path>`
5. `src/persistent_crewai/cli/__init__.py`:
   - Exports `InteractiveAgentExecutor`, `InteractiveREPL`, `get_dashboard_data`, `render_dashboard`, `create_step_trace_callback`, `main`, `cli`.
6. `tests/test_cli.py`:
   - Author thorough unit tests covering:
     - `InteractiveAgentExecutor` Q&A with all roles, context injection, memory search, empty prompt rejection, unknown role rejection.
     - `run_targeted_subtask` execution and SQLite tracking.
     - `get_dashboard_data` and `render_dashboard` with dict and StateManager.
     - `InteractiveREPL.execute_command` for all commands (`status`, `ask`, `run task`, `decisions`, `milestones`, `export`, `import`, `help`).
     - CLI click runner invocation tests.
7. Verification:
   - Run `python -m unittest tests/test_cli.py -v`
   - Run `python -m unittest tests/e2e/test_e2e_tier1_features.py -v` (verify TestTier1FeatureR4CLI passes!)
   - Run `python -m unittest tests/e2e/test_e2e_tier2_boundaries.py -v` (test_b16, test_b17)
   - Run `python -m unittest tests/e2e/test_e2e_tier3_combinations.py -v` (test_c03, test_c04)
   - Run `python -m unittest tests/e2e/test_e2e_tier4_scenarios.py -v` (test_s04!)
   - Run full discovery: `python -m unittest discover -s tests -p "test_*.py" -v`
   - Ensure 100% of discovered tests pass cleanly.
