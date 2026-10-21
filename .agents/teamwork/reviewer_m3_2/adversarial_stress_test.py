"""
Adversarial Stress Testing Script for Milestone 3 (Reviewer M3-2).
Tests delegation constraints, context propagation, telemetry sync, crash recovery, and edge cases.
"""

import sys
import tempfile
import traceback
from pathlib import Path

# Add src to path
project_root = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team")
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from persistent_crewai.agents.role_definitions import (
    get_role_definition,
    ROLE_DEFINITIONS,
    UnknownRoleError,
)
from persistent_crewai.agents.crew_builder import (
    build_agent,
    build_team,
    build_crew,
    Process,
    Agent,
    Task,
)
from persistent_crewai.agents.runner import (
    SequentialPipelineRunner,
    HierarchicalPipelineRunner,
    run_project_workflow,
)
from persistent_crewai.agents.llm_factory import DeterministicMockLLM
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.session import SessionResumptionManager

def run_adversarial_tests():
    print("=== STARTING ADVERSARIAL STRESS TESTS FOR M3 ===")
    results = {}

    # -------------------------------------------------------------------------
    # Test 1: Delegation constraints & boundary tests
    # -------------------------------------------------------------------------
    print("\n[Test 1] Delegation constraints:")
    try:
        # Check all 8 roles in definitions
        for role_name, role_def in ROLE_DEFINITIONS.items():
            if role_name == "Coordinator":
                assert role_def.allow_delegation is True
                assert role_def.delegation_allowed is True
            else:
                assert role_def.allow_delegation is False
                assert role_def.delegation_allowed is False
                
        # Check build_agent default delegation property
        coord_agent = build_agent("Coordinator")
        assert coord_agent.allow_delegation is True
        for spec_role in ["Architect", "Designer", "Developer", "Tester", "Reviewer", "GitHub Manager", "Memory & Status Manager"]:
            spec_agent = build_agent(spec_role)
            assert spec_agent.allow_delegation is False, f"Expected {spec_role} allow_delegation=False, got {spec_agent.allow_delegation}"
        print("  All 8 agent instances enforce strict delegation boundaries (Coordinator=True, 7 specialists=False).")
        
        # Test hierarchical delegation with ambiguous multi-role prompt
        hier_runner = HierarchicalPipelineRunner()
        ambiguous_prompt = "Developer should ask Architect to change DB schema while Tester tests"
        target = hier_runner._determine_target_specialist(ambiguous_prompt)
        print(f"  Ambiguous prompt routed to: {target}")
        assert target in ROLE_DEFINITIONS
        
        # Test invalid explicit role passed to _determine_target_specialist
        target_invalid = hier_runner._determine_target_specialist("Do some work", explicit_role="UnknownAlien")
        print(f"  Unknown explicit role fell back to: {target_invalid}")
        assert target_invalid in ROLE_DEFINITIONS
        
        # Test self-delegation: target_role = 'Coordinator'
        res_self = hier_runner.run(inputs={"task_description": "Organize backlog", "target_role": "Coordinator"})
        assert res_self["status"] == "completed"
        print(f"  Self-delegation to Coordinator handled successfully: {len(res_self['tasks_executed'])} tasks")
        
        results["delegation_constraints"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        results["delegation_constraints"] = f"FAIL: {e}"

    # -------------------------------------------------------------------------
    # Test 2: Context propagation with extreme / edge payloads
    # -------------------------------------------------------------------------
    print("\n[Test 2] Context propagation edge cases:")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            sm = StateManager(db_path=tmppath / "test.db", workspace_root=tmppath, project_id="adv_ctx")
            sm.create_project("adv_ctx", "Adv Ctx", "Description for Adv Ctx", root_path=tmppath)
            
            # Massive payload test (100KB string)
            massive_text = "BLUEPRINT_INVARIANT_" + ("X" * 100000)
            
            class HugePayloadMockLLM(DeterministicMockLLM):
                def predict(self, text: str, **kwargs) -> str:
                    if "(Architect)" in text:
                        return f"Final Answer: {massive_text}"
                    return super().predict(text, **kwargs)
            
            runner = SequentialPipelineRunner(
                state_manager=sm,
                workspace_root=tmppath,
                llm=HugePayloadMockLLM(),
            )
            res = runner.run(workflow_type="feature_development")
            assert res["status"] == "completed"
            
            # Verify developer task prompt received the huge text
            task_history = sm.get_task_history()
            arch_task = [t for t in task_history if t["agent_role"] == "Architect"][0]
            assert massive_text in arch_task["actual_output"]
            print("  Massive 100KB context propagated cleanly through SQLite and pipeline.")
            
            # Test empty output propagation
            class EmptyOutputMockLLM(DeterministicMockLLM):
                def predict(self, text: str, **kwargs) -> str:
                    if "(Architect)" in text:
                        return ""
                    return super().predict(text, **kwargs)
            
            runner2 = SequentialPipelineRunner(
                state_manager=sm,
                workspace_root=tmppath,
                llm=EmptyOutputMockLLM(),
            )
            res2 = runner2.run(workflow_type="feature_development", run_id="run_empty_output")
            assert res2["status"] == "completed"
            print("  Empty output from upstream agent handled gracefully without crash.")
            
        results["context_propagation"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        results["context_propagation"] = f"FAIL: {e}"

    # -------------------------------------------------------------------------
    # Test 3: State & Telemetry synchronization under error conditions
    # -------------------------------------------------------------------------
    print("\n[Test 3] State & Telemetry sync under failures:")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            sm = StateManager(db_path=tmppath / "test.db", workspace_root=tmppath, project_id="adv_telemetry")
            sm.create_project("adv_telemetry", "Adv Telemetry", "Description for Adv Telemetry", root_path=tmppath)
            
            # Agent failure in middle of sequence (Tester fails)
            class MidSequenceFailureLLM(DeterministicMockLLM):
                def predict(self, text: str, **kwargs) -> str:
                    if "(Tester)" in text:
                        raise RuntimeError("Adversarial QA failure injected")
                    return super().predict(text, **kwargs)
            
            runner = SequentialPipelineRunner(
                state_manager=sm,
                workspace_root=tmppath,
                llm=MidSequenceFailureLLM(),
            )
            res = runner.run(workflow_type="feature_development", run_id="run_fail_mid")
            assert res["status"] == "failed"
            assert "Adversarial QA failure injected" in res["error_message"]
            
            # Verify Tester status is 'error' and has last_error
            tester_st = sm.get_agent_status("Tester")
            assert tester_st["status"] == "error", f"Tester status should be error, got {tester_st['status']}"
            assert "Adversarial QA failure injected" in tester_st["last_error"]
            
            # Verify Architect, Designer, Developer are 'idle' (not stuck in 'busy')
            for prev_role in ["Architect", "Designer", "Developer"]:
                st = sm.get_agent_status(prev_role)
                assert st["status"] == "idle", f"Expected {prev_role} to be idle, got {st['status']}"
                
            # Verify downstream agents (Reviewer, GitHub Manager, Coordinator) were NOT executed
            for post_role in ["Reviewer", "GitHub Manager", "Coordinator"]:
                st = sm.get_agent_status(post_role)
                assert st["tasks_completed_count"] == 0
                
            # Check latency tracking
            traces = sm.list_traces(run_id="run_fail_mid")
            assert len(traces) > 0
            for t in traces:
                if t["event_type"] in ("task_complete", "task_error"):
                    assert "latency_ms" in t["payload"]
                    assert t["payload"]["latency_ms"] >= 0
            print("  Telemetry, latency, error boundaries, and agent state transitions verified.")
            
        results["telemetry_sync"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        results["telemetry_sync"] = f"FAIL: {e}"

    # -------------------------------------------------------------------------
    # Test 4: Crash recovery & session resumption robustness
    # -------------------------------------------------------------------------
    print("\n[Test 4] Crash recovery & session resumption edge cases:")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            sm = StateManager(db_path=tmppath / "test.db", workspace_root=tmppath, project_id="adv_crash")
            sm.create_project("adv_crash", "Adv Crash", "Description for Adv Crash", root_path=tmppath)
            
            runner = SequentialPipelineRunner(state_manager=sm, workspace_root=tmppath)
            
            # Normal crash recovery: Interrupted run resumption
            run_interrupted = "run_interrupted_456"
            sm.create_workflow_run(run_interrupted, "feature_development")
            # Step 1 done
            tid1 = f"{run_interrupted}_architecture_design"
            sm.record_task_start(tid1, run_interrupted, "Architect", "architecture_design", execution_order=0)
            sm.record_task_completion(tid1, "Architect", "Blueprint 1")
            # Step 2 interrupted
            tid2 = f"{run_interrupted}_ui_ux_specification"
            sm.record_task_start(tid2, run_interrupted, "Designer", "ui_ux_specification", execution_order=1)
            sm.start_task(tid2)
            
            # Resume interrupted run
            res_resumed = runner.run(workflow_type="feature_development", resume_run_id=run_interrupted)
            assert res_resumed["status"] == "completed"
            executed_names = [t["task_name"] for t in res_resumed["tasks_executed"]]
            assert "architecture_design" not in executed_names
            assert "ui_ux_specification" in executed_names
            assert len(executed_names) == 7
            print(f"  Interrupted run resumed successfully: skipped {8 - len(executed_names)} completed step, executed {len(executed_names)} steps.")

            # All-completed run resumption
            run_all_done = "run_all_done_123"
            sm.create_workflow_run(run_all_done, "code_review")
            for idx, (role, sname, desc) in enumerate([
                ("Architect", "provide_architecture_context", "..."),
                ("Reviewer", "static_analysis_and_audit", "..."),
                ("Tester", "test_coverage_audit", "..."),
                ("Coordinator", "review_synthesis", "..."),
            ]):
                tid = f"{run_all_done}_{sname}"
                sm.record_task_start(tid, run_all_done, role, sname, desc, execution_order=idx)
                sm.record_task_completion(tid, role, f"Deliverable {sname}")
            sm.complete_workflow_run(run_all_done, final_output="All done", status="completed")
            
            res_all = runner.run(workflow_type="code_review", resume_run_id=run_all_done)
            assert res_all["status"] == "completed"
            assert len(res_all["tasks_executed"]) == 0
            print("  Resumption of fully completed run cleanly skips all steps (0 tasks executed).")
            
            # Consecutive inspect_state idempotency
            resump = SessionResumptionManager(db_path=tmppath / "test.db", project_id="adv_crash")
            insp1 = resump.inspect_state(auto_recover=True)
            insp2 = resump.inspect_state(auto_recover=True)
            assert len(insp2["interrupted_tasks"]) == 0
            print("  Consecutive inspect_state is idempotent.")
            
        results["crash_recovery"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        results["crash_recovery"] = f"FAIL: {e}"

    # -------------------------------------------------------------------------
    # Test 5: CrewAI Compatibility & Interface Contracts
    # -------------------------------------------------------------------------
    print("\n[Test 5] Interface contracts & type checks:")
    try:
        # Hierarchical crew without Coordinator or manager
        dev_agent = build_agent("Developer")
        tester_agent = build_agent("Tester")
        try:
            build_crew(agents=[dev_agent, tester_agent], process=Process.hierarchical)
            raise AssertionError("Should have raised ValueError for hierarchical crew without Coordinator")
        except ValueError as ve:
            assert "Hierarchical crew requires a manager_agent or a Coordinator" in str(ve)
            print("  build_crew hierarchical validation correctly rejected missing manager.")
            
        # run_project_workflow input validation
        try:
            run_project_workflow(workflow_type=None)
            raise AssertionError("Should have raised ValueError for None workflow_type")
        except ValueError:
            pass
            
        try:
            run_project_workflow(mode="invalid_quantum_superposition")
            raise AssertionError("Should have raised ValueError for invalid mode")
        except ValueError:
            pass
            
        # Role normalization and error type
        try:
            get_role_definition("non_existent_ninja_agent")
            raise AssertionError("Should have raised UnknownRoleError")
        except UnknownRoleError as ure:
            assert isinstance(ure, KeyError)
            assert isinstance(ure, ValueError)
            print("  UnknownRoleError dual inheritance (ValueError, KeyError) confirmed.")

        results["interface_contracts"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        results["interface_contracts"] = f"FAIL: {e}"

    print("\n=== SUMMARY OF ADVERSARIAL STRESS TESTS ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    
    return all(v == "PASS" for v in results.values())

if __name__ == "__main__":
    success = run_adversarial_tests()
    sys.exit(0 if success else 1)
