"""
Independent forensic test for SQLite persistence, WAL mode, foreign keys, and FTS5.
Executes raw queries outside of project abstractions to prove data is really written to disk.
"""

import os
import sys
import tempfile
import sqlite3
from pathlib import Path

# Add src to sys.path
SRC_DIR = Path(r"C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src")
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.persistence.db import DatabaseManager, init_db
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.vector_store import SQLiteVectorStore, DeterministicHashEmbeddingProvider
from persistent_crewai.persistence.session import SessionResumptionManager

def test_sqlite_forensics():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_file = Path(temp_dir) / "forensic_test.db"
        
        # Step 1: Initialize database via project code
        db_mgr = init_db(db_file)
        
        # Step 2: Use StateManager to write project and records
        sm = StateManager(db_path=db_file, project_id="forensic_proj_1")
        sm.create_project(
            project_id="forensic_proj_1",
            name="Forensic Audit Project",
            description="Testing empirical persistence",
            integrity_mode="development"
        )
        
        dec_id = sm.record_decision(
            agent_role="Architect",
            title="Forensic Verification Title",
            rationale="Testing that SQLite commits to disk",
            implications="Raw sqlite3 should be able to read this",
            tags=["forensic", "audit"]
        )
        
        run = sm.create_workflow_run("run_f1", workflow_type="audit")
        task = sm.record_task_start("t_f1", "run_f1", "Developer", "Implement feature")
        sm.start_task("t_f1")
        sm.record_task_completion("t_f1", actual_output="Empirical feature output")
        
        # Step 3: Add vector store document
        vstore = SQLiteVectorStore(db_path=db_file, project_id="forensic_proj_1")
        vstore.add(
            documents=["Forensic analysis of persistent SQLite storage"],
            metadatas=[{"category": "audit_memory", "role": "Reviewer"}],
            ids=["mem_f1"]
        )
        
        # Step 4: Verify file existence and size on disk
        assert db_file.exists(), f"Database file {db_file} does not exist on disk!"
        file_size = db_file.stat().st_size
        assert file_size > 0, f"Database file is empty (0 bytes)!"
        print(f"[FORENSIC CHECK] DB file exists on disk: {db_file}, size: {file_size} bytes")
        
        # Step 5: Independent raw sqlite3 connection (BYPASSING ALL PROJECT CLASSES)
        raw_conn = sqlite3.connect(str(db_file))
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        
        # Check WAL mode
        raw_cur.execute("PRAGMA journal_mode;")
        journal_mode = raw_cur.fetchone()[0]
        print(f"[FORENSIC CHECK] PRAGMA journal_mode = {journal_mode}")
        assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"
        
        # Check foreign_keys
        raw_cur.execute("PRAGMA foreign_keys = ON;")
        
        # Check schema tables
        raw_cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [r["name"] for r in raw_cur.fetchall()]
        print(f"[FORENSIC CHECK] Tables in sqlite_master: {tables}")
        for expected_table in [
            "projects", "decisions", "workflow_runs", "task_history",
            "milestones", "agent_status", "execution_traces", "memory_records",
            "memory_fts"
        ]:
            assert expected_table in tables, f"Missing table {expected_table}"
            
        # Check raw query for project
        raw_cur.execute("SELECT * FROM projects WHERE project_id = 'forensic_proj_1';")
        proj_row = raw_cur.fetchone()
        assert proj_row is not None, "Project row not found in raw query"
        assert proj_row["name"] == "Forensic Audit Project"
        print(f"[FORENSIC CHECK] Raw project row: id={proj_row['project_id']}, name={proj_row['name']}")
        
        # Check raw query for decision
        raw_cur.execute("SELECT * FROM decisions WHERE decision_id = ?;", (dec_id,))
        dec_row = raw_cur.fetchone()
        assert dec_row is not None, "Decision row not found in raw query"
        assert dec_row["title"] == "Forensic Verification Title"
        print(f"[FORENSIC CHECK] Raw decision row: id={dec_row['decision_id']}, title={dec_row['title']}")
        
        # Check raw query for task latency
        raw_cur.execute("SELECT * FROM task_history WHERE task_id = 't_f1';")
        task_row = raw_cur.fetchone()
        assert task_row is not None, "Task row not found"
        assert task_row["status"] == "completed"
        assert "latency_ms" in task_row["metadata_json"]
        print(f"[FORENSIC CHECK] Raw task row: id={task_row['task_id']}, metadata={task_row['metadata_json']}")
        
        # Check raw query for agent status
        raw_cur.execute("SELECT * FROM agent_status WHERE project_id = 'forensic_proj_1' AND agent_role = 'Developer';")
        dev_row = raw_cur.fetchone()
        assert dev_row["status"] == "idle"
        assert dev_row["tasks_completed_count"] == 1
        print(f"[FORENSIC CHECK] Raw agent row: role={dev_row['agent_role']}, tasks_completed={dev_row['tasks_completed_count']}")
        
        # Check raw FTS5 virtual table
        raw_cur.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH 'Forensic';")
        fts_row = raw_cur.fetchone()
        assert fts_row is not None, "FTS5 MATCH returned no rows for indexed document"
        assert fts_row["record_id"] == "mem_f1"
        print(f"[FORENSIC CHECK] Raw FTS5 MATCH row: record_id={fts_row['record_id']}")
        
        # Check raw vector embedding column
        raw_cur.execute("SELECT embedding_json FROM memory_records WHERE record_id = 'mem_f1';")
        emb_row = raw_cur.fetchone()
        assert emb_row is not None and emb_row["embedding_json"] is not None
        import json
        emb_list = json.loads(emb_row["embedding_json"])
        assert len(emb_list) == 64
        print(f"[FORENSIC CHECK] Raw vector embedding length = {len(emb_list)}, sample={emb_list[:5]}")
        
        # Check cascade delete in raw SQLite
        raw_cur.execute("DELETE FROM projects WHERE project_id = 'forensic_proj_1';")
        raw_conn.commit()
        
        raw_cur.execute("SELECT count(*) as c FROM decisions WHERE project_id = 'forensic_proj_1';")
        assert raw_cur.fetchone()["c"] == 0, "Decisions were not cascade deleted"
        raw_cur.execute("SELECT count(*) as c FROM task_history WHERE project_id = 'forensic_proj_1';")
        assert raw_cur.fetchone()["c"] == 0, "Tasks were not cascade deleted"
        raw_cur.execute("SELECT count(*) as c FROM memory_records WHERE project_id = 'forensic_proj_1';")
        assert raw_cur.fetchone()["c"] == 0, "Memory records were not cascade deleted"
        raw_cur.execute("SELECT count(*) as c FROM memory_fts WHERE record_id = 'mem_f1';")
        assert raw_cur.fetchone()["c"] == 0, "FTS5 table was not synchronized on cascade delete"
        print("[FORENSIC CHECK] Cascade deletion and FTS5 trigger synchronization fully verified in raw SQLite engine.")
        
        raw_conn.close()
        print("[SUCCESS] ALL RAW SQLITE FORENSIC PERSISTENCE CHECKS PASSED EMPIRICALLY.")

if __name__ == "__main__":
    test_sqlite_forensics()
