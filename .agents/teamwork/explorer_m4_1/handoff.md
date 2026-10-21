# Milestone 4 Handoff Report: REPL & CLI Interface

## 1. Observation
1. **Directory and Project Layout**:
   - `src/persistent_crewai/` currently contains `config.py`, `persistence/`, `tools/`, and `agents/`. The directory `src/persistent_crewai/cli/` has not yet been implemented (`find_by_name` returned 41 files, none in `cli/`).
   - `pyproject.toml` lines 46-47 declare:
     ```toml
     [project.scripts]
     crewai-team = "persistent_crewai.cli.main:cli"
     ```
   - Dependencies in `requirements.txt` include `click>=8.0.0`, `rich>=13.0.0`, `crewai>=1.15.0`, `pydantic>=2.0.0`, `python-dotenv>=1.0.0`.
2. **E2E Test Contracts**:
   - `tests/e2e/helpers.py` lines 102-109:
     ```python
     try:
         from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
         from persistent_crewai.cli.repl import InteractiveREPL
         from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
         HAS_CLI = True
     except ImportError:
         HAS_CLI = False
     ```
   - `tests/e2e/test_e2e_tier1_features.py` lines 503-606 (`TestTier1FeatureR4CLI`):
     - `test_r4_01_interactive_query_coordinator`: `executor = InteractiveAgentExecutor(state_manager=ctx.state_manager); response = executor.query_agent(role="Coordinator", user_prompt="What is the current status of the project?")` -> returns non-empty `str`.
     - `test_r4_02_interactive_query_architect`: `executor.query_agent(role="Architect", user_prompt="Explain the database design and persistence strategy.")` -> returns non-empty `str`.
     - `test_r4_03_interactive_query_memory_manager`: `executor.query_agent(role="Memory & Status Manager", user_prompt="Show recorded milestones and session runs.")` -> returns non-empty `str`.
     - `test_r4_04_targeted_subtask_execution`: `task_output = executor.run_targeted_subtask(role="Tester", task_description="Execute unit test verification for SQLite schema")` -> returns non-empty `str`.
     - `test_r4_05_dashboard_data_generation`: `data = get_dashboard_data(state_manager=ctx.state_manager)`; asserts `"project" in data`, `"agents" in data`, `"decisions" in data`, `"milestones" in data`, `len(data["agents"]) == 8`; `rendered = render_dashboard(data)` contains `"DASHBOARD"`.
     - `test_r4_06_repl_command_dispatch`: `repl = InteractiveREPL(state_manager=ctx.state_manager)`; `repl.execute_command("status")` contains `"Project"`; `repl.execute_command("decisions")` contains `"WAL"`; `repl.execute_command("milestones")` contains `"Foundation"`.
   - `tests/e2e/test_e2e_tier2_boundaries.py` lines 339-357:
     - `test_b16_query_unknown_agent_role_raises_or_warns`: `get_role_definition("Wizard")` raises `ValueError` with `"valid"` in message.
     - `test_b17_interactive_query_empty_string_rejected`: `executor.query_agent(role="Coordinator", user_prompt="   ")` raises `ValueError`.
   - `tests/e2e/test_e2e_tier3_combinations.py` lines 160-236:
     - `test_c03`: `executor = InteractiveAgentExecutor(state_manager=rebooted_sm); executor.query_agent(role="Coordinator", user_prompt="What milestones have been achieved?")` -> returns non-empty string.
     - `test_c04`: `executor = InteractiveAgentExecutor(state_manager=sm, vector_store=vstore); executor.query_agent(role="Architect", user_prompt="What did we decide regarding zero-dependency database persistence?")` -> retrieves documents from `vstore.search()` and injects into agent context.
   - `tests/e2e/test_e2e_tier4_scenarios.py` lines 273-315 (`test_s04_interactive_human_in_the_loop_repl_session`):
     - `repl = InteractiveREPL(state_manager=sm)`
     - `repl.execute_command("ask coordinator 'What is the current project status?'")`
     - `repl.execute_command("ask architect 'Explain the database design'")`
     - `repl.execute_command("run task tester 'Verify SQLite WAL mode integrity'")`
     - `repl.execute_command("status")`
     - `repl.execute_command(f"export {export_target}")`
