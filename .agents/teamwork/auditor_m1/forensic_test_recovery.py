"""
Independent forensic test for Session Resumption and Crash Recovery.
Directly inspects SQLite database rows via raw sqlite3 connection to confirm row updates.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.session import SessionResumptionManager

def test_recovery_forensics():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_file = Path(temp_dir) / "crash_recovery.db"
        
        # Step 1: Set up project, workflow, and an in-progress crash simulation
        mgr = SessionResumptionManager(db_path=db_file, project_id="crash_proj")
        sm = mgr._state_mgr
        sm.create_project("crash_proj", "Crash Simulation Project", "Testing crash recovery")
        
        sm.create_workflow_run("run_c1", workflow_type="feature_pipeline")
        sm.record_task_start("task_c1", "run_c1", "Developer", "Code module")
        sm.start_task("task_c1")
        
        # Verify in raw DB that status is 'in_progress' and agent is 'busy'
        raw_conn = sqlite3.connect(str(db_file))
        raw_conn.row_factory = sqlite3.Row
        
        row_task_before = raw_conn.execute("SELECT status, completed_at FROM task_history WHERE task_id = 'task_c1'").fetchone()
        row_agent_before = raw_conn.execute("SELECT status, current_task_id FROM agent_status WHERE agent_role = 'Developer' AND project_id = 'crash_proj'").fetchone()
        row_run_before = raw_conn.execute("SELECT status FROM workflow_runs WHERE run_id = 'run_c1'").fetchone()
        
        assert row_task_before["status"] == "in_progress", f"Precondition failed: {row_task_before['status']}"
        assert row_agent_before["status"] == "busy", f"Precondition failed: {row_agent_before['status']}"
        assert row_agent_before["current_task_id"] == "task_c1", f"Precondition failed: {row_agent_before['current_task_id']}"
        assert row_run_before["status"] == "running", f"Precondition failed: {row_run_before['status']}"
        print("[RECOVERY CHECK] Pre-crash state confirmed: task=in_progress, agent=busy, run=running")
        
        # Step 2: Trigger crash recovery routine
        recovery_result = mgr.inspect_state(auto_recover=True)
        print(f"[RECOVERY CHECK] Recovery routine executed: recovered {len(recovery_result['interrupted_tasks'])} tasks, agents: {recovery_result['recovered_agents']}")
        
        # Step 3: Raw SQLite inspection of database rows post-recovery
        # (Using a NEW raw connection to ensure no memory caching)
        raw_conn.close()
        raw_conn2 = sqlite3.connect(str(db_file))
        raw_conn2.row_factory = sqlite3.Row
        
        row_task_after = raw_conn2.execute("SELECT status, completed_at, error_details FROM task_history WHERE task_id = 'task_c1'").fetchone()
        row_agent_after = raw_conn2.execute("SELECT status, current_task_id FROM agent_status WHERE agent_role = 'Developer' AND project_id = 'crash_proj'").fetchone()
        row_run_after = raw_conn2.execute("SELECT status, error_message, completed_at FROM workflow_runs WHERE run_id = 'run_c1'").fetchone()
        
        print(f"[RECOVERY CHECK] Post-recovery task row: status={row_task_after['status']}, completed_at={row_task_after['completed_at']}")
        print(f"[RECOVERY CHECK] Post-recovery agent row: status={row_agent_after['status']}, current_task_id={row_agent_after['current_task_id']}")
        print(f"[RECOVERY CHECK] Post-recovery run row: status={row_run_after['status']}, error_message={row_run_after['error_message']}")
        
        assert row_task_after["status"] == "interrupted", "Task status was not updated to 'interrupted' in SQLite!"
        assert row_task_after["completed_at"] is not None, "Task completed_at timestamp was not updated in SQLite!"
        assert "interrupted" in row_task_after["error_details"].lower(), "Task error_details was not set in SQLite!"
        
        assert row_agent_after["status"] == "idle", "Agent status was not reset to 'idle' in SQLite!"
        assert row_agent_after["current_task_id"] is None, "Agent current_task_id was not cleared to NULL in SQLite!"
        
        assert row_run_after["status"] == "interrupted", "Workflow run was not marked 'interrupted' in SQLite!"
        assert row_run_after["completed_at"] is not None, "Workflow run completed_at was not populated in SQLite!"
        
        # Step 4: Resume workflow and verify raw DB update back to 'running'
        resume_plan = mgr.resume_workflow("run_c1")
        assert resume_plan.recommended_action == "retry_interrupted"
        assert resume_plan.next_task["task_id"] == "task_c1"
        
        row_run_resumed = raw_conn2.execute("SELECT status FROM workflow_runs WHERE run_id = 'run_c1'").fetchone()
        print(f"[RECOVERY CHECK] Post-resumption run row: status={row_run_resumed['status']}")
        assert row_run_resumed["status"] == "running", "Workflow run status was not transitioned to 'running' in SQLite!"
        
        # Step 5: Export / Import verification into fresh database
        backup_file = Path(temp_dir) / "state_backup.json"
        export_summary = mgr.export_state(backup_file)
        assert backup_file.exists()
        
        db_file_imported = Path(temp_dir) / "imported.db"
        mgr_imported = SessionResumptionManager(db_path=db_file_imported, project_id="crash_proj")
        import_summary = mgr_imported.import_state(backup_file, overwrite=True)
        
        raw_conn_imported = sqlite3.connect(str(db_file_imported))
        raw_conn_imported.row_factory = sqlite3.Row
        
        imp_proj = raw_conn_imported.execute("SELECT * FROM projects WHERE project_id = 'crash_proj'").fetchone()
        imp_task = raw_conn_imported.execute("SELECT * FROM task_history WHERE task_id = 'task_c1'").fetchone()
        assert imp_proj is not None, "Project missing in imported SQLite DB"
        assert imp_task is not None, "Task missing in imported SQLite DB"
        assert imp_task["status"] == "interrupted", "Imported task status mismatch"
        print("[RECOVERY CHECK] Export/import cycle faithfully recreated rows in new SQLite database.")
        
        raw_conn2.close()
        raw_conn_imported.close()
        print("[SUCCESS] ALL CRASH RECOVERY AND RESUMPTION CHECKS PASSED EMPIRICALLY.")

if __name__ == "__main__":
    test_recovery_forensics()
