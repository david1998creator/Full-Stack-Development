# Handoff Report — Explorer M3-3: Pipeline Runners Architecture & Implementation Plan

## 1. Observation

1. **User Request & System Invariants**:
   - `ORIGINAL_REQUEST.md:43`: *"System executes sequential or hierarchical tasks across all 8 roles without runtime errors."*
   - `PROJECT.md:110-111`: Features 24 & 25 specify `Sequential Pipeline Runner` (chained execution passing context artifacts upstream -> downstream) and `Hierarchical Pipeline Runner` (manager-led delegation and task coordination).
   - `PROJECT.md:144`: Contract defines `run_project_workflow(workflow_type: str, mode: str = "sequential") -> dict`.
   - Dispatch task specifies:
     - `SequentialPipelineRunner` (Feature 24): Chained multi-step software engineering workflows, context artifact propagation, state persistence via `StateManager`.
     - `HierarchicalPipelineRunner` (Feature 25): Manager-led orchestration where Coordinator delegates subtasks dynamically to specialized agents; step execution telemetry streaming and error trapping.
     - Resumption & Crash Recovery: Integration with `SessionResumptionManager` to pick up interrupted runs from the last completed task.
     - Interface contract conformance: `run_project_workflow(workflow_type: str, mode: str = "sequential", project_id: str = None) -> dict`.
     - Unit and pipeline test plan in `tests/test_pipeline.py`.

2. **Existing Implementation State**:
   - `src/persistent_crewai/persistence/state_manager.py`:
     - Lines 459–558: `create_workflow_run()`, `update_workflow_run()`, `complete_workflow_run()`, `get_workflow_run()`, `list_workflow_runs()`.
     - Lines 559–759: `record_task_start()`, `start_task()`, `record_task_completion()` (with automatic `latency_ms` tracking and agent status transition to `'idle'`).
     - Lines 760–797: `record_task_failure()` (transitions task to `'failed'`, agent status to `'error'`, records `last_error`).
     - Lines 990–1092: `update_agent_status()`, `get_agent_status()`, `get_all_agent_statuses()`.
     - Lines 1113–1212: `record_trace()`, `record_event()`, `list_traces()`.
   - `src/persistent_crewai/persistence/session.py`:
     - Lines 51–64: `WorkflowResumePlan` dataclass with `run_id`, `workflow_type`, `completed_tasks`, `interrupted_tasks`, `pending_tasks`, `next_task`, `recommended_action`.
     - Lines 120–210: `SessionResumptionManager.inspect_state(auto_recover=True)` automatically detects tasks marked `in_progress` without completion timestamp, transitions them to `interrupted`, resets `busy` agents to `idle`, and transitions running workflow runs to `interrupted`.
     - Lines 284–318: `SessionResumptionManager.resume_workflow(run_id)` produces `WorkflowResumePlan` and transitions workflow run back to `'running'`.
   - `src/persistent_crewai/tools/__init__.py`:
     - Lines 20–42: `BaseTool` resilient fallback pattern when `crewai` is not installed.
     - Lines 441–499: `get_agent_tools(role)` assigns tools to all 8 roles.

3. **Existing E2E Test Expectations**:
   - `tests/e2e/test_e2e_tier1_features.py:149-157`:
     ```python
     runner = SequentialPipelineRunner(state_manager=ctx.state_manager)
     run_result = runner.run(
         workflow_type="feature_development",
         inputs={"feature_title": "Add SQLite WAL mode", "scope": "persistence"},
     )
     self.assertIsInstance(run_result, dict)
     self.assertEqual(run_result.get("status"), "completed")
     self.assertIn("tasks_executed", run_result)
     self.assertTrue(len(run_result["tasks_executed"]) > 0)
     ```
   - `tests/e2e/test_e2e_tier1_features.py:165-171`:
     ```python
     runner = HierarchicalPipelineRunner(state_manager=ctx.state_manager)
     self.assertEqual(runner.manager_role, "Coordinator")
     run_result = runner.run(
         workflow_type="targeted_subtask",
         inputs={"task_description": "Review architecture blueprint"},
     )
     self.assertIn("status", run_result)
     ```
   - `tests/e2e/test_e2e_tier4_scenarios.py:252-272`:
     Scenario 3 validates crash recovery where task 1 is completed, task 2 is interrupted, `SessionResumptionManager.inspect_state()` recovers state, and pipeline resumes.

4. **Runtime Environment Probing**:
   - Running `python -c "import crewai"` exited with code 1 (`ModuleNotFoundError: No module named 'crewai'`).
   - Running `python -m unittest tests/test_tools.py` executed 55 tests in 0.509s (all OK).
   - Running `python -m unittest tests/test_persistence.py` executed 29 tests in 3.307s (all OK).
   - Therefore, the runner MUST support a standalone execution fallback when `crewai` is not installed, while being fully compatible with native `crewai` when installed.

---

## 2. Logic Chain