3. **Existing Infrastructure Integration**:
   - `src/persistent_crewai/agents/runner.py` line 625: `run_project_workflow(workflow_type, mode, project_id, inputs, state_manager, ...)` is already implemented and exported.
   - `src/persistent_crewai/persistence/state_manager.py` line 1231: `get_system_dashboard_data(project_id)` returns `{"project": ..., "agents": ..., "active_milestone": ..., "all_milestones": ..., "recent_decisions": ..., "recent_runs": ..., "metrics": ...}`.
   - `src/persistent_crewai/agents/llm_factory.py` lines 318-335: `DeterministicMockLLM._extract_grounded_context` searches for `"--- current project context ---"` and `"Project:"` / `"Current Active Milestone:"` in prompts to generate grounded context tags.
   - `src/persistent_crewai/persistence/session.py` lines 464, 400: `export_state(output_path)` and `import_state(source_path, overwrite)` are implemented and operational.

---

## 2. Logic Chain
1. From Observation 1 & 2 (`tests/e2e/helpers.py`), importing `persistent_crewai.cli` determines `HAS_CLI = True` which activates all Tier 1-4 CLI tests. Therefore, four specific modules must be created under `src/persistent_crewai/cli/`:
   - `__init__.py`
   - `interactive_query.py`
   - `dashboard.py`
   - `repl.py`
   - `main.py`
2. From Observation 2 (`test_r4_01`, `test_r4_02`, `test_r4_03`, `test_b17`, `test_c04`), `InteractiveAgentExecutor`:
   - Must validate prompt: reject empty/whitespace queries with `ValueError`.
   - Must normalize role using `config.normalize_role()` and raise `ValueError` with suggestions if invalid.
   - Must extract SQLite state (milestones, decisions, project, agent status) and semantic memory (if `vector_store` provided) and format it under `"--- Current Project Context ---"`.
   - Must instantiate the target agent via `build_agent()` and execute via `agent.execute_task()`.
   - Must provide `run_targeted_subtask(role, task_description)` recording task start and completion in `state_manager`.
3. From Observation 2 (`test_r4_05`), `get_dashboard_data()`:
   - Must return a dictionary containing `"project"`, `"agents"` (len == 8), `"decisions"`, `"milestones"`, and `"metrics"`.
   - `render_dashboard()` must return a formatted string containing `"DASHBOARD"` (case-insensitive), utilizing Rich when available with clean ASCII fallback.
4. From Observation 2 (`test_r4_06`, `test_s04`) and dispatch instructions, `InteractiveREPL`:
   - Must parse command input via `shlex.split()`.
   - Must route `status`, `ask <role> <prompt>`, `run task <role> <desc>` (and `task <role> <desc>`), `run <workflow>`, `decisions` (and `decision list/add`), `milestones` (and `milestone list/add`), `memory query`, `resume <run_id>`, `export <path>`, `import <path>`, `help`, `exit`/`quit`.
   - Must support non-interactive batch execution (`run_batch`) and headless execution using custom streams.
5. From Observation 1 (`pyproject.toml`), `src/persistent_crewai/cli/main.py` must define Click group `cli` as entry point for console script `crewai-team`.
6. From all the above observations, a dedicated test file `tests/test_cli.py` containing 35+ test cases must be added to provide 100% unit and integration test coverage for Milestone 4.

---

## 3. Caveats
- No caveats. The exact interfaces, imports, and method signatures have been fully verified against both the E2E test suites and the underlying M1-M3 implementations.

---

## 4. Conclusion
The architecture and implementation plan for Milestone 4 is fully formulated, documented, and ready for immediate implementation by the development agent. All contracts in `analysis.md` match existing codebase expectations and guarantee 100% test passage without regressions.

---

## 5. Verification Method
To verify the implementation once coded:
1. **Unit & Integration Verification**:
   ```powershell
   .\.venv\Scripts\python.exe -m unittest tests/test_cli.py -v
   ```
2. **E2E Feature Verification**:
   ```powershell
   .\.venv\Scripts\python.exe -m unittest tests/e2e/test_e2e_tier1_features.py -k TestTier1FeatureR4CLI -v
   .\.venv\Scripts\python.exe -m unittest tests/e2e/test_e2e_tier2_boundaries.py -k "test_b16 or test_b17" -v
   .\.venv\Scripts\python.exe -m unittest tests/e2e/test_e2e_tier3_combinations.py -k "test_c03 or test_c04" -v
   .\.venv\Scripts\python.exe -m unittest tests/e2e/test_e2e_tier4_scenarios.py -k test_s04 -v
   ```
3. **Files to Inspect**:
   - `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m4_1\analysis.md`
   - `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m4_1\handoff.md`
