# Technical Specifications: State & Memory Persistence, Tooling, and CLI/REPL Interface

## Executive Summary & Scope

This specification defines the architectural design, interface definitions, database schemas, tooling protocols, interactive interfaces, and verification requirements for the persistent 8-agent CrewAI software engineering framework.

It covers:
- **R2: State & Memory Persistence**: SQLite transactional storage, embedding generation with offline fallback, vector store abstraction, session resumption, and state export/import.
- **R3: GitHub & Workspace Tooling**: Sandboxed workspace file operations and bi-modal GitHub adapters (in-memory/persisted mock adapter and real REST API client).
- **R4: CLI & REPL Interface**: Interactive agent Q&A mode grounded in persisted memory, workflow and subtask triggers, real-time trace streaming, and status dashboard.
- **Acceptance Criteria & Test Matrix**: Deterministic unit, integration, and E2E verification test cases with zero required external API keys.

---

## Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Persistence | SQLite Schema & DDL | Relational storage for projects, decisions, task history, milestones, agent status, runs, and traces | Connection parameters, schema version | Initialized SQLite database with WAL & FK enabled | Raises `sqlite3.DatabaseError` on corruption or lock timeout | ORIGINAL_REQUEST.md R2 |
| 2 | Persistence | Decision Tracking | Records architectural and engineering decisions with status, rationale, and implications | Project ID, Agent Role, Title, Rationale, Implications, Status, Tags | Decision record with UUID, timestamp | Validates non-empty fields; raises `ValueError` if missing required fields | ORIGINAL_REQUEST.md R2 |
| 3 | Persistence | Task History & State | Tracks full execution lifecycle of individual agent tasks across workflow runs | Task ID, Workflow Run ID, Agent Role, Description, Output, Status | Task record with start/end timestamps and execution status | Rejects invalid status enum; rolls back on transactional failure | ORIGINAL_REQUEST.md R2 |
| 4 | Persistence | Milestone Management | Tracks project milestones, completion criteria, and target/achieved timestamps | Title, Description, Criteria JSON, Target Date | Milestone record with status (`pending`, `in_progress`, `achieved`) | Raises `ValueError` on malformed criteria JSON | ORIGINAL_REQUEST.md R2 |
| 5 | Persistence | Agent Operational Status | Maintains real-time and persisted operational status of all 8 agents | Agent Role, Status (`idle`, `active`, `busy`, `blocked`, `error`), Current Task ID, Metadata | Updated status row with updated timestamp | Constrained to valid 8 agent role names; raises `KeyError` on unknown role | ORIGINAL_REQUEST.md R1 & R2 |
| 6 | Persistence | Execution Trace Logging | Stores step-by-step agent thoughts, tool invocations, inputs, and outputs | Run ID, Task ID, Agent Role, Event Type (`thought`, `tool_call`, `tool_result`, `output`), Content | Persisted trace record with monotonic timestamp | Truncates over-sized payloads gracefully; logs failure without halting workflow | ORIGINAL_REQUEST.md R2 & R4 |
| 7 | Persistence | Vector Store Interface | Pluggable interface for semantic search over project memories and decisions | Text documents, metadata dictionaries, optional IDs | List of document IDs | Raises `NotImplementedError` if unconfigured; returns empty list on empty query | ORIGINAL_REQUEST.md R2 |
| 8 | Persistence | SQLite Vector / FTS Fallback | Pure Python / SQLite FTS5 cosine/BM25 memory store for offline and zero-dependency execution | Query string, top-k integer, metadata filter | Ranked list of `SearchResult` objects with scores | Gracefully handles empty indices; returns empty list without exception | Runtime probing (Python 3.13, FTS5 available) |
| 9 | Persistence | Deterministic Hash Embedder | Generates normalized dense vector embeddings using deterministic token hashing for offline tests | String or list of strings | List of float vectors of fixed dimension (e.g., 64-dim) | Deterministic output; handles Unicode and empty strings safely | Runtime probing (zero API keys required) |
| 10 | Persistence | State Session Resumption | Reconstructs system state upon reboot and identifies active/pending tasks to resume | Project ID | `ResumptionContext` containing pending tasks, active milestone, and agent states | If project not found, initializes fresh state gracefully | ORIGINAL_REQUEST.md R2 & Acceptance Criteria |
| 11 | Persistence | State Export / Import | Exports complete database and memory store into portable JSON or imports back | Export file path / Import file path, overwrite boolean | Export summary / Rehydrated `ProjectState` | Rejects schema mismatch on import; atomic transactional import | ORIGINAL_REQUEST.md R2 |
| 12 | Workspace Tooling | File Read Tool | Reads file contents safely within workspace boundaries with optional line slicing | File path relative or absolute, optional start_line, end_line | Text content with line numbers | Raises `FileNotFoundError` or `PermissionError` if outside workspace | ORIGINAL_REQUEST.md R3 |
| 13 | Workspace Tooling | File Write Tool | Creates or overwrites files within workspace root with directory auto-creation | File path, content string, overwrite flag | Write confirmation with bytes written | Prevents path traversal outside workspace; atomic write via temp file | ORIGINAL_REQUEST.md R3 |
| 14 | Workspace Tooling | File List Tool | Lists files and subdirectories within workspace with size and modification date | Directory path, recursive flag, glob pattern filter | List of file metadata dictionaries | Raises `NotADirectoryError` if path is not a directory | ORIGINAL_REQUEST.md R3 |
| 15 | Workspace Tooling | File Patch Tool | Replaces an exact block of code or applies unified diff to an existing file | File path, search_block, replace_block | Confirmation of replacement count | Raises `ValueError` if search_block not found or ambiguous (>1 matches) | ORIGINAL_REQUEST.md R3 |
| 16 | GitHub Tooling | GitHub Adapter Interface | Common contract for GitHub operations implemented by both mock and real clients | Repository name, action-specific parameters | Structured dicts matching GitHub REST API response schemas | Standardized `GitHubError` with HTTP status code and message | ORIGINAL_REQUEST.md R3 |
| 17 | GitHub Tooling | Mock GitHub Adapter | Local in-memory and JSON-persisted GitHub simulator supporting issues, PRs, branches, commits | Issue/PR creation, branch creation, commit data | Simulated GitHub entities with auto-incrementing IDs | Validates branch existence on PR creation; returns 404/422 equivalents | ORIGINAL_REQUEST.md R3 |
| 18 | GitHub Tooling | Real GitHub API Adapter | REST client calling `https://api.github.com` via Personal Access Token | Repo name, standard GitHub REST payloads | Verbatim GitHub API response payloads | Catches 401 Unauthorized, 403 Rate Limit, 404 Not Found; maps to `GitHubError` | ORIGINAL_REQUEST.md R3 |
| 19 | GitHub Tooling | GitHub Adapter Factory | Factory resolving adapter based on `GITHUB_TOKEN` presence and user configuration | Explicit adapter type flag or env var check | Initialized adapter instance with log warning if falling back to mock | Falls back safely to Mock adapter if token is absent or invalid | ORIGINAL_REQUEST.md R3 |
| 20 | GitHub Tooling | GitHub Issue Tool | CrewAI tool for creating, reading, listing, and updating GitHub issues | Action (`create`, `list`, `get`, `update`), title, body, number, labels | Formatted string or JSON for LLM consumption | Returns descriptive error string back to LLM instead of crashing crew | ORIGINAL_REQUEST.md R3 |
| 21 | GitHub Tooling | GitHub PR Tool | CrewAI tool for creating, listing, and merging GitHub pull requests | Action (`create`, `list`, `merge`), title, body, head_branch, base_branch | Formatted string or JSON of PR status | Returns descriptive error string on conflict or missing branch | ORIGINAL_REQUEST.md R3 |
| 22 | GitHub Tooling | GitHub Branch & Commit Tool | CrewAI tool for listing branches, creating branches, and viewing commit history | Action (`list_branches`, `create_branch`, `list_commits`), branch name | Formatted branch/commit listing | Returns error string if branch exists or commit not found | ORIGINAL_REQUEST.md R3 |
| 23 | CLI / REPL | Interactive Q&A Mode | Direct conversational REPL allowing users to query any of the 8 agents individually | Agent role name, user query text | Agent response grounded in persisted project context | Validates role name; returns role suggestions if invalid role requested | ORIGINAL_REQUEST.md R4 & Acceptance Criteria |
| 24 | CLI / REPL | State Grounding Pipeline | Injects project context, decisions, milestones, and semantic memories into agent prompt | User query, target agent role | Synthesized system prompt augmentations | Handles empty context gracefully without failing | ORIGINAL_REQUEST.md R4 |
| 25 | CLI / REPL | Workflow Trigger Mode | CLI command to execute full end-to-end multi-agent software engineering workflows | Workflow type (`feature`, `bugfix`, `review`, `audit`), input parameters | Workflow result summary and updated project state | Traps agent execution errors and records them to `task_history` | ORIGINAL_REQUEST.md R4 |
| 26 | CLI / REPL | Targeted Subtask Mode | CLI command to trigger a single task delegated to a specific agent role | Agent role, task description, expected output format | Task execution result and updated state | Rejects invalid role; persists task output upon completion | ORIGINAL_REQUEST.md R4 |
| 27 | CLI / REPL | Status Dashboard | Displays formatted overview of project health, agent states, decisions, milestones | CLI command `status` | Formatted table using `rich` with ASCII fallback | Degrades cleanly if terminal does not support ANSI formatting | ORIGINAL_REQUEST.md R4 |
| 28 | CLI / REPL | Real-Time Trace Display | Live streaming of agent thoughts, tool invocations, and responses during runs | Custom trace callback events | Formatted streaming log to console | Thread-safe; writes to SQLite without blocking execution flow | ORIGINAL_REQUEST.md R4 |
| 29 | Testing | Persistence Test Harness | Automated test verifying that state written in run 1 is recovered exactly in run 2 | Test project ID, sample records | Verification assertion boolean | Fails if state is lost, corrupted, or altered | Acceptance Criteria |
| 30 | Testing | Offline Test Execution | Test mode enabling full agent and workflow verification without real API keys | Mock LLM / Deterministic responses | Successful test execution exit code 0 | Fails if any component requires network or unconfigured API keys | Acceptance Criteria |

