"""
Database connection management and schema initialization for Persistent CrewAI.
Supports SQLite WAL mode, foreign key enforcement, busy timeouts, and thread safety.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, Iterator, Sequence, Optional

from persistent_crewai.config import (
    CURRENT_SCHEMA_VERSION,
    get_default_db_path,
)

SCHEMA_DDL = """
-- Schema migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(32) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL
);

-- 1. Projects: Top-level workspace and project configuration
CREATE TABLE IF NOT EXISTS projects (
    project_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    root_path TEXT NOT NULL,
    integrity_mode VARCHAR(32) DEFAULT 'development' CHECK (integrity_mode IN ('development', 'strict', 'production')),
    status VARCHAR(32) DEFAULT 'active' CHECK (status IN ('active', 'archived', 'completed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

-- 2. Decisions: Architectural, design, and technical decisions recorded by agents
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    rationale TEXT NOT NULL,
    implications TEXT NOT NULL,
    status VARCHAR(32) DEFAULT 'accepted' CHECK (status IN ('proposed', 'accepted', 'superseded', 'rejected')),
    tags TEXT DEFAULT '[]',
    superseded_by VARCHAR(64) REFERENCES decisions(decision_id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_decisions_agent ON decisions(agent_role);

-- 3. Workflow Runs: Full pipelines or targeted subtask execution sessions
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    workflow_type VARCHAR(64) NOT NULL,
    trigger_source VARCHAR(64) DEFAULT 'cli' CHECK (trigger_source IN ('cli', 'repl', 'api', 'test')),
    input_prompt TEXT NOT NULL,
    final_output TEXT,
    status VARCHAR(32) DEFAULT 'running' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'interrupted', 'cancelled')),
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON workflow_runs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_started ON workflow_runs(started_at DESC);

-- 4. Task History: Individual agent task execution lifecycle
CREATE TABLE IF NOT EXISTS task_history (
    task_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    expected_output TEXT,
    actual_output TEXT,
    status VARCHAR(32) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'interrupted', 'skipped', 'cancelled')),
    execution_order INTEGER DEFAULT 0,
    error_details TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON task_history(run_id, execution_order);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON task_history(agent_role, status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON task_history(project_id, status);

-- 5. Milestones: Project milestones and progress verification criteria
CREATE TABLE IF NOT EXISTS milestones (
    milestone_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    criteria_json TEXT NOT NULL DEFAULT '[]',
    target_date TIMESTAMP,
    status VARCHAR(32) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'achieved', 'missed')),
    achieved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id, status);

