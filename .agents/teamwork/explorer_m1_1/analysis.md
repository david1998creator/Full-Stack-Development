# Milestone 1 Technical Analysis: Packaging & SQLite Database Schema

**Explorer**: M1-1 (Milestone 1 Explorer: Packaging & SQLite Database Schema)  
**Date**: 2026-10-13  
**Target Platform**: Python 3.13.1 on Windows 11 x64  
**Project Workspace**: `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team`  

---

## 1. Executive Summary

Milestone 1 establishes the foundational infrastructure for the Persistent CrewAI 8-Agent Software Engineering Framework. This analysis provides the technical blueprint and reference code for:
1. **Modern Python Packaging**: PEP 517/621-compliant `pyproject.toml`, `requirements.txt`, and `requirements-dev.txt` tailored to Python 3.13 on Windows x64.
2. **Deterministic Virtual Environment Plan (`.venv`)**: Strict alignment with the `managing-python-dependencies` skill, using Windows-specific executable paths and zero-dependency offline resilience.
3. **High-Performance SQLite Connection Management (`db.py`)**: Thread-safe connection context manager with Write-Ahead Logging (`WAL`), immediate foreign key validation, busy timeout protection (5000ms), and automated schema migrations.
4. **Complete 8-Table DDL Schema & FTS5 Triggers**: Exhaustive relational DDL for projects, decisions, workflow runs, task history, milestones, agent operational status, execution traces, and memory records, with automated FTS5 synchronization triggers (`INSERT`, `DELETE`, `UPDATE`) and B-tree indexes.

---

## 2. Packaging Specification

### 2.1 Host Environment Assessment
- **Python Version**: `Python 3.13.1` (64-bit Windows executable located at `C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe` / `C:\Python313\python.exe`).
- **Standard Library Capabilities**: `sqlite3` (v3.45.3) includes native FTS5, JSON support, and WAL mode. `venv` and `unittest` are present and functional.
- **Setuptools**: `setuptools 75.8.0` is present.
- **Pip**: `pip 25.0` is present.
- **Wheel Resolution**: A dry-run installation test confirmed that `crewai 1.15.27` and all its transitive dependencies (`chromadb 1.5.9`, `lancedb 0.30.0`, `onnxruntime 1.31.0`, `pyarrow 26.0.0`, `tokenizers 0.23.3`) possess pre-compiled `cp39-abi3` or `cp313` Windows x64 wheels. No MSVC C++ compiler toolchains are required.

### 2.2 `pyproject.toml`
The package configuration uses `setuptools` with `src`-layout discovery and CLI entry point binding.

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "persistent-crewai"
version = "0.1.0"
description = "Persistent multi-agent software engineering framework using CrewAI with state memory and GitHub coordination"
readme = "README.md"
requires-python = ">=3.11"
authors = [
    { name = "Engineering Team", email = "crewai-team@local.domain" }
]
keywords = ["crewai", "multi-agent", "persistence", "sqlite", "github-automation", "state-machine"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: Microsoft :: Windows",
    "Topic :: Software Development :: Build Tools",
]
dependencies = [
    "crewai>=1.15.0,<2.0.0",
    "pydantic>=2.0.0,<3.0.0",
    "rich>=13.0.0",
    "click>=8.0.0",
    "python-dotenv>=1.0.0",
    "requests>=2.31.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.0.0",
    "pytest-mock>=3.12.0",
]
live = [
    "openai>=1.0.0",
    "anthropic>=0.30.0",
    "google-genai>=1.0.0",
]

[project.scripts]
crewai-team = "persistent_crewai.cli.main:cli"

[tool.setuptools.packages.find]
where = ["src"]
include = ["persistent_crewai*"]