---

## Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | SQLite Schema | Concurrent access from multiple threads or processes | Configured with `PRAGMA busy_timeout = 5000;` and `PRAGMA journal_mode = WAL;`. Python connection uses timeout=10.0 to prevent `database is locked` errors. |
| 2 | SQLite Schema | Agent attempts to record task with nonexistent `workflow_run_id` | Foreign key constraint triggers `sqlite3.IntegrityError`; transaction rolls back and error is reported. |
| 3 | State Resumption | Process killed mid-execution while task is `in_progress` | Upon reload, `SessionResumptionManager` detects tasks marked `in_progress` without completion timestamp, transitions them to `interrupted`, and marks agent as `idle` or prompts user for retry. |
| 4 | State Export / Import | Importing JSON export from incompatible schema version | Import validator checks `schema_version`. If versions do not match, raises `SchemaVersionMismatchError` with explicit migration guide. |
| 5 | State Export / Import | Importing state into existing project with `overwrite=False` | Raises `ConflictError` preventing silent data loss; user must explicitly pass `overwrite=True` or a new `project_id`. |
| 6 | Vector Store | Empty document collection or search query consisting only of whitespace | Returns empty list `[]` without raising zero-division or index errors. |
| 7 | Vector Store | Extremely long input text exceeding token bounds | Tokenizer/embedder truncates or chunks text cleanly without throwing uncaught exceptions. |
| 8 | Embedder Fallback | System run in air-gapped / offline environment with no `OPENAI_API_KEY` | `DeterministicHashEmbeddingProvider` generates normalized vector via md5/sha256 token hashing; semantic similarity continues to function deterministically. |
| 9 | Workspace Files | Path traversal attack (e.g. `../../etc/passwd` or `..\..\Windows\System32`) | Path resolver resolves canonical path and checks `resolved_path.is_relative_to(workspace_root)`. Raises `PermissionError: Path traversal outside workspace boundary is forbidden`. |
| 10 | Workspace Files | Write to file in non-existent nested directory (e.g. `src/modules/sub/file.py`) | Tool automatically creates intermediate parent directories (`parents=True, exist_ok=True`) before atomic write. |
| 11 | Workspace Files | File Patch Tool given a search block with 0 matches or multiple identical matches | If 0 matches: raises `ValueError("Search block not found in target file")`. If >1 matches: raises `ValueError("Ambiguous patch: multiple occurrences found. Provide broader context")`. |
| 12 | Mock GitHub Adapter | Attempting to create Pull Request for non-existent head branch | Raises `GitHubError(status_code=422, message="Head branch does not exist")` matching real GitHub API behavior. |
| 13 | Mock GitHub Adapter | Merging an already merged or closed Pull Request | Raises `GitHubError(status_code=405, message="Pull request is not open")`. |
| 14 | Real GitHub Adapter | Expired or invalid token (HTTP 401) | Catches HTTP 401, issues helpful error message advising user to renew `GITHUB_TOKEN` or switch to `--mock-github`. |
| 15 | Real GitHub Adapter | GitHub API rate limit exceeded (HTTP 403) | Checks `X-RateLimit-Remaining` and `X-RateLimit-Reset`; raises `RateLimitExceededError` with remaining time until reset. |
| 16 | CLI / REPL | User queries agent with empty or blank string in interactive mode | REPL warns user "Query cannot be empty" and reprompts without invoking agent pipeline. |
| 17 | CLI / REPL | User passes unknown agent name (e.g. `ask wizard`) | CLI prints error message listing the valid 8 roles: `Coordinator`, `Architect`, `Designer`, `Developer`, `Tester`, `Reviewer`, `GitHub Manager`, `Memory Manager`. |
| 18 | CLI / REPL | User presses Ctrl+C or Ctrl+D during long workflow run | Signal handler intercepts `SIGINT`, cancels active task cleanly, marks task status as `cancelled` in SQLite, flushes traces, and exits gracefully. |
| 19 | REPL Context Injection | No prior decisions or milestones exist in project database | Context injector constructs fallback prompt `"Project initialized. No recorded decisions or milestones yet."` without crashing. |
| 20 | Real-Time Trace | Trace log payload contains non-serializable objects or binary data | Trace logger serializes via safe fallback `str(obj)` or `json.dumps(..., default=str)` to guarantee no logging exceptions. |