1. **Requirement 1 & Observation 3**: E2E test suites expect `SequentialPipelineRunner` and `HierarchicalPipelineRunner` to be directly importable from `persistent_crewai.agents.runner`.
2. **Requirement 2 & Observation 2**: `StateManager` already provides atomic, thread-safe methods for `create_workflow_run`, `record_task_start`, `start_task`, `record_task_completion`, `record_task_failure`, and `record_trace`. By sequencing these calls around each task step, `SequentialPipelineRunner` guarantees:
   - `workflow_runs` row created at start and finalized at completion.
   - `task_history` updated with task status, actual output, and `latency_ms`.
   - `agent_status` transitioned from `idle` -> `busy` during execution, and returned to `idle` upon completion (with `tasks_completed_count` incremented).
   - Every reasoning step recorded to `execution_traces`.
3. **Requirement 3 & Observation 1**: Context propagation is achieved by capturing each upstream agent's `actual_output` into a `context_artifacts` dictionary and injecting it into the downstream agent's prompt:
   - Developer receives outputs from Architect and Designer.
   - Tester receives outputs from Developer.
   - Reviewer receives outputs from Developer and Tester.
   - GitHub Manager receives approval from Reviewer.
   - Memory Manager receives all prior outputs.
   - Coordinator receives summary from Memory Manager.
4. **Requirement 4 & Observation 3**: `HierarchicalPipelineRunner` requires `manager_role = "Coordinator"` as an explicit attribute (`test_r1_06`). In hierarchical mode, Coordinator dynamically inspects the task description, delegates to the appropriate specialist, streams step events via `step_callback`, and traps any exceptions.
5. **Requirement 5 & Observation 2**: Crash recovery integrates cleanly with `SessionResumptionManager`:
   - `resumption_mgr.inspect_state(auto_recover=True)` recovers interrupted tasks and busy agents.
   - `resumption_mgr.resume_workflow(run_id)` returns a `WorkflowResumePlan` indicating which tasks are already completed and which task is next.
   - The runner rehydrates `context_artifacts` from completed tasks, skips already executed steps, and retries the interrupted task followed by remaining pending tasks.
6. **Requirement 6 & Observation 4**: Because `crewai` is not pre-installed in the current Python environment, `runner.py` must incorporate a bimodal architecture:
   - When native `crewai` is available, it constructs `Crew` with `Process.sequential` or `Process.hierarchical`.
   - When running offline or when `crewai` is absent, it executes using its internal orchestration engine backed by `DeterministicMockLLM`.
   - Both modes share the exact same `StateManager` calls, telemetry streaming, and context propagation.

---

## 3. Caveats

1. **Third-Party `crewai` Absence**: The local environment does not have `crewai` installed. All offline tests in `tests/test_pipeline.py` and `tests/e2e/` must execute reliably using the standalone fallback engine and `DeterministicMockLLM`.
2. **LLM Provider Factory & Agent Definitions Co-Dependency**: `runner.py` imports `build_team` from `crew_builder.py` and `get_llm` from `llm_factory.py`. These modules are currently being designed in parallel by Explorers M3-1 and M3-2. `runner.py` will use dynamic/lazy imports (`from persistent_crewai.agents.crew_builder import build_team`, etc.) so that it works seamlessly as soon as M3-1 and M3-2 land.
3. **Multi-Threaded SQLite Concurrency**: While SQLite is configured with WAL mode (`PRAGMA journal_mode = WAL;`) and `PRAGMA busy_timeout = 5000;`, all database writes within the runner should use the `with state_manager.connection()` context manager to guarantee proper thread synchronization.

---

## 4. Conclusion

1. The exact architecture and implementation plan for `src/persistent_crewai/agents/runner.py` has been fully formulated in `analysis.md`.
2. The implementation delivers:
   - `SequentialPipelineRunner`: Chained multi-step execution across all 8 roles (`feature_development`, `bugfix`, `code_review`), context artifact propagation, `StateManager` lifecycle integration, latency tracking, and trace logging.
   - `HierarchicalPipelineRunner`: Coordinator-led manager orchestration (`manager_role = "Coordinator"`), dynamic specialist subtask delegation, telemetry streaming via `step_callback`, and error trapping.
   - `SessionResumptionManager` integration: Crash inspection, agent reset, context rehydration, and continuation from last uncompleted task.
   - Interface contract: `run_project_workflow(workflow_type: str, mode: str = "sequential", project_id: str = None) -> dict`.
   - Full test plan in `tests/test_pipeline.py` covering unit, sequential, hierarchical, resumption, telemetry, and edge case scenarios.

---

## 5. Verification Method

### Verification Commands
Once `runner.py` and `tests/test_pipeline.py` are implemented:
```powershell
# 1. Run pipeline test suite
python -m unittest tests/test_pipeline.py

# 2. Run Tier 1 E2E feature suite
python -m unittest tests/e2e/test_e2e_tier1_features.py

# 3. Run Tier 3 E2E combinations suite
python -m unittest tests/e2e/test_e2e_tier3_combinations.py

# 4. Run Tier 4 E2E scenarios suite
python -m unittest tests/e2e/test_e2e_tier4_scenarios.py
```

### Files to Inspect
- `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m3_3\analysis.md`
- `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m3_3\handoff.md`

### Invalidation Conditions
- If `SequentialPipelineRunner` does not pass outputs of preceding steps into downstream task contexts.
- If `HierarchicalPipelineRunner.manager_role` does not equal `"Coordinator"`.
- If `run_project_workflow("feature_development", mode="invalid")` does not raise `ValueError`.
- If a killed process leaves dangling `busy` agents after resumption inspection.