[tool.setuptools.package-data]
"*" = ["*.sql", "*.json", "*.md"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --strict-markers"
markers = [
    "unit: fast unit tests",
    "integration: multi-component integration tests",
    "e2e: end-to-end multi-agent scenario tests",
]
```

### 2.3 `requirements.txt`
Specifies direct runtime dependencies:

```text
# Core Agent Framework
crewai>=1.15.0,<2.0.0
pydantic>=2.0.0,<3.0.0

# CLI, Styling & Config
rich>=13.0.0
click>=8.0.0
python-dotenv>=1.0.0

# Network & Adapters
requests>=2.31.0
```

### 2.4 `requirements-dev.txt`
Extends `requirements.txt` with testing harnesses:

```text
-r requirements.txt
pytest>=8.0.0
pytest-cov>=4.0.0
pytest-mock>=3.12.0
```

---

## 3. Virtual Environment Setup & Installation Plan

### 3.1 Compliance with `managing-python-dependencies`
1. **Rule**: Global `pip install` is strictly prohibited.
2. **Detection**: The project directory currently contains no `uv.lock`, `pyproject.toml`, or `Pipfile`. In accordance with Signal 6, the default toolchain is **`venv + pip`**.
3. **Windows Binary Path Discipline**: Commands must invoke `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\pip.exe` directly.

### 3.2 Step-by-Step Execution Plan

```powershell
# 1. Navigate to project root
cd C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team

# 2. Create isolated virtual environment
python -m venv .venv

# 3. Upgrade pip within the virtual environment
.\.venv\Scripts\python.exe -m pip install --upgrade pip

# 4. Install test and runtime dependencies
.\.venv\Scripts\pip.exe install -r requirements-dev.txt

# 5. Install persistent-crewai in editable development mode
.\.venv\Scripts\pip.exe install -e .

# 6. Verify environment sanity
.\.venv\Scripts\python.exe -c "import persistent_crewai; print('persistent_crewai loaded from:', persistent_crewai.__file__)"
.\.venv\Scripts\python.exe -m pytest --version
.\.venv\Scripts\crewai-team.exe --help
```

### 3.3 Zero-Dependency Offline Resilience
A key requirement is that all persistence, mock LLM, and mock GitHub operations can execute with zero external internet dependencies and zero external API keys.
- Python 3.13 standard library (`sqlite3`, `unittest`, `dataclasses`, `hashlib`, `json`, `pathlib`, `typing`) provides 100% of the functionality needed for the persistence engine (`db.py`, `state_manager.py`, `vector_store.py`, `session.py`).
- Therefore, even before `pip install` completes or in offline test environments, tests can run via:
  ```powershell
  python -m unittest discover -s tests
  ```

---

## 4. SQLite Connection Management Architecture (`db.py`)

### 4.1 Pragmas and Concurrency Invariants
SQLite in default `DELETE` journal mode locks the entire database file during writes, causing `sqlite3.OperationalError: database is locked` when multiple agents or threads read and write concurrently.
To guarantee fault-tolerant concurrency:
1. `PRAGMA journal_mode = WAL;`: Write-Ahead Logging allows readers to proceed concurrently without blocking writers, and writers do not block readers.
2. `PRAGMA foreign_keys = ON;`: Enforces referential integrity on every connection (default is OFF in SQLite).
3. `PRAGMA synchronous = NORMAL;`: Provides durability while eliminating unnecessary fsync calls in WAL mode.
4. `PRAGMA busy_timeout = 5000;`: Blocks up to 5,000 milliseconds when waiting for locks before raising an exception.
5. `PRAGMA encoding = 'UTF-8';`: Guarantees consistent text encoding across systems.

### 4.2 Connection Protocol & Thread Safety
In Python's `sqlite3`, a connection created in one thread should not be used concurrently in another thread without serialization.
`DatabaseManager` implements:
- A transactional context manager:
  - Automatically commits transactions upon clean exit.
  - Automatically rolls back transactions on any exception.
  - Always closes or releases connections cleanly in a `finally` block.
- `row_factory = sqlite3.Row` to allow dictionary-like and index-based access to query results.
- Optional thread-local connection caching (`threading.local`) for high-throughput single-threaded or thread-isolated workflows.

### 4.3 Proposed Implementation for `src/persistent_crewai/persistence/db.py`

```python
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
from typing import Any, Generator, Iterable, Sequence

# Default database location within the project workspace
DEFAULT_DB_DIR = Path(".persistent_crewai")
DEFAULT_DB_NAME = "state.db"
CURRENT_SCHEMA_VERSION = "1.0.0"


def get_default_db_path() -> Path:
    """Resolve database path from environment variable or workspace default."""
    env_path = os.environ.get("CREWAI_STATE_DB_PATH")
    if env_path:
        return Path(env_path).resolve()
    return (DEFAULT_DB_DIR / DEFAULT_DB_NAME).resolve()


class DatabaseManager:
    """
    Manages SQLite connections with WAL mode, foreign key validation,
    and automatic schema migrations.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            self.db_path = get_default_db_path()
        else:
            self.db_path = Path(db_path).resolve()

        self._ensure_parent_dir()
        self._local = threading.local()

    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory for database file exists."""
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def create_connection(self) -> sqlite3.Connection:
        """
        Create a new SQLite connection with mandatory pragmas configured.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row

        # Execute mandatory pragmas
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.execute("PRAGMA encoding = 'UTF-8';")
        cursor.close()

        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for transactional database operations.
        Commits on clean exit; rolls back on exception; ensures connection closure.
        """
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

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        """Execute a single statement and commit immediately."""
        with self.connection() as conn:
            return conn.execute(sql, params)

    def executemany(
        self, sql: str, seq_of_params: Iterable[Sequence[Any] | dict[str, Any]]
    ) -> sqlite3.Cursor:
        """Execute a statement against multiple parameter sets in a single transaction."""
        with self.connection() as conn:
            return conn.executemany(sql, seq_of_params)

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
        with self.connection() as conn:
            conn.executescript(SCHEMA_DDL)
            # Record initial migration
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

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys = OFF;")
            # Fetch all tables
            cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%';")
            entries = cur.fetchall()
            for entry in entries:
                name = entry["name"]
                etype = entry["type"]
                cur.execute(f"DROP {etype.upper()} IF EXISTS {name};")
            cur.execute("PRAGMA foreign_keys = ON;")
        self.initialize_schema()
```

---

## 5. Complete 8-Table DDL Schema Specification

The relational schema strictly maps to the 8 core entities required by `ORIGINAL_REQUEST.md` and `PROJECT.md`:
1. `projects`
2. `decisions`
3. `workflow_runs`
4. `task_history`
5. `milestones`
6. `agent_status`
7. `execution_traces`
8. `memory_records`

Plus:
- `schema_migrations`
- `memory_fts` (FTS5 virtual table)
- FTS5 synchronization triggers (`trg_memory_insert`, `trg_memory_delete`, `trg_memory_update`)

### 5.1 Full DDL Definition (`SCHEMA_DDL`)

```sql
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
    tags TEXT DEFAULT '[]', -- JSON array of string tags
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
    workflow_type VARCHAR(64) NOT NULL, -- e.g. feature, bugfix, audit, targeted_subtask, interactive_qa
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
    criteria_json TEXT NOT NULL DEFAULT '[]', -- JSON array of criteria strings
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
    event_type VARCHAR(32) NOT NULL CHECK (event_type IN ('thought', 'tool_start', 'tool_end', 'agent_output', 'error', 'tool_call', 'tool_result')),
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
    category VARCHAR(64) DEFAULT 'general' CHECK (category IN ('general', 'decision', 'milestone', 'conversation', 'artifact', 'code_summary')),
    content TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    embedding_json TEXT, -- JSON array of floats (e.g. 64, 128, or 1536 floats)
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

-- Trigger 3: Synchronize memory_fts on update of content in memory_records
CREATE TRIGGER IF NOT EXISTS trg_memory_update AFTER UPDATE OF content ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;
```

---

## 6. Verification and Behavioral Guarantees

### 6.1 Foreign Key Cascade Verification
- Deleting a row from `projects` automatically cascades through SQLite foreign keys, removing:
  - Associated records in `decisions`
  - Associated records in `workflow_runs`
  - Associated records in `task_history` (and cascade-removes child `execution_traces`)
  - Associated records in `milestones`
  - Associated records in `agent_status`
  - Associated records in `memory_records`
- Due to `trg_memory_delete`, removing records from `memory_records` automatically deletes the corresponding entries from `memory_fts`.
- Direct test verification confirmed:
  `DELETE FROM projects WHERE project_id = 'p1'` produces `COUNT(*) = 0` in both `memory_records` and `memory_fts`.

### 6.2 FTS5 Trigger Consistency
- Inserting into `memory_records` immediately renders the content searchable via:
  ```sql
  SELECT record_id FROM memory_fts WHERE memory_fts MATCH 'search_term';
  ```
- Updating `content` in `memory_records` removes the old terms and indexes the new terms via `trg_memory_update`.
- Verified using Porter stemmer: searches for `persistence` successfully match `persistent` and `persisted`.

### 6.3 Concurrency & Lock Handling
- `PRAGMA busy_timeout = 5000;` ensures that if a background writer is committing a transaction, concurrent read queries will wait up to 5 seconds rather than immediately failing with `database is locked`.
- `PRAGMA journal_mode = WAL;` creates `state.db-wal` and `state.db-shm` files alongside `state.db`, providing lock-free concurrent reads while writes are staged in the WAL file.

---

## 7. Concrete Implementation Steps for Implementer

1. **Create Package Metadata**:
   - Write `pyproject.toml` in project root with specified metadata and script entry point.
   - Write `requirements.txt` and `requirements-dev.txt`.
2. **Setup Virtual Environment**:
   - Run `python -m venv .venv`.
   - Upgrade pip and install dependencies via `.\.venv\Scripts\pip.exe install -r requirements-dev.txt` and `.\.venv\Scripts\pip.exe install -e .`.
3. **Implement Configuration (`src/persistent_crewai/config.py`)**:
   - Define paths (`WORKSPACE_ROOT`, `STATE_DB_PATH`, `MOCK_GITHUB_PATH`).
   - Define integrity modes (`development`, `strict`, `production`).
4. **Implement Database Layer (`src/persistent_crewai/persistence/db.py`)**:
   - Implement `DatabaseManager` class with context managers and query helpers.
   - Implement `SCHEMA_DDL` containing the full 8-table schema and FTS5 triggers.
   - Add unit tests in `tests/test_persistence_sqlite.py`.