---

## Detailed Specification: State & Memory Persistence (R2)

### 1. Database Architecture & Schema Design

The persistence engine uses SQLite 3 with Write-Ahead Logging (`WAL`) mode, foreign key validation, and thread-safe connection pooling.

#### Storage Path
- Default location: `<workspace_root>/.persistent_crewai/state.db`
- Configurable via environment variable `CREWAI_STATE_DB_PATH` or CLI `--db-path`.

#### Pragmas Executed on Every Connection
```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA encoding = 'UTF-8';
```

#### DDL Specification

```sql
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(32) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL
);

-- Project context table
CREATE TABLE IF NOT EXISTS projects (
    project_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    root_path TEXT NOT NULL,
    integrity_mode VARCHAR(32) DEFAULT 'development',
    status VARCHAR(32) DEFAULT 'active', -- active, archived, completed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT DEFAULT '{}'
);

-- Architectural & engineering decisions
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    rationale TEXT NOT NULL,
    implications TEXT NOT NULL,
    status VARCHAR(32) DEFAULT 'accepted', -- proposed, accepted, superseded, rejected
    tags TEXT DEFAULT '[]', -- JSON array of tags
    superseded_by VARCHAR(64) REFERENCES decisions(decision_id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, status);

-- Workflow runs (Full workflows or targeted subtask executions)
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    workflow_type VARCHAR(64) NOT NULL, -- full_pipeline, targeted_subtask, interactive_qa
    trigger_source VARCHAR(64) DEFAULT 'cli', -- cli, repl, api, test
    input_prompt TEXT NOT NULL,
    final_output TEXT,
    status VARCHAR(32) DEFAULT 'running', -- pending, running, completed, failed, interrupted, cancelled
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON workflow_runs(project_id, status);

-- Task history
CREATE TABLE IF NOT EXISTS task_history (
    task_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    expected_output TEXT,
    actual_output TEXT,
    status VARCHAR(32) DEFAULT 'pending', -- pending, in_progress, completed, failed, interrupted, skipped
    execution_order INTEGER DEFAULT 0,
    error_details TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON task_history(run_id, execution_order);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON task_history(agent_role, status);

-- Project milestones
CREATE TABLE IF NOT EXISTS milestones (
    milestone_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    criteria_json TEXT NOT NULL, -- JSON array of completion criteria strings
    target_date TIMESTAMP,
    status VARCHAR(32) DEFAULT 'pending', -- pending, in_progress, achieved, missed
    achieved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id, status);

-- Agent operational status table
CREATE TABLE IF NOT EXISTS agent_status (
    agent_role VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    status VARCHAR(32) DEFAULT 'idle', -- idle, active, busy, blocked, completed, error
    current_task_id VARCHAR(64) REFERENCES task_history(task_id) ON DELETE SET NULL,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tasks_completed_count INTEGER DEFAULT 0,
    last_error TEXT,
    metadata_json TEXT DEFAULT '{}',
    PRIMARY KEY (agent_role, project_id)
);

-- Execution trace events (real-time thought and tool monitoring)
CREATE TABLE IF NOT EXISTS execution_traces (
    trace_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    task_id VARCHAR(64) REFERENCES task_history(task_id) ON DELETE CASCADE,
    agent_role VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL, -- thought, tool_start, tool_end, agent_output, error
    tool_name VARCHAR(128),
    payload TEXT NOT NULL, -- JSON or raw string
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_traces_run ON execution_traces(run_id, timestamp);

-- Vector memory records table (fallback SQLite vector storage)
CREATE TABLE IF NOT EXISTS memory_records (
    record_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    agent_role VARCHAR(64),
    category VARCHAR(64) DEFAULT 'general', -- decision, milestone, conversation, artifact, code_summary
    content TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    embedding_json TEXT, -- JSON array of floats (e.g. 64 or 1536 floats)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_records(project_id, category);

-- SQLite Full-Text Search (FTS5) for fast hybrid memory retrieval
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

-- Triggers to synchronize memory_fts with memory_records
CREATE TRIGGER IF NOT EXISTS trg_memory_insert AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_delete AFTER DELETE ON memory_records BEGIN
    DELETE FROM memory_fts WHERE record_id = old.record_id;
END;
```

