import tempfile
import sys
from pathlib import Path

# Add src to path
SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.persistence.db import init_db
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import (
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
)
from persistent_crewai.cli.interactive_query import InteractiveAgentExecutor
from persistent_crewai.cli.dashboard import get_dashboard_data, render_dashboard
from persistent_crewai.cli.repl import InteractiveREPL

def main():
    print("=== EMPIRICAL FORENSIC INTEGRITY TEST FOR M4 ===")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "state.db")
        init_db(db_path)
        project_id = "empirical_project_99"
        sm = StateManager(db_path=db_path, project_id=project_id)
        
        # 1. Test genuine SQLite state extraction
        sm.create_project(
            project_id=project_id,
            name="Empirical Project Omega",
            description="Grounding extraction test",
        )
        sm.create_milestone(
            milestone_id="ms_omega_100",
            title="Milestone Omega Launch",
            criteria=["Criterion Alpha", "Criterion Beta"],
            status="in_progress",
            project_id=project_id,
        )
        sm.record_decision(
            agent_role="Architect",
            title="ADR-999: Asynchronous Event Bus Architecture",
            rationale="Decouples micro-components",
            status="accepted",
            project_id=project_id,
        )
        embedder = DeterministicHashEmbeddingProvider(dimension=64)
        vs = SQLiteVectorStore(db_path=db_path, embedder=embedder, project_id=project_id)
        vs.add(
            documents=["Secret memory record regarding zero-trust security"],
            metadatas=[{"agent": "Reviewer", "tag": "security"}],
        )

        executor = InteractiveAgentExecutor(
            state_manager=sm,
            project_id=project_id,
            vector_store=vs,
        )
        
        ctx, proj_name, ms_title, snips = executor._extract_grounded_context(
            "What security rules apply to the event bus?",
            include_memory=True,
        )
        
        print(f"Extracted Project Name: {proj_name}")
        assert proj_name == "Empirical Project Omega", f"Expected 'Empirical Project Omega', got '{proj_name}'"
        print(f"Extracted Milestone Title: {ms_title}")
        assert ms_title == "Milestone Omega Launch", f"Expected 'Milestone Omega Launch', got '{ms_title}'"
        print(f"Extracted Snippets: {snips}")
        assert any("Secret memory record" in s for s in snips), f"Expected snippet not found: {snips}"
        assert "ADR-999: Asynchronous Event Bus Architecture" in ctx, "ADR not in context block"
        print(">> CHECK 2 (Genuine SQLite state extraction): PASS")

        # 2. Test query_agent output grounding
        resp = executor.query_agent(
            role="Coordinator",
            user_prompt="What is the current project status?",
        )
        print("Query Agent Response Preview:\n", resp[:300])
        assert "Empirical Project Omega" in resp, "Grounded project name missing from query_agent response"
        assert "Milestone Omega Launch" in resp, "Grounded milestone title missing from query_agent response"
        print(">> CHECK 1 (Authentic execution & grounding): PASS")

        # 3. Test genuine Dashboard table rendering
        data = get_dashboard_data(state_manager=sm, project_id=project_id)
        assert data["project"]["name"] == "Empirical Project Omega"
        assert len(data["agents"]) == 8
        assert data["milestones"][0]["title"] == "Milestone Omega Launch"
        assert data["decisions"][0]["title"] == "ADR-999: Asynchronous Event Bus Architecture"
        
        rendered_rich = render_dashboard(sm)
        assert "Empirical Project Omega" in rendered_rich, "Rich dashboard missing live project name"
        assert "Milestone Omega Launch" in rendered_rich, "Rich dashboard missing live milestone title"
        assert "ADR-999" in rendered_rich, "Rich dashboard missing live ADR"

        rendered_plain = render_dashboard(sm, force_plain=True)
        assert "Empirical Project Omega" in rendered_plain, "ASCII dashboard missing live project name"
        assert "Milestone Omega Launch" in rendered_plain, "ASCII dashboard missing live milestone title"
        assert "ADR-999" in rendered_plain, "ASCII dashboard missing live ADR"
        print(">> CHECK 3 (Genuine Rich/ASCII dashboard rendering): PASS")

        # 4. Test genuine REPL command dispatch
        repl = InteractiveREPL(
            state_manager=sm,
            project_id=project_id,
            vector_store=vs,
        )
        
        status_out = repl.execute_command("status")
        assert "Empirical Project Omega" in status_out, "REPL status missing live state"

        dec_out = repl.execute_command("decisions")
        assert "ADR-999" in dec_out, "REPL decisions missing live ADR"

        ms_out = repl.execute_command("milestones")
        assert "Milestone Omega Launch" in ms_out, "REPL milestones missing live milestone"

        mem_out = repl.execute_command("memory query security")
        assert "Secret memory record" in mem_out, "REPL memory missing live vector record"

        task_out = repl.execute_command("run task Developer 'Implement event bus subscriber'")
        assert len(task_out) > 0, "REPL task failed"
        
        # Verify in SQLite that the task was actually recorded in task_history table!
        history = sm.get_task_history(project_id=project_id)
        assert len(history) == 1, f"Expected 1 task in history, found {len(history)}"
        assert history[0]["agent_role"] == "Developer"
        assert history[0]["status"] == "completed"
        print(f"Recorded Task in DB: {history[0]['task_name']}, ID: {history[0]['task_id']}")
        print(">> CHECK 4 (Genuine REPL execution and DB write): PASS")

    print("\nALL EMPIRICAL INTEGRITY CHECKS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