-- 6. Agent Operational Status: Real-time and persisted state of all 8 agents
CREATE TABLE IF NOT EXISTS agent_status (
    agent_role VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    status VARCHAR(32) DEFAULT 'idle' CHECK (status IN ('idle', 'active', 'busy', 'blocked', 'completed', 'error')),
    current_task_id VARCHAR(64) REFERENCES task_history(task_id) ON DELETE SET NULL,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tasks_completed_count INTEGER DEFAULT 0,
    last_error TEXT,
    metadata_json TEXT DEFAULT '{}',
    PRIMARY KEY (agent_role, project_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_status_project ON agent_status(project_id, status);

-- 7. Execution Traces: Granular thought and tool event stream
CREATE TABLE IF NOT EXISTS execution_traces (
    trace_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    task_id VARCHAR(64) REFERENCES task_history(task_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    tool_name VARCHAR(128),
    payload TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_traces_run ON execution_traces(run_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_traces_task ON execution_traces(task_id);

-- 8. Memory Records: Persistent semantic memories and contextual snippets
CREATE TABLE IF NOT EXISTS memory_records (
    record_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64),
    category VARCHAR(64) DEFAULT 'general',
    content TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    embedding_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_records(project_id, category);
CREATE INDEX IF NOT EXISTS idx_memory_agent ON memory_records(agent_role);

-- Full-Text Search (FTS5) Virtual Table for hybrid memory querying
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

-- Trigger 1: Synchronize memory_fts on insert into memory_records
CREATE TRIGGER IF NOT EXISTS trg_memory_insert AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;

-- Trigger 2: Synchronize memory_fts on delete from memory_records
CREATE TRIGGER IF NOT EXISTS trg_memory_delete AFTER DELETE ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
END;

-- Trigger 3: Synchronize memory_fts on update of memory_records
CREATE TRIGGER IF NOT EXISTS trg_memory_update AFTER UPDATE ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;
"""


def get_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """Create a configured SQLite connection with WAL mode and pragmas.
    
    Args:
        db_path: Path to database file or None for default.
        
    Returns:
        Configured sqlite3.Connection object.
    """
    manager = DatabaseManager(db_path=db_path)
    return manager.create_connection()


def init_db(db_path: Optional[str | Path] = None) -> DatabaseManager:
    """Initialize database schema, tables, triggers, and migrations.
    
    Args:
        db_path: Path to database file or None for default.
        
    Returns:
        DatabaseManager instance initialized with full schema.
    """
    manager = DatabaseManager(db_path=db_path)
    manager.initialize_schema()
    return manager


class DetachedCursor:
    """
    A query and execution result object detached from a closed database connection.
    Implements standard sqlite3.Cursor query inspection methods (fetchall, fetchone, fetchmany,
    rowcount, lastrowid, description, iteration, context manager) without raising
    ProgrammingError due to connection closure.
    """

    def __init__(
        self,
        rows: list[sqlite3.Row],
        rowcount: int,
        lastrowid: int | None,
        description: Any,
    ) -> None:
        self._rows = rows
        self._iter = iter(rows)
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = description

    def fetchall(self) -> list[sqlite3.Row]:
        """Fetch all remaining rows."""
        return list(self._iter)

    def fetchone(self) -> sqlite3.Row | None:
        """Fetch the next row or None."""
        try:
            return next(self._iter)
        except StopIteration:
            return None

    def fetchmany(self, size: int | None = None) -> list[sqlite3.Row]:
        """Fetch up to size remaining rows."""
        if size is None:
            size = 1
        res: list[sqlite3.Row] = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            res.append(row)
        return res

    def __iter__(self) -> Iterator[sqlite3.Row]:
        return iter(self._rows)

    def __enter__(self) -> "DetachedCursor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def close(self) -> None:
        """No-op close method for sqlite3.Cursor compatibility."""
        pass


class DatabaseManager:
    """
    Manages SQLite connections with WAL mode, foreign key validation,
    busy timeout protection, and automatic schema migrations.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            self.db_path = get_default_db_path()
        else:
            self.db_path = Path(db_path).resolve() if str(db_path) != ":memory:" else Path(":memory:")

        self._ensure_parent_dir()
        self._lock = threading.RLock()
        self._is_memory = str(self.db_path) == ":memory:"
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._mem_conn = self._create_raw_connection()


    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory for database file exists."""
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _create_raw_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row

        # Execute mandatory pragmas
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.execute("PRAGMA encoding = 'UTF-8';")
        cursor.close()
        return conn

    def create_connection(self) -> sqlite3.Connection:
        """
        Create a new SQLite connection with mandatory pragmas configured.
        """
        if self._is_memory and self._mem_conn is not None:
            return self._mem_conn
        return self._create_raw_connection()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for transactional database operations.
        Commits on clean exit; rolls back on exception; ensures connection closure.
        """
        if self._is_memory:
            with self._lock:
                try:
                    yield self._mem_conn
                    self._mem_conn.commit()
                except Exception:
                    self._mem_conn.rollback()
                    raise
        else:
            conn = self.create_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Convenience context manager yielding an active cursor."""
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> DetachedCursor:
        """Execute a single statement and commit immediately, returning a safe detached cursor."""
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall() if cur.description is not None else []
            return DetachedCursor(rows, cur.rowcount, cur.lastrowid, cur.description)

    def executemany(
        self, sql: str, seq_of_params: Iterable[Sequence[Any] | dict[str, Any]]
    ) -> DetachedCursor:
        """Execute a statement against multiple parameter sets in a single transaction, returning a detached cursor."""
        with self.connection() as conn:
            cur = conn.executemany(sql, seq_of_params)
            rows = cur.fetchall() if cur.description is not None else []
            return DetachedCursor(rows, cur.rowcount, cur.lastrowid, cur.description)

    def fetchone(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        """Execute query and fetch a single record as a dictionary."""
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        """Execute query and fetch all records as dictionaries."""
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def initialize_schema(self) -> None:
        """Initialize all 8 tables, indexes, FTS5 virtual table, and triggers."""
        with self._lock:
            with self.connection() as conn:
                conn.executescript(SCHEMA_DDL)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO schema_migrations (version, description)
                    VALUES (?, ?)
                    """,
                    (CURRENT_SCHEMA_VERSION, "Milestone 1 initial 8-table relational schema with FTS5"),
                )

    def get_schema_version(self) -> str | None:
        """Retrieve highest recorded schema version."""
        row = self.fetchone("SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1")
        return row["version"] if row else None

    def reset_database(self) -> None:
        """
        Drop all tables and vacuum database. Used for test isolation.
        """
        if str(self.db_path) == ":memory:":
            return

        with self._lock:
            with self.connection() as conn:
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys = OFF;")
                cur.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%';"
                )
                entries = cur.fetchall()
                for entry in entries:
                    name = entry["name"]
                    etype = entry["type"]
                    cur.execute(f"DROP {etype.upper()} IF EXISTS {name};")
                cur.execute("PRAGMA foreign_keys = ON;")
            self.initialize_schema()