### 2. Long-Term Vector Store & Embedding Specification

To ensure robust operation in both production (with OpenAI API keys) and test/offline environments (zero external dependencies), the memory system adheres to an abstract interface with pluggable backends.

#### Interface Definitions

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class SearchResult:
    document_id: str
    content: str
    metadata: Dict[str, Any]
    score: float  # Cosine similarity or BM25 score

class EmbeddingProviderInterface(ABC):
    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of text strings into float vectors."""
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string into a float vector."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimensionality."""
        pass

class VectorStoreInterface(ABC):
    @abstractmethod
    def add(
        self,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """Store documents with optional metadata and IDs."""
        pass

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """Search top-k most relevant documents."""
        pass

    @abstractmethod
    def delete(self, ids: List[str]) -> bool:
        """Delete documents by IDs."""
        pass

    @abstractmethod
    def count(self) -> int:
        """Count total stored records."""
        pass

    @abstractmethod
    def persist(self) -> None:
        """Commit data to durable storage."""
        pass
```

#### Embedding Providers

1. **`DeterministicHashEmbeddingProvider` (Default / Offline Fallback)**:
   - Uses cryptographic hashing (`sha256`) of n-grams or tokens mapped onto a fixed dimension $D = 64$ or $D = 128$.
   - Normalizes vector to unit $L_2$ norm: $v = \frac{v}{\|v\|_2}$.
   - Characteristics:
     - Zero external dependencies (uses standard library `hashlib`, `math`).
     - Fast execution (sub-millisecond).
     - 100% deterministic and reproducible across platforms and test runs.
     - Preserves lexical similarity and token overlap for cosine comparison.

2. **`OpenAIEmbeddingProvider` (Production / Real Mode)**:
   - Wraps OpenAI embeddings API (`text-embedding-3-small` or `text-embedding-ada-002`).
   - Dimension: 1536.
   - Activates automatically when `OPENAI_API_KEY` is present and `embedder_type="openai"`.

#### Vector Store Implementations

1. **`SQLiteVectorStore` (Built-in Default)**:
   - Stores documents and vector JSON in `memory_records` table.
   - Executes search using either:
     - Vector Cosine Similarity (evaluating dot products across candidate vectors).
     - SQLite FTS5 BM25 match when vector search is unneeded or in combination (hybrid search).
   - Zero additional packages required; persistent out of the box in `state.db`.

2. **`ChromaVectorStore` (Optional External Backend)**:
   - Wraps `chromadb` client if the package is installed.
   - Saves to `<workspace_root>/.persistent_crewai/chroma/`.

### 3. Session Resumption, State Export & Import

#### Session Resumption Workflow
1. **Startup Check**:
   - `SessionResumptionManager.inspect_state(project_id)` checks if `projects` record exists.
   - If not, initializes project record and registers all 8 agents in `agent_status` as `idle`.
2. **Crash & Interruption Recovery**:
   - Any tasks with `status='in_progress'` from prior runs are updated to `status='interrupted'`.
   - Any agent with `status='active'` or `'busy'` is reset to `'idle'`.
   - The user is notified of interrupted tasks:
     `"Detected 1 interrupted task from previous session [task_id=...]. Ready to resume."`
3. **Resumption Context**:
   - `get_resumption_context()` extracts:
     - Last active milestone and achieved criteria.
     - Recent accepted decisions (for prompt grounding).
     - List of pending tasks in current workflow queue.
     - Last operational state of all 8 agents.

#### State Export / Import Specification

##### Export Format (`state_export.json`)
```json
{
  "schema_version": "1.0.0",
  "exported_at": "2026-10-13T20:15:00Z",
  "project": {
    "project_id": "crewai_core_01",
    "name": "Persistent CrewAI Team",
    "description": "8-agent persistent framework",
    "root_path": "C:\\Users\\User\\.gemini\\antigravity\\scratch\\persistent_crewai_team",
    "integrity_mode": "development",
    "status": "active"
  },
  "decisions": [
    {
      "decision_id": "dec_001",
      "agent_role": "Architect",
      "title": "Use SQLite with WAL for persistence",
      "rationale": "Single-file, zero-dependency, transactional reliability",
      "implications": "Requires local file access; concurrency handled via WAL and timeout",
      "status": "accepted",
      "tags": ["database", "storage"]
    }
  ],
  "milestones": [
    {
      "milestone_id": "ms_001",
      "title": "Core Architecture Specification",
      "description": "Establish schemas and interfaces",
      "criteria": ["Database schema defined", "Agent tools specified"],
      "status": "in_progress"
    }
  ],
  "agent_status": [
    {
      "agent_role": "Coordinator",
      "status": "idle",
      "tasks_completed_count": 3
    }
  ],
  "memory_records": [
    {
      "record_id": "mem_001",
      "category": "decision",
      "content": "Architect chose SQLite with WAL mode.",
      "metadata": {"source": "dec_001"}
    }
  ]
}
```

##### Import Validation
- Validates JSON against `schema_version`.
- If `project_id` already exists and `overwrite=False`, raises `StateImportConflictError`.
- Atomic import inside a single SQLite transaction: all tables updated or rolled back on error.

---

## Detailed Specification: GitHub & Workspace Tooling (R3)

### 1. Workspace File Tooling

All file tools are bound to a configured `workspace_root` (defaulting to the project root directory). Any attempt to read or modify paths outside this directory is strictly blocked to maintain sandbox integrity.

#### Security Invariant: Path Traversal Protection
```python
def validate_safe_path(target_path: str, workspace_root: Path) -> Path:
    resolved = (workspace_root / target_path).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise PermissionError(
            f"Security violation: Access outside workspace root is forbidden. Target: {target_path}"
        )
    return resolved
```

#### Tool Signatures

1. **`WorkspaceFileReadTool`**:
   - `name`: `read_workspace_file`
   - `description`: `"Read the content of a file in the workspace. Supports optional line slice."`
   - `args_schema`:
     - `file_path`: `str` (Relative path to file within workspace)
     - `start_line`: `Optional[int] = None` (1-indexed start line)
     - `end_line`: `Optional[int] = None` (1-indexed inclusive end line)
   - `returns`: Formatted string with line numbers: `<line_num>: <content>`.
   - `error_handling`: Returns clear error message if file does not exist or line indices are invalid.

2. **`WorkspaceFileWriteTool`**:
   - `name`: `write_workspace_file`
   - `description`: `"Write or create a file in the workspace. Creates parent directories automatically."`
   - `args_schema`:
     - `file_path`: `str`
     - `content`: `str`
     - `overwrite`: `bool = True`
   - `returns`: String confirming path and total bytes written.
   - `error_handling`: Refuses overwrite if `overwrite=False` and file exists. Writes atomically using temporary file rename.

3. **`WorkspaceFileListTool`**:
   - `name`: `list_workspace_files`
   - `description`: `"List files and subdirectories in the workspace."`
   - `args_schema`:
     - `directory_path`: `str = "."`
     - `recursive`: `bool = False`
     - `pattern`: `Optional[str] = None` (glob pattern, e.g. `"*.py"`)
   - `returns`: JSON or table string detailing filename, type (`file` / `dir`), and size in bytes.

4. **`WorkspaceFilePatchTool`**:
   - `name`: `patch_workspace_file`
   - `description`: `"Apply an exact search-and-replace edit to an existing file."`
   - `args_schema`:
     - `file_path`: `str`
     - `search_block`: `str` (Exact string snippet to match)
     - `replace_block`: `str` (New string snippet to substitute)
   - `returns`: Confirmation string detailing lines modified.
   - `error_handling`: Rejects with descriptive error if `search_block` is not found or matches multiple ambiguous locations.

---

### 2. GitHub Tooling & Adapter Architecture

To support offline execution, automated testing, and production GitHub workflows, the GitHub subsystem uses an Adapter Pattern.

```
       +------------------------------------+
       |       CrewAI GitHub Tools          |
       | (IssueTool, PRTool, BranchTool,    |
       |  CommitTool, RepoStatusTool)       |
       +-----------------+------------------+
                         |
                         v
       +------------------------------------+
       |      GitHubAdapterInterface        |
       +-----------------+------------------+
                         |
            +------------+------------+
            |                         |
            v                         v
+-----------------------+ +-----------------------+
|   MockGitHubAdapter   | |   RealGitHubAdapter   |
| (In-Memory / JSON DB) | | (GitHub REST API v3)  |
+-----------------------+ +-----------------------+
```

#### Protocol Definition: `GitHubAdapterInterface`

```python
from typing import Protocol, List, Dict, Any, Optional

class GitHubAdapterInterface(Protocol):
    def get_repo_status(self, repo: str) -> Dict[str, Any]: ...
    def list_issues(self, repo: str, state: str = "open") -> List[Dict[str, Any]]: ...
    def get_issue(self, repo: str, issue_number: int) -> Dict[str, Any]: ...
    def create_issue(self, repo: str, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]: ...
    def update_issue(self, repo: str, issue_number: int, state: Optional[str] = None, comment: Optional[str] = None) -> Dict[str, Any]: ...
    def list_pull_requests(self, repo: str, state: str = "open") -> List[Dict[str, Any]]: ...
    def create_pull_request(self, repo: str, title: str, body: str, head_branch: str, base_branch: str = "main") -> Dict[str, Any]: ...
    def merge_pull_request(self, repo: str, pr_number: int, commit_message: Optional[str] = None) -> Dict[str, Any]: ...
    def list_branches(self, repo: str) -> List[Dict[str, Any]]: ...
    def create_branch(self, repo: str, branch_name: str, from_branch: str = "main") -> Dict[str, Any]: ...
    def list_commits(self, repo: str, branch: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]: ...
```

#### `MockGitHubAdapter` Implementation
- Simulates GitHub behavior locally:
  - Repositories have initial default branch `main`.
  - Issues auto-increment numbers: `#1`, `#2`, ...
  - Pull requests auto-increment numbers: `#101`, `#102`, ...
  - Branches tracked in memory; creating a branch records parent commit hash.
  - Commits generate pseudo-SHA hashes: `sha = hashlib.sha256(...).hexdigest()[:8]`.
- Persists mock state to `<workspace_root>/.persistent_crewai/mock_github.json` so GitHub state survives between runs!
- Simulates exact HTTP error codes (e.g. 404 for missing issue, 422 for unmergeable PR or missing branch).

#### `RealGitHubAdapter` Implementation
- Connects to `https://api.github.com`.
- Authenticates using `Authorization: Bearer <GITHUB_TOKEN>`.
- Request headers:
  - `Accept: application/vnd.github.v3+json`
  - `User-Agent: Persistent-CrewAI-Framework`
- Error mapping:
  - 401: Raises `GitHubAuthError("Invalid or missing GITHUB_TOKEN")`.
  - 403: Checks rate limit; raises `GitHubRateLimitError`.
  - 404: Raises `GitHubNotFoundError`.

#### Factory Resolution Logic
```python
def create_github_adapter(force_mock: bool = False) -> GitHubAdapterInterface:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if force_mock or not token:
        logger.info("Initializing MockGitHubAdapter (local simulated GitHub).")
        return MockGitHubAdapter(storage_path=".persistent_crewai/mock_github.json")
    logger.info("Initializing RealGitHubAdapter with active GITHUB_TOKEN.")
    return RealGitHubAdapter(token=token)
```

---

## Detailed Specification: CLI & REPL Interface (R4)

### 1. REPL & Interactive Architecture

The CLI provides two primary operational modes:
1. **Interactive REPL Mode (`crewai-team repl`)**: Direct agent Q&A, interactive task execution, and real-time project state inspection.
2. **Direct CLI Command Mode (`crewai-team <subcommand>`)**: Scriptable workflow execution, subtask delegation, status reporting, and state backup.

#### Grammar & Command Set

| Command | Arguments | Description | Example |
|---------|-----------|-------------|---------|
| `ask` | `<role> <question>` | Prompt a specific agent directly grounded in persisted state | `ask architect "Explain the DB design"` |
| `chat` | `<role>` | Enter dedicated multi-turn conversation loop with an agent | `chat developer` |
| `status` | `[--detailed]` | Render formatted project dashboard and agent operational states | `status` |
| `decisions` | `[--status <s\>]` | Display recorded architectural and design decisions | `decisions --status accepted` |
| `milestones` | None | Display milestones, target dates, and criteria status | `milestones` |
| `history` | `[--limit <n\>]` | Show recent task history across workflow runs | `history --limit 10` |
| `run workflow` | `<workflow_type>` | Trigger full multi-agent pipeline (`feature`, `bugfix`, `audit`) | `run workflow feature` |
| `run task` | `<role> <description>`| Trigger a targeted subtask assigned to a specific role | `run task tester "Write persistence unit tests"` |
| `trace` | `[run_id]` | Display real-time or historical execution trace | `trace latest` |
| `export` | `<file_path>` | Export full system state to JSON | `export state_backup.json` |
| `import` | `<file_path>` | Import system state from JSON | `import state_backup.json` |
| `help` | None | List available commands and syntax | `help` |
| `exit` / `quit` | None | Save session state and exit | `exit` |

### 2. State-Grounded Agent Q&A Pipeline

When a user prompts an individual agent (e.g., `ask manager "What is the current status?"`), the query is grounded through a four-stage pipeline:

```
+-------------------------------------------------------------+
| 1. Query Analyzer: Extract target agent role & user intent  |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| 2. Context Retrieval Pipeline:                              |
|    - Fetch active project record                            |
|    - Retrieve active milestones & completed criteria        |
|    - Retrieve recent decisions by this agent & peers        |
|    - Search Vector Store / FTS for top-k matching memories  |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| 3. Dynamic Prompt Augmentation:                             |
|    - Synthesize Grounding Header with structured facts      |
|    - Append agent specific backstory & role constraints     |
|    - Pass user question to CrewAI agent                     |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| 4. Persistence & Response:                                  |
|    - Record interaction in execution_traces and task_history|
|    - Render formatted response to console                   |
+-------------------------------------------------------------+
```

#### Grounding Prompt Template
```
--- CURRENT PROJECT CONTEXT ---
Project: {project_name} (Mode: {integrity_mode})
Current Active Milestone: {current_milestone_title} (Status: {milestone_status})
Relevant Recorded Decisions:
{formatted_decisions}
Retrieved Project Memories:
{retrieved_memory_snippets}
--- INSTRUCTIONS ---
You are the {agent_role} on the engineering team.
Respond to the user's prompt directly, using the established facts and decisions above.
User prompt: {user_query}
```

### 3. Workflow Runner & Targeted Subtask Engine

#### Full Workflow Pipelines
- **`feature_development`**:
  1. `Coordinator`: Decomposes feature request into tasks and assigns milestones.
  2. `Architect`: Evaluates tech stack and creates architectural blueprint (persisted to `decisions`).
  3. `Designer`: Specifies interface flow and data contracts.
  4. `Developer`: Implements code files via `WorkspaceFileWriteTool`.
  5. `Tester`: Writes and executes unit/integration tests.
  6. `Reviewer`: Performs code review, security check, and style compliance audit.
  7. `GitHub Manager`: Creates branch, commits changes, opens Pull Request via GitHub adapter.
  8. `Memory Manager`: Summarizes run, updates milestone criteria, and records operational status.

#### Targeted Subtask Execution
- Executes a standalone task assigned to a designated agent without spinning up the full 8-agent cycle.
- Example: `crewai-team run task tester "Run pytest tests/ and report failures"`
- The single agent accesses persisted workspace files and records task results in `task_history`.

### 4. Real-Time Execution Trace & Dashboard Renderer

#### Custom Callback Handler: `PersistentTraceCallbackHandler`
- Inherits from CrewAI's callback handlers / LangChain callbacks:
  - `on_task_start`: Records event to `execution_traces`, updates `agent_status` to `busy`.
  - `on_agent_thought`: Streams thought to CLI with formatted styling, logs to DB.
  - `on_tool_start`: Formats tool call: `[TOOL CALL] <tool_name>(<args>)`.
  - `on_tool_end`: Formats tool result: `[TOOL RESULT] <tool_name> -> <output_summary>`.
  - `on_task_end`: Marks task `completed` in `task_history`, resets agent to `idle`.

#### Dashboard Layout (Rendered with `rich` console or ASCII table)
```
========================================================================================
                      PERSISTENT CREWAI PROJECT DASHBOARD
========================================================================================
 Project: Persistent CrewAI Team (crewai_core_01) | Mode: development | DB: state.db
 Status: ACTIVE | Uptime: 2 runs completed | Active Milestone: Core Architecture
----------------------------------------------------------------------------------------
 AGENT OPERATIONAL STATUS
----------------------------------------------------------------------------------------
 Role                     Status     Completed Tasks   Last Active           Current Task
 -----------------------  ---------  ----------------  --------------------  ------------
 Coordinator / Manager    IDLE       2                 2026-10-13 20:10:00   -
 Architect                IDLE       2                 2026-10-13 20:11:15   -
 Designer                 IDLE       1                 2026-10-13 20:11:45   -
 Developer                IDLE       2                 2026-10-13 20:13:00   -
 Tester                   IDLE       2                 2026-10-13 20:13:30   -
 Reviewer                 IDLE       1                 2026-10-13 20:14:00   -
 GitHub Manager           IDLE       1                 2026-10-13 20:14:30   -
 Memory & Status Manager  IDLE       2                 2026-10-13 20:14:55   -
----------------------------------------------------------------------------------------
 RECENT ACCEPTED DECISIONS
----------------------------------------------------------------------------------------
 [dec_001] (Architect) Use SQLite with WAL mode for transactional persistence
 [dec_002] (Architect) Deterministic hash embedder for offline/test reliability
 [dec_003] (GitHub Mgr) Dual Mock/Real GitHub adapter with JSON persistence
========================================================================================
```

---

## Test Requirements & Verification Matrix

The test suite must validate all requirements deterministically without requiring external API keys or live network connectivity.

### 1. Test Suite Architecture

```
tests/
├── conftest.py                   # Fixtures: temp database, temp workspace, mock adapter
├── test_persistence_sqlite.py   # Unit tests for SQLite schema, transactions, migrations
├── test_persistence_vector.py   # Unit tests for VectorStore, hash embeddings, FTS5
├── test_session_resumption.py    # Resumption across process restarts and state export/import
├── test_workspace_tools.py       # File read/write/list/patch tools and path sandboxing
├── test_github_adapter.py        # Mock and real GitHub adapters, PR/issue lifecycle
├── test_agent_qa_grounding.py    # Direct interactive Q&A mode grounded in persisted state
├── test_pipeline_execution.py    # Sequential/hierarchical execution across all 8 roles
└── test_cli_interface.py         # CLI commands, argument parsing, REPL commands
```

### 2. Detailed Test Specifications

#### Test Suite 1: SQLite Persistence & Transaction Integrity (`test_persistence_sqlite.py`)
- **TC-1.1: Schema Creation**: Initializes fresh DB; verifies all tables and indexes exist.
- **TC-1.2: CRUD Operations**: Inserts and reads projects, decisions, milestones, tasks, agent statuses, traces.
- **TC-1.3: Foreign Key Enforcement**: Attempting to insert task with nonexistent `run_id` raises `IntegrityError`.
- **TC-1.4: Cascade Deletion**: Deleting project cascades cleanly to decisions, tasks, milestones, and traces.
- **TC-1.5: Concurrent Thread Access**: Multiple threads performing concurrent writes handle locks cleanly without crashing.

#### Test Suite 2: Vector Memory & Offline Fallback (`test_persistence_vector.py`)
- **TC-2.1: Deterministic Embedder**: Computes vector for text; verifies identical vector returned on repeat call.
- **TC-2.2: Normalized Dimension**: Verifies all vectors have unit norm $\|v\|_2 \approx 1.0$ and expected dimension (e.g. 64).
- **TC-2.3: Semantic Cosine Ranking**: Documents with overlapping tokens rank higher than unrelated documents.
- **TC-2.4: SQLite Vector Persistence**: Documents added in session 1 are queryable in a fresh vector store instance in session 2.
- **TC-2.5: FTS5 Full-Text Search**: Exact keyword searches retrieve matching documents instantly with BM25 ranking.

#### Test Suite 3: Session Resumption & Export/Import (`test_session_resumption.py`)
- **TC-3.1: Interrupted Task Recovery**: A task saved as `in_progress` is automatically flagged as `interrupted` upon restart.
- **TC-3.2: Context Rehydration**: Verifies `get_resumption_context()` extracts last milestone and active tasks.
- **TC-3.3: State Export to JSON**: Full database dumped to JSON; matches expected schema structure.
- **TC-3.4: State Import from JSON**: JSON imported into clean database; verifies all records are restored identically.
- **TC-3.5: Conflict Prevention**: Importing into existing project without `overwrite=True` raises `StateImportConflictError`.

#### Test Suite 4: Workspace File Tools & Sandboxing (`test_workspace_tools.py`)
- **TC-4.1: Safe File Write & Read**: Writes file inside workspace, reads back content, verifies integrity.
- **TC-4.2: Nested Directory Creation**: Writes to `a/b/c/file.txt`; verifies directories created automatically.
- **TC-4.3: Path Traversal Defense**: Passing `../../target.txt` raises `PermissionError`.
- **TC-4.4: File Patching**: Replaces target block in file; verifies replacement made cleanly.
- **TC-4.5: Ambiguous Patch Error**: Patching file with multiple duplicate blocks raises `ValueError`.

#### Test Suite 5: GitHub Mock & Real Adapters (`test_github_adapter.py`)
- **TC-5.1: Mock Issue Lifecycle**: Creates issue `#1`, verifies `list_issues`, updates issue to closed.
- **TC-5.2: Mock PR Lifecycle**: Creates branch, opens PR `#101`, merges PR, verifies status is `merged`.
- **TC-5.3: Mock State Persistence**: Mock data written to `mock_github.json`; verifies state survives adapter restart.
- **TC-5.4: Missing Branch Error**: Opening PR for nonexistent branch returns error (status 422).
- **TC-5.5: Factory Resolution**: Tests factory falls back to `MockGitHubAdapter` when `GITHUB_TOKEN` is unset.

#### Test Suite 6: Agent Instantiation & Direct Q&A Grounding (`test_agent_qa_grounding.py`)
- **TC-6.1: 8 Roles Instantiation**: Verifies all 8 agent roles are instantiated with distinct roles, goals, and backstories.
- **TC-6.2: Grounded Q&A Injection**: Seeds decision in SQLite; queries Architect; verifies prompt receives injected decision context.
- **TC-6.3: Offline Mock LLM**: Executes Q&A using mock LLM backend without making external API calls.
- **TC-6.4: Interaction Recording**: Verifies Q&A turn is recorded in `task_history` or `execution_traces`.

#### Test Suite 7: Full Pipeline Execution (`test_pipeline_execution.py`)
- **TC-7.1: Sequential Pipeline Run**: Executes multi-agent workflow across all 8 roles; verifies 0 runtime errors.
- **TC-7.2: Targeted Subtask Run**: Executes subtask assigned to Tester; verifies only Tester executes.
- **TC-7.3: Trace Streamer**: Custom callback logs all thought and tool events to `execution_traces`.

#### Test Suite 8: CLI & REPL Commands (`test_cli_interface.py`)
- **TC-8.1: CLI Argument Parsing**: Tests subcommands (`status`, `ask`, `run`, `export`, `import`).
- **TC-8.2: REPL Command Dispatch**: Tests REPL commands dispatch to correct handlers.
- **TC-8.3: Dashboard Rendering**: Tests dashboard renders cleanly without ANSI crashes.

---

## Acceptance Criteria Verification Checklist

| Acceptance Criterion | Verification Method | Pass Condition |
|----------------------|---------------------|----------------|
| **Runnable Python Package** | Run `python -m pip install -e .` or package validation script | Zero install errors; CLI executable available |
| **All 8 Agent Roles Configured** | `test_agent_instantiation` in test suite | All 8 agents instantiate with distinct roles, backstories, tools |
| **Interactive Query Mode** | `crewai-team ask coordinator "status"` or `test_agent_qa_grounding` | Agent answers query grounded in persisted SQLite state |
| **Sequential/Hierarchical Execution** | `crewai-team run workflow feature` or `test_pipeline_execution` | Pipeline completes across all 8 roles without runtime errors |
| **State Persistence Across Runs** | Run initial task -> close process -> rerun subsequent task | State loaded from disk; task history and decisions intact |
| **Automated Verification Script** | Run `python -m unittest discover -s tests` or `pytest tests/` | All test suites pass (100% pass rate) with zero external API calls |
| **Documentation** | Inspect `README.md` in project root | Includes setup, architecture diagram, config options, execution examples |
