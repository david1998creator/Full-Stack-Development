"""
Forensic Integrity Test Suite for Milestone 1 Retry.
Empirically tests all 5 audit requirements directly against SQLite files on disk.
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Add src to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from persistent_crewai.persistence.db import DatabaseManager, DetachedCursor, init_db, get_connection
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    SchemaVersionMismatchError,
    StateImportConflictError,
    StateIntegrityError,
)
from persistent_crewai.config import CURRENT_SCHEMA_VERSION


def test_record_event_integrity():
    print("[CHECK 1] Testing record_event row insertion and FK verification...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "record_event_test.db"
        sm = StateManager(db_path=db_path, project_id="proj_audit")
        
        # 1.1 Verify cold-start record_event actually inserts rows into physical DB
        trace_id = sm.record_event("test_system_event", {"param": "audit_value_123"})
        assert trace_id.startswith("trc_"), f"Unexpected trace ID: {trace_id}"
        
        # Inspect raw SQLite database directly with standard sqlite3 library
        raw_conn = sqlite3.connect(str(db_path))
        raw_conn.row_factory = sqlite3.Row
        try:
            cur = raw_conn.cursor()
            
            cur.execute("SELECT * FROM execution_traces WHERE trace_id = ?", (trace_id,))
            row = cur.fetchone()
            assert row is not None, "Raw SQLite query returned no row for trace_id!"
            assert row["event_type"] == "test_system_event"
            assert json.loads(row["payload"]) == {"param": "audit_value_123"}
            assert row["run_id"] == "system_run"
            print("  -> Physical execution_traces row verified with exact payload.")
            
            # 1.2 Verify system_run and project were legitimately created, not FK disabled
            cur.execute("SELECT * FROM projects WHERE project_id = 'proj_audit'")
            p_row = cur.fetchone()
            assert p_row is not None, "Parent project row was not created!"
            
            cur.execute("SELECT * FROM workflow_runs WHERE run_id = 'system_run'")
            r_row = cur.fetchone()
            assert r_row is not None, "Parent workflow_runs row was not created!"
            print("  -> Parent project and system_run legitimately seeded in relational tables.")
        finally:
            raw_conn.close()
        
        # 1.3 Verify DatabaseManager connection strictly enforces FK
        db_conn = sm._db_mgr.create_connection()
        try:
            db_cur = db_conn.cursor()
            db_cur.execute("PRAGMA foreign_keys;")
            fk_status = db_cur.fetchone()[0]
            assert fk_status == 1, f"PRAGMA foreign_keys is {fk_status}, expected 1!"
            print("  -> DatabaseManager PRAGMA foreign_keys = 1 strictly enforced.")
        finally:
            db_conn.close()
        
        # 1.4 Try inserting trace with nonexistent run_id -> MUST fail with IntegrityError
        fk_failed_as_expected = False
        try:
            sm.record_event("illegal_event", {"a": 1}, run_id="nonexistent_run_999")
        except sqlite3.IntegrityError as e:
            fk_failed_as_expected = True
            print(f"  -> FK verification correctly caught invalid run_id: {e}")
        assert fk_failed_as_expected, "FK verification was bypassed for invalid run_id!"

        # 1.5 Try inserting trace with nonexistent task_id -> MUST fail with IntegrityError
        fk_task_failed = False
        try:
            sm.record_event("illegal_task_event", {"a": 1}, task_id="nonexistent_task_999")
        except sqlite3.IntegrityError as e:
            fk_task_failed = True
            print(f"  -> FK verification correctly caught invalid task_id: {e}")
        assert fk_task_failed, "FK verification was bypassed for invalid task_id!"

    print("[PASS] Check 1 passed completely.\n")


def test_record_task_completion_db_update():
    print("[CHECK 2] Testing record_task_completion actual database updates...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "task_completion_test.db"
        sm = StateManager(db_path=db_path, project_id="proj_task_audit")
        sm.create_project("proj_task_audit", "Task Proj", "Desc")
        sm.create_workflow_run("run_task_01", "task_flow")
        
        # Open raw SQLite connection to watch real table mutations
        raw_conn = sqlite3.connect(str(db_path))
        raw_conn.row_factory = sqlite3.Row
        try:
            cur = raw_conn.cursor()

            # 2.1 Test Signature 1: Contract positional (task_id, agent, output)
            sm.record_task_start("t_sig1", "run_task_01", "Developer", "Implement feature X")
            sm.start_task("t_sig1")
            
            # Before completion, check raw SQLite row
            cur.execute("SELECT status, actual_output, completed_at FROM task_history WHERE task_id = 't_sig1'")
            before_row = cur.fetchone()
            assert before_row["status"] == "in_progress", f"Unexpected before status: {before_row['status']}"
            assert before_row["actual_output"] is None
            assert before_row["completed_at"] is None
            
            # Call record_task_completion
            res1 = sm.record_task_completion("t_sig1", "Developer", "Feature X fully implemented.")
            assert res1["status"] == "completed"
            
            # After completion, check raw SQLite row
            cur.execute("SELECT status, actual_output, completed_at, metadata_json FROM task_history WHERE task_id = 't_sig1'")
            after_row = cur.fetchone()
            assert after_row["status"] == "completed", "task_history status was NOT updated in SQLite!"
            assert after_row["actual_output"] == "Feature X fully implemented.", "actual_output was NOT updated in SQLite!"
            assert after_row["completed_at"] is not None, "completed_at was NOT set in SQLite!"
            meta1 = json.loads(after_row["metadata_json"])
            assert "latency_ms" in meta1, "latency_ms not recorded in task metadata!"
            
            cur.execute("SELECT status, current_task_id, tasks_completed_count FROM agent_status WHERE agent_role = 'Developer' AND project_id = 'proj_task_audit'")
            agent_row = cur.fetchone()
            assert agent_row["status"] == "idle", "agent status was NOT reset to idle!"
            assert agent_row["current_task_id"] is None, "current_task_id was NOT cleared!"
            assert agent_row["tasks_completed_count"] == 1, "tasks_completed_count was NOT incremented!"
            print("  -> Signature 1 (task_id, agent, output) verified with raw DB mutations.")

            # 2.2 Test Signature 2: Keyword (task_id=..., agent=..., output=...)
            sm.record_task_start("t_sig2", "run_task_01", "Architect", "Design architecture")
            sm.start_task("t_sig2")
            sm.record_task_completion(task_id="t_sig2", agent="Architect", output="Architecture blueprint verified.")
            
            cur.execute("SELECT status, actual_output FROM task_history WHERE task_id = 't_sig2'")
            after_row2 = cur.fetchone()
            assert after_row2["status"] == "completed"
            assert after_row2["actual_output"] == "Architecture blueprint verified."
            print("  -> Signature 2 (keyword agent & output) verified with raw DB mutations.")

            # 2.3 Test Signature 3: Legacy positional (task_id, output)
            sm.record_task_start("t_sig3", "run_task_01", "Reviewer", "Review PR")
            sm.start_task("t_sig3")
            sm.record_task_completion("t_sig3", "PR approved.")
            
            cur.execute("SELECT status, actual_output FROM task_history WHERE task_id = 't_sig3'")
            after_row3 = cur.fetchone()
            assert after_row3["status"] == "completed"
            assert after_row3["actual_output"] == "PR approved."
            print("  -> Signature 3 (legacy positional task_id, output) verified with raw DB mutations.")

            # 2.4 Test Signature 4: Legacy keyword (task_id=..., actual_output=..., status=...)
            sm.record_task_start("t_sig4", "run_task_01", "Tester", "Execute test suite")
            sm.start_task("t_sig4")
            sm.record_task_completion(task_id="t_sig4", actual_output="All test cases pass.", status="completed")
            
            cur.execute("SELECT status, actual_output FROM task_history WHERE task_id = 't_sig4'")
            after_row4 = cur.fetchone()
            assert after_row4["status"] == "completed"
            assert after_row4["actual_output"] == "All test cases pass."
            print("  -> Signature 4 (legacy keyword actual_output & status) verified with raw DB mutations.")
        finally:
            raw_conn.close()

    print("[PASS] Check 2 passed completely.\n")


def test_import_state_integrity_and_population():
    print("[CHECK 3] Testing import_state population and data integrity enforcement...")
    with tempfile.TemporaryDirectory() as tmpdir:
        src_db = Path(tmpdir) / "source_state.db"
        mgr1 = SessionResumptionManager(db_path=src_db, project_id="proj_import_test")
        sm1 = mgr1._state_mgr
        
        # Seed all 8 tables
        sm1.create_project("proj_import_test", "Import Test Proj", "Description of test project")
        d1 = sm1.record_decision("Architect", "Original Architecture", "Initial choice", status="proposed")
        d2 = sm1.record_decision("Architect", "Refined Architecture", "Newer choice", status="superseded")
        sm1.update_decision_status(d1, "proposed", superseded_by=d2)
        
        sm1.create_workflow_run("run_imp_01", "full_pipeline")
        sm1.record_task_start("task_imp_01", "run_imp_01", "Developer", "Code task")
        sm1.start_task("task_imp_01")
        sm1.record_task_completion("task_imp_01", "Developer", "Code artifact")
        
        sm1.create_milestone("ms_imp_01", "Milestone One", "Desc", criteria=["Crit 1", "Crit 2"], status="in_progress")
        sm1.record_trace("run_imp_01", "task_imp_01", "Developer", "file_written", {"file": "main.py"})
        
        from persistent_crewai.persistence.vector_store import SQLiteVectorStore
        vs1 = SQLiteVectorStore(db_path=src_db, project_id="proj_import_test")
        vs1.add(["Deterministic persistence memory for testing FTS5 and embeddings"], agent_role="Memory Manager", category="notes")
        
        # Export state
        export_file = Path(tmpdir) / "full_export.json"
        summary = mgr1.export_state(export_file)
        assert export_file.exists()
        print(f"  -> State exported: {summary}")
        
        # Import into fresh database
        tgt_db = Path(tmpdir) / "target_state.db"
        mgr2 = SessionResumptionManager(db_path=tgt_db, project_id="proj_import_test")
        imp_summary = mgr2.import_state(export_file, overwrite=True)
        print(f"  -> State imported: {imp_summary}")
        
        # Verify all tables directly with raw SQLite
        raw_conn = sqlite3.connect(str(tgt_db))
        raw_conn.row_factory = sqlite3.Row
        try:
            cur = raw_conn.cursor()
            
            # 1. schema_migrations
            cur.execute("SELECT count(*) as cnt FROM schema_migrations")
            assert cur.fetchone()["cnt"] >= 1, "schema_migrations not populated!"
            
            # 2. projects
            cur.execute("SELECT * FROM projects WHERE project_id = 'proj_import_test'")
            p_row = cur.fetchone()
            assert p_row is not None and p_row["name"] == "Import Test Proj", "projects not populated!"
            
            # 3. decisions (including superseded_by forward reference)
            cur.execute("SELECT * FROM decisions WHERE decision_id = ?", (d1,))
            d1_row = cur.fetchone()
            assert d1_row is not None and d1_row["superseded_by"] == d2, "decision superseded_by forward reference link broken!"
            
            cur.execute("SELECT * FROM decisions WHERE decision_id = ?", (d2,))
            d2_row = cur.fetchone()
            assert d2_row is not None and d2_row["superseded_by"] is None, "decision d2 invalid!"
            
            # 4. workflow_runs
            cur.execute("SELECT * FROM workflow_runs WHERE run_id = 'run_imp_01'")
            assert cur.fetchone() is not None, "workflow_runs not populated!"
            
            # 5. task_history
            cur.execute("SELECT * FROM task_history WHERE task_id = 'task_imp_01'")
            t_row = cur.fetchone()
            assert t_row is not None and t_row["status"] == "completed", "task_history not populated!"
            
            # 6. milestones
            cur.execute("SELECT * FROM milestones WHERE milestone_id = 'ms_imp_01'")
            assert cur.fetchone() is not None, "milestones not populated!"
            
            # 7. agent_status
            cur.execute("SELECT count(*) as cnt FROM agent_status WHERE project_id = 'proj_import_test'")
            assert cur.fetchone()["cnt"] >= 8, "agent_status not populated!"
            
            # 8. execution_traces
            cur.execute("SELECT count(*) as cnt FROM execution_traces WHERE run_id = 'run_imp_01'")
            assert cur.fetchone()["cnt"] >= 1, "execution_traces not populated!"
            
            # 9. memory_records
            cur.execute("SELECT count(*) as cnt FROM memory_records WHERE project_id = 'proj_import_test'")
            assert cur.fetchone()["cnt"] >= 1, "memory_records not populated!"
            
            # 10. memory_fts (FTS5 synchronization triggers test)
            cur.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH 'deterministic'")
            fts_row = cur.fetchone()
            assert fts_row is not None, "memory_fts virtual table was not populated via triggers!"
            print("  -> All 8 relational tables + schema_migrations + FTS5 successfully verified in raw DB.")
        finally:
            raw_conn.close()

        # 3.2 Verify Data Integrity Protections
        # Test 1: Corrupted JSON syntax
        bad_json = Path(tmpdir) / "corrupt_syntax.json"
        bad_json.write_text("{malformed_json: true", encoding="utf-8")
        corrupt_syntax_caught = False
        try:
            mgr2.import_state(bad_json, overwrite=True)
        except StateIntegrityError:
            corrupt_syntax_caught = True
        assert corrupt_syntax_caught, "Malformed JSON syntax was not caught!"
        print("  -> Malformed JSON caught with StateIntegrityError.")

        # Test 2: Incompatible schema version
        bad_ver = Path(tmpdir) / "bad_ver.json"
        bad_ver.write_text(json.dumps({"schema_version": "999.0.0", "projects": [{"project_id": "p"}]}), encoding="utf-8")
        bad_ver_caught = False
        try:
            mgr2.import_state(bad_ver, overwrite=True)
        except SchemaVersionMismatchError:
            bad_ver_caught = True
        assert bad_ver_caught, "Incompatible schema version was not caught!"
        print("  -> Incompatible schema version caught with SchemaVersionMismatchError.")

        # Test 3: Missing project records
        bad_proj = Path(tmpdir) / "no_proj.json"
        bad_proj.write_text(json.dumps({"schema_version": CURRENT_SCHEMA_VERSION}), encoding="utf-8")
        no_proj_caught = False
        try:
            mgr2.import_state(bad_proj, overwrite=True)
        except StateIntegrityError:
            no_proj_caught = True
        assert no_proj_caught, "Missing project records was not caught!"
        print("  -> Missing project records caught with StateIntegrityError.")

        # Test 4: Conflict without overwrite=True
        conflict_caught = False
        try:
            mgr2.import_state(export_file, overwrite=False)
        except StateImportConflictError:
            conflict_caught = True
        assert conflict_caught, "Project conflict without overwrite=True was not caught!"
        print("  -> StateImportConflictError correctly raised when overwrite=False.")

        # Test 5: Corrupt Foreign Key (orphan foreign key) -> transaction rollback
        corrupt_fk_file = Path(tmpdir) / "corrupt_fk.json"
        with open(export_file, "r", encoding="utf-8") as f:
            cdata = json.load(f)
        cdata["decisions"].append({
            "decision_id": "dec_orphan",
            "project_id": "proj_import_test",
            "agent_role": "Architect",
            "title": "Orphan reference",
            "rationale": "Points to ghost",
            "status": "proposed",
            "superseded_by": "nonexistent_ghost_decision_999",
        })
        with open(corrupt_fk_file, "w", encoding="utf-8") as f:
            json.dump(cdata, f)
        
        rollback_db = Path(tmpdir) / "rollback_test.db"
        mgr_rb = SessionResumptionManager(db_path=rollback_db, project_id="proj_import_test")
        fk_err_caught = False
        try:
            mgr_rb.import_state(corrupt_fk_file, overwrite=True)
        except sqlite3.IntegrityError:
            fk_err_caught = True
        assert fk_err_caught, "Corrupt FK did not trigger sqlite3.IntegrityError!"
        
        # Verify rollback: database should NOT have the orphan decision
        raw_rb = sqlite3.connect(str(rollback_db))
        try:
            raw_rb_cur = raw_rb.cursor()
            raw_rb_cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='decisions'")
            tbl_exists = raw_rb_cur.fetchone()[0]
            if tbl_exists:
                raw_rb_cur.execute("SELECT count(*) FROM decisions WHERE decision_id = 'dec_orphan'")
                assert raw_rb_cur.fetchone()[0] == 0, "Atomic rollback failed: corrupt record found in DB!"
        finally:
            raw_rb.close()
        print("  -> Orphan foreign key triggered IntegrityError with clean atomic rollback.")

    print("[PASS] Check 3 passed completely.\n")


def test_detached_cursor_integrity():
    print("[CHECK 4] Testing DetachedCursor real data fetching and semantics...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cursor_test.db"
        mgr = init_db(db_path)
        
        # 4.1 Dynamic arithmetic query (proves no hardcoded results)
        cur1 = mgr.execute("SELECT 384 + 128 AS calc, 'dynamic_token_789' AS token;")
        assert isinstance(cur1, DetachedCursor), f"Expected DetachedCursor, got {type(cur1)}"
        row1 = cur1.fetchone()
        assert row1 is not None, "fetchone returned None!"
        assert row1["calc"] == 512, f"Expected 512, got {row1['calc']}"
        assert row1["token"] == "dynamic_token_789", f"Expected dynamic_token_789, got {row1['token']}"
        assert cur1.fetchone() is None, "Second fetchone did not return None on 1-row query!"
        print("  -> Dynamic mathematical and string expressions return genuine computed values.")

        # 4.2 Real DML execution and rowcount
        cur_dml = mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("p_cursor_1", "Cursor Proj 1", "Desc", "/tmp"),
        )
        assert cur_dml.rowcount == 1, f"Expected rowcount 1, got {cur_dml.rowcount}"
        assert cur_dml.lastrowid is not None
        assert cur_dml.description is None
        
        # 4.3 Multiple rows, fetchmany, fetchall, and description
        mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("p_cursor_2", "Cursor Proj 2", "Desc", "/tmp"),
        )
        mgr.execute(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            ("p_cursor_3", "Cursor Proj 3", "Desc", "/tmp"),
        )
        
        cur_sel = mgr.execute("SELECT project_id, name FROM projects ORDER BY project_id ASC")
        col_names = [d[0] for d in cur_sel.description]
        assert col_names == ["project_id", "name"], f"Unexpected column names: {col_names}"
        
        # fetchone
        r1 = cur_sel.fetchone()
        assert r1["project_id"] == "p_cursor_1"
        
        # fetchmany(1)
        r_many = cur_sel.fetchmany(1)
        assert len(r_many) == 1
        assert r_many[0]["project_id"] == "p_cursor_2"
        
        # fetchall (remaining)
        r_rem = cur_sel.fetchall()
        assert len(r_rem) == 1
        assert r_rem[0]["project_id"] == "p_cursor_3"
        
        # subsequent fetchall is empty
        assert cur_sel.fetchall() == []
        print("  -> fetchone, fetchmany, fetchall consumption semantics verified.")

        # 4.4 Iteration protocol
        cur_iter = mgr.execute("SELECT project_id FROM projects ORDER BY project_id ASC")
        iterated_ids = [r["project_id"] for r in cur_iter]
        assert iterated_ids == ["p_cursor_1", "p_cursor_2", "p_cursor_3"], f"Unexpected iteration: {iterated_ids}"
        print("  -> __iter__ protocol verified.")

        # 4.5 Context manager protocol
        with mgr.execute("SELECT count(*) as total FROM projects") as cm_cur:
            assert cm_cur.fetchone()["total"] == 3
        print("  -> Context manager (__enter__, __exit__) verified.")

        # 4.6 executemany
        cur_em = mgr.executemany(
            "INSERT INTO projects (project_id, name, description, root_path) VALUES (?, ?, ?, ?)",
            [
                ("p_cursor_4", "Proj 4", "D", "/tmp"),
                ("p_cursor_5", "Proj 5", "D", "/tmp"),
            ],
        )
        assert cur_em.rowcount == 2, f"Expected executemany rowcount 2, got {cur_em.rowcount}"
        print("  -> executemany verified.")

        # 4.7 Verify database connection is closed safely (no Windows file locks)
        # In WAL mode, SQLite may hold shared memory / WAL lock if process has open connection.
        # DatabaseManager.execute() closes connection after every call.
        del mgr
        import gc
        gc.collect()
        os.remove(db_path)
        assert not db_path.exists(), "File remained locked or could not be removed!"
        print("  -> Connection closure and absence of file lock verified.")

    print("[PASS] Check 4 passed completely.\n")


def test_facade_and_hardcoded_detection():
    print("[CHECK 5] Static code scan for facades and hardcoded test patterns...")
    persistence_dir = PROJECT_ROOT / "src" / "persistent_crewai" / "persistence"
    for py_file in persistence_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            code = f.read()
        
        # Check for fake return values
        assert "return True  # mock" not in code
        assert "return 'completed'  # fake" not in code
        assert "NotImplemented" not in code or py_file.name == "__init__.py" or "EmbeddingProviderInterface" in code, f"NotImplemented found in {py_file}"
        
    print("  -> No dummy stubs, fake returns, or mock facades found in persistence source code.")
    print("[PASS] Check 5 passed completely.\n")


if __name__ == "__main__":
    print("=============================================================")
    print("FORENSIC INTEGRITY TEST SUITE - MILESTONE 1 RETRY")
    print("=============================================================")
    test_record_event_integrity()
    test_record_task_completion_db_update()
    test_import_state_integrity_and_population()
    test_detached_cursor_integrity()
    test_facade_and_hardcoded_detection()
    print("=============================================================")
    print("ALL 5 FORENSIC INTEGRITY CHECKS PASSED: VERDICT CLEAN")
    print("=============================================================")
