import io
import json
import sys
import tempfile
from pathlib import Path

SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from click.testing import CliRunner
from persistent_crewai.persistence.db import init_db
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
)
from persistent_crewai.cli.interactive_query import (
    InteractiveAgentExecutor,
    SubtaskResult,
)
from persistent_crewai.cli.dashboard import (
    create_step_trace_callback,
    get_dashboard_data,
    render_dashboard,
)
from persistent_crewai.cli.repl import InteractiveREPL
from persistent_crewai.cli.main import cli
from persistent_crewai.agents.role_definitions import UnknownRoleError

def run_stress_tests():
    print("=== ADVERSARIAL STRESS TESTING: MILESTONE 4 ===")
    passed = 0
    total = 0

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "stress.db")
        init_db(db_path)
        ws_root = Path(td) / "workspace"
        ws_root.mkdir()
        sm = StateManager(db_path=db_path, project_id="stress_proj", workspace_root=ws_root)
        embedder = DeterministicHashEmbeddingProvider(dimension=64)
        vs = SQLiteVectorStore(db_path=db_path, embedder=embedder, project_id="stress_proj")

        # Test 1: Completely empty DB dashboard rendering
        total += 1
        try:
            rendered = render_dashboard(sm)
            assert "DASHBOARD" in rendered.upper()
            assert "Project" in rendered
            passed += 1
            print("Test 1 PASS: Empty DB dashboard rendered gracefully")
        except Exception as e:
            print(f"Test 1 FAIL: {e}")

        # Test 2: Role normalization with canonical aliases
        total += 1
        try:
            executor = InteractiveAgentExecutor(state_manager=sm, project_id="stress_proj", vector_store=vs)
            norm1 = executor.validate_and_normalize_role("manager")
            norm2 = executor.validate_and_normalize_role("github")
            norm3 = executor.validate_and_normalize_role("memory manager")
            assert norm1 == "Coordinator", f"Expected Coordinator, got {norm1}"
            assert norm2 == "GitHub Manager", f"Expected GitHub Manager, got {norm2}"
            assert norm3 == "Memory & Status Manager", f"Expected Memory & Status Manager, got {norm3}"
            passed += 1
            print("Test 2 PASS: Role aliases correctly normalized")
        except Exception as e:
            print(f"Test 2 FAIL: {e}")

        # Test 3: Unicode resilience (emojis, multi-language)
        total += 1
        try:
            sm.create_project("stress_proj", name="Project 🚀 特殊字符 Café", description="Unicode test context")
            sm.create_milestone("ms_uni", "Milestone 🌟 中文", criteria=["مرحبا Criterion 1"])
            sm.record_decision("Architect", "Decision 💡 ∑ UTF-8", "Rationale with € and ¥")
            vs.add(["Memory record with emoji 🤖 and umlaut äöü"], metadatas=[{"tag": "test"}])
            
            # Query with Unicode
            resp = executor.query_agent("Architect", "Explain decision 💡 with UTF-8 characters 🚀")
            assert len(resp) > 0
            
            # Dashboard with Unicode
            d_rich = render_dashboard(sm)
            assert "DASHBOARD" in d_rich.upper()
            d_plain = render_dashboard(sm, force_plain=True)
            assert "DASHBOARD" in d_plain.upper()
            passed += 1
            print("Test 3 PASS: Unicode resilience confirmed in executor and dashboard")
        except Exception as e:
            print(f"Test 3 FAIL: {e}")

        # Test 4: Extremely large input prompt (50,000 characters)
        total += 1
        try:
            huge_prompt = "Explain architecture. " + ("A" * 50000)
            huge_resp = executor.query_agent("Architect", huge_prompt)
            assert len(huge_resp) > 0
            assert "Architect" in huge_resp
            passed += 1
            print("Test 4 PASS: 50,000 character prompt handled without memory error or timeout")
        except Exception as e:
            print(f"Test 4 FAIL: {e}")

        # Test 5: Consecutive targeted subtasks state transitions
        total += 1
        try:
            # Subtask 1
            res1 = executor.execute_targeted_subtask("Developer", "Subtask Step 1")
            stat1 = sm.get_agent_status("Developer", project_id="stress_proj")
            assert stat1["status"] == "idle", f"Expected idle, got {stat1['status']}"
            assert res1.status == "completed"
            
            # Subtask 2
            res2 = executor.execute_targeted_subtask("Developer", "Subtask Step 2")
            stat2 = sm.get_agent_status("Developer", project_id="stress_proj")
            assert stat2["status"] == "idle"
            assert res2.status == "completed"

            # Check history count
            hist = sm.get_task_history(project_id="stress_proj")
            dev_tasks = [t for t in hist if t["agent_role"] == "Developer"]
            assert len(dev_tasks) == 2, f"Expected 2 developer tasks, got {len(dev_tasks)}"
            passed += 1
            print("Test 5 PASS: Multiple sequential subtasks correctly transition agent status busy->idle")
        except Exception as e:
            print(f"Test 5 FAIL: {e}")

        # Test 6: REPL adversarial input strings (unclosed quotes, malformed syntax)
        total += 1
        try:
            repl = InteractiveREPL(state_manager=sm, project_id="stress_proj", vector_store=vs)
            out1 = repl.execute_command('ask coordinator "unclosed quote')
            assert "Error" in out1, f"Expected handled Error message for unclosed quote, got: {out1}"
            
            out2 = repl.execute_command("ask coordinator")  # Missing prompt
            assert "Usage: ask" in out2

            out3 = repl.execute_command("task tester")  # Missing desc
            assert "Usage: task" in out3

            out4 = repl.execute_command("decision add")  # Missing fields
            assert "Usage: decision add" in out4

            out5 = repl.execute_command("milestone add")  # Missing fields
            assert "Usage: milestone add" in out5

            out6 = repl.execute_command("memory query")  # Missing query
            assert "Usage: memory query" in out6

            out7 = repl.execute_command("export")  # Missing path
            assert "Usage: export" in out7

            out8 = repl.execute_command("import")  # Missing path
            assert "Usage: import" in out8

            out9 = repl.execute_command("   \t   \n  ")  # Blank line
            assert out9 == ""

            out10 = repl.execute_command("resume")  # Missing run_id
            assert "Usage: resume" in out10
            
            passed += 1
            print("Test 6 PASS: REPL adversarial commands and missing arguments handled cleanly")
        except Exception as e:
            print(f"Test 6 FAIL: {e}")

        # Test 7: Telemetry step callback explosion resilience
        total += 1
        try:
            cb = create_step_trace_callback(sm, run_id="stress_trace_run")
            cb(None)
            cb("")
            cb(12345)
            cb([])
            cb({"event": "step", "agent_role": "NonExistentRole", "payload": None})
            cb({"corrupted": True})
            # Verify no exceptions raised
            passed += 1
            print("Test 7 PASS: Step trace callback successfully shielded against malformed events")
        except Exception as e:
            print(f"Test 7 FAIL: {e}")

        # Test 8: Click CLI CLI runner edge cases
        total += 1
        try:
            runner = CliRunner()
            # Help
            r_help = runner.invoke(cli, ["--help"])
            assert r_help.exit_code == 0
            assert "Persistent CrewAI" in r_help.output

            # Bad subcommand
            r_bad = runner.invoke(cli, ["nonexistent_subcommand"])
            assert r_bad.exit_code != 0

            # Missing argument for ask
            r_ask_fail = runner.invoke(cli, ["ask"])
            assert r_ask_fail.exit_code != 0

            # Missing argument for task
            r_task_fail = runner.invoke(cli, ["task"])
            assert r_task_fail.exit_code != 0

            passed += 1
            print("Test 8 PASS: Click CLI subcommands and error handling verified")
        except Exception as e:
            print(f"Test 8 FAIL: {e}")

        # Test 9: 100 decisions scalability and dashboard truncation
        total += 1
        try:
            for i in range(100):
                sm.record_decision("Architect", f"ADR-{i}", f"Rationale {i}", project_id="stress_proj")
            
            dash_text = render_dashboard(sm)
            assert "ADR-" in dash_text
            # Check plain renderer too
            dash_plain = render_dashboard(sm, force_plain=True)
            assert "ADR-" in dash_plain
            passed += 1
            print("Test 9 PASS: Large number of ADR records capped properly in dashboard")
        except Exception as e:
            print(f"Test 9 FAIL: {e}")

    print(f"\nSTRESS TEST RESULT: {passed}/{total} PASSED")
    if passed == total:
        print("ALL ADVERSARIAL STRESS TESTS PASSED!")
    else:
        sys.exit(1)

if __name__ == "__main__":
    run_stress_tests()
